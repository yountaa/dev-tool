"""Сборка тела разового silence — даты уже заданы, просто кладём в формат AM."""
from ..am_format import matchers_to_am, to_am_time
from ..models import ManualRequest


def build_manual(req: ManualRequest) -> dict:
    return {
        "matchers": matchers_to_am(req.matchers),
        "startsAt": to_am_time(req.starts_at),
        "endsAt": to_am_time(req.ends_at),
        "createdBy": req.created_by,
        "comment": req.comment,
    }
