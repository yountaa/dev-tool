<script setup>
// Вкладка «Рабочие правила»: локальные правила из git-хаба (а не из AM).
// Видно тип, имя и статус (считается локально). Можно искать, фильтровать,
// редактировать, включать/выключать тумблером и удалять.
import { ref, reactive, computed, watch } from 'vue'
import MatchersEditor from './MatchersEditor.vue'
import ScheduleEditor from './ScheduleEditor.vue'
import { silencesApi } from '../api.js'

const props = defineProps({
  env: { type: String, required: true },
  items: { type: Array, required: true },
  auth: { type: Boolean, default: false }, // true — создатель из Keycloak (только показ)
  alerts: { type: Array, default: () => [] }, // боевые алерты env для подсказок matchers
})
const emit = defineEmits(['reload'])

const KIND = { manual: 'manual', schedule: 'schedule' }
const DAY = { mon: 'Пн', tue: 'Вт', wed: 'Ср', thu: 'Чт', fri: 'Пт', sat: 'Сб', sun: 'Вс' }
const TYPES = [['all', 'all'], ['manual', 'manual'], ['schedule', 'schedule']]
const STATUSES = [['all', 'all'], ['on', 'enabled'], ['off', 'disabled']]

const typeFilter = ref('all')
const statusFilter = ref('all')
const query = ref('')

const busy = ref(false)
const editingId = ref(null)
const edit = reactive({ kind: '', name: '', matchers: [], starts_at: '', ends_at: '', windows: [], created_by: '', comment: '' })

function matchersText(m) {
  return (m || []).map((x) => `${x.name}${x.isRegex ? '=~' : '='}${x.value}`).join(', ')
}
function fmtDt(s) {
  if (!s) return ''
  const [d, t] = s.split('T')
  const [, mo, da] = d.split('-')
  return `${da}.${mo} ${(t || '').slice(0, 5)}`
}
function windowsText(windows) {
  return (windows || [])
    .map((w) => {
      const days = w.days.map((d) => DAY[d] || d).join(',')
      const time = w.start === w.end ? 'весь день' : `${w.start}–${w.end}`
      return `${days} ${time}`
    })
    .join('; ')
}
function periodText(r) {
  return r.kind === 'manual'
    ? `${fmtDt(r.payload.starts_at)} → ${fmtDt(r.payload.ends_at)}`
    : windowsText(r.payload.windows)
}
function pluralWindows(n) {
  const a = n % 10
  const b = n % 100
  if (a === 1 && b !== 11) return 'окно'
  if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return 'окна'
  return 'окон'
}
// Одна сжатая строка: условие · когда · кто. Расписание показываем кратко
// («N окон»), полную сетку видно при раскрытии. Длинное обрезается многоточием.
function subText(r) {
  const parts = [matchersText(r.payload.matchers)]
  if (r.kind === 'manual') {
    parts.push(periodText(r))
  } else {
    const n = (r.payload.windows || []).length
    parts.push(`${n} ${pluralWindows(n)}`)
  }
  if (r.payload.created_by) parts.push(r.payload.created_by)
  return parts.filter(Boolean).join('   ·   ')
}

// Клик по плашке: раскрыть на редактирование или свернуть обратно.
function toggleExpand(r) {
  if (editingId.value === r.id) editingId.value = null
  else startEdit(r)
}

const filtered = computed(() =>
  props.items.filter((r) => {
    if (typeFilter.value !== 'all' && r.kind !== typeFilter.value) return false
    if (statusFilter.value === 'on' && !r.enabled) return false
    if (statusFilter.value === 'off' && r.enabled) return false
    if (query.value) {
      const hay = `${r.name || ''} ${matchersText(r.payload.matchers)}`.toLowerCase()
      if (!hay.includes(query.value.toLowerCase())) return false
    }
    return true
  }),
)

