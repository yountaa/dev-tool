"""Роуты «По расписанию». Хранят расписания в git, ставит их шедулер.

Исключение — /preview: ничего не сохраняет, просто показывает, что встанет.
"""
import logging

from fastapi import APIRouter, HTTPException

from .. import client, save_hub
from ..am_format import same_silence
from ..deps import require_env
from ..models import ScheduleRequest
from . import scheduler
from .builder import build_schedule
from logging_setup import event

router = APIRouter(prefix="/{env}/schedules", tags=["silences:schedule"])
log = logging.getLogger("silences.api")


@router.get("")
def list_schedules(env: str):
    """Показать все расписания этого окружения."""
    require_env(env)
    return save_hub.list_configs("schedule", env)


@router.post("")
async def create_schedule(env: str, req: ScheduleRequest):
    """Сохранить новое расписание и сразу поставить его silence в AM."""
    require_env(env)
    cfg = save_hub.save("schedule", env, req.model_dump(mode="json"))
    await scheduler.apply_config(cfg)
    event(log, "rule.created", env=env, kind="schedule", id=cfg.id, name=req.name, windows=len(req.windows))
    return cfg


@router.put("/{config_id}")
def update_schedule(env: str, config_id: str, req: ScheduleRequest):
    """Переписать существующее расписание (редактирование из веба)."""
    require_env(env)
    if save_hub.get_config("schedule", env, config_id) is None:
        raise HTTPException(404, "расписание не найдено")
    return save_hub.save("schedule", env, req.model_dump(mode="json"), cfg_id=config_id)


@router.delete("/{config_id}")
def delete_schedule(env: str, config_id: str):
    """Удалить расписание из git."""
    require_env(env)
    if not save_hub.delete("schedule", env, config_id):
        raise HTTPException(404, "расписание не найдено")
    return {"ok": True}


@router.post("/preview")
async def preview_schedule(env: str, req: ScheduleRequest):
    """Что встанет по расписанию сейчас. Помечаем already_exists для веба."""
    require_env(env)
    bodies = build_schedule(req)
    existing = await client.active_silences(env)
    for body in bodies:
        body["already_exists"] = any(same_silence(body, e) for e in existing)
    return bodies
