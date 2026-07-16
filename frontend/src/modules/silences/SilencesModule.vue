<script setup>
// Корень модуля silences: сверху вкладки окружений (динамические, из бэкенда),
// под ними под-вкладки Разовый / По расписанию / Существующие, дальше контент.
import { ref, computed, watch, watchEffect, onMounted, onUnmounted } from 'vue'
import { urlParams, setUrlParams } from '../../shared/urlstate.js'
import { silencesApi } from './api.js'
import ManualForm from './components/ManualForm.vue'
import ScheduleForm from './components/ScheduleForm.vue'
import RulesList from './components/RulesList.vue'
import AlertsList from './components/AlertsList.vue'
import HistoryList from './components/HistoryList.vue'

// me/auth приходят из App.vue (общий источник — /silences/me).
const props = defineProps({
  me: { type: String, default: '' },
  auth: { type: Boolean, default: false },
})

const envs = ref([]) // [{ name }]
const env = ref(null) // активное окружение
const tab = ref('manual') // активная под-вкладка
const rules = ref([]) // локальные правила активного env (для списка и счётчика)
const alerts = ref([]) // алерты активного env (правила Prometheus/vmalert + пометки AM)
const history = ref([]) // локальная история действий активного env
const error = ref(null)

// Подсказки matchers: наборы лейблов из правил (статические) и их сработавших
// серий (динамические — instance, pod…). MatchersEditor ждёт массив с .labels.
const suggestAlerts = computed(() =>
  alerts.value.flatMap((r) => [
    { labels: r.labels },
    ...(r.instances || []).map((i) => ({ labels: i.labels })),
  ]),
)

const TABS = [
  ['manual', 'Manual'],
  ['schedule', 'Schedule'],
  ['rules', 'Silences'],
  ['alerts', 'Alerts'],
  ['history', 'History'],
]

// Снимок параметров URL на момент открытия (ссылка от коллеги) — читаем до того,
// как наш вотчер начнёт перезаписывать URL своим состоянием.
const initial = urlParams()

onMounted(async () => {
  try {
    envs.value = await silencesApi.environments()
    // Окружение и вкладка: из URL (если валидны), иначе первое/по умолчанию.
    const urlTab = initial.get('tab')
    if (urlTab && TABS.some(([id]) => id === urlTab)) tab.value = urlTab
    const wanted = initial.get('env')
    env.value = envs.value.some((e) => e.name === wanted) ? wanted : envs.value[0]?.name ?? null
  } catch (e) {
    error.value = e.message
  }
})

// Текущий вид — в URL, чтобы ссылку можно было отправить коллеге.
watchEffect(() => {
  if (env.value) setUrlParams({ env: env.value, tab: tab.value })
})

// И алерты (firing/pending/inactive + пометки AM), и список silence меняются сами
// (правило загорелось/потухло, кто-то добавил silence) — раз в REFRESH_MS тихо
// перечитываем оба раздела, чтобы видеть актуальную картину без перезагрузки.
const REFRESH_MS = 15000
let timer = null
onMounted(() => {
  timer = setInterval(() => {
    if (env.value) { loadAlerts(); loadRules() }
  }, REFRESH_MS)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

// При смене окружения перечитываем всё, что зависит от него.
watch(env, loadEnv, { immediate: false })

async function loadEnv() {
  await Promise.all([loadRules(), loadAlerts(), loadHistory()])
}

async function loadHistory() {
  if (!env.value) return
  try {
    history.value = await silencesApi.history(env.value)
  } catch (e) {
    history.value = []
  }
}

// После изменения правила обновляем и список, и историю (там новая запись).
function onChange() {
  loadRules()
  loadHistory()
}

async function loadRules() {
  if (!env.value) return
  try {
    rules.value = await silencesApi.rules(env.value)
  } catch (e) {
    rules.value = []
    error.value = e.message
  }
}

// Алерты берутся напрямую из Alertmanager — если он недоступен, просто оставляем
// пусто: вкладка «Алерты» и подсказки matchers будут пустыми, остальное работает.
async function loadAlerts() {
  if (!env.value) return
  try {
    alerts.value = await silencesApi.alerts(env.value)
  } catch (e) {
    alerts.value = []
  }
}
</script>

<template>
  <div>
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <!-- Окружения модуля -->
    <div class="envs">
      <span class="envs-label">Окружение</span>
      <div class="env-list">
        <button
          v-for="e in envs"
          :key="e.name"
          class="env"
          :class="{ active: e.name === env }"
          @click="env = e.name"
        >
          <span class="dot" :class="{ live: e.name === env }"></span>
          <span class="env-name">{{ e.name }}</span>
        </button>
      </div>
    </div>

    <!-- Под-вкладки -->
    <div class="subtabs">
      <button
        v-for="[id, label] in TABS"
        :key="id"
        class="subtab"
        :class="{ active: tab === id }"
        @click="tab = id"
      >
        {{ label }}
        <span v-if="id === 'rules' && rules.length" class="count">{{ rules.length }}</span>
        <span v-if="id === 'alerts' && alerts.length" class="count">{{ alerts.length }}</span>
      </button>
    </div>

    <!-- Контент активной под-вкладки -->
    <div v-if="env" class="tab-body">
      <ManualForm v-if="tab === 'manual'" :env="env" :me="me" :auth="auth" :alerts="suggestAlerts" @created="onChange" />
      <ScheduleForm v-else-if="tab === 'schedule'" :env="env" :me="me" :auth="auth" :alerts="suggestAlerts" @created="onChange" />
      <RulesList v-else-if="tab === 'rules'" :env="env" :items="rules" :auth="auth" :alerts="suggestAlerts" @reload="onChange" />
      <AlertsList v-else-if="tab === 'alerts'" :env="env" :items="alerts" @reload="loadAlerts" />
      <HistoryList v-else :env="env" :items="history" @reload="loadHistory" />
    </div>
  </div>
</template>

<style scoped>
/* Капсулы окружений и под-вкладки — общая стилистика с модулем victoria
   (одинаковая форма, у каждого модуля свой акцентный цвет из theme.css). */
.envs { margin-bottom: 20px; }
.envs-label {
  display: block; font-size: 11px; color: var(--text-mute); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.08em;
}
.env-list { display: flex; flex-wrap: wrap; gap: 8px; }
.env {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  border-radius: 10px; padding: 8px 14px; cursor: pointer;
  color: var(--text-dim); font-family: var(--mono); font-size: 13px;
}
.env:hover { border-color: var(--border); color: var(--text); }
.env.active { border-color: var(--accent); color: var(--accent-bright); background: var(--accent-soft); }
.env-name { white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--track); flex: none; }
.dot.live { background: var(--accent-bright); }

.subtabs {
  display: flex; gap: 24px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 22px;
}
.subtab {
  background: transparent; border: none;
  color: var(--text-mute); font-size: 14px; letter-spacing: 0.01em;
  padding: 11px 2px; border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.subtab:hover { color: var(--text); }
.subtab.active { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.count {
  background: var(--chip); color: var(--text-dim);
  font-size: 11px; padding: 1px 7px; border-radius: 20px; margin-left: 4px;
}
</style>
