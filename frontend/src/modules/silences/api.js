// Все запросы модуля silences к бэкенду — в одном месте.
import { http } from '../../shared/api.js'

export const silencesApi = {
  // окружения = вкладки (приходят из alert_* на бэкенде)
  environments: () => http.get('/silences/environments'),

  // локальные правила (источник правды — git-хаб, не Alertmanager)
  rules: (env) => http.get(`/silences/${env}/rules`),
  enableRule: (env, kind, id) => http.post(`/silences/${env}/rules/${kind}/${id}/enable`),
  disableRule: (env, kind, id) => http.post(`/silences/${env}/rules/${kind}/${id}/disable`),
  deleteRule: (env, kind, id) => http.del(`/silences/${env}/rules/${kind}/${id}`),
  editOnetimeRule: (env, id, body) => http.put(`/silences/${env}/rules/onetime/${id}`, body),
  editScheduleRule: (env, id, body) => http.put(`/silences/${env}/rules/schedule/${id}`, body),

  // разовый
  createOnetime: (env, body) => http.post(`/silences/${env}/onetime`, body),

  // по расписанию (хранится в git, ставит шедулер)
  listSchedules: (env) => http.get(`/silences/${env}/schedules`),
  createSchedule: (env, body) => http.post(`/silences/${env}/schedules`, body),
  previewSchedule: (env, body) => http.post(`/silences/${env}/schedules/preview`, body),
}
