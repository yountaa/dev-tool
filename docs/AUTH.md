# Подключение Keycloak и oauth2-proxy

Порядок настройки входа: без логина в веб не пускает, имя вошедшего доходит до
приложения и используется для атрибуции действий и для RBAC.

Как приложение читает личность и выдаёт права — [RBAC.md](RBAC.md).
Маршрутизация внутреннего nginx — [NGINX.md](NGINX.md).

---

## Схема

```
браузер ──TLS──► внешний nginx ──auth_request──► oauth2-proxy ──OIDC──► Keycloak
                       │
                       └──► frontend (nginx + SPA) ──► backend
```

Роли слоёв разделены так, чтобы новый модуль не требовал правки внешнего nginx:

- **внешний nginx** не знает о путях приложения. Он делает три вещи: TLS,
  проверку входа (`auth_request`) и простановку личности в `X-Forwarded-*` —
  всё в одном `location /`. Конфигурация статична;
- **внутренний nginx** (`frontend/nginx.conf`, лежит в репозитории вместе с
  приложением) маршрутизирует пути. Новая вкладка — это `location` только здесь.

## Шаг 1. Клиент в Keycloak

В нужном realm:

1. **Clients → Create client**: тип **OpenID Connect**, Client ID — например
   `devtool`.
2. **Capability config**: Client authentication — **On** (confidential),
   Standard flow — **On**, остальное выключено.
3. **Login settings**:
   - Valid redirect URIs: `https://<host>/oauth2/callback`
   - Valid post logout redirect URIs: `https://<host>/*`
   - Web origins: `https://<host>`
4. Вкладка **Credentials** → скопировать **Client secret**.

`preferred_username` приходит в scope `profile`, он в наборе по умолчанию —
отдельный маппер для имени не нужен.

### Маппер групп — обязателен для RBAC

Заголовок `X-Forwarded-Groups` появляется, только если клейм `groups` есть в
токене:

**Client scopes → `<client>-dedicated` → Add mapper → Group Membership**,
имя клейма `groups`, Full group path — Off.

Без маппера бэкенд получит пустой список групп, и при `RBAC_ENABLED=true`
пользователь не увидит закрытые вкладки.

> После добавления маппера токен и cookie сессии заметно растут: полный список
> групп едет в каждом запросе. Лимиты буферов по всей цепочке нужно проверить —
> см. раздел «Размеры заголовков».

## Шаг 2. oauth2-proxy

```yaml
oauth2-proxy:
  image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
  environment:
    OAUTH2_PROXY_HTTP_ADDRESS: 0.0.0.0:4180
    OAUTH2_PROXY_UPSTREAMS: http://frontend:80
    OAUTH2_PROXY_PROVIDER: oidc
    OAUTH2_PROXY_OIDC_ISSUER_URL: https://<keycloak>/realms/<realm>
    OAUTH2_PROXY_CLIENT_ID: devtool
    OAUTH2_PROXY_CLIENT_SECRET: <client secret>
    OAUTH2_PROXY_COOKIE_SECRET: <ровно 16, 24 или 32 байта>
    OAUTH2_PROXY_REDIRECT_URL: https://<host>/oauth2/callback
    OAUTH2_PROXY_EMAIL_DOMAINS: "*"
    OAUTH2_PROXY_PASS_USER_HEADERS: "true"
    OAUTH2_PROXY_SET_XAUTHREQUEST: "true"      # нужен для auth_request
    OAUTH2_PROXY_COOKIE_SECURE: "true"
    OAUTH2_PROXY_COOKIE_DOMAINS: <host>
    OAUTH2_PROXY_WHITELIST_DOMAINS: <keycloak> # редирект на logout Keycloak
    OAUTH2_PROXY_REVERSE_PROXY: "true"
    OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"
```

Токены приложению не нужны и в заголовки не кладутся:
`pass_access_token`, `set_authorization_header` и `pass_authorization_header`
остаются выключенными. Это самые крупные заголовки в ответе subrequest'а.

Ограничить вход конкретной группой можно через `OAUTH2_PROXY_ALLOWED_GROUPS` —
это грубый фильтр «пускать или нет», права на вкладки выдаёт RBAC приложения.

## Шаг 3. Бэкенд

```bash
AUTH_ENABLED=true
AUTH_USER_HEADER=X-Forwarded-Preferred-Username
AUTH_GROUPS_HEADER=X-Forwarded-Groups
```

Поле «Создатель» в интерфейсе становится «только показ», имя берётся из
Keycloak и записывается автором действия.

## Шаг 4. Внешний nginx

Образец с обезличенными адресами. Кладётся в `conf.d/*.conf` — содержимое
попадает в контекст `http`.

