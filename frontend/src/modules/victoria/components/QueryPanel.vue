<script>
// Кэш имён метрик для автодополнения: ОБЩИЙ для всех панелей (обычный <script>
// выполняется один раз на модуль, в отличие от <script setup> — тот на каждую
// панель). Без него каждое переключение вкладки заново тянуло бы список из
// тысяч имён.
const namesCache = new Map() // 'env|tenant' -> { at, names }
const labelNamesCache = new Map()  // 'env|tenant' -> { at, names } — имена лейблов
const labelValuesCache = new Map() // 'env|tenant|label' -> { at, values } — значения лейбла
const NAMES_TTL = 5 * 60 * 1000
</script>

<script setup>
// Панель запросов «как в vmui» (VictoriaMetrics): НЕСКОЛЬКО выражений
// PromQL/MetricsQL в одной панели. «+ Добавить запрос» добавляет поле выражения
// (аналог Add Query в vmui), и данные ВСЕХ запросов показываются вместе:
// Graph — серии всех запросов на одном графике, снизу легенда с метрикой и её
// значением; Table — общая таблица (с номером запроса, когда их несколько);
// JSON — сырые ответы. Автодополнение живёт у поля, которое в фокусе.
// Бэкенд — тонкий прокси, отдаёт Prometheus-JSON.
import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import Skeleton from '../../../shared/Skeleton.vue'
import { urlParams, setUrlParams } from '../../../shared/urlstate.js'
import { victoriaApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null }, // мультитенантный VM; иначе null
})

// Лимиты, чтобы тяжёлый запрос не подвесил браузер.
const MAX_TABLE_ROWS = 1000  // строк в таблице (instant), суммарно по всем запросам
const MAX_SERIES = 20        // линий на графике (range), суммарно по всем запросам
const MAX_METRICS = 10000    // имён метрик в автокомплите

// --- Запросы: несколько выражений в одной панели -------------------------------
let qseq = 0
const queries = ref([{ id: ++qseq, text: '' }]) // поля выражений; всегда хотя бы одно
const activeIdx = ref(0) // чьё поле в фокусе — к нему привязаны подсказки/автопары
const tas = ref([])      // textarea каждого поля (по индексу строки)
const hasExpr = computed(() => queries.value.some((q) => q.text.trim()))
// Был ли последний запуск мультизапросным — тогда показываем бейджи q1/q2 у серий.
const multiRun = ref(false)

function setTa(el, i) { if (el) tas.value[i] = el }
function activeTa() { return tas.value[activeIdx.value] }
function activeText() { return queries.value[activeIdx.value]?.text ?? '' }
function setActiveText(t) { const q = queries.value[activeIdx.value]; if (q) q.text = t }

function addQuery() {
  queries.value.push({ id: ++qseq, text: '' })
  nextTick(() => {
    const el = tas.value[queries.value.length - 1]
    if (el) el.focus()
  })
}
function removeQuery(i) {
  // Последнее поле не убираем — всегда хотя бы один запрос.
  if (queries.value.length <= 1) return
  queries.value.splice(i, 1)
  tas.value.splice(i, 1)
  showSug.value = false
  if (activeIdx.value >= queries.value.length) activeIdx.value = queries.value.length - 1
  // Как в vmui: убрал запрос — график/таблица сразу пересчитываются без него.
  if (hasExpr.value && !loading.value) run()
}

const mode = ref('graph') // 'graph' (range) | 'table' (instant) | 'json' (instant, сырой JSON)

