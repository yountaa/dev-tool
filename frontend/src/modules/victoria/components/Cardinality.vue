<script setup>
// Вкладка TSDB Status: кардинальность (/api/v1/status/tsdb) — топ метрик/лейблов
// по числу серий. Заполненность дисков вынесена в отдельную под-вкладку «Диск».
import { ref, computed, onMounted } from 'vue'
import Skeleton from '../../../shared/Skeleton.vue'
import { victoriaApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null },
})

const data = ref(null)
const topn = ref(10)   // TSDB Status по умолчанию показывает топ-10
const loading = ref(true)
const error = ref(null)
const verified = ref(false) // сверяли ли топ-метрики с хранилищем (на больших кластерах — нет)

// Порог, выше которого НЕ пересчитываем серии топ-метрик (count по __name__ на
// миллионах серий кладёт нагрузку на vmstorage). Число серий берём из tsdb-статуса.
const VERIFY_MAX_SERIES = 5000000

onMounted(() => loadAll())

// refresh=true (кнопка «Обновить») — /status/tsdb перечитывается мимо кэша бэкенда.
async function loadAll(refresh = false) {
  await loadTsdb(refresh)
}

async function loadTsdb(refresh = false) {
  loading.value = true
  error.value = null
  try {
    const r = await victoriaApi.tsdb(props.env, topn.value, props.tenant, refresh)
    const d = r.data || {}
    // Сверка топ-метрик перечитывает их серии count({__name__=~"…"}) — на БОЛЬШОМ
    // кластере это тяжёлый запрос (миллионы серий), поэтому делаем её только когда
    // серий немного; на крупных кластерах показываем счётчики самого VM как есть.
    const total = d.totalSeries ?? d.headStats?.numSeries ?? 0
    verified.value = !!total && total <= VERIFY_MAX_SERIES
    if (verified.value) {
      d.seriesCountByMetricName = await verifyMetricNames(d.seriesCountByMetricName)
    }
    data.value = d
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// /status/tsdb считает статистику за сутки: туда попадают и серии, которых уже нет
// (перезапуски подов, чужой мусор). Одним instant-запросом пересчитываем серии
// каждого имени именно в НАШЕМ тенанте/неймспейсе и оставляем только те метрики,
// что реально лежат в хранилище сейчас, — ничего не придумываем.
async function verifyMetricNames(list) {
  const names = (list || []).map((x) => x.name).filter(Boolean)
  if (!names.length) return []
  try {
    const expr = `count({__name__=~"${names.join('|')}"}) by (__name__)`
    const r = await victoriaApi.query(props.env, expr, null, props.tenant)
    const counts = new Map()
    for (const s of r.data?.result || []) counts.set(s.metric.__name__, parseFloat(s.value[1]))
    return names
      .filter((n) => counts.get(n) > 0)
      .map((n) => ({ name: n, value: counts.get(n) }))
      .sort((a, b) => b.value - a.value)
  } catch (e) {
    return list || [] // сверка не удалась — показываем как отдал VM
  }
}

const total = computed(() => {
  const d = data.value
  if (!d) return null
  return d.totalSeries ?? d.headStats?.numSeries ?? null
})

// Серии по сервису (лейбл service): берём готовую разбивку из tsdb-статуса (пары
// service=…) — без отдельного запроса, поэтому безопасно и на больших кластерах.
// Показывает топ сервисов по числу серий (сколько пар попало — зависит от «топ N»).
const SERVICE_LABEL = 'service'
const byService = computed(() => {
  const pref = SERVICE_LABEL + '='
  const pairs = (data.value || {}).seriesCountByLabelValuePair || []
  return pairs
    .filter((x) => x.name.startsWith(pref))
    .map((x) => ({ name: x.name.slice(pref.length), value: x.value }))
    .sort((a, b) => b.value - a.value)
})

const lists = computed(() => {
  const d = data.value || {}
  const topMetricsTitle = verified.value
    ? 'Топ метрик по числу серий (сверено с хранилищем)'
    : 'Топ метрик по числу серий'
  return [
    [topMetricsTitle, d.seriesCountByMetricName || []],
    ['Серии по сервису (service)', byService.value],
    ['Топ лейблов по числу серий', d.seriesCountByLabelName || []],
    ['Топ пар label=value', d.seriesCountByLabelValuePair || []],
    ['Число значений у лейбла', d.labelValueCountByLabelName || []],
  ].filter(([, arr]) => arr.length)
})

function maxOf(arr) { return arr.reduce((m, x) => Math.max(m, x.value || 0), 0) || 1 }
</script>

<template>
  <div>
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <div class="bar">
      <label class="lbl">Показывать топ
        <select class="input topn" v-model.number="topn" @change="loadTsdb()">
          <option :value="10">10</option>
          <option :value="20">20</option>
          <option :value="50">50</option>
        </select>
      </label>
      <span v-if="total !== null" class="total">Всего серий: <b>{{ total.toLocaleString() }}</b></span>
      <button class="btn btn-sm" :disabled="loading" @click="loadAll(true)">{{ loading ? 'Обновляю…' : 'Обновить' }}</button>
    </div>

    <!-- Загрузка — скелет-плашки на месте будущих карточек. -->
    <div v-if="loading" class="grid">
      <div v-for="i in 4" :key="i" class="card"><Skeleton :lines="6" :height="18" /></div>
    </div>

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

.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 860px) { .grid { grid-template-columns: 1fr; } }

.rows { display: flex; flex-direction: column; gap: 4px; }
/* Три столбца: имя | шкала | значение. Значение фиксированной ширины и вправо —
   так числа стоят ровным столбцом у всех строк. Строка под курсором подсвечена. */
.row {
  display: grid; grid-template-columns: minmax(0, 1fr) 88px 84px; align-items: center; gap: 12px;
  padding: 3px 6px; margin: 0 -6px; border-radius: 6px;
}
.row:hover { background: var(--panel-2); }
.name { font-family: var(--mono); font-size: 12px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar-track { height: 7px; background: var(--track); border-radius: 4px; overflow: hidden; }
.bar-fill { display: block; height: 100%; background: var(--accent); border-radius: 4px; }
.val { font-family: var(--mono); font-size: 12px; text-align: right; white-space: nowrap; }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }
</style>
