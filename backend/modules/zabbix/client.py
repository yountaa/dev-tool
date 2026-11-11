"""HTTP-клиенты модуля zabbix: Elasticsearch (аудит) и Zabbix API (имена объектов).

Один общий httpx-клиент на процесс (keep-alive), trust_env=False — чтобы запросы
к внутренним адресам не уходили в корпоративный прокси из HTTP(S)_PROXY.
"""
from __future__ import annotations

import logging
import time

import httpx

import logging_setup

from . import config

log = logging.getLogger("zabbix.client")

# read 30 с — поиск за месяц по большому индексу бывает небыстрым; connect 3 с —
# мёртвая нода ES отваливается быстро, и мы идём к следующей.
_TIMEOUT = httpx.Timeout(30.0, connect=3.0)


class ZabbixAuditError(RuntimeError):
    """Внешняя система (ES / Zabbix API) недоступна или ответила ошибкой → 502."""


_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False, verify=config.TLS_VERIFY)
    return _client


# --- Elasticsearch ------------------------------------------------------------
def _es_headers() -> dict:
    if config.ES_API_KEY:
        return {"Authorization": f"ApiKey {config.ES_API_KEY}"}
    return {}


def _es_auth():
    if not config.ES_API_KEY and config.ES_USER:
        return (config.ES_USER, config.ES_PASSWORD)
    return None


def _es_reason(resp: httpx.Response) -> str:
    """Причина ошибки ES одной строкой (а не JSON-конверт на полэкрана)."""
    try:
        err = resp.json().get("error")
    except ValueError:
        return resp.text[:300]
    if isinstance(err, dict):
        root = (err.get("root_cause") or [{}])[0]
        return root.get("reason") or err.get("reason") or str(err)[:300]
    return str(err)[:300]


async def es_search(index: str, body: dict) -> dict:
    """POST <index>/_search на первую живую ноду ES. Падение ноды логируем: при HA
    интерфейс продолжит работать со второй, и без строки в логе мёртвая первая
    нода не видна вовсе."""
    if not config.ES_URLS:
        raise ZabbixAuditError("не задан ZBX_AUDIT_ES_URL — некуда идти за аудитом")
    errors = []
    for base in config.ES_URLS:
        try:
            resp = await _http().post(
                f"{base}/{index}/_search",
                json=body, headers=_es_headers(), auth=_es_auth(),
            )
        except httpx.HTTPError as e:
            errors.append(f"нет связи с {base}: {e}")
            continue
        if resp.status_code in (401, 403):
            # Не повод идти на другую ноду: креды общие, ответ будет тем же.
            raise ZabbixAuditError(f"ES отказал в доступе ({resp.status_code}): {_es_reason(resp)}")
        if resp.status_code >= 400:
            # 4xx от живой ноды — ошибка запроса (поле, маппинг), а не ноды.
            if resp.status_code < 500:
                raise ZabbixAuditError(f"ES → {resp.status_code}: {_es_reason(resp)}")
            errors.append(f"{base} → {resp.status_code}: {_es_reason(resp)}")
            continue
        if errors:
            logging_setup.event(
                log, "es.node_failed", level=logging.WARNING,
                used=base, failed=errors, hint="часть нод ES недоступна, ответ взят со следующей",
            )
        return resp.json()
    logging_setup.event(log, "es.all_nodes_failed", level=logging.ERROR, nodes=config.ES_URLS, errors=errors)
    raise ZabbixAuditError("; ".join(errors))


# --- Zabbix API ---------------------------------------------------------------
# Кэш имён: (env, method, id) -> (истекает_в, значение). Имена объектов меняются
# редко, а одна страница аудита ссылается на одни и те же хосты/группы десятки раз.
# Ключей немного (видимые объекты), поэтому чистка не нужна — перезапись.
_NAME_TTL = 600
_names: dict = {}


async def _api_call(env: str, method: str, params: dict) -> list:
    """JSON-RPC вызов к первой живой ноде Zabbix API. Токен — заголовком Bearer
    (Zabbix 6.4+/7.x), а не полем auth: так он не попадает в тело запроса."""
    errors = []
    for url in config.API.get(env, []):
        try:
            resp = await _http().post(
                url,
                json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
                headers={"Authorization": f"Bearer {config.API_TOKEN.get(env, '')}"},
            )
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        if "error" in data:
            err = data["error"]
            # Ошибка уровня API (права, неверный параметр) одинакова на всех нодах.
            raise ZabbixAuditError(f"Zabbix API {method}: {err.get('data') or err.get('message')}")
        return data.get("result") or []
    raise ZabbixAuditError("; ".join(errors) or "Zabbix API не настроен")


async def resolve(env: str, method: str, id_field: str, ids: set, output: list, extra: dict | None = None) -> dict:
    """{id: объект} для набора ID через <method> (host.get, item.get…), с кэшем.

    Объекты, которых в Zabbix уже нет (удалены), просто не вернутся — вызывающий
    показывает ID как есть. Ошибку API наверх не бросаем: имена — украшение,
    без них аудит всё равно читается (деградация вместо отказа).
    """
    now = time.monotonic()
    out, missing = {}, []
    for i in ids:
        hit = _names.get((env, method, id_field, i))
        if hit and hit[0] > now:
            if hit[1] is not None:
                out[i] = hit[1]
        else:
            missing.append(i)
    if not missing:
        return out
    params = {id_field + "s": missing, "output": output, "preservekeys": True}
    params.update(extra or {})
    try:
        result = await _api_call(env, method, params)
    except ZabbixAuditError as e:
        logging_setup.event(
            log, "zabbix.api_failed", level=logging.WARNING, env=env, method=method, error=str(e),
            hint="имена объектов не подтянуты, аудит показан с ID",
        )
        return out
    # preservekeys → словарь id → объект (на пустом результате Zabbix отдаёт []).
    found = result if isinstance(result, dict) else {}
    for i in missing:
        obj = found.get(i)
        _names[(env, method, id_field, i)] = (now + _NAME_TTL, obj)  # None тоже кэшируем: удалён
        if obj is not None:
            out[i] = obj
    return out
