const UNITS = [
  { id: 'minutes', label: 'мин', toMinutes: (n) => n, fromMinutes: (m) => m / 1 },
  { id: 'hours', label: 'ч', toMinutes: (n) => n * 60, fromMinutes: (m) => m / 60 },
  { id: 'days', label: 'дн', toMinutes: (n) => n * 1440, fromMinutes: (m) => m / 1440 },
]

export function storageToMinutes(value, storage = 'minutes') {
  const n = Number(value) || 0
  return storage === 'hours' ? n * 60 : n
}

export function minutesToStorage(minutes, storage = 'minutes') {
  const m = Number(minutes) || 0
  return storage === 'hours' ? m / 60 : m
}

/** Подобрать удобную единицу для отображения. */
export function pickDisplayUnit(totalMinutes) {
  const m = Math.max(0, Number(totalMinutes) || 0)
  if (m >= 1440 && m % 1440 === 0) return 'days'
  if (m >= 60 && m % 60 === 0) return 'hours'
  return 'minutes'
}

export function partsFromStorage(value, storage = 'minutes') {
  const totalMinutes = storageToMinutes(value, storage)
  const unit = pickDisplayUnit(totalMinutes)
  const u = UNITS.find((x) => x.id === unit) || UNITS[0]
  return { amount: u.fromMinutes(totalMinutes), unit }
}

export function storageFromParts(amount, unit, storage = 'minutes') {
  const u = UNITS.find((x) => x.id === unit) || UNITS[0]
  const minutes = u.toMinutes(Number(amount) || 0)
  return minutesToStorage(minutes, storage)
}

/** Человекочитаемая длительность для summary. */
export function formatDurationMinutes(minutes) {
  const m = Math.max(0, Number(minutes) || 0)
  if (m >= 1440 && m % 1440 === 0) {
    const d = m / 1440
    return d === 1 ? '1 день' : `${d} дн`
  }
  if (m >= 60 && m % 60 === 0) {
    const h = m / 60
    return h === 1 ? '1 час' : `${h} ч`
  }
  return m === 1 ? '1 минуту' : `${m} мин`
}

export { UNITS }
