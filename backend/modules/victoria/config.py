"""Настройки модуля Victoria Metrics. Всё из окружения — под каждый стенд не правим код.

Каждый КЛАСТЕР (окружение) описывается переменными окружения:

    vm_<env>=http://vmselect:8481/select/0/prometheus   # ОБЯЗАТЕЛЕН: чтение метрик
    vmagent_<env>=http://vmagent:8429                    # опц.: вкладка Targets
    vmalert_<env>=http://vmalert:8880                    # опц.: вкладка Rules/Alerts

- Имя <env> после префикса = имя вкладки-кластера (в нижнем регистре).
- Значение — ПОЛНЫЙ адрес компонента. Несколько нод (HA) — через запятую: читаем
  с первой живой.
- vm_<env> обязателен: это Prometheus-совместимый адрес vmselect (к нему добавляем
  /api/v1/query и т.п.). Обычно оканчивается на /select/0/prometheus.
- Нет vmagent_<env> → у кластера нет вкладки Targets. Нет vmalert_<env> → нет
  вкладки Rules/Alerts. Это сценарий «кластер только под хранение метрик, без
  vm-agent и vm-alert»: задаёшь только vm_<env>.

Мультитенантность (данные кластера лежат в разных тенантах VM) — ты сам пишешь
ПОЛНЫЙ путь на каждый тенант, а имя тенанта задаёшь через двойное подчёркивание:

    vm_<cluster>__<подпись-тенанта>=<полный URL с нужным accountID в пути>

Например:
    vm_prod__platform=http://vmselect:8481/select/0/prometheus
    vm_prod__billing=http://vmselect:8481/select/5:2/prometheus
    vm_prod__analytics=http://vmselect:8481/select/7/prometheus

Тогда кластер `prod` — ОДНА вкладка + селектор тенанта из трёх подписей (platform/
billing/analytics). Подпись читаемая (для селектора), а accountID стоит прямо в URL,
который ты задал. Тенант влияет только на ЧТЕНИЕ метрик (Query, Cardinality);
vmagent_<cluster>/vmalert_<cluster> — общие на кластер, тенант их не трогает.

Нет `__` → обычный одно-тенантный кластер (или просто Prometheus), селектора нет.

ВНИМАНИЕ про префиксы: `vm_` не пересекается с `vmagent_`/`vmalert_`, т.к. после `vm`
сразу `_` только у vmselect (у остальных — буква). Разделитель кластер/тенант —
именно ДВОЙНОЙ `__` (одиночные `_` внутри подписи тенанта допустимы: делим по первому
`__`, поэтому `vm_prod__team_a` = кластер prod, тенант team_a).
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

load_dotenv()  # подхватит .env рядом, если есть

log = logging.getLogger("victoria.config")


def _urls(value: str) -> list[str]:
    """Значение переменной → список URL (HA-ноды через запятую)."""
    return [u.strip().rstrip("/") for u in value.split(",") if u.strip()]


# Простые кластеры:      cluster -> ноды vmselect
VMSELECT: dict[str, list[str]] = {}
# Мультитенантные:       cluster -> {подпись_тенанта: ноды vmselect}
TENANTS: dict[str, dict[str, list[str]]] = {}
# Компоненты (на кластер, тенант не трогает)
VMAGENT: dict[str, list[str]] = {}    # vmagent_<env>  — targets (опц.)
VMALERT: dict[str, list[str]] = {}    # vmalert_<env>  — rules/alerts (опц.)

for _key, _value in os.environ.items():
    _low = _key.lower()
    if _low.startswith("vmagent_"):
        VMAGENT[_key[len("vmagent_"):].lower()] = _urls(_value)
    elif _low.startswith("vmalert_"):
        VMALERT[_key[len("vmalert_"):].lower()] = _urls(_value)
    elif _low.startswith("vm_"):
        _rest = _key[len("vm_"):].lower()
        if "__" in _rest:                       # vm_<cluster>__<tenant>
            _cluster, _label = _rest.split("__", 1)
            TENANTS.setdefault(_cluster, {})[_label] = _urls(_value)
        else:                                    # vm_<cluster>
            VMSELECT[_rest] = _urls(_value)


# Кластер заявлен и как простой, и с тенантами — берём мультитенантный, простой убираем.
for _c in set(VMSELECT) & set(TENANTS):
    VMSELECT.pop(_c, None)
    log.warning(
        "vm_%s задан и как простой кластер, и с тенантами (vm_%s__...) — оставляю "
        "мультитенантный вариант; убери лишнюю переменную", _c, _c,
    )

# Кластеры = где есть vmselect (простой или тенантный).
ENVIRONMENTS: list[str] = sorted(set(VMSELECT) | set(TENANTS))

# Предупредим, если vmagent/vmalert заданы для несуществующего кластера (опечатка).
for _name in list(VMAGENT) + list(VMALERT):
    if _name not in VMSELECT and _name not in TENANTS:
        log.warning(
            "vmagent_/vmalert_%s задан, но кластера vm_%s нет — проверь имя; "
            "компонент не подключится", _name, _name,
        )


def known_env(env: str) -> bool:
    return env in VMSELECT or env in TENANTS


def tenant_labels(env: str) -> list[str]:
    """Подписи тенантов кластера (отсортированы). Пусто → кластер одно-тенантный."""
    return sorted(TENANTS.get(env, {}).keys())


def resolve_tenant(env: str, tenant: str) -> str:
    """Валидная подпись тенанта: сам tenant, если он есть, иначе первый по алфавиту.
    None — если у кластера тенантов нет (обычный одно-тенантный)."""
    labels = tenant_labels(env)
    if not labels:
        return None
    if tenant and tenant in labels:
        return tenant
    return labels[0]
