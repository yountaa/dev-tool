"""Роутер модуля Victoria Metrics: одна вкладка приложения.

Мы прокси к кластерам VM. Каждое окружение (кластер) = vm_<env> в конфиге; у него
опционально есть vmagent (targets) и vmalert (rules/alerts). Все роуты закрыты
проверкой доступа require_module("victoria") — RBAC уровня приложения.

Ответы VM отдаём сырыми байтами (Response, media_type=json) — без разбора и
пересборки JSON на нашей стороне: так прокси почти не добавляет задержки к
скорости самой VM (см. client.py).
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from access import require_module

from . import client, config, disk
from .deps import require_env

# Вешаем RBAC на весь роутер: нет доступа к модулю victoria → 403 на любой роут.
router = APIRouter(
    prefix="/victoria",
    tags=["victoria"],
    dependencies=[Depends(require_module("victoria"))],
)


def _json(raw: bytes) -> Response:
    """Сырые байты ответа VM → HTTP-ответ как есть."""
    return Response(content=raw, media_type="application/json")


# --- Окружения ----------------------------------------------------------------
@router.get("/environments")
def environments():
    """Кластеры для вкладок + какие под-вкладки доступны у каждого.

    query и cardinality есть всегда (vmselect обязателен), targets — если задан
    vmagent, rules — если задан vmalert. Реальные URL наружу не отдаём.
    """
    out = []
    for name in config.ENVIRONMENTS:
        out.append({
            "name": name,
            "query": True,
            "cardinality": True,
            "targets": name in config.VMAGENT,
            "rules": name in config.VMALERT,
            # подписи тенантов (пусто → выбора нет, одно-тенантный кластер)
            "tenants": config.tenant_labels(name),
        })
    return out


# --- Под-вкладка «Диск»: заполненность по ВСЕМ кластерам (не привязана к env) ---
@router.get("/disk-usage")
async def disk_usage():
    """Заполненность дисков по всем кластерам VM. Подпись строки = имя из env-файла."""
    return await disk.usage()


# --- vmselect: чтение метрик (tenant — для мультитенантного VM) ----------------
@router.get("/{env}/query")
async def query(
    env: str,
    query: str = Query(..., alias="query"),
    time: Optional[str] = None,
    tenant: Optional[str] = None,
):
    """Мгновенный запрос PromQL/MetricsQL."""
    require_env(env)
    return _json(await client.query(env, query, time, config.resolve_tenant(env, tenant)))


@router.get("/{env}/query_range")
async def query_range(
    env: str,
    query: str = Query(..., alias="query"),
    start: str = Query(...),
    end: str = Query(...),
    step: str = Query(...),
    tenant: Optional[str] = None,
):
    """Запрос за интервал — данные для графика."""
    require_env(env)
    return _json(await client.query_range(env, query, start, end, step, config.resolve_tenant(env, tenant)))


@router.get("/{env}/labels")
async def labels(env: str, tenant: Optional[str] = None):
    """Имена лейблов (автодополнение)."""
    require_env(env)
    return _json(await client.labels(env, config.resolve_tenant(env, tenant)))


@router.get("/{env}/label_values")
async def label_values(
    env: str,
    label: str = Query(...),
    tenant: Optional[str] = None,
    limit: Optional[int] = None,
):
    """Значения лейбла (автодополнение). label=__name__ → имена метрик."""
    require_env(env)
    return _json(await client.label_values(env, label, config.resolve_tenant(env, tenant), limit))


@router.get("/{env}/tsdb")
async def tsdb(env: str, topn: int = 20, tenant: Optional[str] = None, refresh: bool = False):
    """Кардинальность (топ серий) — вкладка Cardinality. refresh=1 — мимо кэша."""
    require_env(env)
    return _json(await client.tsdb_status(env, topn, config.resolve_tenant(env, tenant), refresh))


# --- vmagent ------------------------------------------------------------------
@router.get("/{env}/targets")
async def targets(env: str, refresh: bool = False):
    """Цели скрейпа из vmagent. refresh=1 — мимо кэша."""
    require_env(env)
    return _json(await client.targets(env, refresh))


# --- vmalert ------------------------------------------------------------------
@router.get("/{env}/rules")
async def rules(env: str, refresh: bool = False):
    """Группы правил из vmalert. refresh=1 — мимо кэша."""
    require_env(env)
    return _json(await client.rules(env, refresh))


@router.get("/{env}/alerts")
async def alerts(env: str):
    """Активные алерты из vmalert."""
    require_env(env)
    return _json(await client.alerts(env))
