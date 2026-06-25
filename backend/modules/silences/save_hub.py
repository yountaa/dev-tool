"""Хранилище правил и истории — единая точка входа (диспетчер).

Весь модуль (routes, scheduler) зовёт save_hub.X(), не зная, где данные лежат.
Конкретный бэкенд выбирается переменной STORAGE_BACKEND один раз при старте:
  • local    → storage/local.py    (git/файлы);
  • postgres → storage/postgres.py (общая БД, для нескольких нод).

Публичный API одинаков у обоих бэкендов:
  ensure_repo(), save(...), list_configs(...), get_config(...), delete(...),
  history(...), cleanup(...), try_scheduler_lock(), release_scheduler_lock().
"""
from __future__ import annotations

import logging

from . import config

log = logging.getLogger("silences.save_hub")

# Выбираем бэкенд по переменной. Импортируем ленивно — чтобы в режиме local не
# тянуть psycopg2 (и наоборот). Неизвестное значение → local (безопасный дефолт).
if config.STORAGE_BACKEND == "postgres":
    from .storage import postgres as _be
    log.info("хранилище: Postgres")
else:
    from .storage import local as _be
    if config.STORAGE_BACKEND != "local":
        log.warning("неизвестный STORAGE_BACKEND=%s — работаю как local", config.STORAGE_BACKEND)
    else:
        log.info("хранилище: локальное (git/файлы)")


# --- Проброс вызовов в выбранный бэкенд --------------------------------------
def ensure_repo():
    return _be.ensure_repo()


def save(*args, **kwargs):
    return _be.save(*args, **kwargs)


def list_configs(*args, **kwargs):
    return _be.list_configs(*args, **kwargs)


def get_config(*args, **kwargs):
    return _be.get_config(*args, **kwargs)


def delete(*args, **kwargs):
    return _be.delete(*args, **kwargs)


def history(*args, **kwargs):
    return _be.history(*args, **kwargs)


def cleanup(*args, **kwargs):
    return _be.cleanup(*args, **kwargs)


def try_scheduler_lock() -> bool:
    """Взять лок на тик шедулера (чтобы из 2 нод работала одна). local — всегда True."""
    return _be.try_scheduler_lock()


def release_scheduler_lock() -> None:
    return _be.release_scheduler_lock()


# --- Backend-агностичная проверка дублей -------------------------------------
# Зависит только от list_configs, поэтому одна на оба хранилища.
def _matchers_key(matchers: list) -> set:
    """Набор матчеров без учёта порядка — для сравнения правил."""
    return {(m.get("name"), m.get("value"), bool(m.get("isRegex"))) for m in (matchers or [])}


def find_duplicate(kind: str, env: str, payload: dict, exclude_id: str | None = None):
    """Уже есть правило с такими же матчерами и концом действия? Вернуть его (или None).

    manual — сравниваем по ends_at, schedule — по набору окон.
    """
    new_matchers = _matchers_key(payload.get("matchers"))
    for cfg in list_configs(kind, env):
        if cfg.id == exclude_id:
            continue
        if _matchers_key(cfg.payload.get("matchers")) != new_matchers:
            continue
        if kind == "manual":
            if cfg.payload.get("ends_at") == payload.get("ends_at"):
                return cfg
        else:
            if cfg.payload.get("windows") == payload.get("windows"):
                return cfg
    return None
