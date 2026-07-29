<script setup>
// Колонки таблицы в письме. Заменяет JSON вида
// [{key, label, align, format}] на понятную таблицу с порядком.
//
// Выбор ключа — из полей, объявленных в источнике, плюс служебный count:
// так нельзя сослаться на поле, которого не будет в данных (частая ошибка,
// из-за которой в письме оказывались пустые ячейки).
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  available: { type: Array, default: () => [] }, // имена из source.map
})
const emit = defineEmits(['update:modelValue'])

const options = computed(() => [...props.available, 'count'])

function push(list) { emit('update:modelValue', list) }
function edit(i, key, value) {
  const list = props.modelValue.map((c, idx) => (idx === i ? { ...c, [key]: value } : c))
  push(list)
}
function add() {
  const used = props.modelValue.map((c) => c.key)
  const next = options.value.find((o) => !used.includes(o)) || ''
  push([...props.modelValue, { key: next, label: next, align: 'left', format: 'plain' }])
}
function remove(i) { push(props.modelValue.filter((_, idx) => idx !== i)) }
function move(i, delta) {
  const list = props.modelValue.slice()
  const j = i + delta
  if (j < 0 || j >= list.length) return
  const tmp = list[i]; list[i] = list[j]; list[j] = tmp
  push(list)
}
const missing = computed(() =>
  props.modelValue.filter((c) => c.key && c.key !== 'count' && !props.available.includes(c.key)),
)
</script>

<template>
  <div class="cols">
    <div v-if="modelValue.length" class="head">
      <span>Поле</span><span>Заголовок</span><span>Выравнивание</span><span>Формат</span><span></span>
    </div>
    <div v-for="(c, i) in modelValue" :key="i" class="row">
      <select :value="c.key" class="input" @change="edit(i, 'key', $event.target.value)">
        <option v-for="o in options" :key="o" :value="o">{{ o }}</option>
        <option v-if="c.key && !options.includes(c.key)" :value="c.key">{{ c.key }} (нет в источнике)</option>
      </select>
      <input :value="c.label" class="input" placeholder="как подписать" @input="edit(i, 'label', $event.target.value)" />
      <select :value="c.align || 'left'" class="input" @change="edit(i, 'align', $event.target.value)">
        <option value="left">слева</option>
        <option value="center">по центру</option>
        <option value="right">справа</option>
      </select>
      <select :value="c.format || 'plain'" class="input" @change="edit(i, 'format', $event.target.value)">
        <option value="plain">как есть</option>
        <option value="jsonBreak">JSON с переносами</option>
      </select>
      <div class="ops">
        <button class="btn btn-icon" type="button" title="Выше" @click="move(i, -1)">↑</button>
        <button class="btn btn-icon" type="button" title="Ниже" @click="move(i, 1)">↓</button>
        <button class="btn btn-icon" type="button" title="Убрать" @click="remove(i)">×</button>
      </div>
    </div>
    <button class="btn btn-ghost btn-sm add" type="button" @click="add">+ Колонка</button>
    <p v-if="missing.length" class="warn">
      Нет в источнике: {{ missing.map((c) => c.key).join(', ') }} — в письме будут пустые ячейки.
      Добавьте эти поля в раздел «Какие поля брать».
    </p>
  </div>
</template>

<style scoped>
.cols { display: flex; flex-direction: column; gap: 10px; width: 100%; }
.head, .row {
  display: grid;
  grid-template-columns: minmax(120px, 1.2fr) minmax(120px, 1.3fr) minmax(88px, .75fr) minmax(100px, .85fr) 88px;
  gap: 10px;
  align-items: center;
}
.head {
  color: var(--text-mute);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .35px;
  padding: 0 2px;
}
.head span,
.row > select,
.row > input {
  min-width: 0;
}
.row > select.input,
.row > input.input {
  width: 100%;
  height: 34px;
  padding-top: 0;
  padding-bottom: 0;
}
.ops {
  display: flex;
  gap: 2px;
  justify-content: flex-end;
  flex: none;
}
.add { align-self: flex-start; margin-top: 2px; }
.warn { margin: 0; color: var(--danger); font-size: 12px; }
code { font-family: var(--mono); font-size: 11.5px; }
select.input { font-family: var(--sans); font-size: 13px; }
</style>
