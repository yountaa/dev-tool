// Показ дат в одной таймзоне (по умолчанию МСК), чтобы он не зависел от зоны
// браузера. Зону задаёт бэкенд (SILENCE_TZ) — App.vue зовёт setTz() после /me.
//
// Alertmanager/Prometheus отдают время в UTC (с Z/смещением) — Intl с timeZone
// сам переведёт его в нужную зону. Naive-строки (без зоны) трактуем как уже МСК.
let _tz = 'Europe/Moscow'

export function setTz(tz) {
  if (tz) _tz = tz
}

// Есть ли в ISO-строке таймзона (Z или ±ЧЧ:ММ в конце).
const _HAS_TZ = /(Z|[+-]\d{2}:?\d{2})$/

// "ДД.ММ ЧЧ:ММ". Строки С зоной (UTC из AM/Prometheus, история) переводим в _tz.
// Naive-строки (datetime-local: "2026-06-25T14:30") — это уже «настенное» МСК,
// показываем как есть, без перевода (иначе в браузере другой зоны время уехало бы).
export function fmtDt(s) {
  if (!s) return ''
  if (!_HAS_TZ.test(s)) {
    const [date, time] = String(s).split('T')
    const [, mo, da] = (date || '').split('-')
    if (!da) return s
    return `${da}.${mo} ${(time || '').slice(0, 5)}`
  }
  const d = new Date(s)
  if (isNaN(d)) return s
  const parts = new Intl.DateTimeFormat('ru-RU', {
    timeZone: _tz,
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).formatToParts(d)
  const g = (t) => parts.find((p) => p.type === t)?.value || ''
  return `${g('day')}.${g('month')} ${g('hour')}:${g('minute')}`
}
