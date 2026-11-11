// Разбор и матч cron (5 или 6 полей). 6 полей = с секундами, как в n8n:
//   "0 1 9-19 * * *"  → каждую минуту :01 в часы 9–19.

function parseField(field, min, max) {
  const out = new Set()
  const raw = String(field || '*').trim()
  if (raw === '*') {
    for (let i = min; i <= max; i++) out.add(i)
    return out
  }
  for (const part of raw.split(',')) {
    const stepMatch = part.match(/^(.+)\/(\d+)$/)
    const range = stepMatch ? stepMatch[1] : part
    const step = stepMatch ? Number(stepMatch[2]) : 1
    if (!step || step < 1) continue

    let start
    let end
    if (range === '*') {
      start = min
      end = max
    } else if (range.includes('-')) {
      const [a, b] = range.split('-')
      start = Number(a)
      end = Number(b)
    } else {
      start = Number(range)
      end = start
    }
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue
    if (start > end) [start, end] = [end, start]
    start = Math.max(min, start)
    end = Math.min(max, end)
    for (let i = start; i <= end; i += step) out.add(i)
  }
  return out
}

/** Разбить выражение на { second?, minute, hour, dayOfMonth, month, dayOfWeek }. */
export function parseCron(expr) {
  const parts = String(expr || '').trim().split(/\s+/).filter(Boolean)
  if (parts.length === 5) {
    return {
      second: parseField('0', 0, 59),
      minute: parseField(parts[0], 0, 59),
      hour: parseField(parts[1], 0, 23),
      dayOfMonth: parseField(parts[2], 1, 31),
      month: parseField(parts[3], 1, 12),
      dayOfWeek: parseField(parts[4], 0, 7),
    }
  }
  if (parts.length === 6) {
    return {
      second: parseField(parts[0], 0, 59),
      minute: parseField(parts[1], 0, 59),
      hour: parseField(parts[2], 0, 23),
      dayOfMonth: parseField(parts[3], 1, 31),
      month: parseField(parts[4], 1, 12),
      dayOfWeek: parseField(parts[5], 0, 7),
    }
  }
  return null
}

export function isValidCron(expr) {
  return !!parseCron(expr)
}

/** Части «сейчас» в таймзоне для матча cron. */
export function cronNowParts(nowMs, tz) {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz || 'Europe/Moscow',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).formatToParts(new Date(nowMs))
    const get = (t) => (parts.find((p) => p.type === t) || {}).value
    let hour = Number(get('hour'))
    if (hour === 24) hour = 0
    const wdMap = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }
    const dow = wdMap[get('weekday')]
    return {
      second: Number(get('second')),
      minute: Number(get('minute')),
      hour,
      dayOfMonth: Number(get('day')),
      month: Number(get('month')),
      dayOfWeek: dow,
    }
  } catch (_) {
    return null
  }
}

/**
 * Совпадает ли момент nowMs с cron.
 * Движок тикает раз в минуту — секунды в выражении не фильтруем по текущей
 * секунде (иначе почти всегда мимо); достаточно минуты/часа/даты.
 */
export function cronMatches(expr, nowMs, tz) {
  const cron = parseCron(expr)
  const now = cronNowParts(nowMs, tz)
  if (!cron || !now) return false

  if (!cron.minute.has(now.minute)) return false
  if (!cron.hour.has(now.hour)) return false
  if (!cron.month.has(now.month)) return false

  const domOk = cron.dayOfMonth.has(now.dayOfMonth)
  // В cron day-of-week: 0 и 7 = воскресенье.
  const dowOk = cron.dayOfWeek.has(now.dayOfWeek) || (now.dayOfWeek === 0 && cron.dayOfWeek.has(7))

  const domStar = String(expr).trim().split(/\s+/).length === 6
    ? String(expr).trim().split(/\s+/)[3] === '*'
    : String(expr).trim().split(/\s+/)[2] === '*'
  const dowStar = String(expr).trim().split(/\s+/).length === 6
    ? String(expr).trim().split(/\s+/)[5] === '*'
    : String(expr).trim().split(/\s+/)[4] === '*'

  // Как в Vixie cron: если оба DOM и DOW не *, достаточно совпадения одного.
  if (!domStar && !dowStar) return domOk || dowOk
  if (!domStar) return domOk
  if (!dowStar) return dowOk
  return true
}
