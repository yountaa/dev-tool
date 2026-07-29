# Хранилище данных

У приложения два режима хранения, переключаются одной переменной
`STORAGE_BACKEND`. Внешних миграций нет: приложение само создаёт нужные таблицы
при старте (`CREATE TABLE IF NOT EXISTS`).

```
STORAGE_BACKEND=local      → файлы на диске. Одна нода.
STORAGE_BACKEND=postgres   → общая БД. Сколько угодно нод.
```

| Что хранится | local | postgres |
| --- | --- | --- |
| Правила silences | файлы `<env>/<kind>/<id>.json` | таблица `configs` |
| Журнал изменений | `<env>/history/history.ndjson` | таблица `history` |
| Удалённые правила | папка `<env>/old/…` | `configs.deleted_at` (мягкое удаление) |
| Короткие ссылки | JSON-файл `SHORT_LINKS_FILE` | таблица `short_links` |
| Метаданные alerts | — (только Postgres) | `alerts_history`, `alerts_meta`, `alerts_configs` |

Для нескольких нод приложения годится только `postgres`: в режиме `local` каждая
нода видит свой диск.

Модуль **alerts** ходит в Postgres независимо от `STORAGE_BACKEND`: ему хватает
заполненных `PG_*`. Если Postgres не настроен или недоступен, модуль продолжает
работать — просто без истории и без мгновенной гидрации UI.

---

## Режим local — файлы на диске

Всё хранилище — обычное дерево каталогов внутри `GIT_LOCAL_DIR` (имя переменной
историческое, отдельного репозитория режим не требует):

```
<GIT_LOCAL_DIR>/
    <env>/<kind>/<id>.json        правила
    <env>/history/history.ndjson  журнал: по строке-JSON на событие
    <env>/old/<kind>/<id>.json    мягко удалённые правила
```

- удаление правила — перенос файла в `old/`: из интерфейса пропадает, история
  сохраняется. `list_configs` читает только `<env>/<kind>`, папку `old/` не
  показывает;
- журнал пишется построчно, что делает его устойчивым к обрыву записи;
- очистка (`CLEANUP_CRON`) подрезает `history.ndjson` и удаляет файлы из `old/`
  старше срока хранения;
- каталог обязан лежать на постоянном томе: содержимое внутри образа теряется
  при пересоздании контейнера.

Атрибуция «кто что сделал» пишется полем `user` в журнале.

## Режим postgres — общая БД

Нужен, когда нод приложения больше одной: все читают и пишут одну базу.

- **подключение**: `PG_DSN` целиком либо части `PG_HOST/PG_PORT/PG_DB/PG_USER/
  PG_PASSWORD`. `PG_DSN` имеет приоритет;
- **пул соединений** — `ThreadedConnectionPool` из psycopg2, один на процесс;
- **прав достаточно на `CREATE TABLE`** — схему приложение создаёт само;
- **шедулер на нескольких нодах** координируется advisory-локом
  (`pg_try_advisory_lock` с общим ключом): тикает ровно одна нода. Лок держится
  на отдельном долгоживущем соединении, потому что advisory-lock сессионный.

### Таблицы

`configs` — правила silences.

| Поле | Тип | Смысл |
| --- | --- | --- |
| `env`, `kind`, `id` | text | составной первичный ключ |
| `created_at` | timestamptz | когда создано |
| `payload` | jsonb | само правило |
| `enabled` | boolean | включено ли |
| `am_id` | text | id silence в Alertmanager |
| `deleted_at` | timestamptz | мягкое удаление (аналог папки `old/`) |

`history` — журнал изменений: `id`, `env`, `ts`, `user`, `action`, `kind`,
`name`, `before` (jsonb), `after` (jsonb). Индекс `history_env_ts (env, ts DESC)`.

`short_links` — короткие ссылки на вид: `id` (первые 8 символов sha256 от пути),
`path`, `created_at`.

`alerts_history` — журнал модуля alerts: `id`, `ts`, `user`, `action`, `name`,
`alert_key`, `before`, `after`. Индекс по `ts DESC`.

`alerts_meta` — служебные значения модуля alerts: `key`, `value` (jsonb),
`updated_at`. Здесь лежит heartbeat backend-workflow.

`alerts_configs` — зеркало конфигов алертов из n8n для мгновенной отрисовки UI:
`alert_key`, `row_id`, `name`, `enabled`, `type`, `interval_minutes`, `config`,
`state`, `updated_at`. **Source of truth остаётся n8n**, это кэш.

### Авто-очистка

Раз в `CLEANUP_CRON` (по умолчанию 1-го числа в 03:00) приложение подчищает:

- журнал истории старше `HISTORY_RETENTION_DAYS`;
- мягко удалённые правила старше `DELETED_RULES_RETENTION_DAYS`.

Очистка работает в обоих режимах — с таблицами либо с файлами.

---

## Postgres в новом модуле

Шаги для модуля, которому нужны собственные данные.

### 1. Креды — из общего конфига

Свой блок переменных не заводится: подключение у всего приложения одно.

