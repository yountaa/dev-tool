<script>
// Кэш имён метрик для автодополнения: ОБЩИЙ для всех панелей (обычный <script>
// выполняется один раз на модуль, в отличие от <script setup> — тот на каждую
// панель). Без него каждое переключение вкладки заново тянуло бы список из
// тысяч имён.
const namesCache = new Map() // 'env|tenant' -> { at, names }
const labelNamesCache = new Map()  // 'env|tenant' -> { at, names } — имена лейблов
const labelValuesCache = new Map() // 'env|tenant|label' -> { at, values } — значения лейбла
const NAMES_TTL = 5 * 60 * 1000

// История выполненных запусков, свежие сверху. Запись — это ВЕСЬ запуск целиком:
// { q: [выражение q1, выражение q2, …] }, а не отдельное выражение. Иначе откат
// из истории восстанавливал одно поле, а соседние оставались от прошлого запуска.
// Общая на все кластеры (запрос обычно переносят с кластера на кластер) и живёт
// в localStorage — переживает перезагрузку страницы.
const HISTORY_KEY = 'vm.qhistory'
const HISTORY_MAX = 10
function readHistory() {
  try {
    const raw = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    const out = []
    for (const item of raw) {
      // строка — старый формат (одно выражение), объект — новый (запуск целиком)
      const q = typeof item === 'string' ? [item] : (Array.isArray(item && item.q) ? item.q : [])
      const clean = q.filter((s) => typeof s === 'string' && s.trim())
      if (clean.length) out.push({ q: clean })
    }
    return out.slice(0, HISTORY_MAX)
  } catch (e) {
    return [] // мусор в localStorage не должен ронять панель
  }
}
function writeHistory(list) {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)) } catch (e) { /* приват-режим */ }
}
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
const CHART_H = 380          // высота графика (её же резервирует место под canvas)

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

const allRows = ref([])  // ВСЕ строки последнего instant-запуска (до среза)
const rowsShown = ref(MAX_TABLE_ROWS) // сколько строк показываем сейчас («Показать ещё»)
const instant = computed(() => allRows.value.slice(0, rowsShown.value)) // видимая часть таблицы
const rawJson = ref('')  // сырой JSON ответа VM для режима JSON (как вкладка JSON в vmui)

// --- История запусков ---------------------------------------------------------
// Выпадающий список под полями (кнопка «История»). Одна строка = один запуск со
// ВСЕМИ его выражениями; клик восстанавливает поля ровно как было и выполняет.
const history = ref(readHistory())
const histOpen = ref(false)
function remember(exprs) {
  const key = JSON.stringify(exprs)
  // Тот же набор выражений не плодим — поднимаем запись наверх.
  const list = history.value.filter((h) => JSON.stringify(h.q) !== key)
  list.unshift({ q: exprs })
  history.value = list.slice(0, HISTORY_MAX)
  writeHistory(history.value)
}
// Откат к запуску: ПОЛНОСТЬЮ заменяем набор полей (было 2 запроса, откатили на
// один — второе поле исчезает) и сразу выполняем — как «вернуться к тому виду».
function useHistory(entry) {
  queries.value = entry.q.map((text) => ({ id: ++qseq, text }))
  tas.value = []              // ссылки на textarea пересоберутся при отрисовке
  activeIdx.value = 0
  showSug.value = false
  histOpen.value = false
  nextTick(() => {
    autosizeAll()
    if (!loading.value) run()
  })
}
function clearHistory() {
  history.value = []
  histOpen.value = false
  writeHistory([])
}

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

// --- Ширина колонок таблицы (тянем мышью за правый край заголовка) -------------
// { [колонка]: ширина в px }. Колонки без записи ведёт браузер, как раньше.
// Ключи: '__name__', имя лейбла, 'value' (номер запроса не тянем — он узкий).
const colW = ref({})
let resizing = null // { key, startX, startW } — пока тянут мышью
function colStyle(key) {
  const w = colW.value[key]
  // min и max вместе с width: иначе авто-раскладка таблицы всё равно раздувает
  // колонку под самое длинное значение (значения у нас в одну строку, nowrap).
  return w ? { width: w + 'px', minWidth: w + 'px', maxWidth: w + 'px' } : null
}
function startResize(key, e) {
  const th = e.target.closest('th')
  resizing = { key, startX: e.clientX, startW: colW.value[key] || (th ? th.offsetWidth : 120) }
  window.addEventListener('mousemove', onResizeMove)
  window.addEventListener('mouseup', stopResize)
  e.preventDefault()  // не начинаем выделение текста заголовка
  e.stopPropagation()
}
function onResizeMove(e) {
  if (!resizing) return
  const w = Math.max(48, resizing.startW + (e.clientX - resizing.startX))
  colW.value = { ...colW.value, [resizing.key]: w }
}
function stopResize() {
  resizing = null
  window.removeEventListener('mousemove', onResizeMove)
  window.removeEventListener('mouseup', stopResize)
}
// Двойной клик по разделителю — вернуть колонке автоматическую ширину.
function resetCol(key) {
  const c = { ...colW.value }
  delete c[key]
  colW.value = c
}
function resetCols() { colW.value = {} }

