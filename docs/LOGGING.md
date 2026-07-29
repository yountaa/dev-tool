# Логи

Оба сервиса пишут **NDJSON в stdout**: одна строка — один JSON-объект. В файлы
не пишется ничего, сбор берёт на себя контейнерная инфраструктура.

Формат строки: **`ts` · `level` · `module` · `msg` · поля события**

```json
{"ts":"2026-07-29T13:59:26+00:00","level":"WARNING","module":"victoria","msg":"vm.node_failed","path":"/api/v1/query","used":"http://vmselect-2:8481","failed":["нет связи с http://vmselect-1:8481: ..."]}
```

---

## `module` — обязательное поле

**По `module` логи фильтруют.** Оно есть в каждой строке и отвечает на вопрос
«чей это лог»: `silences`, `victoria`, `alerts`, `access`, `share`, `app`.

Поле проставляется автоматически — берётся первый сегмент имени логгера до
точки. Модулю достаточно назвать свой логгер своим именем:

```python
import logging

# module в логе = victoria
log = logging.getLogger("victoria.client")
```

Остаток имени (`client`, `config`, `scheduler`) в строку не выносится: где
сработал код, видно из имени события, а лишнее поле только мешает читать.

Переопределить модуль можно явным полем `module=` — это нужно, когда строку
пишет общий код от имени модуля. Так делают два места:

- middleware ошибок HTTP в `main.py` — `module` берётся из первого сегмента пути
  (`/victoria/...` → `victoria`), поэтому ошибка запроса попадает в тот же
  фильтр, что и собственные логи модуля;
- `require_module()` в `access.py` — отказ доступа приписывается тому модулю, к
  которому не пустили.

## Как писать события

Только через `logging_setup.event()` — он кладёт **кастомные поля** отдельными
ключами NDJSON, а не склеивает их в текст:

```python
import logging

import logging_setup

log = logging.getLogger("disks.client")

logging_setup.event(
    log, "disk.scan_failed",
    level=logging.WARNING,          # по умолчанию INFO
    env=env, node=url, error=str(e),
    hint="узел пропущен, данные собраны с остальных",
)
```

Правила:

1. **Имя события — `<область>.<что произошло>`**, стабильное, латиницей. Оно
   попадает в `msg` и служит фильтром: `vm.node_failed`, `silence.apply_failed`.
2. **Данные — отдельными полями.** Строка `f"нода {url} упала"` не фильтруется и
   не агрегируется; `node=url` — фильтруется.
3. **`hint=` для проблем.** Одно предложение о том, что это значит и что будет
   дальше: «правило сохранено, шедулер доставит его позже». Читающий лог не
   обязан знать код.
4. **Уровень по адресату:** `INFO` — состояние сервиса, `WARNING` — требует
   внимания человека, `ERROR` — сломано.
5. **Секретов в логе нет.** Токены и cookie — только размером; токен git
   вычищается из текста ошибок перед записью.

Не логируется: успешные запросы, каждый шаг обработки, содержимое ответов
внешних систем.

## Что уже логируется

### Общее

| Событие | Модуль | Уровень | Когда |
| --- | --- | --- | --- |
| `auth.config` | access | INFO | старт: действующие `AUTH_*` и `RBAC_*` |
| `auth.me` | access | INFO | запрос `/access/me`: группы, доступные вкладки |
| `auth.no_username_header` | access | WARNING | `AUTH_ENABLED=true`, а имя не пришло |
| `auth.denied` | *модуль* | WARNING | RBAC не пустил: пользователь и его группы |
| `http.error` | *модуль* | WARNING/ERROR | ответ 4xx или 5xx |
| `share.save_failed` / `share.read_failed` | share | ERROR | хранилище коротких ссылок недоступно |

Поля `http.error`: `method`, `path`, `status`, `duration_ms`, `user`,
`auth_hdr` (дошёл ли заголовок с именем), `groups_n` (сколько групп пришло).

### silences

