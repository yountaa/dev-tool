"""Расписание -> тела silence. Вся возня с датами и переводом в UTC тут."""
from __future__ import annotations  # ... | None на Python 3.9

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config
from ..am_format import matchers_to_am, to_am_time
from ..models import ScheduleRequest, Window

_TZ = ZoneInfo(config.SILENCE_TZ)


def _next_occurrence(window: Window, now: datetime) -> tuple[datetime, datetime] | None:
    """Ближайшее ещё не прошедшее окно за 7 дней вперёд (или None)."""
    sh, sm = map(int, window.start.split(":"))
    eh, em = map(int, window.end.split(":"))

    for offset in range(8):  # сегодня + 7 дней
        day = now + timedelta(days=offset)
        if day.strftime("%a").lower() not in window.days:
            continue

        start_dt = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
        end_dt = day.replace(hour=eh, minute=em, second=0, microsecond=0)
        if end_dt <= start_dt:  # окно через полночь, напр. 23:00 -> 01:00
            end_dt += timedelta(days=1)

        if end_dt <= now:  # окно уже прошло — дальше
            continue
        return start_dt, end_dt

    return None


def build_schedule(req: ScheduleRequest) -> list[dict]:
    """По телу silence на каждое окно (ближайшее вхождение)."""
    now = datetime.now(_TZ)
    bodies: list[dict] = []
    for window in req.windows:
        occ = _next_occurrence(window, now)
        if occ is None:
            continue
        start_dt, end_dt = occ
        bodies.append({
            "matchers": matchers_to_am(req.matchers),
            "startsAt": to_am_time(start_dt),
            "endsAt": to_am_time(end_dt),
            "createdBy": req.created_by,
            "comment": req.comment,
        })
    return bodies