// --- Диапазон времени --------------------------------------------------------
// Graph: явные «От»/«До» (datetime-local). Table: «Время расчёта» (instant),
// пусто = сейчас. Быстрые пресеты просто выставляют «От»/«До» относительно now.
function toLocalInput(unixSec) {
  const d = new Date(unixSec * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}
function fromLocalInput(str) {
  if (!str) return null
  const t = new Date(str).getTime()
  return isNaN(t) ? null : Math.floor(t / 1000)
}
const nowSec = () => Math.floor(Date.now() / 1000)

const fromStr = ref(toLocalInput(nowSec() - 3600)) // «От» для Graph
const toStr = ref(toLocalInput(nowSec()))          // «До» для Graph
const evalStr = ref('')                            // «Время расчёта» для Table (пусто = сейчас)

// Восстановление вида из URL (ссылка от коллеги): выражения, режим, время.
// Только если ссылка про ЭТОТ кластер/тенант — иначе панель стартует чистой.
// (env/tenant в URL пишет VictoriaModule до создания панели.)
const init = urlParams()
if (init.get('env') === props.env && (init.get('tenant') || null) === (props.tenant || null)) {
  const qs = init.getAll('q').filter((s) => s.trim())
  if (qs.length) queries.value = qs.map((text) => ({ id: ++qseq, text }))
  if (['graph', 'table', 'json'].includes(init.get('mode'))) mode.value = init.get('mode')
  if (init.get('from')) fromStr.value = init.get('from')
  if (init.get('to')) toStr.value = init.get('to')
  if (init.get('eval')) evalStr.value = init.get('eval')
}
const loading = ref(false)
const errors = ref([]) // ошибки по запросам («Запрос N: …», когда запросов несколько)
const meta = ref('') // строка-сводка под запросом (серий, время расчёта)
const truncated = ref('') // предупреждение об обрезке результата

const instant = ref([]) // [{ qi, metric, value }] для режима Table (уже обрезано до MAX_TABLE_ROWS)
const rawJson = ref('')  // сырой JSON ответа VM для режима JSON (как вкладка JSON в vmui)

// Колонки таблицы = объединение имён лейблов по всем сериям (как таблица в vmui:
// каждый лейбл — отдельный столбец). __name__ показываем отдельной первой колонкой.
const hasMetricName = computed(() => instant.value.some((r) => r.metric && r.metric.__name__))
const columns = computed(() => {
  const keys = new Set()
  for (const row of instant.value) {
    for (const k of Object.keys(row.metric || {})) {
      if (k !== '__name__') keys.add(k)
    }
  }
  return [...keys].sort()
})

// --- Автодополнение ----------------------------------------------------------
const metricNames = ref([]) // имена метрик (__name__)
const labelNames = ref([])  // имена лейблов (подсказки внутри {})
const labelValues = ref([]) // значения активного лейбла (подсказки после label=")
const suggestions = ref([]) // видимый список подсказок
const sugIndex = ref(0)
const showSug = ref(false)

// Частые функции PromQL/MetricsQL — предлагаем вместе с именами метрик.
const FUNCS = [
  'rate(', 'irate(', 'increase(', 'delta(', 'deriv(', 'predict_linear(',
  'sum(', 'sum by (', 'avg(', 'avg by (', 'min(', 'max(', 'count(', 'count_values(',
  'topk(', 'bottomk(', 'quantile(', 'histogram_quantile(', 'stddev(', 'stdvar(',
  'sum_over_time(', 'avg_over_time(', 'max_over_time(', 'min_over_time(',
  'count_over_time(', 'quantile_over_time(', 'last_over_time(',
  'abs(', 'ceil(', 'floor(', 'round(', 'clamp(', 'clamp_min(', 'clamp_max(',
  'label_replace(', 'label_join(', 'absent(', 'absent_over_time(', 'changes(',
  'resets(', 'time(', 'timestamp(', 'vector(', 'scalar(', 'sort(', 'sort_desc(',
]

const PRESETS = [['5m', 300], ['15m', 900], ['1h', 3600], ['6h', 21600], ['24h', 86400], ['7d', 604800]]
// Пресет просто выставляет «От»/«До» относительно текущего момента.
function setPreset(sec) {
  toStr.value = toLocalInput(nowSec())
  fromStr.value = toLocalInput(nowSec() - sec)
  if (hasExpr.value && !loading.value) run()
}
// Шаг «Время расчёта» стрелками ‹ › — на 5 минут; пусто трактуем как «сейчас».
function stepEval(deltaSec) {
  const base = fromLocalInput(evalStr.value) ?? nowSec()
  evalStr.value = toLocalInput(base + deltaSec)
  if (hasExpr.value && !loading.value) run()
}

// Палитра линий графика: первая — фирменный коралл VM, дальше контрастные, но
// не кислотные соседи (подобраны под тёплый фон вкладки и светлую тему).
const COLORS = ['#ff7a59', '#56b8e6', '#6fcb85', '#f0b653', '#a98ff0', '#ef6f9d', '#4ed0b4', '#8f9ff2', '#d3c356', '#e08e77']

const chartEl = ref(null)
const chartSeries = ref([]) // [{ qi, label, color, value, show }] — легенда под графиком
let chart = null
let ro = null
let inflight = null // AbortController текущего запуска (общий на все запросы; «Отмена»)
let seriesMeta = [] // [{ metric, qi }] серий графика (для аккуратного тултипа по наведению)

let metricsTried = false
async function loadMetrics() {
  metricsTried = true
  const key = props.env + '|' + (props.tenant || '')
  const hit = namesCache.get(key)
  if (hit && Date.now() - hit.at < NAMES_TTL) { metricNames.value = hit.names; return }
  try {
    // limit просим ещё у VM — не гоняем по сети сотни тысяч имён ради подсказок.
    const r = await victoriaApi.labelValues(props.env, '__name__', props.tenant, MAX_METRICS)
    metricNames.value = Array.isArray(r.data) ? r.data.slice(0, MAX_METRICS) : []
    namesCache.set(key, { at: Date.now(), names: metricNames.value })
  } catch (e) {
    metricNames.value = [] // подсказок не будет, но поле работает
  }
}
// Имена лейблов (для подсказок внутри {}) — грузим лениво, кэш общий на env.
async function loadLabelNames() {
  const key = props.env + '|' + (props.tenant || '')
  const hit = labelNamesCache.get(key)
  if (hit && Date.now() - hit.at < NAMES_TTL) { labelNames.value = hit.names; return }
  try {
    const r = await victoriaApi.labels(props.env, props.tenant)
    labelNames.value = Array.isArray(r.data) ? r.data : []
    labelNamesCache.set(key, { at: Date.now(), names: labelNames.value })
  } catch (e) {
    labelNames.value = []
  }
}

// Значения конкретного лейбла (для подсказок после label="…). Кэш на (env, label).
async function loadLabelValues(label) {
  const key = props.env + '|' + (props.tenant || '') + '|' + label
  const hit = labelValuesCache.get(key)
  if (hit && Date.now() - hit.at < NAMES_TTL) { labelValues.value = hit.values; return }
  try {
    const r = await victoriaApi.labelValues(props.env, label, props.tenant, 2000)
    const vals = Array.isArray(r.data) ? r.data : []
    labelValuesCache.set(key, { at: Date.now(), values: vals })
    labelValues.value = vals
  } catch (e) {
    labelValues.value = []
  }
}

onMounted(() => {
  loadMetrics()
  nextTick(autosizeAll)
  if (hasExpr.value) run() // выражения пришли из ссылки — сразу показываем результат
})

// Поле запроса растёт под содержимое: сбрасываем высоту и подгоняем под scrollHeight
// (с потолком — дальше внутренняя прокрутка, чтобы огромный запрос не занял экран).
const EXPR_MAX_H = 320
function autosize(i) {
  const el = tas.value[i]
  if (!el) return
  // Схлопываем до 0 перед замером: так scrollHeight = реальная высота содержимого
  // (сброс в 'auto' на первом кадре иногда возвращал завышенное значение → поле
  // растягивалось на пустом запросе). CSS min-height держит нижнюю границу.
  el.style.height = '0px'
  const full = el.scrollHeight
  el.style.height = Math.min(full, EXPR_MAX_H) + 'px'
  // Полоса прокрутки — только когда упёрлись в потолок. Иначе (в т.ч. пустое поле)
  // прячем: при height=scrollHeight скролл не нужен, но браузер иногда рисовал его.
  el.style.overflowY = full > EXPR_MAX_H ? 'auto' : 'hidden'
}
function autosizeAll() { queries.value.forEach((_, i) => autosize(i)) }

function onInput(i) { activeIdx.value = i; refreshSug(); autosize(i) }
function onFocus(i) { activeIdx.value = i; refreshSug() }

// Смена режима — перезапускаем запрос (как в Prometheus). При уходе С ГРАФИКА в
// Table/JSON переносим конец выбранного диапазона (До) во «Время расчёта», чтобы
// instant показал метрики за то же время, что было на графике (в т.ч. после того,
// как диапазон выбрали мышью прямо по графику).
watch(mode, (now, prev) => {
  if ((now === 'table' || now === 'json') && prev === 'graph') evalStr.value = toStr.value
  setUrlParams({ mode: now }) // режим в URL — даже если запрос ещё не выполнялся
  if (hasExpr.value && !loading.value) run()
})

onBeforeUnmount(() => {
  if (inflight) inflight.abort()
  if (ro) ro.disconnect()
  if (chart) chart.destroy()
})

// Токен под курсором (последовательность из букв/цифр/_/:) — то, что дополняем.
function currentToken() {
  const el = activeTa()
  const pos = el ? el.selectionStart : activeText().length
  const text = activeText()
  let start = pos
  while (start > 0 && /[A-Za-z0-9_:]/.test(text[start - 1])) start--
  return { token: text.slice(start, pos), start, end: pos }
}

// Диапазон текста, который заменит выбранная подсказка (для pick). Для значений
// лейбла он шире, чем «слово» currentToken() — значение может содержать . : / - .
const sugRange = ref({ start: 0, end: 0 })

// Что уместно подсказывать в позиции курсора: имя метрики/функция, имя лейбла
// (внутри {}) или значение лейбла (после label="). Разбираем по тексту ДО курсора.
// Внутри {} смотрим ТЕКУЩУЮ клаузу — от последней запятой (или самой {) до курсора,
// чтобы после завершённого матчера (job="node") не сыпать невпопад именами лейблов.
function suggestContext() {
  const el = activeTa()
  const pos = el ? el.selectionStart : activeText().length
  const text = activeText().slice(0, pos)
  const open = text.lastIndexOf('{')
  const close = text.lastIndexOf('}')
  if (open <= close) return { kind: 'metric' }         // не внутри {}
  const seg = text.slice(open + 1)                      // содержимое {} до курсора
  const clause = seg.slice(seg.lastIndexOf(',') + 1)    // текущая пара label=value
  // значение лейбла: label(=|!=|=~|!~)"…  (без закрывающей кавычки)
  const mv = clause.match(/([A-Za-z_]\w*)\s*(=~|!~|!=|=)\s*"([^"]*)$/)
  if (mv) return { kind: 'value', label: mv[1], typed: mv[3], start: pos - mv[3].length, end: pos }
  // имя лейбла: клауза — это только (частичный) идентификатор, оператора ещё нет
  const ml = clause.match(/^\s*([A-Za-z_]\w*)?$/)
  if (ml) { const typed = ml[1] || ''; return { kind: 'label', typed, start: pos - typed.length, end: pos } }
  // завершённый матчер / закрытая кавычка / мусор — не подсказываем ничего
  return { kind: 'none' }
}

