"""Хранилище конфигов в git. Каждый конфиг — файл <env>/<kind>/<id>.json.

git вместо БД: видно историю, можно откатить. Всё на обычном git через
subprocess, без лишних либ.
"""
from __future__ import annotations  # str | None на Python 3.9

import json
import logging
import queue
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..models import StoredConfig

log = logging.getLogger("silences.save_hub")

# Два независимых замка:
#   _lock     — короткие операции с файлами (запись/удаление конфигов);
#   _git_lock — медленные git-команды (clone/fetch/push) в фоне.
# Разнесены, чтобы фоновый push НЕ блокировал запись файлов (и значит — ответ API).
_lock = threading.Lock()
_git_lock = threading.Lock()


def _redact(text: str) -> str:
    """Прячем токен в логах/сообщениях об ошибке, если он туда просочился."""
    if config.GIT_TOKEN and text:
        text = text.replace(config.GIT_TOKEN, "***")
    return text


def _auth_url() -> str:
    """Адрес репозитория с вшитыми логином и токеном (только https).

    Так git всегда ходит по https с токеном и НИКОГДА не сваливается в ssh.
    """
    url = config.GIT_REPO_URL
    if not url.startswith("https://"):
        log.warning("GIT_REPO_URL должен быть https (сейчас: %s) — токен не подставить", url)
        return url
    return url.replace("https://", f"https://{config.GIT_USER}:{config.GIT_TOKEN}@", 1)


def _git(*args: str, check: bool = True, retries: int = 0) -> subprocess.CompletedProcess:
    """Запустить git в папке хранилища. check=False — вернуть результат, не падая.

    retries>0 — для СЕТЕВЫХ команд (fetch/push/ls-remote/clone): повторяем при
    ненулевом коде с нарастающей паузой. Сеть/удалёнка иногда моргают (DNS, TLS,
    таймаут proxy) — один такой сбой не должен ронять вкладку так, что спасает
    только перезагрузка страницы. Локальные команды зовём без retries (их незачем
    повторять — упали, значит упали по делу)."""
    delay = 0.5
    for attempt in range(retries + 1):
        res = subprocess.run(
            ["git", "-C", str(config.GIT_LOCAL_DIR), *args],
            capture_output=True, text=True,
        )
        if res.returncode == 0 or attempt == retries:
            break
        log.warning(
            "git %s не удалось (попытка %d/%d), повторю через %.1fs: %s",
            args[0], attempt + 1, retries + 1, delay, _redact(res.stderr.strip()),
        )
        time.sleep(delay)
        delay *= 2
    if check and res.returncode != 0:
        # в сообщение кладём только имя подкоманды — чтобы не светить url с токеном
        raise RuntimeError(f"git {args[0]} -> {res.returncode}: {_redact(res.stderr.strip())}")
    return res


# Сколько раз докручиваем сетевые git-команды перед тем, как признать сбой.
_NET_RETRIES = 2


def _remote_has_branch() -> bool:
    """Есть ли наша ветка на удалёнке (у пустой/новой репы её ещё нет)."""
    res = _git("ls-remote", "--heads", "origin", config.GIT_BRANCH, check=False, retries=_NET_RETRIES)
    return res.returncode == 0 and bool(res.stdout.strip())


def _ensure_remote() -> None:
    """origin всегда указывает на актуальный https+token адрес (чиним протухший/ssh)."""
    if _git("remote", "get-url", "origin", check=False).returncode == 0:
        _git("remote", "set-url", "origin", _auth_url())
    else:
        _git("remote", "add", "origin", _auth_url())


def _local_ahead() -> bool:
    """Есть ли у нас свои коммиты, которых нет на удалёнке (их нельзя терять)."""
    if _git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
        return False  # ещё ни одного коммита
    res = _git("rev-list", f"origin/{config.GIT_BRANCH}..HEAD", check=False)
    return res.returncode == 0 and bool(res.stdout.strip())


