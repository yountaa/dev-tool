"""NDJSON-логирование: каждая строка лога — один JSON-объект.

Всё уходит в stdout (контейнер отдаёт это в Docker/ELK), в файлы не пишем.

Формат строки:  ts | level | module | msg | поля события

`module` — ЧЕЙ это лог, по нему логи и фильтруют. Берётся автоматически из имени
логгера: первый сегмент до точки (getLogger("victoria.client") → victoria),
поэтому модулю достаточно назвать свой логгер своим именем. Явное поле
`module=` в event() перебивает это — нужно там, где строку пишет общий код от
имени модуля (например middleware ошибок HTTP в main.py).

Имя логгера дальше первого сегмента в строку не выносится: где именно сработал
код, видно из самого сообщения, а лишнее поле только мешает читать.
"""
import datetime
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # копия: тот же record может форматироваться повторно, а module мы извлекаем
        fields = getattr(record, "fields", None)
        fields = dict(fields) if isinstance(fields, dict) else {}
        data = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "module": fields.pop("module", None) or record.name.split(".", 1)[0],
            "msg": record.getMessage(),
        }
        data.update(fields)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup() -> None:
    """Все логи (наши + uvicorn) — в stdout одним NDJSON-потоком."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Сторонние логгеры пишут своими хендлерами — заворачиваем в корневой.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    # Шумные чужие логгеры — только с уровня WARNING:
    #   httpx/httpcore пишут INFO-строку на КАЖДЫЙ исходящий запрос («HTTP Request:
    #     GET http://… "200 OK"»). У модуля-прокси это сотни строк в минуту, в
    #     которых нет информации: что запрашивали — видно в интерфейсе, а поломки
    #     мы логируем сами и с понятным текстом;
    #   apscheduler на старте пишет пять строк про постановку задач — то же самое
    #     говорит наше событие scheduler.started, но одной строкой и с полями.
    for name in ("httpx", "httpcore", "apscheduler"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.WARNING)

    # Access-лог uvicorn глушим — проблемные запросы пишет middleware в main.py.
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False


def event(logger: logging.Logger, name: str, /, *, level: int = logging.INFO, **fields) -> None:
    """Структурное событие: name попадает в msg, остальное — отдельными полями.

    logger и name — позиционные, чтобы поле name= не конфликтовало с параметром.
    level — уровень записи (по умолчанию INFO).
    module= — переопределить модуль, если пишем от имени чужого (обычно не нужно).
    """
    logger.log(level, name, extra={"fields": fields})
