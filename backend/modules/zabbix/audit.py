"""Аудит Zabbix 7.x: запрос к ES и перевод записи auditlog на человеческий язык.

Запись auditlog в Zabbix 7.x (одна таблица, без auditlog_details):

    auditid       cuid записи
    userid        кто (ID) — и username, кто (логин на момент действия)
    clock         когда, unix-время в секундах
    ip            откуда
    action        что сделал — число (0 добавил, 1 изменил, 2 удалил, 8 вошёл…)
    resourcetype  над чем — число (4 хост, 13 триггер, 15 элемент данных…)
    resourceid    ID объекта; resourcename — его имя на момент действия
    recordsetid   cuid «операции»: одно сохранение формы = несколько записей с ним
    details       JSON: {"host.name": ["update", "новое", "старое"], …}

Словари чисел — из CAudit.php Zabbix 7.0. Неизвестное число показываем как есть
(«тип 61»), а не прячем: новая версия Zabbix не должна терять записи.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
from zoneinfo import ZoneInfo

import logging_setup

from . import client, config

log = logging.getLogger("zabbix.audit")

# --- Поля документа в ES ------------------------------------------------------
# Имена совпадают с колонками auditlog: JDBC-выгрузка (индекс zabbix-auditlog-jdbc-*)
# кладёт их как есть. Если в индексе поля называются иначе — правится только этот блок.
F_ID = "auditid"
F_TIME = "clock"           # unix-время, секунды
F_USERID = "userid"
F_USER = "username"
F_IP = "ip"
F_ACTION = "action"
F_RTYPE = "resourcetype"
F_RID = "resourceid"
F_RNAME = "resourcename"
F_RECORDSET = "recordsetid"
F_DETAILS = "details"

_TZ = ZoneInfo(config.APP_TZ)

# --- Словари --------------------------------------------------------------------
# action → (подпись, вид для цвета на фронте). Термины — как в журнале аудита
# Zabbix, но в прошедшем времени: «Удалить» в бейдже читалось как кнопка.
ACTIONS = {
    0: ("Добавлено", "add"),
    1: ("Обновлено", "update"),
    2: ("Удалено", "delete"),
    4: ("Выход", "auth"),
    7: ("Выполнено", "exec"),
    8: ("Вход", "auth"),
    9: ("Неудачный вход", "fail"),
    10: ("История очищена", "delete"),
    11: ("Кэш конфигурации перезагружен", "exec"),
    12: ("Данные отправлены (history.push)", "exec"),
}

RESOURCE_TYPES = {
    0: "Пользователь",
    3: "Способ оповещения",
    4: "Узел сети",
    5: "Действие",
    6: "График",
    11: "Группа пользователей",
    13: "Триггер",
    14: "Группа узлов сети",
    15: "Элемент данных",
    16: "Изображение",
    17: "Преобразование значений",
    18: "Услуга",
    19: "Карта сети",
    22: "Веб-сценарий",
    23: "Правило обнаружения сети",
    25: "Скрипт",
    26: "Прокси",
    27: "Обслуживание",
    28: "Регулярное выражение",
    29: "Макрос",
    30: "Шаблон",
    31: "Прототип триггера",
    32: "Соответствие иконок",
    33: "Дашборд",
    34: "Корреляция событий",
    35: "Прототип графика",
    36: "Прототип элемента данных",
    37: "Прототип узла сети",
    38: "Авторегистрация",
    39: "Модуль",
    40: "Настройки",
    41: "Очистка истории (housekeeping)",
    42: "Аутентификация",
    43: "Дашборд шаблона",
    44: "Роль пользователя",
    45: "API-токен",
    46: "Запланированный отчёт",
    47: "Нода HA",
    48: "SLA",
    49: "Каталог пользователей (LDAP/SAML)",
    50: "Группа шаблонов",
    51: "Коннектор",
    52: "Правило низкоуровневого обнаружения",
    53: "История",
    54: "MFA",
    55: "Группа прокси",
}

# Ресурсы, у которых в записи нет узла сети, а без него запись непонятна:
# «обновлён элемент данных CPU load» — а на каком узле? Узел берём из Zabbix API.
# resourcetype → (метод, поле ID)
# resourcetype → (метод, поле ID, доп. параметры). У прототипов дополнительно
# берём родительское LLD-правило: форма прототипа в Zabbix открывается только с
# parent_discoveryid. У прототипа узла сети нет selectHosts — узел-владелец
# приходит в parentHost.
_HOSTS = {"selectHosts": ["name", "status"]}
_LLD = {"selectDiscoveryRule": ["itemid"]}
HOST_CONTEXT = {
    6: ("graph.get", "graphid", _HOSTS),
    13: ("trigger.get", "triggerid", _HOSTS),
    15: ("item.get", "itemid", _HOSTS),
    22: ("httptest.get", "httptestid", _HOSTS),
    52: ("discoveryrule.get", "itemid", _HOSTS),
    31: ("triggerprototype.get", "triggerid", {**_HOSTS, **_LLD}),
    35: ("graphprototype.get", "graphid", {**_HOSTS, **_LLD}),
    36: ("itemprototype.get", "itemid", {**_HOSTS, **_LLD}),
    37: ("hostprototype.get", "hostid", {"selectParentHost": ["name", "status"], **_LLD}),
}
# Типы, объекты которых ищутся по узлу сети (hostids=…) в host_objects. У
# hostprototype.get параметр hostids — ID самих прототипов, а не узла.
HOST_SEARCHABLE = (6, 13, 15, 22, 52, 31, 35, 36)

# Поля details, в которых лежит ID другого объекта: суффикс ключа → (метод, поле ID,
# поле с именем). templateid — только внутри списка templates: у элемента данных
# поле templateid означает родительский элемент шаблона, а не шаблон.
_ID_REFS = [
    (re.compile(r"\.templates(_clear)?\[\d+\]\.templateid$"), ("template.get", "templateid", "name")),
    (re.compile(r"^template\..*\.groupid$"), ("templategroup.get", "groupid", "name")),
    (re.compile(r"\.groupid$"), ("hostgroup.get", "groupid", "name")),
    (re.compile(r"\.hostid$"), ("host.get", "hostid", "name")),
    (re.compile(r"\.usrgrpid$"), ("usergroup.get", "usrgrpid", "name")),
    (re.compile(r"\.userid$"), ("user.get", "userid", "username")),
    (re.compile(r"\.mediatypeid$"), ("mediatype.get", "mediatypeid", "name")),
    (re.compile(r"\.roleid$"), ("role.get", "roleid", "name")),
    (re.compile(r"\.scriptid$"), ("script.get", "scriptid", "name")),
    (re.compile(r"\.proxyid$"), ("proxy.get", "proxyid", "name")),
    (re.compile(r"\.proxy_groupid$"), ("proxygroup.get", "proxy_groupid", "name")),
    # зависимости триггера, элементы графика, мастер-элемент зависимого элемента
    (re.compile(r"\.triggerid(_up)?$"), ("trigger.get", "triggerid", "description")),
    (re.compile(r"\.(itemid|master_itemid)$"), ("item.get", "itemid", "name")),
    (re.compile(r"\.valuemapid$"), ("valuemap.get", "valuemapid", "name")),
    (re.compile(r"\.interfaceid$"), ("hostinterface.get", "interfaceid", "ip")),
    (re.compile(r"\.actionid$"), ("action.get", "actionid", "name")),
    (re.compile(r"\.serviceid$"), ("service.get", "serviceid", "name")),
    (re.compile(r"\.druleid$"), ("drule.get", "druleid", "name")),
    (re.compile(r"\.sysmapid$"), ("map.get", "sysmapid", "name")),
    (re.compile(r"\.maintenanceid$"), ("maintenance.get", "maintenanceid", "name")),
]

# Списки-связи: элемент списка ссылается на другой объект полем-ID. При удалении
# из такого списка Zabbix пишет только «maintenance.hosts[N]: [delete]», где N —
# ID записи связи, а не узла сети. Узел ищем в истории аудита (resolve_deleted_links).
LINK_ID_FIELDS = {
    "hosts": "hostid",
    "groups": "groupid",
    "templates": "templateid",
    "templates_clear": "templateid",
    "usrgrps": "usrgrpid",
    "users": "userid",
    "dependencies": "triggerid",
}

# Операции над полем в details.
DETAIL_OPS = {
    "add": "добавлено",
    "update": "изменено",
    "delete": "удалено",
    "attach": "привязано",
    "detach": "отвязано",
}

# Подписи частых полей. Остальные показываются техническим именем — оно совпадает
# с документацией API Zabbix, и по нему легко найти смысл.
FIELD_LABELS = {
    "name": "Видимое имя",
    "host": "Имя узла сети",
    "status": "Состояние",
    "active": "Состояние",
    "users_status": "Состояние",
    "gui_access": "Доступ к веб-интерфейсу",
    "debug_mode": "Режим отладки",
    "useip": "Подключаться",
    "main": "Интерфейс",
    "value_type": "Тип информации",
    "recovery_mode": "Восстановление",
    "type": "Тип",
    "operating_mode": "Режим прокси",
    "monitored_by": "Наблюдение через",
    "proxyid": "Прокси",
    "hostid": "Узел сети",
    "groupid": "Группа",
    "usrgrpid": "Группа пользователей",
    "userid": "Пользователь",
    "mediatypeid": "Способ оповещения",
    "sendto": "Отправлять на",
    "description": "Описание",
    "expression": "Выражение",
    "recovery_expression": "Выражение восстановления",
    "priority": "Важность",
    "key_": "Ключ",
    "delay": "Интервал обновления",
    "history": "Период хранения истории",
    "trends": "Период хранения динамики изменений",
    "url": "URL",
    "username": "Логин",
    "surname": "Фамилия",
    "groups": "Группы",
    "templates": "Шаблоны",
    "templates_clear": "Шаблоны (отвязка с очисткой)",
    "macros": "Макросы",
    "macro": "Макрос",
    "value": "Значение",
    "tags": "Теги",
    "tag": "Тег",
    "interfaces": "Интерфейсы",
    "ip": "IP",
    "dns": "DNS",
    "port": "Порт",
    "inventory_mode": "Режим инвентаризации",
    "active_since": "Начало",
    "active_till": "Окончание",
    "maintenance_type": "Тип обслуживания",
    "esc_period": "Период эскалации",
    "operations": "Операции",
    "filter": "Условия",
    "manual_close": "Ручное закрытие",
    "event_name": "Имя события",
    "opdata": "Оперативные данные",
    "units": "Единицы",
    "params": "Параметры",
    "timeout": "Таймаут",
    "command": "Команда",
    "hosts": "Узлы сети",
    "dependencies": "Зависимости",
    "discover": "Обнаружение",
    "lifetime": "Удалять потерянные ресурсы через",
    "enabled_lifetime": "Выключать потерянные ресурсы через",
    "timeperiods": "Периоды",
    "timeperiod_type": "Тип периода",
    "start_date": "Дата начала",
    "start_time": "Время начала",
    "period": "Длительность",
    "every": "Каждые",
    "dayofweek": "Дни недели",
    "day": "День месяца",
    "month": "Месяцы",
    "master_itemid": "Основной элемент данных",
    "valuemapid": "Преобразование значений",
    "interfaceid": "Интерфейс",
    "passwd": "Пароль",
    "roleid": "Роль",
    "usrgrps": "Группы пользователей",
    "medias": "Оповещения",
}

# Значения-коды частых полей. Ключ — последний сегмент имени поля.
# Коды полей → простые слова. Ключ — последний сегмент имени поля (status из
# host.status, item.status, trigger.status…): у этих полей смысл кода один и тот
# же на всех ресурсах Zabbix. Поля, где один код значит разное у разных
# ресурсов (type, flags), сюда не попадают — там остаётся число.
_ENABLED = {"0": "включено", "1": "выключено"}
_YES_NO = {"0": "нет", "1": "да"}
VALUE_LABELS = {
    "status": _ENABLED,
    "active": _ENABLED,            # оповещение пользователя (media.active)
    "users_status": _ENABLED,      # группа пользователей
    "gui_access": {"0": "по умолчанию", "1": "внутренняя", "2": "LDAP", "3": "запрещён"},
    "debug_mode": {"0": "выключен", "1": "включён"},
    "useip": {"0": "по DNS", "1": "по IP"},
    "main": {"0": "дополнительный", "1": "основной"},
    "value_type": {
        "0": "число с плавающей точкой", "1": "символ", "2": "журнал (лог)",
        "3": "целое без знака", "4": "текст", "5": "бинарный",
    },
    "recovery_mode": {"0": "по выражению", "1": "по выражению восстановления", "2": "нет"},
    "type_of_problem": {"0": "одно событие", "1": "несколько событий"},
    "operating_mode": {"0": "активный", "1": "пассивный"},
    "monitored_by": {"0": "сервер", "1": "прокси", "2": "группа прокси"},
    "tls_connect": {"1": "без шифрования", "2": "PSK", "4": "сертификат"},
    "eventsource": {"0": "триггеры", "1": "обнаружение", "2": "авторегистрация", "3": "внутренние", "4": "сервисы"},
    "pause_suppressed": _YES_NO,
    "notify_if_canceled": _YES_NO,
    "correlation_mode": {"0": "все проблемы", "1": "по совпадению тегов"},
    "discover": {"0": "обнаруживать", "1": "не обнаруживать"},
    "timeperiod_type": {"0": "однократно", "2": "ежедневно", "3": "еженедельно", "4": "ежемесячно"},
    "priority": {
        "0": "не классифицировано", "1": "информация", "2": "предупреждение",
        "3": "средняя", "4": "высокая", "5": "чрезвычайная",
    },
    "manual_close": {"0": "запрещено", "1": "разрешено"},
    "multiple": {"0": "одно событие", "1": "несколько событий"},
    "maintenance_type": {"0": "со сбором данных", "1": "без сбора данных"},
    "inventory_mode": {"-1": "выключено", "0": "вручную", "1": "автоматически"},
}

# Время в полях-датах обслуживания и т.п. — unix-время: показываем датой.
_EPOCH_FIELDS = {"active_since", "active_till", "lastaccess", "expires_at", "created_at", "start_date"}

_DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def _bits(value, names: list) -> str | None:
    """Битовая маска Zabbix (dayofweek, month) → «пн, ср, пт»."""
    n = _int(value)
    if not n:
        return None
    return ", ".join(name for i, name in enumerate(names) if n & (1 << i))


def _duration(value) -> str | None:
    """Секунды → «2 ч 30 мин» (period периода обслуживания)."""
    n = _int(value)
    if n is None:
        return None
    d, rest = divmod(n, 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    parts = [f"{d} д" if d else "", f"{h} ч" if h else "", f"{m} мин" if m else ""]
    return " ".join(p for p in parts if p) or f"{n} с"


# Поля, которым нужен не словарь, а пересчёт: секунды от полуночи, маски дней…
_VALUE_FORMATTERS = {
    "start_time": lambda v: (lambda n: f"{n // 3600:02d}:{n % 3600 // 60:02d}" if n is not None else None)(_int(v)),
    "period": _duration,
    "dayofweek": lambda v: _bits(v, _DAYS),
    "month": lambda v: _bits(v, _MONTHS),
}


# --- Время ----------------------------------------------------------------------
def to_epoch(value: str) -> int:
    """'2026-11-08T14:00' (настенное время в APP_TZ, из datetime-local) → unix.
    Локализуем ДО перевода: naive.timestamp() взял бы таймзону контейнера (UTC),
    и период уехал бы на три часа."""
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ)
    return int(dt.timestamp())


def _fmt_ts(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, _TZ).strftime("%d.%m.%Y %H:%M:%S")


# --- Запрос к ES ------------------------------------------------------------------
def _contains(field: str, text: str) -> dict:
    """Подстрока в keyword-поле без учёта регистра.

    Строковые поля индекса (username, resourcename, details, ip) — keyword, не
    text: match/simple_query_string ищут по ним только ЦЕЛОЕ значение, и поиск
    «cpu» по details ничего бы не нашёл. wildcard *…* медленный на больших
    индексах, но аудит — тысячи записей в день, и фильтр по периоду (clock)
    отсекает почти всё до него. Символы * и ? пользователя работают как шаблон.
    """
    return {"wildcard": {field: {"value": f"*{text}*", "case_insensitive": True}}}


def build_query(f: dict) -> dict:
    """Фильтры с фронта → тело _search.

    Типы полей — как в индексе zabbix-auditlog-jdbc-*: числа (clock, action,
    resourcetype, resourceid, userid) — long, строки — keyword. Фильтр периода идёт
    по clock, а не по @timestamp: @timestamp — время выгрузки JDBC-пачкой, у
    десятков записей оно одинаковое и не совпадает с моментом действия.
    """
    must: list = [{"range": {F_TIME: {"gte": f["from"], "lte": f["to"]}}}]
    if f.get("actions"):
        must.append({"terms": {F_ACTION: f["actions"]}})
    if f.get("types"):
        must.append({"terms": {F_RTYPE: f["types"]}})
    if f.get("user"):
        must.append({"term": {F_USER: {"value": f["user"], "case_insensitive": True}}})
    if f.get("ip"):
        must.append(_contains(F_IP, f["ip"]))
    if f.get("rid"):
        must.append({"terms": {F_RID: [f["rid"]]}})
    if f.get("recordset"):
        must.append({"term": {F_RECORDSET: f["recordset"]}})
    # host_ids — ресурсы узлов сети, найденных по тексту в Zabbix API (см.
    # host_objects): у триггера/элемента данных в записи нет имени узла, и
    # без этого поиск «web-01» не находил правку триггера на web-01.
    by_host = [
        {"bool": {"filter": [{"term": {F_RTYPE: rtype}}, {"terms": {F_RID: ids}}]}}
        for rtype, ids in (f.get("host_ids") or {}).items() if ids
    ]
    if f.get("name"):
        must.append({"bool": {
            "should": [_contains(F_RNAME, f["name"])] + by_host,
            "minimum_should_match": 1,
        }})
    if f.get("q"):
        # Объекты узла добавляем, только если «Название ресурса» пустое: иначе
        # они уже учтены в фильтре выше, а здесь лишь расширили бы выдачу.
        extra = by_host if not f.get("name") else []
        must.append({"bool": {
            "should": [_contains(x, f["q"]) for x in (F_USER, F_RNAME, F_DETAILS, F_IP)] + extra,
            "minimum_should_match": 1,
        }})
    return {
        "size": min(int(f.get("size") or config.MAX_RECORDS), config.MAX_RECORDS),
        "track_total_hits": True,
        "sort": [{F_TIME: {"order": "desc"}}],
        "query": {"bool": {"filter": must}},
    }


# --- Нормализация записи ------------------------------------------------------------
def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _str(v) -> str:
    return "" if v is None else str(v)


def _value_label(field: str, value):
    """Код → подпись для частых полей (status 1 → «выключен»), иначе None."""
    if value is None:
        return None
    last = field.rsplit(".", 1)[-1]
    if last in VALUE_LABELS:
        return VALUE_LABELS[last].get(str(value))
    if last in _VALUE_FORMATTERS:
        return _VALUE_FORMATTERS[last](value)
    if last in _EPOCH_FIELDS:
        ts = _int(value)
        if ts:
            return _fmt_ts(ts)
    return None


def _translated(segments: list) -> str | None:
    """Перевод сегментов пути целиком или None, если хоть один не переведён.

    Наполовину переведённый путь («Интерфейсы › details › version») читается
    хуже оригинала — тогда показываем путь как есть, он совпадает с API Zabbix.
    """
    out = []
    for seg in segments:
        base = re.sub(r"\[\d+\]$", "", seg)
        if base not in FIELD_LABELS:
            return None
        out.append(FIELD_LABELS[base])
    return " › ".join(out)


def _field_label(path: str) -> str:
    """'host.status' → 'Состояние'; 'host.groups[5].groupid' → 'Группы';
    непереведённый путь ('host.inventory.os') — как есть.

    Первый сегмент (тип ресурса) отбрасываем: он и так в колонке «Ресурс».
    """
    parts = path.split(".")
    if len(parts) > 1:
        parts = parts[1:]
    # «groups[5].groupid» → просто «Группы»: ID элемента списка и так в значении.
    if len(parts) > 1 and parts[-2].endswith("]") and parts[-1].endswith("id"):
        parts = parts[:-1]
    return _translated(parts) or path


# Элемент списка внутри ресурса: макрос, тег, интерфейс, шаг предобработки…
# Ключ «host.macros[123].value» = элемент «host.macros[123]» + его поле «value».
_ITEM_RX = re.compile(r"^(.*\[(\d+)\])\.([^\[\]]+)$")

# Название элемента списка в заголовке группы (в единственном числе).
LIST_TITLES = {
    "macros": "Макрос",
    "tags": "Тег",
    "interfaces": "Интерфейс",
    "groups": "Группа",
    "templates": "Шаблон",
    "templates_clear": "Шаблон",
    "medias": "Оповещение",
    "operations": "Операция",
    "recovery_operations": "Операция восстановления",
    "update_operations": "Операция обновления",
    "conditions": "Условие",
    "preprocessing": "Шаг предобработки",
    "steps": "Шаг",
    "headers": "Заголовок",
    "variables": "Переменная",
    "parameters": "Параметр",
    "users": "Пользователь",
    "usrgrps": "Группа пользователей",
    "tag_filters": "Фильтр по тегам",
    "hostgroup_rights": "Права на группу узлов сети",
    "templategroup_rights": "Права на группу шаблонов",
    "dependencies": "Зависимость",
    "lld_macro_paths": "LLD-макрос",
    "overrides": "Переопределение",
    "timeperiods": "Период",
    "hosts": "Узел сети",
}

# Поля, по которым элемент списка узнаётся глазами — первое найденное идёт в
# заголовок группы: «Макрос {$DB_HOST}», «Тег env», «Интерфейс 10.0.0.5».
_ITEM_KEYS = ("macro", "tag", "name", "ip", "dns", "sendto", "lld_macro", "key_")


def _group_lists(changes: list, bare: dict) -> list:
    """Поля одного элемента списка → одна группа с заголовком.

    Без группировки создание макроса давало строки «Макросы › Макрос» и
    «Макросы › Значение» вперемешку с другими — не понять, чьё это значение.
    bare — служебные строки «host.macros[123]: [add|delete]» без значения:
    их операция («добавлен», «удалён») идёт в заголовок группы.
    """
    members: dict = {}
    for c in changes:
        m = _ITEM_RX.match(c["field"])
        if m:
            members.setdefault(m.group(1), []).append(c)

    for key, items in members.items():
        # Единственное поле-ID («groups[5].groupid») — группа не нужна: строка
        # «Группы: добавлено Linux servers» и так понятна.
        if len(items) == 1 and items[0]["field"].endswith("id"):
            continue
        m = _ITEM_RX.match(items[0]["field"])
        list_name = re.sub(r"\[\d+\]$", "", key.rsplit(".", 1)[-1])
        ident = ""
        ident_field = ""
        for k in _ITEM_KEYS:
            hit = next((c for c in items if c["field"] == f"{key}.{k}"), None)
            if hit:
                ident = _str(hit["new"] if hit["new"] not in (None, "") else hit["old"])
                ident_field = hit["field"]
                break
        for c in items:
            sub = _ITEM_RX.match(c["field"]).group(3)
            # Не показываем то, что уже есть в заголовке группы: собственный ID
            # элемента (hostmacroid = 501) и его имя при добавлении/удалении.
            # Переименование (update имени) остаётся — там есть «было → стало».
            c["redundant"] = (
                (sub.endswith("id") and _str(c["new"] or c["old"]) == m.group(2))
                or (c["field"] == ident_field and c["op"] != "update")
            )
            c["group"] = key
            c["group_kind"] = list_name
            c["group_id"] = m.group(2)
            c["group_title"] = " ".join(x for x in (LIST_TITLES.get(list_name, list_name), ident) if x)
            c["group_op"] = DETAIL_OPS.get(bare.get(key, ""), "")
            c["label"] = _translated([sub]) or sub

    # Элемент списка, о котором есть только служебная строка (удалён макрос —
    # Zabbix пишет лишь «host.macros[123]: [delete]») — показываем заголовком.
    for key, op in bare.items():
        if key in members:
            continue
        m = re.match(r"^(.*)\[(\d+)\]$", key)
        if not m:
            continue
        list_name = m.group(1).rsplit(".", 1)[-1]
        changes.append({
            "field": key, "label": "", "op": op, "op_label": DETAIL_OPS.get(op, op),
            "new": None, "old": None, "new_label": None, "old_label": None,
            "group": key, "group_kind": list_name, "group_id": m.group(2),
            "group_title": LIST_TITLES.get(list_name, list_name),
            "group_op": DETAIL_OPS.get(op, op), "header_only": True,
        })

    # Поля одной группы — подряд, в порядке первого появления группы.
    order: dict = {}
    for i, c in enumerate(changes):
        order.setdefault(c.get("group") or c["field"], i)
    changes.sort(key=lambda c: order[c.get("group") or c["field"]])
    return changes


def parse_details(raw) -> list:
    """details (JSON-строка или уже объект) → список изменений по полям.

    Формат значения: ["add"|"delete"|"attach"|"detach"] или ["add", значение]
    или ["update", новое, старое]. Поля одного элемента списка (макрос, тег,
    интерфейс…) собираются в группу — см. _group_lists.
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return [{"field": "", "label": "details", "op": "", "op_label": "", "new": raw, "old": None}]
    else:
        data = raw
    if not isinstance(data, dict):
        return [{"field": "", "label": "details", "op": "", "op_label": "", "new": _str(data), "old": None}]

    changes = []
    bare: dict = {}
    for key, val in data.items():
        if not isinstance(val, list) or not val:
            val = ["", val]
        op = _str(val[0])
        new = val[1] if len(val) > 1 else None
        old = val[2] if len(val) > 2 else None
        # «host.macros[123]: [add]» — служебная строка элемента списка без значения.
        if new is None and key.endswith("]"):
            bare[key] = op
            continue
        changes.append({
            "field": key,
            "label": _field_label(key),
            "op": op,
            "op_label": DETAIL_OPS.get(op, op),
            "new": new,
            "old": old,
            "new_label": _value_label(key, new),
            "old_label": _value_label(key, old),
        })
    return _group_lists(changes, bare)


