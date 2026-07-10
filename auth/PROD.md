# Подключение Keycloak на проде

Цель: имя создателя/редактора правил берётся из Keycloak, а в веб не пускает без логина.
Механизм — **oauth2-proxy** перед вебом. Он логинит пользователя в Keycloak и кладёт его
имя в заголовок, бэкенд читает заголовок.

## Схема запроса

```
браузер ──TLS──► внешний nginx ──► oauth2-proxy ──► frontend (nginx) ──► backend
   ▲ логин                              │
   └───────────── OIDC ────────────► Keycloak (realms/<realm>)
```

- oauth2-proxy — «вахтёр»: без логина дальше не пускает.
- После логина ставит `X-Forwarded-Preferred-Username: <preferred_username>`.
- frontend nginx уже пробрасывает этот заголовок на бэкенд (готово в `nginx.conf`).
- backend с `AUTH_ENABLED=true` берёт имя из заголовка.

---

## Шаг 1. Keycloak — завести клиента

В нужном realm (можно отдельный, напр. `infra`):

1. **Clients → Create client**
   - Client type: **OpenID Connect**
   - Client ID: `silences`
2. **Capability config**
   - Client authentication: **On** (confidential)
   - Standard flow: **On** (остальное Off)
3. **Login settings**
   - Valid redirect URIs: `https://silences.example.com/oauth2/callback`
   - Valid post logout redirect URIs: `https://silences.example.com/*`
   - Web origins: `https://silences.example.com`
4. Сохранить → вкладка **Credentials** → скопировать **Client secret**.

`preferred_username` отдаётся в scope `profile` (он в дефолтных) — отдельный маппер не нужен.
Пользователи — обычные пользователи realm (заводятся в Keycloak или приходят из LDAP/AD).

---

## Шаг 2. oauth2-proxy — поставить перед вебом

На проде Keycloak доступен по одному адресу, поэтому работает обычный OIDC discovery
(никаких split-URL, как в тестовом стенде). Пример (docker-compose-сервис; для k8s — те же
переменные в Deployment):

```yaml
oauth2-proxy:
  image: quay.io/oauth2-proxy/oauth2-proxy:v7.6.0
  environment:
    OAUTH2_PROXY_HTTP_ADDRESS: 0.0.0.0:4180
    OAUTH2_PROXY_UPSTREAMS: http://frontend:80          # весь веб за прокси
    OAUTH2_PROXY_PROVIDER: oidc
    OAUTH2_PROXY_OIDC_ISSUER_URL: https://keycloak.example.com/realms/infra
    OAUTH2_PROXY_CLIENT_ID: silences
    OAUTH2_PROXY_CLIENT_SECRET: <client secret из Keycloak>
    OAUTH2_PROXY_COOKIE_SECRET: <openssl rand -base64 32 | head -c 32>
    OAUTH2_PROXY_REDIRECT_URL: https://silences.example.com/oauth2/callback
    OAUTH2_PROXY_EMAIL_DOMAINS: "*"                     # или ограничь доменом почты
    OAUTH2_PROXY_PASS_USER_HEADERS: "true"             # ставит X-Forwarded-Preferred-Username
    OAUTH2_PROXY_COOKIE_SECURE: "true"                 # за TLS
    OAUTH2_PROXY_COOKIE_DOMAINS: silences.example.com
    OAUTH2_PROXY_WHITELIST_DOMAINS: keycloak.example.com   # для редиректа на logout Keycloak
    OAUTH2_PROXY_REVERSE_PROXY: "true"                 # доверять X-Forwarded-* от внешнего nginx
    OAUTH2_PROXY_SKIP_PROVIDER_BUTTON: "true"
```

Ограничить доступ конкретной группой/ролью Keycloak — через
`OAUTH2_PROXY_ALLOWED_GROUPS` + маппер groups в клиенте (опционально).

**Для RBAC вкладок (`RBAC_ENABLED=true`) маппер groups ОБЯЗАТЕЛЕН**: заголовок
`X-Forwarded-Groups` появляется только когда клеймы groups есть в токене.
В клиенте Keycloak: **Client scopes → <client>-dedicated → Add mapper → Group Membership**,
имя клейма `groups`, Full group path — Off. Без маппера бэкенд получит пустые группы,
и при включённом RBAC пользователь не увидит закрытые вкладки.

