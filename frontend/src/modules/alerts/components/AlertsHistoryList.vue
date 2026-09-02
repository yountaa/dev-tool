<script setup>
// История изменений алертов. UI как в Silence Manager: before|after + подсветка diff.
import { ref, computed, watch } from 'vue'
import { fmtDt } from '../../../shared/time.js'

const props = defineProps({
  items: { type: Array, required: true },
})
const emit = defineEmits(['reload'])

const query = ref('')
const openKey = ref(null)

const ACT = {
  создал: 'created',
  изменил: 'edited',
  удалил: 'deleted',
  включил: 'enabled',
  выключил: 'disabled',
  create: 'created',
  update: 'edited',
  delete: 'deleted',
}

const LABELS = {
  name: 'name',
  alertKey: 'alertKey',
  enabled: 'enabled',
  type: 'type',
  id: 'id',
  'source.index': 'index',
  'source.timeField': 'timeField',
  'source.kql': 'kql',
  'notify.alertId': 'alertId',
  'notify.title': 'title',
  'notify.subjectTemplate': 'subject',
  'notify.helpUrl': 'helpUrl',
  'delivery.from': 'from',
  'delivery.to': 'to',
  'delivery.email.from': 'from',
  'delivery.email.to': 'to',
  'schedule.intervalMinutes': 'interval',
  'schedule.trigger': 'trigger',
  'schedule.sendWindow.enabled': 'sendWindow',
  'schedule.sendWindow.from': 'sendFrom',
  'schedule.sendWindow.to': 'sendTo',
  'schedule.sendWindow.days': 'sendDays',
  'schedule.sendWindow.timezone': 'sendTz',
  'rule.minCount': 'minCount',
  'rule.mode': 'mode',
  'rule.groupBy': 'groupBy',
  'rule.emptyPolicy': 'emptyPolicy',
  'rule.thresholdMinutes': 'threshold',
  'rule.repeatMinutes': 'repeat',
  'rule.hostField': 'hostField',
  'rule.messageField': 'messageField',
  'rule.standbyMarker': 'standby',
  'presentation.layout': 'layout',
}

const ORDER = [
  'name', 'alertKey', 'enabled', 'type', 'id',
  'source.index', 'source.timeField', 'source.kql',
  'notify.alertId', 'notify.title', 'notify.subjectTemplate', 'notify.helpUrl',
  'delivery.to', 'delivery.from', 'delivery.email.to', 'delivery.email.from',
  'schedule.intervalMinutes', 'schedule.trigger',
  'schedule.sendWindow.enabled', 'schedule.sendWindow.from', 'schedule.sendWindow.to',
  'schedule.sendWindow.days', 'schedule.sendWindow.timezone',
  'rule.minCount', 'rule.mode', 'rule.groupBy', 'rule.emptyPolicy',
  'rule.thresholdMinutes', 'rule.repeatMinutes',
  'rule.hostField', 'rule.messageField', 'rule.standbyMarker',
  'presentation.layout',
]

function actionLabel(a) { return ACT[a] || a || 'changed' }
function caption(h) {
  const l = actionLabel(h.action)
  return l.charAt(0).toUpperCase() + l.slice(1)
}
function labelOf(k) { return LABELS[k] || k.split('.').pop() || k }

function flatten(obj, prefix = '') {
  const out = {}
  if (!obj || typeof obj !== 'object') return out
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      Object.assign(out, flatten(v, key))
    } else {
      out[key] = v
    }
  }
  return out
}