// Отфильтровать пул по введённому токену — ТОЛЬКО по началу имени (startsWith), не
// по подстроке: пишешь `up` → видишь `up`, `uptime`, `up_…`, а не всё, где `up` где-то
// в середине. Пустой токен — показываем весь пул (актуально для лейблов/значений).
// Уже вписанное целиком не предлагаем (item === token). Нет подходящих — прячем список.
function filterSug(pool, token, range) {
  const q = (token || '').toLowerCase()
  const out = []
  for (const item of pool) {
    if (item === token) continue           // ровно то, что уже набрано — лишнее
    if (!q || String(item).toLowerCase().startsWith(q)) out.push(item)
    if (out.length >= 80) break
  }
  suggestions.value = out
  sugIndex.value = 0
  sugRange.value = range
  showSug.value = out.length > 0
}

async function refreshSug() {
  if (!metricsTried) loadMetrics() // подстраховка, если onMounted не успел/не сработал
  const ctx = suggestContext()

  if (ctx.kind === 'none') { showSug.value = false; suggestions.value = []; return }

  if (ctx.kind === 'value') {
    await loadLabelValues(ctx.label)              // из кэша — мгновенно
    const now = suggestContext()                  // контекст мог измениться за await
    if (now.kind !== 'value' || now.label !== ctx.label) return
    filterSug(labelValues.value, now.typed, { start: now.start, end: now.end })
    return
  }
  if (ctx.kind === 'label') {
    await loadLabelNames()                        // из кэша — мгновенно
    const now = suggestContext()
    if (now.kind !== 'label') return              // контекст мог измениться за await
    filterSug(labelNames.value, now.typed, { start: now.start, end: now.end })
    return
  }
  // имя метрики / функция — как раньше, но не сыплем всем списком на пустой токен
  const { token, start, end } = currentToken()
  if (token.length < 1) { showSug.value = false; suggestions.value = []; return }
  filterSug([...FUNCS, ...metricNames.value], token, { start, end })
}

function pick(item) {
  const { start, end } = sugRange.value
  const text = activeText()
  const before = text.slice(0, start)
  const after = text.slice(end)
  setActiveText(before + item + after)
  showSug.value = false
  nextTick(() => {
    const pos = (before + item).length
    const el = activeTa()
    if (el) { el.focus(); el.setSelectionRange(pos, pos) }
    autosize(activeIdx.value)
  })
}

function setCaret(pos) {
  const el = activeTa()
  if (!el) return
  el.focus()
  el.setSelectionRange(pos, pos)
}

// Авто-пары скобок/кавычек в поле запроса (как в редакторах кода):
//   {  → {}   и   "  → ""   (курсор встаёт МЕЖДУ ними);
//   печать «поверх»: набрал закрывающий, а он уже стоит справа — просто шагаем через
//   него (второй раз не подставляется);
//   Backspace между пустой парой удаляет обе половинки (стёр — снова пишешь сам).
// Возвращает true, если событие обработано (дальше в onKeydown не идём).
function autoPair(e) {
  const el = activeTa()
  if (!el) return false
  const s = el.selectionStart, en = el.selectionEnd
  const text = activeText()
  const next = text[en] || ''

  // печать поверх закрывающего — не плодим второй символ
  if ((e.key === '}' && next === '}') || (e.key === '"' && next === '"')) {
    e.preventDefault()
    setCaret(en + 1)
    return true
  }
  // авто-пара на открытие (только когда нет выделения)
  const close = e.key === '{' ? '}' : e.key === '"' ? '"' : null
  if (close && s === en) {
    e.preventDefault()
    setActiveText(text.slice(0, s) + e.key + close + text.slice(en))
    nextTick(() => { setCaret(s + 1); refreshSug(); autosize(activeIdx.value) })
    return true
  }
  // Backspace между пустой парой {}/"" — сносим обе
  if (e.key === 'Backspace' && s === en && s > 0) {
    const prev = text[s - 1]
    if ((prev === '{' && next === '}') || (prev === '"' && next === '"')) {
      e.preventDefault()
      setActiveText(text.slice(0, s - 1) + text.slice(en + 1))
      nextTick(() => { setCaret(s - 1); refreshSug(); autosize(activeIdx.value) })
      return true
    }
  }
  return false
}

