<script setup>
// Вкладка TSDB Status: кардинальность (/api/v1/status/tsdb) + прогноз заполнения
// дисков по нодам vmstorage. Прогноз строим из self-метрик VM:
//   vm_free_disk_space_bytes — свободно на ноде,
//   vm_data_size_bytes       — занято данными,
//   rate(vm_data_size_bytes)  — скорость роста → ETA = свободно / рост.
// Если этих метрик нет в источнике (напр. обычный Prometheus) — прогноз скрыт.
import { ref, computed, onMounted } from 'vue'
import Skeleton from '../../../shared/Skeleton.vue'
import { victoriaApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null },
})

const data = ref(null)
const groups = ref([])      // прогноз заполнения по группам vmstorage
const topn = ref(20)
const loading = ref(true)
const error = ref(null)
const verified = ref(false) // сверяли ли топ-метрики с хранилищем (на больших кластерах — нет)

// Порог, выше которого НЕ пересчитываем серии топ-метрик (count по __name__ на
// миллионах серий кладёт нагрузку на vmstorage). Число серий берём из tsdb-статуса.
const VERIFY_MAX_SERIES = 5000000

onMounted(() => loadAll())

// refresh=true (кнопка «Обновить») — /status/tsdb перечитывается мимо кэша бэкенда.
async function loadAll(refresh = false) {
  await Promise.all([loadTsdb(refresh), loadForecast()])
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

// Оценка «за сколько дней заполнится диск» по группам vmstorage. Считает сам VM:
// свободное место (за вычетом резерва) делим на скорость роста данных, где рост
// учитывает сжатие/дедуп (байт на строку) и отдельно рост индекса (new timeseries).
// Усредняем за 10 минут, чтобы одиночный всплеск не искажал прогноз. Всё в
// props.tenant — цифры именно этого namespace, а не суммарно по всем.
const ETA_DAYS_EXPR = `
avg_over_time((
  min by(group)(
    (vm_free_disk_space_bytes - vm_free_disk_space_limit_bytes)
      / ignoring(path)
    (
      (rate(vm_rows_added_to_storage_total[1d]) - sum without(type)(rate(vm_deduplicated_samples_total[1d])))
        * (sum without(type)(vm_data_size_bytes{type!~"indexdb.*"}) / sum without(type)(vm_rows{type!~"indexdb.*"}))
      + rate(vm_new_timeseries_created_total[1d])
        * (sum without(type)(vm_data_size_bytes{type="indexdb/file"}) / sum without(type)(vm_rows{type="indexdb/file"}))
    )
  ) / 86400
)[10m:])`.trim()

async function loadForecast() {
  try {
    // Три лёгких запроса по self-метрикам (низкая кардинальность): дни до заполнения
    // по формуле выше + свободно/занято по группам для контекста.
    const [eta, free, used] = await Promise.all([
      victoriaApi.query(props.env, ETA_DAYS_EXPR, null, props.tenant),
      victoriaApi.query(props.env, 'min by(group)(vm_free_disk_space_bytes - vm_free_disk_space_limit_bytes)', null, props.tenant),
      victoriaApi.query(props.env, 'sum by(group)(vm_data_size_bytes)', null, props.tenant),
    ])
    const by = {}
    const put = (resp, key) => {
      for (const s of resp.data?.result || []) {
        const g = (s.metric && (s.metric.group || s.metric.instance)) || '(group)'
        if (!by[g]) by[g] = { group: g }
        by[g][key] = parseFloat(s.value[1])
      }
    }
    put(eta, 'days'); put(free, 'free'); put(used, 'used')
    groups.value = Object.values(by).sort((a, b) => a.group.localeCompare(b.group))
  } catch (e) {
    groups.value = [] // нет self-метрик — просто не показываем прогноз
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

// --- Форматтеры ---
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—'
  const u = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let i = 0, v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`
}
// На вход — дни до заполнения (из ETA_DAYS_EXPR). Отрицательные/нулевые/пустые
// значит роста нет → «стабильно».
function fmtEta(days) {
  if (days == null || isNaN(days) || days <= 0) return { text: 'стабильно', cls: 'ok' }
  if (days > 730) return { text: '> 2 лет', cls: 'ok' }
  if (days > 60) return { text: `~${Math.round(days / 30)} мес`, cls: days < 90 ? 'warn' : 'ok' }
  return { text: `~${Math.round(days)} дн`, cls: days < 14 ? 'bad' : 'warn' }
}
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

    <!-- Прогноз заполнения дисков по группам vmstorage -->
    <div v-if="groups.length" class="card">
      <div class="card-title">Заполнение дисков по группам vmstorage</div>
      <div class="tbl-scroll">
        <table class="nodes">
          <thead>
            <tr><th class="l">Группа</th><th class="r">Свободно</th><th class="r">Занято</th><th class="r">Заполнится</th></tr>
          </thead>
          <tbody>
            <tr v-for="n in groups" :key="n.group">
              <td class="l mono">{{ n.group }}</td>
              <td class="r mono">{{ fmtBytes(n.free) }}</td>
              <td class="r mono">{{ fmtBytes(n.used) }}</td>
              <td class="r mono eta" :class="fmtEta(n.days).cls">{{ fmtEta(n.days).text }}</td>
            </tr>
          </tbody>
        </table>
      </div>
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

/* Таблица нод — числа выровнены по правому краю, единым столбцом.
   На узком окне скроллится внутри себя, а не распирает страницу вбок. */
.tbl-scroll { overflow-x: auto; }
/* table-layout: fixed — ширина числовых колонок НЕ зависит от содержимого
   (иначе «1.0 TiB» и «953 GiB» давали разную ширину, и колонки «прыгали» от
   строки к строке и от кластера к кластеру). Числовые колонки одинаковой ширины
   везде; имя группы забирает остаток и обрезается многоточием. */
.nodes { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
.nodes th { color: var(--text-mute); font-weight: 600; padding: 6px 10px; border-bottom: 1px solid var(--border-soft); }
.nodes td { padding: 7px 10px; border-bottom: 1px solid var(--border-soft); }
.nodes .l { text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nodes .r { text-align: right; white-space: nowrap; width: 120px; }
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
