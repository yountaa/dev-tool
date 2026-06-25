"""Настройки модуля. Всё из окружения, чтобы не править код под каждый стенд."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # подхватит .env рядом, если есть

log = logging.getLogger("silences.config")


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

# Проверка: имя в rules_<имя> должно совпадать с окружением alert_<имя>. Если задал
# rules_ для несуществующего окружения (опечатка в имени) — источник правил «повиснет»
# и вкладка «Алерты» молча откатится на алерты из AM. Предупреждаем об этом при старте.
for _name in RULE_SOURCES:
    if _name not in ALERTMANAGERS:
        log.warning(
            "rules_%s задан, но окружения alert_%s нет — проверь имя; "
            "источник правил не подключится, «Алерты» покажут только живые алерты Alertmanager",
            _name, _name,
        )


# --- Где хранить правила и историю -------------------------------------------
# STORAGE_BACKEND=local    — git/файлы (как раньше, см. storage/local.py);
# STORAGE_BACKEND=postgres — общая БД (storage/postgres.py): нужна для нескольких
#                            нод приложения (обе читают/пишут одну базу).
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").strip().lower()

# Креды Postgres. Либо одной строкой PG_DSN, либо по частям PG_HOST/PORT/DB/USER/PASSWORD.
# Достаточно прав на CREATE TABLE — таблицы приложение создаёт само при старте.
PG_DSN = os.getenv("PG_DSN", "").strip()
PG_HOST = os.getenv("PG_HOST", "localhost").strip()
PG_PORT = os.getenv("PG_PORT", "5432").strip()
PG_DB = os.getenv("PG_DB", "devtool").strip()
PG_USER = os.getenv("PG_USER", "").strip()
PG_PASSWORD = os.getenv("PG_PASSWORD", "")


def pg_dsn() -> str:
    """Строка подключения к Postgres: берём PG_DSN, иначе собираем из частей."""
    if PG_DSN:
        return PG_DSN
    return (
        f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} "
        f"user={PG_USER} password={PG_PASSWORD}"
    )


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

# Авто-очистка: когда запускать (cron) и сколько дней хранить два набора данных.
CLEANUP_CRON = os.getenv("CLEANUP_CRON", "0 3 1 * *").strip()                          # КОГДА чистить (по умолч. 1-го числа в 03:00)
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))                # сколько хранить ЖУРНАЛ ИСТОРИИ (кто/что/когда менял)
DELETED_RULES_RETENTION_DAYS = int(os.getenv("DELETED_RULES_RETENTION_DAYS", "30"))    # сколько хранить УДАЛЁННЫЕ ПРАВИЛА (deleted_at / old/)


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
