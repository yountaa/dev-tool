"""Клиент к Alertmanager API. Async (httpx), чтобы не блокировать FastAPI."""
from __future__ import annotations  # dict | None на Python 3.9

import httpx

from .config import ALERTMANAGERS

_TIMEOUT = httpx.Timeout(10.0)  # таймаут запроса


class AlertmanagerError(RuntimeError):
    pass


def _base_url(env: str) -> str:
    url = ALERTMANAGERS.get(env)
    if not url:
        raise AlertmanagerError(f"неизвестное окружение: {env}")
    return url


async def _request(method: str, env: str, path: str, json: dict | None = None) -> httpx.Response:
    """Запрос к Alertmanager с понятной ошибкой, если он недоступен.

    trust_env=False — чтобы httpx НЕ тащил запрос через HTTP(S)_PROXY из окружения.
    Иначе обращение к внутреннему адресу AM уходит в корпоративный прокси, и тот
    рвёт соединение.
    """
    url = f"{_base_url(env)}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            resp = await client.request(method, url, json=json)
    except httpx.HTTPError as e:
        raise AlertmanagerError(f"нет связи с Alertmanager {_base_url(env)}: {e}")
    if resp.status_code >= 400:
        raise AlertmanagerError(f"AM {resp.status_code}: {resp.text}")
    return resp


async def list_silences(env: str) -> list[dict]:
    """Все silence стенда (вкладка «Рабочие правила»)."""
    return (await _request("GET", env, "/api/v2/silences")).json()


async def active_silences(env: str) -> list[dict]:
    """Живые silence (active/pending) — по ним проверяем дубли перед созданием."""
    silences = await list_silences(env)
    return [s for s in silences if s.get("status", {}).get("state") in ("active", "pending")]


async def create_silence(env: str, payload: dict) -> str:
    """Создать silence, вернуть его id из AM."""
    return (await _request("POST", env, "/api/v2/silences", json=payload)).json().get("silenceID", "")


async def delete_silence(env: str, silence_id: str) -> None:
    """Снять silence по id."""
    await _request("DELETE", env, f"/api/v2/silence/{silence_id}")
