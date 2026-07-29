<script setup>
// Фильтр в стиле Kibana Add filter: строки field / operator / value, preview, Query DSL.
import { computed, ref, watch } from 'vue'
import {
  toConditions, toQuery, OPS,
  queryToKibana,
} from '../lib/query.js'
import { previewFilter } from '../lib/filterPreview.js'
import AutocompleteInput from '../../silences/components/AutocompleteInput.vue'

const props = defineProps({
  modelValue: { type: Object, default: () => ({ bool: { must: [] } }) },
  filter: { type: Object, default: null },
  kql: { type: String, default: '' },
  fields: { type: Array, default: () => [] },
})
const emit = defineEmits(['change', 'remember'])

const KIBANA_OPS = [
  { op: 'is', label: 'is' },
  { op: 'is_not', label: 'is not' },
  { op: 'contains', label: 'contains' },
  { op: 'starts_with', label: 'starts with' },
  { op: 'one_of', label: 'is one of' },
  { op: 'regex', label: 'matches' },
  { op: 'exists', label: 'exists' },
  { op: 'missing', label: 'does not exist' },
]

const fieldOptions = computed(() =>
  (props.fields || []).map((f) => (typeof f === 'string' ? f : f.name)).filter(Boolean),
)

const showJson = ref(false)
const rawText = ref('')
const rawError = ref('')
const kibanaText = ref('')
const model = ref({ combinator: 'all', conditions: [] })

function hasClauses(q) {
  if (!q || typeof q !== 'object') return false
  const b = q.bool
  if (!b) return Object.keys(q).length > 0
  return !!(
    (Array.isArray(b.must) && b.must.length) ||
    (Array.isArray(b.must_not) && b.must_not.length) ||
    (Array.isArray(b.should) && b.should.length)
  )
}

function mergeFilterAndKql(filter, kql) {
  const must = []
  const mustNot = []
  const should = []
  let minShould = null

  if (filter && hasClauses(filter)) {
    if (filter.bool) {
      if (Array.isArray(filter.bool.must)) must.push(...filter.bool.must)
      if (Array.isArray(filter.bool.must_not)) mustNot.push(...filter.bool.must_not)
      if (Array.isArray(filter.bool.should) && filter.bool.should.length) {
        if (!must.length && !mustNot.length) {
          must.push({ bool: {
            should: filter.bool.should,
            minimum_should_match: filter.bool.minimum_should_match || 1,
          } })
        } else {
          must.push({ bool: {
            should: filter.bool.should,
            minimum_should_match: filter.bool.minimum_should_match || 1,
          } })
        }
      }
    } else {
      must.push(filter)
    }
  }

  const k = String(kql || '').trim()
  if (k) {
    must.push({ query_string: { query: k, default_operator: 'AND' } })
  }

  const bool = {}
  if (must.length) bool.must = must
  if (mustNot.length) bool.must_not = mustNot
  if (should.length) {
    bool.should = should
    bool.minimum_should_match = minShould || 1
  }
  if (!Object.keys(bool).length) return { bool: { must: [] } }
  return { bool }
}

function splitIncoming() {
  if (props.filter != null || (props.kql != null && props.kql !== '')) {
    return {
      filter: props.filter || { bool: { must: [] } },
      kql: props.kql || '',
    }
  }
  const q = props.modelValue
  const onlyKql = queryToKibana(q)
  if (onlyKql != null) {
    return { filter: { bool: { must: [] } }, kql: onlyKql }
  }
  if (q?.bool && Array.isArray(q.bool.must) && q.bool.must.length >= 1) {
    const qsIdx = q.bool.must.findIndex(
      (c) => c && c.query_string && typeof c.query_string.query === 'string' && !c.query_string.default_field,
    )
    if (qsIdx >= 0) {
      const kql = q.bool.must[qsIdx].query_string.query
      const rest = q.bool.must.filter((_, i) => i !== qsIdx)
      const filter = rest.length
        ? { bool: { ...q.bool, must: rest } }
        : { bool: { must: [], must_not: q.bool.must_not, should: q.bool.should } }
      return { filter, kql }
    }
  }
  return { filter: q || { bool: { must: [] } }, kql: '' }
}

function load() {
  rawError.value = ''
  const { filter, kql } = splitIncoming()
  kibanaText.value = kql
  const parsed = toConditions(filter)
  if (parsed.mode === 'raw') {
    showJson.value = true
    rawText.value = JSON.stringify(filter, null, 2)
    model.value = { combinator: 'all', conditions: [] }
  } else {
    showJson.value = false
    model.value = { combinator: parsed.combinator, conditions: parsed.conditions.slice() }
  }
}

