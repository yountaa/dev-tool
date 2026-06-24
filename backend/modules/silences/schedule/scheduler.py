"""Шедулер режима «по расписанию»: по таймеру обходит расписания и ставит silence.

Частота — переменная SILENCE_CRON (по умолчанию раз в 5 минут).
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .. import config, save_hub
from ..am_format import parse_comment, same_silence, tag_comment
from ..client import AlertmanagerError, active_silences, create_silence
from ..models import ManualRequest, ScheduleRequest
from ..manual.builder import build_manual
from .builder import build_schedule

log = logging.getLogger("silences.scheduler")

scheduler = AsyncIOScheduler(timezone=config.SILENCE_TZ)
_TZ = ZoneInfo(config.SILENCE_TZ)


async def apply_config(cfg) -> None:
    """Поставить в AM недостающие silence одного расписания (сразу, без ожидания тика).

    Зовётся и из тика шедулера, и из веба при создании/включении правила.
    """
    req = ScheduleRequest(**cfg.payload)
    bodies = build_schedule(req)
    if not bodies:
        return
    existing = await active_silences(cfg.env)
    # отбираем только недостающие, ставим их в AM параллельно (а не по одному)
    to_create = []
    for body in bodies:
        body["comment"] = tag_comment("schedule", cfg.id, req.name, body["comment"])
        if not any(same_silence(body, e) for e in existing):
            to_create.append(body)
    if to_create:
        await asyncio.gather(*(create_silence(cfg.env, b) for b in to_create))
        log.info("[%s] поставлено %d silence по расписанию %s", cfg.env, len(to_create), cfg.id)


async def reconcile_manual(cfg) -> None:
    """До-ставить разовый silence, если он включён, время не прошло, а в AM его нет.

    Нужно на случай, когда AM лежал в момент создания правила.
    """
    try:
        end = datetime.fromisoformat(cfg.payload["ends_at"])
    except Exception:
        return
    if datetime.now(_TZ).replace(tzinfo=None) > end:
        return  # время правила уже прошло — ставить нечего

    for s in await active_silences(cfg.env):
        kind, sid, _ = parse_comment(s.get("comment", ""))
        if kind == "manual" and sid == cfg.id:
            return  # уже стоит в AM

    body = build_manual(ManualRequest(**cfg.payload))
    body["comment"] = tag_comment("manual", cfg.id, cfg.payload.get("name", ""), body["comment"])
    am_id = await create_silence(cfg.env, body)
    save_hub.save("manual", cfg.env, cfg.payload, cfg_id=cfg.id,
                  enabled=True, am_id=am_id, created_at=cfg.created_at, actor="scheduler", action="доставил")
    log.info("[%s] до-ставлен разовый silence %s", cfg.env, cfg.id)


async def run_once() -> None:
    """Один тик: поставить недостающие silence по всем включённым правилам."""
    save_hub.ensure_repo()
    for cfg in save_hub.list_configs("schedule"):
        if cfg.enabled:
            try:
                await apply_config(cfg)
            except AlertmanagerError as e:
                log.warning("[%s] AM недоступен, повторю позже: %s", cfg.env, e)
    for cfg in save_hub.list_configs("manual"):
        if cfg.enabled:
            try:
                await reconcile_manual(cfg)
            except AlertmanagerError as e:
                log.warning("[%s] AM недоступен, повторю позже: %s", cfg.env, e)


def start() -> None:
    """Запустить шедулер. Зовётся один раз на старте приложения.

    Работает от локальных правил (git не обязателен).
    """
    scheduler.add_job(
        run_once,
        CronTrigger.from_crontab(config.SILENCE_CRON, timezone=config.SILENCE_TZ),
        id="silences_schedule",
        replace_existing=True,
    )
    scheduler.start()
    log.info("шедулер запущен, cron=%s", config.SILENCE_CRON)