def normalize(src: dict) -> dict:
    """Документ ES → запись для фронта: числа уже переведены в слова."""
    ts = _int(src.get(F_TIME), 0)
    action = _int(src.get(F_ACTION))
    rtype = _int(src.get(F_RTYPE))
    a_label, a_kind = ACTIONS.get(action, (f"действие {src.get(F_ACTION)}", "other"))
    rid = _str(src.get(F_RID))
    return {
        "id": _str(src.get(F_ID)),
        "ts": ts,
        "time": _fmt_ts(ts) if ts else "",
        "user": _str(src.get(F_USER)),
        "userid": _str(src.get(F_USERID)),
        "ip": _str(src.get(F_IP)),
        "action": action,
        "action_label": a_label,
        "action_kind": a_kind,
        "rtype": rtype,
        "rtype_label": RESOURCE_TYPES.get(rtype, f"тип {src.get(F_RTYPE)}"),
        # resourceid=0 — у входа/настроек объекта нет; не показываем «#0».
        "rid": "" if rid in ("", "0") else rid,
        "rname": _str(src.get(F_RNAME)),
        "recordset": _str(src.get(F_RECORDSET)),
        "hosts": [],
        "context": "host",
        "ui_url": "",
        "changes": parse_details(src.get(F_DETAILS)),
    }


