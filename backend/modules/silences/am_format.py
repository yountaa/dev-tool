"""Хелперы под формат Alertmanager. Общие для разового и расписания."""
import re
from datetime import datetime, timezone

# Тип и id конфига прячем в начало комментария: «[schedule:ab12] текст».
# По метке потом понимаем — разовый silence или по расписанию, и какой конфиг.
_TAG_RE = re.compile(r"^\[(onetime|schedule):([0-9a-f]+)\]\s*(.*)$", re.S)


def tag_comment(kind: str, cfg_id: str, text: str) -> str:
    return f"[{kind}:{cfg_id}] {text}"


def parse_comment(comment: str):
    """Вернуть (kind, config_id, чистый текст). Без метки — (None, None, текст)."""
    m = _TAG_RE.match(comment or "")
    if not m:
        return None, None, comment or ""
    return m.group(1), m.group(2), m.group(3)


def to_am_time(dt: datetime) -> str:
    """Время в формате AM: UTC с Z на конце."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def matchers_to_am(matchers) -> list:
    """Matcher-модели -> словари для AM. isEqual всегда True (только совпадение)."""
    return [
        {"name": m.name, "value": m.value, "isRegex": m.isRegex, "isEqual": True}
        for m in matchers
    ]


def same_silence(body: dict, existing: dict) -> bool:
    """Такой silence уже есть в AM? Сравниваем matchers + endsAt + comment (start не важен)."""
    ex_matchers = [
        {"name": m["name"], "value": m["value"], "isRegex": m["isRegex"], "isEqual": m.get("isEqual", True)}
        for m in existing.get("matchers", [])
    ]
    return (
        body["matchers"] == ex_matchers
        and body["endsAt"] == existing.get("endsAt")
        and body["comment"] == existing.get("comment")
    )
