<script setup>
// Форма алерта. Разделы идут в порядке вопроса, на который отвечают:
// что это → откуда брать → что считать событием → когда алертить → кому и как.
import { computed, ref, watch } from 'vue'
import ConditionsEditor from './ConditionsEditor.vue'
import FieldsEditor from './FieldsEditor.vue'
import ColumnsEditor from './ColumnsEditor.vue'
import ChipsPicker from './ChipsPicker.vue'
import InfoHint from './InfoHint.vue'
import ScheduleEditor from './ScheduleEditor.vue'
import DurationInput from './DurationInput.vue'
import AutocompleteInput from '../../silences/components/AutocompleteInput.vue'
import IndexPatternSelect from './IndexPatternSelect.vue'
import { TYPE_INFO, TEMPLATES } from '../templates.js'
import { emailListToDraft, parseDraftEmails, GARBAGE } from '../lib/emailList.js'

const props = defineProps({
  cfg: { type: Object, required: true },
  name: { type: String, default: '' },
  fields: { type: Array, default: () => [] },
  /** Статус загрузки полей индекса из ELK. */
  fieldsStatus: { type: Object, default: () => ({ state: 'idle', index: '', count: 0, error: '' }) },
  errors: { type: Array, default: () => [] },
  indexPatterns: { type: Array, default: () => [] },
  /** Инкремент при pick/create — перечитать черновики даже для того же alertKey. */
  configLoadId: { type: Number, default: 0 },
})
const emit = defineEmits(['update:cfg', 'update:name', 'remember', 'draft-change', 'index-patterns', 'reload-fields'])

const fieldsHint = computed(() => {
  const s = props.fieldsStatus || {}
  if (s.state === 'loading') return `загрузка полей индекса ${s.index}…`
  if (s.state === 'ok') return `поля индекса ${s.index}: ${s.count}`
  if (s.state === 'empty') return `в индексе ${s.index} поля не найдены — вводите вручную`
  if (s.state === 'error') return `поля индекса недоступны (${s.error}) — общий список`
  if (!props.cfg?.source?.index) return 'укажите индекс — подтянем поля из ELK'
  return 'из них строятся группировка и колонки'
})

const info = computed(() => TYPE_INFO[props.cfg.type] || TYPE_INFO.batch)

// Показать подстановку буквально: писать {{title}} внутри шаблона нельзя —
// парсер Vue принимает это за вложенную интерполяцию.
const ph = (word) => '{' + '{' + word + '}' + '}'
const mapped = computed(() => Object.keys(props.cfg.source?.map || {}))
const fieldOptions = computed(() =>
  (props.fields || []).map((f) => (typeof f === 'string' ? f : f.name)).filter(Boolean),
)

const layoutOptions = [
  { value: 'table', label: 'Таблица — широкая HTML-таблица (дайджесты OMNI)' },
  { value: 'text', label: 'Текст — простой моноширинный отчёт' },
  { value: 'card', label: 'Карточка — как silence/zabbix (цветной заголовок)' },
]

function set(path, value) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  const keys = path.split('.')
  let node = next
  for (let i = 0; i < keys.length - 1; i++) {
    if (typeof node[keys[i]] !== 'object' || node[keys[i]] === null) node[keys[i]] = {}
    node = node[keys[i]]
  }
  node[keys[keys.length - 1]] = value
  emit('update:cfg', next)
}

function onFieldsChange({ map, sourceFields }) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  if (!next.source || typeof next.source !== 'object') next.source = {}
  next.source.map = map
  next.source.sourceFields = sourceFields
  // Убрать из groupBy/parseJson/columns то, чего больше нет в map
  const aliases = Object.keys(map || {})
  if (Array.isArray(next.rule?.groupBy)) {
    next.rule.groupBy = next.rule.groupBy.filter((k) => aliases.includes(k))
  }
  if (Array.isArray(next.rule?.parseJson)) {
    next.rule.parseJson = next.rule.parseJson.filter((k) => aliases.includes(k))
  }
  emit('update:cfg', next)
}

