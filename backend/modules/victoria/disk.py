"""Под-вкладка «Диск»: заполненность дисков по ВСЕМ кластерам VM одним списком.

Живёт внутри модуля victoria (под-вкладка справа от TSDB Status), но в отличие от
остальных под-вкладок не привязана к выбранному кластеру — пробегает по ВСЕМ
кластерам из окружения (vm_<env>). Значения показываем В РАЗБИВКЕ по лейблу
`group` (группы vmstorage): по каждой группе кластера — занято, свободно и прогноз
заполнения. Подпись строки = имя кластера из env-файла (vm_<env> → <env>) + группа.

Считаем сами (self-метрики VM, низкая кардинальность), всё по группам:
    занято  = sum by(group) (vm_data_size_bytes)
    свободно = sum by(group) (vm_free_disk_space_bytes)
    всего   = занято + свободно            (диск ≈ данные VM + свободное место)
    процент = занято / всего * 100

Кластер недоступен — не роняем всю вкладку: у этой строки ставим error, остальные
показываем. Метрики без лейбла group (обычный Prometheus, один vmstorage) попадают
в группу "" — фронт показывает её как «—». Мультитенантный кластер: self-метрики
хранилища общие на кластер, поэтому тенант не важен — читаем через первый доступный
(tenant=None).
"""
from __future__ import annotations

import asyncio
import json

from . import client, config

# Занято данными и свободно на дисках — по self-метрикам vmstorage, по группам.
USED_EXPR = "sum by(group) (vm_data_size_bytes)"
FREE_EXPR = "sum by(group) (vm_free_disk_space_bytes)"

# ETA — примерное число ДНЕЙ до заполнения диска, ПО КАЖДОЙ группе vmstorage.
# Считает сам VM: свободное место (за вычетом резерва) делим на скорость роста
# данных, где рост учитывает сжатие/дедуп (байт на строку) и отдельно рост индекса
# (new timeseries). Усредняем за 10 минут, чтобы одиночный всплеск не искажал
# прогноз. min by(group) даёт прогноз по каждой группе — его и показываем строкой,
# не сворачивая кластер в одно число.
ETA_DAYS_EXPR = """avg_over_time(
  (
    min by(group) (
      (vm_free_disk_space_bytes - vm_free_disk_space_limit_bytes)
        / ignoring(path)
      (
        (rate(vm_rows_added_to_storage_total[1d]) - sum without(type)(rate(vm_deduplicated_samples_total[1d])))
          * (sum without(type)(vm_data_size_bytes{type!~"indexdb.*"}) / sum without(type)(vm_rows{type!~"indexdb.*"}))
        + rate(vm_new_timeseries_created_total[1d])
          * (sum without(type)(vm_data_size_bytes{type="indexdb/file"}) / sum without(type)(vm_rows{type="indexdb/file"}))
      )
    ) / 86400
  )[10m:]
)"""


def _by_group(raw: bytes) -> dict:
    """Из ответа vmselect на `... by(group)` — словарь {группа: число}.

    Серии без лейбла group (обычный Prometheus) ложатся в группу ""."""
    data = json.loads(raw)
    out: dict = {}
    for item in (data.get("data") or {}).get("result") or []:
        value = item.get("value")  # [timestamp, "число"]
        if not value or len(value) < 2:
            continue
        try:
            v = float(value[1])
        except (TypeError, ValueError):
            continue
        out[(item.get("metric") or {}).get("group", "")] = v
    return out


async def _one(env: str) -> list:
    """Строки одного кластера: по строке на каждую группу vmstorage (+ ETA)."""
    # return_exceptions: ETA-метрик может не быть (обычный Prometheus, урезанный VM) —
    # из-за них строки не роняем, просто не покажем прогноз.
    used_raw, free_raw, eta_raw = await asyncio.gather(
        client.query(env, USED_EXPR),
        client.query(env, FREE_EXPR),
        client.query(env, ETA_DAYS_EXPR),
        return_exceptions=True,
    )
    # И used, и free не пришли — кластер недоступен: показываем ошибку строкой.
    if isinstance(used_raw, Exception) and isinstance(free_raw, Exception):
        return [{"env": env, "error": str(used_raw)}]
    used = _by_group(used_raw) if not isinstance(used_raw, Exception) else {}
    free = _by_group(free_raw) if not isinstance(free_raw, Exception) else {}
    eta = _by_group(eta_raw) if not isinstance(eta_raw, Exception) else {}
    groups = sorted(set(used) | set(free))
    if not groups:
        return [{"env": env, "error": "нет self-метрик VM (vm_data_size_bytes/vm_free_disk_space_bytes)"}]
    rows = []
    for g in groups:
        u = used.get(g, 0.0)
        f = free.get(g, 0.0)
        total = u + f
        percent = (u / total * 100) if total > 0 else 0.0
        rows.append({
            "env": env, "group": g,
            "used": u, "free": f, "total": total, "percent": percent,
            "eta_days": eta.get(g),
        })
    return rows


async def usage() -> list:
    """Заполненность дисков по всем кластерам VM, по строке на группу vmstorage."""
    per_env = await asyncio.gather(*(_one(env) for env in config.ENVIRONMENTS))
    return [row for rows in per_env for row in rows]
