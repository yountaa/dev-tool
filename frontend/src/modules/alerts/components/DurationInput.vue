<script setup>
// Интервал / глубина: быстрые кнопки + своё значение в мин/ч/дн.
import { ref, watch } from 'vue'
import { UNITS, partsFromStorage, storageFromParts } from '../lib/duration.js'

const props = defineProps({
  modelValue: { type: Number, default: 1 },
  /** В чём хранится modelValue в конфиге: minutes | hours */
  storage: { type: String, default: 'minutes' },
  /** { label, value } — value в единицах storage */
  presets: { type: Array, default: () => [] },
  min: { type: Number, default: 1 },
  allowZero: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const amount = ref(1)
const unit = ref('minutes')

function syncFromModel() {
  const parts = partsFromStorage(props.modelValue, props.storage)
  amount.value = parts.amount
  unit.value = parts.unit
}

watch(() => [props.modelValue, props.storage], syncFromModel, { immediate: true })

function emitValue(next) {
  emit('update:modelValue', next)
}

function applyDisplay() {
  const stored = storageFromParts(amount.value, unit.value, props.storage)
  if (props.allowZero && stored === 0) {
    emitValue(0)
    return
  }
  emitValue(Math.max(props.min, stored))
}

function onAmountInput(e) {
  amount.value = Number(e.target.value)
  applyDisplay()
}

function onUnitChange(e) {
  unit.value = e.target.value
  applyDisplay()
}

function pickPreset(p) {
  emitValue(p.value)
}
</script>

<template>
  <div class="dur">
    <div v-if="presets.length" class="chips">
      <button
        v-for="p in presets"
        :key="`${p.label}-${p.value}`"
        type="button"
        class="chip"
        :class="{ on: modelValue === p.value }"
        @click="pickPreset(p)"
      >{{ p.label }}</button>
    </div>
    <div class="custom">
      <input
        type="number"
        class="input num"
        :min="allowZero ? 0 : min"
        :value="amount"
        @input="onAmountInput"
      />
      <select class="input unit" :value="unit" @change="onUnitChange">
        <option v-for="u in UNITS" :key="u.id" :value="u.id">{{ u.label }}</option>
      </select>
    </div>
  </div>
</template>

<style scoped>
.dur { display: flex; flex-direction: column; gap: 8px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  border: 1px solid var(--border);
  background: var(--chip);
  color: var(--text-dim);
  border-radius: 8px;
  padding: 6px 11px;
  font-size: 12.5px;
  font-family: var(--sans);
}
.chip.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-bright); }
.custom { display: flex; gap: 8px; align-items: center; max-width: 220px; }
.num { width: 88px; flex: none; }
.unit { width: 72px; flex: none; font-family: var(--sans); font-size: 13px; }
</style>
