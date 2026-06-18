<script setup>
// Сетка расписания: строки — дни недели, колонки — часы 0–23. Кликом или
// протяжкой мыши красим ячейки (в эти часы тишина активна). Наружу отдаём
// массив ключей вида "mon-13" через v-model.
const props = defineProps({ modelValue: { type: Array, required: true } })
const emit = defineEmits(['update:modelValue'])

const DAYS = [
  ['mon', 'Пн'], ['tue', 'Вт'], ['wed', 'Ср'], ['thu', 'Чт'],
  ['fri', 'Пт'], ['sat', 'Сб'], ['sun', 'Вс'],
]
const HOURS = Array.from({ length: 24 }, (_, i) => i)
const ALL = DAYS.map(([d]) => d)

// Состояние «рисования» мышью.
let painting = false
let mode = 'add' // add | erase

function has(d, h) {
  return props.modelValue.includes(`${d}-${h}`)
}
function setCell(d, h, on) {
  const key = `${d}-${h}`
  const set = new Set(props.modelValue)
  if (on) set.add(key)
  else set.delete(key)
  emit('update:modelValue', [...set])
}
function down(d, h) {
  painting = true
  mode = has(d, h) ? 'erase' : 'add'
  setCell(d, h, mode === 'add')
}
function enter(d, h) {
  if (painting) setCell(d, h, mode === 'add')
}
function stop() {
  painting = false
}

function range(a, b) {
  return Array.from({ length: b - a }, (_, i) => a + i)
}
function addCells(days, hours) {
  const set = new Set(props.modelValue)
  days.forEach((d) => hours.forEach((h) => set.add(`${d}-${h}`)))
  emit('update:modelValue', [...set])
}
function preset(kind) {
  if (kind === 'weekday') addCells(['mon', 'tue', 'wed', 'thu', 'fri'], range(9, 18))
  else if (kind === 'night') addCells(ALL, range(0, 6))
  else if (kind === 'weekend') addCells(['sat', 'sun'], range(0, 24))
}
function clear() {
  emit('update:modelValue', [])
}
</script>

<template>
  <div>
    <div class="bar">
      <span class="bar-label">Быстро:</span>
      <button class="chip" type="button" @click="preset('weekday')">будни 9–18</button>
      <button class="chip" type="button" @click="preset('night')">ночи 0–6</button>
      <button class="chip" type="button" @click="preset('weekend')">выходные</button>
      <span class="spacer"></span>
      <span class="count">{{ modelValue.length }} ячеек</span>
      <button class="chip" type="button" @click="clear">очистить</button>
    </div>

    <div class="grid" @mouseup="stop" @mouseleave="stop">
      <span class="corner"></span>
      <span v-for="h in HOURS" :key="'h' + h" class="hh">{{ h }}</span>

      <template v-for="[code, label] in DAYS" :key="code">
        <span class="dlabel">{{ label }}</span>
        <span
          v-for="h in HOURS"
          :key="code + '-' + h"
          class="cell"
          :class="{ on: has(code, h) }"
          @mousedown.prevent="down(code, h)"
          @mouseenter="enter(code, h)"
        ></span>
      </template>
    </div>

    <div class="legend">
      <span class="lg"><i class="sw on"></i> тишина активна</span>
      <span class="lg"><i class="sw"></i> алерты работают</span>
    </div>
  </div>
</template>

<style scoped>
.bar { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.bar-label { color: var(--text-dim); font-size: 12px; }
.spacer { flex: 1; }
.count { color: var(--text-mute); font-size: 12px; font-family: var(--mono); }
.chip {
  background: var(--panel-2); border: 1px solid var(--border);
  color: var(--text-dim); border-radius: 7px; padding: 5px 10px; font-size: 12px;
}
.chip:hover { color: var(--text); }

.grid {
  display: grid;
  grid-template-columns: 30px repeat(24, 1fr);
  gap: 2px;
  user-select: none;
}
.corner { }
.hh { text-align: center; font-size: 10px; color: var(--text-mute); font-family: var(--mono); }
.dlabel { font-size: 12px; color: var(--text-dim); display: flex; align-items: center; }
.cell {
  height: 22px;
  background: var(--panel-2);
  border: 1px solid var(--border-soft);
  border-radius: 3px;
  cursor: pointer;
}
.cell:hover { border-color: var(--text-mute); }
.cell.on { background: var(--accent); border-color: var(--accent); }

.legend { display: flex; gap: 18px; margin-top: 12px; }
.lg { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-dim); }
.sw { width: 12px; height: 12px; border-radius: 3px; background: var(--panel-2); border: 1px solid var(--border); }
.sw.on { background: var(--accent); border-color: var(--accent); }
</style>