load()

let lastEmitted = ''
watch(
  () => [props.filter, props.kql, props.modelValue],
  () => {
    const snap = JSON.stringify({
      filter: props.filter,
      kql: props.kql || '',
      query: props.modelValue,
    })
    if (snap === lastEmitted) return
    load()
  },
  { deep: true },
)

function currentFilter() {
  if (showJson.value) {
    try { return JSON.parse(rawText.value || '{}') }
    catch (_) { return props.filter || { bool: { must: [] } } }
  }
  return toQuery(model.value)
}

function emitAll() {
  const filter = currentFilter()
  const kql = kibanaText.value
  const query = mergeFilterAndKql(filter, kql)
  lastEmitted = JSON.stringify({ filter, kql, query })
  emit('change', { filter, kql, query })
  const names = (model.value.conditions || []).map((c) => c.field).filter(Boolean)
  if (names.length) emit('remember', names)
}

function setField(c, value) {
  c.field = value
  emitAll()
}
function addRow(combinator) {
  if (combinator) model.value.combinator = combinator
  model.value.conditions.push({ field: '', op: 'is', value: '' })
}
function removeRow(i) {
  model.value.conditions.splice(i, 1)
  emitAll()
}
function onOpChange(i) {
  const c = model.value.conditions[i]
  const spec = OPS.find((o) => o.op === c.op)
  if (!spec.needsValue) c.value = ''
  else if (spec.multi && !Array.isArray(c.value)) c.value = c.value ? [c.value] : []
  else if (!spec.multi && Array.isArray(c.value)) c.value = c.value[0] || ''
  emitAll()
}
function needsValue(op) {
  const spec = OPS.find((o) => o.op === op)
  return spec ? spec.needsValue : true
}
function isMulti(op) {
  const spec = OPS.find((o) => o.op === op)
  return !!(spec && spec.multi)
}
function listValue(c) {
  return Array.isArray(c.value) ? c.value.join(', ') : c.value
}
function setListValue(c, text) {
  c.value = text.split(',').map((s) => s.trim()).filter(Boolean)
  emitAll()
}

const previewText = computed(() => {
  if (showJson.value) return 'условия из Query DSL'
  return previewFilter({ filter: currentFilter(), kql: kibanaText.value, model: model.value }) || '—'
})

function toggleJson() {
  rawError.value = ''
  if (!showJson.value) {
    rawText.value = JSON.stringify(toQuery(model.value), null, 2)
    showJson.value = true
    return
  }
  try {
    const parsed = JSON.parse(rawText.value || '{}')
    const asModel = toConditions(parsed)
    if (asModel.mode === 'raw') {
      rawError.value = 'Этот JSON сложнее конструктора — правьте здесь или упростите.'
      return
    }
    model.value = { combinator: asModel.combinator, conditions: asModel.conditions.slice() }
    showJson.value = false
    emitAll()
  } catch (e) {
    rawError.value = 'Некорректный JSON: ' + e.message
  }
}

function onRawInput() {
  rawError.value = ''
  try {
    JSON.parse(rawText.value || '{}')
    emitAll()
  } catch (e) {
    rawError.value = 'Некорректный JSON: ' + e.message
  }
}

function combinatorLabel(i) {
  if (i === 0) return ''
  return model.value.combinator === 'any' ? 'OR' : 'AND'
}
</script>