function onKeydown(i, e) {
  activeIdx.value = i
  if (autoPair(e)) return
  if (showSug.value && suggestions.value.length) {
    if (e.key === 'ArrowDown') { e.preventDefault(); sugIndex.value = (sugIndex.value + 1) % suggestions.value.length; return }
    if (e.key === 'ArrowUp') { e.preventDefault(); sugIndex.value = (sugIndex.value - 1 + suggestions.value.length) % suggestions.value.length; return }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(suggestions.value[sugIndex.value]); return }
    if (e.key === 'Escape') { e.preventDefault(); showSug.value = false; return }
  }
  // Enter выполняет запрос (как в Prometheus), Shift+Enter — перенос строки.
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); run() }
}

// --- Выполнение --------------------------------------------------------------
function labelsStr(m) {
  const name = m.__name__ || ''
  const rest = Object.entries(m).filter(([k]) => k !== '__name__').map(([k, v]) => `${k}="${v}"`).join(', ')
  return rest ? `${name}{${rest}}` : name
}

// Непустые выражения на запуск: qi — номер поля (для бейджей q1/q2 и ошибок).
function runItems() {
  return queries.value
    .map((q, i) => ({ qi: i, expr: q.text.trim() }))
    .filter((x) => x.expr)
}

async function run() {
  const items = runItems()
  if (!items.length) return
  // Выражения/режим/время — в URL: ссылка на страницу воспроизводит этот запрос.
  setUrlParams({
    q: items.map((x) => x.expr),
    mode: mode.value,
    from: mode.value === 'graph' ? fromStr.value : null,
    to: mode.value === 'graph' ? toStr.value : null,
    eval: mode.value !== 'graph' ? evalStr.value : null,
  })
  showSug.value = false
  if (inflight) inflight.abort()        // отменяем предыдущий, если ещё летит
  const controller = new AbortController()
  inflight = controller
  loading.value = true
  errors.value = []
  meta.value = ''
  truncated.value = ''
  multiRun.value = items.length > 1
  const t0 = performance.now()
  try {
    // Graph — range-запросы; Table и JSON — instant (мгновенное значение).
    const n = mode.value === 'graph'
      ? await runRange(items, controller.signal)
      : await runInstant(items, controller.signal)
    meta.value = `${n} серий · ${Math.round(performance.now() - t0)} мс`
  } catch (e) {
    if (e.name !== 'AbortError') errors.value = [e.message]  // отмену пользователем ошибкой не считаем
  } finally {
    // сбрасываем состояние только если это всё ещё НАШ запуск (новый мог стартовать)
    if (inflight === controller) { loading.value = false; inflight = null }
  }
}

// Отмена долгого запроса — рвём fetch через AbortController (общий на все запросы).
function cancel() { if (inflight) inflight.abort() }

// Все выражения летят ПАРАЛЛЕЛЬНО; упавшее не роняет остальные — по нему своя
// ошибка «Запрос N: …», успешные рисуем. Отмена (Abort) пробрасывается наверх.
async function settle(items, promises) {
  const settled = await Promise.allSettled(promises)
  const ok = []
  settled.forEach((s, k) => {
    if (s.status === 'rejected') {
      if (s.reason && s.reason.name === 'AbortError') throw s.reason
      const prefix = items.length > 1 ? `Запрос ${items[k].qi + 1}: ` : ''
      errors.value.push(prefix + (s.reason?.message ?? String(s.reason)))
    } else {
      ok.push({ qi: items[k].qi, expr: items[k].expr, r: s.value })
    }
  })
  return ok
}

async function runInstant(items, signal) {
  destroyChart()
  const time = fromLocalInput(evalStr.value)
  const ok = await settle(items, items.map(({ expr }) =>
    victoriaApi.query(props.env, expr, time, props.tenant, signal)))
  // JSON: один запрос — сырой ответ как есть (как вкладка JSON в vmui);
  // несколько — массив пар {query, response}, чтобы было видно, чей это ответ.
  if (items.length > 1) {
    rawJson.value = JSON.stringify(ok.map(({ expr, r }) => ({ query: expr, response: r })), null, 2)
  } else {
    rawJson.value = ok.length ? JSON.stringify(ok[0].r, null, 2) : ''
  }
  const rows = []
  for (const { qi, r } of ok) {
    const rt = r.data?.resultType
    if (rt === 'scalar' || rt === 'string') {
      // скаляр/строка — одна «серия» без лейблов, значение во второй позиции.
      rows.push({ qi, metric: { __name__: rt }, value: r.data.result?.[1] ?? '' })
    } else {
      for (const s of r.data?.result || []) {
        rows.push({ qi, metric: s.metric || {}, value: s.value ? s.value[1] : '' })
      }
    }
  }
  instant.value = rows.slice(0, MAX_TABLE_ROWS)
  if (rows.length > MAX_TABLE_ROWS) truncated.value = `показаны первые ${MAX_TABLE_ROWS} из ${rows.length} серий`
  return rows.length
}

