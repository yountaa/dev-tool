<script setup>
// Вкладка TSDB Status: кардинальность (/api/v1/status/tsdb) + прогноз заполнения
// дисков по нодам vmstorage. Прогноз строим из self-метрик VM:
//   vm_free_disk_space_bytes — свободно на ноде,
//   vm_data_size_bytes       — занято данными,
//   rate(vm_data_size_bytes)  — скорость роста → ETA = свободно / рост.
// Если этих метрик нет в источнике (напр. обычный Prometheus) — прогноз скрыт.
import { ref, computed, onMounted } from 'vue'
import { victoriaApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null },
})

const data = ref(null)
const nodes = ref([])       // прогноз по нодам
const topn = ref(20)
const loading = ref(true)
const error = ref(null)

onMounted(loadAll)

async function loadAll() {
  await Promise.all([loadTsdb(), loadForecast()])
}

async function loadTsdb() {
  loading.value = true
  error.value = null
  try {
    const r = await victoriaApi.tsdb(props.env, topn.value, props.tenant)
    data.value = r.data || {}
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadForecast() {
  try {
    const [free, size, growth] = await Promise.all([
      victoriaApi.query(props.env, 'vm_free_disk_space_bytes', null, props.tenant),
      victoriaApi.query(props.env, 'vm_data_size_bytes', null, props.tenant),
      victoriaApi.query(props.env, 'rate(vm_data_size_bytes[1d])', null, props.tenant),
    ])
    const by = {}
    const put = (resp, key) => {
      for (const s of resp.data?.result || []) {
        const inst = (s.metric && (s.metric.instance || s.metric.job)) || '(node)'
        if (!by[inst]) by[inst] = { instance: inst }
        by[inst][key] = parseFloat(s.value[1])
      }
    }
    put(free, 'free'); put(size, 'size'); put(growth, 'growth')
    nodes.value = Object.values(by).sort((a, b) => a.instance.localeCompare(b.instance))
  } catch (e) {
    nodes.value = [] // нет self-метрик — просто не показываем прогноз
  }
}

const total = computed(() => {
  const d = data.value
  if (!d) return null
  return d.totalSeries ?? d.headStats?.numSeries ?? null
})

const lists = computed(() => {
  const d = data.value || {}
  return [
    ['Топ метрик по числу серий', d.seriesCountByMetricName || []],
    ['Топ лейблов по числу серий', d.seriesCountByLabelName || []],
    ['Топ пар label=value', d.seriesCountByLabelValuePair || []],
    ['Число значений у лейбла', d.labelValueCountByLabelName || []],
  ].filter(([, arr]) => arr.length)
})

function maxOf(arr) { return arr.reduce((m, x) => Math.max(m, x.value || 0), 0) || 1 }

// --- Форматтеры ---
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—'
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}
function fmtEta(n) {
  if (!n || !n.growth || n.growth <= 0) return { text: 'стабильно', cls: 'ok' }
  if (n.free == null) return { text: '—', cls: '' }
  const sec = n.free / n.growth
  const days = sec / 86400
  let text
  if (days > 730) text = '> 2 лет'
  else if (days > 60) text = `~${Math.round(days / 30)} мес`
  else text = `~${Math.round(days)} дн`
  return { text, cls: days < 14 ? 'bad' : days < 60 ? 'warn' : 'ok' }
}
function growthPerDay(n) {
  return n.growth != null ? fmtBytes(n.growth * 86400) + '/сут' : '—'
}
</script>

<template>
  <div>
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <div class="bar">
      <label class="lbl">Показывать топ
        <select class="input topn" v-model.number="topn" @change="loadTsdb">
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="50">50</option>
        </select>
      </label>
      <span v-if="total !== null" class="total">Всего серий: <b>{{ total.toLocaleString() }}</b></span>
      <button class="btn btn-sm" @click="loadAll">Обновить</button>
    </div>

    <!-- Прогноз заполнения дисков по нодам -->
    <div v-if="nodes.length" class="card">
      <div class="card-title">Заполнение дисков по нодам</div>
      <div class="tbl-scroll">
        <table class="nodes">
          <thead>
            <tr><th class="l">Нода</th><th class="r">Свободно</th><th class="r">Занято</th><th class="r">Рост</th><th class="r">Заполнится</th></tr>
          </thead>
          <tbody>
            <tr v-for="n in nodes" :key="n.instance">
              <td class="l mono">{{ n.instance }}</td>
              <td class="r mono">{{ fmtBytes(n.free) }}</td>
              <td class="r mono">{{ fmtBytes(n.size) }}</td>
              <td class="r mono">{{ growthPerDay(n) }}</td>
              <td class="r mono eta" :class="fmtEta(n).cls">{{ fmtEta(n).text }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="loading" class="empty">Загрузка…</div>

    <!-- Топ-списки кардинальности -->
    <div v-else class="grid">
      <div v-for="([title, arr], i) in lists" :key="i" class="card">
        <div class="card-title">{{ title }}</div>
        <div class="rows">
          <div v-for="(row, j) in arr" :key="j" class="row">
            <span class="name" :title="row.name">{{ row.name }}</span>
            <span class="bar-track">
              <span class="bar-fill" :style="{ width: (row.value / maxOf(arr) * 100) + '%' }"></span>
            </span>
            <span class="val">{{ (row.value || 0).toLocaleString() }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
.lbl { font-size: 12px; color: var(--text-dim); display: inline-flex; align-items: center; gap: 8px; }
.topn { width: auto; }
.total { font-size: 13px; color: var(--text-dim); margin-left: auto; }
.total b { font-family: var(--mono); color: var(--text); }

/* Таблица нод — числа выровнены по правому краю, единым столбцом.
   На узком окне скроллится внутри себя, а не распирает страницу вбок. */
.tbl-scroll { overflow-x: auto; }
.nodes { width: 100%; border-collapse: collapse; font-size: 13px; }
.nodes th { color: var(--text-mute); font-weight: 600; padding: 6px 10px; border-bottom: 1px solid var(--border-soft); }
.nodes td { padding: 7px 10px; border-bottom: 1px solid var(--border-soft); }
.nodes .l { text-align: left; }
.nodes .r { text-align: right; white-space: nowrap; }
.mono { font-family: var(--mono); }
.eta.ok { color: #50fa7b; }
.eta.warn { color: #ffb86c; }
.eta.bad { color: var(--danger); }

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }

.rows { display: flex; flex-direction: column; gap: 6px; }
/* Три столбца: имя | шкала | значение. Значение фиксированной ширины и вправо —
   так числа стоят ровным столбцом у всех строк. */
.row { display: grid; grid-template-columns: minmax(0, 1fr) 88px 84px; align-items: center; gap: 12px; }
.name { font-family: var(--mono); font-size: 12px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { height: 7px; background: var(--track); border-radius: 4px; overflow: hidden; }
.bar-fill { display: block; height: 100%; background: var(--accent); border-radius: 4px; }
.val { font-family: var(--mono); font-size: 12px; text-align: right; white-space: nowrap; }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }
</style>
