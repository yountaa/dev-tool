"""Настройки модуля alerts. Postgres — те же PG_* / PG_DSN, что у silences."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# Те же креды, что silences: либо PG_DSN, либо части.
PG_DSN = os.getenv("PG_DSN", "").strip()
PG_HOST = os.getenv("PG_HOST", "localhost").strip()
PG_PORT = os.getenv("PG_PORT", "5432").strip()
PG_DB = os.getenv("PG_DB", "devtool").strip()
PG_USER = os.getenv("PG_USER", "").strip()
PG_PASSWORD = os.getenv("PG_PASSWORD", "")

HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))


def pg_configured() -> bool:
    """Есть ли чем подключаться к Postgres."""
    return bool(PG_DSN or PG_USER)


def pg_dsn() -> str:
    if PG_DSN:
        return PG_DSN
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} "
        f"user={PG_USER} password={PG_PASSWORD}"
    )
