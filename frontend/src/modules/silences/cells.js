// Конвертация выбора в сетке (ключи "mon-13") <-> окна {days, start, end}.
const pad = (n) => String(n).padStart(2, '0')

// Соседние часы дня -> непрерывные диапазоны [{start, end}] (end — час, эксклюзивно).
function hourRuns(hours) {
  const hs = [...hours].sort((a, b) => a - b)
  const runs = []
  let start = hs[0]
  let prev = hs[0]
  for (let i = 1; i <= hs.length; i++) {
    if (i < hs.length && hs[i] === prev + 1) {
      prev = hs[i]
      continue
    }
    runs.push({ start: `${pad(start)}:00`, end: `${pad((prev + 1) % 24)}:00` })
    if (i < hs.length) {
      start = hs[i]
      prev = hs[i]
    }
  }
  return runs
}

// Ячейки -> окна. Дни с ОДИНАКОВЫМ набором часов объединяем в одно окно
// {days: [...], start, end}, чтобы не плодить лишние silence: на одно окно
// шедулер ставит только ближайшее вхождение, а дальше — по мере окончания.
export function cellsToWindows(cells) {
  const byDay = {}
  for (const key of cells) {
    const i = key.lastIndexOf('-')
    const day = key.slice(0, i)
    const hour = +key.slice(i + 1)
    ;(byDay[day] = byDay[day] || []).push(hour)
  }
  // группируем дни по одинаковой «подписи» из их диапазонов часов
  const groups = {}
  for (const day in byDay) {
    const runs = hourRuns(byDay[day])
    const sig = runs.map((r) => `${r.start}-${r.end}`).join('|')
    if (!groups[sig]) groups[sig] = { days: [], runs }
    groups[sig].days.push(day)
  }
  const windows = []
  for (const sig in groups) {
    for (const r of groups[sig].runs) {
      windows.push({ days: groups[sig].days, start: r.start, end: r.end })
    }
  }
  return windows
}

// Окна -> ячейки (для предзаполнения сетки при редактировании).
export function windowsToCells(windows) {
  const cells = []
  for (const w of windows || []) {
    const sh = parseInt(w.start.split(':')[0], 10)
    let eh = parseInt(w.end.split(':')[0], 10)
    if (eh <= sh) eh = 24 // окно до полуночи / через полночь — берём до конца суток
    for (const d of w.days) {
      for (let h = sh; h < eh; h++) cells.push(`${d}-${h}`)
    }
  }
  return cells
}
