<script setup>
// Какие поля документа вытащить и как их назвать в отчёте.
// Черновик строк — локальный; в конфиг пишем одним событием (map + sourceFields),
// иначе два подряд set() в форме затирают друг друга и строка «пропадает».
import { computed, ref, watch } from 'vue'
import AutocompleteInput from '../../silences/components/AutocompleteInput.vue'

const props = defineProps({
  map: { type: Object, default: () => ({}) },
  timeField: { type: String, default: '@timestamp' },
  fields: { type: Array, default: () => [] },
})
const emit = defineEmits(['change', 'remember'])

/** @type {import('vue').Ref<{id:string,alias:string,path:string}[]>} */
const draft = ref([])
let lastEmitted = ''
let seq = 0

function uid() {
  seq += 1
  return 'f' + seq
}

function fromMap(map) {
  return Object.entries(map || {}).map(([alias, path]) => ({
    id: uid(),
    alias: alias === path ? '' : alias,
    path: String(path || ''),
  }))
}

const options = computed(() =>
  (props.fields || []).map((f) => (typeof f === 'string' ? f : f.name)).filter(Boolean),
)

function commit() {
  const map = {}
  for (const r of draft.value) {
    const path = (r.path || '').trim()
    if (!path) continue
    const alias = (r.alias || '').trim() || path
    map[alias] = path
  }
  const sourceFields = [...new Set([props.timeField, ...Object.values(map)].filter(Boolean))]
  lastEmitted = JSON.stringify(map)
  emit('change', { map, sourceFields })
  const remembered = []
  for (const r of draft.value) {
    if (r.path?.trim()) remembered.push(r.path.trim())
    if (r.alias?.trim()) remembered.push(r.alias.trim())
  }
  if (remembered.length) emit('remember', remembered)
}

watch(
  () => props.map,
  (m) => {
    const s = JSON.stringify(m || {})
    if (s === lastEmitted) return
    // Внешняя смена (другой алерт) — пересобираем строки; пустые черновики не трогаем сами.
    draft.value = fromMap(m)
    lastEmitted = s
  },
  { immediate: true, deep: true },
)

function setPath(row, value) {
  row.path = value
  commit()
}
function setAlias(row, value) {
  row.alias = value
  commit()
}
function add() {
  draft.value = [...draft.value, { id: uid(), alias: '', path: '' }]
}
function remove(id) {
  draft.value = draft.value.filter((r) => r.id !== id)
  commit()
}
</script>

<template>
  <div class="fields">
    <div v-if="draft.length" class="head">
      <span>Путь в документе ELK</span>
      <span>Имя в отчёте</span>
      <span></span>
    </div>
    <div v-for="r in draft" :key="r.id" class="row">
      <AutocompleteInput
        :model-value="r.path"
        :options="options"
        placeholder="hoff_zone"
        @update:model-value="setPath(r, $event)"
      />
      <input
        :value="r.alias"
        class="input"
        :placeholder="r.path || 'как назвать'"
        spellcheck="false"
        @input="setAlias(r, $event.target.value)"
      />
      <button class="btn btn-icon" type="button" title="Убрать" @click="remove(r.id)">×</button>
    </div>
    <button class="btn btn-ghost btn-sm add" type="button" @click="add">+ Поле</button>
  </div>
</template>

<style scoped>
.fields { display: flex; flex-direction: column; gap: 8px; }
.head, .row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto; gap: 8px; align-items: center; }
.head { color: var(--text-mute); font-size: 11.5px; text-transform: uppercase; letter-spacing: .4px; }
.add { align-self: flex-start; }
.hint { margin: 2px 0 0; color: var(--text-mute); font-size: 12px; }
code { font-family: var(--mono); font-size: 11.5px; color: var(--text-dim); }
</style>