# --- Имена из Zabbix API ------------------------------------------------------------
def _iface(o: dict) -> str:
    addr = o.get("ip") if _str(o.get("useip")) == "1" else o.get("dns")
    return f"{addr}:{o.get('port')}"


# Элементы списков, чьё имя можно узнать по ID: kind → (метод, поле ID, формат).
_GROUP_NAMES = {
    "macros": ("usermacro.get", "hostmacroid", lambda o: o.get("macro", "")),
    "interfaces": ("hostinterface.get", "interfaceid", _iface),
}

# Формы ресурсов в веб-интерфейсе Zabbix 7.0: resourcetype → путь с {id} и
# {ctx} (host|template). Модальные формы 7.0 открываются прямой ссылкой
# action=popup&popup=…. Ресурсы без своей формы (настройки, аутентификация)
# ссылки не получают.
UI_PATHS = {
    0: "zabbix.php?action=user.edit&userid={id}",
    3: "zabbix.php?action=popup&popup=mediatype.edit&mediatypeid={id}",
    4: "zabbix.php?action=popup&popup=host.edit&hostid={id}",
    6: "graphs.php?form=update&graphid={id}&context={ctx}",
    11: "zabbix.php?action=usergroup.edit&usrgrpid={id}",
    13: "zabbix.php?action=popup&popup=trigger.edit&triggerid={id}&context={ctx}",
    14: "zabbix.php?action=popup&popup=hostgroup.edit&groupid={id}",
    15: "zabbix.php?action=popup&popup=item.edit&itemid={id}&context={ctx}",
    19: "zabbix.php?action=map.view&sysmapid={id}",
    22: "httpconf.php?form=update&httptestid={id}&context={ctx}",
    23: "zabbix.php?action=popup&popup=discovery.edit&druleid={id}",
    52: "host_discovery.php?form=update&itemid={id}&context={ctx}",
    31: "zabbix.php?action=popup&popup=trigger.prototype.edit&triggerid={id}&parent_discoveryid={lld}&context={ctx}",
    35: "graphs.php?form=update&graphid={id}&parent_discoveryid={lld}&context={ctx}",
    36: "zabbix.php?action=popup&popup=item.prototype.edit&itemid={id}&parent_discoveryid={lld}&context={ctx}",
    37: "host_prototypes.php?form=update&hostid={id}&parent_discoveryid={lld}&context={ctx}",
    25: "zabbix.php?action=popup&popup=script.edit&scriptid={id}",
    26: "zabbix.php?action=popup&popup=proxy.edit&proxyid={id}",
    27: "zabbix.php?action=popup&popup=maintenance.edit&maintenanceid={id}",
    28: "zabbix.php?action=regex.edit&regexid={id}",
    30: "zabbix.php?action=popup&popup=template.edit&templateid={id}",
    33: "zabbix.php?action=dashboard.view&dashboardid={id}",
    44: "zabbix.php?action=userrole.edit&roleid={id}",
    50: "zabbix.php?action=popup&popup=templategroup.edit&groupid={id}",
}


