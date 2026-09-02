"""Postgres-хранилище метаданных модуля alerts.

Отдельные таблицы alerts_* — чужих (configs/history silences) не трогаем.
Креды — из общего PG_* через config (→ silences.config).
БД недоступна — методы тихо возвращают пустое; модуль работает без истории/кэша.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

import logging_setup
from modules.silences import config as storage_config

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
                    logging_setup.event(
                        log, "storage.pg_connected",
                        host=storage_config.PG_HOST,
                        port=storage_config.PG_PORT,
                        db=storage_config.PG_DB,
                    )
                except Exception as e:
                    _unavailable = True
                    logging_setup.event(
                        log, "storage.pg_unavailable",
                        level=logging.ERROR,
                        error=str(e),
                        hint="модуль alerts работает без истории и кэша",
                    )
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
        logging_setup.event(
            log, "storage.pg_skipped",
            hint="PG не настроен — история и кэш alerts выключены",
        )
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
                # Папки — метаданные UI в Postgres (конфиги алертов остаются в n8n).
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_folders (
                        id          bigserial   PRIMARY KEY,
                        name        text        NOT NULL,
                        sort_order  int         NOT NULL DEFAULT 0,
                        created_at  timestamptz NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS alerts_folder_members (
                        alert_key  text PRIMARY KEY,
                        folder_id  bigint NOT NULL
                                   REFERENCES alerts_folders(id) ON DELETE CASCADE
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS alerts_folder_members_folder "
                    "ON alerts_folder_members (folder_id);"
                )
        _initialized = True
        logging_setup.event(
            log, "storage.schema_ready",
            tables=[
                "alerts_history", "alerts_meta", "alerts_configs",
                "alerts_folders", "alerts_folder_members",
            ],
        )
        return True
    except Exception as e:
        _unavailable = True
        logging_setup.event(
            log, "storage.pg_unavailable",
            level=logging.ERROR,
            error=str(e),
            hint="модуль alerts работает без истории и кэша",
        )
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


def _lookup_meta_key(kind: str, token: str) -> str:
    return f"lookup:{kind}:{token}"


def lookup_cache_token(payload: object) -> str:
    """Стабильный ключ кэша для lookup-запроса."""
    try:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(payload)


def get_lookup_cache(kind: str, token: str, ttl_seconds: int) -> dict | None:
    """Прочитать lookup-кэш (fields/indices), если TTL ещё не истёк."""
    if ttl_seconds <= 0 or not ensure_schema():
        return None
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                cur.execute(
                    "SELECT value, updated_at FROM alerts_meta WHERE key = %s",
                    (_lookup_meta_key(kind, token),),
                )
                row = cur.fetchone()
                if not row or not isinstance(row[0], dict) or not row[1]:
                    return None
                age = datetime.now(timezone.utc) - row[1]
                if age.total_seconds() > ttl_seconds:
                    return None
                payload = row[0].get("payload")
                return payload if isinstance(payload, dict) else None
    except Exception as e:
        log.warning("alerts get_lookup_cache(%s): %s", kind, e)
        return None


def set_lookup_cache(kind: str, token: str, payload: dict) -> dict:
    """Записать lookup-кэш в alerts_meta."""
    data = payload if isinstance(payload, dict) else {}
    if not ensure_schema():
        return data
    try:
        with _conn() as con:
            if con is None:
                return data
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO alerts_meta (key, value, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        _lookup_meta_key(kind, token),
                        Json({"payload": data}),
                        datetime.now(timezone.utc),
                    ),
                )
        return data
    except Exception as e:
        log.warning("alerts set_lookup_cache(%s): %s", kind, e)
        return data


# --- Папки алертов (Postgres-only) -------------------------------------------

def list_folders() -> list[dict]:
    """Папки + membership: [{id, name, sortOrder, alertKeys: [...]}]."""
    if not ensure_schema():
        return []
    try:
        with _conn() as con:
            if con is None:
                return []
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, sort_order
                    FROM alerts_folders
                    ORDER BY sort_order ASC, name ASC, id ASC
                    """
                )
                folders = cur.fetchall()
                cur.execute(
                    "SELECT alert_key, folder_id FROM alerts_folder_members"
                )
                members = cur.fetchall()
        by_id: dict[int, list[str]] = {}
        for alert_key, folder_id in members:
            by_id.setdefault(int(folder_id), []).append(str(alert_key))
        out = []
        for fid, name, sort_order in folders:
            keys = by_id.get(int(fid), [])
            keys.sort()
            out.append({
                "id": int(fid),
                "name": name or "",
                "sortOrder": int(sort_order or 0),
                "alertKeys": keys,
            })
        return out
    except Exception as e:
        log.warning("alerts list_folders: %s", e)
        return []


