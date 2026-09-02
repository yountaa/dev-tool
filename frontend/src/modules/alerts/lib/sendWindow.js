/**
 * Окно отправки писем (schedule.sendWindow).
 * Вставить в Select Due + Build Body после проверки isDue:
 *
 *   if (!isWithinSendWindow(cfg.schedule && cfg.schedule.sendWindow, now)) continue;
 */

function normHm(s) {
  const m = String(s || '').match(/^(\d{1,2}):(\d{2})/)
  if (!m) return '00:00'
  let h = Number(m[1])
  // Intl иногда отдаёт 24:xx в полночь — для сравнения с type=time нужно 00:xx.
  if (h === 24) h = 0
  return String(h).padStart(2, '0') + ':' + m[2]
}

export function isWithinSendWindow(sw, nowMs = Date.now()) {
  if (!sw || !sw.enabled) return true
  const tz = sw.timezone || 'Europe/Moscow'
  const days = Array.isArray(sw.days) && sw.days.length ? sw.days : [0, 1, 2, 3, 4, 5, 6]
  const from = normHm(sw.from || '00:00')
  const to = normHm(sw.to || '23:59')

  let parts
  try {
    parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz,
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(new Date(nowMs))
  } catch (_) {
    return true
  }

  const get = (type) => (parts.find((p) => p.type === type) || {}).value
  const wd = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[get('weekday')]
  if (wd === undefined || !days.map(Number).includes(wd)) return false

  const hm = normHm(`${get('hour')}:${get('minute')}`)
  if (from <= to) return hm >= from && hm <= to
  // через полночь: 22:00–06:00
  return hm >= from || hm <= to
}
