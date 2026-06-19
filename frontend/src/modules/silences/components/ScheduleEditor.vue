<script setup>
// Редактор расписания: грубая форма задаётся сеткой дни×часы (ScheduleGrid),
// а под ней — «Точное время окон», где у каждого окна можно довести начало/конец
// до минуты. Наружу через v-model отдаём окна [{days, start:"HH:MM", end:"HH:MM"}].
//
// Минутные правки храним как overrides по «часовой» сигнатуре окна, чтобы перекраска
// сетки их не затирала: пока окно с тем же набором дней и тем же часовым диапазоном
// существует — его минуты сохраняются.
import { ref, reactive } from 'vue'
import ScheduleGrid from './ScheduleGrid.vue'
import { cellsToWindows, windowsToCells } from '../cells.js'

const props = defineProps({ modelValue: { type: Array, required: true } })
const emit = defineEmits(['update:modelValue'])

const DAY = { mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс' }
const pad = (n) => String(n).padStart(2, '0')

function hourPart(t) {
  return parseInt(t.split(':')[0], 10)
}
// «Часовая» сигнатура окна: дни + диапазон часов (минуты обнулены). Так окно из сетки
// и его минутно-подправленная версия дают один ключ.
function baseKey(w) {
  const sh = hourPart(w.start)
  let eh = hourPart(w.end)
  if (eh <= sh) eh = 24
  return `${[...w.days].sort().join(',')}|${pad(sh)}:00|${pad(eh % 24)}:00`
}

const cells = ref(windowsToCells(props.modelValue))
const overrides = reactive({}) // baseKey -> { start, end }
// Подхватываем минуты из уже сохранённых окон (при редактировании существующего правила).
for (const w of props.modelValue || []) {
  if (w.start.slice(3) !== '00' || w.end.slice(3) !== '00') {
    overrides[baseKey(w)] = { start: w.start, end: w.end }
  }
}

const rows = ref([])

function rebuild() {
  rows.value = cellsToWindows(cells.value).map((w) => {
    const k = baseKey(w)
    const o = overrides[k]
    return { days: w.days, start: o ? o.start : w.start, end: o ? o.end : w.end, key: k }
  })
  emit(
    'update:modelValue',
    rows.value.map((r) => ({ days: r.days, start: r.start, end: r.end })),
  )
}
rebuild()

function onCells(next) {
  cells.value = next
  rebuild()
}

function onTime(row, field, value) {
  if (!value) return // пустое поле игнорируем, оставляем как было
  overrides[row.key] = {
    start: field === 'start' ? value : row.start,
    end: field === 'end' ? value : row.end,
  }
  rebuild()
}

function daysLabel(days) {
  return [...days].map((d) => DAY[d] || d).join(', ')
}
</script>

<template>
  <div>
    <ScheduleGrid :model-value="cells" @update:model-value="onCells" />

    <div v-if="rows.length" class="mins">
      <div class="mins-head">Точное время окон <span class="hint">можно до минуты</span></div>
      <div v-for="row in rows" :key="row.key" class="win">
        <span class="days">{{ daysLabel(row.days) }}</span>
        <input
          class="input time"
          type="time"
          :value="row.start"
          @change="onTime(row, 'start', $event.target.value)"
        />
        <span class="dash">–</span>
        <input
          class="input time"
          type="time"
          :value="row.end"
          @change="onTime(row, 'end', $event.target.value)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.mins { margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 14px; }
.mins-head { font-size: 12px; color: var(--text-dim); margin-bottom: 10px; }
.hint { color: var(--text-mute); }
.win { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.days {
  font-size: 12px; color: var(--text-dim); min-width: 120px;
  font-family: var(--mono);
}
.time { width: auto; flex: none; }
.dash { color: var(--text-mute); }
</style>
