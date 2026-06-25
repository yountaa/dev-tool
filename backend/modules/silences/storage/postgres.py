"""Хранилище правил и истории в Postgres. Для нескольких нод приложения: обе
читают и пишут одну базу, поэтому видят одни и те же правила.

Тот же публичный API, что и у storage/local.py — модуль save_hub выбирает один из них.

Почему без дублей:
  • правила лежат по первичному ключу (env, kind, id); запись — UPSERT
    (INSERT ... ON CONFLICT DO UPDATE), повторное сохранение не плодит строки;
  • чтение всегда идёт прямо из БД (свежие данные на любой ноде);
  • шедулер на 2 нодах координируется advisory-локом Postgres (try_scheduler_lock),
    так что один и тот же silence/историю две ноды не поставят.

Просто и явно: обычный psycopg2, без ORM. Таблицы создаются сами при старте.
"""
from __future__ import annotations

import logging
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import Json
from psycopg2.pool import ThreadedConnectionPool

from .. import config
from ..models import StoredConfig

log = logging.getLogger("silences.save_hub")

# Произвольный постоянный ключ для pg_advisory_lock — общий для всех нод.
_LOCK_KEY = 728193

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_initialized = False


def _get_pool() -> ThreadedConnectionPool:
    """Пул соединений (создаётся один раз, лениво)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 10, dsn=config.pg_dsn())
                # пароль в лог не пишем — только куда подключились
                log.info("postgres: подключение к %s:%s/%s (пользователь %s)",
                         config.PG_HOST, config.PG_PORT, config.PG_DB, config.PG_USER or "(из PG_DSN)")
    return _pool


@contextmanager
def _conn():
    """Соединение из пула: коммит при успехе, откат при ошибке, возврат в пул всегда."""
    pool = _get_pool()
    con = pool.getconn()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        pool.putconn(con)


# --- Схема -------------------------------------------------------------------
def ensure_repo() -> None:
    """Создать таблицы, если их ещё нет (идемпотентно, один раз за процесс).

    Зовётся при старте и на каждом тике шедулера — после первого успеха ничего не делает.
    Если БД недоступна — не валим сервис: залогируем и попробуем снова на следующем тике.
    """
    global _initialized
    if _initialized:
        return
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS configs (
                    env        text        NOT NULL,
                    kind       text        NOT NULL,
                    id         text        NOT NULL,
                    created_at timestamptz NOT NULL,
                    payload    jsonb       NOT NULL,
                    enabled    boolean     NOT NULL DEFAULT true,
                    am_id      text        NOT NULL DEFAULT '',
                    deleted_at timestamptz,                 -- мягкое удаление (аналог old/)
                    PRIMARY KEY (env, kind, id)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id      bigserial   PRIMARY KEY,
                    env     text        NOT NULL,
                    ts      timestamptz NOT NULL,
                    "user"  text        NOT NULL,
                    action  text        NOT NULL,
                    kind    text        NOT NULL,
                    name    text        NOT NULL,
                    before  jsonb,
                    after   jsonb
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS history_env_ts ON history (env, ts DESC);")
        _initialized = True
        log.info("postgres: схема готова")
    except Exception as e:
        log.error("postgres недоступен (повторю позже): %s", e)


# --- Маппинг строк -----------------------------------------------------------
_COLS = "env, kind, id, created_at, payload, enabled, am_id"


def _row_to_cfg(row) -> StoredConfig:
    """Строка configs (порядок _COLS) → StoredConfig."""
    env, kind, cid, created_at, payload, enabled, am_id = row
    return StoredConfig(
        id=cid, kind=kind, env=env, created_at=created_at,
        payload=payload, enabled=enabled, am_id=am_id or "",
    )


def _snap(payload: dict | None, enabled: bool | None) -> dict | None:
    """Снимок правила для истории: payload + enabled (как в local._snap)."""
    if payload is None:
        return None
    return {**payload, "enabled": enabled}


def _record(cur, env, user, action, kind, name, before, after) -> None:
    """Дописать запись истории в той же транзакции, что и изменение правила."""
    cur.execute(
        'INSERT INTO history (env, ts, "user", action, kind, name, before, after) '
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (env, datetime.now(timezone.utc), user or "dev-tool", action, kind, name,
         Json(before) if before is not None else None,
         Json(after) if after is not None else None),
    )


# --- Запись/чтение правил ----------------------------------------------------
def save(kind: str, env: str, payload: dict, cfg_id: str | None = None,
         enabled: bool = True, am_id: str = "", created_at: datetime | None = None,
         actor: str = "dev-tool", action: str = "изменил") -> StoredConfig:
    """Создать конфиг или перезаписать существующий (UPSERT по env,kind,id).

    Повторная запись того же id обновляет строку, а не плодит дубль. Историю пишем
    только при значимом изменении (новый конфиг либо изменился payload/enabled) —
    чтобы переприменения шедулера (меняется только am_id) не засоряли журнал.
    """
    cfg = StoredConfig(
        id=cfg_id or uuid.uuid4().hex[:8],
        kind=kind, env=env,
        created_at=created_at or datetime.now(timezone.utc),
        payload=payload, enabled=enabled, am_id=am_id,
    )
    with _conn() as con, con.cursor() as cur:
        # снимок «до» (для истории и отсева no-op)
        cur.execute(
            "SELECT payload, enabled FROM configs WHERE env=%s AND kind=%s AND id=%s",
            (env, kind, cfg.id),
        )
        row = cur.fetchone()
        prev_payload, prev_enabled = (row[0], row[1]) if row else (None, None)

        cur.execute(
            """
            INSERT INTO configs (env, kind, id, created_at, payload, enabled, am_id, deleted_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT (env, kind, id) DO UPDATE SET
                created_at = EXCLUDED.created_at,
                payload    = EXCLUDED.payload,
                enabled    = EXCLUDED.enabled,
                am_id      = EXCLUDED.am_id,
                deleted_at = NULL
            """,
            (env, kind, cfg.id, cfg.created_at, Json(payload), enabled, am_id),
        )

        if row is None or prev_payload != payload or prev_enabled != enabled:
            name = payload.get("name") or cfg.id
            _record(cur, env, actor, action, kind, name,
                    _snap(prev_payload, prev_enabled), _snap(payload, enabled))
    return cfg


def list_configs(kind: str, env: str | None = None) -> list[StoredConfig]:
    """Конфиги одного типа (без env — по всем окружениям, нужно шедулеру). Без удалённых."""
    sql = f"SELECT {_COLS} FROM configs WHERE kind=%s AND deleted_at IS NULL"
    params: list = [kind]
    if env is not None:
        sql += " AND env=%s"
        params.append(env)
    sql += " ORDER BY created_at"
    with _conn() as con, con.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [_row_to_cfg(r) for r in rows]


def get_config(kind: str, env: str, cfg_id: str) -> StoredConfig | None:
    """Один конфиг по id (None — нет такого или удалён)."""
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            f"SELECT {_COLS} FROM configs WHERE env=%s AND kind=%s AND id=%s AND deleted_at IS NULL",
            (env, kind, cfg_id),
        )
        row = cur.fetchone()
    return _row_to_cfg(row) if row else None


def delete(kind: str, env: str, cfg_id: str, actor: str = "dev-tool") -> bool:
    """«Удалить» правило: мягко (deleted_at=now), из веба пропадает, в истории остаётся.

    False — если удалять нечего (нет такого активного правила).
    """
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "SELECT payload, enabled FROM configs WHERE env=%s AND kind=%s AND id=%s AND deleted_at IS NULL",
            (env, kind, cfg_id),
        )
        row = cur.fetchone()
        if row is None:
            return False
        prev_payload, prev_enabled = row[0], row[1]
        cur.execute(
            "UPDATE configs SET deleted_at=%s WHERE env=%s AND kind=%s AND id=%s",
            (datetime.now(timezone.utc), env, kind, cfg_id),
        )
        name = (prev_payload or {}).get("name") or cfg_id
        _record(cur, env, actor, "удалил", kind, name, _snap(prev_payload, prev_enabled), None)
    return True


# --- История -----------------------------------------------------------------
def history(env: str, limit: int = 500) -> list[dict]:
    """История окружения, новые сверху. Формат тот же, что у local (ключ time + tz)."""
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            'SELECT ts, "user", action, kind, name, before, after '
            "FROM history WHERE env=%s ORDER BY ts DESC LIMIT %s",
            (env, limit),
        )
        rows = cur.fetchall()
    items = []
    for ts, user, action, kind, name, before, after in rows:
        items.append({
            "time": ts.isoformat(),  # с таймзоной — фронт покажет в МСК
            "user": user,
            "action": action,
            "kind": kind,
            "name": name,
            "before": before,
            "after": after,
        })
    return items


def cleanup(history_days: int, old_days: int) -> dict:
    """Подрезать: историю старше history_days и мягко удалённые правила старше old_days."""
    now = datetime.now(timezone.utc)
    h_cutoff = now - timedelta(days=history_days)
    o_cutoff = now - timedelta(days=old_days)
    removed = {"history": 0, "old": 0}
    with _conn() as con, con.cursor() as cur:
        cur.execute("DELETE FROM history WHERE ts < %s", (h_cutoff,))
        removed["history"] = cur.rowcount
        cur.execute("DELETE FROM configs WHERE deleted_at IS NOT NULL AND deleted_at < %s", (o_cutoff,))
        removed["old"] = cur.rowcount
    log.info("очистка: история -%d, удалённые -%d", removed["history"], removed["old"])
    return removed


# --- Лок шедулера (чтобы из 2 нод тикала одна) -------------------------------
# Держим лок на ОТДЕЛЬНОМ долгоживущем соединении: advisory-lock сессионный, его
# нельзя брать из пула (соединение вернётся в пул и лок потеряется/не отпустится).
_lock_conn = None
_lock_held = False


def try_scheduler_lock() -> bool:
    """Попытаться взять общий лок шедулера. True — мы владелец, можно тикать."""
    global _lock_conn, _lock_held
    try:
        if _lock_conn is None or _lock_conn.closed:
            _lock_conn = psycopg2.connect(config.pg_dsn())
            _lock_conn.autocommit = True
        with _lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (_LOCK_KEY,))
            _lock_held = bool(cur.fetchone()[0])
        return _lock_held
    except Exception as e:
        log.warning("postgres: не удалось взять лок шедулера: %s", e)
        return False


def release_scheduler_lock() -> None:
    """Отпустить лок шедулера (чтобы успела другая нода, если эта встанет)."""
    global _lock_held
    if not _lock_held or _lock_conn is None:
        return
    try:
        with _lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    except Exception as e:
        log.warning("postgres: не удалось отпустить лок шедулера: %s", e)
    finally:
        _lock_held = False
