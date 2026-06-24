<script setup>
// Вкладка «Алерты»: боевые алерты окружения прямо из Alertmanager (только чтение).
// По образцу «Рабочих правил» — статус, фильтры, поиск; раскрытие показывает все
// лейблы и описание. Менять отсюда нечего: это зеркало того, что сейчас в AM.
import { ref, computed } from 'vue'

const props = defineProps({
  env: { type: String, required: true },
  items: { type: Array, required: true }, // алерты активного env (грузит родитель)
})
const emit = defineEmits(['reload'])

// active — горит, уведомления идут; suppressed — заглушён silence или подавлен инхибитом.
const STATES = [['all', 'all'], ['active', 'active'], ['suppressed', 'suppressed']]
const stateFilter = ref('all')
const query = ref('')
const openKey = ref(null)

function alertname(a) {
  return a.labels?.alertname || '(без alertname)'
}
// Бейдж состояния — технический термин AM как есть (active / suppressed).
function stateText(s) {
  return s || '—'
}
// Лейблы для подписи — без alertname (он уже в заголовке).
function labelPairs(a) {
  return Object.entries(a.labels || {})
    .filter(([k]) => k !== 'alertname')
    .map(([k, v]) => `${k}=${v}`)
}
function fmtDt(s) {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d)) return s
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function why(a) {
  if (a.silenced_by?.length) return 'заглушён silence'
  if (a.inhibited_by?.length) return 'подавлен другим алертом'
  return ''
}
function subText(a) {
  const parts = labelPairs(a)
  const w = why(a)
  if (w) parts.push(w)
  return parts.join('   ·   ')
}
function keyOf(a, i) {
  return alertname(a) + i
}

const filtered = computed(() =>
  props.items.filter((a) => {
    if (stateFilter.value !== 'all' && a.state !== stateFilter.value) return false
    if (query.value) {
      const hay = `${alertname(a)} ${labelPairs(a).join(' ')}`.toLowerCase()
      if (!hay.includes(query.value.toLowerCase())) return false
    }
    return true
  }),
)

function toggle(key) {
  openKey.value = openKey.value === key ? null : key
}
</script>

<template>
  <div>
    <div class="row-head">
      <div>
        <h2 class="tab-title">Алерты</h2>
        <p class="tab-desc">Боевые алерты окружения <b>{{ env }}</b> прямо из Alertmanager — только просмотр.</p>
      </div>
      <button class="btn btn-sm" @click="emit('reload')">обновить</button>
    </div>

    <div class="filters">
      <div class="pills">
        <button v-for="[id, label] in STATES" :key="id" class="pill" :class="{ on: stateFilter === id }" @click="stateFilter = id">{{ label }}</button>
      </div>
      <input class="input search" v-model="query" placeholder="search by name or labels…" />
    </div>

    <p v-if="!filtered.length" class="tab-desc">Алертов нет.</p>

    <div v-for="(a, i) in filtered" :key="keyOf(a, i)" class="rule" :class="{ open: openKey === keyOf(a, i) }">
      <div class="rule-row" @click="toggle(keyOf(a, i))">
        <span class="dot" :class="a.state"></span>
        <div class="rule-info">
          <div class="rule-head">
            <span class="nm">{{ alertname(a) }}</span>
            <span class="state" :class="a.state">{{ stateText(a.state) }}</span>
            <span v-if="a.starts_at" class="since">с {{ fmtDt(a.starts_at) }}</span>
          </div>
          <div class="sub" :title="subText(a)">{{ subText(a) }}</div>
        </div>
        <span class="chev">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
      </div>

      <!-- Раскрытие: все лейблы и описание алерта. -->
      <div v-if="openKey === keyOf(a, i)" class="detail">
        <div class="kv-grid">
          <template v-for="(v, k) in a.labels" :key="k">
            <span class="k">{{ k }}</span>
            <span class="v">{{ v }}</span>
          </template>
        </div>
        <div v-if="a.annotations && Object.keys(a.annotations).length" class="ann">
          <div v-for="(v, k) in a.annotations" :key="k" class="ann-row">
            <span class="ann-k">{{ k }}</span>
            <span class="ann-v">{{ v }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.row-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 12px; }
.tab-title { font-size: 17px; font-weight: 700; margin: 2px 0; }
.tab-desc { color: var(--text-dim); margin: 0; font-size: 13px; }

.filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.pills { display: flex; gap: 4px; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 8px; padding: 4px; }
.pill { background: transparent; border: none; color: var(--text-dim); border-radius: 6px; padding: 5px 11px; font-size: 12px; }
.pill:hover { color: var(--text); }
.pill.on { background: var(--panel-2); color: var(--accent-bright); }
.search { width: auto; flex: 1; min-width: 160px; }

.rule { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 9px; padding: 9px 14px; margin-bottom: 7px; transition: border-color 0.12s; }
.rule:hover { border-color: var(--border); }
.rule.open { border-color: var(--accent); }
.rule-row { display: flex; align-items: center; gap: 11px; cursor: pointer; user-select: none; }
.chev { display: flex; color: var(--text-mute); transition: transform 0.15s; }
.rule.open .chev { transform: rotate(180deg); }

.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--track); flex: none; }
.dot.active { background: var(--danger); }
.dot.suppressed { background: var(--track); }

.rule-info { flex: 1; min-width: 0; }
.rule-head { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; min-width: 0; }
.nm { font-weight: 600; font-size: 14px; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state, .since { flex: none; }
.state { font-size: 10px; padding: 1px 6px; border-radius: 20px; background: var(--chip); color: var(--text-dim); }
.state.active { background: var(--danger-soft, rgba(220,80,80,0.16)); color: var(--danger); }
.state.suppressed { background: var(--chip); color: var(--text-mute); }
.since { font-family: var(--mono); font-size: 11px; color: var(--text-mute); margin-left: auto; }

.sub { font-family: var(--mono); font-size: 12px; color: var(--text-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.kv-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; font-family: var(--mono); font-size: 12px; }
.kv-grid .k { color: var(--text-mute); }
.kv-grid .v { color: var(--text); word-break: break-all; }
.ann { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.ann-row { font-size: 12px; }
.ann-k { color: var(--text-mute); margin-right: 8px; }
.ann-v { color: var(--text-dim); }
</style>