def create_folder(name: str, sort_order: int | None = None) -> dict | None:
    title = (name or "").strip()
    if not title or not ensure_schema():
        return None
    now = datetime.now(timezone.utc)
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                order = sort_order
                if order is None:
                    cur.execute(
                        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM alerts_folders"
                    )
                    order = int(cur.fetchone()[0] or 0)
                cur.execute(
                    """
                    INSERT INTO alerts_folders (name, sort_order, created_at)
                    VALUES (%s, %s, %s)
                    RETURNING id, name, sort_order
                    """,
                    (title, int(order), now),
                )
                row = cur.fetchone()
        return {
            "id": int(row[0]),
            "name": row[1],
            "sortOrder": int(row[2] or 0),
            "alertKeys": [],
        }
    except Exception as e:
        log.warning("alerts create_folder: %s", e)
        return None


def rename_folder(folder_id: int, name: str) -> dict | None:
    title = (name or "").strip()
    if not title or not ensure_schema():
        return None
    try:
        with _conn() as con:
            if con is None:
                return None
            with con.cursor() as cur:
                cur.execute(
                    """
                    UPDATE alerts_folders SET name = %s
                    WHERE id = %s
                    RETURNING id, name, sort_order
                    """,
                    (title, int(folder_id)),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    "SELECT alert_key FROM alerts_folder_members WHERE folder_id = %s",
                    (int(folder_id),),
                )
                keys = sorted(str(r[0]) for r in cur.fetchall())
        return {
            "id": int(row[0]),
            "name": row[1],
            "sortOrder": int(row[2] or 0),
            "alertKeys": keys,
        }
    except Exception as e:
        log.warning("alerts rename_folder: %s", e)
        return None


def delete_folder(folder_id: int) -> bool:
    """Удалить папку; алерты становятся без папки (CASCADE на members)."""
    if not ensure_schema():
        return False
    try:
        with _conn() as con:
            if con is None:
                return False
            with con.cursor() as cur:
                cur.execute(
                    "DELETE FROM alerts_folders WHERE id = %s",
                    (int(folder_id),),
                )
                return (cur.rowcount or 0) > 0
    except Exception as e:
        log.warning("alerts delete_folder: %s", e)
        return False


def move_alert(alert_key: str, folder_id: int | None) -> dict:
    """folder_id=None — убрать из папки. Возвращает {alertKey, folderId, ok}."""
    key = (alert_key or "").strip()
    if not key or not ensure_schema():
        return {"alertKey": key, "folderId": None, "ok": False}
    try:
        with _conn() as con:
            if con is None:
                return {"alertKey": key, "folderId": None, "ok": False}
            with con.cursor() as cur:
                if folder_id is None:
                    cur.execute(
                        "DELETE FROM alerts_folder_members WHERE alert_key = %s",
                        (key,),
                    )
                    return {"alertKey": key, "folderId": None, "ok": True}
                fid = int(folder_id)
                cur.execute("SELECT 1 FROM alerts_folders WHERE id = %s", (fid,))
                if not cur.fetchone():
                    return {"alertKey": key, "folderId": None, "ok": False}
                cur.execute(
                    """
                    INSERT INTO alerts_folder_members (alert_key, folder_id)
                    VALUES (%s, %s)
                    ON CONFLICT (alert_key) DO UPDATE SET folder_id = EXCLUDED.folder_id
                    """,
                    (key, fid),
                )
                return {"alertKey": key, "folderId": fid, "ok": True}
    except Exception as e:
        log.warning("alerts move_alert: %s", e)
        return {"alertKey": key, "folderId": None, "ok": False}


def prune_folder_members(valid_alert_keys: list[str] | None = None) -> int:
    """Удалить membership для ключей, которых больше нет в n8n/кэше."""
    if valid_alert_keys is None or not ensure_schema():
        return 0
    valid = {str(k) for k in valid_alert_keys if k}
    try:
        with _conn() as con:
            if con is None:
                return 0
            with con.cursor() as cur:
                cur.execute("SELECT alert_key FROM alerts_folder_members")
                existing = [str(r[0]) for r in cur.fetchall()]
                stale = [k for k in existing if k not in valid]
                for k in stale:
                    cur.execute(
                        "DELETE FROM alerts_folder_members WHERE alert_key = %s",
                        (k,),
                    )
                return len(stale)
    except Exception as e:
        log.warning("alerts prune_folder_members: %s", e)
        return 0
