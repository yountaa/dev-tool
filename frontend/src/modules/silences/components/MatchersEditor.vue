<script setup>
// Редактор matchers — пары label=value с переключателем regex.
// Работает через v-model: родитель даёт массив, мы его правим и отдаём обратно.
const props = defineProps({
  modelValue: { type: Array, required: true },
  showErrors: { type: Boolean, default: false }, // подсветить пустые поля красным
})
const emit = defineEmits(['update:modelValue'])

function update(list) {
  emit('update:modelValue', list)
}
function addRow() {
  update([...props.modelValue, { name: '', value: '', isRegex: false }])
}
function removeRow(i) {
  update(props.modelValue.filter((_, idx) => idx !== i))
}
function toggleRegex(i) {
  const list = props.modelValue.map((m, idx) =>
    idx === i ? { ...m, isRegex: !m.isRegex } : m,
  )
  update(list)
}
</script>

<template>
  <div class="card">
    <div class="card-head">
      <span class="card-title">Matchers</span>
      <span class="card-hint">label = value</span>
    </div>

    <div v-for="(m, i) in modelValue" :key="i" class="matcher-row">
      <input class="input" :class="{ invalid: showErrors && !m.name }" v-model="m.name" placeholder="alertname" />
      <input class="input" :class="{ invalid: showErrors && !m.value }" v-model="m.value" placeholder="HighCPUUsage" />
      <button
        class="toggle"
        :class="{ on: m.isRegex }"
        type="button"
        @click="toggleRegex(i)"
        :aria-pressed="m.isRegex"
      >
        <span class="knob"></span>
        regex
      </button>
      <button class="btn-icon" type="button" title="удалить" @click="removeRow(i)">×</button>
    </div>

    <button class="btn btn-ghost btn-sm" type="button" @click="addRow">+ добавить matcher</button>
  </div>
</template>

<style scoped>
.matcher-row {
  display: grid;
  grid-template-columns: 1fr 1fr auto auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}
.toggle {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  color: var(--text-mute);
  border-radius: 7px;
  padding: 7px 11px;
  font-size: 12px;
}
.toggle .knob {
  width: 26px; height: 15px;
  background: var(--track);
  border-radius: 8px;
  position: relative;
  transition: background 0.15s;
}
.toggle .knob::after {
  content: '';
  position: absolute; top: 2px; left: 2px;
  width: 11px; height: 11px;
  background: var(--text-mute);
  border-radius: 50%;
  transition: transform 0.15s;
}
.toggle.on { color: var(--accent-bright); border-color: var(--accent); }
.toggle.on .knob { background: var(--accent); }
.toggle.on .knob::after { transform: translateX(11px); background: #fff; }
</style>
