"""Клиент к Alertmanager и источникам правил. Async (httpx).

Каждое окружение может иметь НЕСКОЛЬКО нод (HA): и Alertmanager, и Prometheus/
vmalert задаются списком. Чтение собираем со всех нод и дедупим (устойчиво к
недоступным). Запись silence шлём на первую живую — AM-кластер разносит silence
по госсипу сам.
"""
from __future__ import annotations  # dict | None на Python 3.9

import httpx

from .config import ALERTMANAGERS, RULE_SOURCES

_TIMEOUT = httpx.Timeout(10.0)  # таймаут запроса


class AlertmanagerError(RuntimeError):
    pass


def _am_nodes(env: str) -> list[str]:
    nodes = ALERTMANAGERS.get(env)
    if not nodes:
        raise AlertmanagerError(f"неизвестное окружение: {env}")
    return nodes


# trust_env=False — чтобы httpx НЕ тащил запрос через HTTP(S)_PROXY из окружения
# (иначе обращение к внутреннему адресу уходит в корпоративный прокси и рвётся).
async def _request(method: str, base: str, path: str, json: dict | None = None) -> httpx.Response:
    """Один запрос к конкретной ноде; понятная ошибка, если недоступна."""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            resp = await client.request(method, f"{base}{path}", json=json)
    except httpx.HTTPError as e:
        raise AlertmanagerError(f"нет связи с {base}: {e}")
    if resp.status_code >= 400:
        raise AlertmanagerError(f"{base} → {resp.status_code}: {resp.text}")
    return resp


async def _read_all(nodes: list[str], path: str, params: dict | None = None) -> list[list]:
    """GET со всех нод; недоступные пропускаем. Если упали ВСЕ — кидаем ошибку."""
    out, errors = [], []
    for base in nodes:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
                resp = await client.get(f"{base}{path}", params=params)
            if resp.status_code >= 400:
                errors.append(f"{base} → {resp.status_code}")
                continue
            out.append(resp.json())
        except httpx.HTTPError as e:
            errors.append(f"{base}: {e}")
    if not out and errors:
        raise AlertmanagerError("; ".join(errors))
    return out


def _dedup(items, key) -> list:
    """Убрать повторы (одни и те же сущности приходят с реплик-нод)."""
    seen, out = set(), []
    for it in items:
        k = key(it)
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


async def _post_first(env: str, path: str, json: dict | None = None) -> httpx.Response:
    """Запрос на первую живую ноду AM (для записи; кластер разнесёт сам)."""
    last: Exception | None = None
    for base in _am_nodes(env):
        try:
            return await _request("POST" if json is not None else "DELETE", base, path, json=json)
        except AlertmanagerError as e:
            last = e
    raise last or AlertmanagerError(f"нет живых нод AM для {env}")


async def list_silences(env: str) -> list[dict]:
    """Все silence окружения со всех нод AM (дедуп по id)."""
    lists = await _read_all(_am_nodes(env), "/api/v2/silences")
    return _dedup((s for chunk in lists for s in chunk), key=lambda s: s.get("id"))


async def active_silences(env: str) -> list[dict]:
    """Живые silence (active/pending) — по ним проверяем дубли перед созданием."""
    silences = await list_silences(env)
    return [s for s in silences if s.get("status", {}).get("state") in ("active", "pending")]


async def list_alerts(env: str) -> list[dict]:
    """Текущие алерты со всех нод AM (дедуп по fingerprint)."""
    lists = await _read_all(_am_nodes(env), "/api/v2/alerts")
    return _dedup(
        (a for chunk in lists for a in chunk),
        key=lambda a: a.get("fingerprint") or str(sorted((a.get("labels") or {}).items())),
    )


async def list_rule_defs(env: str) -> list[dict]:
    """Определения алертинг-правил из Prometheus/vmalert (GET /api/v1/rules).

    Формат у Prometheus и vmalert одинаковый. Собираем со всех нод источника и
    дедупим по (имя, выражение) — на случай реплик vmalert. Источник не задан —
    отдаём пусто: вкладка «Алерты» откатится на живые алерты AM.
    """
    nodes = RULE_SOURCES.get(env)
    if not nodes:
        return []
    lists = await _read_all(nodes, "/api/v1/rules", params={"type": "alert"})
    rules: list[dict] = []
    for data in lists:
        for group in data.get("data", {}).get("groups", []):
            for r in group.get("rules", []):
                if r.get("type") == "alerting":
                    rules.append(r)
    return _dedup(rules, key=lambda r: (r.get("name"), r.get("query")))


async def create_silence(env: str, payload: dict) -> str:
    """Создать silence на первой живой ноде, вернуть его id."""
    resp = await _post_first(env, "/api/v2/silences", json=payload)
    return resp.json().get("silenceID", "")


async def delete_silence(env: str, silence_id: str) -> None:
    """Снять silence по id (на первой живой ноде; кластер разнесёт)."""
    await _post_first(env, f"/api/v2/silence/{silence_id}", json=None)
