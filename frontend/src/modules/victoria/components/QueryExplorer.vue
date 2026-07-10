<script setup>
// Explore метрик «как в Prometheus»: одна или несколько панелей запроса,
// сложенных вертикально. Каждая панель (QueryPanel) независима — свой запрос,
// режим Graph/Table и диапазон времени. Кнопка «+ Добавить запрос» снизу
// добавляет новую панель (аналог «Add Panel» в вебе Prometheus).
//
// Панели живут по id из счётчика; смена окружения/тенанта пересоздаёт весь
// QueryExplorer (см. :key в VictoriaModule) — панели сбрасываются к одной.
import { ref } from 'vue'
import QueryPanel from './QueryPanel.vue'

const props = defineProps({
  env: { type: String, required: true },
  tenant: { type: String, default: null }, // мультитенантный VM; иначе null
})

let seq = 0
const panels = ref([++seq]) // список id-панелей; начинаем с одной

function addPanel() { panels.value.push(++seq) }
function removePanel(id) {
  // Последнюю панель не убираем — всегда хотя бы одно поле запроса.
  if (panels.value.length > 1) panels.value = panels.value.filter((p) => p !== id)
}
</script>

<template>
  <div class="qe-list">
    <QueryPanel
      v-for="(id, i) in panels"
      :key="id"
      :env="props.env"
      :tenant="props.tenant"
      :index="i + 1"
      :removable="panels.length > 1"
      @remove="removePanel(id)"
    />
    <button class="add-panel" @click="addPanel">+ Добавить запрос</button>
  </div>
</template>

<style scoped>
/* Панели складываются столбиком, между ними — разделитель-отступ. */
.qe-list { display: flex; flex-direction: column; gap: 26px; }

.add-panel {
  align-self: flex-start;
  background: var(--panel-2); border: 1px dashed var(--border);
  color: var(--text-dim); border-radius: 8px; padding: 9px 16px;
  font-family: var(--mono); font-size: 13px;
}
.add-panel:hover { border-color: var(--accent); color: var(--accent-bright); }
</style>