LINK_ACTIONS = (0, 1)  # добавлено, обновлено


def ui_url(env: str, r: dict) -> str:
    """Ссылка на форму ресурса в Zabbix или '' — когда открывать нечего.

    Нет ссылки у удалённого ресурса и у элемента данных/триггера, которого API
    уже не нашёл (удалён позже): форма открылась бы с ошибкой «нет прав или не
    существует», и непонятно, что пошло не так.
    """
    base = config.UI.get(env)
    # Только «добавлено» и «обновлено»: это правки, которые хочется открыть и
    # поправить. У удалённого открывать нечего, а вход/выход/выполнение —
    # события, а не изменения: ссылка на пользователя там только мешает.
    if not base or not r["rid"] or r["action"] not in LINK_ACTIONS:
        return ""
    if r["rtype"] == 29:
        # Макрос — ссылка на того, у кого он правится (см. enrich, шаг 3).
        owner = r.get("macro_owner")
        if not owner:
            return ""
        if owner.get("global"):
            return f"{base}/zabbix.php?action=macros.edit"
        rtype = 30 if owner["template"] else 4
        return f"{base}/{UI_PATHS[rtype].format(id=owner['hostid'], ctx='host')}"
    path = UI_PATHS.get(r["rtype"])
    if not path:
        return ""
    if r["rtype"] in HOST_CONTEXT and config.api_enabled(env) and not r.get("found"):
        return ""
    if "{lld}" in path and not r.get("parent_lld"):
        return ""  # форма прототипа без родительского LLD-правила не откроется
    return f"{base}/{path.format(id=r['rid'], ctx=r.get('context') or 'host', lld=r.get('parent_lld', ''))}"
