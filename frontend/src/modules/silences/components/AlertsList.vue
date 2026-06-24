<script setup>
// Вкладка «Алерты»: определения алертов из Prometheus/vmalert (/api/v1/rules),
// помеченные состоянием от Alertmanager. state — от эвалуатора правил:
//   firing — горит, pending — зреет (ждёт for), inactive — спит.
// AM добавляет: заглушён ли silence / подавлен ли инхибитом / реально ли уведомляет.
// Раскрытие показывает expr, аннотации, статические лейблы и сработавшие серии.
// Только просмотр — отсюда ничего не меняем.
import { ref, computed, watch } from 'vue'

const props = defineProps({
  env: { type: String, required: true },
  items: { type: Array, required: true }, // алерты активного env (грузит родитель)
})
const emit = defineEmits(['reload'])

const STATES = [['all', 'all'], ['firing', 'firing'], ['pending', 'pending'], ['inactive', 'inactive']]
const stateFilter = ref('all')
// Фильтр по подавлению от Alertmanager: all / silenced (на чём сейчас активен silence).
const MUTES = [['all', 'all'], ['silenced', 'silenced']]
const muteFilter = ref('all')
const query = ref('')
const openKey = ref(null)

// Лейблы строкой — для поиска (alertname уже учитываем отдельно).
function labelPairs(a) {
  return Object.entries(a.labels || {})
    .filter(([k]) => k !== 'alertname')
    .map(([k, v]) => `${k}=${v}`)
}
// Лейблы серии строкой (для раскрытия) — без alertname.
function instLabels(inst) {
  return Object.entries(inst.labels || {})
    .filter(([k]) => k !== 'alertname')
    .map(([k, v]) => `${k}=${v}`)
    .join('  ')
}
// «for» правила приходит в секундах.
function fmtFor(s) {
  if (!s) return ''
  const m = Math.round(s / 60)
  return m >= 60 ? `${Math.round(m / 60)}ч` : `${m}м`
}
function fmtDt(s) {
  if (!s) return ''
  const d = new Date(s)
  if (isNaN(d)) return s
  const p = (n) => String(n).padStart(2, '0')
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`
}
function keyOf(a, i) {
  return a.name + i
}
// 1 серия / 2 серии / 5 серий
function seriesWord(n) {
  const o = n % 10, t = n % 100
  if (t >= 11 && t <= 14) return 'серий'
  if (o === 1) return 'серия'
  if (o >= 2 && o <= 4) return 'серии'
  return 'серий'
}
// Подпись под именем (как matcher-строка в Silences): человеческий summary, иначе
// severity/team. Чтобы строка не была пустой и сразу было понятно, про что алерт.
function subText(a) {
  if (a.annotations && a.annotations.summary) return a.annotations.summary
  const lp = labelPairs(a)
  return lp.length ? lp.join('  ·  ') : '—'
}

const filtered = computed(() =>
  props.items.filter((a) => {
    if (stateFilter.value !== 'all' && a.state !== stateFilter.value) return false
    if (muteFilter.value === 'silenced' && !a.silenced) return false
    if (query.value) {
      const hay = `${a.name} ${labelPairs(a).join(' ')}`.toLowerCase()
      if (!hay.includes(query.value.toLowerCase())) return false
    }
    return true
  }),
)

// Пагинация: показываем по PAGE_SIZE на странице, остальное листаем.
const PAGE_SIZE = 10
const page = ref(1)
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const paged = computed(() => filtered.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))
watch([query, stateFilter, muteFilter], () => { page.value = 1 })
watch(page, () => { openKey.value = null })

function toggle(key) {
  openKey.value = openKey.value === key ? null : key
}
</script>

<template>
  <div>
    <div class="row-head">
      <div>
        <h2 class="tab-title">Алерты</h2>
        <p class="tab-desc">Правила окружения <b>{{ env }}</b> из Prometheus/vmalert; состояние помечает Alertmanager — только просмотр.</p>
      </div>
      <button class="btn btn-sm" @click="emit('reload')">обновить</button>
    </div>

    <div class="filters">
      <div class="pills">
        <button v-for="[id, label] in STATES" :key="id" class="pill" :class="{ on: stateFilter === id }" @click="stateFilter = id">{{ label }}</button>
      </div>
      <div class="pills">
        <button v-for="[id, label] in MUTES" :key="id" class="pill" :class="{ on: muteFilter === id }" @click="muteFilter = id">{{ label }}</button>
      </div>
      <input class="input search" v-model="query" placeholder="search by name or labels…" />
    </div>

    <p v-if="!filtered.length" class="tab-desc">Алертов нет.</p>

    <div v-for="(a, i) in paged" :key="keyOf(a, i)" class="rule" :class="{ open: openKey === keyOf(a, i), muted: a.silenced || a.inhibited }">
      <div class="rule-row" @click="toggle(keyOf(a, i))">
        <span class="dot" :class="a.state"></span>
        <div class="rule-info">
          <div class="rule-head">
            <span class="nm">{{ a.name || '(без alertname)' }}</span>
            <span class="state" :class="a.state">{{ a.state || '—' }}</span>
            <!-- Подавление от AM: firing, но не уведомляет — наглядно иконкой mute. -->
            <span v-if="a.silenced" class="mark mute">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4V5z" /><line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" /></svg>
              silenced
            </span>
            <span v-else-if="a.inhibited" class="mark mute">
              <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6 9H2v6h4l5 4V5z" /><line x1="23" y1="9" x2="17" y2="15" /><line x1="17" y1="9" x2="23" y2="15" /></svg>
              inhibited
            </span>
            <span v-if="a.instances && a.instances.length" class="cnt" title="сработавших серий сейчас">{{ a.instances.length }} {{ seriesWord(a.instances.length) }}</span>
            <span v-if="a.for" class="for">for {{ fmtFor(a.for) }}</span>
          </div>
          <div class="sub" :title="subText(a)">{{ subText(a) }}</div>
        </div>
        <span class="chev">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6" /></svg>
        </span>
      </div>

      <!-- Раскрытие: весь состав алерта одним списком — expr, лейблы, аннотации;
           ниже сработавшие серии с их динамическими лейблами. -->
      <div v-if="openKey === keyOf(a, i)" class="detail">
        <div class="kv-grid">
          <span v-if="a.expr" class="k">expr</span>
          <span v-if="a.expr" class="v">{{ a.expr }}</span>
          <template v-for="(v, k) in a.labels" :key="'l-' + k">
            <span class="k">{{ k }}</span>
            <span class="v">{{ v }}</span>
          </template>
          <template v-for="(v, k) in a.annotations" :key="'a-' + k">
            <span class="k">{{ k }}</span>
            <span class="v">{{ v }}</span>
          </template>
        </div>

        <!-- Сработавшие серии (несут динамические лейблы: instance, pod…). -->
        <div v-if="a.instances && a.instances.length" class="insts">
          <div class="insts-head">сработавшие серии</div>
          <div v-for="(inst, j) in a.instances" :key="j" class="inst-row">
            <span class="inst-labels">{{ instLabels(inst) }}</span>
            <span v-if="inst.starts_at" class="inst-since">с {{ fmtDt(inst.starts_at) }}</span>
          </div>
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
.rule-row { display: flex; align-items: center; gap: 11px; cursor: pointer; user-select: none; min-height: 40px; }
.chev { display: flex; color: var(--text-mute); transition: transform 0.15s; }
.rule.open .chev { transform: rotate(180deg); }

.dot { width: 8px; height: 8px; border-radius: 50%; background: var(--track); flex: none; }
.dot.firing { background: var(--danger); }
.dot.pending { background: var(--info); }
.dot.inactive { background: var(--track); }

.rule-info { flex: 1; min-width: 0; }
.rule-head { display: flex; align-items: center; gap: 8px; margin-bottom: 2px; min-width: 0; }
.nm { font-weight: 600; font-size: 14px; flex: 0 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state, .mark, .for, .cnt { flex: none; }
.cnt { background: var(--chip); color: var(--text-dim); font-size: 11px; padding: 1px 7px; border-radius: 20px; }
.state { font-size: 10px; padding: 1px 6px; border-radius: 20px; background: var(--chip); color: var(--text-dim); }
.state.firing { background: var(--danger-soft, rgba(220,80,80,0.16)); color: var(--danger); }
.state.pending { background: var(--info-soft); color: var(--info); }
.state.inactive { background: var(--chip); color: var(--text-mute); }
.mark { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; padding: 1px 8px 1px 6px; border-radius: 20px; }
.mark.mute { background: var(--info-soft); color: var(--info); }
.for { font-family: var(--mono); font-size: 11px; color: var(--text-mute); margin-left: auto; }

/* Подавленный (silence/inhibit) алерт — приглушаем строку: горит, но не уведомляет. */
.rule.muted .dot { opacity: 0.3; }
.rule.muted .nm { color: var(--text-dim); }
.rule.muted .state.firing { opacity: 0.55; }

.sub { font-family: var(--mono); font-size: 12px; color: var(--text-mute); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.detail { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
.kv-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; font-family: var(--mono); font-size: 12px; }
.kv-grid .k { color: var(--text-mute); }
.kv-grid .v { color: var(--text); word-break: break-all; }
.insts { margin-top: 12px; }
.insts-head { font-size: 11px; color: var(--text-mute); margin-bottom: 6px; }
.inst-row { display: flex; gap: 12px; align-items: baseline; font-family: var(--mono); font-size: 12px; padding: 3px 0; border-top: 1px dashed var(--border-soft); }
.inst-labels { color: var(--text); word-break: break-all; flex: 1; }
.inst-since { color: var(--text-mute); flex: none; }
</style>
