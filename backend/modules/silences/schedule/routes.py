"""Роуты «По расписанию». Хранят расписания в git, ставит их шедулер.

Исключение — /preview: ничего не сохраняет, просто показывает, что встанет.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import client, config, save_hub
from ..am_format import same_silence
from ..deps import current_user, require_env
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
async def create_schedule(env: str, req: ScheduleRequest, user: str = Depends(current_user)):
    """Сохранить новое расписание и сразу поставить его silence в AM."""
    require_env(env)
    if config.AUTH_ENABLED:
        req.created_by = user  # с авторизацией автор = залогиненный (из Keycloak), форму игнорируем
    actor = req.created_by or user  # без авторизации — имя из формы (как раньше)
    payload = req.model_dump(mode="json")
    dup = save_hub.find_duplicate("schedule", env, payload)
    if dup is not None:
        raise HTTPException(409, f"Такое правило уже есть: «{dup.payload.get('name') or dup.id}»")
    cfg = save_hub.save("schedule", env, payload, actor=actor, action="создал")
    await scheduler.apply_config(cfg)
    event(log, "rule.created", env=env, kind="schedule", id=cfg.id, name=req.name, windows=len(req.windows))
    return cfg


@router.delete("/{config_id}")
def delete_schedule(env: str, config_id: str, user: str = Depends(current_user)):
    """Удалить расписание из git."""
    require_env(env)
    if not save_hub.delete("schedule", env, config_id, actor=user):
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
