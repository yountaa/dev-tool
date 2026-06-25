#!/bin/sh
# Собирает upstream backend_pool из BACKEND_URL для nginx фронта.
#
# BACKEND_URL — один адрес ИЛИ несколько через запятую (HA, как alert_*/rules_*):
#   BACKEND_URL=http://bk1:8000,http://bk2:8000,http://bk3:8000
# Из каждого берём host:port и кладём в upstream. nginx балансирует запросы и
# временно исключает мёртвую ноду (max_fails/fail_timeout) — фронт переживает
# падение части бэкендов. Запускается до envsubst (см. имя 10-... < 20-...).
set -e

: "${BACKEND_URL:=http://backend:8000}"
conf=/etc/nginx/conf.d/upstream.conf

echo "upstream backend_pool {" > "$conf"
echo "    least_conn;" >> "$conf"          # шлём на наименее загруженную ноду

OLD_IFS=$IFS
IFS=','
for url in $BACKEND_URL; do
    # сперва убираем пробелы, затем схему (http://) и путь — остаётся host:port
    hp=$(printf '%s' "$url" | sed -e 's/[[:space:]]//g' -e 's#^[a-zA-Z][a-zA-Z0-9+.-]*://##' -e 's#/.*$##')
    if [ -n "$hp" ]; then
        echo "    server $hp max_fails=3 fail_timeout=10s;" >> "$conf"
    fi
done
IFS=$OLD_IFS

echo "}" >> "$conf"
echo "[backend-upstream] нод в backend_pool: $(grep -c '    server ' "$conf") (BACKEND_URL=$BACKEND_URL)"