function onFilterChange({ filter, kql, query }) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  if (!next.source || typeof next.source !== 'object') next.source = {}
  next.source.filter = filter
  next.source.kql = kql
  next.source.query = query
  emit('update:cfg', next)
}

/** Схлопывание: только имена из map; при выборе — колонка в письме, если её ещё нет. */
function setGroupBy(keys) {
  const next = JSON.parse(JSON.stringify(props.cfg))
  if (!next.rule) next.rule = {}
  next.rule.groupBy = keys
  const cols = Array.isArray(next.rule.columns) ? next.rule.columns.slice() : []
  const have = new Set(cols.map((c) => c.key))
  for (const k of keys) {
    if (!have.has(k)) {
      cols.push({ key: k, label: k, align: 'left', format: 'plain' })
      have.add(k)
    }
  }
  if (!have.has('count')) {
    cols.push({ key: 'count', label: 'Сколько раз', align: 'center', format: 'plain' })
  }
  next.rule.columns = cols
  emit('update:cfg', next)
}

const mapEntries = computed(() =>
  Object.entries(props.cfg.source?.map || {}).map(([alias, path]) => ({
    alias,
    path: String(path || ''),
    label: alias === path || !path ? alias : `${alias} ← ${path}`,
  })),
)
const groupByMissing = computed(() => {
  const aliases = new Set(Object.keys(props.cfg.source?.map || {}))
  return (props.cfg.rule?.groupBy || []).filter((k) => !aliases.has(k))
})

const subjectHint = computed(() =>
  `Подстановки: ${ph('title')}, ${ph('alertId')}, ${ph('total')}${props.cfg.type === 'silence' ? `, ${ph('host')}` : ''}.`,
)

const SILENCE_THRESHOLD_PRESETS = [
  { label: '15 мин', value: 15 },
  { label: '30 мин', value: 30 },
  { label: '1 ч', value: 60 },
  { label: '2 ч', value: 120 },
  { label: '6 ч', value: 360 },
  { label: '12 ч', value: 720 },
  { label: '1 дн', value: 1440 },
]

const SILENCE_REPEAT_PRESETS = [
  { label: 'один раз', value: 0 },
  { label: '30 мин', value: 30 },
  { label: '1 ч', value: 60 },
  { label: '6 ч', value: 360 },
  { label: '12 ч', value: 720 },
  { label: '1 дн', value: 1440 },
]


// Смена типа: общие разделы сохраняем, правило берём из заготовки нового типа.
function changeType(type) {
  const tpl = TEMPLATES[type]()
  const next = JSON.parse(JSON.stringify(props.cfg))
  next.type = type
  next.rule = tpl.rule
  next.notify = { ...tpl.notify, ...next.notify, subjectTemplate: tpl.notify.subjectTemplate }
  if (type === 'silence') {
    delete next.window
    next.presentation = { ...(next.presentation || {}), layout: 'card' }
  } else if (!next.window) next.window = tpl.window
  if (!next.schedule) next.schedule = {}
  next.schedule.intervalMinutes = tpl.schedule.intervalMinutes
  if (type !== 'silence' && !next.presentation?.layout) {
    next.presentation = { ...(tpl.presentation || {}), ...(next.presentation || {}) }
  }
  emit('update:cfg', next)
}

const recipientsDraft = ref('')
const ignoreDraft = ref('')

function syncRecipientsFromCfg() {
  recipientsDraft.value = emailListToDraft(props.cfg?.delivery?.email?.to)
}

function syncIgnoreFromCfg() {
  const patterns = props.cfg?.rule?.ignorePatterns
  ignoreDraft.value = Array.isArray(patterns) ? patterns.join('\n') : ''
}

watch(() => props.configLoadId, () => {
  syncRecipientsFromCfg()
  syncIgnoreFromCfg()
}, { immediate: true })

