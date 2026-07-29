<script setup>
// Расписание: как запускать, за какой период брать логи, когда слать письма.
import { computed } from 'vue'
import InfoHint from './InfoHint.vue'
import DurationInput from './DurationInput.vue'
import { formatDurationMinutes } from '../lib/duration.js'
import { lookbackMinutes } from '../lib/window.js'

const props = defineProps({ cfg: { type: Object, required: true } })
const emit = defineEmits(['update:cfg'])

const INTERVAL_PRESETS = [
  { label: '1 мин', value: 1 },
  { label: '5 мин', value: 5 },
  { label: '15 мин', value: 15 },
  { label: '30 мин', value: 30 },
  { label: '1 ч', value: 60 },
  { label: '2 ч', value: 120 },
  { label: '6 ч', value: 360 },
  { label: '12 ч', value: 720 },
  { label: '1 дн', value: 1440 },
]

const LOOKBACK_PRESETS = [
  { label: '1 мин', value: 1 },
  { label: '5 мин', value: 5 },
  { label: '15 мин', value: 15 },
  { label: '30 мин', value: 30 },
  { label: '1 ч', value: 60 },
  { label: '6 ч', value: 360 },
  { label: '24 ч', value: 1440 },
  { label: '7 дн', value: 10080 },
]

const OVERLAP_PRESETS = [
  { label: '0', value: 0 },
  { label: '2 мин', value: 2 },
  { label: '5 мин', value: 5 },
  { label: '15 мин', value: 15 },
]

const DAYS = [
  { v: 1, short: 'пн' }, { v: 2, short: 'вт' }, { v: 3, short: 'ср' },
  { v: 4, short: 'чт' }, { v: 5, short: 'пт' }, { v: 6, short: 'сб' }, { v: 0, short: 'вс' },
]

function set(path, value) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  const keys = path.split('.')
  let node = next
  for (let i = 0; i < keys.length - 1; i++) {
    if (typeof node[keys[i]] !== 'object' || node[keys[i]] === null) node[keys[i]] = {}
    node = node[keys[i]]
  }
  node[keys[keys.length - 1]] = value
  emit('update:cfg', next)
}

const sched = computed(() => props.cfg.schedule || {})
const trigger = computed(() => {
  const t = sched.value.trigger || 'interval'
  return t === 'window' ? 'interval' : t
})
const interval = computed(() => Number(sched.value.intervalMinutes) || 30)
const times = computed(() => Array.isArray(sched.value.times) ? sched.value.times : [])
const winMode = computed(() => props.cfg.window?.mode === 'fixed' ? 'fixed' : 'sliding')
const lookback = computed(() => lookbackMinutes(props.cfg.window))
const sw = computed(() => sched.value.sendWindow || {})
const swDays = computed(() => Array.isArray(sw.value.days) ? sw.value.days : [1, 2, 3, 4, 5])

function setTrigger(t) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  next.schedule = next.schedule || {}
  next.schedule.trigger = t
  if (t === 'dailyTimes' && !Array.isArray(next.schedule.times)) next.schedule.times = ['09:00']
  if (!next.schedule.timezone) next.schedule.timezone = next.notify?.timezone || 'Europe/Moscow'
  emit('update:cfg', next)
}

function addTime() { set('schedule.times', [...times.value, '12:00']) }
function setTime(i, v) { set('schedule.times', times.value.map((t, idx) => idx === i ? v : t)) }
function removeTime(i) { set('schedule.times', times.value.filter((_, idx) => idx !== i)) }

function ensureSw(patch) {
  set('schedule.sendWindow', {
    enabled: !!sw.value.enabled, timezone: sw.value.timezone || 'Europe/Moscow',
    days: swDays.value, from: sw.value.from || '09:00', to: sw.value.to || '18:00', ...patch,
  })
}
function toggleSwDay(d) {
  const cur = new Set(swDays.value)
  cur.has(d) ? cur.delete(d) : cur.add(d)
  ensureSw({ days: [...cur].sort((a, b) => (a === 0 ? 7 : a) - (b === 0 ? 7 : b)) })
}

const isSilence = computed(() => props.cfg.type === 'silence')

const summary = computed(() => {
  const run = trigger.value === 'dailyTimes'
    ? (times.value.length ? `в ${times.value.join(', ')}` : 'в заданное время (время не указано)')
    : `каждые ${interval.value} мин`
  if (isSilence.value) return `Движок проверит логи ${run} и напишет, если узел молчит дольше порога.`
  if (props.cfg.type === 'any') return `Движок ${run} заберёт новые события и пришлёт письмо на каждое.`
  const depthLabel = formatDurationMinutes(lookback.value)
  const depth = winMode.value === 'fixed'
    ? `за последние ${depthLabel}`
    : `с прошлого прогона (первый раз — за ${depthLabel})`
  return `Движок ${run} соберёт события ${depth} и пришлёт сводку.`
})
</script>

