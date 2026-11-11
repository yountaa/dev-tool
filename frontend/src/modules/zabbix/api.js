// Все запросы модуля zabbix к бэкенду — в одном месте.
// Бэкенд уже переводит записи auditlog в слова: числа действий и типов объектов,
// unix-время, JSON details — фронт только рисует.
import { http } from '../../shared/api.js'

// query-string из объекта параметров (пропускаем пустые; массив — через запятую).
function qs(params) {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) {
      if (v.length) p.set(k, v.join(','))
    } else if (v !== undefined && v !== null && v !== '') {
      p.set(k, v)
    }
  }
  return p.toString()
}

export const zabbixApi = {
  // инстансы Zabbix = вкладки окружений: [{ name, api }]
  environments: () => http.get('/zabbix/environments'),
  // справочники фильтров: { actions: [{id,label,kind}], types: [{id,label}] }
  dictionaries: () => http.get('/zabbix/dictionaries'),
  // поиск: { total, limit, from, to, records: [...] }
  audit: (env, filters, signal) => http.get(`/zabbix/${env}/audit?${qs(filters)}`, { signal }),
}
