<script setup>
// Вкладка «По расписанию»: matchers + сетка времени (дни × часы) + метаданные.
// Выбор ячеек превращаем в окна (windows) и сохраняем в git — ставит шедулер.
import { ref, reactive } from 'vue'
import MatchersEditor from './MatchersEditor.vue'
import ScheduleGrid from './ScheduleGrid.vue'
import { silencesApi } from '../api.js'
import { cellsToWindows } from '../cells.js'

const props = defineProps({ env: { type: String, required: true } })
const emit = defineEmits(['created'])

const form = reactive({
  name: '',
  matchers: [{ name: '', value: '', isRegex: false }],
  cells: [], // ключи "mon-13" из сетки
  created_by: '',
  comment: '',
})

const busy = ref(false)
const msg = ref(null)
const preview = ref(null)
const showErrors = ref(false)

function badMatchers() {
  return form.matchers.some((m) => !m.name || !m.value)
}
function isValid() {
  return form.name && form.created_by && form.comment && form.cells.length && !badMatchers()
}

function payload() {
  return {
    name: form.name,
    matchers: form.matchers,
    windows: cellsToWindows(form.cells),
    created_by: form.created_by,
    comment: form.comment,
  }
}

function reset() {
  form.name = ''
  form.matchers = [{ name: '', value: '', isRegex: false }]
  form.cells = []
  form.created_by = ''
  form.comment = ''
  msg.value = null
  preview.value = null
  showErrors.value = false
}

async function submit() {
  showErrors.value = true
  if (!isValid()) {
    msg.value = { ok: false, text: 'Заполни поля, отмеченные красным, и выбери время в сетке' }
    return
  }
  busy.value = true
  msg.value = null
  try {
    const res = await silencesApi.createSchedule(props.env, payload())
    msg.value = { ok: true, text: `Расписание «${form.name}» сохранено (конфиг ${res.id})` }
    emit('created')
  } catch (e) {
    msg.value = { ok: false, text: e.message }
  } finally {
    busy.value = false
  }
}

async function doPreview() {
  busy.value = true
  msg.value = null
  try {
    preview.value = await silencesApi.previewSchedule(props.env, payload())
  } catch (e) {
    msg.value = { ok: false, text: e.message }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div>
    <h2 class="tab-title">Silence по расписанию</h2>
    <p class="tab-desc">Повторяющееся окно тишины. Отметь дни и часы — клик или протяни мышью.</p>

    <MatchersEditor v-model="form.matchers" :show-errors="showErrors" />

    <div class="card" :class="{ 'card-invalid': showErrors && !form.cells.length }">
      <div class="card-head">
        <span class="card-title">Окно расписания</span>
        <span class="card-hint">часы 0–23, дни недели</span>
      </div>
      <ScheduleGrid v-model="form.cells" />
    </div>

    <div class="card">
      <div class="card-head"><span class="card-title">Метаданные</span></div>
      <div class="field" style="margin-bottom: 14px">
        <label>Название расписания<span class="req">*</span></label>
        <input class="input" :class="{ invalid: showErrors && !form.name }" v-model="form.name" placeholder="nightly-backup-window" />
      </div>
      <div class="field" style="margin-bottom: 14px">
        <label>Создал<span class="req">*</span></label>
        <input class="input" :class="{ invalid: showErrors && !form.created_by }" v-model="form.created_by" placeholder="ivan.petrov" />
      </div>
      <div class="field">
        <label>Комментарий<span class="req">*</span></label>
        <textarea class="input" :class="{ invalid: showErrors && !form.comment }" v-model="form.comment" placeholder="Ночные регламентные работы, тикет INFRA-1234"></textarea>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-primary" :disabled="busy" @click="submit">{{ busy ? 'Сохраняется…' : `✓ Сохранить расписание в ${env}` }}</button>
      <button class="btn" :disabled="busy" @click="doPreview">Предпросмотр</button>
      <button class="btn" @click="reset">Сбросить</button>
    </div>

    <div v-if="msg" class="msg" :class="msg.ok ? 'msg-ok' : 'msg-err'">{{ msg.text }}</div>

    <div v-if="preview" class="card preview">
      <div class="card-head"><span class="card-title">Предпросмотр — ближайшие silence</span></div>
      <p v-if="!preview.length" class="tab-desc">Сейчас нет подходящих окон.</p>
      <div v-for="(b, i) in preview" :key="i" class="prev-row">
        <span class="mono">{{ b.startsAt }} → {{ b.endsAt }}</span>
        <span class="badge" :class="b.already_exists ? 'badge-dim' : 'badge-new'">
          {{ b.already_exists ? 'уже есть' : 'будет создан' }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tab-title { font-size: 18px; font-weight: 700; margin: 4px 0 4px; }
.tab-desc { color: var(--text-dim); margin: 0 0 18px; }
.actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.card-invalid { border-color: var(--danger); }

.preview { margin-top: 24px; }
.preview .prev-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 0; border-bottom: 1px solid var(--border-soft);
}
.mono { font-family: var(--mono); font-size: 12px; color: var(--text-dim); }
.badge { font-size: 11px; padding: 3px 9px; border-radius: 20px; }
.badge-new { background: var(--accent-soft); color: var(--accent-bright); }
.badge-dim { background: var(--chip); color: var(--text-mute); }
</style>
