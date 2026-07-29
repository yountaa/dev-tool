# dev-tool

Веб-инструмент для эксплуатации observability-контура. Одно приложение, несколько
независимых модулей — каждый занимает свою вкладку и отвечает за свой участок
работы.

| Модуль | Вкладка | Что делает |
| --- | --- | --- |
| **silences** | Silence Manager | Ставит silence в Alertmanager: разово по датам и повторяющимися окнами по расписанию. Хранит правила и историю изменений, шедулер дочиняет недостающие silence. |
| **victoria** | Victoria Metrics | Работа с кластерами VictoriaMetrics: графики PromQL/MetricsQL с автодополнением, цели скрейпа, правила алертинга, кардинальность и заполненность дисков. |
| **alerts** | Alert Constructor | Конструктор алертов поверх Elasticsearch-индексов. Конфигурации ведёт n8n, приложение хранит журнал изменений и статус выполнения. |

Доступ к вкладкам разграничивается по группам Keycloak.

---

## Из чего состоит

Два независимых сервиса. Собираются и выкатываются по отдельности.

| Сервис | Стек | Роль |
| --- | --- | --- |
| `backend/` | FastAPI, Python 3.12 | API всех модулей, работа с внешними системами и хранилищем |
| `frontend/` | Vue 3 + Vite, nginx | Интерфейс и проксирование API на бэкенд |

```
браузер → внешний nginx (TLS, oauth2-proxy) → frontend (nginx + SPA) → backend → внешние системы
```

Браузер обращается только к фронту: внутренний nginx отдаёт статику и проксирует
на бэкенд пути `/silences/`, `/victoria/`, `/alerts/`, `/access/`, `/share`,
`/s/`, `/health`, а `/webhook/` — в n8n. Открывать бэкенд наружу не требуется.

Архитектурный принцип: **1 модуль = 1 роутер на бэкенде = 1 вкладка во фронте**.
Новый модуль добавляется папкой в `backend/modules/` и `frontend/src/modules/`
без правок в остальных.

## Развёртывание

```bash
# бэкенд
docker build -t dev-tool-backend ./backend
docker run --env-file .env -p 8000:8000 dev-tool-backend

# фронтенд
docker build -t dev-tool-frontend ./frontend
docker run -e BACKEND_URL=http://backend:8000 -e N8N_URL=http://n8n:5678 -p 80:80 dev-tool-frontend
```

Бэкенду передаётся файл окружения целиком. Образу фронта нужны только
`FRONTEND_PORT`, `BACKEND_URL` и `N8N_URL`.

**Отказоустойчивость.** `BACKEND_URL` принимает список адресов через запятую —
nginx фронта балансирует между ними (`least_conn`) и временно исключает упавшую
ноду. Для нескольких нод бэкенда требуется `STORAGE_BACKEND=postgres`: шедулер
координируется advisory-локом и тикает ровно на одной ноде.

**Живость:** `GET /health` → `{"status": "ok"}`.

## Переменные окружения

Полный список с комментариями и примерами — [`.env.example`](.env.example).
Ниже сводка по группам.

### Связность

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `BACKEND_URL` | `http://backend:8000` | Куда nginx фронта проксирует API. Полный адрес; несколько нод — через запятую |
| `FRONTEND_PORT` | `80` | Порт, который слушает nginx фронта |
| `BACKEND_PORT` | `8000` | Порт публикации API |
| `PORT` | `8000` | Порт uvicorn внутри контейнера бэкенда |

### Хранилище

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `STORAGE_BACKEND` | `local` | `local` — файлы на диске, одна нода; `postgres` — общая БД, сколько угодно нод |
| `PG_DSN` | — | Строка подключения целиком; имеет приоритет над частями ниже |
| `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` / `PG_PASSWORD` | `localhost` / `5432` / `devtool` / — / — | Подключение по частям. Достаточно прав на `CREATE TABLE` — схему приложение создаёт само |
| `GIT_LOCAL_DIR` | `./.hub-repo` | Только при `STORAGE_BACKEND=local`: папка хранилища, выносится на постоянный том |
| `SHORT_LINKS_FILE` | `./data/short_links.json` | Только при `STORAGE_BACKEND=local`: файл коротких ссылок. При `postgres` не читается — ссылки идут в таблицу `short_links` |

Пример подключения к общей БД:

```bash
STORAGE_BACKEND=postgres
PG_DSN=postgresql://devtool:secret@pg-cluster:5432/devtool
```

