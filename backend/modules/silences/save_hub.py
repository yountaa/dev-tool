"""Хранилище конфигов в git. Каждый конфиг — файл <env>/<kind>/<id>.json.

git вместо БД: видно историю, можно откатить. Всё на обычном git через
subprocess, без лишних либ.
"""
from __future__ import annotations  # str | None на Python 3.9

import logging
import queue
import re
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


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Запустить git в папке хранилища. check=False — вернуть результат, не падая."""
    res = subprocess.run(
        ["git", "-C", str(config.GIT_LOCAL_DIR), *args],
        capture_output=True, text=True,
    )
    if check and res.returncode != 0:
        # в сообщение кладём только имя подкоманды — чтобы не светить url с токеном
        raise RuntimeError(f"git {args[0]} -> {res.returncode}: {_redact(res.stderr.strip())}")
    return res


def _remote_has_branch() -> bool:
    """Есть ли наша ветка на удалёнке (у пустой/новой репы её ещё нет)."""
    res = _git("ls-remote", "--heads", "origin", config.GIT_BRANCH, check=False)
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
    _git("fetch", "origin", config.GIT_BRANCH)
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
    if _git("push", "origin", config.GIT_BRANCH, check=False).returncode == 0:
        return
    log.info("git: push отклонён, сливаю удалёнку (наши правила приоритетнее) и повторяю")
    _sync_onto_remote()
    _git("push", "origin", config.GIT_BRANCH)  # тут уже падаем с понятной ошибкой, если что


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
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(cfg.model_dump_json(indent=2), encoding="utf-8")
        name = payload.get("name") or cfg.id
        rel = str(file.relative_to(config.GIT_LOCAL_DIR))
        _commit_push([rel], f"{actor} {action} {kind} «{name}» ({env}/{cfg.id})", actor)
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


def _matchers_key(matchers: list) -> set:
    """Набор матчеров без учёта порядка — для сравнения правил."""
    return {(m.get("name"), m.get("value"), bool(m.get("isRegex"))) for m in (matchers or [])}


def find_duplicate(kind: str, env: str, payload: dict, exclude_id: str | None = None):
    """Уже есть правило с такими же матчерами и концом алертинга? Вернуть его (или None).

    onetime — сравниваем по ends_at, schedule — по набору окон.
    """
    new_matchers = _matchers_key(payload.get("matchers"))
    for cfg in list_configs(kind, env):
        if cfg.id == exclude_id:
            continue
        if _matchers_key(cfg.payload.get("matchers")) != new_matchers:
            continue
        if kind == "onetime":
            if cfg.payload.get("ends_at") == payload.get("ends_at"):
                return cfg
        else:
            if cfg.payload.get("windows") == payload.get("windows"):
                return cfg
    return None


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


def delete(kind: str, env: str, cfg_id: str, actor: str = "dev-tool") -> bool:
    """«Удалить» конфиг: переносим в env/old/kind/ (история в git), из веба пропадает.

    В вебе old/ не показывается (list_configs читает только env/kind), а в git/GitLab
    файл остаётся — видно, кто и когда удалил. Возвращаем False, если переносить нечего.
    """
    with _lock:
        file = _path(env, kind, cfg_id)
        if not file.exists():
            return False
        try:  # достаём имя для сообщения коммита до переноса
            name = StoredConfig.model_validate_json(file.read_text(encoding="utf-8")).payload.get("name") or cfg_id
        except Exception:
            name = cfg_id
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = config.GIT_LOCAL_DIR / env / "old" / kind / f"{cfg_id}__{stamp}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        file.replace(archive)
        rel_old = str(file.relative_to(config.GIT_LOCAL_DIR))
        rel_new = str(archive.relative_to(config.GIT_LOCAL_DIR))
        _commit_push([rel_old, rel_new], f"{actor} удалил {kind} «{name}» ({env}/{cfg_id})", actor)
    return True