⚠️ После маппера токен и cookie сессии заметно толстеют (полный список групп едет
в каждом запросе). Проверь лимиты заголовков по ВСЕЙ цепочке — см. «Отладка» ниже.

---

## Шаг 3. backend — включить режим

В окружении бэкенда (`.env`):

```bash
AUTH_ENABLED=true
AUTH_USER_HEADER=X-Forwarded-Preferred-Username
```

Поле «Создатель» в вебе станет «только показ», имя возьмётся из Keycloak, а в git-коммитах
автор = вошедший пользователь.

---

## Шаг 4. внешний nginx (TLS) → oauth2-proxy

```nginx
server {
    listen 443 ssl;
    server_name silences.example.com;
    # ssl_certificate ...; ssl_certificate_key ...;

    location / {
        proxy_pass http://oauth2-proxy:4180;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ВАЖНО: затираем заголовки личности от клиента — чтобы их нельзя было подделать.
        # Настоящие значения проставит oauth2-proxy уже после логина.
        # X-Forwarded-Groups тоже: по нему бэкенд решает доступ к вкладкам (RBAC).
        proxy_set_header X-Forwarded-Preferred-Username "";
        proxy_set_header X-Forwarded-User   "";
        proxy_set_header X-Forwarded-Email  "";
        proxy_set_header X-Forwarded-Groups "";
    }

    # Cookie сессии oauth2-proxy большая (а с маппером groups — очень), дефолтных
    # буферов nginx (4 8k) не хватает → 400 «Request Header Or Cookie Too Large».
    client_header_buffer_size 64k;
    large_client_header_buffers 16 128k;
}
```

### Вариант Б: nginx с auth_request (как у нас на проде)

Внешний nginx сам ходит в oauth2-proxy subrequest'ом (`auth_request /oauth2/auth`),
забирает личность из заголовков ответа (`X-Auth-Request-*`) и отдаёт ВЕСЬ трафик
внутреннему nginx фронта. Рабочий образец со всеми комментариями —
**`auth/external-nginx.example.conf`**.

Разделение ролей (чтобы внешний не приходилось трогать при каждом новом модуле):

- **Внешний nginx** ничего не знает о путях приложения: TLS + «вахтёр» (auth_request) +
  личность в `X-Forwarded-*` — всё в ОДНОМ `location /`. Конфиг статичен.
- **Внутренний nginx** (`frontend/nginx.conf`, живёт в git с приложением) маршрутизирует
  пути: `/silences/`, `/victoria/`, `/access/` → backend, остальное → статика SPA.
  Новый модуль-вкладка = location только во внутреннем.

Правила, на которых этот вариант ломается:

1. **Заголовки личности ставятся в КАЖДОМ location внешнего nginx, который проксирует
   к приложению.** Поэтому location и держим один. История: раньше имя ставилось только
   в `location /silences/` (и веб брал его из `/silences/me`) — версия с victoria+RBAC
   стала брать имя из нового `/access/me`, для которого location'а не было → все стали
   «local». Один общий `location /` закрывает это раз и навсегда.
2. **Группы** = `auth_request_set $groups $upstream_http_x_auth_request_groups;` +
   `proxy_set_header X-Forwarded-Groups $groups;`. У oauth2-proxy должен быть включён
   `--set-xauthrequest=true`, в Keycloak — маппер groups (см. шаг 2), иначе заголовок
   будет пустым.
3. **Буферы — на уровне http**: и на заголовки запроса (cookie сессии), и
   `proxy_buffer_size` — ответ subrequest'а `/oauth2/auth` несёт полный список групп,
   он обязан влезть в буфер ответа, иначе вход ломается целиком. Значения — в образце.
4. **backend и oauth2-proxy не публиковать наружу**: весь трафик — только через внешний
   nginx → frontend. Прямой доступ к backend = подделка личности запросто.

frontend nginx менять не нужно — он уже форвардит заголовок на бэкенд.

---

## Безопасность (обязательно проверить)

- **backend и frontend НЕ доступны в обход oauth2-proxy.** Не публикуй их порты наружу —
  только oauth2-proxy смотрит в мир (через внешний nginx). Иначе заголовок с именем
  подделывается, и кто угодно «станет» кем угодно.
