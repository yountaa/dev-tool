"""Postgres-хранилище метаданных модуля alerts (история + статус engine).

Отдельные таблицы alerts_history / alerts_meta — не трогаем configs/history silences.
Если Postgres недоступен или не настроен — методы тихо возвращают пустое
(модуль alerts во фронте всё равно ходит в n8n за конфигами).
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

from . import config

log = logging.getLogger("alerts.storage")

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_initialized = False
_unavailable = False  # PG нет / упал — не долбим на каждый запрос


def _get_pool() -> ThreadedConnectionPool | None:
    global _pool, _unavailable
    if _unavailable or not config.pg_configured():
        return None
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                try:
                    _pool = ThreadedConnectionPool(1, 5, dsn=config.pg_dsn())
                    log.info(
                        "alerts/postgres: %s:%s/%s",
                        config.PG_HOST, config.PG_PORT, config.PG_DB,
                    )
                except Exception as e:
                    _unavailable = True
                    log.error("alerts/postgres недоступен: %s", e)
                    return None
    return _pool


@contextmanager
def _conn():
    pool = _get_pool()
    if pool is None:
        yield None
        return
    con = pool.getconn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(con)


def ensure_schema() -> bool:
    """Создать таблицы alerts_* (идемпотентно). False — БД нет."""
    global _initialized, _unavailable
    if _initialized:
        return True
    if not config.pg_configured():
        log.info("alerts/postgres: PG не настроен — история и engine-кэш выключены")
        return False
    try:
        with _conn() as con:
            if con is None:
                return False
            with con.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_history (
                        id         bigserial   PRIMARY KEY,
                        ts         timestamptz NOT NULL,
                        "user"     text        NOT NULL,
                        action     text        NOT NULL,
                        name       text        NOT NULL DEFAULT '',
                        alert_key  text        NOT NULL DEFAULT '',
                        before     jsonb,
                        after      jsonb
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS alerts_history_ts "
                    "ON alerts_history (ts DESC);"
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_meta (
                        key   text PRIMARY KEY,
                        value jsonb NOT NULL,
                        updated_at timestamptz NOT NULL
                    );
                    """
                )
                # Зеркало конфигов из n8n — мгновенный UI на F5, n8n остаётся source of truth.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_configs (
                        alert_key         text PRIMARY KEY,
                        row_id            text,
                        name              text NOT NULL DEFAULT '',
                        enabled           boolean NOT NULL DEFAULT true,
                        type              text NOT NULL DEFAULT 'batch',
                        interval_minutes  int NOT NULL DEFAULT 30,
                        config            jsonb NOT NULL DEFAULT '{}',
                        state             jsonb,
                        updated_at        timestamptz NOT NULL
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS alerts_configs_name "
                    "ON alerts_configs (name);"
                )
        _initialized = True
        log.info("alerts/postgres: схема готова")
        return True
    except Exception as e:
        _unavailable = True
        log.error("alerts/postgres схема не создана: %s", e)
        return False


def get_engine() -> dict | None:
    """Кэш статуса backend-workflow: { lastRunAt, lastError } или None."""
    if not ensure_schema():
        return None
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                cur.execute(
                    "SELECT value FROM alerts_meta WHERE key = %s",
                    ("engine",),
                )
                row = cur.fetchone()
                if not row or not row[0]:
                    return None
                val = row[0]
                if isinstance(val, dict) and val.get("lastRunAt"):
                    return {
                        "lastRunAt": val["lastRunAt"],
                        "lastError": val.get("lastError"),
                    }
                return None
    except Exception as e:
        log.warning("alerts get_engine: %s", e)
        return None


