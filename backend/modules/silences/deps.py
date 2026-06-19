"""Маленькие проверки, общие для всех роутов."""
from fastapi import HTTPException, Request

from . import config


def require_env(env: str) -> None:
    """404, если такого окружения нет."""
    if not config.known_env(env):
        raise HTTPException(404, f"неизвестное окружение: {env}")


def current_user(request: Request) -> str:
    """Кто сейчас работает. С AUTH_ENABLED берём имя из заголовка, который проставляет
    oauth2-proxy после логина в Keycloak (preferred_username). Иначе — фолбэк."""
    if config.AUTH_ENABLED:
        name = request.headers.get(config.AUTH_USER_HEADER)
        if name:
            return name
    return config.AUTH_FALLBACK_USER
