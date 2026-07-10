<script setup>
// Одна панель запроса «как в Prometheus»: ввод PromQL/MetricsQL с живым
// автодополнением (метрики + функции), режимы Graph (range → uPlot) и Table
// (instant → таблица). Несколько таких панелей складываются в QueryExplorer
// (кнопка «добавить запрос» снизу — как «Add Panel» в Prometheus).
// Бэкенд — тонкий прокси, отдаёт Prometheus-JSON.
import { ref, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { victoriaApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null }, // мультитенантный VM; иначе null
  index: { type: Number, default: 1 },      // номер панели (для шапки)
  removable: { type: Boolean, default: false }, // показывать кнопку «убрать»
})
defineEmits(['remove'])

// Лимиты, чтобы тяжёлый запрос не подвесил браузер.
const MAX_TABLE_ROWS = 1000  // строк в таблице (instant)
const MAX_SERIES = 20        // линий на графике (range)
const MAX_METRICS = 10000    // имён метрик в автокомплите

const expr = ref('')
const mode = ref('graph') // 'graph' (range) | 'table' (instant)

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
const loading = ref(false)
const error = ref(null)
const meta = ref('') // строка-сводка под запросом (серий, время расчёта)
const truncated = ref('') // предупреждение об обрезке результата

const instant = ref([]) // [{ labels, value }] для режима Table (уже обрезано до MAX_TABLE_ROWS)

// --- Автодополнение ----------------------------------------------------------
const ta = ref(null) // ссылка на textarea
const metricNames = ref([]) // имена метрик (__name__)
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

const PRESETS = [['15m', 900], ['1h', 3600], ['6h', 21600], ['24h', 86400], ['7d', 604800]]
// Пресет просто выставляет «От»/«До» относительно текущего момента.
function setPreset(sec) {
  toStr.value = toLocalInput(nowSec())
  fromStr.value = toLocalInput(nowSec() - sec)
  if (expr.value.trim() && !loading.value) run()
}
// Шаг «Время расчёта» стрелками ‹ › — на 5 минут; пусто трактуем как «сейчас».
function stepEval(deltaSec) {
  const base = fromLocalInput(evalStr.value) ?? nowSec()
  evalStr.value = toLocalInput(base + deltaSec)
  if (expr.value.trim() && !loading.value) run()
}

const COLORS = ['#ff7a59', '#8be9fd', '#50fa7b', '#ffb86c', '#bd93f9', '#ff79c6', '#f1fa8c', '#6272a4', '#ff5555', '#69ff94']

const chartEl = ref(null)
const chartSeries = ref([]) // [{ label, color, show }] — легенда под графиком
let chart = null
let ro = null

let metricsTried = false
async function loadMetrics() {
  metricsTried = true
  try {
    const r = await victoriaApi.labelValues(props.env, '__name__', props.tenant)
    metricNames.value = Array.isArray(r.data) ? r.data.slice(0, MAX_METRICS) : []
  } catch (e) {
    metricNames.value = [] // подсказок не будет, но поле работает
  }
}
onMounted(loadMetrics)

// Смена Graph/Table — перезапускаем запрос, если он уже введён (как в Prometheus).
watch(mode, () => { if (expr.value.trim() && !loading.value) run() })

onBeforeUnmount(() => {
  if (ro) ro.disconnect()
  if (chart) chart.destroy()
})

// Токен под курсором (последовательность из букв/цифр/_/:) — то, что дополняем.
function currentToken() {
  const el = ta.value
  const pos = el ? el.selectionStart : expr.value.length
  const text = expr.value
  let start = pos
  while (start > 0 && /[A-Za-z0-9_:]/.test(text[start - 1])) start--
  return { token: text.slice(start, pos), start, end: pos }
}

function refreshSug() {
  if (!metricsTried) loadMetrics() // подстраховка, если onMounted не успел/не сработал
  const { token } = currentToken()
  if (token.length < 1) { showSug.value = false; suggestions.value = []; return }
  const q = token.toLowerCase()
  const starts = []
  const incl = []
  for (const item of [...FUNCS, ...metricNames.value]) {
    const l = item.toLowerCase()
    if (l.startsWith(q)) starts.push(item)
    else if (l.includes(q)) incl.push(item)
    if (starts.length >= 80) break
  }
  suggestions.value = [...starts, ...incl].slice(0, 80)
  sugIndex.value = 0
  showSug.value = true // показываем всегда при непустом токене — даже «нет совпадений»
}

function pick(item) {
  const { start, end } = currentToken()
  const before = expr.value.slice(0, start)
  const after = expr.value.slice(end)
  expr.value = before + item + after
  showSug.value = false
  nextTick(() => {
    const pos = (before + item).length
    if (ta.value) { ta.value.focus(); ta.value.setSelectionRange(pos, pos) }
  })
}