def _ref_for(field: str):
    for rx, ref in _ID_REFS:
        if rx.search(field):
            return ref
    return None


async def enrich(env: str, records: list) -> None:
    """Дописать в записи имена из Zabbix API: хост объекта и имена по ID в details.

    Запросы группируются по методу: на всю выдачу — по одному вызову на тип
    объекта, а не по вызову на запись.
    """
    if not config.api_enabled(env):
        for r in records:
            r["ui_url"] = ui_url(env, r)
        return

    # 1) хост у элементов данных, триггеров, графиков…
    by_type: dict = {}
    for r in records:
        if r["rtype"] in HOST_CONTEXT and r["rid"]:
            by_type.setdefault(r["rtype"], set()).add(r["rid"])
    for rtype, ids in by_type.items():
        method, id_field, extra = HOST_CONTEXT[rtype]
        found = await client.resolve(env, method, id_field, ids, [id_field], extra)
        for r in records:
            obj = found.get(r["rid"]) if r["rtype"] == rtype else None
            if obj:
                hosts = obj.get("hosts") or ([obj["parentHost"]] if obj.get("parentHost") else [])
                lld = obj.get("discoveryRule") or {}
                if lld.get("itemid"):
                    r["parent_lld"] = lld["itemid"]
                r["hosts"] = [h.get("name", "") for h in hosts]
                # status 3 — шаблон: ссылка в Zabbix открывает форму с context=template
                r["context"] = "template" if any(_str(h.get("status")) == "3" for h in hosts) else "host"
                r["found"] = True

    # 2) ID других объектов внутри details (группы, шаблоны, пользователи…)
    wanted: dict = {}
    for r in records:
        for c in r["changes"]:
            ref = _ref_for(c["field"])
            if not ref:
                continue
            for v in (c["new"], c["old"]):
                if v not in (None, "", "0"):
                    wanted.setdefault(ref, set()).add(str(v))
    for ref, ids in wanted.items():
        method, id_field, name_field = ref
        found = await client.resolve(env, method, id_field, ids, [name_field])
        for r in records:
            for c in r["changes"]:
                if _ref_for(c["field"]) != ref:
                    continue
                for side in ("new", "old"):
                    obj = found.get(_str(c[side]))
                    if obj:
                        c[side + "_label"] = obj.get(name_field)

    # 3) ресурс «Макрос»: своей формы у макроса в Zabbix нет — он правится на
    # вкладке «Макросы» узла сети/шаблона или в Администрирование → Макросы.
    # Чей он — знает только API: сначала ищем как макрос узла, потом глобальный.
    macro_ids = {r["rid"] for r in records if r["rtype"] == 29 and r["rid"]}
    if macro_ids:
        own = await client.resolve(env, "usermacro.get", "hostmacroid", macro_ids, ["hostid"],
                                   {"selectHosts": ["hostid", "name", "status"]})
        rest = macro_ids - set(own)
        glob = await client.resolve(env, "usermacro.get", "globalmacroid", rest, ["macro"],
                                    {"globalmacro": True}) if rest else {}
        for r in records:
            if r["rtype"] != 29:
                continue
            hosts = (own.get(r["rid"]) or {}).get("hosts") or []
            if hosts:
                r["hosts"] = [h.get("name", "") for h in hosts]
                r["macro_owner"] = {
                    "hostid": hosts[0].get("hostid"),
                    "template": _str(hosts[0].get("status")) == "3",
                }
            elif r["rid"] in glob:
                r["macro_owner"] = {"global": True}

    # 4) заголовки групп без имени: при правке макроса Zabbix пишет только
    # «host.macros[123].value» — какой это макрос, знает только API.
    for kind, (method, id_field, fmt) in _GROUP_NAMES.items():
        ids = {c["group_id"] for r in records for c in r["changes"]
               if c.get("group_kind") == kind and c["group_title"] == LIST_TITLES.get(kind)}
        if not ids:
            continue
        found = await client.resolve(env, method, id_field, ids, ["macro", "ip", "dns", "port", "useip"])
        for r in records:
            for c in r["changes"]:
                obj = found.get(c.get("group_id")) if c.get("group_kind") == kind else None
                if obj and c["group_title"] == LIST_TITLES.get(kind):
                    c["group_title"] = f"{LIST_TITLES[kind]} {fmt(obj)}"

    for r in records:
        r["ui_url"] = ui_url(env, r)


