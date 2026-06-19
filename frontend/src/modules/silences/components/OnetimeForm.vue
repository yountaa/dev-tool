<script setup>
// Вкладка «Разовый»: matchers + период по датам + метаданные.
// Сразу ставит silence в AM (бэкенд) и сохраняет конфиг в git.
import { ref, reactive } from 'vue'
import MatchersEditor from './MatchersEditor.vue'
import { silencesApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  me: { type: String, default: '' }, // имя из Keycloak (когда auth=true)
  auth: { type: Boolean, default: false }, // true — имя из Keycloak, false — вводим руками
})
const emit = defineEmits(['created'])

const form = reactive({
  name: '',
  matchers: [{ name: '', value: '', isRegex: false }],
  starts_at: '',
  ends_at: '',
  created_by: '', // используется только когда auth=false (ручной ввод)
  comment: '',
})

const busy = ref(false)
const msg = ref(null) // { ok: bool, text: string }
const showErrors = ref(false) // подсветить незаполненные поля после попытки сохранить

// Какие обязательные поля пустые.
function badMatchers() {
  return form.matchers.some((m) => !m.name || !m.value)
}
function isValid() {
  const creatorOk = props.auth || form.created_by // при ручном вводе имя обязательно
  return form.name && creatorOk && form.comment && form.starts_at && form.ends_at && !badMatchers()
}

function reset() {
  form.name = ''
  form.matchers = [{ name: '', value: '', isRegex: false }]
  form.starts_at = ''
  form.ends_at = ''
  form.created_by = ''
  form.comment = ''
  msg.value = null
  showErrors.value = false
}

async function submit() {
  showErrors.value = true
  if (!isValid()) {
    msg.value = { ok: false, text: 'Заполни поля, отмеченные красным' }
    return
  }
  busy.value = true
  msg.value = null
  try {
    const res = await silencesApi.createOnetime(props.env, {
      ...form,
      created_by: props.auth ? props.me : form.created_by,
    })
    msg.value = res.placed
      ? { ok: true, text: `Создан silence ${res.silence_id}` }
      : { ok: true, text: 'Правило сохранено. AM сейчас недоступен — silence поставит шедулер.' }
    emit('created')
  } catch (e) {
    msg.value = { ok: false, text: e.message }
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div>
    <h2 class="tab-title">Разовый silence</h2>
    <p class="tab-desc">Заглушить алерты на конкретный период по датам.</p>

    <MatchersEditor v-model="form.matchers" :show-errors="showErrors" />

    <div class="card">
      <div class="card-head">
        <span class="card-title">Период</span>
        <span class="card-hint">локальное время</span>
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Начало<span class="req">*</span></label>
          <input class="input" :class="{ invalid: showErrors && !form.starts_at }" type="datetime-local" v-model="form.starts_at" />
        </div>
        <div class="field">
          <label>Конец<span class="req">*</span></label>
          <input class="input" :class="{ invalid: showErrors && !form.ends_at }" type="datetime-local" v-model="form.ends_at" />
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span class="card-title">Метаданные</span>
      </div>
      <div class="field" style="margin-bottom: 14px">
        <label>Название правила<span class="req">*</span></label>
        <input class="input" :class="{ invalid: showErrors && !form.name }" v-model="form.name" placeholder="Бэкап db-01" />
      </div>
      <div class="field" style="margin-bottom: 14px">
        <label>Создатель<span v-if="!auth" class="req">*</span></label>
        <input v-if="!auth" class="input" :class="{ invalid: showErrors && !form.created_by }" v-model="form.created_by" placeholder="ivan.petrov" />
        <div v-else class="who">{{ me || '—' }}</div>
      </div>
      <div class="field">
        <label>Комментарий<span class="req">*</span></label>
        <textarea class="input" :class="{ invalid: showErrors && !form.comment }" v-model="form.comment" placeholder="Плановые работы, тикет INFRA-1234"></textarea>
      </div>
    </div>

    <div class="actions">
      <button class="btn btn-primary" :disabled="busy" @click="submit">
        {{ busy ? 'Создаётся…' : `✓ Создать в ${env}` }}
      </button>
      <button class="btn" @click="reset">Сбросить</button>
    </div>

    <div v-if="msg" class="msg" :class="msg.ok ? 'msg-ok' : 'msg-err'">{{ msg.text }}</div>
  </div>
</template>

<style scoped>
.tab-title { font-size: 18px; font-weight: 700; margin: 4px 0 4px; }
.tab-desc { color: var(--text-dim); margin: 0 0 18px; }
.actions { display: flex; align-items: center; gap: 12px; }
.who {
  font-family: var(--mono); font-size: 13px; color: var(--text);
  background: var(--panel-2); border: 1px solid var(--border-soft);
  border-radius: 7px; padding: 8px 11px;
}
</style>
