<script setup>
// Правила из vmalert (/api/v1/rules) — «как в silence-моде»: каждое правило можно
// РАСКРЫТЬ кликом и посмотреть состав (expr, labels, annotations, health) и активные
// алерты (rule.alerts — сработавшие серии). Только просмотр.
import { ref, computed, onMounted } from 'vue'
import { fmtDt } from '../../../shared/time.js'
import { victoriaApi } from '../api.js'

const props = defineProps({ env: { type: String, required: true } })

const groups = ref([]) // data.groups
const search = ref('')
const stateFilter = ref('all')
const openKey = ref(null)
const loading = ref(true)
const error = ref(null)

const STATES = [
  ['all', 'all'], ['firing', 'firing'], ['pending', 'pending'],
  ['inactive', 'inactive'], ['recording', 'recording'],
]

onMounted(load)

async function load() {
  loading.value = true
  error.value = null
  try {
    const r = await victoriaApi.rules(props.env)
    groups.value = r.data?.groups || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

// Сводка сверху: сколько горит/зреет по всем группам.
const stats = computed(() => {
  let firing = 0, pending = 0
  for (const g of groups.value) for (const r of g.rules || []) {
    if (r.type !== 'alerting') continue
    if (r.state === 'firing') firing++
    else if (r.state === 'pending') pending++
  }
  return { firing, pending }
})

function ruleMatches(r) {
  if (search.value) {
    const hay = ((r.name || '') + ' ' + (r.query || '')).toLowerCase()
    if (!hay.includes(search.value.toLowerCase())) return false
  }
  if (stateFilter.value === 'all') return true
  if (stateFilter.value === 'recording') return r.type === 'recording'
  return r.type === 'alerting' && r.state === stateFilter.value
}

// Группы с отфильтрованными правилами (пустые группы прячем).
const filtered = computed(() =>
  groups.value
    .map((g) => ({ ...g, rules: (g.rules || []).filter(ruleMatches) }))
    .filter((g) => g.rules.length),
)

function dotClass(r) {
  if (r.type === 'recording') return 'rec'
  return r.state || 'inactive'
}
function badge(r) {
  return r.type === 'recording' ? 'rec' : (r.state || 'inactive')
}
function fmtFor(s) {
  if (!s) return ''
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.round(m / 60)}ч` : `${m}м`
}
function labelsStr(labels) {
  return Object.entries(labels || {}).map(([k, v]) => `${k}="${v}"`).join('  ')
}
function keyOf(gi, ri) { return gi + '-' + ri }
function toggle(k) { openKey.value = openKey.value === k ? null : k }
</script>

<template>
  <div>
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <div class="filters">
      <div class="pills">
        <button v-for="[id, label] in STATES" :key="id" class="pill" :class="{ on: stateFilter === id }" @click="stateFilter = id">{{ label }}</button>
      </div>
      <input class="input search" v-model="search" placeholder="поиск по имени / выражению…" />
      <span class="counts">
        <span class="firing" :class="{ zero: !stats.firing }">firing {{ stats.firing }}</span>
        <span class="pending" :class="{ zero: !stats.pending }">pending {{ stats.pending }}</span>
      </span>
      <button class="btn btn-sm" @click="load">обновить</button>
    </div>

    <div v-if="loading" class="empty">Загрузка…</div>
    <div v-else-if="!filtered.length" class="empty">Правил не найдено.</div>

    <div v-for="(g, gi) in filtered" :key="gi" class="group">
      <div class="group-head">
        <span class="group-name">{{ g.name }}</span>
        <span v-if="g.file" class="group-file">{{ g.file }}</span>
        <span class="group-cnt">{{ g.rules.length }}</span>
      </div>

      <!-- Правила — раскрываемые строки -->
      <div v-for="(r, ri) in g.rules" :key="ri" class="rule" :class="{ open: openKey === keyOf(gi, ri) }">
        <div class="rule-row" @click="toggle(keyOf(gi, ri))">
          <span class="dot" :class="dotClass(r)"></span>
          <div class="rule-info">
            <div class="rule-head">
              <span class="nm">{{ r.name }}</span>
              <span class="state" :class="dotClass(r)">{{ badge(r) }}</span>
              <span v-if="r.duration" class="for">for {{ fmtFor(r.duration) }}</span>
              <span v-if="r.health && r.health !== 'ok'" class="mark err">{{ r.health }}</span>
            </div>
            <div class="sub" :title="r.query">{{ r.query }}</div>
          </div>
          <span class="chev">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
          </span>
        </div>

        <!-- Раскрытие: expr, лейблы, аннотации, служебное; ниже активные алерты. -->
        <div v-if="openKey === keyOf(gi, ri)" class="detail">
          <div class="kv-grid">
            <span class="k">expr</span>
            <span class="v">{{ r.query }}</span>
            <template v-for="(v, k) in r.labels" :key="'l-' + k">
              <span class="k">{{ k }}</span>
              <span class="v">{{ v }}</span>
            </template>
            <template v-for="(v, k) in r.annotations" :key="'a-' + k">
              <span class="k">{{ k }}</span>
              <span class="v">{{ v }}</span>
            </template>
            <span v-if="r.health" class="k">health</span>
            <span v-if="r.health" class="v">{{ r.health }}<template v-if="r.lastError"> — {{ r.lastError }}</template></span>
            <span v-if="r.lastEvaluation" class="k">last eval</span>
            <span v-if="r.lastEvaluation" class="v">{{ fmtDt(r.lastEvaluation) }}</span>
          </div>

          <!-- Активные алерты (сработавшие серии) для alerting-правил. -->
          <div v-if="r.alerts && r.alerts.length" class="insts">
            <div class="insts-head">активные алерты</div>
            <div v-for="(a, j) in r.alerts" :key="j" class="inst-row">
              <span class="inst-state" :class="a.state">{{ a.state }}</span>
              <span class="inst-labels">{{ labelsStr(a.labels) }}</span>
              <span v-if="a.value !== undefined" class="inst-val">= {{ a.value }}</span>
              <span v-if="a.activeAt" class="inst-since">с {{ fmtDt(a.activeAt) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.pills { display: flex; gap: 4px; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 8px; padding: 4px; }
.pill { background: transparent; border: none; color: var(--text-dim); border-radius: 6px; padding: 5px 11px; font-size: 12px; }
.pill:hover { color: var(--text); }
.pill.on { background: var(--panel-2); color: var(--accent-bright); }
.search { width: auto; flex: 1; min-width: 160px; }
.counts { display: inline-flex; gap: 10px; font-family: var(--mono); font-size: 12px; }
.counts .firing { color: var(--danger); }
.counts .pending { color: #ffb86c; }
.counts .zero { color: var(--text-mute); }

.group { margin-bottom: 18px; }
.group-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.group-name { font-weight: 700; font-size: 14px; font-family: var(--mono); }
.group-file { font-family: var(--mono); font-size: 11px; color: var(--text-mute); }
.group-cnt { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--text-mute); background: var(--chip); padding: 1px 8px; border-radius: 20px; }

.rule { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 9px; padding: 9px 14px; margin-bottom: 7px; transition: border-color 0.12s; }
.rule:hover { border-color: var(--border); }
.rule.open { border-color: var(--accent); }
.rule-row { display: flex; align-items: center; gap: 11px; cursor: pointer; user-select: none; min-height: 38px; }
.chev { display: flex; color: var(--text-mute); transition: transform 0.15s; flex: none; }
.rule.open .chev { transform: rotate(180deg); }

.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--track); flex: none; }
.dot.firing { background: var(--danger); }
.dot.pending { background: #ffb86c; }
.dot.inactive { background: var(--track); }
.dot.rec { background: var(--info); }

.rule-info { flex: 1; min-width: 0; }
.rule-head { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; min-width: 0; }
.nm { font-weight: 600; font-size: 14px; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state, .for, .mark { flex: none; }
.state { font-size: 10px; padding: 1px 7px; border-radius: 20px; background: var(--chip); color: var(--text-dim); text-transform: uppercase; }
.state.firing { background: rgba(255,85,85,0.16); color: var(--danger); }
.state.pending { background: rgba(255,184,108,0.16); color: #ffb86c; }
.state.inactive { background: var(--chip); color: var(--text-mute); }
.state.rec { background: var(--info-soft); color: var(--info); }
.mark.err { font-size: 10px; padding: 1px 7px; border-radius: 20px; background: rgba(255,85,85,0.16); color: var(--danger); }
.for { font-family: var(--mono); font-size: 11px; color: var(--text-mute); margin-left: auto; }
.sub { font-family: var(--mono); font-size: 12px; color: var(--text-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.kv-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; font-family: var(--mono); font-size: 12px; }
.kv-grid .k { color: var(--text-mute); }
.kv-grid .v { color: var(--text); word-break: break-all; }
.insts { margin-top: 12px; }
.insts-head { font-size: 11px; color: var(--text-mute); margin-bottom: 6px; }
.inst-row { display: flex; gap: 10px; align-items: baseline; font-family: var(--mono); font-size: 12px; padding: 4px 0; border-top: 1px dashed var(--border-soft); }
.inst-state { flex: none; text-transform: uppercase; font-size: 10px; }
.inst-state.firing { color: var(--danger); }
.inst-state.pending { color: #ffb86c; }
.inst-labels { color: var(--text); word-break: break-all; flex: 1; }
.inst-val { color: var(--text-dim); flex: none; }
.inst-since { color: var(--text-mute); flex: none; }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }
</style>
