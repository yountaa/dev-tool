# Маршрутизация и nginx

Перед приложением два nginx:

| Слой | Файл | Ответственность |
| --- | --- | --- |
| **Внешний** | конфигурация инфраструктуры | TLS, имя хоста, `auth_request` к oauth2-proxy |
| **Внутренний** | `frontend/nginx.conf` | статика SPA, проксирование API на бэкенд |

Браузер обращается только к фронту. Бэкенд наружу открывать не требуется.

---

## Внутренний nginx

`frontend/nginx.conf` — **шаблон**: образ `nginx:alpine` при старте подставляет
`${FRONTEND_PORT}` и `${N8N_URL}` и кладёт результат в `conf.d/default.conf`.
Переменные самого nginx (`$uri`, `$http_*`) остаются нетронутыми.

### Пути

| Путь | Куда | Заголовки личности |
| --- | --- | --- |
| `/silences/` | `backend_pool` | да |
| `/victoria/` | `backend_pool` | да |
| `/alerts/` | `backend_pool` | да |
| `/access/` | `backend_pool` | да |
| `/share`, `/s/` | `backend_pool` | нет |
| `/health` | `backend_pool` | нет |
| `/webhook/` | `${N8N_URL}` | нет |
| `/assets/` | статика, кэш на год | — |
| `/` | SPA, `try_files … /index.html` | — |

`index.html` отдаётся с `Cache-Control: no-cache`: иначе после пересборки
браузер продолжит тянуть старый бандл. Файлы в `/assets/` содержат хэш в имени и
кэшируются надолго как иммутабельные.

### Проброс личности

На API-путях заголовки от oauth2-proxy передаются дальше явно — без этого
бэкенд получит запрос без имени и групп:

```nginx
proxy_set_header X-Forwarded-Preferred-Username $http_x_forwarded_preferred_username;
proxy_set_header X-Forwarded-Email             $http_x_forwarded_email;
proxy_set_header X-Forwarded-User              $http_x_forwarded_user;
proxy_set_header X-Forwarded-Groups            $http_x_forwarded_groups;
```

На `/webhook/` группы намеренно не пробрасываются: заголовок бывает очень
большим, а получателю он не нужен.

### Буферы под крупные заголовки

oauth2-proxy кладёт в запрос крупную session-cookie и полный список групп
Keycloak. При большом числе групп это перерастает дефолтные буферы nginx: запрос
рубится с `400 Request Header Or Cookie Too Large`, либо заголовки теряются — и
пользователь становится «local».

```nginx
client_header_buffer_size 64k;
large_client_header_buffers 16 128k;
proxy_buffer_size 32k;
proxy_buffers 16 32k;
proxy_busy_buffers_size 64k;
```

Аналогичный лимит есть у uvicorn — он последний хоп цепочки. В `CMD` бэкенда
явно задан парсер h11 с потолком 2 МБ; иначе uvicorn отвечает 400 **до**
приложения, и в логах приложения такого запроса просто нет.

### HA бэкендов

`BACKEND_URL` принимает один адрес или список через запятую. Скрипт
`docker-entrypoint.d/10-backend-upstream.sh` собирает из него upstream
(запускается до подстановки переменных — отсюда префикс `10-`):

```nginx
upstream backend_pool {
    least_conn;
    server 10.0.0.5:8000 max_fails=3 fail_timeout=10s;
    server 10.0.0.6:8000 max_fails=3 fail_timeout=10s;
}
```

Упавшая нода временно исключается, а `proxy_next_upstream error timeout
http_502 http_503 http_504` переводит запрос на следующую.

### Сжатие

`gzip on` для JSON, JS, CSS и SVG. Бэкенд сжимает свои ответы сам
(`GZipMiddleware`), уже сжатое nginx пропускает как есть.

### Логи

Access-лог в формате NDJSON (`authlog`) пишется в stdout и содержит личность из
заголовков. Значения групп и cookie в лог не попадают — только признак наличия и
размеры. Разбор — [LOGGING.md](LOGGING.md).

## Внешний nginx

Терминирует TLS и выполняет авторизацию через `auth_request` к oauth2-proxy.

**Личность проставляется в каждом `location`**, а не только в корневом. Директивы
`auth_request_set` и `proxy_set_header` не наследуются в дочерние блоки так, как
этого ожидают: пропущенный `location` даёт запросы без имени именно на своих
путях. Это самая частая причина, когда часть интерфейса знает пользователя, а
часть считает его «local».

Список путей, на которых личность обязательна: `/silences/`, `/victoria/`,
`/alerts/`, `/access/`.

Готовая конфигурация внешнего nginx с `auth_request`, настройка oauth2-proxy и
клиента Keycloak — [AUTH.md](AUTH.md).

## Dev-сервер

`frontend/vite.config.js` повторяет ту же маршрутизацию для `npm run dev`:

```js
'/silences': 'http://localhost:8000',
'/victoria': 'http://localhost:8000',
'/alerts':   'http://localhost:8000',
'/access':   'http://localhost:8000',
'/health':   'http://localhost:8000',
'/share':    'http://localhost:8000',
'/s/':       'http://localhost:8000',
'/webhook':  { target: N8N_ORIGIN, changeOrigin: true },
```

**Новый путь API добавляется в оба файла.** Путь, добавленный только в один,
работает ровно в одном режиме — это типовая ошибка при добавлении модуля.

## Проверка после изменений

```bash
# синтаксис конфигурации внутри контейнера фронта
nginx -t

# что реально дошло до бэкенда (через прокси, как обычный пользователь)
curl -s https://<host>/access/debug | jq

# живость бэкенда через фронт
curl -s https://<host>/health
```