```python
from modules.silences import config as storage_config

dsn = storage_config.pg_dsn()   # PG_DSN целиком либо PG_HOST/PORT/DB/USER/PASSWORD
```

Это зафиксированное исключение из правила «модули независимы»: параметры
хранилища живут в `modules/silences/config.py`, оттуда их берут `share.py` и
модуль alerts.

### 2. Имена таблиц — с префиксом модуля

`disks_history`, `disks_meta`, `disks_configs`. Так по имени видно владельца, и
таблицы разных модулей не сталкиваются. Исторические исключения — `configs` и
`history` у silences, `short_links` у коротких ссылок.

**Чужие таблицы не читаются напрямую**, даже если это быстрее: модули
взаимодействуют через API. Иначе изменение схемы в одном модуле ломает другой.

### 3. Шаблон `storage.py`

```python
"""Хранилище модуля disks. Отдельные таблицы disks_* — чужих не трогаем.

Postgres недоступен или не настроен — методы тихо возвращают пустое, модуль
продолжает работать без истории.
"""
from __future__ import annotations

import logging
import threading

from psycopg2.pool import ThreadedConnectionPool

import logging_setup
from modules.silences import config as storage_config

log = logging.getLogger("disks.storage")   # module в логе = disks

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()
_initialized = False
_unavailable = False   # БД нет или упала — не долбим её на каждый запрос


def _get_pool() -> ThreadedConnectionPool | None:
    global _pool
    if _unavailable:
        return None
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadedConnectionPool(1, 5, dsn=storage_config.pg_dsn())
    return _pool


def ensure_schema() -> bool:
    """Создать таблицы модуля (идемпотентно). False — БД недоступна."""
    global _initialized, _unavailable
    if _initialized:
        return True
    try:
        pool = _get_pool()
        if pool is None:
            return False
        con = pool.getconn()
        try:
            con.autocommit = True
            with con.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS disks_history (
                        id         bigserial   PRIMARY KEY,
                        ts         timestamptz NOT NULL,
                        "user"     text        NOT NULL,
                        env        text        NOT NULL DEFAULT '',
                        payload    jsonb
                    );
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS disks_history_ts ON disks_history (ts DESC);"
                )
        finally:
            pool.putconn(con)
        _initialized = True
        logging_setup.event(log, "storage.schema_ready", tables=["disks_history"])
        return True
    except Exception as e:
        _unavailable = True
        logging_setup.event(
            log, "storage.pg_unavailable", level=logging.ERROR,
            error=str(e), hint="модуль работает без истории",
        )
        return False
```

Каждый публичный метод начинается с `if not ensure_schema(): return []` — тогда
недоступная БД деградирует модуль, а не ломает его.

### 4. Вызов при старте

В `lifespan` в `main.py`, рядом с остальными:

```python
disks_storage.ensure_schema()
```

Не при импорте модуля: до `logging_setup.setup()` строка уйдёт мимо
JSON-форматтера, без полей и без `module`.

### 5. Пул соединений

Один `ThreadedConnectionPool` на процесс, создаётся лениво под локом. Новое
соединение на каждый запрос не открывается — кроме редких операций, где это
осознанно (пример — `share.py`: поделиться ссылкой случается несколько раз в
день, и модуль не тянет пул ради этого).

**Advisory-лок держится на отдельном долгоживущем соединении**, а не на взятом
из пула: он сессионный и снимется, как только соединение вернётся в пул.

## Изменение схемы

1. `CREATE TABLE IF NOT EXISTS` или `ALTER TABLE … ADD COLUMN IF NOT EXISTS`
   дописывается в `ensure_schema()` того модуля, которому принадлежит таблица.
   Отдельная система миграций в проекте не используется.
2. Схема накатывается **идемпотентно** и не мешает уже работающим нодам: во
   время выката рядом продолжает работать предыдущая версия кода.
3. Новое поле добавляется **с `DEFAULT`**; `NOT NULL` без значения по умолчанию
   ломает выкат на непустой таблице.
4. Колонки не переименовываются и не удаляются в том же релизе, где меняется
   читающий их код: старая версия приложения ещё жива.
5. Данные, которые должны работать и в режиме `local`, реализуются в обоих
   вариантах — как в `share.py` (Postgres либо JSON-файл).

## Что не кладём в БД

- секреты и токены — они приходят из окружения;
- значения cookie и сессий;
- то, что можно посчитать на лету из внешней системы (метрики, targets, rules) —
  для них есть кэш в памяти с TTL, см. `modules/victoria/client.py`.

## Диагностика

```bash
# какие таблицы уже есть
psql "$PG_DSN" -c '\dt'
# кто последним что менял
psql "$PG_DSN" -c 'SELECT ts, "user", action, kind, name FROM history ORDER BY ts DESC LIMIT 20;'
# состояние алертов-зеркала
psql "$PG_DSN" -c 'SELECT alert_key, name, enabled, updated_at FROM alerts_configs ORDER BY updated_at DESC LIMIT 20;'
```

В логах приложения при старте видно, какой режим выбран и поднялась ли схема:
события `alerts/postgres: схема готова`, ошибки подключения — уровнем `error`.
Подробнее — [LOGGING.md](LOGGING.md).