# Сколько ID объектов узлов сети максимум добавляем в запрос к ES. У крупного
# узла тысячи элементов данных; больше — значит текст совпал со слишком многими
# узлами, и запрос раздулся бы, не сузив выдачу.
MAX_HOST_IDS = 20000
MAX_HOSTS = 20


async def host_objects(env: str, text: str) -> dict:
    """Узлы сети, чьё имя содержит text, и ВСЕ их ресурсы: {resourcetype: [id, …]}.

    В записи аудита про триггер/элемент данных/график нет имени узла — только
    имя самого объекта. Поэтому узел ищем в Zabbix API, а затем его объекты.
    Шаблоны узла тоже берём: правка триггера в шаблоне меняет его на узле.
    Удалённые объекты API уже не вернёт — их записи по узлу не найдутся.
    Ошибка API не ломает поиск: остаётся поиск по тексту.
    """
    if not config.api_enabled(env) or not text.strip():
        return {}
    try:
        hosts = await client._api_call(env, "host.get", {
            "output": ["hostid"],
            "search": {"host": text, "name": text},
            "searchByAny": True,
            "selectParentTemplates": ["templateid"],
            "limit": MAX_HOSTS,
        })
        if not hosts:
            return {}
        host_ids = [h["hostid"] for h in hosts]
        tpl_ids = sorted({t["templateid"] for h in hosts for t in h.get("parentTemplates") or []})
        out = {4: host_ids}
        if tpl_ids:
            out[30] = tpl_ids
        total = len(host_ids) + len(tpl_ids)
        for rtype in HOST_SEARCHABLE:
            method, id_field, _extra = HOST_CONTEXT[rtype]
            rows = await client._api_call(env, method, {
                "output": [id_field], "hostids": host_ids + tpl_ids,
            })
            ids = [r[id_field] for r in rows]
            total += len(ids)
            if total > MAX_HOST_IDS:
                logging_setup.event(
                    log, "zabbix.host_search_too_wide", level=logging.WARNING,
                    env=env, text=text, hosts=len(host_ids),
                    hint="по тексту нашлось слишком много объектов узлов — ищем только по тексту",
                )
                return {}
            if ids:
                out[rtype] = ids
        return out
    except client.ZabbixAuditError as e:
        logging_setup.event(
            log, "zabbix.api_failed", level=logging.WARNING, env=env, method="host_objects",
            error=str(e), hint="поиск по узлу сети без Zabbix API — только по тексту записи",
        )
        return {}


