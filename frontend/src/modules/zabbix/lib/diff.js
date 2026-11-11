// Сравнение «было → стало» как в GitLab: показываем только то, что поменялось,
// и подсвечиваем внутри строки сам изменённый кусок — вплоть до отдельных цифр.
//
// Два режима:
//   • короткое однострочное значение → две строки «−»/«+» целиком, подсвечен
//     изменённый кусок (status 0 → 1, порог 80 → 95);
//   • многострочное (скрипт, выражение, JSON) → только изменённые строки плюс
//     CONTEXT строк вокруг, остальное свёрнуто в «N строк без изменений».
//
// Алгоритм — LCS (наибольшая общая подпоследовательность) динамикой. Он
// квадратичный, поэтому на огромных значениях действуют лимиты, после которых
// сравнение грубеет (общие начало/конец + «всё остальное изменено»), но не
// подвешивает вкладку.

const CONTEXT = 2 // строк контекста вокруг изменения
const MAX_CELLS = 2_000_000 // предел таблицы LCS (строки × строки, токены × токены)

// --- Подготовка значений -------------------------------------------------------
// JSON одной строкой (условия действий, теги) разворачиваем по строкам с
// отступами: иначе вся разница утонула бы в одной огромной строке.
function pretty(s) {
  const t = s.trim()
  if (!(t.startsWith('{') || t.startsWith('['))) return s
  try {
    return JSON.stringify(JSON.parse(t), null, 2)
  } catch (e) {
    return s
  }
}

// Слова, числа, пробелы и одиночные знаки — по отдельности. Число — один токен:
// 80 → 95 подсветится как замена числа, а не россыпь символов.
const TOKEN = /\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu
function tokenize(s) {
  return s.match(TOKEN) || []
}

// --- LCS ------------------------------------------------------------------------
// Возвращает список операций [{ op: 'eq'|'del'|'add', a?, b? }] в порядке строк.
function lcsOps(a, b) {
  // Общие начало и конец отрезаем заранее: обычно меняется малая часть, и
  // таблица LCS строится только на середине.
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start++
  let endA = a.length
  let endB = b.length
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB-- }

  const head = []
  for (let i = 0; i < start; i++) head.push({ op: 'eq', a: a[i], b: b[i] })
  const tail = []
  for (let i = endA, j = endB; i < a.length; i++, j++) tail.push({ op: 'eq', a: a[i], b: b[j] })

  const ma = a.slice(start, endA)
  const mb = b.slice(start, endB)
  const n = ma.length
  const m = mb.length
  const mid = []

  if (n * m > MAX_CELLS) {
    // Слишком большая середина — без поиска общих кусков: всё «удалено и добавлено».
    for (const x of ma) mid.push({ op: 'del', a: x })
    for (const y of mb) mid.push({ op: 'add', b: y })
    return head.concat(mid, tail)
  }

  // dp[i][j] — длина LCS суффиксов ma[i:] и mb[j:]; плоский массив ради памяти.
  const w = m + 1
  const dp = new Uint32Array((n + 1) * w)
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * w + j] = ma[i] === mb[j]
        ? dp[(i + 1) * w + j + 1] + 1
        : Math.max(dp[(i + 1) * w + j], dp[i * w + j + 1])
    }
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (ma[i] === mb[j]) { mid.push({ op: 'eq', a: ma[i], b: mb[j] }); i++; j++ }
    else if (dp[(i + 1) * w + j] >= dp[i * w + j + 1]) mid.push({ op: 'del', a: ma[i++] })
    else mid.push({ op: 'add', b: mb[j++] })
  }
  while (i < n) mid.push({ op: 'del', a: ma[i++] })
  while (j < m) mid.push({ op: 'add', b: mb[j++] })
  return head.concat(mid, tail)
}

// --- Подсветка внутри строки -------------------------------------------------------
// Пара строк → сегменты для «−» и «+»: [{ text, hl }], hl=true — изменённый кусок.
function push(segs, text, hl) {
  if (!text) return
  const last = segs[segs.length - 1]
  if (last && last.hl === hl) last.text += text
  else segs.push({ text, hl })
}

// Общие начало/конец двух чисел — чтобы в «95» → «85» подсветить только «9»/«8».
function trimCommon(x, y) {
  let p = 0
  while (p < x.length && p < y.length && x[p] === y[p]) p++
  let s = 0
  while (s < x.length - p && s < y.length - p && x[x.length - 1 - s] === y[y.length - 1 - s]) s++
  return [p, s]
}

