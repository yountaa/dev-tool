"""RBAC уровня приложения: кто в каких группах и какие вкладки (модули) ему видны.

Группы приходят из Keycloak через oauth2-proxy в заголовке `X-Forwarded-Groups`
(список через запятую). Права на модуль задаются переменными окружения:

    access_<module>=group1,group2   # какие группы видят этот модуль (вкладку)
    RBAC_ADMIN_GROUP=grp1,grp2      # эти группы видят ВСЕ модули (можно несколько через запятую)

Правила:
- RBAC_ENABLED=false (по умолчанию) → доступ не проверяем, всем видно всё.
- Модуль без своей access_<module> (или с пустой) → его видят все.
- access_<module>=* → модуль открыт ВСЕМ вошедшим: любая группа из Keycloak и
  даже пользователь без групп. Удобно, когда вкладка публичная, но RBAC включён
  для других вкладок.
- Иначе нужно, чтобы хотя бы одна группа пользователя была в списке.
- Пользователь из RBAC_ADMIN_GROUP видит всё, независимо от access_*.

Идентичность (имя) и группы читаются из заголовков, которые проставляет
oauth2-proxy. Без прокси (локальный режим) заголовков нет — берём фолбэк.

ВАЖНО: когда добавляешь новый модуль-вкладку, впиши его id в KNOWN_MODULES ниже,
иначе фронт не узнает, что этот модуль вообще есть в списке прав.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request

import logging_setup

# ВАЖНО: загрузить .env ДО чтения переменных ниже. main.py импортирует access.py
# ПЕРВЫМ — раньше модулей silences/victoria, которые тоже зовут load_dotenv(). Без
# этой строки AUTH_ENABLED/RBAC_* читались бы из ещё не загруженного окружения →
# AUTH_ENABLED=false → пользователь всегда «local», а RBAC молча выключен.
load_dotenv()

# Все модули-вкладки приложения. Фронт строит рельс из своего реестра, а бэкенд
# по этому списку решает, какие вкладки показать пользователю (см. /access/me).
KNOWN_MODULES = ["silences", "victoria"]

log = logging.getLogger("access")  # логи авторизации/RBAC


# --- Настройки из окружения ---------------------------------------------------
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes")
AUTH_USER_HEADER = os.getenv("AUTH_USER_HEADER", "X-Forwarded-Preferred-Username").strip()
AUTH_FALLBACK_USER = os.getenv("AUTH_FALLBACK_USER", "local").strip()
# Заголовок со списком групп Keycloak (oauth2-proxy отдаёт через запятую).
AUTH_GROUPS_HEADER = os.getenv("AUTH_GROUPS_HEADER", "X-Forwarded-Groups").strip()

RBAC_ENABLED = os.getenv("RBAC_ENABLED", "false").strip().lower() in ("1", "true", "yes")

# Таймзона показа дат — общая для всех вкладок (совпадает с SILENCE_TZ силенсов).
APP_TZ = os.getenv("SILENCE_TZ", "Europe/Moscow").strip()


def _groups_of_var(value: str) -> list[str]:
    """Значение переменной со списком групп → нормализованный список."""
    return [g.strip() for g in value.split(",") if g.strip()]


# Карта модуль → список групп с доступом. Собираем из access_<module>=...
ACCESS: dict[str, list[str]] = {}
for _key, _value in os.environ.items():
    if _key.lower().startswith("access_"):
        _module = _key[len("access_"):].lower()
        ACCESS[_module] = _groups_of_var(_value)

# Админ-группы: видят ВСЕ модули. Можно перечислить несколько через запятую.
RBAC_ADMIN_GROUPS: list[str] = _groups_of_var(os.getenv("RBAC_ADMIN_GROUP", ""))


# --- Чтение личности и групп из запроса ---------------------------------------
def current_user(request: Request) -> str:
    """Имя пользователя из заголовка oauth2-proxy, иначе фолбэк."""
    if AUTH_ENABLED:
        name = request.headers.get(AUTH_USER_HEADER)
        if name:
            return name
    return AUTH_FALLBACK_USER


def current_groups(request: Request) -> list[str]:
    """Группы пользователя из заголовка. Без авторизации групп нет."""
    if not AUTH_ENABLED:
        return []
    raw = request.headers.get(AUTH_GROUPS_HEADER, "")
    return _groups_of_var(raw)


# --- Диагностика: что реально дошло до бэкенда ---------------------------------
def snapshot(request: Request) -> dict:
    """Сводка авторизации по запросу: кто пришёл, какие заголовки дошли, размеры.

    Одна и та же сводка идёт и в логи, и в ответ /access/debug — в браузере и в
    логе видно одно и то же. Значения cookie не раскрываем (там сессия) — только
    размер: по нему видно, что прокси вообще что-то прислал и насколько «толстый»
    запрос (большие cookie + группы упираются в лимиты буферов по дороге).
    """
    raw_user = request.headers.get(AUTH_USER_HEADER)
    raw_groups = request.headers.get(AUTH_GROUPS_HEADER)
    return {
        "auth_enabled": AUTH_ENABLED,
        "user": current_user(request),          # кем запрос станет в приложении
        "user_header": AUTH_USER_HEADER,
        "user_header_value": raw_user,          # None = заголовок НЕ дошёл
        "email": request.headers.get("X-Forwarded-Email"),
        "forwarded_user": request.headers.get("X-Forwarded-User"),
        "groups_header": AUTH_GROUPS_HEADER,
        "groups_n": len(current_groups(request)),
        "groups_bytes": len(raw_groups) if raw_groups is not None else None,
        "cookie_bytes": len(request.headers.get("Cookie", "")),
        "headers_total_bytes": sum(len(k) + len(v) for k, v in request.headers.raw),
    }


def request_log_fields(request: Request) -> dict:
    """Короткие поля про личность для строки access-лога КАЖДОГО запроса.

    user       — кем запрос был для приложения (атрибуция «кто что сделал»);
    auth_hdr   — дошёл ли заголовок с именем (false при auth_enabled=true = потеря);
    groups_n   — сколько групп пришло (0 при включённом RBAC — подозрительно).
    """
    return {
        "user": current_user(request),
        "auth_hdr": request.headers.get(AUTH_USER_HEADER) is not None,
        "groups_n": len(current_groups(request)),
    }


# --- Логика доступа -----------------------------------------------------------
def is_admin(groups: list[str]) -> bool:
    """В одной из админ-групп? RBAC_ADMIN_GROUP — можно несколько через запятую."""
    return any(g in RBAC_ADMIN_GROUPS for g in groups)


def can_access(module: str, groups: list[str]) -> bool:
    """Виден ли модуль пользователю с такими группами."""
    # RBAC имеет смысл только вместе с авторизацией: без AUTH_ENABLED групп нет
    # вообще (локальный режим = прямой доступ), поэтому доступ не режем.
    if not RBAC_ENABLED or not AUTH_ENABLED:
        return True
    if is_admin(groups):
        return True
    allowed = ACCESS.get(module)
    if not allowed:  # у модуля нет ограничений — виден всем
        return True
    if "*" in allowed:  # спецзначение: открыт всем вошедшим (в т.ч. без групп)
        return True
    return any(g in allowed for g in groups)


def require_module(module: str):
    """Зависимость FastAPI: 403, если у пользователя нет доступа к модулю.

    Вешается на роутер модуля: dependencies=[Depends(require_module("victoria"))].
    """
    def dep(request: Request) -> None:
        groups = current_groups(request)
        if not can_access(module, groups):
            # Отказ доступа — событие уровня WARNING (кто/куда/с какими группами).
            logging_setup.event(
                log, "auth.denied", level=logging.WARNING,
                module=module, user=current_user(request), groups=groups,
            )
            raise HTTPException(403, f"нет доступа к модулю «{module}»")
    return dep


def log_config() -> None:
    """Однократно на старте — вывести ДЕЙСТВУЮЩИЕ настройки авторизации/RBAC.

    Сразу видно, что AUTH_ENABLED/группы прочитаны как ожидалось (если тут
    auth_enabled=false, а ты ждал true — значит .env не подхватился/не проброшен,
    и все запросы поедут под fallback-пользователем «local»).
    """
    logging_setup.event(
        log, "auth.config",
        auth_enabled=AUTH_ENABLED,
        user_header=AUTH_USER_HEADER,
        groups_header=AUTH_GROUPS_HEADER,
        fallback_user=AUTH_FALLBACK_USER,
        rbac_enabled=RBAC_ENABLED,
        rbac_active=(RBAC_ENABLED and AUTH_ENABLED),  # реально режет только при обоих
        admin_groups=RBAC_ADMIN_GROUPS,
        access_rules=ACCESS,
        known_modules=KNOWN_MODULES,
    )


# --- Роутер уровня приложения -------------------------------------------------
router = APIRouter(prefix="/access", tags=["access"])


@router.get("/me")
def me(request: Request):
    """Кто вошёл + какие вкладки ему видны. Фронт по этому фильтрует рельс.

    - name/auth — как в /silences/me (совместимо).
    - tz — таймзона показа дат, общая для всех вкладок.
    - groups — группы пользователя (для отладки/показа).
    - admin — состоит ли в админ-группе.
    - modules — id доступных модулей из KNOWN_MODULES.
    """
    groups = current_groups(request)
    name = current_user(request)
    admin = is_admin(groups)
    modules = [m for m in KNOWN_MODULES if can_access(m, groups)]

    if AUTH_ENABLED and name == AUTH_FALLBACK_USER:
        # Авторизация включена, но имя не пришло из заголовка → это и есть
        # «постоянно local»: oauth2-proxy не проставил заголовок или nginx его не
        # пробросил. Логируем ПОЛНУЮ сводку: по cookie_bytes/groups_bytes видно,
        # дошло ли от прокси хоть что-то (дошло всё, кроме имени ≠ не дошло ничего).
        logging_setup.event(
            log, "auth.no_username_header", level=logging.WARNING, **snapshot(request),
        )
    else:
        logging_setup.event(
            log, "auth.me",
            groups=groups, admin=admin, modules=modules, **snapshot(request),
        )

    return {
        "name": name,
        "auth": AUTH_ENABLED,
        "tz": APP_TZ,
        "groups": groups,
        "admin": admin,
        "modules": modules,
    }


@router.get("/debug")
def debug(request: Request):
    """Диагностика «почему я local»: показать, что РЕАЛЬНО дошло до бэкенда.

    Открой /access/debug в браузере (через прокси, как обычный юзер) — сразу видно:
    пришёл ли заголовок с именем, какие группы, каких размеров cookie, и подсказка,
    на каком хопе искать потерю. Значения cookie/токенов не раскрываем — только размер
    (это сессия; утечь в лог/скриншот она не должна).
    """
    snap = snapshot(request)
    groups = current_groups(request)

    # Все X-Forwarded-* как есть (токены — только размером).
    received = {}
    for key, value in request.headers.items():
        if not key.lower().startswith("x-forwarded-"):
            continue
        if "token" in key.lower():
            received[key] = f"<скрыто, {len(value)} байт>"
        else:
            received[key] = value

    # Подсказка по цепочке: браузер → внешний nginx → oauth2-proxy → nginx фронта → бэкенд.
    if not AUTH_ENABLED:
        hint = ("AUTH_ENABLED=false: бэкенд вообще не читает заголовки — все запросы идут "
                f"под фолбэком «{AUTH_FALLBACK_USER}». Проверь, что .env дошёл до контейнера "
                "(событие auth.config на старте показывает действующие значения).")
    elif not snap["user_header_value"]:  # нет заголовка ИЛИ пришёл пустым
        hint = (f"Заголовок {AUTH_USER_HEADER} до бэкенда НЕ дошёл. Смотри access-лог nginx "
                "фронта (authlog): если там user есть — потеря на хопе nginx→бэкенд; если "
                "пустой — раньше (oauth2-proxy или внешний nginx). cookie_bytes/groups_bytes "
                "подскажут, дошло ли остальное.")
    else:
        hint = f"Всё на месте: вы «{snap['user']}», групп: {len(groups)}."

    logging_setup.event(log, "auth.debug", **snap)
    return {
        "resolved": {
            "user": snap["user"],
            "groups": groups,
            "admin": is_admin(groups),
            "modules": [m for m in KNOWN_MODULES if can_access(m, groups)],
        },
        "received": {
            "x_forwarded": received,
            "cookie_bytes": snap["cookie_bytes"],
            "headers_total_bytes": snap["headers_total_bytes"],
        },
        "config": {
            "auth_enabled": AUTH_ENABLED,
            "rbac_enabled": RBAC_ENABLED,
            "user_header": AUTH_USER_HEADER,
            "groups_header": AUTH_GROUPS_HEADER,
            "fallback_user": AUTH_FALLBACK_USER,
            "admin_groups": RBAC_ADMIN_GROUPS,
            "access_rules": ACCESS,
        },
        "hint": hint,
    }
