<script setup>
// Корень модуля silences: сверху вкладки окружений (динамические, из бэкенда),
// под ними под-вкладки Разовый / По расписанию / Существующие, дальше контент.
import { ref, watch, onMounted } from 'vue'
import { silencesApi } from './api.js'
import OnetimeForm from './components/OnetimeForm.vue'
import ScheduleForm from './components/ScheduleForm.vue'
import RulesList from './components/RulesList.vue'

const envs = ref([]) // [{ name }]
const env = ref(null) // активное окружение
const tab = ref('onetime') // активная под-вкладка
const rules = ref([]) // локальные правила активного env (для списка и счётчика)
const error = ref(null)

const TABS = [
  ['onetime', 'Разовый'],
  ['schedule', 'По расписанию'],
  ['rules', 'Рабочие правила'],
]

onMounted(async () => {
  try {
    envs.value = await silencesApi.environments()
    env.value = envs.value[0]?.name ?? null
  } catch (e) {
    error.value = e.message
  }
})

// При смене окружения перечитываем правила.
watch(env, loadRules, { immediate: false })

async function loadRules() {
  if (!env.value) return
  try {
    rules.value = await silencesApi.rules(env.value)
  } catch (e) {
    rules.value = []
    error.value = e.message
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
      </button>
    </div>

    <!-- Контент активной под-вкладки -->
    <div v-if="env" class="tab-body">
      <OnetimeForm v-if="tab === 'onetime'" :env="env" @created="loadRules" />
      <ScheduleForm v-else-if="tab === 'schedule'" :env="env" @created="loadRules" />
      <RulesList v-else :env="env" :items="rules" @reload="loadRules" />
    </div>
  </div>
</template>

<style scoped>
.envs { margin-bottom: 20px; }
.envs-label { display: block; font-size: 12px; color: var(--text-mute); margin-bottom: 8px; }
.env-list { display: flex; flex-wrap: wrap; gap: 8px; }
.env {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  border-radius: 8px; padding: 7px 13px; cursor: pointer;
  color: var(--text-dim); font-family: var(--mono); font-size: 13px;
}
.env:hover { border-color: var(--border); color: var(--text); }
.env.active { border-color: var(--accent); color: var(--accent-bright); background: var(--accent-soft); }
.env-name { white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--track); flex: none; }
.dot.live { background: var(--accent-bright); }

.subtabs {
  display: flex; gap: 22px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 22px;
}
.subtab {
  background: transparent; border: none;
  color: var(--text-dim); font-size: 14px;
  padding: 10px 2px; border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}
.subtab:hover { color: var(--text); }
.subtab.active { color: var(--text); border-bottom-color: var(--accent); }
.count {
  background: var(--chip); color: var(--text-dim);
  font-size: 11px; padding: 1px 7px; border-radius: 20px; margin-left: 4px;
}
</style>