function onRecipientsInput(event) {
  recipientsDraft.value = event.target.value
  if (recipientsDraft.value.includes(GARBAGE)) {
    recipientsDraft.value = parseDraftEmails(recipientsDraft.value).join('\n')
  }
  emit('draft-change')
}

function onIgnoreInput(event) {
  ignoreDraft.value = event.target.value
  emit('draft-change')
}

/** Сбросить черновики textarea в cfg. Возвращает cfg — чтобы save не читал устаревший v-model. */
function applyDrafts(base) {
  const next = JSON.parse(JSON.stringify(base || props.cfg))
  if (!next.delivery) next.delivery = {}
  if (!next.delivery.email) next.delivery.email = {}
  next.delivery.email.to = parseDraftEmails(recipientsDraft.value)
  if (!next.rule) next.rule = {}
  next.rule.ignorePatterns = ignoreDraft.value
    .split(/\n/).map((s) => s.trim()).filter(Boolean)
  return next
}

function flushDrafts() {
  const next = applyDrafts(props.cfg)
  emit('update:cfg', next)
  return next
}

/** Для validate/canSave: cfg + черновики textarea, без emit. */
function getEffectiveCfg() {
  return applyDrafts(props.cfg)
}

defineExpose({ flushDrafts, getEffectiveCfg })
</script>