| Событие | Уровень | Смысл |
| --- | --- | --- |
| `silence.config` | INFO | старт: окружения, источники правил, хранилище, таймзона |
| `silence.orphan_rules_source` | WARNING | `rules_<env>` без парного `alert_<env>` — опечатка в имени |
| `silence.apply_failed` | WARNING | silence не доехал до Alertmanager; правило сохранено |
| `silence.schedule_applied` | INFO | шедулер поставил silence по расписанию |
| `silence.manual_recovered` | INFO | разовый silence отсутствовал в AM и поставлен заново |
| `am.node_failed` | WARNING | часть нод AM молчит, данные собраны с остальных |
| `am.all_nodes_failed` | ERROR | недоступны все ноды AM |
| `am.write_failed` | ERROR | запись не прошла ни на одну ноду — silence не поставлен |
| `scheduler.started` | INFO | старт: cron тика и очистки |
| `scheduler.tick_skipped` | INFO | тик выполняет другая нода (взят advisory-лок) |
| `scheduler.apply_failed` | WARNING | правило не доставлено, повтор следующим тиком |
| `storage.selected` | INFO | какой бэкенд хранилища выбран |
| `storage.pg_connected` / `storage.schema_ready` | INFO | подключение к Postgres и готовность схемы |
| `storage.pg_unavailable` | ERROR | БД недоступна |
| `storage.cleanup_done` | INFO | сколько записей вычищено по retention |
| `rule.created` / `rule.updated` / `rule.enabled` / `rule.disabled` / `rule.deleted` | INFO | действия с правилами |

### victoria

| Событие | Уровень | Смысл |
| --- | --- | --- |
| `vm.config` | INFO | старт: кластеры, тенанты, у кого есть Targets и Rules |
| `vm.cluster_conflict` | WARNING | кластер задан и простым, и с тенантами |
| `vm.orphan_component` | WARNING | `vmagent_`/`vmalert_` без парного `vm_` |
| `vm.node_failed` | WARNING | HA: часть нод недоступна, ответ взят со следующей |
| `vm.all_nodes_failed` | ERROR | недоступны все ноды компонента |

`vm.node_failed` существует именно потому, что при HA интерфейс продолжает
работать со второй ноды: без этой строки мёртвая первая нода не видна вовсе —
о ней узнают, когда упадут обе.

## Чужие логгеры

Логи uvicorn заворачиваются в общий поток. Приглушены до `WARNING`:

- **httpx / httpcore** — писали INFO-строку на каждый исходящий запрос
  (`HTTP Request: GET … "200 OK"`). У модуля-прокси это сотни строк в минуту без
  полезной информации: что запрашивали, видно в интерфейсе, а поломки логируются
  своими событиями с понятным текстом;
- **apscheduler** — на старте писал пять строк о постановке задач; то же самое
  говорит `scheduler.started` одной строкой и с полями.

Собственный access-лог uvicorn отключён: проблемные запросы пишет middleware.

## nginx фронта

Формат `authlog`, тоже NDJSON в stdout:

```json
{"ts":"2026-07-29T13:05:41+03:00","src":"front-nginx","remote":"10.0.0.1","method":"GET","path":"/victoria/prod/query","status":200,"upstream_status":"200","request_length":4821,"user":"ivanov","email":"…","fwd_user":"…","groups":"present"}
```

Значение групп не пишется — оно бывает огромным; фиксируется только наличие
(`present` / `missing`). Cookie не пишется вовсе, общий вес запроса виден в
`request_length`.

## Диагностика

**Личность теряется.** Сопоставляются два лога:

| `authlog` | Лог бэкенда | Вывод |
| --- | --- | --- |
| `user` заполнен | `user: local` | потеря на хопе nginx → бэкенд |
| `user` пустой | — | потеря раньше: oauth2-proxy или внешний nginx |

**Запроса нет ни в одном логе.** Вероятно, его отверг uvicorn до приложения из-за
размера заголовков — в его логе будет `Invalid HTTP request received`. Лимиты
описаны в [NGINX.md](NGINX.md).

**Сервис ведёт себя не по конфигурации.** Строки `*.config` на старте показывают,
что реально прочитано из окружения: вкладки, окружения, хранилище, RBAC.

## Примеры фильтров

```bash
# всё по одному модулю — и его события, и ошибки его запросов
kubectl logs deploy/dev-tool-backend | jq 'select(.module=="victoria")'

# только проблемы
kubectl logs deploy/dev-tool-backend | jq 'select(.level!="INFO")'

# конкретное событие
kubectl logs deploy/dev-tool-backend | jq 'select(.msg=="vm.node_failed")'

# конфигурация, прочитанная на старте
kubectl logs deploy/dev-tool-backend | jq 'select(.msg|endswith(".config"))'

# запросы, дошедшие без имени пользователя
kubectl logs deploy/dev-tool-backend | jq 'select(.auth_hdr==false)'
```
