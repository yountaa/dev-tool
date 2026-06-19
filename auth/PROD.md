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
        proxy_set_header X-Forwarded-Preferred-Username "";
        proxy_set_header X-Forwarded-User  "";
        proxy_set_header X-Forwarded-Email "";
    }
}
```

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

## Проверка после выката

1. Открыть `https://silences.example.com` без сессии → редирект на Keycloak-логин.
2. Залогиниться → в шапке видно имя.
3. Создать правило → в git-хабе коммит подписан этим пользователем.
4. `curl https://silences.example.com/silences/me` (с cookie) → `{"name":"<ты>","auth":true}`.
5. Попробовать достучаться до бэкенда в обход (если порт случайно открыт) — не должно быть доступно.