// --- Горизонтальная прокрутка таблицы ------------------------------------------
// Таблица показывается целиком (высоту не режем, страница листается как в vmui),
// а родной горизонтальный ползунок таблицы спрятан — вместо него ОДИН ползунок,
// прилипший к нижнему краю ЭКРАНА: он всегда на виду, куда бы ты ни долистал.
// Прокрутка общая: тянем ползунок — едет таблица, крутим таблицу — едет ползунок.
const scrollerEl = ref(null)
const xbarEl = ref(null)
const tblW = ref(0)     // ширина содержимого таблицы
const viewW = ref(0)    // ширина видимой области — ползунок нужен, только если уже
let syncing = false     // защита от «эха»: синхронизация в обе стороны
// Пересчёт «в долях»: у полос разный запас хода (у таблицы вертикальный ползунок
// съедает ширину), поэтому копировать scrollLeft один-в-один нельзя — правый край
// не сходился бы.
function part(el) {
  const max = el.scrollWidth - el.clientWidth
  return max > 0 ? el.scrollLeft / max : 0
}
function setPart(el, k) {
  el.scrollLeft = (el.scrollWidth - el.clientWidth) * k
}
function syncFromTable() {
  if (syncing || !xbarEl.value || !scrollerEl.value) return
  syncing = true
  setPart(xbarEl.value, part(scrollerEl.value))
  syncing = false
}
function syncFromBar() {
  if (syncing || !xbarEl.value || !scrollerEl.value) return
  syncing = true
  setPart(scrollerEl.value, part(xbarEl.value))
  syncing = false
}
function measureTable() {
  const el = scrollerEl.value
  tblW.value = el ? el.scrollWidth : 0
  viewW.value = el ? el.clientWidth : 0
}
const needXbar = computed(() => tblW.value > viewW.value + 1)

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
const chartSeries = ref([]) // [{ qi, label, color, value, show, lastAt, stale }] — легенда
const allSeries = ref([])   // ВСЕ серии последнего range-запуска (до среза)
const seriesShown = ref(MAX_SERIES) // сколько серий на графике сейчас («Показать ещё»)
const rangeInfo = ref('')   // подпись под графиком: какой период показан и с каким шагом
let chart = null
let ro = null
let inflight = null // AbortController текущего запуска (общий на все запросы; «Отмена»)
let seriesMeta = [] // [{ metric, qi }] серий графика (для аккуратного тултипа по наведению)
// Диапазон последнего запуска. График рисуем на ВЕСЬ запрошенный период, а не по
// крайним точкам ответа, — иначе метрика, переставшая идти неделю назад, тянулась
// бы линией до правого края и выглядела бы живой (см. drawChart).
let lastRange = { start: 0, end: 0, step: 60 }

// Смена размера окна: пересчитываем ползунок таблицы И ширину графика. Ширину
// графика ведёт ResizeObserver (см. drawChart), но полагаться только на него
// нельзя — в некоторых окружениях (скрытая вкладка, фоновое окно) его колбэк не
// приходит, и canvas остаётся шириной от прошлого размера окна, распирая
// страницу вбок. Обработчик вешается в onMounted, снимается в onBeforeUnmount.
function onWindowResize() {
  measureTable()
  if (chart && chartEl.value) chart.setSize({ width: chartEl.value.clientWidth, height: CHART_H })
}

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
  window.addEventListener('resize', onWindowResize)
})

// Ширина таблицы меняется от данных, набора колонок и ручного ресайза — после
// каждой такой правки пересчитываем верхний ползунок.
watch([instant, columns, colW, mode], () => nextTick(measureTable))

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
  stopResize() // если ушли со вкладки прямо во время перетаскивания колонки
  window.removeEventListener('resize', onWindowResize)
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
  multiRun.value = items.length > 1
  remember(items.map((x) => x.expr)) // в историю — то, что реально выполняли
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
  allSeries.value = [] // график в этом режиме не рисуем — и «Показать ещё» под ним не нужен
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
  // Показываем первую порцию, остальное — по кнопке «Показать ещё» (тысячи строк
  // разом кладут DOM, а данные уже у нас — второй запрос не нужен).
  allRows.value = rows
  rowsShown.value = MAX_TABLE_ROWS
  return rows.length
}

