/** Устаревший trigger window → interval (часы отправки — через sendWindow). */
export function normalizeSchedule(cfg) {
  if (!cfg || typeof cfg !== 'object') return cfg
  if (!cfg.schedule || typeof cfg.schedule !== 'object') return cfg
  if (cfg.schedule.trigger === 'window') {
    cfg.schedule.trigger = 'interval'
    if (!cfg.schedule.intervalMinutes) cfg.schedule.intervalMinutes = 15
  }
  return cfg
}
