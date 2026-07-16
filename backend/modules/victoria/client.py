"""Клиент к компонентам Victoria Metrics (async, httpx).

Мы — тонкий прокси: фронт шлёт нам запрос, мы дёргаем нужный компонент кластера
(vmselect / vmagent / vmalert) и возвращаем его ответ как есть. API у vmselect
Prometheus-совместимый (/api/v1/query, /query_range, /labels, /series, /status/tsdb);
у vmagent — /api/v1/targets; у vmalert — /api/v1/rules и /api/v1/alerts.

Несколько нод компонента (HA) — читаем с первой живой. Отдаём СЫРЫЕ БАЙТЫ ответа:
разбор формата Prometheus делает фронт.

Скорость (цель — не отставать от родных vmui/веба Prometheus; наш неизбежный
overhead — цепочка nginx → oauth2-proxy → бэкенд, остальное выжимаем):
- ответ НЕ разбираем и НЕ пересобираем: JSON проходит сквозь бэкенд сырыми
  байтами (на мегабайтных ответах парсинг+сериализация съедали заметное время;
  исключение — targets, их надо облегчить, см. targets());
- один общий httpx-клиент на процесс — соединения переиспользуются, а не
  открываются заново на каждый запрос;
- connect-таймаут короткий (3 с): мёртвая нода отваливается быстро, и мы сразу
  идём к следующей, вместо зависания на полные 30 с;
- медленные и редко меняющиеся ответы (targets, tsdb, имена метрик/лейблов)
  кэшируются в памяти на несколько минут; кнопка «Обновить» на фронте шлёт
  refresh=1 и перечитывает мимо кэша;
- повторные query/query_range не кэшируем у себя — их ускоряет rollup-кэш самой
  VM, мы его не заслоняем.
"""
from __future__ import annotations

import json
import time

import httpx

from .config import TENANTS, VMAGENT, VMALERT, VMSELECT

# read 30 с — запросы к метрикам бывают тяжёлыми; connect 3 с — чтобы недоступная
# нода не подвешивала весь запрос перед переходом к следующей (HA).
_TIMEOUT = httpx.Timeout(30.0, connect=3.0)

# --- Кэш «на несколько минут» ---------------------------------------------------
# Простой словарь ключ -> (истекает_в, байты ответа). Ключи — маленький
# фиксированный набор (окружение × вкладка), так что чистка не нужна: записи
# перезаписываются.
TTL_TARGETS = 300   # targets: тяжёлый JSON, состав целей меняется редко
TTL_TSDB = 300      # /status/tsdb: дорогой для vmstorage
TTL_LABELS = 300    # имена метрик/лейблов для автодополнения
TTL_RULES = 60      # правила: полегче, но тоже не на каждый клик

_cache: dict = {}


async def _cached(key: tuple, ttl: int, refresh: bool, fetch) -> bytes:
    """Отдаём из кэша, если не истёк и не попросили refresh; иначе перечитываем."""
    now = time.monotonic()
    if not refresh:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    data = await fetch()
    _cache[key] = (now + ttl, data)
    return data


class VictoriaError(RuntimeError):
    pass


# Один клиент на процесс: пул соединений (keep-alive) вместо нового TCP/TLS на
# каждый запрос. trust_env=False — чтобы httpx НЕ уводил запрос через HTTP(S)_PROXY
# из окружения (иначе обращение к внутреннему адресу уходит в корпоративный прокси).
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False)
    return _client


async def _get_raw(base: str, path: str, params: dict | None = None) -> bytes:
    """GET к одной ноде; тело ответа сырыми байтами. Понятная ошибка, если нода
    недоступна или ответила ошибкой."""
    try:
        resp = await _http().get(f"{base}{path}", params=params)
    except httpx.HTTPError as e:
        raise VictoriaError(f"нет связи с {base}: {e}")
    if resp.status_code >= 400:
        # VM кладёт причину в JSON-поле error (например, синтаксис PromQL) —
        # показываем её текстом, а не сырой JSON-конверт целиком.
        detail = resp.text
        try:
            detail = resp.json().get("error") or detail
        except ValueError:
            pass
        raise VictoriaError(f"{base} → {resp.status_code}: {detail}")
    return resp.content


async def _first(nodes: list[str], path: str, params: dict | None = None) -> bytes:
    """Запрос к первой живой ноде из списка; если упали все — ошибка."""
    errors = []
    for base in nodes:
        try:
            return await _get_raw(base, path, params)
        except VictoriaError as e:
            errors.append(str(e))
    raise VictoriaError("; ".join(errors) or "нет нод")


