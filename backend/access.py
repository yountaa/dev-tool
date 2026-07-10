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

import os

from fastapi import APIRouter, HTTPException, Request

# Все модули-вкладки приложения. Фронт строит рельс из своего реестра, а бэкенд
# по этому списку решает, какие вкладки показать пользователю (см. /access/me).
KNOWN_MODULES = ["silences", "victoria"]


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
        if not can_access(module, current_groups(request)):
            raise HTTPException(403, f"нет доступа к модулю «{module}»")
    return dep


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
    return {
        "name": current_user(request),
        "auth": AUTH_ENABLED,
        "tz": APP_TZ,
        "groups": groups,
        "admin": is_admin(groups),
        "modules": [m for m in KNOWN_MODULES if can_access(m, groups)],
    }
