<script setup>
// Вкладка «History»: действия с правилами окружения (кто/что/когда).
// Минимально: пользователь · действие · имя · время. Раскрытие — две колонки
// before | after со ВСЕМИ полями: пустая сторона = те же поля, но без значений.
import { ref, computed, watch } from 'vue'
import { fmtDt } from '../../../shared/time.js'

const props = defineProps({
  env: { type: String, required: true },
  items: { type: Array, required: true }, // история активного env (грузит родитель)
})
const emit = defineEmits(['reload'])

const query = ref('')
const openKey = ref(null)

// Действия → английские подписи.
const ACT = { создал: 'created', изменил: 'edited', удалил: 'deleted',
  включил: 'enabled', выключил: 'disabled', доставил: 'applied' }
function actionLabel(a) { return ACT[a] || a || 'changed' }
function caption(h) {
  const l = actionLabel(h.action)
  return l.charAt(0).toUpperCase() + l.slice(1)
}

// Дни недели и окна расписания — в читаемый вид вместо JSON.
const DOW = { mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun' }
const WEEK = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
function fmtDays(days) {
  const idx = (days || []).map((d) => WEEK.indexOf(d)).filter((i) => i >= 0).sort((a, b) => a - b)
  if (!idx.length) return ''
  const contiguous = idx.every((v, i) => i === 0 || v === idx[i - 1] + 1)
  if (idx.length >= 3 && contiguous) return `${DOW[WEEK[idx[0]]]}–${DOW[WEEK[idx[idx.length - 1]]]}`
  return idx.map((i) => DOW[WEEK[i]]).join(', ')
}
function fmtWindow(w) {
  return `${fmtDays(w.days)} ${w.start}–${w.end}`.trim()
}

// Значение поля в читаемом виде по его смыслу.
function display(k, v) {
  if (v === undefined || v === null || v === '') return ''
  if (k === 'matchers' && Array.isArray(v)) {
    return v.map((m) => `${m.name}${m.isRegex ? '=~' : '='}${m.value}`).join(', ')
  }
  if (k === 'windows' && Array.isArray(v)) {
    return v.map(fmtWindow).join('; ')
  }
  if (k === 'starts_at' || k === 'ends_at') return fmtDt(v)
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

// Все поля (объединение before/after) в удобном порядке.
const ORDER = ['name', 'matchers', 'starts_at', 'ends_at', 'windows', 'comment', 'enabled']
function fields(h) {
  const keys = new Set([...Object.keys(h.before || {}), ...Object.keys(h.after || {})])
  const ordered = ORDER.filter((k) => keys.has(k))
  for (const k of keys) if (!ordered.includes(k)) ordered.push(k)
  return ordered
}
function leftVal(h, k) { return h.before ? display(k, h.before[k]) : '' }
function rightVal(h, k) { return h.after ? display(k, h.after[k]) : '' }
function changed(h, k) {
  if (!h.before || !h.after) return true // создание/удаление — всё новое/убранное
  return display(k, h.before[k]) !== display(k, h.after[k])
}
function keyOf(h, i) { return (h.time || '') + i }

// Вторая строка плашки (как в Alerts/Silences): для правки — что изменилось,
// для создания/удаления — matchers правила (его опознание).
function subText(h) {
  if (h.before && h.after) {
    const ch = fields(h).filter((k) => changed(h, k))
    return ch.length ? 'changed: ' + ch.join(', ') : 'no changes'
  }
  const src = h.after || h.before || {}
  if (Array.isArray(src.matchers) && src.matchers.length) {
    return src.matchers.map((m) => `${m.name}${m.isRegex ? '=~' : '='}${m.value}`).join(', ')
  }
  return src.name || ''
}

const filtered = computed(() =>
  props.items.filter((h) => {
    if (!query.value) return true
    const hay = `${h.user || ''} ${h.name || ''} ${actionLabel(h.action)}`.toLowerCase()
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
  <div>
    <div class="row-head">
      <div>
        <h2 class="tab-title">History</h2>
        <p class="tab-desc">Кто и что менял в окружении <b>{{ env }}</b> и когда.</p>
      </div>
      <button class="btn btn-sm" @click="emit('reload')">обновить</button>
    </div>

    <div class="filters">
      <input class="input search" v-model="query" placeholder="search by user or name…" />
    </div>

    <p v-if="!filtered.length" class="tab-desc">Истории пока нет.</p>

    <div v-for="(h, i) in paged" :key="keyOf(h, i)" class="rule" :class="{ open: openKey === keyOf(h, i) }">
      <div class="rule-row" @click="toggle(keyOf(h, i))">
        <span class="dot"></span>
        <div class="rule-info">
          <div class="rule-head">
            <span class="user">{{ h.user || '—' }}</span>
            <span class="action">{{ actionLabel(h.action) }}</span>
            <span class="nm">{{ h.name || '(без имени)' }}</span>
            <span v-if="h.kind" class="kind" :class="h.kind">{{ h.kind }}</span>
            <span class="when">{{ fmtDt(h.time) }}</span>
          </div>
          <div class="sub" :title="subText(h)">{{ subText(h) }}</div>
        </div>
        <span class="chev">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
      </div>

      <!-- Раскрытие: before → after; пустая сторона = те же поля без значений. -->
      <div v-if="openKey === keyOf(h, i)" class="detail">
        <div class="cap">{{ caption(h) }}</div>
        <div class="cols">
          <div class="col">
            <div class="col-h">before</div>
            <div v-for="k in fields(h)" :key="'b-' + k" class="kv">
              <span class="kk">{{ k }}</span>
              <span class="vv" :class="{ rm: leftVal(h, k) && (!h.after || changed(h, k)) }">{{ leftVal(h, k) || '—' }}</span>
            </div>
          </div>
          <div class="col">
            <div class="col-h">after</div>
            <div v-for="k in fields(h)" :key="'a-' + k" class="kv">
              <span class="kk">{{ k }}</span>
              <span class="vv" :class="{ add: rightVal(h, k) && (!h.before || changed(h, k)) }">{{ rightVal(h, k) || '—' }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Пагинация -->
    <div v-if="totalPages > 1" class="pager">
      <button class="btn btn-sm" :disabled="page === 1" @click="page--">←</button>
      <span class="pager-info">{{ page }} / {{ totalPages }}</span>
      <button class="btn btn-sm" :disabled="page === totalPages" @click="page++">→</button>
    </div>
  </div>
</template>

<style scoped>
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
/* min-height — чтобы плашка совпадала по высоте с Alerts/Silences (там 2 строки). */
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
.kind { font-size: 10px; padding: 1px 7px; border-radius: 20px; flex: none; }
.kind.manual { background: var(--info-soft); color: var(--info); }
.kind.schedule { background: var(--accent-soft); color: var(--accent-bright); }
.when { font-family: var(--mono); font-size: 11px; color: var(--text-mute); margin-left: auto; flex: none; }

.detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.cap { font-size: 12px; color: var(--text-dim); margin-bottom: 10px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.col { background: var(--panel-2); border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px 12px; }
.col-h { font-size: 11px; color: var(--text-mute); text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px; }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 8px; font-family: var(--mono); font-size: 12px; padding: 2px 0; }
.kk { color: var(--text-mute); }
.vv { color: var(--text); word-break: break-all; }
.vv.rm { color: var(--danger); }
.vv.add { color: var(--accent-bright); }
</style>
