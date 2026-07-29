<script setup>
// Выбор нескольких значений из списка — для группировки и дедупликации.
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  options: { type: Array, default: () => [] },
  /** alias → подпись на чипе (например "зона ← hoff_zone") */
  labels: { type: Object, default: () => ({}) },
  empty: { type: String, default: 'ничего не выбрано' },
})
const emit = defineEmits(['update:modelValue'])

function toggle(name) {
  const has = props.modelValue.includes(name)
  emit('update:modelValue', has
    ? props.modelValue.filter((v) => v !== name)
    : [...props.modelValue, name])
}

function labelOf(o) {
  return (props.labels && props.labels[o]) || o
}
</script>

<template>
  <div class="chips">
    <button
      v-for="o in options"
      :key="o"
      type="button"
      class="chip"
      :class="{ on: modelValue.includes(o) }"
      :title="o"
      @click="toggle(o)"
    >{{ labelOf(o) }}</button>
    <span v-if="!options.length" class="none">{{ empty }}</span>
  </div>
</template>

<style scoped>
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip {
  border: 1px solid var(--border); background: var(--chip); color: var(--text-dim);
  border-radius: 999px; padding: 4px 12px; font-size: 12.5px; font-family: var(--sans);
}
.chip.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-bright); }
.none { color: var(--text-mute); font-size: 12px; }
</style>