- **Затирай входящие `X-Forwarded-*`-заголовки личности на внешнем входе** (см. nginx выше).
- **TLS + `COOKIE_SECURE=true`.** По http cookie не защищены.
- **Секреты** (`CLIENT_SECRET`, `COOKIE_SECRET`) — из секрет-стора/переменных окружения,
  не в git. `COOKIE_SECRET` ровно 16/24/32 байта.

---

## Выход / смена пользователя

Кнопка «выйти» в шапке ведёт на `/oauth2/sign_out` (гасит сессию oauth2-proxy). С обычным
OIDC discovery oauth2-proxy умеет и RP-initiated logout — добавь
`OAUTH2_PROXY_WHITELIST_DOMAINS=keycloak.example.com`, и редирект на logout Keycloak
сработает. Если Keycloak держит SSO-сессию — зайти другим пользователем проще из приватного
окна или после logout в Keycloak.

---

## Отладка: кто вошёл и что дошло («почему я local»)

Пользователь «local» = бэкенд НЕ увидел имя. Либо `AUTH_ENABLED` фактически false,
либо заголовок `X-Forwarded-Preferred-Username` потерялся на одном из хопов:

```
браузер ──► внешний nginx ──► oauth2-proxy ──► nginx фронта ──► uvicorn (бэкенд)
            буферы заголовков   ставит имя      буферы +         свой лимит заголовков
            (подними, см. шаг 4) и группы        authlog-лог      (поднят в Dockerfile)
```

Инструменты (в порядке применения):

1. **`/access/debug` в браузере** (через прокси, как обычный юзер) — показывает,
   что реально дошло до бэкенда: заголовки `X-Forwarded-*`, размеры cookie/групп,
   действующую конфигурацию и подсказку `hint`, на каком хопе искать потерю.
2. **Лог бэкенда на старте — событие `auth.config`** — действующие значения
   `AUTH_ENABLED`/`RBAC_*`/`access_*`. Если `auth_enabled=false`, а ждал true —
   .env не доехал до контейнера, дальше можно не искать.
3. **Лог бэкенда на каждый запрос — `http.request`** — поля `user`, `auth_hdr`
   (дошёл ли заголовок с именем), `groups_n`. По нему всегда видно, кто что сделал
   и с чем пришёл. `auth.no_username_header` (WARNING) — имя не дошло, с размерами
   того, что дошло. `auth.denied` (WARNING) — RBAC не пустил (кто/куда/с какими группами).
4. **Лог nginx фронта — `authlog`** (stdout контейнера, NDJSON) — что пришло ОТ
   oauth2-proxy: `user`, `email`, `groups` (present/missing), `request_length`,
   `status`/`upstream_status`. Сравни с логом бэкенда:
   в authlog user есть, бэкенд пишет local → потеря на хопе nginx→бэкенд;
   в authlog user пустой → смотри oauth2-proxy и внешний nginx.
5. **Размеры.** С маппером groups заголовки легко переваливают за дефолтные лимиты.
   Признаки: 400 «Request Header Or Cookie Too Large» (nginx) или warning uvicorn
   «Invalid HTTP request received» (лимит парсера, поднят в Dockerfile бэкенда).
   `request_length` в authlog и `headers_total_bytes` в `/access/debug` показывают
   фактический вес.

---

## Проверка после выката

1. Открыть `https://silences.example.com` без сессии → редирект на Keycloak-логин.
2. Залогиниться → в шапке видно имя.
3. Создать правило → в git-хабе коммит подписан этим пользователем.
4. `curl https://silences.example.com/silences/me` (с cookie) → `{"name":"<ты>","auth":true}`.
5. Открыть `/access/debug` → `hint` говорит «всё на месте», в `resolved.groups` — твои группы,
   в `resolved.modules` — ожидаемые вкладки.
6. Попробовать достучаться до бэкенда в обход (если порт случайно открыт) — не должно быть доступно.
7. Прислать `X-Forwarded-Groups: admin-group` снаружи (curl с заголовком) → группы НЕ должны
   примениться (внешний nginx затирает, см. шаг 4).