async function runRange(items, signal) {
  allRows.value = []
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
  // Рисуем первую порцию; остальные серии добавляет кнопка «Показать ещё» под
  // легендой — данные уже пришли, перезапрашивать VM не нужно.
  allSeries.value = merged
  seriesShown.value = MAX_SERIES
  lastRange = { start, end, step }
  await drawChart(merged.slice(0, seriesShown.value))
  return merged.length
}

// «Показать ещё» под графиком: дорисовываем следующие MAX_SERIES серий из уже
// полученного ответа. Сколько показано — видно по длине легенды.
async function showMoreSeries() {
  seriesShown.value += MAX_SERIES
  await drawChart(allSeries.value.slice(0, seriesShown.value))
}
// «Показать ещё» под таблицей: следующая порция строк того же ответа.
function showMoreRows() { rowsShown.value += MAX_TABLE_ROWS }

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

// --- Показ времени на графике --------------------------------------------------
// Всё время на графике — местное, как и поля «От»/«До» (datetime-local): смешивать
// зоны в одной панели нельзя, иначе подпись под курсором не сходится с полями.
const two = (n) => String(n).padStart(2, '0')
const dayOf = (d) => `${two(d.getDate())}.${two(d.getMonth() + 1)}`
const timeOf = (d) => `${two(d.getHours())}:${two(d.getMinutes())}`
// «29.07.2026 14:35» — полная метка (подпись периода, легенда).
function fmtStamp(sec, withSeconds = false) {
  const d = new Date(sec * 1000)
  const s = withSeconds ? ':' + two(d.getSeconds()) : ''
  return `${dayOf(d)}.${d.getFullYear()} ${timeOf(d)}${s}`
}
// Длительность по-человечески: 45 с / 21 мин / 2 ч 30 мин / 3 сут.
function fmtDur(sec) {
  if (sec < 60) return `${Math.round(sec)} с`
  if (sec < 3600) return `${Math.round(sec / 60)} мин`
  if (sec < 86400) {
    const h = Math.floor(sec / 3600)
    const m = Math.round((sec % 3600) / 60)
    return m ? `${h} ч ${m} мин` : `${h} ч`
  }
  return `${(sec / 86400).toFixed(sec % 86400 ? 1 : 0)} сут`
}

// Подписи оси времени. Голое «14:35» не отвечает на вопрос «какой это день», а
// дата у каждой метки — каша. Поэтому: дату пишем у ПЕРВОЙ метки и там, где
// начинаются новые сутки, дальше по дню — только время. Ровная полночь и так
// читается как дата, время у неё не дублируем.
function axisTimeValues(u, splits) {
  let prevDay = null
  return splits.map((t) => {
    const d = new Date(t * 1000)
    const day = dayOf(d)
    const time = timeOf(d)
    const newDay = day !== prevDay
    prevDay = day
    if (!newDay) return time
    return time === '00:00' ? day : `${day} ${time}`
  })
}

// Расстояние в пикселях от точки (px,py) до отрезка (ax,ay)—(bx,by).
function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  let k = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0
  k = Math.max(0, Math.min(1, k)) // за концы отрезка не выходим
  return Math.hypot(px - (ax + dx * k), py - (ay + dy * k))
}

// Какая ЛИНИЯ ближе всего к курсору.
// Меряем расстояние до самой линии (до отрезков рядом с курсором), а не по
// вертикали в точке idx: на крутом участке вертикаль уводила выбор на соседнюю
// серию — наводишься на одну линию, а в окошке другая метрика. Серии, скрытые в
// легенде, пропускаем: их линии на графике нет, показывать их значения нельзя.
const TIP_PROX = 26 // px; дальше от всех линий — окошко не показываем
function nearestSeries(u, idx, left, top) {
  let best = null
  for (let si = 1; si < u.series.length; si++) {
    if (u.series[si].show === false) continue
    const ys = u.data[si]
    const to = Math.min(ys.length - 1, idx + 1)
    for (let i = Math.max(0, idx - 1); i <= to; i++) {
      const v = ys[i]
      if (v == null || isNaN(v)) continue
      const px = u.valToPos(u.data[0][i], 'x')
      const py = u.valToPos(v, 'y')
      let d = Math.hypot(px - left, py - top)
      const nv = ys[i + 1]
      if (nv != null && !isNaN(nv)) { // отрезок к следующей точке (в разрыве его нет)
        const qx = u.valToPos(u.data[0][i + 1], 'x')
        const qy = u.valToPos(nv, 'y')
        d = Math.min(d, distToSegment(left, top, px, py, qx, qy))
      }
      if (!best || d < best.dist) best = { si, i, dist: d }
    }
  }
  return best && best.dist <= TIP_PROX ? best : null
}