```nginx
# --- Размеры, уровень http ---------------------------------------------------
# Cookie сессии oauth2-proxy плюс полный список групп Keycloak делают заголовки
# большими. Дефолтов (4×8k) не хватает: 400 «Request Header Or Cookie Too Large».
client_header_buffer_size 64k;
large_client_header_buffers 16 128k;
# Ответ oauth2-proxy на subrequest /oauth2/auth несёт X-Auth-Request-Groups со
# всеми группами пользователя и обязан влезть в буфер ответа.
proxy_buffer_size 256k;
proxy_buffers 16 256k;
proxy_busy_buffers_size 512k;

upstream frontend { server <frontend-host>:8088; }
upstream oauth2   { server <oauth2-proxy-host>:4180; }

server {
    listen 80;
    server_name <host>;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name <host>;

    ssl_certificate     /etc/nginx/certs/<host>.crt;
    ssl_certificate_key /etc/nginx/certs/<host>.key;

    # Личность из ОТВЕТА oauth2-proxy на subrequest /oauth2/auth.
    auth_request_set $username $upstream_http_x_auth_request_preferred_username;
    auth_request_set $email    $upstream_http_x_auth_request_email;
    auth_request_set $user     $upstream_http_x_auth_request_user;
    auth_request_set $groups   $upstream_http_x_auth_request_groups;

    # Всё приложение — одним location: проверить вход, положить личность, отдать
    # внутреннему nginx. proxy_set_header заодно ЗАТИРАЕТ одноимённые заголовки
    # от клиента — подделать имя или группы снаружи нельзя.
    location / {
        auth_request /oauth2/auth;
        error_page 401 = /oauth2/sign_in;

        proxy_pass http://frontend;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Preferred-Username $username;
        proxy_set_header X-Forwarded-Email              $email;
        proxy_set_header X-Forwarded-User               $user;
        proxy_set_header X-Forwarded-Groups             $groups;
    }

    location /oauth2/auth {
        proxy_pass http://oauth2;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header Host $host;
        # Буферы прямо здесь: location перекрывает server и http, и никакое
        # другое значение их не пересилит.
        proxy_buffer_size 256k;
        proxy_buffers 16 256k;
        proxy_busy_buffers_size 512k;
    }

    location /oauth2/ {
        proxy_pass http://oauth2;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_set_header X-Auth-Request-Redirect $request_uri;
    }

    # Health-чек без логина, если он нужен мониторингу:
    # location = /health { proxy_pass http://frontend; }
}
```

### Правила, на которых эта схема ломается

1. **Заголовки личности ставятся в каждом `location`, который проксирует к
   приложению.** Поэтому `location` и держится один. Исторический случай: имя
   проставлялось только в `location /silences/`, а появившийся позже `/access/me`
   своего блока не получил — все пользователи стали «local».
2. **Группы** требуют `auth_request_set $groups
   $upstream_http_x_auth_request_groups;` вместе с
   `proxy_set_header X-Forwarded-Groups $groups;`, включённого
   `--set-xauthrequest=true` у oauth2-proxy и маппера в Keycloak. Без любого из
   трёх заголовок приходит пустым.
3. **Буферы ответа для `/oauth2/auth` задаются внутри самого `location`.** Если
   ответ subrequest'а не влезает, ломается вход целиком: 500 на всё, а в
   `error.log` — `upstream sent too big header ... subrequest: "/oauth2/auth"`.
   `proxy_buffer_size` в блоке `server` перекрывает уровень `http` — старые
   маленькие значения нужно удалить.
4. **Бэкенд и oauth2-proxy не публикуются наружу.** Весь трафик идёт только
   через внешний nginx во фронт.

## Размеры заголовков по всей цепочке

Крупная cookie сессии и полный список групп проходят четыре хопа, и лимит есть у
каждого:

| Хоп | Что настраивается |
| --- | --- |
| внешний nginx | `client_header_buffer_size`, `large_client_header_buffers`, буферы ответа `/oauth2/auth` |
| oauth2-proxy | не пересылать токены: `pass_access_token=false` и родственные |
| внутренний nginx | те же буферы, уже заданы в `frontend/nginx.conf` |
| uvicorn | `--http h11 --h11-max-incomplete-event-size 2097152`, задано в `CMD` бэкенда |

Превышение лимита на любом хопе выглядит одинаково: пользователь становится
«local» либо получает 400. Запроса, отвергнутого uvicorn, в логах приложения не
будет вовсе — только `Invalid HTTP request received` в логе самого uvicorn.

## Безопасность

- бэкенд и фронт недоступны в обход oauth2-proxy — иначе заголовок с именем
  подделывается тривиально;
- входящие `X-Forwarded-*` с личностью затираются на внешнем входе;
- TLS обязателен, `COOKIE_SECURE=true`;
- `CLIENT_SECRET` и `COOKIE_SECRET` берутся из секрет-стора, в репозиторий не
  попадают. `COOKIE_SECRET` — ровно 16, 24 или 32 байта.

## Выход и смена пользователя

**Собственной кнопки выхода в интерфейсе нет.** В шапке показывается только имя
вошедшего (при `AUTH_ENABLED=false` — подпись «локальный режим»).

Сессию гасит сам oauth2-proxy своим эндпоинтом `/oauth2/sign_out` — он обслуживает
его независимо от приложения, адрес открывается вручную. При обычном OIDC
discovery работает и RP-initiated logout: для этого в
`OAUTH2_PROXY_WHITELIST_DOMAINS` указывается домен Keycloak.

Если Keycloak держит SSO-сессию, он молча вернёт того же пользователя. Вход под
другим выполняется из приватного окна либо после выхода в самом Keycloak.

Чтобы кнопка появилась в интерфейсе, её нужно добавить в шапку `App.vue` ссылкой
на `/oauth2/sign_out` — сейчас такого кода нет.

## Проверка после выката

```bash
# кто вошёл и что ему видно
curl -s https://<host>/access/me -H "Cookie: <сессия>" | jq

# что реально дошло до бэкенда и где потерялось
curl -s https://<host>/access/debug -H "Cookie: <сессия>" | jq
```

- `auth.config` в логе бэкенда при старте показывает действующие значения
  `AUTH_*` и `RBAC_*`;
- `groups` в `/access/me` непустой — маппер работает;
- `hint` в `/access/debug` подсказывает, на каком хопе теряется личность.

Разбор диагностики — [RBAC.md](RBAC.md) и [LOGGING.md](LOGGING.md).
