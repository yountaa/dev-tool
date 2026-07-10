// Все запросы модуля victoria к бэкенду — в одном месте.
// Бэкенд — тонкий прокси к VM: отдаёт Prometheus-совместимый JSON, разбираем тут.
import { http } from '../../shared/api.js'

// query-string из объекта параметров (пропускаем пустые).
function qs(params) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') p.set(k, v)
  }
  return p.toString()
}

export const victoriaApi = {
  // кластеры-окружения = вкладки; у каждого флаги доступных под-вкладок
  environments: () => http.get('/victoria/environments'),

  // vmselect: чтение метрик (tenant — опц., для мультитенантного VM)
  query: (env, expr, time, tenant) =>
    http.get(`/victoria/${env}/query?${qs({ query: expr, time, tenant })}`),
  queryRange: (env, expr, start, end, step, tenant) =>
    http.get(`/victoria/${env}/query_range?${qs({ query: expr, start, end, step, tenant })}`),
  labels: (env, tenant) => http.get(`/victoria/${env}/labels?${qs({ tenant })}`),
  labelValues: (env, label, tenant, limit) =>
    http.get(`/victoria/${env}/label_values?${qs({ label, tenant, limit })}`),
  // refresh=1 — перечитать мимо кэша бэкенда (кнопка «Обновить»)
  tsdb: (env, topn, tenant, refresh) =>
    http.get(`/victoria/${env}/tsdb?${qs({ topn, tenant, refresh: refresh ? 1 : '' })}`),

  // vmagent / vmalert
  targets: (env, refresh) => http.get(`/victoria/${env}/targets?${qs({ refresh: refresh ? 1 : '' })}`),
  rules: (env, refresh) => http.get(`/victoria/${env}/rules?${qs({ refresh: refresh ? 1 : '' })}`),
  alerts: (env) => http.get(`/victoria/${env}/alerts`),
}
