<script setup>
// Конструктор алертов: список слева, форма справа, предпросмотр по кнопке.
//
// Конфиги и preview — через n8n webhook (POST /webhook/alerts).
// История и кэш engine — FastAPI + Postgres (/alerts/*).
import { computed, onActivated, onMounted, onUnmounted, ref, watch, watchEffect } from 'vue'
import { urlParams, setUrlParams } from '../../shared/urlstate.js'
import { api, historySnapshot, newerEngine } from './api.js'
import { TEMPLATES } from './templates.js'
import AlertForm from './components/AlertForm.vue'
import PreviewModal from './components/PreviewModal.vue'
import AlertsHistoryList from './components/AlertsHistoryList.vue'
import AlertsRunsList from './components/AlertsRunsList.vue'
import AlertsTree from './components/AlertsTree.vue'
import FolderDialog from './components/FolderDialog.vue'
import { normalizeConfig } from './lib/normalize.js'
import { validateConfig, slugId } from './lib/validate.js'
import { sanitizeDelivery } from './lib/emailList.js'
import { forEngine } from './lib/forEngine.js'
import { normalizeWindow } from './lib/window.js'
import { normalizeSchedule } from './lib/scheduleConfig.js'
import { asFieldOptions, buildFieldHints, mergeFieldHints } from './lib/fieldSuggest.js'

defineOptions({ name: 'AlertsModule' })

defineProps({
  me: { type: String, default: '' },
  auth: { type: Boolean, default: false },
})

const TABS = [
  ['alerts', 'Alerts'],
  ['runs', 'Runs'],
  ['history', 'History'],
]

const initial = urlParams()
const tab = ref(TABS.some(([id]) => id === initial.get('tab')) ? initial.get('tab') : 'alerts')

const alerts = ref([])
const engine = ref(null)
const indexPatterns = ref([
  'omni-prod-*',
  'n8n_stack-*',
  'logs-*',
  'filebeat-*',
  'kubernetes-*',
])
const history = ref([])
const folders = ref([])
const folderDialog = ref(null) // { mode, folder?, busy? }
const selected = ref(null)
const selectedRowId = ref(null)
const isNew = ref(false)
const cfg = ref(null)
const name = ref('')
/** Снимок при открытии алерта — для History.before (cfg уже меняется по мере правок). */
const historyBaseline = ref(null)
const fieldHints = ref(buildFieldHints())
/** Поля текущего индекса из ELK (_field_caps через n8n action: fields). */
const indexFields = ref([])
const fieldsStatus = ref({ state: 'idle', index: '', count: 0, error: '' })
const busy = ref(false)
const msg = ref(null)
let msgTimer = null
const previewOpen = ref(false)
const previewResult = ref(null)
const formRef = ref(null)
const draftRev = ref(0)
const configLoadId = ref(0)
const listLoading = ref(false)
let booted = false
let fieldsTimer = null
let fieldsReq = 0
const fieldsCache = new Map() // index → string[]

/** Подсказки для Fields / Add filter: приоритет — поля выбранного индекса. */
const fields = computed(() => {
  if (indexFields.value.length) return asFieldOptions(indexFields.value)
  // Пока ELK не ответил — хотя бы поля текущего конфига + seed.
  return asFieldOptions(fieldHints.value)
})

function effectiveCfg() {
  const raw = formRef.value?.getEffectiveCfg?.() || cfg.value
  return prepareCfg(raw)
}

const errors = computed(() => {
  draftRev.value
  if (!cfg.value) return []
  return validateConfig(effectiveCfg()).errors
})
const canSave = computed(() => cfg.value && !errors.value.length && name.value.trim())

const engineAlive = computed(() => {
  const iso = engine.value?.lastRunAt
  if (!iso) return null
  return (Date.now() - new Date(iso).getTime()) / 60000 <= 5
})

watchEffect(() => {
  setUrlParams({ tab: tab.value })
})

function flash(text, kind = 'ok') {
  msg.value = { text, kind }
  if (msgTimer) clearTimeout(msgTimer)
  msgTimer = setTimeout(() => { msg.value = null }, 4000)
}

function rememberFields(names) {
  // Ручной ввод — в локальный кэш; indexFields не трогаем (они из ELK).
  fieldHints.value = mergeFieldHints(fieldHints.value, names)
  if (indexFields.value.length && names?.length) {
    const set = new Set(indexFields.value)
    for (const n of names) if (n) set.add(String(n).trim())
    indexFields.value = [...set].sort((a, b) => a.localeCompare(b))
  }
}