MAX_LINK_LOOKUPS = 100


async def resolve_deleted_links(index: str, records: list) -> None:
    """«maintenance.hosts[N]: [delete]» → «Узлы сети: удалено web-01».

    При удалении элемента списка-связи (узел из обслуживания, группа у узла,
    шаблон) Zabbix пишет только ID записи связи N — сам узел в записи не назван.
    Но связь когда-то добавляли, и та запись аудита содержит
    «maintenance.hosts[N].hostid: [add, 10084]». Находим её одним запросом к ES
    на всю выдачу и превращаем заголовок в обычную строку «удалено <ID узла>» —
    имя по ID дальше подставит enrich. Не нашли (добавляли до начала хранения
    аудита) — остаётся заголовок с пометкой.
    """
    todo = {}
    for r in records:
        for c in r["changes"]:
            id_field = LINK_ID_FIELDS.get(c.get("group_kind", ""))
            if c.get("header_only") and id_field and c["op"] in ("delete", "detach"):
                todo.setdefault(f"{c['field']}.{id_field}", []).append(c)
    if not todo:
        return
    keys = list(todo)[:MAX_LINK_LOOKUPS]
    body = {
        "size": 500,
        "_source": [F_DETAILS],
        "sort": [{F_TIME: {"order": "desc"}}],
        "query": {"bool": {
            "filter": [{"terms": {F_ACTION: [0, 1]}}],
            "should": [{"wildcard": {F_DETAILS: {"value": f'*"{k}"*'}}} for k in keys],
            "minimum_should_match": 1,
        }},
    }
    try:
        raw = await client.es_search(index, body)
    except client.ZabbixAuditError as e:
        logging_setup.event(log, "zabbix.link_lookup_failed", level=logging.WARNING, error=str(e),
                            hint="удалённые из списков узлы/группы показаны без имени")
        return
    found: dict = {}
    for h in (raw.get("hits") or {}).get("hits", []):
        details = (h.get("_source") or {}).get(F_DETAILS)
        try:
            data = json.loads(details) if isinstance(details, str) else (details or {})
        except ValueError:
            continue
        for k in keys:
            v = data.get(k) if isinstance(data, dict) else None
            if k not in found and isinstance(v, list) and len(v) > 1 and v[1] not in (None, ""):
                found[k] = _str(v[1])
    for k, changes in todo.items():
        if k not in found:
            continue
        for c in changes:
            c.update({
                "field": k, "label": _field_label(k), "old": found[k], "new": None,
                "old_label": None, "new_label": None, "header_only": False,
            })
            for extra in ("group", "group_kind", "group_id", "group_title", "group_op"):
                c.pop(extra, None)


