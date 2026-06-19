"""Настройки модуля. Всё из окружения, чтобы не править код под каждый стенд."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # подхватит .env рядом, если есть


# Каждая переменная alert_<имя>=<url> = отдельная вкладка окружения в вебе.
ALERTMANAGERS: dict[str, str] = {}
for _key, _value in os.environ.items():
    if _key.lower().startswith("alert_"):
        _name = _key[len("alert_"):].lower()
        ALERTMANAGERS[_name] = _value.rstrip("/")

if not ALERTMANAGERS:
    raise RuntimeError("не задано ни одного alert_* в окружении")


# Git-хранилище конфигов (save_hub), доступ по deploy-токену.
GIT_REPO_URL = os.getenv("GIT_REPO_URL", "").strip()
GIT_USER = os.getenv("GIT_USER", "").strip()
GIT_TOKEN = os.getenv("GIT_TOKEN", "").strip()
GIT_BRANCH = os.getenv("GIT_BRANCH", "main").strip()
GIT_LOCAL_DIR = Path(os.getenv("GIT_LOCAL_DIR", "./.hub-repo")).resolve()

# Нет git — хранилище и шедулер не включаем.
GIT_ENABLED = bool(GIT_REPO_URL and GIT_USER and GIT_TOKEN)


SILENCE_CRON = os.getenv("SILENCE_CRON", "*/5 * * * *").strip()  # как часто проверять расписания
SILENCE_TZ = os.getenv("SILENCE_TZ", "Europe/Moscow").strip()   # таймзона окон расписания


# --- Авторизация (oauth2-proxy перед вебом) ---
# AUTH_ENABLED=true — личность берём из заголовка, который проставляет oauth2-proxy
# после логина в Keycloak (имя = preferred_username из токена). Тогда поле «Создал»
# в вебе только показывается, а не вводится. AUTH_ENABLED=false (по умолчанию) —
# прокси нет, ходим во фронт напрямую, в качестве автора берём AUTH_FALLBACK_USER.
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "false").strip().lower() in ("1", "true", "yes")
AUTH_USER_HEADER = os.getenv("AUTH_USER_HEADER", "X-Forwarded-Preferred-Username").strip()
AUTH_FALLBACK_USER = os.getenv("AUTH_FALLBACK_USER", "local").strip()


def known_env(env: str) -> bool:
    return env in ALERTMANAGERS