function mergeIndexPatterns(list) {
  if (!list?.length) return
  const set = new Set(indexPatterns.value)
  for (const p of list) if (p) set.add(String(p))
  indexPatterns.value = [...set].sort((a, b) => a.localeCompare(b))
}

async function loadFieldsForIndex(index, { force = false } = {}) {
  const idx = String(index || '').trim()
  if (!idx) {
    indexFields.value = []
    fieldsStatus.value = { state: 'idle', index: '', count: 0, error: '' }
    return
  }
  if (!force && fieldsCache.has(idx)) {
    indexFields.value = fieldsCache.get(idx)
    fieldsStatus.value = { state: 'ok', index: idx, count: indexFields.value.length, error: '' }
    return
  }
  const req = ++fieldsReq
  fieldsStatus.value = { state: 'loading', index: idx, count: 0, error: '' }
  try {
    const list = await api.fields(idx)
    if (req !== fieldsReq) return
    fieldsCache.set(idx, list)
    indexFields.value = list
    fieldsStatus.value = {
      state: list.length ? 'ok' : 'empty',
      index: idx,
      count: list.length,
      error: '',
    }
  } catch (e) {
    if (req !== fieldsReq) return
    indexFields.value = []
    fieldsStatus.value = {
      state: 'error',
      index: idx,
      count: 0,
      error: e.message || 'не удалось загрузить поля',
    }
  }
}

function scheduleFieldsLoad(index) {
  if (fieldsTimer) clearTimeout(fieldsTimer)
  fieldsTimer = setTimeout(() => loadFieldsForIndex(index), 350)
}

watch(
  () => cfg.value?.source?.index,
  (idx) => { scheduleFieldsLoad(idx) },
)

watch(configLoadId, () => {
  // Смена алерта — сразу подтянуть поля его индекса.
  scheduleFieldsLoad(cfg.value?.source?.index)
})


async function hydrateFromCache() {
  const cached = await api.getCache()
  if (cached.alerts?.length) {
    alerts.value = cached.alerts
    fieldHints.value = buildFieldHints(alerts.value)
  }
  if (cached.engine?.lastRunAt) {
    engine.value = newerEngine(engine.value, cached.engine)
  }
  if (cached.indexPatterns?.length) {
    indexPatterns.value = cached.indexPatterns
  } else if (cached.alerts?.length) {
    indexPatterns.value = await api.indexPatterns(cached.alerts)
  }
}

async function applyListData(data) {
  alerts.value = data.alerts || []
  const fromN8n = data.engine
  engine.value = newerEngine(engine.value, fromN8n)
  fieldHints.value = buildFieldHints(alerts.value)
  const patterns = await api.indexPatterns(alerts.value)
  indexPatterns.value = patterns
  // Зеркало в Postgres — следующий F5 без пустого списка.
  await api.putCache({
    alerts: alerts.value,
    engine: engine.value,
    indexPatterns: patterns,
  })
}

async function load({ quiet = false } = {}) {
  if (!quiet && !alerts.value.length) listLoading.value = true
  try {
    const data = await api.list()
    await applyListData(data)
  } catch (e) {
    if (!quiet && !alerts.value.length) {
      flash('Не удалось загрузить список: ' + e.message, 'err')
    }
  } finally {
    if (!quiet) listLoading.value = false
  }
}

async function loadHistory() {
  try {
    history.value = await api.history()
  } catch {
    history.value = []
  }
}

async function loadFolders() {
  try {
    folders.value = await api.listFolders()
  } catch {
    folders.value = []
  }
}

async function refreshQuiet() {
  try {
    const data = await api.list()
    await applyListData(data)
  } catch { /* тихий poll */ }
}

const REFRESH_MS = 30000
let timer = null

onMounted(async () => {
  // 1) Мгновенно из Postgres  2) фоном обновить из n8n
  await hydrateFromCache()
  listLoading.value = !alerts.value.length
  await Promise.all([load({ quiet: !!alerts.value.length }), loadHistory(), loadFolders()])
  listLoading.value = false
  booted = true
  timer = setInterval(() => {
    refreshQuiet()
    if (tab.value === 'history') loadHistory()
  }, REFRESH_MS)
})

// KeepAlive: при возврате на вкладку — тихий refresh, без полного remount-флэша.
onActivated(() => {
  if (booted) refreshQuiet()
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (msgTimer) clearTimeout(msgTimer)
})