async def search(env: str, filters: dict) -> dict:
    """Поиск записей аудита: ES → нормализация → имена из API."""
    index = config.INDEX[env]
    text = filters.get("name") or filters.get("q") or ""
    filters["host_ids"] = await host_objects(env, text)
    raw = await client.es_search(index, build_query(filters))
    # Паттерн с * не совпал ни с одним ДОСТУПНЫМ индексом → ES отвечает не
    # ошибкой, а пустым результатом с _shards.total = 0. Так выглядят и опечатка
    # в имени, и API key без права read на индекс (ES молча прячет чужие индексы).
    # Без этой проверки вкладка честно писала «записей нет», а Kibana под личной
    # учёткой эти записи показывала.
    if (raw.get("_shards") or {}).get("total") == 0:
        logging_setup.event(
            log, "zabbix.no_indices", level=logging.WARNING, env=env, index=index,
            hint="индекс не найден или у ключа ES нет права read на него",
        )
        raise client.ZabbixAuditError(
            f"в ES не найдено ни одного доступного индекса по «{index}»: проверь имя "
            "(ZBX_AUDIT_INDEX) и права API key на чтение этого индекса"
        )
    hits = raw.get("hits", {})
    total = hits.get("total", 0)
    if isinstance(total, dict):
        total = total.get("value", 0)
    records = [normalize(h.get("_source") or {}) for h in hits.get("hits", [])]
    await resolve_deleted_links(index, records)
    await enrich(env, records)
    # Имени элемента списка нет ни в записи, ни в API (удалён) — хотя бы ID,
    # чтобы два удалённых макроса в одной записи не выглядели одинаково.
    for r in records:
        for c in r["changes"]:
            if c.get("group") and c["group_title"] == LIST_TITLES.get(c["group_kind"], c["group_kind"]):
                # у списков-связей N — ID записи связи, а не объекта: так и пишем
                what = "ID связи" if c["group_kind"] in LINK_ID_FIELDS else "ID"
                c["group_title"] += f" ({what} {c['group_id']})"
    return {"total": total, "records": records, "limit": config.MAX_RECORDS}