function onKeydown(e) {
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

async function run() {
  if (!expr.value.trim()) return
  showSug.value = false
  loading.value = true
  error.value = null
  meta.value = ''
  truncated.value = ''
  const t0 = performance.now()
  try {
    const n = mode.value === 'table' ? await runInstant() : await runRange()
    meta.value = `${n} серий · ${Math.round(performance.now() - t0)} мс`
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function runInstant() {
  destroyChart()
  const r = await victoriaApi.query(props.env, expr.value, fromLocalInput(evalStr.value), props.tenant)
  const rt = r.data?.resultType
  let rows
  if (rt === 'scalar' || rt === 'string') {
    rows = [{ labels: rt, value: r.data.result?.[1] ?? '' }]
  } else {
    rows = (r.data?.result || []).map((s) => ({ labels: labelsStr(s.metric), value: s.value ? s.value[1] : '' }))
  }
  instant.value = rows.slice(0, MAX_TABLE_ROWS)
  if (rows.length > MAX_TABLE_ROWS) truncated.value = `показаны первые ${MAX_TABLE_ROWS} из ${rows.length} серий`
  return rows.length
}

async function runRange() {
  instant.value = []
  const end = fromLocalInput(toStr.value) ?? nowSec()
  const start = fromLocalInput(fromStr.value) ?? (end - 3600)
  if (start >= end) throw new Error('«От» должно быть раньше «До»')
  const step = Math.max(1, Math.floor((end - start) / 400))
  const r = await victoriaApi.queryRange(props.env, expr.value, start, end, step, props.tenant)
  const res = r.data?.result || []
  await drawChart(res.slice(0, MAX_SERIES), start, end, step)
  if (res.length > MAX_SERIES) truncated.value = `на графике первые ${MAX_SERIES} из ${res.length} серий`
  return res.length
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
}

// Плагин-тултип: вместо гигантской легенды показываем по наведению ближайшую к
// курсору серию — цвет, ПОЛНОЕ имя с лейблами, значение и время.
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
        tip.innerHTML =
          `<div class="qe-tip-h"><span class="qe-tip-dot" style="background:${s.stroke}"></span>${escapeHtml(s.label)}</div>` +
          `<div class="qe-tip-v">${bestVal}</div>` +
          `<div class="qe-tip-t">${new Date(t * 1000).toLocaleString()}</div>`
        tip.style.display = 'block'
        const w = u.over.clientWidth
        let lft = left + 14
        if (lft + 300 > w) lft = left - 300 - 14  // не вылезать за правый край
        tip.style.left = Math.max(0, lft) + 'px'
        tip.style.top = Math.max(0, top - 10) + 'px'
      },
    },
  }
}