def set_engine(last_run_at: str | None, last_error: str | None = None) -> dict | None:
    """Записать статус engine. Пустой lastRunAt — no-op."""
    if not last_run_at or not ensure_schema():
        return get_engine()
    payload = {"lastRunAt": last_run_at, "lastError": last_error}
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                # Не затирать более свежий кэш более старым значением.
                cur.execute("SELECT value FROM alerts_meta WHERE key = %s", ("engine",))
                row = cur.fetchone()
                if row and isinstance(row[0], dict) and row[0].get("lastRunAt"):
                    try:
                        old = datetime.fromisoformat(
                            str(row[0]["lastRunAt"]).replace("Z", "+00:00")
                        )
                        new = datetime.fromisoformat(
                            str(last_run_at).replace("Z", "+00:00")
                        )
                        if old > new:
                            return {
                                "lastRunAt": row[0]["lastRunAt"],
                                "lastError": row[0].get("lastError"),
                            }
                    except Exception:
                        pass
                cur.execute(
                    """
                    INSERT INTO alerts_meta (key, value, updated_at)
                    VALUES ('engine', %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (Json(payload), datetime.now(timezone.utc)),
                )
        return payload
    except Exception as e:
        log.warning("alerts set_engine: %s", e)
        return None


def history(limit: int = 500) -> list[dict]:
    if not ensure_schema():
        return []
    try:
        with _conn() as con:
            if con is None:
                return []
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts, "user", action, name, alert_key, before, after
                    FROM alerts_history
                    ORDER BY ts DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = cur.fetchall()
        out = []
        for ts, user, action, name, alert_key, before, after in rows:
            out.append({
                "time": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "user": user,
                "action": action,
                "name": name or "",
                "alertKey": alert_key or "",
                "before": before,
                "after": after,
            })
        return out
    except Exception as e:
        log.warning("alerts history: %s", e)
        return []


def record_history(
    user: str,
    action: str,
    name: str = "",
    alert_key: str = "",
    before: dict | None = None,
    after: dict | None = None,
) -> dict | None:
    if not ensure_schema():
        return None
    ts = datetime.now(timezone.utc)
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts_history
                        (ts, "user", action, name, alert_key, before, after)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        ts,
                        user or "dev-tool",
                        action,
                        name or "",
                        alert_key or "",
                        Json(before) if before is not None else None,
                        Json(after) if after is not None else None,
                    ),
                )
        return {
            "time": ts.isoformat(),
            "user": user or "dev-tool",
            "action": action,
            "name": name or "",
            "alertKey": alert_key or "",
            "before": before,
            "after": after,
        }
    except Exception as e:
        log.warning("alerts record_history: %s", e)
        return None


def cleanup(history_days: int | None = None) -> int:
    """Удалить историю старше N дней. Возвращает число удалённых строк."""
    days = history_days if history_days is not None else config.HISTORY_RETENTION_DAYS
    if not ensure_schema():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        with _conn() as con:
            if con is None:
                return 0
            with con.cursor() as cur:
                cur.execute("DELETE FROM alerts_history WHERE ts < %s", (cutoff,))
                return cur.rowcount or 0
    except Exception as e:
        log.warning("alerts cleanup: %s", e)
        return 0


def list_configs() -> list[dict]:
    """Кэш списка алертов (формат как у фронтового shapeAlert)."""
    if not ensure_schema():
        return []
    try:
        with _conn() as con:
            if con is None:
                return []
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT alert_key, row_id, name, enabled, type,
                           interval_minutes, config, state
                    FROM alerts_configs
                    ORDER BY name ASC, alert_key ASC
                    """
                )
                rows = cur.fetchall()
        out = []
        for key, row_id, name, enabled, typ, interval, conf, state in rows:
            out.append({
                "alertKey": key,
                "rowId": row_id,
                "name": name or key,
                "enabled": bool(enabled),
                "type": typ or (conf or {}).get("type") or "batch",
                "intervalMinutes": int(interval or 30),
                "config": conf or {},
                "state": state or {},
            })
        return out
    except Exception as e:
        log.warning("alerts list_configs: %s", e)
        return []


def replace_configs(alerts: list[dict]) -> int:
    """Полная синхронизация кэша с ответом n8n list (replace)."""
    if not ensure_schema():
        return 0
    now = datetime.now(timezone.utc)
    try:
        with _conn() as con:
            if con is None:
                return 0
            with con.cursor() as cur:
                cur.execute("DELETE FROM alerts_configs")
                for a in alerts or []:
                    key = a.get("alertKey") or a.get("configId") or ""
                    if not key:
                        continue
                    conf = a.get("config") or {}
                    cur.execute(
                        """
                        INSERT INTO alerts_configs
                            (alert_key, row_id, name, enabled, type,
                             interval_minutes, config, state, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            key,
                            a.get("rowId") or a.get("id"),
                            a.get("name") or key,
                            bool(a.get("enabled", True)),
                            a.get("type") or conf.get("type") or "batch",
                            int(a.get("intervalMinutes") or 30),
                            Json(conf),
                            Json(a.get("state") or {}),
                            now,
                        ),
                    )
                return len(alerts or [])
    except Exception as e:
        log.warning("alerts replace_configs: %s", e)
        return 0


def get_index_patterns() -> list[str]:
    if not ensure_schema():
        return []
    try:
        with _conn() as con:
            if con is None:
                return []
            with con.cursor() as cur:
                cur.execute(
                    "SELECT value FROM alerts_meta WHERE key = %s",
                    ("index_patterns",),
                )
                row = cur.fetchone()
                if row and isinstance(row[0], dict):
                    pats = row[0].get("patterns") or []
                    return [str(p) for p in pats if p]
                if row and isinstance(row[0], list):
                    return [str(p) for p in row[0] if p]
                return []
    except Exception as e:
        log.warning("alerts get_index_patterns: %s", e)
        return []


def set_index_patterns(patterns: list[str]) -> list[str]:
    if not ensure_schema():
        return patterns or []
    clean = sorted({str(p).strip() for p in (patterns or []) if str(p).strip()})
    try:
        with _conn() as con:
            if con is None:
                return clean
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts_meta (key, value, updated_at)
                    VALUES ('index_patterns', %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (Json({"patterns": clean}), datetime.now(timezone.utc)),
                )
        return clean
    except Exception as e:
        log.warning("alerts set_index_patterns: %s", e)
        return clean
