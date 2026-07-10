FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 8000
# У uvicorn СВОЙ потолок на размер заголовков запроса, независимый от буферов
# nginx: ~16КБ у парсера h11 и ~80КБ у httptools (дефолт с uvicorn[standard]).
# Это последний хоп цепочки (внешний nginx → oauth2-proxy → nginx фронта → uvicorn):
# большие cookie сессии + X-Forwarded-Groups со всеми группами Keycloak пробивают
# этот лимит, и uvicorn отвечает 400 ещё ДО приложения (в логах приложения такого
# запроса нет — только warning uvicorn «Invalid HTTP request received»).
# Явно берём h11 с лимитом 2МБ — под стать large_client_header_buffers 16 128k у nginx.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --http h11 --h11-max-incomplete-event-size 2097152"]