watch(tab, (v) => {
  if (v === 'history') loadHistory()
  if (v === 'runs') refreshQuiet()
})

function prepareCfg(raw) {
  return sanitizeDelivery(
    normalizeSchedule(normalizeWindow(normalizeConfig(JSON.parse(JSON.stringify(raw || {})))),
  ))
}

function setBaseline(cfgObj, listName, alertKey) {
  historyBaseline.value = historySnapshot(cfgObj, {
    name: listName || '',
    alertKey: alertKey || '',
  })
}

function pick(a) {
  selected.value = a.alertKey
  selectedRowId.value = a.rowId || null
  isNew.value = false
  cfg.value = prepareCfg(a.config)
  name.value = a.name || a.alertKey
  setBaseline(cfg.value, name.value, a.alertKey)
  configLoadId.value++
}
function create() {
  selected.value = null
  selectedRowId.value = null
  isNew.value = true
  cfg.value = sanitizeDelivery(TEMPLATES.batch())
  name.value = ''
  historyBaseline.value = null
  configLoadId.value++
  tab.value = 'alerts'
}
function cancel() {
  selected.value = null
  selectedRowId.value = null
  isNew.value = false
  cfg.value = null
  name.value = ''
  historyBaseline.value = null
}

function payload(fromCfg = cfg.value) {
  const conf = sanitizeDelivery(normalizeConfig(JSON.parse(JSON.stringify(fromCfg))))
  conf.enabled = !!cfg.value.enabled
  if (!conf.id) conf.id = slugId(conf.notify?.alertId) || selected.value || undefined
  const engineCfg = forEngine(conf)
  return {
    alertKey: isNew.value ? (engineCfg.id || null) : selected.value,
    rowId: isNew.value ? null : selectedRowId.value,
    name: name.value.trim(),
    enabled: !!cfg.value.enabled,
    intervalMinutes: engineCfg.schedule?.intervalMinutes || 30,
    config: engineCfg,
  }
}

async function save() {
  if (!canSave.value) {
    flash(errors.value.length ? errors.value.join('; ') : 'Заполните обязательные поля', 'err')
    return
  }
  if (!isNew.value && !selected.value) {
    flash('Не выбран алерт для сохранения', 'err')
    return
  }
  busy.value = true
  try {
    // before — снимок при открытии, не текущий cfg (он уже отредактирован).
    const beforeSnap = !isNew.value ? historyBaseline.value : null
    const savedCfg = formRef.value?.flushDrafts?.() || cfg.value
    cfg.value = savedCfg
    const body = payload(savedCfg)
    const afterSnap = historySnapshot(body.config, {
      name: body.name,
      alertKey: selected.value || body.alertKey || '',
    })
    if (isNew.value) {
      const created = await api.create(body)
      selected.value = created.alertKey || created.config?.id || selected.value
      selectedRowId.value = created.rowId || created.id || selectedRowId.value
      isNew.value = false
      flash('Алерт создан')
      await api.recordHistory({
        action: 'создал',
        name: body.name,
        alertKey: selected.value || body.alertKey || '',
        before: null,
        after: afterSnap,
      })
    } else {
      await api.update(selected.value, body)
      flash('Сохранено')
      if (JSON.stringify(beforeSnap) !== JSON.stringify(afterSnap)) {
        await api.recordHistory({
          action: 'изменил',
          name: body.name,
          alertKey: selected.value,
          before: beforeSnap,
          after: afterSnap,
        })
      }
    }
    await load({ quiet: true })
    await loadHistory()
    if (selected.value) {
      const fresh = alerts.value.find((a) => a.alertKey === selected.value)
      if (fresh) pick(fresh)
      else setBaseline(body.config, body.name, selected.value)
    }
  } catch (e) {
    flash(e.message, 'err')
  } finally {
    busy.value = false
  }
}

async function remove() {
  if (!selected.value || isNew.value) return
  if (!confirm(`Удалить алерт ${selected.value}?`)) return
  try {
    const beforeSnap = historySnapshot(cfg.value, { name: name.value, alertKey: selected.value })
    const key = selected.value
    await api.remove(key, { rowId: selectedRowId.value, id: selectedRowId.value })
    await api.recordHistory({
      action: 'удалил',
      name: beforeSnap?.name || key,
      alertKey: key,
      before: beforeSnap,
      after: null,
    })
    cancel()
    await load({ quiet: true })
    await loadFolders()
    await loadHistory()
    flash('Удалён')
  } catch (e) {
    flash(e.message, 'err')
  }
}

