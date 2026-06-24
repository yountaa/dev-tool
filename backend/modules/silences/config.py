"""Настройки модуля. Всё из окружения, чтобы не править код под каждый стенд."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # подхватит .env рядом, если есть


def _urls(value: str) -> list[str]:
    """Значение переменной → список URL. Несколько нод (HA) пишем через запятую."""
    return [u.strip().rstrip("/") for u in value.split(",") if u.strip()]


# Каждая переменная alert_<имя>=<url[,url2...]> = отдельная вкладка окружения.
# Значение — список нод Alertmanager (HA-кластер): читаем со всех, пишем на живую.
ALERTMANAGERS: dict[str, list[str]] = {}
for _key, _value in os.environ.items():
    if _key.lower().startswith("alert_"):
        _name = _key[len("alert_"):].lower()
        ALERTMANAGERS[_name] = _urls(_value)

if not ALERTMANAGERS:
    raise RuntimeError("не задано ни одного alert_* в окружении")


# Источник определений алертов на окружение: rules_<имя>=<url[,url2...]> — ноды
# Prometheus ИЛИ vmalert (у обоих одинаковый GET /api/v1/rules). Несколько нод
# (HA vmalert) — через запятую: правила собираем со всех и дедупим. Необязательно:
# нет переменной — вкладка «Алерты» откатывается на живые алерты из Alertmanager.
RULE_SOURCES: dict[str, list[str]] = {}
for _key, _value in os.environ.items():
    if _key.lower().startswith("rules_"):
        _name = _key[len("rules_"):].lower()
        RULE_SOURCES[_name] = _urls(_value)


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

# Авто-очистка: когда запускать (cron) и что считать «старым» (в днях).
# По умолчанию раз в месяц чистим историю и архив удалённых правил старше 30 дней.
CLEANUP_CRON = os.getenv("CLEANUP_CRON", "0 3 1 * *").strip()           # 1-го числа в 03:00
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))  # сколько хранить историю
OLD_RETENTION_DAYS = int(os.getenv("OLD_RETENTION_DAYS", "30"))          # сколько хранить old/ (удалённые)


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
