FROM node:20-alpine AS build
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV FRONTEND_PORT=80 \
    BACKEND_URL=http://backend:8000
EXPOSE 80
