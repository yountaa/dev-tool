<script setup>
// Корень модуля Victoria Metrics: сверху вкладки окружений (кластеров, из бэкенда),
// под ними под-вкладки Query / Targets / Rules / Cardinality — показываем только те,
// что доступны у выбранного кластера (Targets — если есть vmagent, Rules — vmalert).
import { ref, computed, watch, onMounted } from 'vue'
import Skeleton from '../../shared/Skeleton.vue'
import { victoriaApi } from './api.js'
import QueryExplorer from './components/QueryExplorer.vue'
import TargetsList from './components/TargetsList.vue'
import RulesAlerts from './components/RulesAlerts.vue'
import Cardinality from './components/Cardinality.vue'
import DiskUsage from './components/DiskUsage.vue'

const envs = ref([]) // [{ name, query, targets, rules, cardinality, tenants }]
const env = ref(null) // активное окружение (объект)
const tenant = ref(null) // выбранный тенант (для мультитенантного VM; иначе null)
const tab = ref(localStorage.getItem('vm.tab') || 'query') // активная под-вкладка (запоминаем)
const loading = ref(true) // грузим список окружений
const error = ref(null)

// Список под-вкладок для активного окружения (по флагам доступности компонентов).
// Названия под-вкладок — как в вебе Prometheus (Query / Targets / Rules / TSDB Status).
// Флаг null → под-вкладка есть всегда (не зависит от компонентов кластера): «Диск»
// сводит все кластеры в одну таблицу, поэтому показываем её при любом окружении.
const SUBTABS = [
  ['query', 'Query', 'query'],
  ['targets', 'Targets', 'targets'],
  ['rules', 'Rules', 'rules'],
  ['cardinality', 'TSDB Status', 'cardinality'],
  ['disk', 'Disk Usage', null],
]
const subtabs = computed(() =>
  env.value ? SUBTABS.filter(([, , flag]) => flag === null || env.value[flag]) : [],
)

onMounted(async () => {
  try {
    envs.value = await victoriaApi.environments()
    // Восстанавливаем выбранный кластер по имени (если ещё существует), иначе первый.
    const savedEnv = localStorage.getItem('vm.env')
    env.value = envs.value.find((e) => e.name === savedEnv) || envs.value[0] || null
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})

// При смене окружения: сбрасываем тенант на первый доступный (или null, если
// мультитенантности нет), откатываем под-вкладку, если она недоступна, и запоминаем.
watch(env, () => {
  tenant.value = env.value?.tenants?.[0] ?? null
  if (!subtabs.value.some(([id]) => id === tab.value)) {
    tab.value = subtabs.value[0]?.[0] ?? 'query'
  }
  if (env.value) localStorage.setItem('vm.env', env.value.name)
})

// Запоминаем выбранную под-вкладку между перезагрузками.
watch(tab, (v) => localStorage.setItem('vm.tab', v))
</script>

<template>
  <div class="vm">
    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <!-- Пока грузится список окружений — плашка на месте вкладок. -->
    <Skeleton v-if="loading" :lines="1" :height="36" />

    <!-- Вкладки окружений (кластеров) + выбор тенанта, если кластер мультитенантный.
         Подпись «Окружение» над вкладками — как в модуле silences. -->
    <div v-else-if="envs.length" class="envs">
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
          <span class="env-name">{{ e.name }}</span>
        </button>

        <!-- Тенант влияет только на чтение из vmselect → показываем селектор лишь на
             Query и TSDB Status; на Targets/Rules он не участвует. -->
        <label v-if="env && env.tenants && env.tenants.length && (tab === 'query' || tab === 'cardinality')" class="tenant">
          <span>tenant</span>
          <select class="input" v-model="tenant">
            <option v-for="t in env.tenants" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
      </div>
    </div>
    <div v-else-if="!error" class="empty">Нет настроенных кластеров (задай vm_&lt;env&gt; в окружении).</div>

    <!-- Под-вкладки выбранного кластера -->
    <div v-if="env" class="subtabs">
      <button
        v-for="[id, label] in subtabs"
        :key="id"
        class="subtab"
        :class="{ active: tab === id }"
        @click="tab = id"
      >{{ label }}</button>
    </div>

    <!-- Контент активной под-вкладки. :key — чтобы при смене окружения компонент
         пересоздавался и перечитывал данные нового кластера. KeepAlive держит
         неактивные вкладки живыми: вернулся на Query — твой запрос и график на
         месте, targets/rules не перечитываются заново при каждом переключении. -->
    <div v-if="env" class="body">
      <KeepAlive :max="10">
        <QueryExplorer v-if="tab === 'query'" :key="'q-' + env.name + '-' + (tenant || '')" :env="env.name" :tenant="tenant" />
        <TargetsList v-else-if="tab === 'targets'" :key="'t-' + env.name" :env="env.name" />
        <RulesAlerts v-else-if="tab === 'rules'" :key="'r-' + env.name" :env="env.name" />
        <Cardinality v-else-if="tab === 'cardinality'" :key="'c-' + env.name + '-' + (tenant || '')" :env="env.name" :tenant="tenant" />
        <DiskUsage v-else-if="tab === 'disk'" key="disk" />
      </KeepAlive>
    </div>
  </div>
</template>

<style scoped>
.vm { display: flex; flex-direction: column; }

/* Вкладки окружений (кластеров) — те же капсулы и подпись, что в модуле silences. */
.envs { margin-bottom: 20px; }
.envs-label { display: block; font-size: 12px; color: var(--text-mute); margin-bottom: 8px; }
.env-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.env {
  display: flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  color: var(--text-dim); border-radius: 8px; padding: 7px 13px;
  font-family: var(--mono); font-size: 13px;
}
.env:hover { border-color: var(--border); color: var(--text); }
.env.active { color: var(--accent-bright); border-color: var(--accent); background: var(--accent-soft); }
.env-name { white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--track); flex: none; }
.dot.live { background: var(--accent-bright); }

/* Выбор тенанта — прижат вправо от вкладок кластеров */
.tenant { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; }
.tenant > span { font-family: var(--mono); font-size: 12px; color: var(--text-mute); }
.tenant .input { width: auto; padding: 6px 10px; font-size: 13px; }

/* Под-вкладки — подчёркиванием, как в модуле silences. */
.subtabs { display: flex; gap: 22px; border-bottom: 1px solid var(--border-soft); margin-bottom: 22px; }
.subtab {
  background: transparent; border: none; color: var(--text-dim);
  padding: 10px 2px; font-size: 14px;
  border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.subtab:hover { color: var(--text); }
.subtab.active { color: var(--text); border-bottom-color: var(--accent); }

.empty { color: var(--text-mute); font-size: 13px; padding: 20px 0; }
</style>
