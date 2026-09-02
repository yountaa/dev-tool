<script setup>
// Статус запусков алертов: интервал, последний прогон, успех, ошибки.
import { computed, ref } from 'vue'
import { fmtDt } from '../../../shared/time.js'
import { TYPE_INFO } from '../templates.js'

const props = defineProps({
  alerts: { type: Array, default: () => [] },
  engine: { type: Object, default: null },
  engineAlive: { type: Boolean, default: null },
})
const emit = defineEmits(['reload'])

const SYSTEM_KEYS = new Set(['__engine__', '__heartbeat__', 'engine-heartbeat'])
const query = ref('')

function typeLabel(t) {
  return TYPE_INFO[t]?.title || t || '—'
}

function ago(iso) {
  if (!iso) return 'ещё не запускался'
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} мин назад`
  return `${Math.round(min / 60)} ч назад`
}

const rows = computed(() => {
  const q = query.value.trim().toLowerCase()
  return (props.alerts || [])
    .filter((a) => a?.alertKey && !SYSTEM_KEYS.has(a.alertKey))
    .filter((a) => {
      if (!q) return true
      const hay = [
        a.name,
        a.alertKey,
        a.type,
        typeLabel(a.type),
        a.state?.lastError,
      ].filter(Boolean).join(' ').toLowerCase()
      return hay.includes(q)
    })
    .sort((a, b) => {
      const ta = Date.parse(a.state?.lastRunAt || '') || 0
      const tb = Date.parse(b.state?.lastRunAt || '') || 0
      return tb - ta
    })
})

function runStatus(a) {
  if (a.state?.lastError) return 'err'
  if (!a.enabled) return 'off'
  if (!a.state?.lastRunAt) return 'none'
  return 'ok'
}
</script>

<template>
  <div class="runs">
    <div class="row-head">
      <div>
        <h2 class="tab-title">Запуски</h2>
        <p class="tab-desc">Когда движок последний раз проверял каждый алерт и были ли ошибки.</p>
      </div>
      <button type="button" class="btn btn-ghost btn-sm" @click="emit('reload')">Обновить</button>
    </div>

    <div
      class="engine-banner"
      :class="{ bad: engineAlive === false, none: engineAlive === null }"
    >
      <span class="pip"></span>
      <div class="engine-text">
        <strong>backend-workflow</strong>
        <span v-if="engineAlive === true">работает · тик {{ ago(engine?.lastRunAt) }}</span>
        <span v-else-if="engineAlive === false">молчит с {{ ago(engine?.lastRunAt) }}</span>
        <span v-else-if="engine?.lastRunAt">последний тик {{ ago(engine.lastRunAt) }}</span>
        <span v-else>ни разу не отчитывался</span>
        <span v-if="engine?.lastError" class="engine-err"> · {{ engine.lastError }}</span>
      </div>
    </div>

    <div class="filters">
      <input v-model="query" class="input search" placeholder="Поиск по имени, ключу, типу…" />
      <span class="cnt">{{ rows.length }} алертов</span>
    </div>

    <div v-if="!rows.length" class="empty">Нет алертов для отображения</div>

    <div v-else class="table-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th>Алерт</th>
            <th>Тип</th>
            <th>Статус</th>
            <th>Интервал</th>
            <th>Последний запуск</th>
            <th>Успешный отчёт</th>
            <th>Ошибка</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="a in rows" :key="a.alertKey" :class="runStatus(a)">
            <td class="name-cell">
              <span class="nm">{{ a.name || a.alertKey }}</span>
              <span class="key">{{ a.alertKey }}</span>
            </td>
            <td>{{ typeLabel(a.type) }}</td>
            <td>
              <span class="badge" :class="runStatus(a)">
                {{ a.enabled ? (a.state?.lastError ? 'ошибка' : 'вкл') : 'выкл' }}
              </span>
            </td>
            <td class="mono">{{ a.intervalMinutes }} мин</td>
            <td class="mono">
              <span v-if="a.state?.lastRunAt">{{ fmtDt(a.state.lastRunAt) }}</span>
              <span v-else class="mute">—</span>
              <span v-if="a.state?.lastRunAt" class="ago">{{ ago(a.state.lastRunAt) }}</span>
            </td>
            <td class="mono">
              <span v-if="a.state?.lastSuccessAt">{{ fmtDt(a.state.lastSuccessAt) }}</span>
              <span v-else class="mute">—</span>
            </td>
            <td class="err-cell" :title="a.state?.lastError || ''">
              {{ a.state?.lastError || '—' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.runs { min-width: 0; }
.row-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.tab-title { font-size: 17px; font-weight: 700; margin: 2px 0; }
.tab-desc { color: var(--text-dim); margin: 0; font-size: 13px; }
.engine-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
  border-radius: 9px;
  border: 1px solid var(--border-soft);
  background: var(--panel);
  font-size: 13px;
  color: var(--text-dim);
}
.engine-banner .pip {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #50c878;
  flex: none;
  margin-top: 5px;
}
.engine-banner.bad .pip,
.engine-banner.none .pip { background: var(--danger); }
.engine-text { display: flex; flex-wrap: wrap; gap: 0 6px; line-height: 1.5; }
.engine-text strong { color: var(--text); }
.engine-err { color: var(--danger); }
.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.search { flex: 1; min-width: 160px; }
.cnt { font-size: 12px; color: var(--text-mute); white-space: nowrap; }
.empty {
  padding: 24px;
  text-align: center;
  color: var(--text-mute);
  font-size: 13px;
  border: 1px dashed var(--border-soft);
  border-radius: 9px;
}
.table-wrap {
  overflow: auto;
  border: 1px solid var(--border-soft);
  border-radius: 9px;
  background: var(--panel);
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.tbl th {
  text-align: left;
  padding: 10px 12px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-mute);
  background: var(--panel-2);
  border-bottom: 1px solid var(--border-soft);
  white-space: nowrap;
}
.tbl td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border-soft);
  vertical-align: top;
}
.tbl tbody tr:last-child td { border-bottom: none; }
.tbl tbody tr:hover { background: var(--panel-2); }
.tbl tbody tr.err { background: rgba(248, 81, 73, 0.06); }
.name-cell { min-width: 160px; max-width: 280px; }
.nm {
  display: block;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.key {
  display: block;
  font-family: var(--mono);
  font-size: 11px;
  color: var(--text-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mono { font-family: var(--mono); font-size: 12px; white-space: nowrap; }
.mute { color: var(--text-mute); }
.ago {
  display: block;
  font-size: 11px;
  color: var(--text-mute);
  margin-top: 2px;
}
.err-cell {
  max-width: 240px;
  font-size: 12px;
  color: var(--danger);
  word-break: break-word;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  background: var(--chip);
  color: var(--text-dim);
}
.badge.ok { background: rgba(80, 200, 120, 0.15); color: #3d9a62; }
.badge.err { background: rgba(248, 81, 73, 0.12); color: var(--danger); }
.badge.off { opacity: 0.75; }
.badge.none { opacity: 0.75; }
@media (max-width: 900px) {
  .table-wrap { overflow-x: auto; }
}
</style>