// Плагин-тултип: вместо гигантской легенды показываем по наведению ближайшую к
// курсору серию — цвет, ПОЛНОЕ имя с лейблами, значение и время (+ номер запроса,
// когда запросов несколько). КЛИК по графику закрепляет окошко на месте (как в
// vmui): оно перестаёт бегать за курсором, текст лейблов можно выделить и
// скопировать. Снять — крестик, повторный клик по графику или Escape.
function tooltipPlugin() {
  let tip
  let pinned = false
  let downX = 0, downY = 0 // точка нажатия — отличаем клик от протяжки-выделения
  let onKey

  function unpin() {
    pinned = false
    if (tip) { tip.classList.remove('pinned'); tip.style.display = 'none' }
  }
  // Копирование лейблов кнопкой: текст готовим при отрисовке (tip.dataset.copy).
  function copyLabels() {
    const text = tip?.dataset.copy || ''
    if (!text) return
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text))
    } else {
      fallbackCopy(text)
    }
    const btn = tip.querySelector('.qe-tip-copy')
    if (btn) { btn.classList.add('done'); setTimeout(() => btn.classList.remove('done'), 900) }
  }
  // Без разрешения на буфер (или по http) — старый способ через скрытое поле.
  function fallbackCopy(text) {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;left:-9999px;top:0'
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } catch (e) { /* совсем никак — пусть копируют руками */ }
    document.body.removeChild(ta)
  }

  return {
    hooks: {
      init(u) {
        tip = document.createElement('div')
        tip.className = 'qe-tip'
        tip.style.display = 'none'
        u.over.appendChild(tip)

        u.over.addEventListener('mousedown', (e) => { downX = e.clientX; downY = e.clientY })
        u.over.addEventListener('click', (e) => {
          // Протяжка (выбор диапазона) кликом не считается.
          if (Math.abs(e.clientX - downX) > 4 || Math.abs(e.clientY - downY) > 4) return
          if (pinned) { unpin(); return }
          if (tip.style.display === 'none') return // курсор не над серией — нечего закреплять
          pinned = true
          tip.classList.add('pinned')
        })
        // Мышь внутри закреплённого окошка до графика не доходит: иначе выделение
        // текста превращалось бы в протяжку-зум, а клик — снимал бы закрепление.
        tip.addEventListener('mousedown', (e) => e.stopPropagation())
        tip.addEventListener('click', (e) => {
          e.stopPropagation()
          if (e.target.closest('.qe-tip-x')) unpin()
          else if (e.target.closest('.qe-tip-copy')) copyLabels()
        })
        onKey = (e) => { if (e.key === 'Escape' && pinned) unpin() }
        document.addEventListener('keydown', onKey)
      },
      destroy() {
        pinned = false
        if (onKey) document.removeEventListener('keydown', onKey)
      },
      setCursor(u) {
        if (pinned) return // закреплено — ни содержимое, ни место не трогаем
        const { idx, left, top } = u.cursor
        if (idx == null || left == null || left < 0) { tip.style.display = 'none'; return }
        // Ближайшая к курсору ЛИНИЯ (скрытые в легенде не участвуют). Курсор не
        // у линии — окошка нет: пустое место графика ничего не «выбирает».
        const hit = nearestSeries(u, idx, left, top)
        if (!hit) { tip.style.display = 'none'; return }
        const best = hit.si
        const ys = u.data[best]
        // Значение берём в точке под курсором; если у серии там разрыв — в той
        // соседней точке, по которой её и опознали.
        const at = (ys[idx] != null && !isNaN(ys[idx])) ? idx : hit.i
        const bestVal = ys[at]
        const s = u.series[best]
        const t = u.data[0][at]
        // Имя метрики и лейблы — по отдельности: имя строкой сверху, каждый лейбл
        // отдельной строкой key=value. Так читается даже при десятке лейблов.
        const sm = seriesMeta[best - 1] || { metric: {}, qi: 0 }
        const metric = sm.metric
        const name = metric.__name__ || s.label || 'значение'
        const qBadge = multiRun.value ? `<span class="qe-tip-qn">q${sm.qi + 1}</span>` : ''
        // Строка лейбла — ГОТОВЫЙ кусок запроса key="value": что выделил мышью, то и
        // скопировалось, без пробелов вокруг «=» — сразу вставляется в выражение.
        const labelRows = Object.entries(metric)
          .filter(([k]) => k !== '__name__')
          .map(([k, v]) => `<div class="qe-tip-row"><span class="qe-tip-k">${escapeHtml(k)}</span><span class="qe-tip-eq">="</span><span class="qe-tip-val">${escapeHtml(v)}</span><span class="qe-tip-eq">"</span></div>`)
          .join('')
        tip.innerHTML =
          `<div class="qe-tip-h"><span class="qe-tip-dot" style="background:${s.stroke}"></span><span class="qe-tip-name">${escapeHtml(name)}</span>${qBadge}` +
          `<button class="qe-tip-copy" title="скопировать лейблы">⧉</button>` +
          `<button class="qe-tip-x" title="открепить (Esc)">×</button></div>` +
          (labelRows ? `<div class="qe-tip-labels">${labelRows}</div>` : '') +
          `<div class="qe-tip-foot"><span class="qe-tip-v">${bestVal}</span><span class="qe-tip-t">${fmtStamp(t, true)}</span></div>` +
          `<div class="qe-tip-hint">клик — закрепить</div>`
        // Текст для кнопки «копировать» — серия целиком, как её пишут в запросе.
        tip.dataset.copy = labelsStr(metric) || name
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

// Шаг сетки времени: наш step, но если VM отдала точки реже (она вправе округлить
// шаг вверх), берём её шаг — иначе между реальными точками остались бы пустые
// слоты и сплошная линия рассыпалась бы на пунктир.
function gridStep(series, step) {
  let least = Infinity
  for (const s of series) {
    const vals = s.values || []
    for (let i = 1; i < vals.length; i++) {
      const d = vals[i][0] - vals[i - 1][0]
      if (d > 0 && d < least) least = d
    }
  }
  return least === Infinity ? step : Math.max(step, least)
}

async function drawChart(series) {
  destroyChart()
  if (!series.length) return
  const { start, end, step } = lastRange

  // Ось X — РОВНАЯ сетка на ВЕСЬ запрошенный период, а не только по точкам ответа.
  // Так видно, что метрика перестала идти: раньше ось сжималась до последней точки,
  // линия дотягивалась до правого края и запрос за неделю выглядел так, будто
  // данные есть до сих пор.
  // Точку ответа кладём в БЛИЖАЙШИЙ слот сетки (VM выравнивает свои метки времени
  // по своей сетке, кратной шагу, — сравнивать их с нашими «в лоб» нельзя, на этом
  // график когда-то пропадал целиком). Слот, в который ничего не легло, остаётся
  // null, а spanGaps: false рвёт на нём линию — разрыв данных виден как разрыв.
  const gstep = gridStep(series, step)
  const timeline = []
  for (let t = Math.ceil(start / gstep) * gstep; t <= end; t += gstep) timeline.push(t)
  if (!timeline.length) timeline.push(start, end) // окно уже шага — пусть будет хоть ось

  const t0 = timeline[0]
  const data = [timeline]
  const uSeries = [{}]
  series.forEach((s, i) => {
    const col = new Array(timeline.length).fill(null)
    for (const [ts, v] of (s.values || [])) {
      const k = Math.round((ts - t0) / gstep)
      if (k >= 0 && k < col.length) col[k] = parseFloat(v)
    }
    data.push(col)
    uSeries.push({
      label: labelsStr(s.metric) || `series ${i + 1}`,
      stroke: COLORS[i % COLORS.length],
      width: 1.6,
      points: { show: false },
      spanGaps: false, // нет данных — нет линии (см. выше)
    })
  })
  // Легенда под графиком: цвет + полное имя серии + последнее значение (и номер
  // запроса, когда их несколько), с управлением видимостью. Серию, у которой
  // данные оборвались до конца окна, помечаем временем последней точки — по
  // легенде сразу видно, какая метрика замолчала и когда.
  chartSeries.value = series.map((s, i) => {
    const vals = s.values || []
    const lastAt = vals.length ? vals[vals.length - 1][0] : null
    return {
      qi: s.qi ?? 0,
      label: labelsStr(s.metric) || `series ${i + 1}`,
      color: COLORS[i % COLORS.length],
      value: lastValue(s),
      show: true,
      lastAt,
      stale: lastAt != null && end - lastAt > gstep * 2,
      lastStamp: lastAt != null ? fmtStamp(lastAt) : '',
    }
  })
  seriesMeta = series.map((s) => ({ metric: s.metric || {}, qi: s.qi ?? 0 })) // для тултипа
  rangeInfo.value = `${fmtStamp(start)} — ${fmtStamp(end)} · шаг ${fmtDur(gstep)}`

  await nextTick()
  const width = chartEl.value?.clientWidth || 900
  const axisColor = getCss('--text-mute') || '#888'
  const gridColor = getCss('--border-soft') || 'rgba(128,128,128,0.2)'
  const axisFont = '11px ' + (getCss('--mono') || 'monospace')
  chart = new uPlot(
    {
      width,
      height: CHART_H,
      series: uSeries,
      // Ось X держим на запрошенном окне: пустой «хвост» справа — это и есть
      // ответ «данные кончились тогда-то», его нельзя обрезать под данные.
      scales: { x: { time: true, range: [start, end] } },
      axes: [
        {
          stroke: axisColor,
          font: axisFont,
          grid: { stroke: gridColor, width: 1 },
          ticks: { stroke: gridColor },
          values: axisTimeValues, // «29.07 14:00» на смене суток, дальше «14:30»
          space: 90,              // подписи с датой шире — метки реже, без каши
          size: 34,
        },
        { stroke: axisColor, font: axisFont, grid: { stroke: gridColor, width: 1 }, ticks: { stroke: gridColor } },
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
    if (chart && chartEl.value) chart.setSize({ width: chartEl.value.clientWidth, height: CHART_H })
  })
  ro.observe(chartEl.value)
}

function destroyChart() {
  if (chart) { chart.destroy(); chart = null }
  chartSeries.value = []
  rangeInfo.value = ''
}

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

      <!-- Кнопки под полями: ещё одно выражение (как Add Query в vmui) и история. -->
      <div class="qtools">
        <button class="add-q" @click="addQuery">+ Добавить запрос</button>
        <button
          v-if="history.length"
          class="add-q hist-btn"
          :class="{ on: histOpen }"
          @click="histOpen = !histOpen"
        >⟲ История <span class="hist-n">{{ history.length }}</span></button>
      </div>

      <!-- История: последние запуски, свежие сверху. Строка = запуск целиком:
           клик возвращает ВСЕ его выражения в поля (лишние поля убираются) и
           сразу выполняет. Список в потоке, как подсказки — не перекрывается. -->
      <div v-if="histOpen && history.length" class="hist">
        <div class="hist-head">
          <span>последние запуски — клик вернёт запрос во все поля и выполнит</span>
          <button class="hist-clear" @click="clearHistory">очистить</button>
        </div>
        <ul class="hist-list">
          <li v-for="(h, i) in history" :key="i">
            <button class="hist-item" @mousedown.prevent @click="useHistory(h)">
              <span v-for="(e, j) in h.q" :key="j" class="hist-q" :title="e">
                <span v-if="h.q.length > 1" class="hist-qn">q{{ j + 1 }}</span>{{ e }}
              </span>
            </button>
          </li>
        </ul>
      </div>

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

      <!-- Какой период показан и с каким шагом. Ось X всегда на весь период, даже
           если данные кончились раньше, — подпись отвечает «докуда вообще график». -->
      <div v-if="rangeInfo" class="range-info">{{ rangeInfo }}</div>

      <!-- Легенда: найденные серии с цветом, полными лейблами и значением
           (номер запроса — когда выражений несколько). У замолчавшей серии —
           отметка, когда пришла последняя точка. -->
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
          <span v-if="s.stale" class="leg-stale" :title="'последняя точка: ' + s.lastStamp">
            нет данных с {{ s.lastStamp }}
          </span>
          <span class="leg-val">{{ s.value }}</span>
        </button>
      </div>

      <!-- Серий больше, чем нарисовано: догружаем порциями по кнопке (данные уже
           у нас, VM не перезапрашиваем). -->
      <div v-if="allSeries.length > chartSeries.length" class="more">
        <button class="btn btn-sm" @click="showMoreSeries">
          Показать ещё {{ Math.min(MAX_SERIES, allSeries.length - chartSeries.length) }}
        </button>
        <span class="more-note">показано {{ chartSeries.length }} из {{ allSeries.length }} серий</span>
      </div>
    </div>

    <!-- Table: столбец на каждый лейбл (как таблица в vmui); при нескольких
         запросах первая колонка — чей это результат (q1/q2). Первая загрузка —
         скелет-строки; при повторном запросе старая таблица остаётся, но пригашена. -->
    <div v-if="mode === 'table' && loading && !instant.length" class="card"><Skeleton :lines="6" :height="24" /></div>
    <div v-else-if="mode === 'table' && instant.length" class="card" :class="{ dim: loading }">
      <div v-if="Object.keys(colW).length" class="tbl-bar">
        <button class="btn btn-sm" @click="resetCols">сбросить ширины</button>
      </div>
      <div ref="scrollerEl" class="tbl-scroll" @scroll="syncFromTable">
        <table class="tbl">
          <thead>
            <tr>
              <th v-if="multiRun" class="qn">#</th>
              <th v-if="hasMetricName" class="lbl" :style="colStyle('__name__')">
                __name__
                <span class="rz" title="потянуть — ширина, двойной клик — сброс"
                      @mousedown="startResize('__name__', $event)" @dblclick="resetCol('__name__')"></span>
              </th>
              <th v-for="c in columns" :key="c" class="lbl" :style="colStyle(c)">
                {{ c }}
                <span class="rz" title="потянуть — ширина, двойной клик — сброс"
                      @mousedown="startResize(c, $event)" @dblclick="resetCol(c)"></span>
              </th>
              <th class="val" :style="colStyle('value')">
                value
                <span class="rz" title="потянуть — ширина, двойной клик — сброс"
                      @mousedown="startResize('value', $event)" @dblclick="resetCol('value')"></span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, i) in instant" :key="i">
              <td v-if="multiRun" class="qn mono">q{{ row.qi + 1 }}</td>
              <td v-if="hasMetricName" class="ser" :style="colStyle('__name__')" :title="row.metric.__name__ || ''">{{ row.metric.__name__ || '' }}</td>
              <td v-for="c in columns" :key="c" class="ser" :style="colStyle(c)" :title="row.metric[c] ?? ''">{{ row.metric[c] ?? '' }}</td>
              <td class="val" :style="colStyle('value')" :title="String(row.value)">{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Единственный горизонтальный ползунок таблицы: прилипает к нижнему краю
           экрана, пока таблица в поле зрения (родной ползунок скрыт стилями). -->
      <div v-show="needXbar" ref="xbarEl" class="xbar" @scroll="syncFromBar">
        <div class="xbar-in" :style="{ width: tblW + 'px' }"></div>
      </div>

      <!-- Строк больше, чем показано: следующая порция по кнопке. -->
      <div v-if="allRows.length > instant.length" class="more">
        <button class="btn btn-sm" @click="showMoreRows">
          Показать ещё {{ Math.min(MAX_TABLE_ROWS, allRows.length - instant.length) }}
        </button>
        <span class="more-note">показано {{ instant.length }} из {{ allRows.length }} строк</span>
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
/* Кнопки под полями запросов: «+ Добавить запрос» и «История». */
.qtools { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
.hist-btn { display: inline-flex; align-items: center; gap: 7px; }
.hist-btn.on { border-style: solid; border-color: var(--accent); color: var(--accent-bright); }
.hist-n {
  font-size: 11px; color: var(--text-mute);
  background: var(--chip); border-radius: 20px; padding: 1px 7px;
}
.hist-btn.on .hist-n { color: var(--accent-bright); background: var(--accent-soft); }

/* Выпадающая история запусков — в потоке под кнопками (как список подсказок). */
.hist {
  margin-top: 8px; border: 1px solid var(--border); border-radius: 10px;
  background: var(--panel-2); box-shadow: var(--shadow); overflow: hidden;
}
.hist-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 7px 12px; border-bottom: 1px solid var(--border-soft);
  font-size: 11px; color: var(--text-mute);
}
.hist-clear { background: transparent; border: none; color: var(--text-mute); font-size: 11px; padding: 0; }
.hist-clear:hover { color: var(--danger); }
.hist-list { list-style: none; margin: 0; padding: 4px; max-height: 260px; overflow-y: auto; }
/* Строка = один запуск: сверху вниз его выражения (q1/q2 — чьё какое). */
.hist-item {
  display: flex; flex-direction: column; gap: 3px; width: 100%; text-align: left;
  background: transparent; border: none; border-radius: 7px; padding: 7px 9px;
}
.hist-item:hover { background: var(--accent-soft); }
.hist-q {
  display: block; font-family: var(--mono); font-size: 12px; color: var(--text-dim);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.hist-item:hover .hist-q { color: var(--accent-bright); }
/* Номер выражения внутри запуска — тем же чипом, что и у полей запроса. */
.hist-qn {
  display: inline-block; margin-right: 7px;
  font-size: 10px; font-weight: 600; color: var(--accent-bright);
  background: var(--accent-soft); border-radius: 5px; padding: 1px 5px;
}

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

/* «Показать ещё» — по центру под графиком/таблицей, рядом счётчик показанного. */
.more { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 14px; }
.more-note { font-family: var(--mono); font-size: 12px; color: var(--text-mute); }

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
/* Серия, у которой данные оборвались раньше конца окна: значение в легенде —
   старое, и об этом надо сказать прямо, а не оставлять его выглядеть свежим. */
.leg-stale {
  margin-left: auto; flex: none;
  font-family: var(--mono); font-size: 11px; white-space: nowrap;
  color: var(--text-mute); background: var(--chip);
  border-radius: 20px; padding: 1px 8px;
}
.leg-stale + .leg-val { margin-left: 0; padding-left: 10px; }

/* Подпись под графиком: какой период показан и с каким шагом. */
.range-info {
  margin-top: 10px;
  font-family: var(--mono); font-size: 11px; color: var(--text-mute); text-align: center;
}

/* Кнопка сброса ширин колонок (появляется, когда что-то тянули мышью). */
.tbl-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }

/* Единственный горизонтальный ползунок таблицы. Липнет к НИЖНЕМУ краю экрана,
   пока карточка с таблицей в поле зрения: длинный список листаешь страницей, а
   ползунок всё это время на виду — можно уехать вправо по лейблам в любой момент.
   Внутри пустая распорка шириной с таблицу — по ней браузер и рисует ползунок.
   Вид задаём явно (::-webkit-scrollbar): иначе macOS прячет полосу до тех пор,
   пока что-нибудь не прокрутишь, и «всегда на виду» не получается. */
.xbar {
  position: sticky; bottom: 0; z-index: 4;
  overflow-x: auto; overflow-y: hidden;
  margin-top: 6px; padding-top: 2px;
  background: var(--panel); border-radius: 6px;
}
.xbar-in { height: 1px; }
.xbar::-webkit-scrollbar { height: 11px; }
.xbar::-webkit-scrollbar-track { background: var(--panel-2); border-radius: 6px; }
.xbar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 6px; }
.xbar::-webkit-scrollbar-thumb:hover { background: var(--accent); }

/* Таблицу по высоте НЕ режем: список видно целиком, страница листается как в
   vmui. Прокрутка тут только горизонтальная, и её родной ползунок прячем — вместо
   него один липкий (.xbar ниже), чтобы полоса не появлялась в двух местах. */
.tbl-scroll { overflow-x: auto; overflow-y: hidden; scrollbar-width: none; }
.tbl-scroll::-webkit-scrollbar { display: none; }
.tbl { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; }
.tbl th {
  position: relative; /* точка отсчёта для .rz — «ручки» ширины колонки */
  text-align: left; color: var(--text-mute); font-weight: 600; padding: 8px; white-space: nowrap;
  border-bottom: 1px solid var(--border-soft);
}
.tbl td { padding: 8px; border-bottom: 1px solid var(--border-soft); vertical-align: top; }
/* Колонку, которой задали ширину, обрезаем многоточием (полный текст — в title):
   иначе авто-раскладка растянет её обратно под самое длинное значение. */
.tbl th, .tbl td { overflow: hidden; text-overflow: ellipsis; }
/* Разделитель для растягивания — узкая полоса у правого края заголовка. */
.rz {
  position: absolute; top: 0; right: 0; width: 7px; height: 100%;
  cursor: col-resize; user-select: none;
}
.rz:hover { background: var(--accent); opacity: 0.55; }
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
/* Кнопки «копировать»/«открепить» — только у ЗАКРЕПЛЁННОГО окошка (пока оно
   бегает за курсором, нажать на них всё равно нельзя). */
.qe-tip-copy, .qe-tip-x { display: none; }
.qe-tip-qn + .qe-tip-copy { margin-left: 6px; }
.qe-tip.pinned .qe-tip-copy, .qe-tip.pinned .qe-tip-x {
  display: inline-flex; align-items: center; justify-content: center;
  flex: none; width: 20px; height: 20px; margin-top: -2px;
  background: transparent; border: 1px solid var(--border-soft); border-radius: 5px;
  color: var(--text-mute); font-size: 12px; line-height: 1; cursor: pointer;
}
.qe-tip.pinned .qe-tip-copy { margin-left: auto; }
.qe-tip.pinned .qe-tip-qn + .qe-tip-copy { margin-left: 6px; }
.qe-tip.pinned .qe-tip-x { margin-left: 4px; font-size: 15px; }
.qe-tip.pinned .qe-tip-copy:hover, .qe-tip.pinned .qe-tip-x:hover { color: var(--accent-bright); border-color: var(--accent); }
.qe-tip.pinned .qe-tip-copy.done { color: #3fae72; border-color: #3fae72; }
/* Подсказка «клик — закрепить» видна только пока не закреплено. */
.qe-tip-hint { margin-top: 6px; font-size: 10px; color: var(--text-mute); text-align: right; }
.qe-tip.pinned .qe-tip-hint { display: none; }
/* Закреплённое окошко: мышь работает (можно выделить и скопировать лейблы),
   рамка акцентная — видно, что оно «прибито». */
.qe-tip.pinned {
  pointer-events: auto; user-select: text; cursor: auto;
  border-color: var(--accent); max-height: 420px;
}
/* Лейблы — по строке на каждый, key приглушён, value ярче; отступ и линия слева. */
.qe-tip-labels { margin: 7px 0 0; padding: 6px 0 2px 16px; border-top: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 3px; }
/* Строка лейбла — обычный текст key="value" одним куском: без flex-gap и без «=»
   в ::after (браузер добавлял бы к копии лишние пробелы, и вставить в запрос было
   нельзя). Цветом различаем только имя и значение. */
.qe-tip-row { display: block; line-height: 1.35; word-break: break-all; }
.qe-tip-k { color: var(--text-mute); }
.qe-tip-eq { color: var(--text-mute); }
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
