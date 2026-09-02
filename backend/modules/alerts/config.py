"""Настройки модуля alerts.

Креды Postgres — из modules.silences.config (общее хранилище приложения).
Свои переменные: N8N_URL и TTL кэша lookup (fields / indices).
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

import logging_setup
from modules.silences import config as storage_config

load_dotenv()

log = logging.getLogger("alerts.config")

# Origin n8n без хвостового /webhook — backend ходит туда за fields/indices
# (фронт для CRUD/preview по-прежнему бьёт в /webhook через nginx).
N8N_URL = os.getenv("N8N_URL", "").strip()

# TTL lookup-кэша в Postgres. fields меняются редко, indices — чаще.
FIELDS_CACHE_TTL_SECONDS = int(os.getenv("ALERTS_FIELDS_CACHE_TTL_SECONDS", "3600"))
INDICES_CACHE_TTL_SECONDS = int(os.getenv("ALERTS_INDICES_CACHE_TTL_SECONDS", "900"))

# Retention истории — тот же HISTORY_RETENTION_DAYS, что у silences.
HISTORY_RETENTION_DAYS = storage_config.HISTORY_RETENTION_DAYS


def pg_configured() -> bool:
    """Есть ли чем подключаться к Postgres."""
    return bool(storage_config.PG_DSN or storage_config.PG_USER)


def pg_dsn() -> str:
    return storage_config.pg_dsn()


def n8n_webhook_url() -> str:
    base = N8N_URL.rstrip("/")
    return f"{base}/webhook/alerts" if base else ""


def log_config() -> None:
    """Что прочитано из окружения — одной строкой на старте (зовётся из lifespan)."""
    logging_setup.event(
        log, "alerts.config",
        n8n=bool(N8N_URL),
        pg=pg_configured(),
        fields_ttl_s=FIELDS_CACHE_TTL_SECONDS,
        indices_ttl_s=INDICES_CACHE_TTL_SECONDS,
    )
