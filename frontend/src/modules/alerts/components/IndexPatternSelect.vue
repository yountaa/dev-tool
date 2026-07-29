<script setup>
// Поле индекса с живым поиском в ELK (через n8n action: indices) + локальный fallback.
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import { api } from '../api.js'

const props = defineProps({
  modelValue: { type: String, default: '' },
  /** Локальный кэш / fallback паттерны (из алертов, Postgres). */
  patterns: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'patterns'])

const open = ref(false)
const active = ref(-1)
const loading = ref(false)
const remote = ref([]) // результаты из ELK
const error = ref('')
let timer = null
let reqId = 0

const local = computed(() =>
  [...new Set((props.patterns || []).map(String).filter(Boolean))]
    .sort((a, b) => a.localeCompare(b)),
)

/** Объединённый список: ELK + локальные, отфильтрованные по вводу. */
const visible = computed(() => {
  const cur = String(props.modelValue || '')
  const v = cur.toLowerCase()
  const set = new Set()
  for (const o of remote.value) set.add(o)
  for (const o of local.value) set.add(o)
  let list = [...set].filter((o) => o !== cur)
  if (v) {
    const filtered = list.filter((o) => o.toLowerCase().includes(v))
    // Если есть совпадения — показываем их; иначе оставляем remote как есть
    // (вдруг пользователь ищет редкий индекс — n8n уже отфильтровал по q).
    if (filtered.length) list = filtered
    else if (remote.value.length) list = remote.value.filter((o) => o !== cur)
  }
  return list.sort((a, b) => a.localeCompare(b)).slice(0, 80)
})

async function fetchRemote(q) {
  const id = ++reqId
  loading.value = true
  error.value = ''
  try {
    const list = await api.searchIndices(q)
    if (id !== reqId) return
    remote.value = list
    if (list.length) emit('patterns', list)
  } catch (e) {
    if (id !== reqId) return
    // Без ветки indices в n8n — тихо откатываемся на локальный список.
    remote.value = []
    error.value = e.message || 'ELK unavailable'
  } finally {
    if (id === reqId) loading.value = false
  }
}

function scheduleFetch(q) {
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => fetchRemote(q), 280)
}

function show() {
  open.value = true
  active.value = -1
  // Сразу подтянуть каталог (или префикс текущего значения).
  scheduleFetch(props.modelValue || '')
}

function onInput(e) {
  const v = e.target.value
  emit('update:modelValue', v)
  open.value = true
  active.value = -1
  scheduleFetch(v)
}

function pick(opt) {
  emit('update:modelValue', opt)
  open.value = false
}

function onBlur() {
  setTimeout(() => { open.value = false }, 140)
}

function onKeydown(e) {
  if (!open.value) {
    if (e.key === 'ArrowDown') { show(); e.preventDefault() }
    return
  }
  const n = visible.value.length
  if (e.key === 'ArrowDown' && n) { active.value = (active.value + 1) % n; e.preventDefault() }
  else if (e.key === 'ArrowUp' && n) { active.value = (active.value - 1 + n) % n; e.preventDefault() }
  else if (e.key === 'Enter' && active.value >= 0 && n) { pick(visible.value[active.value]); e.preventDefault() }
  else if (e.key === 'Escape') { open.value = false }
}

onBeforeUnmount(() => {
  if (timer) clearTimeout(timer)
})

// Если родитель подставил новые patterns — не мешаем remote.
watch(() => props.patterns, () => {}, { deep: true })
</script>

<template>
  <div class="idx">
    <input
      class="input"
      :value="modelValue"
      placeholder="начни вводить: n8n_stack, omni-prod…"
      autocomplete="off"
      spellcheck="false"
      @input="onInput"
      @focus="show"
      @blur="onBlur"
      @keydown="onKeydown"
    />
    <span v-if="loading" class="spin" title="Поиск в ELK…"></span>
    <button
      v-else
      type="button"
      class="chev"
      tabindex="-1"
      title="Показать индексы"
      @mousedown.prevent="show"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6" /></svg>
    </button>

    <ul v-if="open" class="list">
      <li v-if="loading && !visible.length" class="hint">поиск в ELK…</li>
      <li v-else-if="!visible.length && !loading" class="hint">
        {{ error ? 'локальный список (ELK недоступен)' : 'ничего не найдено — можно ввести вручную' }}
      </li>
      <li
        v-for="(o, i) in visible"
        :key="o"
        class="item"
        :class="{ active: i === active }"
        @mousedown.prevent="pick(o)"
        @mouseenter="active = i"
      >
        {{ o }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.idx { position: relative; }
.idx .input { width: 100%; padding-right: 32px; }
.chev, .spin {
  position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
  background: none; border: none; color: var(--text-mute); padding: 4px;
  display: flex; cursor: pointer;
}
.chev:hover { color: var(--text); }
.spin {
  width: 14px; height: 14px; border: 2px solid var(--border);
  border-top-color: var(--accent-bright); border-radius: 50%;
  padding: 0; cursor: default;
  animation: spin 0.7s linear infinite;
}
@keyframes spin { to { transform: translateY(-50%) rotate(360deg); } }
.list {
  position: absolute;
  top: calc(100% + 4px);
  left: 0; right: 0;
  z-index: 40;
  margin: 0; padding: 4px;
  list-style: none;
  max-height: 280px; overflow-y: auto;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
}
.item {
  padding: 8px 10px;
  border-radius: 6px;
  font-family: var(--mono); font-size: 13px;
  color: var(--text-dim);
  cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.item.active, .item:hover { background: var(--accent-soft); color: var(--accent-bright); }
.hint {
  padding: 8px 10px;
  font-size: 12px; color: var(--text-mute);
  font-family: var(--sans);
}
</style>