// Пагинация: показываем по PAGE_SIZE на странице, остальное листаем.
const PAGE_SIZE = 20
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const paged = computed(() => filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
// Смена фильтра/поиска — на первую страницу.
watch([typeFilter, statusFilter, query], () => { page.value = 1 })

async function toggle(r) {
  busy.value = true
  try {
    if (r.enabled) await silencesApi.disableRule(props.env, r.kind, r.id)
    else await silencesApi.enableRule(props.env, r.kind, r.id)
    emit('reload')
  } finally {
    busy.value = false
  }
}

async function remove(r) {
  if (!confirm(`Удалить правило «${r.name || r.id}»?`)) return
  busy.value = true
  try {
    await silencesApi.deleteRule(props.env, r.kind, r.id)
    emit('reload')
  } finally {
    busy.value = false
  }
}

function startEdit(r) {
  editingId.value = r.id
  edit.kind = r.kind
  edit.name = r.payload.name || ''
  edit.matchers = (r.payload.matchers || []).map((m) => ({ name: m.name, value: m.value, isRegex: !!m.isRegex }))
  edit.created_by = r.payload.created_by || ''
  edit.comment = r.payload.comment || ''
  if (r.kind === 'manual') {
    edit.starts_at = (r.payload.starts_at || '').slice(0, 16)
    edit.ends_at = (r.payload.ends_at || '').slice(0, 16)
    edit.windows = []
  } else {
    edit.windows = (r.payload.windows || []).map((w) => ({ days: [...w.days], start: w.start, end: w.end }))
    edit.starts_at = ''
    edit.ends_at = ''
  }
}

async function saveEdit(r) {
  busy.value = true
  try {
    if (r.kind === 'manual') {
      await silencesApi.editManualRule(props.env, r.id, {
        name: edit.name, matchers: edit.matchers,
        starts_at: edit.starts_at, ends_at: edit.ends_at,
        created_by: edit.created_by, comment: edit.comment,
      })
    } else {
      await silencesApi.editScheduleRule(props.env, r.id, {
        name: edit.name, matchers: edit.matchers,
        windows: edit.windows,
        created_by: edit.created_by, comment: edit.comment,
      })
    }
    editingId.value = null
    emit('reload')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div>
    <div class="row-head">
      <div>
        <h2 class="tab-title">Рабочие правила</h2>
        <p class="tab-desc">Локальные правила окружения <b>{{ env }}</b> — список и статус считаются у нас, не из Alertmanager.</p>
      </div>
      <button class="btn btn-sm" :disabled="busy" @click="emit('reload')">обновить</button>
    </div>

    <div class="filters">
      <div class="pills">
        <button v-for="[id, label] in TYPES" :key="id" class="pill" :class="{ on: typeFilter === id }" @click="typeFilter = id">{{ label }}</button>
      </div>
      <div class="pills">
        <button v-for="[id, label] in STATUSES" :key="id" class="pill" :class="{ on: statusFilter === id }" @click="statusFilter = id">{{ label }}</button>
      </div>
      <input class="input search" v-model="query" placeholder="search by name or labels…" />
    </div>

    <p v-if="!filtered.length" class="tab-desc">Ничего не найдено.</p>

    <div v-for="r in paged" :key="r.kind + r.id" class="rule" :class="{ open: editingId === r.id }">
      <div class="rule-row" @click="toggleExpand(r)">
        <span class="dot" :class="r.enabled ? 'enabled' : 'disabled'"></span>
        <div class="rule-info">
          <div class="rule-head">
            <span class="nm">{{ r.name || '(без имени)' }}</span>
            <span class="kind" :class="r.kind">{{ KIND[r.kind] }}</span>
            <span class="state" :class="r.enabled ? 'enabled' : 'disabled'">{{ r.enabled ? 'enabled' : 'disabled' }}</span>
          </div>
          <div class="sub" :title="subText(r)">{{ subText(r) }}</div>
        </div>
        <div class="ctrls">
          <button class="sw" :class="{ on: r.enabled }" :title="r.enabled ? 'выключить' : 'включить'" :disabled="busy" @click.stop="toggle(r)">
            <span class="knob"></span>
          </button>
          <button class="ico danger" title="удалить" :disabled="busy" @click.stop="remove(r)">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3" /></svg>
          </button>
          <span class="chev">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
          </span>
        </div>
      </div>

      <!-- Редактирование того же правила -->
      <div v-if="editingId === r.id" class="edit">
        <div class="field" style="margin-bottom: 12px">
          <label>Название</label>
          <input class="input" v-model="edit.name" />
        </div>
        <MatchersEditor v-model="edit.matchers" :alerts="alerts" />

        <template v-if="r.kind === 'manual'">
          <div class="grid-2">
            <div class="field">
              <label>Начало</label>
              <input class="input" type="datetime-local" v-model="edit.starts_at" />
            </div>
            <div class="field">
              <label>Конец</label>
              <input class="input" type="datetime-local" v-model="edit.ends_at" />
            </div>
          </div>
        </template>
        <div v-else class="card" style="margin-top: 4px">
          <ScheduleEditor v-model="edit.windows" />
        </div>

        <div class="grid-2" style="margin-top: 12px">
          <div class="field">
            <label>Создатель</label>
            <input v-if="!auth" class="input" v-model="edit.created_by" placeholder="ivan.petrov" />
            <div v-else class="who">{{ edit.created_by || '—' }}</div>
          </div>
          <div class="field">
            <label>Комментарий</label>
            <input class="input" v-model="edit.comment" />
          </div>
        </div>

        <div class="edit-actions">
          <button class="btn btn-primary btn-sm" :disabled="busy" @click="saveEdit(r)">{{ busy ? 'Сохраняется…' : 'Сохранить' }}</button>
          <button class="btn btn-sm" @click="editingId = null">Отмена</button>
        </div>
      </div>
    </div>

    <!-- Пагинация -->
    <div v-if="totalPages > 1" class="pager">
      <button class="btn btn-sm" :disabled="page === 1" @click="page--">←</button>
      <span class="pager-info">{{ page }} / {{ totalPages }}</span>
      <button class="btn btn-sm" :disabled="page === totalPages" @click="page++">→</button>
    </div>
  </div>
</template>

<style scoped>
.row-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 12px; }
.tab-title { font-size: 17px; font-weight: 700; margin: 2px 0; }
.tab-desc { color: var(--text-dim); margin: 0; font-size: 13px; }

.filters { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.pills { display: flex; gap: 4px; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 8px; padding: 4px; }
.pill { background: transparent; border: none; color: var(--text-dim); border-radius: 6px; padding: 5px 11px; font-size: 12px; }
.pill:hover { color: var(--text); }
.pill.on { background: var(--panel-2); color: var(--accent-bright); }
.search { width: auto; flex: 1; min-width: 160px; }

.pager { display: flex; align-items: center; justify-content: center; gap: 12px; margin-top: 14px; }
.pager-info { font-family: var(--mono); font-size: 13px; color: var(--text-mute); min-width: 56px; text-align: center; }

.rule { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 9px; padding: 9px 14px; margin-bottom: 7px; transition: border-color 0.12s; }
.rule:hover { border-color: var(--border); }
.rule.open { border-color: var(--accent); }
.rule-row { display: flex; align-items: center; gap: 11px; cursor: pointer; user-select: none; }
.chev { display: flex; color: var(--text-mute); transition: transform 0.15s; }
.rule.open .chev { transform: rotate(180deg); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--track); flex: none; }
.dot.enabled { background: var(--accent-bright); }
.dot.disabled { background: var(--track); }

.rule-info { flex: 1; min-width: 0; }
.rule-head { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; min-width: 0; }
.nm { font-weight: 600; font-size: 14px; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.kind, .state { flex: none; }
.kind { font-size: 10px; padding: 1px 7px; border-radius: 20px; white-space: nowrap; }
.kind.manual { background: var(--info-soft); color: var(--info); }
.kind.schedule { background: var(--accent-soft); color: var(--accent-bright); }
.state { font-size: 10px; padding: 1px 6px; border-radius: 20px; background: var(--chip); color: var(--text-dim); }
.state.enabled { background: var(--accent-soft); color: var(--accent-bright); }
.state.disabled { background: var(--chip); color: var(--text-mute); }

.sub {
  font-family: var(--mono); font-size: 12px; color: var(--text-mute);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.ctrls { display: flex; align-items: center; gap: 6px; flex: none; }
.ico { background: transparent; border: 1px solid var(--border); color: var(--text-mute); border-radius: 6px; padding: 5px; display: flex; align-items: center; justify-content: center; }
.ico:hover { color: var(--text); border-color: var(--text-mute); }
.ico.danger:hover { color: var(--danger); border-color: var(--danger); }

.sw { width: 34px; height: 19px; border-radius: 11px; padding: 0; background: var(--track); border: 1px solid var(--border); position: relative; cursor: pointer; flex: none; }
.sw .knob { position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: var(--text-mute); transition: transform 0.15s, background 0.15s; }
.sw.on { background: var(--accent); border-color: var(--accent); }
.sw.on .knob { transform: translateX(15px); background: #fff; }

.edit { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border-soft); }
.edit-actions { display: flex; gap: 10px; margin-top: 12px; }
.who {
  font-family: var(--mono); font-size: 13px; color: var(--text);
  background: var(--panel-2); border: 1px solid var(--border-soft);
  border-radius: 7px; padding: 8px 11px;
}
</style>
