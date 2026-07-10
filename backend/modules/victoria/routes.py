"""Роутер модуля Victoria Metrics: одна вкладка приложения.

Мы прокси к кластерам VM. Каждое окружение (кластер) = vm_<env> в конфиге; у него
опционально есть vmagent (targets) и vmalert (rules/alerts). Все роуты закрыты
проверкой доступа require_module("victoria") — RBAC уровня приложения.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from access import require_module

from . import client, config
from .deps import require_env

# Вешаем RBAC на весь роутер: нет доступа к модулю victoria → 403 на любой роут.
router = APIRouter(
    prefix="/victoria",
    tags=["victoria"],
    dependencies=[Depends(require_module("victoria"))],
)


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
    return await client.query(env, query, time, config.resolve_tenant(env, tenant))


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
    return await client.query_range(env, query, start, end, step, config.resolve_tenant(env, tenant))


@router.get("/{env}/labels")
async def labels(env: str, tenant: Optional[str] = None):
    """Имена лейблов (автодополнение)."""
    require_env(env)
    return await client.labels(env, config.resolve_tenant(env, tenant))


@router.get("/{env}/label_values")
async def label_values(env: str, label: str = Query(...), tenant: Optional[str] = None):
    """Значения лейбла (автодополнение). label=__name__ → имена метрик."""
    require_env(env)
    return await client.label_values(env, label, config.resolve_tenant(env, tenant))


@router.get("/{env}/tsdb")
async def tsdb(env: str, topn: int = 20, tenant: Optional[str] = None):
    """Кардинальность (топ серий) — вкладка Cardinality."""
    require_env(env)
    return await client.tsdb_status(env, topn, config.resolve_tenant(env, tenant))


# --- vmagent ------------------------------------------------------------------
@router.get("/{env}/targets")
async def targets(env: str):
    """Цели скрейпа из vmagent."""
    require_env(env)
    return await client.targets(env)


# --- vmalert ------------------------------------------------------------------
@router.get("/{env}/rules")
async def rules(env: str):
    """Группы правил из vmalert."""
    require_env(env)
    return await client.rules(env)


@router.get("/{env}/alerts")
async def alerts(env: str):
    """Активные алерты из vmalert."""
    require_env(env)
    return await client.alerts(env)