<template>
  <div class="filter-box">
    <div class="filter-head">
      <span class="filter-title">Add filter</span>
      <button type="button" class="linkish" @click="toggleJson">
        {{ showJson ? 'Вернуть конструктор' : 'Edit as Query DSL' }}
      </button>
    </div>

    <template v-if="!showJson">
      <div v-if="kibanaText.trim()" class="kql-row">
        <label class="kql-label">KQL</label>
        <input
          v-model="kibanaText"
          class="input kql-input"
          placeholder="kubernetes.deployment.name:prod-shipping-v1 AND &quot;текст&quot;"
          spellcheck="false"
          @input="emitAll"
        />
        <button class="btn btn-icon" type="button" title="Убрать KQL" @click="kibanaText = ''; emitAll()">×</button>
      </div>

      <div v-for="(c, i) in model.conditions" :key="i" class="filter-block">
        <div v-if="combinatorLabel(i)" class="joiner">{{ combinatorLabel(i) }}</div>
        <div class="filter-row">
          <span class="drag" title="перетаскивание">≡</span>
          <AutocompleteInput
            class="f-field"
            :model-value="c.field"
            :options="fieldOptions"
            placeholder="_id"
            @update:model-value="setField(c, $event)"
          />
          <select v-model="c.op" class="input f-op" @change="onOpChange(i)">
            <option v-for="o in KIBANA_OPS" :key="o.op" :value="o.op">{{ o.label }}</option>
          </select>
          <input
            v-if="needsValue(c.op) && !isMulti(c.op)"
            v-model="c.value"
            class="input f-val"
            placeholder="value"
            spellcheck="false"
            @input="emitAll"
          />
          <input
            v-else-if="isMulti(c.op)"
            :value="listValue(c)"
            class="input f-val"
            placeholder="values, comma-separated"
            spellcheck="false"
            @input="setListValue(c, $event.target.value)"
          />
          <span v-else class="f-val muted"></span>
          <button class="btn btn-icon del" type="button" title="Удалить" @click="removeRow(i)">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14Z" /></svg>
          </button>
        </div>
        <div class="row-actions">
          <button type="button" class="add-link" @click="addRow('any')">+ OR</button>
          <button type="button" class="add-link" @click="addRow('all')">+ AND</button>
        </div>
      </div>

      <button v-if="!model.conditions.length" type="button" class="btn btn-ghost btn-sm" @click="addRow('all')">
        + Add filter
      </button>
      <button v-else type="button" class="btn btn-ghost btn-sm add-filter" @click="addRow(model.combinator)">
        + Add filter
      </button>
    </template>

    <template v-else>
      <textarea v-model="rawText" class="input raw" rows="10" spellcheck="false" @input="onRawInput" />
      <p v-if="rawError" class="err">{{ rawError }}</p>
    </template>

    <div class="preview">
      <span class="preview-label">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>
        Preview
      </span>
      <code class="preview-text">{{ previewText }}</code>
    </div>
  </div>
</template>

<style scoped>
.filter-box {
  border: 1px solid var(--border);
  border-radius: 10px;
  /* Чуть другой тон, чем у card-секции (--panel), чтобы блок фильтра читался отдельно. */
  background: var(--panel-2);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.filter-box :deep(.input),
.filter-box .input {
  background: var(--panel);
}
.filter-box .preview {
  background: var(--panel);
}
.filter-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.filter-title { font-size: 13px; font-weight: 600; color: var(--text); }
.linkish {
  background: none; border: none; color: var(--accent-bright);
  font-size: 12.5px; padding: 0; cursor: pointer; text-decoration: underline;
}
.kql-row { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; }
.kql-label { font-size: 12px; color: var(--text-mute); font-family: var(--mono); }
.kql-input { font-family: var(--mono); font-size: 13px; }
.filter-block { display: flex; flex-direction: column; gap: 4px; }
.joiner {
  font-size: 11px; font-weight: 600; color: var(--text-mute);
  padding-left: 28px; margin-top: 2px;
}
.filter-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1.2fr) minmax(0, 0.85fr) minmax(0, 1.2fr) auto;
  gap: 8px; align-items: center;
}
.drag {
  color: var(--text-mute); font-size: 14px; text-align: center; cursor: default;
  user-select: none;
}
.f-op { font-family: var(--sans); font-size: 13px; }
.f-val.muted { min-height: 34px; }
.del { color: var(--danger); opacity: 0.85; }
.del:hover { opacity: 1; }
.row-actions { display: flex; gap: 12px; padding-left: 30px; }
.add-link {
  background: none; border: none; color: var(--accent-bright);
  font-size: 12px; padding: 0; cursor: pointer;
}
.add-link:hover { text-decoration: underline; }
.add-filter { align-self: flex-start; }
.preview {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--border-soft);
}
.preview-label {
  display: flex; align-items: center; gap: 5px;
  font-size: 12px; color: var(--text-mute); flex: none;
}
.preview-text {
  font-family: var(--mono); font-size: 12.5px; line-height: 1.5;
  color: var(--text); word-break: break-word;
}
.err { margin: 0; color: var(--danger); font-size: 12px; }
.raw { font-family: var(--mono); font-size: 12px; }
@media (max-width: 900px) {
  .filter-row { grid-template-columns: 22px 1fr auto; }
  .f-op, .f-val { grid-column: span 2; }
}
</style>
