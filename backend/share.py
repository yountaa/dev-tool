"""Короткие ссылки на текущий вид интерфейса.

Фронт держит всё состояние страницы (модуль, кластер, тенант, вкладка, запросы,
режим, время) в query-параметрах URL — такую ссылку можно отправить коллеге, и он
увидит то же самое. Но она длинная, поэтому кнопка «ссылка» в шапке просит у нас
короткую: POST /share сохраняет путь и отдаёт id, GET /s/<id> отвечает редиректом
на полный путь.

Хранилище — как у правил silences, выбирается тем же STORAGE_BACKEND:
  • postgres (прод) — таблица short_links в общей БД (те же PG_*-креды из
    silences.config): ссылки видны со всех нод приложения и переживают деплой;
  • local (стенд/локальный режим) — JSON-файл SHORT_LINKS_FILE
    (по умолчанию ./data/short_links.json), без БД.

id — первые 8 символов sha256 от пути: одинаковый вид даёт одинаковый id,
повторные «поделиться» не плодят записи.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

# PG-креды и выбор бэкенда общие для всего приложения — живут в silences.config
# (STORAGE_BACKEND, PG_DSN/PG_HOST/...). Отдельный конфиг не заводим.
from modules.silences import config as storage_config

log = logging.getLogger("share")

router = APIRouter(tags=["share"])

MAX_PATH_LEN = 4096
USE_PG = storage_config.STORAGE_BACKEND == "postgres"


# --- Postgres (STORAGE_BACKEND=postgres) ---------------------------------------
# Соединение на операцию, без пула: делиться ссылкой — редкое действие, а так
# модуль не тянет зависимостей из silences.storage. Таблица создаётся лениво.
_table_ready = False


def _pg_conn():
    import psycopg2  # импорт тут — local-режиму psycopg2 не нужен
    con = psycopg2.connect(storage_config.pg_dsn())
    con.autocommit = True
    return con


def _pg_ensure(cur) -> None:
    global _table_ready
    if _table_ready:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS short_links (
            id         text        PRIMARY KEY,
            path       text        NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    _table_ready = True


def _pg_save(link_id: str, path: str) -> None:
    with _pg_conn() as con, con.cursor() as cur:
        _pg_ensure(cur)
        # тот же id = тот же путь (id — хэш пути), конфликт просто игнорируем
        cur.execute(
            "INSERT INTO short_links (id, path) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (link_id, path),
        )


def _pg_get(link_id: str):
    with _pg_conn() as con, con.cursor() as cur:
        _pg_ensure(cur)
        cur.execute("SELECT path FROM short_links WHERE id=%s", (link_id,))
        row = cur.fetchone()
    return row[0] if row else None


# --- JSON-файл (STORAGE_BACKEND=local) ------------------------------------------
def _file() -> Path:
    """Путь к файлу-хранилищу (читаем при каждом обращении — удобно для тестов)."""
    return Path(os.environ.get("SHORT_LINKS_FILE", "./data/short_links.json"))


def _local_load() -> dict:
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}  # файла ещё нет или он битый — начинаем с пустого


def _local_save(link_id: str, path: str) -> None:
    links = _local_load()
    if links.get(link_id) != path:
        links[link_id] = path
        f = _file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(links, ensure_ascii=False, indent=0), encoding="utf-8")


# --- Роуты ----------------------------------------------------------------------
class SharePayload(BaseModel):
    path: str


@router.post("/share")
def create(payload: SharePayload):
    """Сохранить путь вида «/?m=victoria&env=…» и вернуть короткий id."""
    path = payload.path
    # Принимаем ТОЛЬКО путь внутри приложения: без схемы/хоста (иначе получится
    # open redirect — редирект на чужой сайт с нашего домена) и без гигантов.
    if not path.startswith("/") or path.startswith("//") or len(path) > MAX_PATH_LEN:
        raise HTTPException(status_code=400, detail="ожидается относительный путь вида /?m=…")
    link_id = hashlib.sha256(path.encode()).hexdigest()[:8]
    try:
        if USE_PG:
            _pg_save(link_id, path)
        else:
            _local_save(link_id, path)
    except Exception as e:
        log.error("не удалось сохранить короткую ссылку: %s", e)
        raise HTTPException(status_code=503, detail=f"хранилище ссылок недоступно: {e}")
    return {"id": link_id}


@router.get("/s/{link_id}")
def resolve(link_id: str):
    """Редирект с короткой ссылки на сохранённый полный путь."""
    try:
        path = _pg_get(link_id) if USE_PG else _local_load().get(link_id)
    except Exception as e:
        log.error("не удалось прочитать короткую ссылку: %s", e)
        raise HTTPException(status_code=503, detail=f"хранилище ссылок недоступно: {e}")
    if not path:
        raise HTTPException(status_code=404, detail="ссылка не найдена или устарела")
    return RedirectResponse(url=path, status_code=302)
