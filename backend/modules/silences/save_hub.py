"""Хранилище конфигов в git. Каждый конфиг — файл <env>/<kind>/<id>.json.

git вместо БД: видно историю, можно откатить. Всё на обычном git через
subprocess, без лишних либ.
"""
from __future__ import annotations  # str | None на Python 3.9

import logging
import queue
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import StoredConfig

log = logging.getLogger("silences.save_hub")

# Два независимых замка:
#   _lock     — короткие операции с файлами (запись/удаление конфигов);
#   _git_lock — медленные git-команды (clone/fetch/push) в фоне.
# Разнесены, чтобы фоновый push НЕ блокировал запись файлов (и значит — ответ API).
_lock = threading.Lock()
_git_lock = threading.Lock()


def _auth_url() -> str:
    # https://host/repo.git -> https://user:token@host/repo.git
    return config.GIT_REPO_URL.replace(
        "https://", f"https://{config.GIT_USER}:{config.GIT_TOKEN}@", 1
    )


def _git(*args: str) -> None:
    """git внутри папки локального клона. При ошибке — с текстом stderr от git."""
    res = subprocess.run(
        ["git", "-C", str(config.GIT_LOCAL_DIR), *args],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} -> {res.returncode}: {res.stderr.strip()}")


def ensure_repo() -> None:
    """Готовим локальную папку с правилами.

    С настроенным git — клон (первый раз) или обновление. Если git недоступен
    (плохой токен, нет сети) — НЕ падаем: работаем локально, синхронизация позже.
    """
    config.GIT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    if not config.GIT_ENABLED:
        return

    try:
        with _git_lock:
            if not (config.GIT_LOCAL_DIR / ".git").exists():
                res = subprocess.run(
                    ["git", "clone", "-b", config.GIT_BRANCH, _auth_url(), str(config.GIT_LOCAL_DIR)],
                    capture_output=True,
                    text=True,
                )
                if res.returncode != 0:
                    raise RuntimeError(res.stderr.strip())
            else:
                _git("fetch", "origin", config.GIT_BRANCH)
                _git("reset", "--hard", f"origin/{config.GIT_BRANCH}")
    except Exception as e:
        log.error("git недоступен, работаю локально: %s", e)


def _path(env: str, kind: str, cfg_id: str) -> Path:
    return config.GIT_LOCAL_DIR / env / kind / f"{cfg_id}.json"


# Очередь задач на commit+push. Сам push делает фоновый воркер, чтобы ответ API
# не ждал сети до GitHub (файл уже записан на диск — этого для работы достаточно).
_git_q: "queue.Queue[str]" = queue.Queue()


def _commit_push(message: str) -> None:
    if config.GIT_ENABLED:
        _git_q.put(message)  # просто ставим задачу, не блокируя запрос


def _git_worker() -> None:
    """Фоновый поток: коммитит и пушит накопившиеся изменения одним заходом."""
    while True:
        message = _git_q.get()
        # склеиваем все накопившиеся задачи в один push
        while not _git_q.empty():
            try:
                _git_q.get_nowait()
            except queue.Empty:
                break
        with _git_lock:
            try:
                _git("add", "-A")
                status = subprocess.run(
                    ["git", "-C", str(config.GIT_LOCAL_DIR), "status", "--porcelain"],
                    capture_output=True,
                    text=True,
                )
                if status.stdout.strip():  # есть что коммитить
                    # identity коммиттера задаём прямо в команде — в контейнере
                    # глобального git config нет, без этого commit падает.
                    name = config.GIT_USER or "dev-tool"
                    _git("-c", f"user.name={name}", "-c", f"user.email={name}@dev-tool.local",
                         "commit", "-m", message)
                    _git("push", "origin", config.GIT_BRANCH)
                    log.info("git: запушено (%s)", message)
            except Exception as e:
                log.error("git push не удался (конфиг сохранён локально): %s", e)


if config.GIT_ENABLED:
    threading.Thread(target=_git_worker, daemon=True).start()


def save(kind: str, env: str, payload: dict, cfg_id: str | None = None,
         enabled: bool = True, am_id: str = "", created_at: datetime | None = None) -> StoredConfig:
    """Создать новый конфиг или перезаписать существующий (если передан cfg_id)."""
    cfg = StoredConfig(
        id=cfg_id or uuid.uuid4().hex[:8],
        kind=kind,
        env=env,
        created_at=created_at or datetime.now(timezone.utc),
        payload=payload,
        enabled=enabled,
        am_id=am_id,
    )
    with _lock:
        file = _path(env, kind, cfg.id)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        _commit_push(f"{kind} {env}/{cfg.id}")
    return cfg


def list_configs(kind: str, env: str | None = None) -> list[StoredConfig]:
    """Конфиги одного типа. Без env — по всем окружениям (нужно шедулеру)."""
    root = config.GIT_LOCAL_DIR
    envs = [env] if env else [d.name for d in root.iterdir() if d.is_dir() and d.name != ".git"]
    result: list[StoredConfig] = []
    for e in envs:
        folder = root / e / kind
        if not folder.exists():
            continue
        for file in folder.glob("*.json"):
            result.append(StoredConfig.model_validate_json(file.read_text(encoding="utf-8")))
    return result


def get_config(kind: str, env: str, cfg_id: str) -> StoredConfig | None:
    """Достать один конфиг по id. None — если такого файла нет."""
    file = _path(env, kind, cfg_id)
    if not file.exists():
        return None
    return StoredConfig.model_validate_json(file.read_text(encoding="utf-8"))


def set_enabled(kind: str, env: str, cfg_id: str, enabled: bool) -> bool:
    """Включить/выключить конфиг (для шедулера). False — если конфига нет."""
    cfg = get_config(kind, env, cfg_id)
    if cfg is None:
        return False
    save(kind, env, cfg.payload, cfg_id=cfg_id, enabled=enabled, am_id=cfg.am_id, created_at=cfg.created_at)
    return True


def delete(kind: str, env: str, cfg_id: str) -> bool:
    """Удалить конфиг из репы. Возвращаем False, если удалять было нечего."""
    with _lock:
        file = _path(env, kind, cfg_id)
        if not file.exists():
            return False
        file.unlink()
        _commit_push(f"delete {kind} {env}/{cfg_id}")
    return True