async function runRange(items, signal) {
  instant.value = []
  const end = fromLocalInput(toStr.value) ?? nowSec()
  const start = fromLocalInput(fromStr.value) ?? (end - 3600)
  if (start >= end) throw new Error('«От» должно быть раньше «До»')
  const step = Math.max(1, Math.floor((end - start) / 400))
  const ok = await settle(items, items.map(({ expr }) =>
    victoriaApi.queryRange(props.env, expr, start, end, step, props.tenant, signal)))
  // Серии ВСЕХ запросов — на один общий график (как в vmui).
  const merged = []
  for (const { qi, r } of ok) {
    for (const s of r.data?.result || []) merged.push({ qi, metric: s.metric || {}, values: s.values || [] })
  }
  await drawChart(merged.slice(0, MAX_SERIES))
  if (merged.length > MAX_SERIES) truncated.value = `на графике первые ${MAX_SERIES} из ${merged.length} серий`
  return merged.length
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

// Плагин-тултип: вместо гигантской легенды показываем по наведению ближайшую к
// курсору серию — цвет, ПОЛНОЕ имя с лейблами, значение и время (+ номер запроса,
// когда запросов несколько).
function tooltipPlugin() {
  let tip
  return {
    hooks: {
      init(u) {
        tip = document.createElement('div')
        tip.className = 'qe-tip'
        tip.style.display = 'none'
        u.over.appendChild(tip)
      },
      setCursor(u) {
        const { idx, left, top } = u.cursor
        if (idx == null || left == null || left < 0) { tip.style.display = 'none'; return }
        // ближайшая по вертикали серия в точке idx
        let best = -1, bestDist = Infinity, bestVal = null
        for (let i = 1; i < u.series.length; i++) {
          const v = u.data[i][idx]
          if (v == null || isNaN(v)) continue
          const d = Math.abs(u.valToPos(v, 'y') - top)
          if (d < bestDist) { bestDist = d; best = i; bestVal = v }
        }
        if (best < 0) { tip.style.display = 'none'; return }
        const s = u.series[best]
        const t = u.data[0][idx]
        // Имя метрики и лейблы — по отдельности: имя строкой сверху, каждый лейбл
        // отдельной строкой key=value. Так читается даже при десятке лейблов.
        const sm = seriesMeta[best - 1] || { metric: {}, qi: 0 }
        const metric = sm.metric
        const name = metric.__name__ || s.label || 'значение'
        const qBadge = multiRun.value ? `<span class="qe-tip-qn">q${sm.qi + 1}</span>` : ''
        const labelRows = Object.entries(metric)
          .filter(([k]) => k !== '__name__')
          .map(([k, v]) => `<div class="qe-tip-row"><span class="qe-tip-k">${escapeHtml(k)}</span><span class="qe-tip-val">${escapeHtml(v)}</span></div>`)
          .join('')
        tip.innerHTML =
          `<div class="qe-tip-h"><span class="qe-tip-dot" style="background:${s.stroke}"></span><span class="qe-tip-name">${escapeHtml(name)}</span>${qBadge}</div>` +
          (labelRows ? `<div class="qe-tip-labels">${labelRows}</div>` : '') +
          `<div class="qe-tip-foot"><span class="qe-tip-v">${bestVal}</span><span class="qe-tip-t">${new Date(t * 1000).toLocaleString()}</span></div>`
        tip.style.display = 'block'
        const w = u.over.clientWidth
        const tw = tip.offsetWidth || 340
        let lft = left + 14
        if (lft + tw > w) lft = left - tw - 14  // не вылезать за правый край
        tip.style.left = Math.max(0, lft) + 'px'
        tip.style.top = Math.max(0, top - 10) + 'px'
      },
      // Выделение мышью по графику (drag) = выбор диапазона: подставляем его в
      // «От»/«До» вверху и перезапрашиваем — так шаг пересчитывается под новое окно,
      // а поля времени показывают ровно то, что выделили. Микродвижение = не зум.
      setSelect(u) {
        const sel = u.select
        if (!sel || sel.width < 6) return
        const min = u.posToVal(sel.left, 'x')
        const max = u.posToVal(sel.left + sel.width, 'x')
        if (!(max > min)) return
        fromStr.value = toLocalInput(Math.floor(min))
        toStr.value = toLocalInput(Math.ceil(max))
        u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false) // снять выделение без события
        if (!loading.value) run()
      },
    },
  }
}

// Последнее непустое значение серии (для легенды) — как в vmui/Grafana.
function lastValue(s) {
  const vals = s.values || []
  for (let i = vals.length - 1; i >= 0; i--) {
    if (!isNaN(parseFloat(vals[i][1]))) return vals[i][1]
  }
  return ''
}

async function drawChart(series) {
  destroyChart()
  if (!series.length) return
  // Ось X строим из РЕАЛЬНЫХ меток времени, которые вернул VM (объединение по всем
  // сериям), а не из start+k·step: VM выравнивает точки range-ответа по своей сетке
  // шага (кратной step), и при широком окне они не совпадали с нашим start+k·step —
  // выборка по map.has(t) давала сплошь null, и график «пропадал».
  const tsSet = new Set()
  for (const s of series) for (const p of (s.values || [])) tsSet.add(p[0])
  const timeline = [...tsSet].sort((a, b) => a - b)
  if (!timeline.length) return

  const data = [timeline]
  const uSeries = [{}]
  series.forEach((s, i) => {
    const map = new Map((s.values || []).map(([ts, v]) => [ts, parseFloat(v)]))
    data.push(timeline.map((t) => (map.has(t) ? map.get(t) : null)))
    uSeries.push({
      label: labelsStr(s.metric) || `series ${i + 1}`,
      stroke: COLORS[i % COLORS.length],
      width: 1.6,
      points: { show: false },
      spanGaps: true,
    })
  })
  // Легенда под графиком: цвет + полное имя серии + последнее значение (и номер
  // запроса, когда их несколько), с управлением видимостью.
  chartSeries.value = series.map((s, i) => ({
    qi: s.qi ?? 0,
    label: labelsStr(s.metric) || `series ${i + 1}`,
    color: COLORS[i % COLORS.length],
    value: lastValue(s),
    show: true,
  }))
  seriesMeta = series.map((s) => ({ metric: s.metric || {}, qi: s.qi ?? 0 })) // для тултипа

  await nextTick()
  const width = chartEl.value?.clientWidth || 900
  const axisColor = getCss('--text-mute') || '#888'
  const gridColor = getCss('--border-soft') || 'rgba(128,128,128,0.2)'
  chart = new uPlot(
    {
      width,
      height: 380,
      series: uSeries,
      axes: [
        { stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor } },
        { stroke: axisColor, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor } },
      ],
      legend: { show: false },       // вместо громоздкой легенды — тултип по наведению
      cursor: { focus: { prox: 30 } },
      plugins: [tooltipPlugin()],
    },
    data,
    chartEl.value,
  )
  if (ro) ro.disconnect()
  ro = new ResizeObserver(() => {
    if (chart && chartEl.value) chart.setSize({ width: chartEl.value.clientWidth, height: 380 })
  })
  ro.observe(chartEl.value)
}

function destroyChart() { if (chart) { chart.destroy(); chart = null } chartSeries.value = [] }

