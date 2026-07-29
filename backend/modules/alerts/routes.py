"""API метаданных alerts: история, кэш engine и зеркало конфигов.

Source of truth конфигов — n8n Data Tables. Postgres держит копию для
мгновенного UI на F5 и журнал History.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from access import current_user
from . import storage

router = APIRouter(prefix="/alerts", tags=["alerts"])


class EngineBody(BaseModel):
    lastRunAt: str | None = None
    lastError: str | None = None


class HistoryBody(BaseModel):
    action: str
    name: str = ""
    alertKey: str = ""
    before: dict | None = None
    after: dict | None = None


class ConfigsBody(BaseModel):
    alerts: list[dict] = Field(default_factory=list)
    engine: EngineBody | None = None
    indexPatterns: list[str] = Field(default_factory=list)


@router.get("/cache")
def get_cache():
    """Полный кэш для гидрации UI: alerts + engine + indexPatterns."""
    return {
        "alerts": storage.list_configs(),
        "engine": storage.get_engine(),
        "indexPatterns": storage.get_index_patterns(),
    }


@router.put("/cache")
def put_cache(body: ConfigsBody):
    """Синхронизировать кэш после успешного list из n8n."""
    n = storage.replace_configs(body.alerts)
    eng = None
    if body.engine and body.engine.lastRunAt:
        eng = storage.set_engine(body.engine.lastRunAt, body.engine.lastError)
    patterns = storage.set_index_patterns(body.indexPatterns) if body.indexPatterns else storage.get_index_patterns()
    return {"ok": True, "count": n, "engine": eng, "indexPatterns": patterns}


@router.get("/engine")
def get_engine():
    """Последний известный heartbeat backend-workflow (из Postgres)."""
    eng = storage.get_engine()
    return {"engine": eng}


@router.put("/engine")
def put_engine(body: EngineBody):
    """Обновить кэш heartbeat после list из n8n."""
    eng = storage.set_engine(body.lastRunAt, body.lastError)
    return {"engine": eng}


@router.get("/history")
def get_history():
    """Журнал create/update/delete алертов."""
    return storage.history()


@router.post("/history")
def post_history(request: Request, body: HistoryBody):
    """Записать событие в журнал (вызывается фронтом после успешного CRUD в n8n)."""
    user = current_user(request)
    item = storage.record_history(
        user=user,
        action=body.action,
        name=body.name,
        alert_key=body.alertKey,
        before=body.before,
        after=body.after,
    )
    return item or {"ok": False, "detail": "postgres unavailable"}