<template>
  <div class="sched">
    <p class="summary">{{ summary }}</p>

    <div class="sub-block">
      <div class="h">Как запускать<InfoHint text="Тик движка — момент, когда правило выполняется. Это не то же самое, за какой период берутся логи." /></div>
      <div class="sub-body">
        <div class="seg">
          <button type="button" class="seg-b" :class="{ on: trigger === 'interval' }" @click="setTrigger('interval')">Периодически</button>
          <button type="button" class="seg-b" :class="{ on: trigger === 'dailyTimes' }" @click="setTrigger('dailyTimes')">В заданное время</button>
        </div>

        <template v-if="trigger === 'interval'">
          <label class="row">
            <span>Интервал</span>
            <DurationInput
              :model-value="interval"
              storage="minutes"
              :presets="INTERVAL_PRESETS"
              @update:model-value="set('schedule.intervalMinutes', $event)"
            />
          </label>
        </template>

        <template v-else-if="trigger === 'dailyTimes'">
          <div class="times">
            <div v-for="(t, i) in times" :key="i" class="time-row">
              <input type="time" class="input narrow" :value="t" @input="setTime(i, $event.target.value)" />
              <button type="button" class="btn btn-icon" @click="removeTime(i)">×</button>
            </div>
          </div>
          <button type="button" class="btn btn-ghost btn-sm" @click="addTime">+ Время</button>
          <label class="row"><span>Часовой пояс</span>
            <input class="input tz" :value="sched.timezone || 'Europe/Moscow'"
                   @input="set('schedule.timezone', $event.target.value)" /></label>
        </template>
      </div>
    </div>

    <div v-if="!isSilence" class="sub-block">
      <div class="h">За какой период брать логи<InfoHint text="Насколько глубоко назад движок читает логи в каждый прогон. Отдельно от того, как часто он запускается." /></div>
      <div class="sub-body">
        <div class="seg">
          <button type="button" class="seg-b" :class="{ on: winMode === 'sliding' }" @click="set('window.mode', 'sliding')">С прошлого прогона</button>
          <button type="button" class="seg-b" :class="{ on: winMode === 'fixed' }" @click="set('window.mode', 'fixed')">Всегда последние N</button>
        </div>

        <label class="row">
          <span>Глубина<InfoHint :text="winMode === 'fixed'
            ? 'Каждый прогон берёт одни и те же последние N часов/дней — одно событие может попасть в несколько писем подряд.'
            : 'Первый прогон возьмёт заданную глубину, дальше — только новое с прошлого раза.'" /></span>
          <DurationInput
            :model-value="lookback"
            storage="minutes"
            :presets="LOOKBACK_PRESETS"
            @update:model-value="set('window.lookbackMinutes', $event)"
          />
        </label>

        <label v-if="winMode === 'sliding'" class="row">
          <span>Запас<InfoHint text="Небольшой нахлёст назад, чтобы не потерять события на стыке двух прогонов." /></span>
          <DurationInput
            :model-value="cfg.window?.overlapMinutes ?? 2"
            storage="minutes"
            :presets="OVERLAP_PRESETS"
            allow-zero
            :min="0"
            @update:model-value="set('window.overlapMinutes', $event)"
          />
        </label>
      </div>
    </div>

    <div class="sub-block">
      <div class="h">Когда можно слать письма<InfoHint text="Даже если правило сработало, письмо уйдёт только в разрешённые часы. Логи при этом всё равно анализируются." /></div>
      <div class="sub-body">
        <label class="row switch"><input type="checkbox" :checked="!!sw.enabled" @change="ensureSw({ enabled: $event.target.checked })" />
          <span>Ограничить часы отправки</span></label>
        <template v-if="sw.enabled">
          <div class="days">
            <button v-for="d in DAYS" :key="d.v" type="button" class="day"
                    :class="{ on: swDays.includes(d.v) }" @click="toggleSwDay(d.v)">{{ d.short }}</button>
          </div>
          <div class="row3">
            <label class="row"><span>С</span>
              <input type="time" class="input" :value="sw.from || '09:00'" @input="ensureSw({ from: $event.target.value })" /></label>
            <label class="row"><span>До</span>
              <input type="time" class="input" :value="sw.to || '18:00'" @input="ensureSw({ to: $event.target.value })" /></label>
            <label class="row"><span>Часовой пояс</span>
              <input class="input" :value="sw.timezone || 'Europe/Moscow'" @input="ensureSw({ timezone: $event.target.value })" /></label>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sched { display: flex; flex-direction: column; gap: 14px; }
.summary {
  margin: 0; padding: 10px 12px; border-radius: 8px;
  background: var(--accent-soft); color: var(--text-dim);
  font-size: 12.5px; line-height: 1.55;
}
.sub-block {
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: var(--panel-2);
  padding: 12px 14px;
}
.sub-body {
  display: flex; flex-direction: column; gap: 12px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border-soft);
}
.h { font-size: 13.5px; font-weight: 600; display: flex; align-items: center; }
.seg {
  display: inline-flex; border: 1px solid var(--border);
  border-radius: 9px; overflow: hidden; width: fit-content; max-width: 100%;
}
.seg-b {
  padding: 7px 14px; font-size: 12.5px; background: var(--panel);
  color: var(--text-dim); border: none; border-right: 1px solid var(--border);
  font-family: var(--sans);
}
.seg-b:last-child { border-right: none; }
.seg-b.on { background: var(--accent-soft); color: var(--accent-bright); }
.row { display: flex; flex-direction: column; gap: 6px; }
.row > span { font-size: 12.5px; color: var(--text-dim); display: flex; align-items: center; }
.row.switch { flex-direction: row; align-items: center; gap: 8px; }
.row.switch input { width: auto; }
.narrow { width: 120px; }
.tz { max-width: 220px; }
.row3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; align-items: start; }
.times { display: flex; flex-direction: column; gap: 6px; }
.time-row { display: flex; gap: 6px; align-items: center; }
.days { display: flex; gap: 6px; flex-wrap: wrap; }
.day {
  width: 40px; height: 34px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--chip); color: var(--text-dim); font-family: var(--sans); font-size: 12px;
}
.day.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-bright); }
@media (max-width: 900px) { .row3 { grid-template-columns: 1fr; } }
</style>