def _nodes(mapping: dict, env: str, component: str) -> list[str]:
    """Ноды компонента для окружения или понятная ошибка, что он не настроен."""
    nodes = mapping.get(env)
    if not nodes:
        raise VictoriaError(f"{component} не настроен для окружения «{env}»")
    return nodes


def _vmselect_nodes(env: str, tenant: str | None) -> list[str]:
    """Ноды vmselect для кластера. У мультитенантного кластера берём полный URL по
    подписи тенанта (её резолвит config.resolve_tenant); у простого — прямой список."""
    tmap = TENANTS.get(env)
    if tmap:
        label = tenant if tenant in tmap else sorted(tmap)[0]
        return tmap[label]
    return _nodes(VMSELECT, env, "vmselect")


# --- vmselect: чтение метрик (tenant — для мультитенантного VM) ----------------
async def query(env: str, expr: str, time: str | None = None, tenant: str | None = None) -> bytes:
    """Мгновенный запрос (instant): значение в точке времени."""
    params = {"query": expr}
    if time:
        params["time"] = time
    return await _first(_vmselect_nodes(env, tenant), "/api/v1/query", params)


async def query_range(env: str, expr: str, start: str, end: str, step: str, tenant: str | None = None) -> bytes:
    """Запрос за интервал (range): ряд точек для графика."""
    params = {"query": expr, "start": start, "end": end, "step": step}
    return await _first(_vmselect_nodes(env, tenant), "/api/v1/query_range", params)


async def labels(env: str, tenant: str | None = None) -> bytes:
    """Список имён лейблов (для автодополнения). Кэш TTL_LABELS."""
    async def fetch():
        return await _first(_vmselect_nodes(env, tenant), "/api/v1/labels")
    return await _cached(("labels", env, tenant), TTL_LABELS, False, fetch)


async def label_values(env: str, label: str, tenant: str | None = None, limit: int | None = None) -> bytes:
    """Значения одного лейбла (для автодополнения). label=__name__ → имена метрик.
    limit понимает VM (и свежий Prometheus) — не тащим сотни тысяч имён целиком.
    Кэш TTL_LABELS."""
    params = {"limit": str(limit)} if limit else None

    async def fetch():
        return await _first(_vmselect_nodes(env, tenant), f"/api/v1/label/{label}/values", params)
    return await _cached(("label_values", env, tenant, label, limit), TTL_LABELS, False, fetch)


async def tsdb_status(env: str, topn: int = 20, tenant: str | None = None, refresh: bool = False) -> bytes:
    """Кардинальность: топ метрик/лейблов по числу серий (/api/v1/status/tsdb).
    Дорогой запрос для vmstorage — кэш TTL_TSDB, «Обновить» шлёт refresh."""
    async def fetch():
        return await _first(
            _vmselect_nodes(env, tenant), "/api/v1/status/tsdb", {"topN": str(topn)}
        )
    return await _cached(("tsdb", env, tenant, topn), TTL_TSDB, refresh, fetch)


# --- vmagent: цели скрейпа -----------------------------------------------------
async def targets(env: str, refresh: bool = False) -> bytes:
    """Цели скрейпа из vmagent (/api/v1/targets): up/down, ошибки.

    Единственный роут, где ответ разбираем: state=active просит vmagent/Prometheus
    даже не сериализовать dropped-цели, а из активных выбрасываем discoveredLabels
    (сырые лейблы до relabel — в разы больше итоговых; UI их не показывает).
    JSON худеет в разы, вкладка открывается заметно быстрее.
    Кэш TTL_TARGETS, «Обновить» шлёт refresh."""
    async def fetch():
        raw = await _first(_nodes(VMAGENT, env, "vmagent"), "/api/v1/targets", {"state": "active"})
        data = json.loads(raw)
        inner = data.get("data") or {}
        for t in inner.get("activeTargets") or []:
            t.pop("discoveredLabels", None)
        inner.pop("droppedTargets", None)
        return json.dumps(data, ensure_ascii=False).encode()
    return await _cached(("targets", env), TTL_TARGETS, refresh, fetch)


# --- vmalert: правила и алерты -------------------------------------------------
async def rules(env: str, refresh: bool = False) -> bytes:
    """Группы правил (recording+alerting) из vmalert (/api/v1/rules). Кэш TTL_RULES."""
    async def fetch():
        return await _first(_nodes(VMALERT, env, "vmalert"), "/api/v1/rules")
    return await _cached(("rules", env), TTL_RULES, refresh, fetch)


async def alerts(env: str) -> bytes:
    """Активные алерты из vmalert (/api/v1/alerts). Не кэшируем — статус живой."""
    return await _first(_nodes(VMALERT, env, "vmalert"), "/api/v1/alerts")
