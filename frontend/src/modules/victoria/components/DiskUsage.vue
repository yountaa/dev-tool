<script setup>
// Под-вкладка «Диск» (справа от TSDB Status): заполненность дисков по ВСЕМ
// кластерам VM одним списком, в разбивке по лейблу `group` (группы vmstorage).
// В отличие от других под-вкладок не зависит от выбранного кластера — бэкенд
// (/victoria/disk-usage) обходит все vm_<env> из окружения и по каждому отдаёт
// строку НА КАЖДУЮ группу: занято/свободно/прогноз заполнения (по тем же
// запросам sum by(group) + предиктивный ETA, что и раньше, но без сворачивания
// кластера в одно число).
import { ref, onMounted } from 'vue'
import Skeleton from '../../../shared/Skeleton.vue'
import { victoriaApi } from '../api.js'

const rows = ref([]) // [{ env, group, used, free, total, percent, eta_days } | { env, error }]
const loading = ref(true)
const error = ref(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    rows.value = await victoriaApi.diskUsage()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
onMounted(load)

// Байты → человекочитаемо (КиБ/МиБ/…). null/undefined → «—».
function fmtBytes(n) {
  if (n == null || isNaN(n)) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

// Цвет полосы по заполненности: спокойный < 75%, тёплый < 90%, тревожный дальше.
function fillClass(percent) {
  if (percent >= 90) return 'crit'
  if (percent >= 75) return 'warn'
  return 'ok'
}

// ETA (дни до заполнения) → человекочитаемо + цвет. null/NaN — метрик нет («—»);
// <=0 — роста нет («стабильно»); дальше дни/месяцы, с предупреждением на близком сроке.
function fmtEta(days) {
  if (days == null || isNaN(days)) return { text: '—', cls: 'mute' }
  if (days <= 0) return { text: 'стабильно', cls: 'ok' }
  if (days > 730) return { text: '> 2 лет', cls: 'ok' }
  if (days > 60) return { text: `~${Math.round(days / 30)} мес`, cls: days < 90 ? 'warn' : 'ok' }
  return { text: `~${Math.round(days)} дн`, cls: days < 14 ? 'bad' : 'warn' }
}
</script>

<template>
  <div class="disk">
    <div class="head">
      <button class="btn" :disabled="loading" @click="load">{{ loading ? '…' : 'Обновить' }}</button>
    </div>

    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <Skeleton v-if="loading && !rows.length" :lines="4" :height="52" />

    <div v-else-if="rows.length" class="card">
      <div class="tbl-scroll">
      <table class="tbl">
        <thead>
          <tr>
            <th class="l">Кластер</th>
            <th class="l">Группа</th>
            <th class="r">Занято</th>
            <th class="r">Свободно</th>
            <th class="r">Всего</th>
            <th class="r">Заполнится</th>
            <th class="fillcol">Заполнено</th>
          </tr>
        </thead>
        <tbody>
          <!-- Имя кластера пишем один раз на блок его групп; граница между
               кластерами чуть заметнее (cluster-start), внутри блока — мягкая. -->
          <tr
            v-for="(r, i) in rows"
            :key="r.env + '|' + (r.group ?? '')"
            :class="{ 'cluster-start': i > 0 && rows[i - 1].env !== r.env }"
          >
            <td class="l mono envcell">{{ i === 0 || rows[i - 1].env !== r.env ? r.env : '' }}</td>
            <template v-if="r.error">
              <td class="err" colspan="6">{{ r.error }}</td>
            </template>
            <template v-else>
              <!-- Метрики без лейбла group (обычный Prometheus) — группа «—» -->
              <td class="l mono grp">{{ r.group || '—' }}</td>
              <td class="r mono">{{ fmtBytes(r.used) }}</td>
              <td class="r mono">{{ fmtBytes(r.free) }}</td>
              <td class="r mono">{{ fmtBytes(r.total) }}</td>
              <td class="r mono eta" :class="fmtEta(r.eta_days).cls" title="примерное время до заполнения диска">{{ fmtEta(r.eta_days).text }}</td>
              <td class="fillcol">
                <div class="fill">
                  <div class="bar">
                    <div class="bar-in" :class="fillClass(r.percent)" :style="{ width: Math.min(100, r.percent) + '%' }"></div>
                  </div>
                  <span class="pct mono" :class="fillClass(r.percent)">{{ r.percent.toFixed(1) }}%</span>
                </div>
              </td>
            </template>
          </tr>
        </tbody>
      </table>
      </div>
    </div>

    <div v-else-if="!error" class="empty">Нет настроенных кластеров (задай vm_&lt;env&gt; в окружении).</div>
  </div>
</template>

<style scoped>
.disk { display: flex; flex-direction: column; }
.head { display: flex; align-items: center; justify-content: flex-end; margin-bottom: 18px; }

/* На узком окне таблица скроллится внутри себя, а не обрезается/распирает страницу. */
.tbl-scroll { overflow-x: auto; }
/* Не растягиваем на всю ширину рабочей области — иначе колонок мало, они широкие,
   и заголовок отъезжает далеко от своих чисел. */
.tbl { width: 100%; max-width: 1120px; min-width: 620px; border-collapse: collapse; font-size: 13px; }
.tbl th { color: var(--text-mute); font-weight: 600; padding: 9px 10px; border-bottom: 1px solid var(--border-soft); }
.tbl td { padding: 11px 10px; border-bottom: 1px solid var(--border-soft); }
.tbl tr:last-child td { border-bottom: none; }
/* Выравнивание задаём и заголовку, и ячейке ОДНОЙ парой правил — чтобы «Занято»
   стояло ровно над своими числами (было: .tbl th перебивал .r и заголовки уезжали
   влево, а числа держались справа → «не друг под другом»). */
.tbl th.l, .tbl td.l { text-align: left; }
.tbl th.r, .tbl td.r { text-align: right; white-space: nowrap; }
.mono { font-family: var(--mono); }
/* Имя кластера — раз на блок групп, полужирно; граница между кластерами заметнее. */
.envcell { font-weight: 600; }
.cluster-start td { border-top: 1px solid var(--border); }
/* Группа vmstorage — приглушённо, чтобы взгляд цеплялся сначала за кластер. */
.grp { color: var(--text-dim); white-space: nowrap; }
.err { color: var(--danger); font-family: var(--mono); font-size: 12px; }
/* ETA (Заполнится): цвет по срочности — спокойные тона, читаются в обеих темах. */
.eta.ok { color: #3fae72; }
.eta.warn { color: #dd9a3e; }
.eta.bad { color: var(--danger); }
.eta.mute { color: var(--text-mute); }

/* Колонка «Заполнено»: полоса + процент. Цвета в тон ETA. */
.fillcol { width: 40%; min-width: 220px; }
.fill { display: flex; align-items: center; gap: 12px; }
.bar { flex: 1; height: 9px; border-radius: 6px; background: var(--track); overflow: hidden; }
.bar-in { height: 100%; border-radius: 6px; transition: width 0.3s; }
.bar-in.ok { background: var(--accent); }
.bar-in.warn { background: #dd9a3e; }
.bar-in.crit { background: var(--danger); }
.pct { font-size: 12px; min-width: 52px; text-align: right; font-weight: 600; }
.pct.warn { color: #dd9a3e; }
.pct.crit { color: var(--danger); }

.empty { color: var(--text-mute); font-size: 13px; padding: 20px 0; }
</style>
