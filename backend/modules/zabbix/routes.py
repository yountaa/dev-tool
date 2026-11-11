"""Роутер модуля zabbix: вкладка «Zabbix Audit» — кто что сделал в Zabbix.

Все роуты закрыты require_module("zabbix") — RBAC уровня приложения.
"""
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from access import require_module

from . import audit, config

router = APIRouter(
    prefix="/zabbix",
    tags=["zabbix"],
    dependencies=[Depends(require_module("zabbix"))],
)


@router.get("/environments")
def environments():
    """Инстансы Zabbix для вкладок. Индексы и адреса наружу не отдаются."""
    return [{"name": name, "api": config.api_enabled(name)} for name in config.ENVIRONMENTS]


@router.get("/dictionaries")
def dictionaries():
    """Справочники для фильтров: действия и типы объектов."""
    return {
        "actions": [{"id": k, "label": v[0], "kind": v[1]} for k, v in audit.ACTIONS.items()],
        "types": sorted(
            [{"id": k, "label": v} for k, v in audit.RESOURCE_TYPES.items()],
            key=lambda t: t["label"],
        ),
    }


def _ints(value: Optional[str]) -> list:
    """'0,1,2' → [0, 1, 2]; мусор отбрасываем, а не роняем запрос."""
    out = []
    for part in (value or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


@router.get("/{env}/audit")
async def search(
    env: str,
    last: Optional[int] = None,          # период «последние N секунд»
    from_: Optional[str] = Query(None, alias="from"),  # либо явный период: настенное время APP_TZ
    to: Optional[str] = None,
    actions: Optional[str] = None,       # через запятую: 0,1,2
    types: Optional[str] = None,         # через запятую: 4,13,15
    user: Optional[str] = None,
    ip: Optional[str] = None,
    name: Optional[str] = None,
    q: Optional[str] = None,
    rid: Optional[str] = None,
    recordset: Optional[str] = None,
    size: Optional[int] = None,
):
    """Записи аудита за период с фильтрами; новые сверху."""
    if not config.known_env(env):
        raise HTTPException(404, f"окружение «{env}» не настроено (нет zbxaudit_{env})")
    now = int(time.time())
    try:
        start = audit.to_epoch(from_) if from_ else now - (last or 86400)
        end = audit.to_epoch(to) if to else now
    except ValueError:
        raise HTTPException(400, "неверный формат периода: ожидается ГГГГ-ММ-ДДTЧЧ:ММ")
    if start > end:
        raise HTTPException(400, "начало периода позже конца")
    filters = {
        "from": start, "to": end,
        "actions": _ints(actions), "types": _ints(types),
        "user": (user or "").strip(), "ip": (ip or "").strip(),
        "name": (name or "").strip(), "q": (q or "").strip(),
        "rid": (rid or "").strip(), "recordset": (recordset or "").strip(),
        "size": size,
    }
    result = await audit.search(env, filters)
    result["from"] = start
    result["to"] = end
    return result