### Авторизация и доступ

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `AUTH_ENABLED` | `false` | `true` — личность берётся из заголовка от oauth2-proxy |
| `AUTH_USER_HEADER` | `X-Forwarded-Preferred-Username` | Заголовок с именем пользователя |
| `AUTH_GROUPS_HEADER` | `X-Forwarded-Groups` | Заголовок со списком групп Keycloak |
| `AUTH_FALLBACK_USER` | `local` | Имя автора, когда авторизации нет |
| `RBAC_ENABLED` | `false` | `false` — все вкладки видны всем |
| `access_<module>` | — | Группы, которым видна вкладка. `*` — открыта всем вошедшим |
| `RBAC_ADMIN_GROUP` | — | Группы, которым видны все вкладки |

```bash
AUTH_ENABLED=true
RBAC_ENABLED=true
RBAC_ADMIN_GROUP=observability-admins,platform-admins
access_silences=team-oncall,team-sre
access_victoria=team-metrics
access_alerts=*
```

Имена групп сверяются точно — регистр и ведущий слэш значимы. RBAC действует
только вместе с `AUTH_ENABLED=true`: без входа групп нет, и доступ не режется.
Защита двойная — бэкенд отвечает 403 на API чужого модуля, фронт прячет вкладку.

### Модуль silences

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `alert_<env>` | — | **Обязательна минимум одна.** Alertmanager окружения; каждая переменная = вкладка. Ноды HA — через запятую |
| `rules_<env>` | — | Источник определений алертов: Prometheus или vmalert |
| `SILENCE_CRON` | `*/5 * * * *` | Как часто шедулер проверяет расписания |
| `SILENCE_TZ` | `Europe/Moscow` | Таймзона окон расписания и показа дат во всём интерфейсе |
| `CLEANUP_CRON` | `0 3 1 * *` | Расписание автоочистки |
| `HISTORY_RETENTION_DAYS` | `30` | Срок хранения журнала изменений |
| `DELETED_RULES_RETENTION_DAYS` | `30` | Срок хранения удалённых правил |

```bash
alert_prod=http://am-1.prod.svc:9093,http://am-2.prod.svc:9093
rules_prod=http://vmalert.prod.svc:8880
```

Без `alert_*` приложение не стартует.

### Модуль victoria

Имя `<env>` после префикса становится названием вкладки.

| Переменная | Обязательна | Что включает |
| --- | :---: | --- |
| `vm_<env>` | да | vmselect → под-вкладки **Graph** и **TSDB Status** |
| `vmagent_<env>` | нет | vmagent → под-вкладка **Targets** |
| `vmalert_<env>` | нет | vmalert → под-вкладка **Rules** |

```bash
vm_prod=http://vmselect-1.prod.svc:8481/select/0/prometheus,http://vmselect-2.prod.svc:8481/select/0/prometheus
vmagent_prod=http://vmagent.prod.svc:8429
vmalert_prod=http://vmalert.prod.svc:8880
```

Кластер только под хранение описывается одной переменной `vm_<env>` — вкладок
Targets и Rules у него не появится. Мультитенантный кластер задаётся синтаксисом
`vm_<cluster>__<подпись>` и получает селектор тенанта.

### Модуль alerts

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `N8N_URL` | `http://n8n:5678` | Origin n8n, куда nginx фронта проксирует `/webhook/`. Обязательна для образа фронта |
| `N8N_ORIGIN` | `http://localhost:5678` | Тот же адрес для dev-сервера Vite |

Метаданные модуля (журнал, статус выполнения, кэш конфигураций) хранятся в
Postgres по `PG_*`. Без настроенного Postgres модуль работает без истории.

## Диагностика

| Адрес | Что показывает |
| --- | --- |
| `GET /health` | Живость бэкенда |
| `GET /access/me` | Кто вошёл, его группы и доступные вкладки |
| `GET /access/debug` | Какие заголовки реально дошли до бэкенда + подсказка, на каком хопе теряется личность |

Оба сервиса пишут NDJSON в stdout: бэкенд — события авторизации и ошибки
запросов, nginx фронта — access-лог с личностью. Сопоставление двух логов
локализует потерю заголовков.

## Документация

Устройство проекта, правила разработки и разбор каждого механизма — в папке
[`docs/`](docs/):

| Документ | О чём |
| --- | --- |
| [AGENTS.md](docs/AGENTS.md) | Правила работы над проектом |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Полная схема, модули, потоки данных |
| [MODULES.md](docs/MODULES.md) | Как добавить модуль |
| [UI.md](docs/UI.md) | Шрифты, палитра, компоненты |
| [RBAC.md](docs/RBAC.md) | Авторизация и доступ к вкладкам |
| [AUTH.md](docs/AUTH.md) | Настройка Keycloak, oauth2-proxy, внешнего nginx |
| [STORAGE.md](docs/STORAGE.md) | Хранилище и таблицы |
| [NGINX.md](docs/NGINX.md) | Маршрутизация и прокси |
| [LOGGING.md](docs/LOGGING.md) | Логи и диагностика |
