FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
# npm ci ставит строго по package-lock.json — детерминированно и без полного
# резолва дерева из реестра (именно он подвешивал "npm install").
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
# Скрипт собирает upstream backend_pool из BACKEND_URL (запускается до envsubst).
COPY docker-entrypoint.d/10-backend-upstream.sh /docker-entrypoint.d/10-backend-upstream.sh
RUN chmod +x /docker-entrypoint.d/10-backend-upstream.sh
ENV FRONTEND_PORT=80 \
    BACKEND_URL=http://backend:8000
EXPOSE 80
