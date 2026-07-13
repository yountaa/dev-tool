"""Под-вкладка «Диск»: заполненность дисков по ВСЕМ кластерам VM одним списком.

Живёт внутри модуля victoria (под-вкладка справа от TSDB Status), но в отличие от
остальных под-вкладок не привязана к выбранному кластеру — пробегает по ВСЕМ
кластерам из окружения (vm_<env>) и по каждому спрашивает у его vmselect два числа:
сколько занято данными и сколько свободно на дисках vmstorage. Подпись строки = ИМЯ
кластера из env-файла (vm_<env> → <env>), а НЕ лейбл `group` у метрики.

Считаем сами (self-метрики VM, низкая кардинальность):
    занято  = sum(vm_data_size_bytes)
    свободно = sum(vm_free_disk_space_bytes)
    всего   = занято + свободно            (диск ≈ данные VM + свободное место)
    процент = занято / всего * 100

Кластер недоступен — не роняем всю вкладку: у этой строки ставим error, остальные
показываем. Мультитенантный кластер: self-метрики хранилища общие на кластер,
поэтому тенант не важен — читаем через первый доступный (tenant=None).
"""
from __future__ import annotations

import asyncio
import json

from . import client, config

# Занято данными и свободно на дисках — по self-метрикам vmstorage, суммарно на кластер.
USED_EXPR = "sum(vm_data_size_bytes)"
FREE_EXPR = "sum(vm_free_disk_space_bytes)"

# ETA — примерное число ДНЕЙ до заполнения диска. Считает сам VM: свободное место
# (за вычетом резерва) делим на скорость роста данных, где рост учитывает сжатие/дедуп
# (байт на строку) и отдельно рост индекса (new timeseries). Усредняем за 10 минут,
# чтобы одиночный всплеск не искажал прогноз. min by(group) даёт прогноз по каждой
# группе vmstorage, внешний min(...) сворачивает кластер в ОДНО число — по группе,
# которая заполнится РАНЬШЕ всех (она и лимитирует кластер). Фильтр «< 14» из примера
# убран — здесь показываем само значение, а не только «горит».
ETA_DAYS_EXPR = """min(avg_over_time(
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
))"""


def _scalar(raw: bytes) -> float | None:
    """Из ответа vmselect на `sum(...)` достаём одно число (или None, если пусто)."""
    data = json.loads(raw)
    result = (data.get("data") or {}).get("result") or []
    if not result:
        return None
    value = result[0].get("value")  # [timestamp, "число"]
    if not value or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


async def _one(env: str) -> dict:
    """Заполненность одного кластера + ETA (примерное время до заполнения)."""
    # return_exceptions: ETA-метрик может не быть (обычный Prometheus, урезанный VM) —
    # из-за них строку не роняем, просто не покажем прогноз.
    used_raw, free_raw, eta_raw = await asyncio.gather(
        client.query(env, USED_EXPR),
        client.query(env, FREE_EXPR),
        client.query(env, ETA_DAYS_EXPR),
        return_exceptions=True,
    )
    # И used, и free не пришли — кластер недоступен: показываем ошибку строкой.
    if isinstance(used_raw, Exception) and isinstance(free_raw, Exception):
        return {"env": env, "error": str(used_raw)}
    used = _scalar(used_raw) if not isinstance(used_raw, Exception) else None
    free = _scalar(free_raw) if not isinstance(free_raw, Exception) else None
    eta_days = _scalar(eta_raw) if not isinstance(eta_raw, Exception) else None
    if used is None and free is None:
        return {"env": env, "error": "нет self-метрик VM (vm_data_size_bytes/vm_free_disk_space_bytes)"}
    used = used or 0.0
    free = free or 0.0
    total = used + free
    percent = (used / total * 100) if total > 0 else 0.0
    return {"env": env, "used": used, "free": free, "total": total, "percent": percent, "eta_days": eta_days}


async def usage() -> list[dict]:
    """Заполненность дисков по всем кластерам VM. Подпись строки = имя из env-файла."""
    rows = await asyncio.gather(*(_one(env) for env in config.ENVIRONMENTS))
    return list(rows)
