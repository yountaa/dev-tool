"""Главный роутер модуля: окружения, локальные правила, под-роутеры.

Источник правды — локальный git-хаб, а не Alertmanager. Список и статус правил
считаем у себя; в AM только применяем (разовый — сразу, расписание — шедулером).
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from logging_setup import event

from . import client, config, save_hub
from .am_format import parse_comment, tag_comment
from .client import AlertmanagerError
from .deps import current_user, require_env
from .models import OnetimeRequest, ScheduleRequest
from .onetime.builder import build_onetime
from .onetime.routes import router as onetime_router
from .schedule import scheduler
from .schedule.routes import router as schedule_router

router = APIRouter(prefix="/silences", tags=["silences"])

_TZ = ZoneInfo(config.SILENCE_TZ)
log = logging.getLogger("silences.api")


# --- Окружения ----------------------------------------------------------------
@router.get("/environments")
def environments():
    """Имена алертменеджеров для вкладок. Реальные URL наружу не отдаём."""
    return [{"name": name} for name in config.ALERTMANAGERS]


@router.get("/me")
def me(user: str = Depends(current_user)):
    """Кто залогинен (для показа в форме). auth=true — имя пришло из Keycloak через прокси."""
    return {"name": user, "auth": config.AUTH_ENABLED}


# --- Правила (читаем и считаем локально, AM не дёргаем) -----------------------
def _status(cfg) -> str:
    """Статус правила по локальным данным: disabled / pending / active / expired."""
    if not cfg.enabled:
        return "disabled"
    if cfg.kind == "onetime":
        try:
            now = datetime.now(_TZ).replace(tzinfo=None)
            start = datetime.fromisoformat(cfg.payload["starts_at"])
            end = datetime.fromisoformat(cfg.payload["ends_at"])
        except Exception:
            return "active"
        if now < start:
            return "pending"
        if now <= end:
            return "active"
        return "expired"
    return "active"  # включённое расписание считаем работающим


def _rule(cfg) -> dict:
    return {
        "id": cfg.id,
        "kind": cfg.kind,
        "name": cfg.payload.get("name", ""),
        "enabled": cfg.enabled,
        "status": _status(cfg),
        "payload": cfg.payload,
    }


@router.get("/{env}/rules")
def list_rules(env: str):
    """Все локальные правила окружения (разовые + по расписанию) со статусом."""
    require_env(env)
    rules = []
    for kind in ("onetime", "schedule"):
        for cfg in save_hub.list_configs(kind, env):
            rules.append(_rule(cfg))
    return rules


def _require_rule(kind: str, env: str, cfg_id: str):
    cfg = save_hub.get_config(kind, env, cfg_id)
    if cfg is None:
        raise HTTPException(404, "правило не найдено")
    return cfg


async def _expire(env: str, am_id: str) -> None:
    """Погасить silence в AM, не падая, если его уже нет."""
    if not am_id:
        return
    try:
        await client.delete_silence(env, am_id)
    except AlertmanagerError:
        pass


async def _expire_schedule(env: str, cfg_id: str) -> None:
    """Погасить в AM все silence этого расписания (ищем по метке, гасим параллельно)."""
    try:
        silences = await client.active_silences(env)
    except AlertmanagerError:
        return
    ids = []
    for s in silences:
        kind, sid, _ = parse_comment(s.get("comment", ""))
        if kind == "schedule" and sid == cfg_id:
            ids.append(s.get("id"))
    if ids:
        await asyncio.gather(*(_expire(env, i) for i in ids))


async def _apply_onetime(env: str, cfg_id: str, payload: dict, old_am_id: str) -> str:
    """Переставить разовый silence в AM (старый гасим) и вернуть новый am_id."""
    await _expire(env, old_am_id)
    body = build_onetime(OnetimeRequest(**payload))
    body["comment"] = tag_comment("onetime", cfg_id, payload.get("name", ""), body["comment"])
    return await client.create_silence(env, body)


async def _try_apply_onetime(env: str, cfg_id: str, payload: dict, old_am_id: str) -> str:
    """Как _apply_onetime, но при недоступном AM не роняет запрос: вернёт "".

    Конфиг тогда сохранится без am_id, а silence до-поставит шедулер
    (reconcile_onetime) — так правка/включение не «ломают» правило в вебе.
    """
    try:
        return await _apply_onetime(env, cfg_id, payload, old_am_id)
    except AlertmanagerError as e:
        log.warning("[%s] не удалось поставить разовый silence %s в AM: %s", env, cfg_id, e)
        return ""


@router.post("/{env}/rules/{kind}/{cfg_id}/enable")
async def enable_rule(env: str, kind: str, cfg_id: str, user: str = Depends(current_user)):
    """Включить правило. Разовый сразу ставим в AM, расписание оживит шедулер."""
    require_env(env)
    cfg = _require_rule(kind, env, cfg_id)
    am_id = cfg.am_id
    if kind == "onetime":
        am_id = await _try_apply_onetime(env, cfg_id, cfg.payload, cfg.am_id)
    saved = save_hub.save(kind, env, cfg.payload, cfg_id=cfg_id, enabled=True, am_id=am_id,
                          created_at=cfg.created_at, actor=user, action="включил")
    if kind == "schedule":
        try:
            await scheduler.apply_config(saved)  # ставим сразу, не ждём тика
        except AlertmanagerError as e:
            log.warning("[%s] не удалось поставить расписание %s в AM: %s", env, cfg_id, e)
    event(log, "rule.enabled", env=env, kind=kind, id=cfg_id, name=cfg.payload.get("name", ""))
    return {"ok": True}


@router.post("/{env}/rules/{kind}/{cfg_id}/disable")
async def disable_rule(env: str, kind: str, cfg_id: str, user: str = Depends(current_user)):
    """Выключить правило. Разовый гасим в AM, расписание перестанет ставить шедулер."""
    require_env(env)
    cfg = _require_rule(kind, env, cfg_id)
    if kind == "onetime":
        await _expire(env, cfg.am_id)
    else:
        await _expire_schedule(env, cfg_id)  # гасим уже поставленные шедулером silence
    save_hub.save(kind, env, cfg.payload, cfg_id=cfg_id, enabled=False, am_id="",
                  created_at=cfg.created_at, actor=user, action="выключил")
    event(log, "rule.disabled", env=env, kind=kind, id=cfg_id, name=cfg.payload.get("name", ""))
    return {"ok": True}


@router.delete("/{env}/rules/{kind}/{cfg_id}")
async def delete_rule(env: str, kind: str, cfg_id: str, user: str = Depends(current_user)):
    """Удалить правило: гасим silence в AM (если разовый) и убираем конфиг из git."""
    require_env(env)
    cfg = _require_rule(kind, env, cfg_id)
    if kind == "onetime":
        await _expire(env, cfg.am_id)
    else:
        await _expire_schedule(env, cfg_id)
    save_hub.delete(kind, env, cfg_id, actor=user)
    event(log, "rule.deleted", env=env, kind=kind, id=cfg_id, name=cfg.payload.get("name", ""))
    return {"ok": True}


@router.put("/{env}/rules/onetime/{cfg_id}")
async def edit_onetime_rule(env: str, cfg_id: str, req: OnetimeRequest, user: str = Depends(current_user)):
    """Изменить разовое правило. Если включено — переставляем silence в AM."""
    require_env(env)
    cfg = _require_rule("onetime", env, cfg_id)
    payload = req.model_dump(mode="json")
    if config.AUTH_ENABLED:
        payload["created_by"] = cfg.payload.get("created_by", req.created_by)  # с авторизацией создателя не меняем
    am_id = cfg.am_id
    if cfg.enabled:
        am_id = await _try_apply_onetime(env, cfg_id, payload, cfg.am_id)
    save_hub.save("onetime", env, payload, cfg_id=cfg_id,
                  enabled=cfg.enabled, am_id=am_id, created_at=cfg.created_at, actor=user, action="изменил")
    event(log, "rule.updated", env=env, kind="onetime", id=cfg_id, name=req.name)
    return {"ok": True}


@router.put("/{env}/rules/schedule/{cfg_id}")
async def edit_schedule_rule(env: str, cfg_id: str, req: ScheduleRequest, user: str = Depends(current_user)):
    """Изменить расписание. Старые окна гасим, новые поставит шедулер на ближайшем проходе."""
    require_env(env)
    cfg = _require_rule("schedule", env, cfg_id)
    payload = req.model_dump(mode="json")
    if config.AUTH_ENABLED:
        payload["created_by"] = cfg.payload.get("created_by", req.created_by)  # с авторизацией создателя не меняем
    saved = save_hub.save("schedule", env, payload, cfg_id=cfg_id,
                          enabled=cfg.enabled, created_at=cfg.created_at, actor=user, action="изменил")
    if cfg.enabled:
        await _expire_schedule(env, cfg_id)       # убираем silence со старыми окнами
        try:
            await scheduler.apply_config(saved)   # и сразу ставим с новыми
        except AlertmanagerError as e:
            log.warning("[%s] не удалось переставить расписание %s в AM: %s", env, cfg_id, e)
    event(log, "rule.updated", env=env, kind="schedule", id=cfg_id, name=req.name)
    return {"ok": True}


# Под-роутеры создания (свои префиксы /{env}/onetime, /{env}/schedules).
router.include_router(onetime_router)
router.include_router(schedule_router)
