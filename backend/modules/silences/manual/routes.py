"""Роуты «Разовый»: silence ставится в AM сразу, конфиг сохраняем в git для истории."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from .. import client, config, save_hub
from ..am_format import tag_comment
from ..client import AlertmanagerError
from ..deps import current_user, require_env
from ..models import OnetimeRequest
from .builder import build_onetime
from logging_setup import event

router = APIRouter(prefix="/{env}/onetime", tags=["silences:onetime"])
log = logging.getLogger("silences.api")


@router.post("")
async def create_onetime(env: str, req: OnetimeRequest, user: str = Depends(current_user)):
    """Создать разовый правило: сохраняем всегда, в AM ставим сразу (если доступен).

    Если AM лежит — правило всё равно сохранится, а шедулер до-ставит silence,
    когда AM поднимется (пока время правила не прошло).
    """
    require_env(env)
    if config.AUTH_ENABLED:
        req.created_by = user  # с авторизацией автор = залогиненный (из Keycloak), форму игнорируем
    actor = req.created_by or user  # без авторизации — имя из формы (как раньше)
    payload = req.model_dump(mode="json")
    dup = save_hub.find_duplicate("onetime", env, payload)
    if dup is not None:
        raise HTTPException(409, f"Такое правило уже есть: «{dup.payload.get('name') or dup.id}»")
    cfg_id = uuid.uuid4().hex[:8]
    am_id = ""
    try:
        body = build_onetime(req)
        body["comment"] = tag_comment("onetime", cfg_id, req.name, body["comment"])  # метим для связи с правилом
        am_id = await client.create_silence(env, body)
    except AlertmanagerError:
        pass  # AM недоступен — поставит шедулер позже
    cfg = save_hub.save("onetime", env, payload, cfg_id=cfg_id, am_id=am_id, actor=actor, action="создал")
    event(log, "rule.created", env=env, kind="onetime", id=cfg.id, name=req.name, placed=bool(am_id))
    return {"silence_id": am_id, "config_id": cfg.id, "placed": bool(am_id)}


@router.get("")
def list_onetime(env: str):
    """Показать все разовые конфиги этого окружения (история из git)."""
    require_env(env)
    return save_hub.list_configs("onetime", env)
