// СГЕНЕРИРОВАНО из src/lib/window.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

// Окно выборки логов. Два режима, задаётся window.mode:
//   'sliding' (по умолчанию) — от прошлого успешного прогона минус overlap,
//              с потолком maxHours. Не теряет события на стыке прогонов.
//   'fixed'   — каждый тик ровно последние lookbackMinutes. Проще для понимания,
//              но одно событие попадёт в несколько писем подряд (это ожидаемо
//              и honestly предупреждается в UI).

function lookbackMinutes(w) {
  const win = w || {};
  if (win.lookbackMinutes != null && win.lookbackMinutes !== '') {
    return Math.max(1, Number(win.lookbackMinutes) || 1);
  }
  const lookbackH = Number(win.lookbackHours != null ? win.lookbackHours : 24);
  return Math.max(1, lookbackH * 60);
}

function normalizeWindow(cfg) {
  if (!cfg || typeof cfg !== 'object') return cfg;
  if (!cfg.window || typeof cfg.window !== 'object') return cfg;
  const w = cfg.window;
  if (w.lookbackMinutes == null && w.lookbackHours != null) {
    w.lookbackMinutes = Number(w.lookbackHours) * 60;
  }
  return cfg;
}

function computeWindow(cfg, state, now) {
  const w = (cfg && cfg.window) || {};
  const mode = w.mode === 'fixed' ? 'fixed' : 'sliding';
  const lookbackMs = lookbackMinutes(w) * 60000;
  const to = new Date(now);

  if (mode === 'fixed') {
    return { from: new Date(now - lookbackMs).toISOString(), to: to.toISOString() };
  }

  const overlapMin = Number(w.overlapMinutes != null ? w.overlapMinutes : 2);
  const maxH = Number(w.maxHours != null ? w.maxHours : 48);
  const lastRaw = state && state.lastSuccessAt ? Date.parse(state.lastSuccessAt) : NaN;

  let from = Number.isNaN(lastRaw)
    ? new Date(now - lookbackMs)
    : new Date(lastRaw - overlapMin * 60000);

  const maxFrom = new Date(now - maxH * 3600000);
  if (from.getTime() < maxFrom.getTime()) from = maxFrom;

  return { from: from.toISOString(), to: to.toISOString() };
}

export { computeWindow, lookbackMinutes, normalizeWindow };
