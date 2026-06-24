<script setup>
// Редактор matchers — пары label=value с переключателем regex.
// Работает через v-model: родитель даёт массив, мы его правим и отдаём обратно.
//
// alerts — боевые алерты окружения (у каждого есть .labels). Подсказки каскадные:
// для строки подсказываем только то, что встречается у алертов, которые уже подходят
// под ОСТАЛЬНЫЕ заполненные matcher-ы. Так после alertname=HighCPUUsage следующий
// matcher предложит только лейблы/значения именно этих алертов, а не вообще все.
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Array, required: true },
  showErrors: { type: Boolean, default: false }, // подсветить пустые поля красным
  alerts: { type: Array, default: () => [] }, // боевые алерты env; пусто — без подсказок
})
const emit = defineEmits(['update:modelValue'])

// Наборы лейблов по алертам: [{ alertname: '...', instance: '...' }, ...].
const labelSets = computed(() => props.alerts.map((a) => a.labels || {}))

// Алерты, подходящие под все ЗАПОЛНЕННЫЕ matcher-ы, кроме строки skip.
// Учитываем только обычные (не regex) пары с непустыми name и value.
function matchingSets(skip) {
  const active = props.modelValue.filter(
    (m, idx) => idx !== skip && m.name && m.value && !m.isRegex,
  )
  return labelSets.value.filter((ls) => active.every((m) => ls[m.name] === m.value))
}

// Имена лейблов для строки i: что есть у подходящих алертов, минус уже занятые
// в других строках (повторно тот же лейбл предлагать незачем).
function namesFor(i) {
  const used = new Set(
    props.modelValue.filter((m, idx) => idx !== i && m.name).map((m) => m.name),
  )
  const names = new Set()
  for (const ls of matchingSets(i)) {
    for (const k of Object.keys(ls)) if (!used.has(k)) names.add(k)
  }
  return [...names].sort()
}

// Значения для строки i: значения её лейбла среди подходящих алертов.
function valuesFor(i) {
  const name = props.modelValue[i]?.name
  if (!name) return []
  const values = new Set()
  for (const ls of matchingSets(i)) if (ls[name] != null) values.add(ls[name])
  return [...values].sort()
}

// Сколько боевых алертов сейчас попадает под ВСЕ заполненные matcher-ы (с regex).
// null — пока нечего считать (нет matcher-ов или нет алертов): подпись не показываем.
function matchOne(m, v) {
  if (v == null) return false
  if (!m.isRegex) return v === m.value
  try {
    return new RegExp('^(?:' + m.value + ')$').test(v) // AM матчит regex по всей строке
  } catch {
    return false // кривой regex — считаем, что не совпало
  }
}
const matchCount = computed(() => {
  const active = props.modelValue.filter((m) => m.name && m.value)
  if (!active.length || !labelSets.value.length) return null
  return labelSets.value.filter((ls) => active.every((m) => matchOne(m, ls[m.name]))).length
})
// 1 алерт / 2 алерта / 5 алертов
function alertWord(n) {
  const t = n % 100, o = n % 10
  if (t >= 11 && t <= 14) return 'алертов'
  if (o === 1) return 'алерт'
  if (o >= 2 && o <= 4) return 'алерта'
  return 'алертов'
}

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
      <!-- Имена и значения каскадные: зависят от уже заполненных строк. -->
      <input class="input" :class="{ invalid: showErrors && !m.name }" v-model="m.name" :list="'lbl-names-' + i" placeholder="alertname" />
      <datalist :id="'lbl-names-' + i">
        <option v-for="n in namesFor(i)" :key="n" :value="n" />
      </datalist>
      <input class="input" :class="{ invalid: showErrors && !m.value }" v-model="m.value" :list="'lbl-vals-' + i" placeholder="HighCPUUsage" />
      <datalist :id="'lbl-vals-' + i">
        <option v-for="v in valuesFor(i)" :key="v" :value="v" />
      </datalist>
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

    <!-- Сколько боевых алертов сейчас ловит этот набор matcher-ов. -->
    <p v-if="matchCount !== null" class="match" :class="{ none: matchCount === 0 }">
      <template v-if="matchCount">сейчас под matchers подходит <b>{{ matchCount }}</b> {{ alertWord(matchCount) }}</template>
      <template v-else>сейчас под matchers не подходит ни один алерт</template>
    </p>
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

.match { margin: 10px 0 0; font-size: 12px; color: var(--text-mute); }
.match b { color: var(--text); }
.match.none { color: var(--warning, #d39a3a); }
</style>
