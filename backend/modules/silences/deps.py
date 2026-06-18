"""Маленькие проверки, общие для всех роутов."""
from fastapi import HTTPException

from . import config


def require_env(env: str) -> None:
    """404, если такого окружения нет."""
    if not config.known_env(env):
        raise HTTPException(404, f"неизвестное окружение: {env}")
