<script setup>
// Цели скрейпа из vmagent (/api/v1/targets) — «как в Prometheus»: сгруппированы по
// job (scrapePool), каждая группа раскрывается/сворачивается, в шапке счётчик up/total.
import { ref, computed, onMounted } from 'vue'
import { victoriaApi } from '../api.js'

const props = defineProps({ env: { type: String, required: true } })

const targets = ref([]) // activeTargets
const search = ref('')
const onlyDown = ref(false)
const collapsed = ref({}) // { [pool]: true } — свёрнутые группы (по умолчанию раскрыты)
const loading = ref(true)
const error = ref(null)

onMounted(load)

async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await victoriaApi.targets(props.env)
    targets.value = r.data?.activeTargets || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function instance(t) {
  return (t.labels && (t.labels.instance || t.labels.job)) || t.scrapeUrl || ''
}
function labelsStr(labels) {
  return Object.entries(labels || {}).map(([k, v]) => `${k}="${v}"`).join(', ')
}

// Здоровье считаем по ВСЕМ таргетам job'а (не зависит от фильтра/поиска).
const healthByPool = computed(() => {
  const h = {}
  for (const t of targets.value) {
    const p = t.scrapePool || '(no pool)'
    if (!h[p]) h[p] = { up: 0, total: 0 }
    h[p].total++
    if (t.health === 'up') h[p].up++
  }
  return h
})

// Группы для показа (с учётом поиска и «только down»), отсортированы по имени job.
const groups = computed(() => {
  const q = search.value.trim().toLowerCase()
  const map = new Map()
  for (const t of targets.value) {
    if (onlyDown.value && t.health === 'up') continue
    if (q) {
      const hay = ((t.scrapePool || '') + ' ' + (t.scrapeUrl || '') + ' ' + JSON.stringify(t.labels || {})).toLowerCase()
      if (!hay.includes(q)) continue
    }
    const p = t.scrapePool || '(no pool)'
    if (!map.has(p)) map.set(p, [])
    map.get(p).push(t)
  }
  return [...map.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([pool, ts]) => ({ pool, targets: ts, ...healthByPool.value[pool] }))
})

const totalUp = computed(() => targets.value.filter((t) => t.health === 'up').length)
const totalDown = computed(() => targets.value.length - totalUp.value)

function toggle(pool) { collapsed.value = { ...collapsed.value, [pool]: !collapsed.value[pool] } }
function setAll(state) {
  const c = {}
  if (state) for (const g of groups.value) c[g.pool] = true
  collapsed.value = c
}
</script>

<template>
  <div>
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <div class="bar">
      <input class="input" v-model="search" placeholder="поиск: job / instance / label" />
      <label class="chk"><input type="checkbox" v-model="onlyDown" /> только down</label>
      <button class="btn btn-sm" @click="setAll(false)">развернуть всё</button>
      <button class="btn btn-sm" @click="setAll(true)">свернуть всё</button>
      <span class="counts">
        <span class="up">up {{ totalUp }}</span>
        <span class="down" :class="{ zero: !totalDown }">down {{ totalDown }}</span>
      </span>
      <button class="btn btn-sm" @click="load">Обновить</button>
    </div>

    <div v-if="loading" class="empty">Загрузка…</div>
    <div v-else-if="!groups.length" class="empty">Целей не найдено.</div>

    <!-- Группы-джобы -->
    <div v-for="g in groups" :key="g.pool" class="group">
      <button class="group-head" @click="toggle(g.pool)">
        <span class="chev" :class="{ open: !collapsed[g.pool] }">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
        <span class="group-name">{{ g.pool }}</span>
        <span class="group-count" :class="{ bad: g.up < g.total }">{{ g.up }}/{{ g.total }} up</span>
      </button>

      <div v-if="!collapsed[g.pool]" class="tbl-scroll">
        <table class="tbl">
          <thead>
            <tr><th></th><th>Instance</th><th>Endpoint</th><th>Labels</th><th>Ошибка</th></tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in g.targets" :key="i">
              <td><span class="dot" :class="t.health === 'up' ? 'ok' : 'bad'"></span></td>
              <td class="mono">{{ instance(t) }}</td>
              <td class="mono url">{{ t.scrapeUrl }}</td>
              <td class="mono lbl">{{ labelsStr(t.labels) }}</td>
              <td class="err">{{ t.lastError }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
.bar .input { width: 260px; }
.chk { font-size: 12px; color: var(--text-dim); display: inline-flex; align-items: center; gap: 5px; font-family: var(--mono); }
.counts { display: inline-flex; gap: 10px; font-family: var(--mono); font-size: 12px; margin-left: auto; }
.counts .up { color: #50fa7b; }
.counts .down { color: var(--danger); }
.counts .down.zero { color: var(--text-mute); }

.group { margin-bottom: 12px; border: 1px solid var(--border-soft); border-radius: 9px; overflow: hidden; }
.group-head {
  display: flex; align-items: center; gap: 10px; width: 100%;
  background: var(--panel); border: none; padding: 11px 14px; text-align: left;
}
.group-head:hover { background: var(--panel-2); }
.chev { display: flex; color: var(--text-mute); transition: transform 0.12s; flex: none; transform: rotate(-90deg); }
.chev.open { transform: rotate(0deg); }
.group-name { font-weight: 700; font-size: 14px; font-family: var(--mono); }
.group-count { margin-left: auto; font-family: var(--mono); font-size: 12px; color: #50fa7b; }
.group-count.bad { color: var(--danger); }

/* На узком окне таблица скроллится внутри себя, а не распирает страницу вбок. */
.tbl-scroll { overflow-x: auto; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl th { text-align: left; color: var(--text-mute); font-weight: 600; padding: 7px 12px; border-top: 1px solid var(--border-soft); background: var(--panel-2); }
.tbl td { padding: 7px 12px; border-top: 1px solid var(--border-soft); vertical-align: top; }
.mono { font-family: var(--mono); }
.url { color: var(--text-dim); word-break: break-all; }
.lbl { color: var(--text-mute); font-size: 12px; word-break: break-word; max-width: 320px; }
.err { color: var(--danger); font-family: var(--mono); font-size: 12px; word-break: break-word; }
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; }
.dot.ok { background: #50fa7b; }
.dot.bad { background: var(--danger); }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }
</style>