def _sync_onto_remote() -> None:
    """Совместить локальную ветку с удалёнкой, не теряя наши правила.

    Без своих коммитов — просто перематываемся на удалёнку. Если разошлись — сливаем
    удалёнку в нашу ветку (`merge`), причём:
      • -X ours — при конфликте по файлу побеждает НАШ конфиг (приложение — владелец правил);
      • --allow-unrelated-histories — переживаем даже разные «корни» истории
        (репа создана с README, а локально была своя история — частый случай).
    Чужие файлы удалёнки (README и т.п.) при этом сохраняются.
    """
    ident = ["-c", f"user.name={config.GIT_USER or 'dev-tool'}",
             "-c", f"user.email={_email_for(config.GIT_USER or 'dev-tool')}"]
    # Сначала фиксируем ВСЁ незакоммиченное (в т.ч. untracked, напр. примонтированный
    # README) — иначе git откажется мерджить «чтобы не затереть untracked файлы».
    _git("add", "-A")
    if _has_staged():
        _git(*ident, "commit", "-q", "-m", "локальные изменения хранилища")
    _git("fetch", "origin", config.GIT_BRANCH, retries=_NET_RETRIES)
    _git("checkout", "-B", config.GIT_BRANCH)
    if not _local_ahead():
        _git("reset", "--hard", f"origin/{config.GIT_BRANCH}")
        return
    res = _git(*ident, "merge", "--allow-unrelated-histories", "-X", "ours",
               "-m", "слияние с удалёнкой (хаб правил)", f"origin/{config.GIT_BRANCH}", check=False)
    if res.returncode != 0:
        _git("merge", "--abort", check=False)
        raise RuntimeError("не удалось слить локальные правила с удалёнкой")


def ensure_repo() -> None:
    """Подготовить локальное хранилище и синхронизироваться с удалёнкой.

    Чтобы всё завелось, достаточно задать GIT_REPO_URL (https) + GIT_USER + GIT_TOKEN
    и выдать токену доступ к репе. Папку инициализируем НА МЕСТЕ (git init), а не
    `git clone`: так не мешает примонтированный README и работает даже пустая репа.
    origin всегда чиним на https+token (не уходим в ssh). Свои незапушенные коммиты
    не теряем — сливаем с удалёнкой (см. _sync_onto_remote), переживая даже разные
    истории. Любая ошибка git не валит сервис — работаем локально.
    """
    config.GIT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    if not config.GIT_ENABLED:
        log.info("git выключен (нет GIT_REPO_URL/USER/TOKEN) — храню правила только локально")
        return
    try:
        with _git_lock:
            if not (config.GIT_LOCAL_DIR / ".git").exists():
                _git("init", "-q")
            _ensure_remote()
            # дефолтная identity (на случай, если у действия нет автора)
            _git("config", "user.name", config.GIT_USER or "dev-tool")
            _git("config", "user.email", _email_for(config.GIT_USER or "dev-tool"))
            if _remote_has_branch():
                _sync_onto_remote()
            else:
                _git("checkout", "-B", config.GIT_BRANCH)
                log.info("git: ветки '%s' на удалёнке нет — создам первым коммитом", config.GIT_BRANCH)
        log.info("git готов: %s (ветка %s)", config.GIT_REPO_URL, config.GIT_BRANCH)
    except Exception as e:
        log.error("git недоступен, работаю локально: %s", _redact(str(e)))


def _path(env: str, kind: str, cfg_id: str) -> Path:
    return config.GIT_LOCAL_DIR / env / kind / f"{cfg_id}.json"


# Очередь задач на commit+push. Каждая задача = (пути, сообщение, автор). Файлы уже
# записаны на диск, воркер только коммитит и пушит — ответ API сети не ждёт.
_git_q: "queue.Queue[tuple]" = queue.Queue()


def _commit_push(paths: list, message: str, actor: str) -> None:
    if config.GIT_ENABLED:
        _git_q.put((list(paths), message, actor or "dev-tool"))  # ставим задачу, не блокируя запрос