// Показ/скрытие серий из легенды (как в Prometheus):
//   клик — показать ТОЛЬКО эту серию (повторный клик по ней — показать все);
//   ⌘/Ctrl+клик — скрыть/показать одну серию, не трогая остальные.
function setShow(i, show) {
  chartSeries.value[i].show = show
  if (chart) chart.setSeries(i + 1, { show }) // +1: series[0] у uPlot — ось времени
}
function legendClick(i, e) {
  // Тянул мышью, чтобы выделить имя серии для копирования — это не клик-переключение.
  const sel = window.getSelection()
  if (sel && sel.toString().length > 0) return
  if (e.metaKey || e.ctrlKey) {
    setShow(i, !chartSeries.value[i].show)
    return
  }
  const onlyThis = chartSeries.value.every((s, j) => (j === i ? s.show : !s.show))
  chartSeries.value.forEach((s, j) => setShow(j, onlyThis ? true : j === i))
}
function getCss(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() }
</script>

<template>
  <div class="qe">
    <div class="card">
      <!-- Поля запросов (одно или несколько) + выпадающие подсказки у активного.
           Слева номер запроса (>_ — когда запрос один, как в Prometheus). -->
      <div v-for="(q, i) in queries" :key="q.id" class="editor">
        <span class="prompt" :class="{ chip: queries.length > 1 }">{{ queries.length > 1 ? 'q' + (i + 1) : '>_' }}</span>
        <textarea
          :ref="(el) => setTa(el, i)"
          class="input expr"
          :class="{ 'has-x': queries.length > 1 }"
          v-model="q.text"
          rows="1"
          spellcheck="false"
          placeholder="выражение (Shift+Enter — перенос строки)"
          @input="onInput(i)"
          @focus="onFocus(i)"
          @click="onFocus(i)"
          @keydown="onKeydown(i, $event)"
          @blur="showSug = false"
        ></textarea>
        <!-- mousedown.prevent — чтобы клик по «×» не дёргал blur/фокус textarea -->
        <button
          v-if="queries.length > 1"
          class="q-x"
          title="Убрать запрос"
          @mousedown.prevent
          @click="removeQuery(i)"
        >×</button>
        <ul v-if="showSug && activeIdx === i" class="sug">
          <li
            v-for="(s, j) in suggestions"
            :key="s"
            :class="{ active: j === sugIndex }"
            @mousedown.prevent="pick(s)"
          >{{ s }}</li>
        </ul>
      </div>

      <!-- Ещё одно выражение на тот же график/таблицу — как Add Query в vmui. -->
      <button class="add-q" @click="addQuery">+ Добавить запрос</button>

      <!-- Вкладки режима Table / Graph — подчёркиванием, как в Prometheus. -->
      <div class="qmodes">
        <button class="qmode" :class="{ on: mode === 'table' }" @click="mode = 'table'">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" /></svg>
          Table
        </button>
        <button class="qmode" :class="{ on: mode === 'graph' }" @click="mode = 'graph'">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5M4 19h16M8 15l3-4 3 2 4-6" /></svg>
          Graph
        </button>
        <button class="qmode" :class="{ on: mode === 'json' }" @click="mode = 'json'">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4H6a2 2 0 0 0-2 2v4l-2 2 2 2v4a2 2 0 0 0 2 2h2M16 4h2a2 2 0 0 1 2 2v4l2 2-2 2v4a2 2 0 0 1-2 2h-2" /></svg>
          JSON
        </button>
      </div>

      <!-- Панель времени: Graph — «От»/«До» + пресеты; Table — «Время расчёта».
           Общая на все запросы — они выполняются за один и тот же диапазон. -->
      <div class="timebar">
        <template v-if="mode === 'graph'">
          <label class="tfield">От <input type="datetime-local" class="input dt" v-model="fromStr" /></label>
          <label class="tfield">До <input type="datetime-local" class="input dt" v-model="toStr" /></label>
          <div class="presets">
            <button v-for="[label, sec] in PRESETS" :key="sec" class="preset" @click="setPreset(sec)">{{ label }}</button>
          </div>
        </template>
        <template v-else>
          <div class="evalpick">
            <button class="step" title="раньше на 5 мин" @click="stepEval(-300)">‹</button>
            <input type="datetime-local" class="input dt" v-model="evalStr" placeholder="сейчас" />
            <button class="step" title="позже на 5 мин" @click="stepEval(300)">›</button>
          </div>
          <button class="btn btn-sm" v-if="evalStr" @click="evalStr = ''">сейчас</button>
        </template>

        <button v-if="loading" class="btn btn-cancel" @click="cancel">Отменить</button>
        <button v-else class="btn btn-primary" @click="run">Execute</button>
        <span v-if="meta" class="meta">{{ meta }}</span>
      </div>
    </div>

    <div v-for="(e, i) in errors" :key="i" class="msg msg-err">{{ e }}</div>
    <div v-if="truncated" class="trunc">{{ truncated }}</div>

    <!-- Graph: первая загрузка — скелет на месте графика; при повторном запросе
         старый график остаётся, но пригашен (dim), чтобы было видно ожидание. -->
    <div v-show="mode === 'graph'" class="card" :class="{ dim: loading && chartSeries.length }">
      <Skeleton v-if="loading && !chartSeries.length" :lines="1" :height="340" />
      <!-- Резервируем высоту графика, пока он есть или грузится: при перезапросе
           (пресеты «1h/6h…») uPlot на миг удаляет canvas, и без min-height блок
           схлопывался в 0 → высота страницы падала → браузер прижимал прокрутку к
           новому максимуму и «кидал наверх». -->
      <div ref="chartEl" class="chart" :style="(chartSeries.length || loading) ? 'min-height:380px' : null"></div>
      <div v-if="!chartSeries.length && !loading" class="empty">Нет данных — выполни запрос.</div>

      <!-- Легенда: найденные серии с цветом, полными лейблами и значением
           (номер запроса — когда выражений несколько) -->
      <div v-if="chartSeries.length" class="legend">
        <button
          v-for="(s, i) in chartSeries"
          :key="i"
          class="leg"
          :class="{ off: !s.show }"
          @click="legendClick(i, $event)"
        >
          <span class="leg-dot" :style="{ background: s.color }"></span>
          <span v-if="multiRun" class="leg-q">q{{ s.qi + 1 }}</span>
          <span class="leg-lab">{{ s.label }}</span>
          <span class="leg-val">{{ s.value }}</span>
        </button>
      </div>
    </div>

    <!-- Table: столбец на каждый лейбл (как таблица в vmui); при нескольких
         запросах первая колонка — чей это результат (q1/q2). Первая загрузка —
         скелет-строки; при повторном запросе старая таблица остаётся, но пригашена. -->
    <div v-if="mode === 'table' && loading && !instant.length" class="card"><Skeleton :lines="6" :height="24" /></div>
    <div v-else-if="mode === 'table' && instant.length" class="card" :class="{ dim: loading }">
      <div class="tbl-scroll">
        <table class="tbl">
          <thead>
            <tr>
              <th v-if="multiRun" class="qn">#</th>
              <th v-if="hasMetricName" class="lbl">__name__</th>
              <th v-for="c in columns" :key="c" class="lbl">{{ c }}</th>
              <th class="val">value</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in instant" :key="i">
              <td v-if="multiRun" class="qn mono">q{{ row.qi + 1 }}</td>
              <td v-if="hasMetricName" class="ser">{{ row.metric.__name__ || '' }}</td>
              <td v-for="c in columns" :key="c" class="ser">{{ row.metric[c] ?? '' }}</td>
              <td class="val">{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-else-if="mode === 'table' && !loading" class="empty">Нет данных — выполни запрос.</div>

    <!-- JSON: сырой ответ VM (при нескольких запросах — массив {query, response}). -->
    <div v-if="mode === 'json' && loading && !rawJson" class="card"><Skeleton :lines="6" :height="24" /></div>
    <div v-else-if="mode === 'json' && rawJson" class="card" :class="{ dim: loading }">
      <pre class="json">{{ rawJson }}</pre>
    </div>
    <div v-else-if="mode === 'json' && !loading" class="empty">Нет данных — выполни запрос.</div>
  </div>
</template>

<style scoped>
.editor { position: relative; }
.editor + .editor { margin-top: 10px; }
.prompt {
  position: absolute; left: 12px; top: 11px; z-index: 1;
  font-family: var(--mono); font-size: 14px; color: var(--text-mute); pointer-events: none;
}
/* Номер запроса (q1/q2) при нескольких полях — акцентный чип вместо голого текста. */
.prompt.chip {
  top: 9px; left: 9px;
  font-size: 11px; font-weight: 600; line-height: 1;
  color: var(--accent-bright); background: var(--accent-soft);
  padding: 5px 7px; border-radius: 6px;
}
.expr {
  font-family: var(--mono); font-size: 14px; line-height: 1.5;
  min-height: 42px; padding: 10px 12px 10px 34px;
  /* высоту ведёт autosize() под содержимое; ручной ресайз отключаем, чтобы не
     конфликтовал. overflow-y переключает autosize(): hidden по умолчанию (нет
     лишнего ползунка на пустом/коротком поле), auto — только на потолке EXPR_MAX_H. */
  resize: none; overflow-y: hidden;
}
/* Когда справа есть «×» — не даём тексту заехать под кнопку; слева место под чип. */
.expr.has-x { padding-right: 38px; padding-left: 40px; }
/* Убрать выражение (при нескольких) — «×» в правом углу поля. */
.q-x {
  position: absolute; right: 8px; top: 9px; z-index: 1;
  background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-mute); width: 24px; height: 24px; border-radius: 6px;
  font-size: 16px; line-height: 1; display: inline-flex; align-items: center; justify-content: center;
}
.q-x:hover { color: var(--danger); border-color: var(--danger); }

