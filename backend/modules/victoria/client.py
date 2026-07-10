"""Клиент к компонентам Victoria Metrics (async, httpx).

Мы — тонкий прокси: фронт шлёт нам запрос, мы дёргаем нужный компонент кластера
(vmselect / vmagent / vmalert) и возвращаем его ответ как есть. API у vmselect
Prometheus-совместимый (/api/v1/query, /query_range, /labels, /series, /status/tsdb);
у vmagent — /api/v1/targets; у vmalert — /api/v1/rules и /api/v1/alerts.

Несколько нод компонента (HA) — читаем с первой живой. Отдаём JSON целиком:
разбор формата Prometheus делает фронт.
"""
from __future__ import annotations

import httpx

from .config import TENANTS, VMAGENT, VMALERT, VMSELECT

_TIMEOUT = httpx.Timeout(30.0)  # запросы к метрикам бывают тяжёлыми


class VictoriaError(RuntimeError):
    pass


# trust_env=False — чтобы httpx НЕ уводил запрос через HTTP(S)_PROXY из окружения
# (иначе обращение к внутреннему адресу кластера уходит в корпоративный прокси).
async def _get_json(base: str, path: str, params: dict | None = None) -> dict:
    """GET к одной ноде; понятная ошибка, если недоступна или ответила ошибкой."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            resp = await client.get(f"{base}{path}", params=params)
    except httpx.HTTPError as e:
        raise VictoriaError(f"нет связи с {base}: {e}")
    if resp.status_code >= 400:
        raise VictoriaError(f"{base} → {resp.status_code}: {resp.text}")
    return resp.json()


async def _first(nodes: list[str], path: str, params: dict | None = None) -> dict:
    """Запрос к первой живой ноде из списка; если упали все — ошибка."""
    errors = []
    for base in nodes:
        try:
            return await _get_json(base, path, params)
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
async def query(env: str, expr: str, time: str | None = None, tenant: str | None = None) -> dict:
    """Мгновенный запрос (instant): значение в точке времени."""
    params = {"query": expr}
    if time:
        params["time"] = time
    return await _first(_vmselect_nodes(env, tenant), "/api/v1/query", params)


async def query_range(env: str, expr: str, start: str, end: str, step: str, tenant: str | None = None) -> dict:
    """Запрос за интервал (range): ряд точек для графика."""
    params = {"query": expr, "start": start, "end": end, "step": step}
    return await _first(_vmselect_nodes(env, tenant), "/api/v1/query_range", params)


async def labels(env: str, tenant: str | None = None) -> dict:
    """Список имён лейблов (для автодополнения)."""
    return await _first(_vmselect_nodes(env, tenant), "/api/v1/labels")


async def label_values(env: str, label: str, tenant: str | None = None) -> dict:
    """Значения одного лейбла (для автодополнения). label=__name__ → имена метрик."""
    return await _first(_vmselect_nodes(env, tenant), f"/api/v1/label/{label}/values")


async def tsdb_status(env: str, topn: int = 20, tenant: str | None = None) -> dict:
    """Кардинальность: топ метрик/лейблов по числу серий (/api/v1/status/tsdb)."""
    return await _first(
        _vmselect_nodes(env, tenant), "/api/v1/status/tsdb", {"topN": str(topn)}
    )


# --- vmagent: цели скрейпа -----------------------------------------------------
async def targets(env: str) -> dict:
    """Цели скрейпа из vmagent (/api/v1/targets): up/down, ошибки."""
    return await _first(_nodes(VMAGENT, env, "vmagent"), "/api/v1/targets")


# --- vmalert: правила и алерты -------------------------------------------------
async def rules(env: str) -> dict:
    """Группы правил (recording+alerting) из vmalert (/api/v1/rules)."""
    return await _first(_nodes(VMALERT, env, "vmalert"), "/api/v1/rules")


async def alerts(env: str) -> dict:
    """Активные алерты из vmalert (/api/v1/alerts)."""
    return await _first(_nodes(VMALERT, env, "vmalert"), "/api/v1/alerts")