<template>
  <div class="form">
    <p v-if="errors.length" class="msg msg-err">
      <b>Не сохранится:</b> {{ errors.join('; ') }}
    </p>

    <!-- 1. Что это -->
    <section class="card">
      <div class="card-head"><h3 class="card-title">Что это за алерт</h3></div>
      <div class="body">
        <div class="grid-2">
          <label class="field">
            <span class="req">Название</span>
            <input :value="name" class="input" placeholder="Ошибки Delivery-Atomic"
                   @input="$emit('update:name', $event.target.value)" />
          </label>
          <label class="field">
            <span>Тип правила<InfoHint :text="info.hint" /></span>
            <select :value="cfg.type" class="input sans" @change="changeType($event.target.value)">
              <option value="batch">Сводка за период</option>
              <option value="silence">Пропажа логов</option>
              <option value="any">Каждое событие</option>
            </select>
          </label>
        </div>
        <div class="grid-2">
          <label class="field">
            <span class="req">Код алерта<InfoHint text="Попадает в тему письма и становится id конфига (например DLVATOMC.01006 → dlvatomc-01006)." /></span>
            <input :value="cfg.notify?.alertId" class="input" placeholder="DLVATOMC.01006"
                   @input="set('notify.alertId', $event.target.value)" />
          </label>
          <label class="field">
            <span class="req">Заголовок письма</span>
            <input :value="cfg.notify?.title" class="input" placeholder="Ошибки в сервисе Delivery-Atomic"
                   @input="set('notify.title', $event.target.value)" />
          </label>
        </div>
      </div>
    </section>

    <!-- 2. Откуда брать -->
    <section class="card">
      <div class="card-head">
        <h3 class="card-title">Откуда брать логи</h3>
        <span class="card-hint">индекс ELK и фильтр</span>
      </div>
      <div class="body">
        <div class="grid-2">
          <label class="field">
            <span class="req">Индекс<InfoHint text="Начни вводить имя — подтянутся индексы из ELK. Можно с маской: omni-prod-*." /></span>
            <IndexPatternSelect
              :model-value="cfg.source?.index || ''"
              :patterns="indexPatterns"
              @update:model-value="set('source.index', $event)"
              @patterns="$emit('index-patterns', $event)"
            />
          </label>
          <label class="field">
            <span>Поле времени</span>
            <AutocompleteInput
              :model-value="cfg.source?.timeField || ''"
              :options="fieldOptions"
              placeholder="@timestamp"
              @update:model-value="set('source.timeField', $event); $emit('remember', [$event])"
            />
          </label>
        </div>

        <div class="field filter-field">
          <span>Какие записи считаем нашими</span>
          <ConditionsEditor
            :model-value="cfg.source?.query || { bool: { must: [] } }"
            :filter="cfg.source?.filter ?? null"
            :kql="cfg.source?.kql || ''"
            :fields="fields"
            @change="onFilterChange"
            @remember="$emit('remember', $event)"
          />
        </div>
      </div>
    </section>

    <!-- 3. Какие поля -->
    <section class="card">
      <div class="card-head">
        <h3 class="card-title">Какие поля брать</h3>
        <span class="card-hint fields-meta">
          {{ fieldsHint }}
          <button
            v-if="cfg.source?.index"
            type="button"
            class="btn btn-ghost btn-sm reload-fields"
            :disabled="fieldsStatus.state === 'loading'"
            title="Обновить поля индекса из ELK"
            @click="$emit('reload-fields')"
          >↻</button>
        </span>
      </div>
      <div class="body">
        <FieldsEditor
          :map="cfg.source?.map || {}"
          :time-field="cfg.source?.timeField || '@timestamp'"
          :fields="fields"
          @change="onFieldsChange"
          @remember="$emit('remember', $event)"
        />
      </div>
    </section>

    <!-- 4. Правило -->
    <section class="card">
      <div class="card-head">
        <h3 class="card-title">Когда алертить</h3>
        <span class="card-hint">{{ info.title.toLowerCase() }}</span>
      </div>
      <div class="body">
        <!-- batch -->
        <template v-if="cfg.type === 'batch'">
          <div class="field">
            <span>Схлопывать события, у которых совпадают</span>
            <ChipsPicker
              :model-value="cfg.rule?.groupBy || []"
              :options="mapped"
              :labels="Object.fromEntries(mapEntries.map((e) => [e.alias, e.label]))"
              empty="сначала добавьте поля выше в «Какие поля брать»"
              @update:model-value="setGroupBy"
            />
            <p v-if="groupByMissing.length" class="warn">
              Нет в источнике: {{ groupByMissing.join(', ') }} — добавьте в «Какие поля брать»
              или снимите с группировки.
            </p>
          </div>
          <div class="field" v-if="mapped.length">
            <span>Поля, где внутри лежит JSON</span>
            <ChipsPicker :model-value="cfg.rule?.parseJson || []" :options="mapped"
                         @update:model-value="set('rule.parseJson', $event)" />
          </div>
          <div class="grid-2">
            <label class="field">
              <span>Слать, если событий не меньше</span>
              <input type="number" min="1" :value="cfg.rule?.minCount" class="input"
                     @input="set('rule.minCount', Number($event.target.value))" />
            </label>
            <label class="field">
              <span>Если событий нет</span>
              <select :value="cfg.rule?.emptyPolicy" class="input sans"
                      @change="set('rule.emptyPolicy', $event.target.value)">
                <option value="skip">не слать письмо</option>
                <option value="send_empty">слать пустой отчёт</option>
              </select>
            </label>
          </div>
        </template>

        <!-- silence -->
        <template v-else-if="cfg.type === 'silence'">
          <div class="grid-2">
            <label class="field">
              <span class="req">Считать отдельно по полю<InfoHint text="Обычно узел или хост. Значения подхватятся из данных сами." /></span>
              <input :value="cfg.rule?.hostField" class="input" list="alerts-field-list" placeholder="host.name"
                     @input="set('rule.hostField', $event.target.value)" />
            </label>
            <label class="field">
              <span>Поле с текстом лога</span>
              <input :value="cfg.rule?.messageField" class="input" list="alerts-field-list" placeholder="message"
                     @input="set('rule.messageField', $event.target.value)" />
            </label>
          </div>
          <div class="grid-2">
            <label class="field">
              <span class="req">Тишина дольше<InfoHint text="Через сколько молчания считать, что что-то не так." /></span>
              <DurationInput
                :model-value="cfg.rule?.thresholdMinutes ?? 3"
                storage="minutes"
                :presets="SILENCE_THRESHOLD_PRESETS"
                @update:model-value="set('rule.thresholdMinutes', $event)"
              />
            </label>
            <label class="field">
              <span>Повторять не чаще<InfoHint text="0 — написать один раз и молчать до восстановления." /></span>
              <DurationInput
                :model-value="cfg.rule?.repeatMinutes ?? 0"
                storage="minutes"
                :presets="SILENCE_REPEAT_PRESETS"
                allow-zero
                :min="0"
                @update:model-value="set('rule.repeatMinutes', $event)"
              />
            </label>
          </div>
          <label class="field">
            <span>Не алертить, если последний осмысленный лог содержит<InfoHint text="Штатная тишина. Регистр и кавычки не важны: node is working in &quot;standby&quot; mode подойдёт." /></span>
            <input :value="cfg.rule?.standbyMarker" class="input" placeholder="standby mode"
                   @input="set('rule.standbyMarker', $event.target.value)" />
          </label>
          <label class="field">
            <span>Строки-шум, которые не считаются осмысленными<InfoHint text="По одной в строке. Такие логи не мешают увидеть предыдущий осмысленный — например, slow query поверх standby не снимет подавление." /></span>
            <textarea
              :key="`ignore-${configLoadId}`"
              :value="ignoreDraft"
              class="input"
              rows="3"
              placeholder="slow query"
              @input="onIgnoreInput"
            ></textarea>
          </label>
          <label class="field switch-row">
            <input type="checkbox" :checked="cfg.rule?.notifyRecovery !== false"
                   @change="set('rule.notifyRecovery', $event.target.checked)" />
            <span>Писать, когда логи вернулись</span>
          </label>
        </template>

        <!-- any -->
        <template v-else>
          <div class="grid-2">
            <label class="field">
              <span>Как слать</span>
              <select :value="cfg.rule?.mode" class="input sans" @change="set('rule.mode', $event.target.value)">
                <option value="capped">по письму на событие, но не больше лимита</option>
                <option value="perEvent">по письму на каждое событие</option>
                <option value="digestPerRun">одно письмо за прогон со списком</option>
              </select>
            </label>
            <label class="field">
              <span>Не больше писем за прогон<InfoHint text="Остаток придёт одной сводкой." /></span>
              <input type="number" min="1" :value="cfg.rule?.maxEmailsPerRun" class="input"
                     @input="set('rule.maxEmailsPerRun', Number($event.target.value))" />
            </label>
          </div>
          <div class="field">
            <span>Считать одинаковыми события, где совпадают<InfoHint text="Схлопывает повторы внутри одного прогона." /></span>
            <ChipsPicker :model-value="cfg.rule?.dedupeBy || []" :options="mapped"
                         empty="сначала добавьте поля выше"
                         @update:model-value="set('rule.dedupeBy', $event)" />
          </div>
          <label class="field">
            <span>Аварийный порог<InfoHint text="Если за прогон совпало больше — рассылки не будет вовсе, придёт одна сводка. Защита от слишком широкого фильтра." /></span>
            <input type="number" min="1" :value="cfg.rule?.panicThreshold" class="input"
                   @input="set('rule.panicThreshold', Number($event.target.value))" />
          </label>
        </template>
      </div>
    </section>

    <!-- 5. Расписание -->
    <section class="card">
      <div class="card-head"><h3 class="card-title">Расписание</h3></div>
      <div class="body">
        <ScheduleEditor :cfg="cfg" @update:cfg="$emit('update:cfg', $event)" />
      </div>
    </section>

    <!-- 6. Кому -->
    <section class="card">
      <div class="card-head"><h3 class="card-title">Кому отправлять</h3></div>
      <div class="body">
        <div class="grid-2">
          <label class="field">
            <span>От кого</span>
            <input :value="cfg.delivery?.email?.from" class="input"
                   @input="set('delivery.email.from', $event.target.value)" />
          </label>
          <label class="field">
            <span>Тема письма<InfoHint :text="subjectHint" /></span>
            <input :value="cfg.notify?.subjectTemplate" class="input"
                   @input="set('notify.subjectTemplate', $event.target.value)" />
          </label>
        </div>
        <label class="field">
          <span class="req">Получатели<InfoHint text="По одному адресу в строке (Enter), или через запятую/точку с запятой." /></span>
          <textarea
            :key="`recipients-${configLoadId}`"
            :value="recipientsDraft"
            class="input"
            rows="4"
            placeholder="ivan@hofftech.ru&#10;petrov@hofftech.ru"
            @input="onRecipientsInput"
          ></textarea>
        </label>
        <label class="field">
          <span>Ссылка на инструкцию<InfoHint text="Появится в письме отдельной строкой — куда идти чинить." /></span>
          <input :value="cfg.notify?.helpUrl" class="input" placeholder="https://confluence…"
                 @input="set('notify.helpUrl', $event.target.value)" />
        </label>
      </div>
    </section>

    <!-- 7. Вид письма -->
    <section class="card presentation-card">
      <div class="card-head">
        <h3 class="card-title">Как выглядит письмо</h3>
      </div>
      <div class="body presentation-body">
        <label class="field format-field">
          <span>Формат<InfoHint v-if="cfg.type === 'silence'" text="Для тишины всегда карточка в стиле zabbix-server (цветной статус + сниппет лога)." /></span>
          <select
            :value="cfg.type === 'silence' ? 'card' : (cfg.presentation?.layout || 'table')"
            class="input sans"
            :disabled="cfg.type === 'silence'"
            @change="set('presentation.layout', $event.target.value)"
          >
            <option v-for="o in layoutOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
          </select>
        </label>
        <template v-if="cfg.type !== 'silence'">
          <div class="columns-block">
            <div class="columns-label">
              <span>Колонки в отчёте</span>
              <InfoHint text="count — счётчик одинаковых событий, его считает движок. Формат «JSON с переносами» разбивает длинные списки товаров по строкам." />
            </div>
            <ColumnsEditor :model-value="cfg.rule?.columns || []" :available="mapped"
                           @update:model-value="set('rule.columns', $event)" />
          </div>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.form { display: flex; flex-direction: column; gap: 14px; }