def _email_for(name: str) -> str:
    """Подобие e-mail для git-автора из имени пользователя."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-").lower() or "dev-tool"
    return f"{slug}@dev-tool.local"


def _has_staged() -> bool:
    """Есть ли застейдженные изменения (git diff --cached): 0 — нет, иначе есть."""
    res = subprocess.run(["git", "-C", str(config.GIT_LOCAL_DIR), "diff", "--cached", "--quiet"])
    return res.returncode != 0


def _push() -> None:
    """Запушить ветку. Если удалёнка ушла вперёд — слить (наши правила приоритетнее) и повторить."""
    if _git("push", "origin", config.GIT_BRANCH, check=False, retries=_NET_RETRIES).returncode == 0:
        return
    log.info("git: push отклонён, сливаю удалёнку (наши правила приоритетнее) и повторяю")
    _sync_onto_remote()
    _git("push", "origin", config.GIT_BRANCH, retries=_NET_RETRIES)  # тут уже падаем с понятной ошибкой, если что


def _git_worker() -> None:
    """Фоновый поток: каждый накопившийся таск — отдельный коммит (автор = тот, кто
    сделал действие; в сообщении — кто, что и с каким правилом), затем один push на пачку."""
    while True:
        batch = [_git_q.get()]
        while True:  # подбираем всё, что успело накопиться
            try:
                batch.append(_git_q.get_nowait())
            except queue.Empty:
                break
        with _git_lock:
            try:
                committed = 0
                for paths, message, actor in batch:
                    # стейджим только пути этого действия — чтобы получить отдельный коммит
                    _git("add", "-A", "--", *paths)
                    if not _has_staged():
                        continue
                    # identity задаём прямо в команде — глобального git config в контейнере нет.
                    _git("-c", f"user.name={actor}", "-c", f"user.email={_email_for(actor)}",
                         "commit", "-m", message)
                    committed += 1
                if committed:
                    _push()
                    log.info("git: запушено коммитов: %d", committed)
            except Exception as e:
                log.error("git push не удался (конфиг сохранён локально): %s", _redact(str(e)))


if config.GIT_ENABLED:
    threading.Thread(target=_git_worker, daemon=True).start()


def save(kind: str, env: str, payload: dict, cfg_id: str | None = None,
         enabled: bool = True, am_id: str = "", created_at: datetime | None = None,
         actor: str = "dev-tool", action: str = "изменил") -> StoredConfig:
    """Создать новый конфиг или перезаписать существующий (если передан cfg_id).

    actor/action попадают в git-коммит: кто (actor) что сделал (action) с каким правилом.
    """
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
        # Снимок «до» (если правило уже было) — для истории и чтобы отсеять no-op
        # переприменения шедулера (меняется только am_id — значимого изменения нет).
        prev = None
        if file.exists():
            try:
                prev = StoredConfig.model_validate_json(file.read_text(encoding="utf-8"))
            except Exception:
                prev = None
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        name = payload.get("name") or cfg.id
        rel = str(file.relative_to(config.GIT_LOCAL_DIR))
        _commit_push([rel], f"{actor} {action} {kind} «{name}» ({env}/{cfg.id})", actor)
        if prev is None or prev.payload != cfg.payload or prev.enabled != cfg.enabled:
            _record(env, actor, action, kind, name,
                    _snap(prev) if prev else None, _snap(cfg))
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
            # Битый/недописанный файл (например, поправили руками в GitLab) не должен
            # ронять весь список — пропускаем его с предупреждением.
            try:
                result.append(StoredConfig.model_validate_json(file.read_text(encoding="utf-8")))
            except Exception as ex:
                log.warning("пропускаю нечитаемый конфиг %s: %s", file, ex)
    return result


# --- Локальная история действий ----------------------------------------------
# Пишем в папку самого окружения: <env>/history/history.ndjson (по строке-JSON на
# действие). Это НЕ git-операция: даже если push упадёт или git недоступен, история
# останется на диске. Хранит снимок «до» и «после» — для показа что изменилось.
def _snap(cfg: StoredConfig) -> dict:
    """Снимок правила для истории: значимые поля (payload + enabled)."""
    return {**cfg.payload, "enabled": cfg.enabled}


def _history_path(env: str) -> Path:
    """История окружения — в его же папке: <env>/history/history.ndjson."""
    return config.GIT_LOCAL_DIR / env / "history" / "history.ndjson"


def _record(env: str, user: str, action: str, kind: str, name: str,
            before: dict | None, after: dict | None) -> None:
    """Дописать одну строку в локальный журнал окружения. Ошибки не роняют действие."""
    try:
        path = _history_path(env)
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "time": datetime.now(timezone.utc).isoformat(),
            "user": user or "dev-tool",
            "action": action,
            "kind": kind,
            "name": name,
            "before": before,   # None — создание
            "after": after,     # None — удаление
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("история: не записал запись (%s/%s): %s", env, name, e)


def history(env: str, limit: int = 500) -> list[dict]:
    """История окружения, новые сверху. Читаем локальный журнал, git не трогаем."""
    path = _history_path(env)
    if not path.exists():
        return []
    items: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            items.append(json.loads(line))
        except Exception:
            continue
    items.reverse()
    return items[:limit]


def cleanup(history_days: int, old_days: int) -> dict:
    """Подрезать по всем окружениям: историю старше history_days и архив удалённых
    правил (<env>/old/) старше old_days. Чистая, не зависит от git. Возвращает счётчики.
    """
    removed = {"history": 0, "old": 0}
    root = config.GIT_LOCAL_DIR
    if not root.exists():
        return removed
    now = datetime.now(timezone.utc)
    h_cutoff = now - timedelta(days=history_days)
    o_cutoff = now.timestamp() - old_days * 86400
    with _lock:
        for env_dir in [d for d in root.iterdir() if d.is_dir() and d.name != ".git"]:
            # 1) история: оставить только записи свежее h_cutoff
            hpath = env_dir / "history" / "history.ndjson"
            if hpath.exists():
                kept = []
                for line in hpath.read_text(encoding="utf-8").splitlines():
                    try:
                        if datetime.fromisoformat(json.loads(line)["time"]) >= h_cutoff:
                            kept.append(line)
                        else:
                            removed["history"] += 1
                    except Exception:
                        kept.append(line)  # нечитаемую строку не теряем
                hpath.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
            # 2) архив удалённых: убрать файлы old/**/*.json старше o_cutoff
            old_dir = env_dir / "old"
            if old_dir.exists():
                for f in old_dir.rglob("*.json"):
                    try:
                        if f.stat().st_mtime < o_cutoff:
                            f.unlink()
                            removed["old"] += 1
                    except Exception:
                        pass
                # убрать опустевшие папки (в т.ч. старую onetime/ после рефактора)
                for d in sorted((p for p in old_dir.rglob("*") if p.is_dir()), reverse=True):
                    try:
                        if not any(d.iterdir()):
                            d.rmdir()
                    except Exception:
                        pass
    log.info("очистка: история -%d, архив -%d", removed["history"], removed["old"])
    return removed


def get_config(kind: str, env: str, cfg_id: str) -> StoredConfig | None:
    """Достать один конфиг по id. None — если такого файла нет."""
    file = _path(env, kind, cfg_id)
    if not file.exists():
        return None
    return StoredConfig.model_validate_json(file.read_text(encoding="utf-8"))


def delete(kind: str, env: str, cfg_id: str, actor: str = "dev-tool") -> bool:
    """«Удалить» конфиг: переносим в env/old/kind/ (история в git), из веба пропадает.

    В вебе old/ не показывается (list_configs читает только env/kind), а в git/GitLab
    файл остаётся — видно, кто и когда удалил. Возвращаем False, если переносить нечего.
    """
    with _lock:
        file = _path(env, kind, cfg_id)
        if not file.exists():
            return False
        before = None
        try:  # достаём имя и снимок «до» для коммита и истории
            prev = StoredConfig.model_validate_json(file.read_text(encoding="utf-8"))
            name = prev.payload.get("name") or cfg_id
            before = _snap(prev)
        except Exception:
            name = cfg_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = config.GIT_LOCAL_DIR / env / "old" / kind / f"{cfg_id}__{stamp}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        file.replace(archive)
        rel_old = str(file.relative_to(config.GIT_LOCAL_DIR))
        rel_new = str(archive.relative_to(config.GIT_LOCAL_DIR))
        _commit_push([rel_old, rel_new], f"{actor} удалил {kind} «{name}» ({env}/{cfg_id})", actor)
        _record(env, actor, "удалил", kind, name, before, None)
    return True


# --- Лок шедулера ------------------------------------------------------------
# В локальном режиме нода одна — координировать нечего. Заглушки нужны, чтобы
# шедулер звал их одинаково при любом хранилище (см. save_hub-диспетчер).
def try_scheduler_lock() -> bool:
    return True


def release_scheduler_lock() -> None:
    pass
