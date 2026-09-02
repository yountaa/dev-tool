"""HTTP-клиент к n8n webhook для lookup-справочников (fields / indices).

CRUD конфигов фронт по-прежнему шлёт напрямую в /webhook (nginx → n8n).
Через backend идут только fields/indices — чтобы кэшировать ответ в Postgres
и не дёргать ELK на каждый фокус/ввод.
"""
from __future__ import annotations

import logging

import httpx

import logging_setup

from . import config

log = logging.getLogger("alerts.client")

_TIMEOUT = httpx.Timeout(20.0, connect=3.0)
_client: httpx.AsyncClient | None = None


class AlertsN8nError(Exception):
    """n8n недоступен или вернул ошибку по lookup."""


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # trust_env=False — внутренние адреса не уводить в корпоративный прокси.
        _client = httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False)
    return _client


async def call_webhook(action: str, body: dict | None = None) -> dict:
    """POST {action, body} в n8n /webhook/alerts. Бросает AlertsN8nError."""
    url = config.n8n_webhook_url()
    if not url:
        raise AlertsN8nError("N8N_URL не задан")
    try:
        res = await _http().post(url, json={"action": action, "body": body or {}})
        res.raise_for_status()
        data = res.json()
    except httpx.HTTPError as e:
        logging_setup.event(
            log, "alerts.n8n_failed",
            level=logging.WARNING,
            action=action, error=str(e),
            hint="lookup fields/indices недоступен, UI останется на локальном fallback",
        )
        raise AlertsN8nError(str(e)) from e

    if isinstance(data, dict) and data.get("ok") is False:
        msg = data.get("message") or data.get("error") or "n8n lookup failed"
        raise AlertsN8nError(str(msg))
    return data if isinstance(data, dict) else {"ok": True}