/* «+ Добавить запрос» — под полями, как Add Query в vmui. */
.add-q {
  margin-top: 10px;
  background: var(--panel-2); border: 1px dashed var(--border);
  color: var(--text-dim); border-radius: 8px; padding: 7px 14px;
  font-family: var(--mono); font-size: 12px;
}
.add-q:hover { border-color: var(--accent); color: var(--accent-bright); }

/* Выпадашка подсказок под полем */
/* Список подсказок — В ПОТОКЕ (не absolute): появляется под полем, гарантированно
   виден (не зависит от z-index/overflow соседей). */
.sug {
  margin: 6px 0 0; padding: 4px; list-style: none;
  max-height: 260px; overflow-y: auto;
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px;
  box-shadow: var(--shadow);
}
.sug li {
  padding: 7px 10px; border-radius: 6px; cursor: pointer;
  font-family: var(--mono); font-size: 13px; color: var(--text-dim);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sug li.active, .sug li:hover { background: var(--accent-soft); color: var(--accent-bright); }

/* Плашка «результат обрезан» — мягкий янтарный чип (читается в обеих темах). */
.trunc {
  display: inline-block; margin: 8px 0 0;
  font-size: 12px; font-family: var(--mono); color: #e8a13c;
  background: rgba(232, 161, 60, 0.12); border: 1px solid rgba(232, 161, 60, 0.35);
  border-radius: 7px; padding: 6px 10px;
}

/* Вкладки режима Table / Graph — подчёркиванием, как в Prometheus. */
.qmodes { display: flex; gap: 20px; margin-top: 14px; border-bottom: 1px solid var(--border-soft); }
.qmode {
  display: inline-flex; align-items: center; gap: 7px;
  background: transparent; border: none; color: var(--text-dim);
  font-size: 14px; padding: 9px 2px; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.qmode:hover { color: var(--text); }
.qmode.on { color: var(--text); border-bottom-color: var(--accent); }

/* Панель времени */
.timebar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
.tfield { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-dim); }
.dt { width: auto; padding: 7px 9px; font-size: 12px; }
.presets { display: inline-flex; gap: 4px; }
.preset {
  background: var(--panel-2); border: 1px solid var(--border-soft); color: var(--text-dim);
  border-radius: 6px; padding: 6px 10px; font-family: var(--mono); font-size: 12px;
}
.preset:hover { border-color: var(--accent); color: var(--accent-bright); }
/* «Время расчёта» со стрелками ‹ › */
.evalpick { display: inline-flex; align-items: stretch; }
.evalpick .dt { border-radius: 0; }
.step {
  background: var(--panel-2); border: 1px solid var(--border); color: var(--text-dim);
  padding: 0 11px; font-size: 15px; line-height: 1;
}
.step:first-child { border-radius: 7px 0 0 7px; border-right: none; }
.step:last-child { border-radius: 0 7px 7px 0; border-left: none; }
.step:hover { color: var(--accent-bright); }
/* Кнопка отмены запроса — на месте Execute, пока идёт загрузка. */
.btn-cancel { background: transparent; border: 1px solid var(--danger); color: var(--danger); }
.btn-cancel:hover { background: var(--danger); color: var(--bg); }
.meta { font-family: var(--mono); font-size: 12px; color: var(--text-mute); margin-left: auto; }

.chart { width: 100%; min-height: 0; }

/* Пока идёт повторный запрос — пригашаем старый результат. */
.dim { opacity: 0.5; transition: opacity 0.15s; }

/* Легенда под графиком — серии столбиком с цветом и полными лейблами */
.legend { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 2px; }
.leg { display: flex; align-items: center; gap: 9px; background: transparent; border: none; padding: 5px 8px; text-align: left; border-radius: 7px; }
.leg:hover { background: var(--panel-2); }
.leg.off { opacity: 0.4; }
/* Цвет серии — «таблетка»-линия, как штрих на графике (виднее квадратика). */
.leg-dot { flex: none; width: 14px; height: 5px; border-radius: 3px; }
/* Номер запроса у серии (q1/q2) — когда выражений несколько. */
.leg-q {
  flex: none; font-family: var(--mono); font-size: 10px; font-weight: 600;
  color: var(--accent-bright); background: var(--accent-soft);
  border-radius: 5px; padding: 2px 6px;
}
/* Имя и значение серии в легенде можно выделить и скопировать (легенда — <button>,
   а у кнопок текст по умолчанию не выделяется; клик-переключение серии при этом
   не срабатывает, если что-то выделено — см. legendClick). */
.leg-lab { font-family: var(--mono); font-size: 12px; color: var(--text-dim); word-break: break-all; user-select: text; cursor: text; }
.leg.off .leg-lab { text-decoration: line-through; }
/* Значение серии — прижато вправо, как колонка Value в таблице. */
.leg-val { margin-left: auto; padding-left: 14px; font-family: var(--mono); font-size: 12px; color: var(--text); white-space: nowrap; user-select: text; cursor: text; }

.tbl-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: var(--text-mute); font-weight: 600; padding: 8px; border-bottom: 1px solid var(--border-soft); white-space: nowrap; }
.tbl td { padding: 8px; border-bottom: 1px solid var(--border-soft); vertical-align: top; }
/* Строка под курсором подсвечивается — легче вести взгляд по широкой таблице.
   Полупрозрачный серый одинаково спокойно работает в обеих темах. */
