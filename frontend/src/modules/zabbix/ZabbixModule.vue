<script setup>
// Корень модуля Zabbix Audit: сверху вкладки окружений (инстансов Zabbix), под
// ними журнал аудита выбранного инстанса.
import { ref, watch, onMounted, onUnmounted } from 'vue'
import Skeleton from '../../shared/Skeleton.vue'
import { urlParams, setUrlParams } from '../../shared/urlstate.js'
import { zabbixApi } from './api.js'
import AuditPanel from './components/AuditPanel.vue'

const envs = ref([])
const env = ref(null)
const dicts = ref({ actions: [], types: [] })
const loading = ref(true)
const error = ref(null)

const initial = urlParams()

onMounted(async () => {
  try {
    const [e, d] = await Promise.all([zabbixApi.environments(), zabbixApi.dictionaries()])
    envs.value = e
    dicts.value = d
    const wanted = initial.get('env') || localStorage.getItem('zbx.env')
    env.value = envs.value.find((x) => x.name === wanted) || envs.value[0] || null
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

watch(env, () => {
  if (!env.value) return
  localStorage.setItem('zbx.env', env.value.name)
  setUrlParams({ env: env.value.name })
})

onUnmounted(() => setUrlParams({ env: null }))
</script>

<template>
  <div class="zbx">
    <div v-if="error" class="msg msg-err">{{ error }}</div>
    <Skeleton v-if="loading" :lines="1" :height="36" />

    <div v-else-if="envs.length > 1" class="envs">
      <span class="envs-label">Окружение</span>
      <div class="env-row">
        <button
          v-for="e in envs"
          :key="e.name"
          class="env"
          :class="{ active: env && e.name === env.name }"
          @click="env = e"
        >
          <span class="dot" :class="{ live: env && e.name === env.name }"></span>
          <span>{{ e.name }}</span>
        </button>
      </div>
    </div>
    <div v-else-if="!envs.length && !error" class="empty">
      Нет настроенных инстансов Zabbix (задай zbxaudit_&lt;env&gt; в окружении).
    </div>

    <!-- :key — смена инстанса пересоздаёт панель с чистыми результатами. -->
    <AuditPanel v-if="env" :key="env.name" :env="env.name" :api="env.api" :dicts="dicts" />
  </div>
</template>

<style scoped>
.zbx { display: flex; flex-direction: column; }
.envs { margin-bottom: 20px; }
.envs-label {
  display: block; font-size: 11px; color: var(--text-mute); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.08em;
}
.env-row { display: flex; flex-wrap: wrap; gap: 8px; }
.env {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  color: var(--text-dim); border-radius: 10px; padding: 8px 14px;
  font-family: var(--mono); font-size: 13px;
}
.env:hover { border-color: var(--border); color: var(--text); }
.env.active { color: var(--accent-bright); border-color: var(--accent); background: var(--accent-soft); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--track); }
.dot.live { background: var(--accent-bright); }
.empty { color: var(--text-mute); font-size: 13px; padding: 20px 0; }
</style>
