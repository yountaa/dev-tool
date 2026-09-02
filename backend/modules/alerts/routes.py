"""API метаданных alerts: история, кэш engine/конфигов и lookup fields/indices.

Source of truth конфигов — n8n Data Tables. Postgres держит копию для
мгновенного UI на F5, журнал History и TTL-кэш справочников ELK.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from access import current_user, require_module

from . import client, config, storage

router = APIRouter(
    prefix="/alerts",
    tags=["alerts"],
    dependencies=[Depends(require_module("alerts"))],
)


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


class LookupBody(BaseModel):
    index: str = ""
    q: str = ""


class FolderCreateBody(BaseModel):
    name: str


class FolderRenameBody(BaseModel):
    name: str


class MoveAlertBody(BaseModel):
    alertKey: str
    folderId: int | None = None


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
    keys = [
        a.get("alertKey") or a.get("configId") or ""
        for a in (body.alerts or [])
    ]
    storage.prune_folder_members([k for k in keys if k])
    eng = None
    if body.engine and body.engine.lastRunAt:
        eng = storage.set_engine(body.engine.lastRunAt, body.engine.lastError)
    patterns = (
        storage.set_index_patterns(body.indexPatterns)
        if body.indexPatterns
        else storage.get_index_patterns()
    )
    return {"ok": True, "count": n, "engine": eng, "indexPatterns": patterns}


@router.get("/engine")
def get_engine():
    """Последний известный heartbeat backend-workflow (из Postgres)."""
    return {"engine": storage.get_engine()}


@router.put("/engine")
def put_engine(body: EngineBody):
    """Обновить кэш heartbeat после list из n8n."""
    eng = storage.set_engine(body.lastRunAt, body.lastError)
    return {"engine": eng}


@router.post("/fields")
async def post_fields(body: LookupBody):
    """Поля индекса: кэш Postgres → при промахе n8n (ELK)."""
    index = str(body.index or "").strip()
    if not index:
        return {"ok": False, "message": "не указан индекс", "fields": []}
    token = storage.lookup_cache_token({"index": index})
    cached = storage.get_lookup_cache("fields", token, config.FIELDS_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    data = await client.call_webhook("fields", {"index": index})
    payload = {
        "ok": True,
        "fields": data.get("fields") or data.get("names") or data.get("fieldNames") or [],
    }
    return storage.set_lookup_cache("fields", token, payload)


@router.post("/indices")
async def post_indices(body: LookupBody):
    """Паттерны индексов: кэш Postgres → при промахе n8n (ELK)."""
    q = str(body.q or "").strip()
    token = storage.lookup_cache_token({"q": q})
    cached = storage.get_lookup_cache("indices", token, config.INDICES_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached
    data = await client.call_webhook("indices", {"q": q})
    payload = {
        "ok": True,
        "patterns": data.get("patterns") or data.get("indices") or data.get("names") or [],
    }
    return storage.set_lookup_cache("indices", token, payload)


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


@router.get("/folders")
def get_folders():
    """Список папок и membership alertKey → folder."""
    return {"folders": storage.list_folders()}


@router.post("/folders")
def post_folder(body: FolderCreateBody):
    folder = storage.create_folder(body.name)
    if not folder:
        return {"ok": False, "detail": "не удалось создать папку"}
    return {"ok": True, "folder": folder}


@router.patch("/folders/{folder_id}")
def patch_folder(folder_id: int, body: FolderRenameBody):
    folder = storage.rename_folder(folder_id, body.name)
    if not folder:
        return {"ok": False, "detail": "папка не найдена или имя пустое"}
    return {"ok": True, "folder": folder}


@router.delete("/folders/{folder_id}")
def delete_folder_route(folder_id: int):
    ok = storage.delete_folder(folder_id)
    return {"ok": ok}


@router.put("/folders/move")
def put_move(body: MoveAlertBody):
    """Переместить алерт в папку (folderId=null — без папки)."""
    return storage.move_alert(body.alertKey, body.folderId)