function display(k, v) {
  if (v === undefined || v === null || v === '') return ''
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (k.endsWith('intervalMinutes') || k.endsWith('thresholdMinutes') || k.endsWith('repeatMinutes') || k === 'intervalMinutes') {
    return `${v} min`
  }
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function fields(h) {
  const before = flatten(h.before)
  const after = flatten(h.after)
  const keys = new Set([...Object.keys(before), ...Object.keys(after)])
  let ordered = ORDER.filter((k) => keys.has(k))
  for (const k of keys) if (!ordered.includes(k)) ordered.push(k)
  // Для edited с before+after — только изменившиеся поля.
  if (h.before && h.after) {
    const diffOnly = ordered.filter((k) => {
      const lv = display(k, before[k])
      const rv = display(k, after[k])
      return lv !== rv
    })
    if (diffOnly.length) ordered = diffOnly
  }
  return ordered
}
function leftVal(h, k) {
  return h.before ? display(k, flatten(h.before)[k]) : ''
}
function rightVal(h, k) {
  return h.after ? display(k, flatten(h.after)[k]) : ''
}
function changed(h, k) {
  if (!h.before || !h.after) return true
  return leftVal(h, k) !== rightVal(h, k)
}
function keyOf(h, i) { return (h.time || h.ts || '') + ':' + (h.alertKey || '') + ':' + i }

function subText(h) {
  if (h.before && h.after) {
    const ch = fields(h).filter((k) => changed(h, k)).map(labelOf)
    return ch.length ? 'changed: ' + ch.join(', ') : 'no changes'
  }
  const src = h.after || h.before || {}
  return src.name || src.alertKey || ''
}

const filtered = computed(() =>
  props.items.filter((h) => {
    if (!query.value) return true
    const hay = `${h.user || ''} ${h.name || ''} ${h.alertKey || ''} ${actionLabel(h.action)}`.toLowerCase()
    return hay.includes(query.value.toLowerCase())
  }),
)

const PAGE_SIZE = 10
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const paged = computed(() => filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
watch(query, () => { page.value = 1 })
watch(page, () => { openKey.value = null })

function toggle(key) {
  openKey.value = openKey.value === key ? null : key
}
</script>

<template>
  <div class="hist">
    <div class="row-head">
      <div>
        <h2 class="tab-title">History</h2>
        <p class="tab-desc">Who changed alerts and when.</p>
      </div>
      <button class="btn btn-sm" @click="emit('reload')">обновить</button>
    </div>

    <div class="filters">
      <input class="input search" v-model="query" placeholder="search by user or name…" />
    </div>

    <p v-if="!filtered.length" class="tab-desc">No history yet.</p>

    <div v-for="(h, i) in paged" :key="keyOf(h, i)" class="rule" :class="{ open: openKey === keyOf(h, i) }">
      <div class="rule-row" @click="toggle(keyOf(h, i))">
        <span class="dot"></span>
        <div class="rule-info">
          <div class="rule-head">
            <span class="user">{{ h.user || '—' }}</span>
            <span class="action">{{ actionLabel(h.action) }}</span>
            <span class="nm">{{ h.name || h.alertKey || '(без имени)' }}</span>
            <span class="when">{{ fmtDt(h.time || h.ts) }}</span>
          </div>
          <div class="sub" :title="subText(h)">{{ subText(h) }}</div>
        </div>
        <span class="chev">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
      </div>

      <div v-if="openKey === keyOf(h, i)" class="detail">
        <div class="cap">{{ caption(h) }}<span v-if="h.before && h.after" class="cap-hint"> — только изменения</span></div>
        <div class="cols">
          <div class="col">
            <div class="col-h">before</div>
            <p v-if="!h.before" class="empty">—</p>
            <div
              v-for="k in fields(h)" :key="'b-' + k"
              class="kv"
              :class="{ diff: changed(h, k) }"
            >
              <span class="kk" :title="k">{{ labelOf(k) }}</span>
              <span
                class="vv"
                :class="{ rm: !!leftVal(h, k) && (!h.after || changed(h, k)) }"
              >{{ leftVal(h, k) || '—' }}</span>
            </div>
          </div>
          <div class="col">
            <div class="col-h">after</div>
            <p v-if="!h.after" class="empty">—</p>
            <div
              v-for="k in fields(h)" :key="'a-' + k"
              class="kv"
              :class="{ diff: changed(h, k) }"
            >
              <span class="kk" :title="k">{{ labelOf(k) }}</span>
              <span
                class="vv"
                :class="{ add: !!rightVal(h, k) && (!h.before || changed(h, k)) }"
              >{{ rightVal(h, k) || '—' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="totalPages > 1" class="pager">
      <button class="btn btn-sm" :disabled="page === 1" @click="page--">←</button>
      <span class="pager-info">{{ page }} / {{ totalPages }}</span>
      <button class="btn btn-sm" :disabled="page === totalPages" @click="page++">→</button>
    </div>
  </div>
</template>

<style scoped>
.hist { min-width: 0; }
.row-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 12px; }
.tab-title { font-size: 17px; font-weight: 700; margin: 2px 0; }
.tab-desc { color: var(--text-dim); margin: 0; font-size: 13px; }
.filters { display: flex; align-items: center; gap: 12px; margin-bottom: 14px; }
.search { width: auto; flex: 1; min-width: 160px; }
.pager { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 14px; }
.pager-info { font-family: var(--mono); font-size: 13px; color: var(--text-mute); min-width: 56px; text-align: center; }
.rule { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 9px; padding: 9px 14px; margin-bottom: 7px; transition: border-color 0.12s; }
.rule:hover { border-color: var(--border); }
.rule.open { border-color: var(--accent); }
.rule-row { display: flex; align-items: center; gap: 11px; cursor: pointer; user-select: none; min-height: 40px; }
.chev { display: flex; color: var(--text-mute); transition: transform 0.15s; }
.rule.open .chev { transform: rotate(180deg); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent-bright); flex: none; opacity: 0.6; }
.rule-info { flex: 1; min-width: 0; }
.rule-head { display: flex; align-items: center; gap: 8px; min-width: 0; margin-bottom: 2px; }
.sub { font-family: var(--mono); font-size: 12px; color: var(--text-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.user { font-weight: 600; font-size: 14px; flex: none; }
.action { font-size: 12px; color: var(--text-dim); flex: none; }
.nm { font-weight: 600; font-size: 14px; color: var(--text); flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.when { font-family: var(--mono); font-size: 11px; color: var(--text-mute); margin-left: auto; flex: none; }
.detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.cap { font-size: 12px; color: var(--text-dim); margin-bottom: 10px; }
.cap-hint { color: var(--text-mute); }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.col { background: var(--panel-2); border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px 12px; overflow: hidden; }
.col-h { font-size: 11px; color: var(--text-mute); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px; }
.empty { margin: 0; color: var(--text-mute); font-size: 12px; }
.kv {
  display: grid;
  grid-template-columns: 96px minmax(0, 1fr);
  gap: 10px;
  font-family: var(--mono);
  font-size: 12px;
  padding: 4px 6px;
  border-radius: 4px;
  align-items: start;
}
.kv.diff {
  background: rgba(234, 179, 8, 0.12);
  outline: 1px solid rgba(234, 179, 8, 0.28);
}
.kk {
  color: var(--text-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vv { color: var(--text); word-break: break-word; min-width: 0; }
.vv.rm { color: var(--danger); font-weight: 600; text-decoration: line-through; text-decoration-thickness: 1px; }
.vv.add { color: var(--accent-bright); font-weight: 600; }
@media (max-width: 800px) {
  .cols { grid-template-columns: 1fr; }
}
</style>