async function preview() {
  busy.value = true
  previewResult.value = null
  try {
    const savedCfg = formRef.value?.flushDrafts?.() || cfg.value
    cfg.value = savedCfg
    const conf = sanitizeDelivery(normalizeConfig(JSON.parse(JSON.stringify(savedCfg))))
    conf.enabled = !!cfg.value.enabled
    previewResult.value = await api.preview(forEngine(conf))
    previewOpen.value = true
  } catch (e) {
    previewResult.value = { error: e.message, emails: [] }
    previewOpen.value = true
  } finally {
    busy.value = false
  }
}

function ago(iso) {
  if (!iso) return 'ещё не запускался'
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (min < 1) return 'только что'
  if (min < 60) return `${min} мин назад`
  return `${Math.round(min / 60)} ч назад`
}

function openFolderDialog(mode, folder = null) {
  folderDialog.value = { mode, folder, busy: false }
}

function closeFolderDialog() {
  if (folderDialog.value?.busy) return
  folderDialog.value = null
}

async function onFolderDialogConfirm({ mode, name }) {
  if (!folderDialog.value) return
  folderDialog.value = { ...folderDialog.value, busy: true }
  try {
    if (mode === 'create') {
      await api.createFolder(name)
      await loadFolders()
      flash('Папка создана')
    } else if (mode === 'rename') {
      const f = folderDialog.value.folder
      if (!f || name === f.name) {
        folderDialog.value = null
        return
      }
      await api.renameFolder(f.id, name)
      await loadFolders()
      flash('Папка переименована')
    } else if (mode === 'delete') {
      const f = folderDialog.value.folder
      if (!f) return
      await api.deleteFolder(f.id)
      await loadFolders()
      flash('Папка удалена')
    }
    // busy=true блокирует closeFolderDialog — закрываем напрямую после успеха.
    folderDialog.value = null
  } catch (e) {
    folderDialog.value = { ...folderDialog.value, busy: false }
    flash(e.message, 'err')
  }
}

async function moveAlertTo({ alertKey, folderId }) {
  if (!alertKey) return
  try {
    await api.moveAlert(alertKey, folderId)
    await loadFolders()
    flash(folderId == null ? 'Убран из папки' : 'Перемещён в папку')
  } catch (e) {
    flash(e.message, 'err')
  }
}
</script>

