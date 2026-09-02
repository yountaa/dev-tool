// СГЕНЕРИРОВАНО из src/lib/schedule.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

// Когда движку запускать правило и когда можно слать письма.
//
// schedule.trigger:
//   'interval'   — раз в intervalMinutes (по умолчанию)
//   'dailyTimes' — в заданные моменты суток, times: ["09:00","18:00"]
//   'window'     — только в окно часов from..to по дням days, внутри — по интервалу
//
// sendWindow (необязательно, поверх любого триггера) — «письма только в эти часы».

function tzParts(nowMs, tz) {
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: tz || 'Europe/Moscow', weekday: 'short',
      hour: '2-digit', minute: '2-digit', hour12: false
    }).formatToParts(new Date(nowMs));
    const get = (t) => (parts.find((p) => p.type === t) || {}).value;
    const wd = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 }[get('weekday')];
    let hour = get('hour');
    // Intl иногда отдаёт 24 в полночь — сравниваем с type=time как 00:xx.
    if (hour === '24') hour = '00';
    const minute = get('minute');
    return { wd: wd, hm: String(hour).padStart(2, '0') + ':' + String(minute).padStart(2, '0') };
  } catch (_) {
    return null;
  }
}

function normHm(s) {
  const m = String(s || '').match(/^(\d{1,2}):(\d{2})/);
  if (!m) return '00:00';
  let h = Number(m[1]);
  if (h === 24) h = 0;
  return String(h).padStart(2, '0') + ':' + m[2];
}

function inDayWindow(p, days, from, to) {
  const list = (Array.isArray(days) && days.length ? days : [0, 1, 2, 3, 4, 5, 6]).map(Number);
  if (list.indexOf(p.wd) < 0) return false;
  const f = normHm(from || '00:00');
  const t = normHm(to || '23:59');
  const hm = normHm(p.hm);
  return f <= t ? (hm >= f && hm <= t) : (hm >= f || hm <= t);
}

// Наступил ли момент запуска. lastRunAt — из состояния, now — мс.
function isScheduledNow(cfg, lastRunAtIso, now) {
  const s = (cfg && cfg.schedule) || {};
  const trigger = s.trigger || 'interval';
  const lastMs = lastRunAtIso ? Date.parse(lastRunAtIso) : NaN;
  const sinceLast = Number.isNaN(lastMs) ? Infinity : now - lastMs;

  if (trigger === 'dailyTimes') {
    const tz = s.timezone || (cfg.notify && cfg.notify.timezone) || 'Europe/Moscow';
    const p = tzParts(now, tz);
    if (!p) return true;
    const times = Array.isArray(s.times) ? s.times : [];
    // Срабатывает, если текущее HH:MM совпало с одним из times и в эту минуту
    // ещё не запускались (защита от двойного тика внутри минуты).
    if (times.indexOf(p.hm) < 0) return false;
    return sinceLast >= 60000; // не чаще раза в минуту на один и тот же момент
  }

  if (trigger === 'window') {
    const tz = s.timezone || (cfg.notify && cfg.notify.timezone) || 'Europe/Moscow';
    const p = tzParts(now, tz);
    if (!p) return true;
    if (!inDayWindow(p, s.days, s.from, s.to)) return false;
    const interval = Number(s.intervalMinutes) || 5;
    return sinceLast >= interval * 60000;
  }

  // interval
  const interval = Number(s.intervalMinutes) || 5;
  return sinceLast >= interval * 60000;
}

// Можно ли сейчас слать письмо (sendWindow поверх триггера).
function isWithinSendWindow(sw, now) {
  if (!sw || !sw.enabled) return true;
  const p = tzParts(now, sw.timezone || 'Europe/Moscow');
  if (!p) return true;
  return inDayWindow(p, sw.days, sw.from, sw.to);
}

export { isScheduledNow, isWithinSendWindow, tzParts, inDayWindow };
