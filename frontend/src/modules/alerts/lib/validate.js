// СГЕНЕРИРОВАНО из src/lib/validate.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

import { emailEnabled, normalizeEmailList, telegramEnabled } from './emailList.js'
import { isValidCron } from './cron.js'

const TYPES = ['batch', 'silence', 'any', 'incident'];
const ANY_MODES = ['perEvent', 'digestPerRun', 'capped'];

function slugId(value) {
  const s = String(value || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return s
}

function validateConfig(cfg) {
  const errors = []
  const c = cfg || {}
  const s = c.source || {}
  const n = c.notify || {}
  const r = c.rule || {}
  const d = c.delivery || {}
  const sched = c.schedule || {}
  const to = normalizeEmailList((d.email || {}).to)
  const wantEmail = emailEnabled(d)
  const wantTg = telegramEnabled(d)

  // id = бизнес-ключ; в форме задаётся через код алерта (notify.alertId)
  const id = c.id || slugId(n.alertId)
  if (!id) errors.push('укажите код алерта (из него получится id)')
  if (!TYPES.includes(c.type)) errors.push('type должен быть одним из: ' + TYPES.join(', '))
  if (!s.index) errors.push('source.index обязателен')
  if (!s.timeField) errors.push('source.timeField обязателен')
  if (s.query !== undefined && (typeof s.query !== 'object' || s.query === null)) {
    errors.push('source.query должен быть объектом')
  }
  if (!n.alertId) errors.push('notify.alertId обязателен')
  if (!n.title) errors.push('notify.title обязателен')

  if (!wantEmail && !wantTg) {
    errors.push('включите хотя бы один канал: email или Telegram')
  }
  if (wantEmail && !to.length) {
    errors.push('delivery.email.to: нужен хотя бы один адрес')
  }
  if (wantTg) {
    const tg = d.telegram || {}
    if (!String(tg.authorization || '').trim()) {
      errors.push('delivery.telegram.authorization обязателен')
    }
    if (!Array.isArray(tg.groupList) || !tg.groupList.length) {
      errors.push('delivery.telegram.groupList: укажите хотя бы одну группу')
    }
  }

  if (sched.trigger === 'cron') {
    if (!isValidCron(sched.cron)) {
      errors.push('schedule.cron: неверное cron-выражение (5 или 6 полей)')
    }
  }
  if (sched.trigger === 'dailyTimes') {
    const times = Array.isArray(sched.times) ? sched.times : []
    if (!times.length) errors.push('schedule.times: укажите хотя бы одно время')
  }

  if (c.type === 'batch') {
    if (!Array.isArray(r.groupBy) || r.groupBy.length === 0) errors.push('rule.groupBy: нужен непустой список')
    if (!Array.isArray(r.columns) || r.columns.length === 0) errors.push('rule.columns: нужен непустой список')
  }
  if (c.type === 'silence') {
    if (!r.hostField) errors.push('rule.hostField обязателен')
    if (!(Number(r.thresholdMinutes) > 0)) errors.push('rule.thresholdMinutes должен быть больше нуля')
  }
  if (c.type === 'any') {
    if (!ANY_MODES.includes(r.mode)) errors.push('rule.mode должен быть одним из: ' + ANY_MODES.join(', '))
  }
  if (c.type === 'incident') {
    if (!r.statusField) errors.push('rule.statusField обязателен')
    if (!String(r.openValue || '').trim()) errors.push('rule.openValue обязателен')
    if (!String(r.closeValue || '').trim()) errors.push('rule.closeValue обязателен')
    if (!Array.isArray(r.incidentKey) || !r.incidentKey.length) errors.push('rule.incidentKey: нужен хотя бы один признак')
  }

  return { ok: errors.length === 0, errors }
}

export { validateConfig, slugId };
