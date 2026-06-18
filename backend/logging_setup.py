"""NDJSON-логирование: каждая строка лога — один JSON-объект.

Всё уходит в stdout (контейнер отдаёт это в Docker/ELK), в файлы не пишем.
Структурные события удобно агрегировать: ключевые поля идут отдельными ключами.
"""
import datetime
import json
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            data.update(fields)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup() -> None:
    """Все логи (наши + uvicorn + httpx) — в stdout одним NDJSON-потоком."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Сторонние логгеры пишут своими хендлерами — заворачиваем в корневой.
    for name in ("uvicorn", "uvicorn.error", "httpx"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # Access-лог uvicorn глушим — запросы пишем своим middleware (структурно).
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False


def event(logger: logging.Logger, name: str, /, **fields) -> None:
    """Структурное событие: name попадает в msg, остальное — отдельными полями.

    logger и name — позиционные, чтобы поле name= не конфликтовало с параметром.
    """
    logger.info(name, extra={"fields": fields})