.body { padding: 14px 16px; display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 5px; }
.field > span { font-size: 12.5px; color: var(--text-dim); }
.field small { color: var(--text-mute); font-size: 11.5px; line-height: 1.45; }
.explain {
  margin: 0; padding: 9px 12px; border-radius: 8px;
  background: var(--panel-2); color: var(--text-dim); font-size: 12.5px; line-height: 1.55;
}
.explain .dim { color: var(--text-mute); }
.with-btn { display: flex; gap: 8px; }
.with-btn .input { flex: 1; }
.switch-row { flex-direction: row; align-items: center; gap: 8px; }
.switch-row input { width: auto; }
select.sans, .sans { font-family: var(--sans); }
code { font-family: var(--mono); font-size: 11px; }
.warn { margin: 4px 0 0; color: var(--danger); font-size: 12px; }
.presentation-body { gap: 18px; }
.format-field { max-width: 560px; }
.reload-fields {
  padding: 0 4px; font-size: 13px; line-height: 1;
  color: var(--text-mute); margin-left: 6px; vertical-align: middle;
}
.reload-fields:hover { color: var(--accent-bright); }
.fields-meta { display: inline-flex; align-items: center; max-width: 55%; }
.fields-meta { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.columns-block {
  display: flex; flex-direction: column; gap: 10px;
  padding-top: 4px; border-top: 1px solid var(--border-soft);
}
.columns-label {
  display: flex; align-items: center; gap: 4px;
  font-size: 12.5px; color: var(--text-dim);
}
</style>