export function inlineDiff(oldLine, newLine) {
  const ops = lcsOps(tokenize(oldLine), tokenize(newLine))
  const del = []
  const add = []
  // Подряд идущие удаления/добавления копим блоком: один заменённый блок
  // сравниваем ещё и посимвольно (по общим краям).
  let bufDel = ''
  let bufAdd = ''
  // Посимвольно — только числа: «95 → 85» подсветит одну цифру, а слово
  // «включён → выключен» подсвечивается целиком (по буквам читалось бы как
  // «в[ключё]н»).
  const NUM = /^\d+$/
  const flush = () => {
    if (bufDel && bufAdd && NUM.test(bufDel) && NUM.test(bufAdd)) {
      const [p, s] = trimCommon(bufDel, bufAdd)
      push(del, bufDel.slice(0, p), false)
      push(del, bufDel.slice(p, bufDel.length - s), true)
      push(del, bufDel.slice(bufDel.length - s), false)
      push(add, bufAdd.slice(0, p), false)
      push(add, bufAdd.slice(p, bufAdd.length - s), true)
      push(add, bufAdd.slice(bufAdd.length - s), false)
    } else {
      push(del, bufDel, true)
      push(add, bufAdd, true)
    }
    bufDel = ''
    bufAdd = ''
  }
  for (const o of ops) {
    if (o.op === 'eq') {
      flush()
      push(del, o.a, false)
      push(add, o.b, false)
    } else if (o.op === 'del') bufDel += o.a
    else bufAdd += o.b
  }
  flush()
  return { del, add }
}

// --- Главная функция -------------------------------------------------------------------
// Результат — список строк для отрисовки:
//   { type: 'del'|'add'|'ctx', segs: [{text, hl}] }
//   { type: 'skip', count }   — свёрнутые неизменённые строки
export function diffValues(oldVal, newVal) {
  const a = pretty(oldVal ?? '')
  const b = pretty(newVal ?? '')
  const la = a.split('\n')
  const lb = b.split('\n')

  // Одна строка с обеих сторон — две строки целиком с подсветкой внутри.
  if (la.length === 1 && lb.length === 1) {
    const { del, add } = inlineDiff(a, b)
    return [{ type: 'del', segs: del }, { type: 'add', segs: add }]
  }

  // Многострочное: сначала по строкам, затем внутри заменённых пар — по словам.
  const ops = lcsOps(la, lb)
  const rows = []
  for (let k = 0; k < ops.length;) {
    if (ops[k].op === 'eq') {
      rows.push({ type: 'ctx', segs: [{ text: ops[k].a, hl: false }] })
      k++
      continue
    }
    // Блок изменений: удалённые строки, затем добавленные. Пары «удалена i-я /
    // добавлена i-я» считаем правкой строки и подсвечиваем в них слова.
    const dels = []
    const adds = []
    while (k < ops.length && ops[k].op !== 'eq') {
      if (ops[k].op === 'del') dels.push(ops[k].a)
      else adds.push(ops[k].b)
      k++
    }
    const pairs = Math.min(dels.length, adds.length)
    const delRows = []
    const addRows = []
    for (let i = 0; i < dels.length; i++) {
      if (i < pairs) {
        const { del, add } = inlineDiff(dels[i], adds[i])
        delRows.push({ type: 'del', segs: del })
        addRows.push({ type: 'add', segs: add })
      } else {
        delRows.push({ type: 'del', segs: [{ text: dels[i], hl: false }] })
      }
    }
    for (let i = pairs; i < adds.length; i++) addRows.push({ type: 'add', segs: [{ text: adds[i], hl: false }] })
    rows.push(...delRows, ...addRows)
  }

  // Неизменённые строки дальше CONTEXT от ближайшего изменения сворачиваем.
  const near = new Array(rows.length).fill(false)
  rows.forEach((r, idx) => {
    if (r.type === 'ctx') return
    for (let d = -CONTEXT; d <= CONTEXT; d++) {
      if (rows[idx + d]) near[idx + d] = true
    }
  })
  const out = []
  let skipped = 0
  rows.forEach((r, idx) => {
    if (r.type === 'ctx' && !near[idx]) { skipped++; return }
    if (skipped) { out.push({ type: 'skip', count: skipped }); skipped = 0 }
    out.push(r)
  })
  if (skipped) out.push({ type: 'skip', count: skipped })
  return out
}
