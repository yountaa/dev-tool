<script setup>
// Поле ввода со своим (не нативным) выпадающим списком подсказок.
// Зачем не <datalist>: нативный список на Windows выглядит чужеродно, не скрывается
// при не совпадающем вводе и предлагает уже вписанное значение повторно. Здесь:
//   • список фильтруется по введённому тексту (подстрока) — не совпало, список пуст;
//   • уже выбранное значение из подсказок убираем (option === value);
//   • открыт только в фокусе и пока есть что показать; закрытие по blur/Escape/выбору.
import { ref, computed, nextTick } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  options: { type: Array, default: () => [] }, // строки-подсказки
  placeholder: { type: String, default: '' },
  invalid: { type: Boolean, default: false }, // красная рамка для незаполненного поля
})
const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const active = ref(-1) // подсвеченный пункт для стрелок/Enter

// Подходящие подсказки: содержат введённый текст и не равны ему целиком
// (повторно уже вписанное не предлагаем).
const filtered = computed(() => {
  const v = (props.modelValue || '').toLowerCase()
  return props.options.filter((o) => o.toLowerCase().includes(v) && o !== props.modelValue)
})

function show() {
  open.value = filtered.value.length > 0
  active.value = -1
}
function onInput(e) {
  emit('update:modelValue', e.target.value)
  nextTick(show) // пересчитать список под новый текст
}
function pick(opt) {
  emit('update:modelValue', opt)
  open.value = false
}
// blur с задержкой — чтобы успел отработать клик по пункту списка.
function onBlur() {
  setTimeout(() => { open.value = false }, 120)
}
function onKeydown(e) {
  if (!open.value) {
    if (e.key === 'ArrowDown') show()
    return
  }
  const n = filtered.value.length
  if (e.key === 'ArrowDown') { active.value = (active.value + 1) % n; e.preventDefault() }
  else if (e.key === 'ArrowUp') { active.value = (active.value - 1 + n) % n; e.preventDefault() }
  else if (e.key === 'Enter' && active.value >= 0) { pick(filtered.value[active.value]); e.preventDefault() }
  else if (e.key === 'Escape') { open.value = false }
}
</script>

<template>
  <div class="ac">
    <input
      class="input"
      :class="{ invalid }"
      :value="modelValue"
      :placeholder="placeholder"
      @input="onInput"
      @focus="show"
      @blur="onBlur"
      @keydown="onKeydown"
    />
    <ul v-if="open" class="ac-list">
      <li
        v-for="(o, i) in filtered"
        :key="o"
        class="ac-item"
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
.ac { position: relative; }
.ac .input { width: 100%; }

.ac-list {
  position: absolute;
  top: calc(100% + 4px);
  left: 0; right: 0;
  z-index: 30;
  margin: 0; padding: 4px;
  list-style: none;
  max-height: 220px; overflow-y: auto;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
}
.ac-item {
  padding: 7px 10px;
  border-radius: 6px;
  font-family: var(--mono); font-size: 13px;
  color: var(--text-dim);
  cursor: pointer;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ac-item.active { background: var(--accent-soft); color: var(--accent-bright); }
</style>