<template>
  <div class="module">
    <div class="subtabs">
      <button
        v-for="[id, label] in TABS"
        :key="id"
        class="subtab"
        :class="{ active: tab === id }"
        @click="tab = id"
      >
        {{ label }}
        <span v-if="id === 'alerts' && alerts.length" class="count">{{ alerts.length }}</span>
        <span v-if="id === 'runs' && alerts.length" class="count">{{ alerts.length }}</span>
        <span v-if="id === 'history' && history.length" class="count">{{ history.length }}</span>
      </button>
    </div>

    <div v-if="tab === 'runs'" class="runs-pane">
      <AlertsRunsList
        :alerts="alerts"
        :engine="engine"
        :engine-alive="engineAlive"
        @reload="refreshQuiet"
      />
    </div>

    <div v-else-if="tab === 'history'" class="history-pane">
      <AlertsHistoryList :items="history" @reload="loadHistory" />
    </div>

    <div v-else class="wrap">
      <aside class="rail">
        <div class="rail-head">
          <button class="btn btn-primary create" @click="create">+ Создать алерт</button>
          <div class="engine" :class="{ bad: engineAlive === false, none: engineAlive === null, loading: listLoading && !engine?.lastRunAt }">
            <span class="pip"></span>
            <span v-if="engineAlive === true">backend-workflow работает</span>
            <span v-else-if="engineAlive === false">backend-workflow молчит с {{ ago(engine.lastRunAt) }}</span>
            <span v-else-if="listLoading">подключение к backend-workflow…</span>
            <span v-else>backend-workflow ни разу не отчитывался</span>
          </div>
        </div>

        <AlertsTree
          :alerts="alerts"
          :folders="folders"
          :selected="selected"
          :loading="listLoading"
          @pick="pick"
          @create-folder="openFolderDialog('create')"
          @rename-folder="(f) => openFolderDialog('rename', f)"
          @delete-folder="(f) => openFolderDialog('delete', f)"
          @move-alert="moveAlertTo"
        />
      </aside>

      <section class="main">
        <div v-if="!cfg" class="placeholder">
          <h2>Конструктор алертов</h2>
          <p>Выберите алерт слева или создайте новый.</p>
        </div>

        <template v-else>
          <div class="toolbar">
            <label class="enable">
              <input type="checkbox" :checked="cfg.enabled" @change="cfg.enabled = $event.target.checked" />
              <span>{{ cfg.enabled ? 'Включён' : 'Выключен' }}</span>
            </label>
            <span class="key" v-if="!isNew">{{ selected }}</span>
            <span class="spacer"></span>
            <button class="btn btn-ghost" :disabled="busy" @click="preview">Предпросмотр</button>
            <button v-if="!isNew" class="btn btn-ghost danger" @click="remove">Удалить</button>
            <button class="btn btn-ghost" @click="cancel">Отмена</button>
            <button
              class="btn btn-primary"
              :disabled="!canSave || busy"
              :title="!canSave && errors.length ? errors.join('; ') : ''"
              @click="save"
            >Сохранить</button>
          </div>

          <p v-if="!canSave && cfg && errors.length" class="msg msg-err toolbar-err">
            Чтобы сохранить: {{ errors.join('; ') }}
          </p>

          <AlertForm
            ref="formRef"
            v-model:cfg="cfg"
            v-model:name="name"
            :config-load-id="configLoadId"
            :fields="fields"
            :fields-status="fieldsStatus"
            :index-patterns="indexPatterns"
            :errors="errors"
            @remember="rememberFields"
            @index-patterns="mergeIndexPatterns"
            @draft-change="draftRev++"
            @reload-fields="loadFieldsForIndex(cfg?.source?.index, { force: true })"
          />
        </template>
      </section>
    </div>

    <PreviewModal :open="previewOpen" :result="previewResult" @close="previewOpen = false" />

    <FolderDialog
      :open="!!folderDialog"
      :mode="folderDialog?.mode || 'create'"
      :initial-name="folderDialog?.folder?.name || ''"
      :folder-name="folderDialog?.folder?.name || ''"
      :member-count="(folderDialog?.folder?.alertKeys || []).length"
      :busy="!!folderDialog?.busy"
      @close="closeFolderDialog"
      @confirm="onFolderDialogConfirm"
    />

    <Teleport to="body">
      <div
        v-if="msg"
        class="alerts-toast"
        :class="msg.kind === 'err' ? 'err' : 'ok'"
        role="status"
      >{{ msg.text }}</div>
    </Teleport>
  </div>
</template>

<style scoped>
.module { display: flex; flex-direction: column; gap: 0; }
.subtabs {
  display: flex; gap: 24px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 18px;
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
.history-pane, .runs-pane { min-width: 0; }
.wrap { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 18px; align-items: start; }
.rail { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.rail-head { display: flex; flex-direction: column; gap: 8px; }
.create { width: 100%; }
.engine { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text-mute); }
.engine .pip { width: 8px; height: 8px; border-radius: 50%; background: #50c878; flex: none; }
.engine.bad .pip, .engine.none .pip { background: var(--danger); }
.engine.loading .pip { background: var(--text-mute); animation: pulse 1s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.35; } }
.main { min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.toolbar {
  position: sticky; top: 0; z-index: 5; display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
}
.enable { display: flex; align-items: center; gap: 7px; font-size: 13px; }
.enable input { width: auto; }
.key { font-family: var(--mono); font-size: 12px; color: var(--text-mute); }
.spacer { flex: 1; }
.danger { color: var(--danger); }
.toolbar-err { margin: 0; position: sticky; top: 56px; z-index: 4; }
.placeholder { color: var(--text-dim); padding: 40px 8px; }
.placeholder h2 { margin: 0 0 8px; }
.alerts-toast {
  position: fixed;
  top: 20px;
  right: 20px;
  z-index: 10000;
  max-width: min(420px, calc(100vw - 32px));
  padding: 12px 16px;
  border-radius: 10px;
  font-size: 13.5px;
  line-height: 1.4;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.18);
  pointer-events: none;
}
.alerts-toast.ok {
  background: var(--panel);
  color: var(--accent-bright);
  border: 1px solid var(--accent);
}
.alerts-toast.err {
  background: var(--panel);
  color: var(--danger);
  border: 1px solid rgba(248, 81, 73, 0.55);
}
@media (max-width: 1000px) { .wrap { grid-template-columns: 1fr; } .rail { position: static; } }
</style>