async function drawChart(series, start, end, step) {
  destroyChart()
  if (!series.length) return
  const timeline = []
  for (let t = start; t <= end; t += step) timeline.push(t)

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
  // Легенда под графиком: цвет + полное имя серии, с управлением видимостью.
  chartSeries.value = series.map((s, i) => ({
    label: labelsStr(s.metric) || `series ${i + 1}`,
    color: COLORS[i % COLORS.length],
    show: true,
  }))

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
    <!-- Шапка панели: номер запроса + кнопка «убрать» (только если панелей больше одной) -->
    <div v-if="removable" class="panel-head">
      <span class="panel-num">Запрос {{ index }}</span>
      <button class="panel-x" title="Убрать запрос" @click="$emit('remove')">×</button>
    </div>

    <div class="card">
      <!-- Поле запроса + выпадающие подсказки. Префикс >_ — как в Prometheus. -->
      <div class="editor">
        <span class="prompt">&gt;_</span>
        <textarea
          ref="ta"
          class="input expr"
          v-model="expr"
          rows="1"
          spellcheck="false"
          placeholder="выражение (Shift+Enter — перенос строки)"
          @input="refreshSug"
          @focus="refreshSug"
          @click="refreshSug"
          @keydown="onKeydown"
          @blur="showSug = false"
        ></textarea>
        <ul v-if="showSug" class="sug">
          <li
            v-for="(s, i) in suggestions"
            :key="s"
            :class="{ active: i === sugIndex }"
            @mousedown.prevent="pick(s)"
          >{{ s }}</li>
          <li v-if="!suggestions.length" class="sug-empty">нет совпадений</li>
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
      </div>

      <!-- Панель времени: Graph — «От»/«До» + пресеты; Table — «Время расчёта». -->
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

        <button class="btn btn-primary" :disabled="loading" @click="run">{{ loading ? '…' : 'Execute' }}</button>
        <span v-if="meta" class="meta">{{ meta }}</span>
      </div>
    </div>

    <div v-if="error" class="msg msg-err">{{ error }}</div>
    <div v-if="truncated" class="trunc">{{ truncated }}</div>

    <!-- Graph -->
    <div v-show="mode === 'graph'" class="card">
      <div ref="chartEl" class="chart"></div>
      <div v-if="!chartSeries.length && !loading" class="empty">Нет данных — выполни запрос.</div>

      <!-- Легенда: найденные серии с цветом и полными лейблами -->
      <div v-if="chartSeries.length" class="legend">
        <button
          v-for="(s, i) in chartSeries"
          :key="i"
          class="leg"
          :class="{ off: !s.show }"
          @click="legendClick(i, $event)"
        >
          <span class="leg-dot" :style="{ background: s.color }"></span>
          <span class="leg-lab">{{ s.label }}</span>
        </button>
      </div>
    </div>

    <!-- Table -->
    <div v-if="mode === 'table' && instant.length" class="card">
      <div class="tbl-scroll">
        <table class="tbl">
          <thead><tr><th>Element</th><th class="val">Value</th></tr></thead>
          <tbody>
            <tr v-for="(row, i) in instant" :key="i">
              <td class="ser">{{ row.labels }}</td>
              <td class="val">{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-else-if="mode === 'table' && !loading" class="empty">Нет данных — выполни запрос.</div>
  </div>
</template>

<style scoped>
/* Шапка панели: номер запроса слева, кнопка «убрать» справа. */
.panel-head { display: flex; align-items: center; margin-bottom: 8px; }
.panel-num { font-family: var(--mono); font-size: 12px; color: var(--text-mute); }
.panel-x {
  margin-left: auto; background: transparent; border: 1px solid var(--border-soft);
  color: var(--text-mute); width: 24px; height: 24px; border-radius: 6px;
  font-size: 16px; line-height: 1; display: inline-flex; align-items: center; justify-content: center;
}
.panel-x:hover { color: var(--danger); border-color: var(--danger); }

.editor { position: relative; }
.prompt {
  position: absolute; left: 12px; top: 11px; z-index: 1;
  font-family: var(--mono); font-size: 14px; color: var(--text-mute); pointer-events: none;
}
.expr {
  font-family: var(--mono); font-size: 14px; line-height: 1.5;
  min-height: 42px; padding: 10px 12px 10px 34px; resize: vertical;
}

/* Выпадашка подсказок под полем */
/* Список подсказок — В ПОТОКЕ (не absolute): появляется под полем, гарантированно
   виден (не зависит от z-index/overflow соседей). */
.sug {
  margin: 6px 0 0; padding: 4px; list-style: none;
  max-height: 260px; overflow-y: auto;
  background: var(--panel-2); border: 1px solid var(--border); border-radius: 8px;
}
.sug li {
  padding: 7px 10px; border-radius: 6px; cursor: pointer;
  font-family: var(--mono); font-size: 13px; color: var(--text-dim);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.sug li.active, .sug li:hover { background: var(--accent-soft); color: var(--accent-bright); }
.sug-empty { color: var(--text-mute); cursor: default; }
.sug-empty:hover { background: transparent; color: var(--text-mute); }

.trunc { font-size: 12px; color: #ffb86c; margin: 8px 2px 0; font-family: var(--mono); }

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
.meta { font-family: var(--mono); font-size: 12px; color: var(--text-mute); margin-left: auto; }

.chart { width: 100%; min-height: 0; }

/* Легенда под графиком — серии столбиком с цветом и полными лейблами */
.legend { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 4px; }
.leg { display: flex; align-items: center; gap: 9px; background: transparent; border: none; padding: 3px 4px; text-align: left; border-radius: 5px; }
.leg:hover { background: var(--panel-2); }
.leg.off { opacity: 0.4; }
.leg-dot { flex: none; width: 11px; height: 11px; border-radius: 3px; }
.leg-lab { font-family: var(--mono); font-size: 12px; color: var(--text-dim); word-break: break-all; }
.leg.off .leg-lab { text-decoration: line-through; }

.tbl-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: var(--text-mute); font-weight: 600; padding: 7px 8px; border-bottom: 1px solid var(--border-soft); }
.tbl td { padding: 7px 8px; border-bottom: 1px solid var(--border-soft); }
.tbl .ser { font-family: var(--mono); color: var(--text-dim); word-break: break-all; }
.tbl .val { font-family: var(--mono); text-align: right; white-space: nowrap; }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }
</style>

<!-- Не scoped: тултип графика создаётся из JS внутри uPlot (scoped-стили до него
     не достают). Класс qe-tip достаточно специфичен, чтобы не пересекаться. -->
<style>
.qe-tip {
  position: absolute; z-index: 10; pointer-events: none;
  max-width: 300px; padding: 7px 10px;
  background: var(--panel); border: 1px solid var(--border); border-radius: 7px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
  font-family: var(--mono); font-size: 12px; color: var(--text);
}
.qe-tip-h { display: flex; align-items: flex-start; gap: 6px; word-break: break-all; line-height: 1.35; }
.qe-tip-dot { flex: none; width: 9px; height: 9px; border-radius: 2px; margin-top: 3px; }
.qe-tip-v { margin-top: 5px; font-weight: 700; }
.qe-tip-t { margin-top: 2px; color: var(--text-mute); font-size: 11px; }
</style>