.tbl tbody tr:hover td { background: rgba(127, 127, 127, 0.07); }
/* Заголовок-лейбл моноширинным — так столбцы читаются как имена лейблов в vmui. */
.tbl th.lbl { font-family: var(--mono); font-weight: 600; color: var(--accent-bright); }
/* Номер запроса (q1/q2) в таблице — приглушённо, узкой колонкой. */
.tbl th.qn, .tbl td.qn { color: var(--text-mute); white-space: nowrap; width: 1%; }
/* Много лейблов = много колонок: держим значения в одну строку и скроллим таблицу
   вбок (tbl-scroll), а не ломаем ячейки по буквам в высоченные столбики. */
.tbl .ser { font-family: var(--mono); color: var(--text-dim); white-space: nowrap; }
.tbl .val { font-family: var(--mono); text-align: right; white-space: nowrap; }
/* Значения таблицы можно выделять и копировать (метрику, лейблы). */
.tbl td { user-select: text; }
.mono { font-family: var(--mono); }

/* JSON-режим — сырой ответ VM с горизонтальной прокруткой; текст выделяем/копируем.
   Слегка утоплен (panel-2) — читается как «код», а не как обычный текст. */
.json {
  margin: 0; padding: 12px 14px; max-height: 520px; overflow: auto;
  background: var(--panel-2); border-radius: 8px;
  font-family: var(--mono); font-size: 12px; line-height: 1.5; color: var(--text-dim);
  white-space: pre; word-break: normal; user-select: text;
}
.empty { color: var(--text-mute); font-size: 13px; padding: 28px 2px; text-align: center; }
</style>

<!-- Не scoped: тултип графика создаётся из JS внутри uPlot (scoped-стили до него
     не достают). Класс qe-tip достаточно специфичен, чтобы не пересекаться. -->
<style>
.qe-tip {
  position: absolute; z-index: 10; pointer-events: none;
  max-width: 420px; max-height: 320px; overflow-y: auto; padding: 9px 11px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  font-family: var(--mono); font-size: 12px; color: var(--text);
}
/* Шапка — цветная точка серии + имя метрики (жирным) + номер запроса. */
.qe-tip-h { display: flex; align-items: flex-start; gap: 7px; line-height: 1.35; }
.qe-tip-dot { flex: none; width: 9px; height: 9px; border-radius: 2px; margin-top: 3px; }
.qe-tip-name { font-weight: 700; word-break: break-all; }
.qe-tip-qn {
  flex: none; margin-left: auto; color: var(--text-mute); font-size: 10px;
  border: 1px solid var(--border-soft); border-radius: 4px; padding: 0 4px;
}
/* Лейблы — по строке на каждый, key приглушён, value ярче; отступ и линия слева. */
.qe-tip-labels { margin: 7px 0 0; padding: 6px 0 2px 16px; border-top: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 3px; }
.qe-tip-row { display: flex; gap: 6px; line-height: 1.3; word-break: break-all; }
.qe-tip-k { color: var(--text-mute); flex: none; }
.qe-tip-k::after { content: '='; margin-left: 6px; color: var(--text-mute); }
.qe-tip-val { color: var(--text); }
/* Подвал — значение (жирно) и время (приглушённо). */
.qe-tip-foot { margin-top: 8px; padding-top: 6px; border-top: 1px solid var(--border-soft); display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.qe-tip-v { font-weight: 700; font-size: 13px; }
.qe-tip-t { color: var(--text-mute); font-size: 11px; white-space: nowrap; }

/* Выделение диапазона мышью по графику — заметнее, чем бледная заливка uPlot по
   умолчанию: тёмная полупрозрачная подложка + акцентные границы по краям окна. */
.u-select {
  background: rgba(0, 0, 0, 0.38);
  border-left: 2px solid var(--accent);
  border-right: 2px solid var(--accent);
}
</style>
