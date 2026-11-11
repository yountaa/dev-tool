"""Настройки модуля zabbix (вкладка «Zabbix Audit»). Всё из окружения.

Аудит Zabbix (таблица auditlog) выгружается из БД в Elasticsearch. Модуль читает
его оттуда напрямую и показывает человеческим языком: кто, когда, что сделал,
какие поля поменялись. Имена объектов, которых нет в самой записи аудита (хост
у элемента данных, имя группы по groupid и т.п.), опционально подтягиваются из
Zabbix API.

Окружение (инстанс Zabbix) задаётся префиксом имени переменной:

    zbxaudit_<env>=<индекс или паттерн в ES>        # ОБЯЗАТЕЛЕН: откуда читать аудит
    zbxapi_<env>=<URL api_jsonrpc.php>[,<url2>]      # опц.: резолв ID → имена
    zbxapitoken_<env>=<API-токен Zabbix>             # опц.: токен read-only пользователя
    zbxui_<env>=<URL веб-интерфейса Zabbix>          # опц.: по умолчанию — из zbxapi_

Elasticsearch общий на все окружения:

    ZBX_AUDIT_ES_URL=https://es-1:9200,https://es-2:9200   # HA — через запятую
    ZBX_AUDIT_ES_USER / ZBX_AUDIT_ES_PASSWORD               # basic auth сервисной УЗ
    ZBX_AUDIT_ES_API_KEY                                    # либо API key (приоритетнее)
    ZBX_AUDIT_TLS_VERIFY=true|false|/path/ca.pem            # проверка TLS (ES и Zabbix API)
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

import logging_setup

load_dotenv()

# module в логе = zabbix (первый сегмент имени логгера)
log = logging.getLogger("zabbix.config")


def _urls(value: str) -> list[str]:
    """Значение переменной → список URL. Несколько нод (HA) — через запятую."""
    return [u.strip().rstrip("/") for u in value.split(",") if u.strip()]


# zbxaudit_<env> — индекс аудита; zbxapi_<env> — Zabbix API; zbxapitoken_<env> — токен.
# Порядок проверки префиксов важен: «zbxapitoken_» начинается с «zbxapi», поэтому
# проверяем его первым — иначе токен прочитался бы как адрес API окружения «token_…».
INDEX: dict[str, str] = {}
API: dict[str, list[str]] = {}
API_TOKEN: dict[str, str] = {}
UI: dict[str, str] = {}
for _key, _value in os.environ.items():
    _low = _key.lower()
    if _low.startswith("zbxapitoken_"):
        API_TOKEN[_low[len("zbxapitoken_"):]] = _value.strip()
    elif _low.startswith("zbxapi_"):
        API[_low[len("zbxapi_"):]] = _urls(_value)
    elif _low.startswith("zbxaudit_"):
        INDEX[_low[len("zbxaudit_"):]] = _value.strip()
    elif _low.startswith("zbxui_"):
        UI[_low[len("zbxui_"):]] = _value.strip().rstrip("/")

# Веб-интерфейс Zabbix для кнопки «Открыть в Zabbix». По умолчанию — адрес API
# без api_jsonrpc.php (веб и API Zabbix на одном сервере); zbxui_<env> нужен,
# только если это не так.
for _env, _nodes in API.items():
    if _env not in UI and _nodes and _nodes[0].endswith("/api_jsonrpc.php"):
        UI[_env] = _nodes[0][: -len("/api_jsonrpc.php")]

ENVIRONMENTS: list[str] = sorted(INDEX)

ES_URLS: list[str] = _urls(os.getenv("ZBX_AUDIT_ES_URL", ""))
ES_USER = os.getenv("ZBX_AUDIT_ES_USER", "").strip()
ES_PASSWORD = os.getenv("ZBX_AUDIT_ES_PASSWORD", "")
ES_API_KEY = os.getenv("ZBX_AUDIT_ES_API_KEY", "").strip()


def _verify(value: str):
    """true/false или путь к CA-бандлу — то, что принимает httpx в verify=.
    Корпоративные ES и Zabbix обычно на сертификатах внутреннего CA: без пути к
    нему (или false) httpx не установит TLS-соединение."""
    v = value.strip()
    if v.lower() in ("", "1", "true", "yes"):
        return True
    if v.lower() in ("0", "false", "no"):
        return False
    return v


TLS_VERIFY = _verify(os.getenv("ZBX_AUDIT_TLS_VERIFY", "true"))

# Сколько записей максимум отдаём за один поиск. Фронт рисует их порциями
# («Показать ещё» — из уже полученного), а больше — повод сузить фильтр.
MAX_RECORDS = int(os.getenv("ZBX_AUDIT_MAX_RECORDS", "2000"))

# Таймзона показа времени — общая для приложения (как в access.APP_TZ).
APP_TZ = os.getenv("SILENCE_TZ", "Europe/Moscow").strip()


def known_env(env: str) -> bool:
    return env in INDEX


def api_enabled(env: str) -> bool:
    return bool(API.get(env)) and bool(API_TOKEN.get(env))


def log_config() -> None:
    """Что прочитано из окружения — одной строкой на старте (зовётся из main.py)."""
    logging_setup.event(
        log, "zabbix.config",
        envs=ENVIRONMENTS,
        es_nodes=len(ES_URLS),
        es_auth="api_key" if ES_API_KEY else ("basic" if ES_USER else "none"),
        tls_verify=TLS_VERIFY if isinstance(TLS_VERIFY, bool) else "ca-file",
        api={env: api_enabled(env) for env in ENVIRONMENTS},
        ui={env: bool(UI.get(env)) for env in ENVIRONMENTS},
        max_records=MAX_RECORDS,
    )
    if TLS_VERIFY is False:
        logging_setup.event(
            log, "zabbix.tls_insecure", level=logging.WARNING,
            hint="проверка TLS к ES и Zabbix API выключена (ZBX_AUDIT_TLS_VERIFY=false) — "
                 "допустимо на стенде; на проде смонтируй CA и укажи путь к нему",
        )
    if ENVIRONMENTS and not ES_URLS:
        logging_setup.event(
            log, "zabbix.no_es_url", level=logging.WARNING,
            hint="заданы zbxaudit_*, но нет ZBX_AUDIT_ES_URL — вкладка будет отвечать 502",
        )
    # API без токена (или токен без API) — почти наверняка опечатка в имени переменной.
    for env in sorted(set(API) ^ set(API_TOKEN)):
        logging_setup.event(
            log, "zabbix.api_incomplete", level=logging.WARNING, env=env,
            hint="для резолва имён нужны обе переменные zbxapi_<env> и zbxapitoken_<env>",
        )
    for env in sorted((set(API) | set(API_TOKEN)) - set(INDEX)):
        logging_setup.event(
            log, "zabbix.orphan_api", level=logging.WARNING, env=env,
            hint="zbxapi_/zbxapitoken_ без парного zbxaudit_ — опечатка в имени окружения",
        )
