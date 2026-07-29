# Добавление модуля

Модуль — это одна вкладка приложения и один роутер бэкенда. Ниже полный путь от
пустой папки до работающей вкладки с правами, логами и своими данными. Пример по
тексту — модуль `disks`.

Правки в других модулях не требуются: затрагиваются только новые файлы плюс
несколько строк в общих (`main.py`, `access.py`, `registry.js`, `nginx.conf`,
`vite.config.js`).

| Шаг | Что делаем |
| --- | --- |
| [1](#1-бэкенд-роутер) | Роутер, конфигурация из окружения, подключение в `main.py` |
| [2](#2-логи-модуля) | Логи: `module` для фильтра, события с полями |
| [3](#3-данные-модуля-postgres) | Свои таблицы в Postgres (если нужны) |
| [4](#4-фронтенд-вкладка) | Дескриптор, компонент, `api.js`, реестр |
| [5](#5-маршрутизация) | `nginx.conf` **и** `vite.config.js` |
| [6](#6-цвет-пространство-вкладки) | Палитра вкладки в `theme.css` |
| [7](#7-конфигурация-и-права) | `.env.example` и RBAC |
| [8](#8-проверка) | Чек-лист |

---

## 1. Бэкенд: роутер

```
backend/modules/disks/
    __init__.py        пустой
    config.py          переменные окружения модуля
    client.py          HTTP-клиент к внешней системе (если нужен)
    deps.py            проверки-зависимости
    storage.py         работа со своими таблицами (если нужны)
    routes.py          роутер
```

### config.py

Переменные читаются при импорте. Набор окружений задаётся **префиксом имени**,
чтобы добавление стенда не требовало релиза:

```python
"""Настройки модуля disks. Всё из окружения — код под стенд не правится."""
import logging
import os

from dotenv import load_dotenv

import logging_setup

load_dotenv()

# module в логе = disks (первый сегмент имени логгера)
log = logging.getLogger("disks.config")


def _urls(value: str) -> list[str]:
    """Значение переменной → список URL. Несколько нод (HA) — через запятую."""
    return [u.strip().rstrip("/") for u in value.split(",") if u.strip()]


# disks_<env>=<url[,url2...]> — каждая переменная даёт отдельное окружение.
SOURCES: dict[str, list[str]] = {}
for _key, _value in os.environ.items():
    if _key.lower().startswith("disks_"):
        SOURCES[_key[len("disks_"):].lower()] = _urls(_value)


def known_env(env: str) -> bool:
    return env in SOURCES


def log_config() -> None:
    """Что прочитано из окружения — одной строкой на старте (зовётся из main.py)."""
    logging_setup.event(log, "disks.config", envs=sorted(SOURCES))
```

Префикс выбирается так, чтобы не пересекаться с существующими: `alert_`,
`rules_`, `vm_`, `vmagent_`, `vmalert_`, `access_`, `disks_`.

### routes.py

RBAC вешается на **весь роутер** — тогда ни один роут не останется открытым по
недосмотру:

```python
from fastapi import APIRouter, Depends

from access import require_module

from . import client, config

router = APIRouter(
    prefix="/disks",
    tags=["disks"],
    dependencies=[Depends(require_module("disks"))],
)


@router.get("/environments")
def environments():
    """Список окружений для вкладок. Реальные URL наружу не отдаются."""
    return [{"name": name} for name in sorted(config.SOURCES)]
```

Ответы внешних систем объёмом от сотен килобайт передаются сырыми байтами, без
разбора JSON:

```python
from fastapi import Response

def _json(raw: bytes) -> Response:
    return Response(content=raw, media_type="application/json")
```

### Подключение в main.py

```python
from modules.disks import config as disks_config
from modules.disks.routes import router as disks_router
...
app.include_router(disks_router)
```

В `lifespan` добавляются вызовы старта — конфигурация в лог и, если нужно, схема
БД:

```python
disks_config.log_config()
disks_storage.ensure_schema()
```

**Почему в `lifespan`, а не при импорте:** файлы модулей выполняются до
`logging_setup.setup()`, и строка, написанная при импорте, уйдёт мимо
JSON-форматтера — без полей и без `module`.

### Регистрация в RBAC

В `backend/access.py` id модуля добавляется в `KNOWN_MODULES`:

```python
KNOWN_MODULES = ["silences", "victoria", "alerts", "disks"]
```

**Без этой строки `/access/me` не вернёт модуль, и вкладка не появится при
включённом RBAC.** Самая частая причина «вкладки нет, а код есть».

## 2. Логи модуля

Логи пишутся в NDJSON в stdout. Полные правила — [LOGGING.md](LOGGING.md), для
модуля достаточно трёх вещей.

**Логгер называется именем модуля** — из него автоматически берётся поле
`module`, по которому логи фильтруют:

```python
import logging

log = logging.getLogger("disks.client")   # module в логе = disks
```

**События пишутся через `logging_setup.event()`** с кастомными полями — они
попадают в NDJSON отдельными ключами, а не склеиваются в текст:

```python
import logging_setup

logging_setup.event(
    log, "disk.node_failed",
    level=logging.WARNING,
    env=env, node=url, error=str(e),
    hint="узел пропущен, данные собраны с остальных",
)
```

**Логируется то, чего не видно из интерфейса:**

| Логировать | Не логировать |
| --- | --- |
| прочитанную конфигурацию на старте | каждый успешный запрос |
| отказ внешней системы, с адресом узла | каждый шаг обработки |
| частичную деградацию при HA (упала нода, но ответ есть) | содержимое ответов |
| несогласованную конфигурацию (опечатка в имени переменной) | секреты, cookie, токены |

Частичная деградация — самое важное: при HA интерфейс продолжает работать со
второй ноды, и без строки в логе мёртвая первая нода не видна вовсе.

Ошибки HTTP-запросов модуля логировать не нужно — их пишет общий middleware в
`main.py`, проставляя `module` по пути запроса.

## 3. Данные модуля: Postgres

Нужны, только если модулю есть что хранить. Полный шаблон `storage.py`, правила
именования и подводные камни — [STORAGE.md](STORAGE.md#postgres-в-новом-модуле).
Коротко:

1. **Креды берутся из общего конфига**, свой блок переменных не заводится:

   ```python
   from modules.silences import config as storage_config
   dsn = storage_config.pg_dsn()   # PG_DSN либо PG_HOST/PORT/DB/USER/PASSWORD
   ```

2. **Таблицы называются с префиксом модуля**: `disks_history`, `disks_meta`.
   Чужие таблицы не читаются напрямую — только через API их модуля.

3. **Схема создаётся самим модулем** в `ensure_schema()` через
   `CREATE TABLE IF NOT EXISTS`, вызывается из `lifespan`. Отдельной системы
   миграций в проекте нет.

4. **Недоступная БД не роняет модуль**: `ensure_schema()` возвращает `False`,
   методы отдают пустой результат, в лог уходит одна ошибка — а не по строке на
   каждый запрос.

5. **Новое поле добавляется с `DEFAULT`**; `NOT NULL` без значения по умолчанию
   ломает выкат на непустой таблице.

## 4. Фронтенд: вкладка

```
frontend/src/modules/disks/
    index.js               дескриптор модуля
    DisksModule.vue        корневой компонент вкладки
    api.js                 все запросы модуля
    components/*.vue       внутренние компоненты
```

### index.js

```js
// Дескриптор модуля — то, что видит App и реестр.
// icon — значение атрибута d у <path>.
import DisksModule from './DisksModule.vue'

export default {
  id: 'disks',              // совпадает с KNOWN_MODULES и access_disks
  title: 'Disks',
  subtitle: 'заполненность дисков',
  icon: 'M3 6h18v12H3zM7 10h.01',
  component: DisksModule,
}
```

### api.js

Все запросы модуля собраны в одном файле и идут относительными путями:

```js
import { http } from '../../shared/api.js'

export const disksApi = {
  environments: () => http.get('/disks/environments'),
  usage: (env) => http.get(`/disks/${env}/usage`),
}
```

### Реестр

`frontend/src/modules/registry.js` — импорт и элемент массива:

```js
import disks from './disks/index.js'

export const modules = [silences, victoria, alerts, disks]
```

Порядок в массиве определяет порядок иконок в левом рельсе. Требования к
внешнему виду и поведению вкладки — [UI.md](UI.md).

## 5. Маршрутизация

Новый путь прописывается **в двух местах**, иначе он работает только в одном из
режимов.

`frontend/nginx.conf` — с пробросом личности и групп:

```nginx
location /disks/ {
    proxy_pass http://backend_pool;
    proxy_next_upstream error timeout http_502 http_503 http_504;
    proxy_set_header X-Forwarded-Preferred-Username $http_x_forwarded_preferred_username;
    proxy_set_header X-Forwarded-Email $http_x_forwarded_email;
    proxy_set_header X-Forwarded-User $http_x_forwarded_user;
    proxy_set_header X-Forwarded-Groups $http_x_forwarded_groups;
}
```

`frontend/vite.config.js` — та же запись для dev-сервера:

```js
'/disks': 'http://localhost:8000',
```

Внешний nginx при этом не меняется — он ничего не знает о путях приложения.
Подробности — [NGINX.md](NGINX.md).

## 6. Цвет-пространство вкладки

`App.vue` ставит на `<html>` атрибут `data-space="disks"`, а
`frontend/src/shared/theme.css` содержит блок переменных под это значение. Тема
указывается **явно**, иначе тёмный вариант протечёт в светлый:

```css
:root[data-theme="dark"][data-space="disks"]  { --accent: #4aa3df; … }
:root[data-theme="light"][data-space="disks"] { --accent: #1f6fa8; … }
```

Ничего в JavaScript менять не требуется. Полный набор переменных — [UI.md](UI.md).

## 7. Конфигурация и права

В [`.env.example`](../.env.example) добавляется блок модуля — с пояснением, на
что влияет каждая переменная, и примером значения:

```bash
# =============================================================================
#  МОДУЛЬ disks — вкладка «Disks»
# =============================================================================
disks_prod=http://exporter.prod.svc:9100
```

Права на вкладку задаются в блоке RBAC: `access_disks=team-sre`. Правила
выдачи — [RBAC.md](RBAC.md).

## 8. Проверка

- [ ] `GET /disks/environments` отвечает, а при `RBAC_ENABLED=true` без нужной
      группы возвращает 403;
- [ ] `GET /access/me` содержит `disks` в списке `modules`;
- [ ] вкладка появилась в рельсе, переключение перекрашивает палитру;
- [ ] путь работает и через nginx, и через dev-сервер;
- [ ] на старте в логе есть строка `disks.config` с `module: disks`;
- [ ] отказ внешней системы даёт понятное событие с `hint`, а не молчание;
- [ ] `cd frontend && npm run build` проходит;
- [ ] `cd backend && python -m unittest discover tests` проходит;
- [ ] модуль описан в [ARCHITECTURE.md](ARCHITECTURE.md), переменные — в
      `.env.example`.

## Частые ошибки

| Симптом | Причина |
| --- | --- |
| Вкладки нет, API отвечает | id не добавлен в `KNOWN_MODULES` |
| Вкладка есть, API отдаёт 404 в проде | не добавлен `location` в `nginx.conf` |
| Работает в проде, не работает при разработке | не добавлен путь в `vite.config.js` |
| Вкладка видна всем при включённом RBAC | не задана `access_<module>` — модуль без ограничений виден всем |
| Переменная окружения не читается | дефис в имени: docker отбрасывает такие переменные |
| Палитра вкладки протекла в светлую тему | в CSS не указан `data-theme` вместе с `data-space` |
| Строка лога без полей и без `module` | событие написано при импорте модуля, до `logging_setup.setup()` — переносится в `lifespan` |
| Логи модуля не находятся фильтром | логгер назван не именем модуля (`getLogger("client")` вместо `getLogger("disks.client")`) |
