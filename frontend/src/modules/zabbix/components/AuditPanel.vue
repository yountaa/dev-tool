<script setup>
// Журнал аудита Zabbix: фильтры → таблица «когда / кто / что сделал / с чем» →
// по клику раскрывается, какие поля поменялись (было → стало).
//
// Бэкенд отдаёт до limit записей за раз; рисуем их порциями по PAGE, «Показать
// ещё» дорисовывает из уже полученного ответа, без нового запроса к ES.
import { ref, computed, onMounted, onUnmounted } from 'vue'
import Skeleton from '../../../shared/Skeleton.vue'
import { urlParams, setUrlParams } from '../../../shared/urlstate.js'
import { zabbixApi } from '../api.js'
import DiffValue from './DiffValue.vue'

const props = defineProps({
  env: { type: String, required: true },
  api: { type: Boolean, default: false }, // подключён ли Zabbix API (имена по ID)
  dicts: { type: Object, required: true },
})

const PAGE = 100
const PERIODS = [
  [3600, '1 час'],
  [86400, '24 часа'],
  [7 * 86400, '7 дней'],
  [30 * 86400, '30 дней'],
  ['custom', 'свой период'],
]

// --- Фильтры (начальные значения — из ссылки, если её прислали) ----------------
const u = urlParams()
const period = ref(u.get('p') === 'custom' ? 'custom' : Number(u.get('p')) || 86400)
const from = ref(u.get('from') || '')
const to = ref(u.get('to') || '')
const user = ref(u.get('user') || '')
const actions = ref((u.get('act') || '').split(',').filter(Boolean).map(Number))
const rtype = ref(u.get('typ') || '')
const name = ref(u.get('name') || '')
const ip = ref(u.get('ip') || '')
const q = ref(u.get('q') || '')
// Узкие фильтры ставятся кликом из записи («вся операция», «история объекта»).
const rid = ref(u.get('rid') || '')
const recordset = ref(u.get('rs') || '')

const result = ref(null) // { total, limit, from, to, records }
const shown = ref(PAGE)
const open = ref({}) // id записи → раскрыта
const loading = ref(false)
const error = ref(null)
let ctrl = null

const records = computed(() => result.value?.records || [])
const visible = computed(() => records.value.slice(0, shown.value))

// Одно действие в Zabbix (привязка шаблона, массовое изменение) пишет десятки
// записей с одним recordset подряд — они забивали таблицу и прятали остальные
// действия. Длинную серию сворачиваем: видны первые SET_HEAD записей и строка
// «ещё N записей этой операции», которая раскрывает остальные.
const SET_HEAD = 2
const SET_MIN = 5 // серия короче — показываем целиком, сворачивать нечего
const openSets = ref({})
const rows = computed(() => {
  const out = []
  const list = visible.value
  for (let i = 0; i < list.length;) {
    let j = i + 1
    const rs = list[i].recordset
    while (rs && j < list.length && list[j].recordset === rs) j++
    const run = list.slice(i, j)
    if (run.length >= SET_MIN && !openSets.value[rs]) {
      for (const r of run.slice(0, SET_HEAD)) out.push({ r })
      out.push({ more: rs, count: run.length - SET_HEAD, key: 'more-' + rs + '-' + i })
    } else {
      for (const r of run) out.push({ r })
    }
    i = j
  }
  return out
})

function filters() {
  const f = {
    actions: actions.value, types: rtype.value ? [rtype.value] : [],
    user: user.value.trim(), ip: ip.value.trim(), name: name.value.trim(), q: q.value.trim(),
    rid: rid.value, recordset: recordset.value,
  }
  if (period.value === 'custom') {
    f.from = from.value
    f.to = to.value
  } else {
    f.last = period.value
  }
  return f
}

function syncUrl() {
  setUrlParams({
    p: period.value, from: period.value === 'custom' ? from.value : null,
    to: period.value === 'custom' ? to.value : null,
    user: user.value.trim(), act: actions.value.join(','), typ: rtype.value,
    name: name.value.trim(), ip: ip.value.trim(), q: q.value.trim(),
    rid: rid.value, rs: recordset.value,
  })
}

async function search() {
  if (period.value === 'custom' && !from.value) {
    error.value = 'Укажи начало периода'
    return
  }
  ctrl?.abort()
  ctrl = new AbortController()
  loading.value = true
  error.value = null
  syncUrl()
  try {
    result.value = await zabbixApi.audit(props.env, filters(), ctrl.signal)
    shown.value = PAGE
    open.value = {}
    openSets.value = {}
  } catch (e) {
    if (e.name !== 'AbortError') error.value = e.message
  } finally {
    loading.value = false
  }
}

function reset() {
  period.value = 86400
  from.value = to.value = ''
  user.value = name.value = ip.value = q.value = rtype.value = ''
  actions.value = []
  rid.value = recordset.value = ''
  search()
}

function toggleAction(id) {
  const s = new Set(actions.value)
  s.has(id) ? s.delete(id) : s.add(id)
  actions.value = [...s].sort((a, b) => a - b)
}

// Переходы из записи: сузить выдачу до операции / объекта / пользователя.
function byRecordset(r) { recordset.value = r.recordset; rid.value = ''; search() }
function byObject(r) { rid.value = r.rid; rtype.value = String(r.rtype ?? ''); recordset.value = ''; search() }
function byUser(name) { user.value = name; search() }
function clearNarrow(which) { if (which === 'rid') rid.value = ''; else recordset.value = ''; search() }

onMounted(search)
onUnmounted(() => {
  ctrl?.abort()
  setUrlParams({ p: null, from: null, to: null, user: null, act: null, typ: null, name: null, ip: null, q: null, rid: null, rs: null })
})

// --- Сводка по выдаче: кто сколько действий сделал (кликабельно) ---------------
const topUsers = computed(() => {
  const m = new Map()
  for (const r of records.value) m.set(r.user, (m.get(r.user) || 0) + 1)
  return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8)
})

// --- Отрисовка значений --------------------------------------------------------
function val(v) {
  if (v === null || v === undefined) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
// «выключен (1)»: подпись — для человека, код — чтобы сверить с API/базой.
// «выключено (1)»: слово — чтобы понять без справочника, код — чтобы сверить
// с API или базой.
function shownVal(c, side) {
  const raw = val(c[side])
  const label = c[side + '_label']
  return label && label !== raw ? `${label} (${raw})` : raw
}
// 1 запись, 2 записи, 5 записей
function plural(n, one, few, many) {
  const d = n % 10
  const dd = n % 100
  if (d === 1 && dd !== 11) return one
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return few
  return many
}

function summary(r) {
  if (!r.changes.length) return ''
  const labels = [...new Set(r.changes.map((c) => c.group_title || c.label))]
  const head = labels.slice(0, 3).join(', ')
  return labels.length > 3 ? `${head} и ещё ${labels.length - 3}` : head
}

// Изменения записи → блоки для таблицы: отдельные поля и группы элементов
// списков (макрос, тег, интерфейс…). Бэкенд уже поставил поля группы подряд.
function blocks(r) {
  const out = []
  for (const c of r.changes) {
    if (!c.group) { out.push({ c }); continue }
    const last = out[out.length - 1]
    // redundant — поле уже видно в заголовке группы (ID элемента, имя макроса)
    const show = !c.header_only && !c.redundant
    if (last && last.group === c.group) {
      if (show) last.items.push(c)
    } else {
      out.push({ group: c.group, title: c.group_title, op: c.group_op, items: show ? [c] : [] })
    }
  }
  return out
}
// Вид операции группы для цвета: добавлено — зелёным, удалено — красным.
function opKind(op) {
  if (op === 'добавлено' || op === 'привязано') return 'add'
  if (op === 'удалено' || op === 'отвязано') return 'del'
  return ''
}

// --- CSV текущей выдачи (для разбора инцидента в таблице / письма) -------------
function csvCell(s) {
  const t = String(s ?? '')
  return /[";\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t
}
function exportCsv() {
  const rows = [['Время', 'Пользователь', 'IP', 'Ресурс', 'Название ресурса', 'ID ресурса', 'Узел сети', 'Действие', 'Поле', 'Операция', 'Было', 'Стало', 'ID набора записей']]
  for (const r of records.value) {
    const base = [r.time, r.user, r.ip, r.rtype_label, r.rname, r.rid, r.hosts.join(', '), r.action_label]
    if (!r.changes.length) rows.push([...base, '', '', '', '', r.recordset])
    for (const c of r.changes) rows.push([...base, c.field, c.op_label, shownVal(c, 'old'), shownVal(c, 'new'), r.recordset])
  }
  // BOM — чтобы Excel открыл UTF-8 с кириллицей, а не кракозябры.
  const blob = new Blob(['﻿' + rows.map((r) => r.map(csvCell).join(';')).join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `zabbix-audit-${props.env}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div>
    <!-- Фильтры. Enter в любом поле — поиск. -->
    <div class="card filters" @keydown.enter.prevent="search">
      <div class="row">
        <div class="periods">
          <button
            v-for="[v, label] in PERIODS"
            :key="v"
            class="chip"
            :class="{ active: period === v }"
            @click="period = v"
          >{{ label }}</button>
        </div>
        <template v-if="period === 'custom'">
          <input class="input dt" type="datetime-local" v-model="from" />
          <span class="dash">—</span>
          <input class="input dt" type="datetime-local" v-model="to" title="пусто — по текущий момент" />
        </template>
      </div>

      <div class="row grid">
        <label class="field"><span>Пользователь</span>
          <input class="input" v-model="user" placeholder="логин целиком" /></label>
        <label class="field"><span>Ресурс</span>
          <select class="input" v-model="rtype">
            <option value="">все</option>
            <option v-for="t in dicts.types" :key="t.id" :value="String(t.id)">{{ t.label }}</option>
          </select></label>
        <label class="field"><span>Название ресурса</span>
          <input class="input" v-model="name" placeholder="часть имени узла сети, триггера…"
            :title="api ? 'по имени узла сети находятся и изменения его триггеров, элементов данных, графиков и шаблонов' : ''" /></label>
        <label class="field"><span>IP</span>
          <input class="input" v-model="ip" placeholder="часть адреса" /></label>
        <label class="field wide"><span>Поиск</span>
          <input class="input" v-model="q" placeholder="по названию ресурса, пользователю, IP и подробностям" /></label>
      </div>

      <div class="row">
        <div class="acts">
          <button
            v-for="a in dicts.actions"
            :key="a.id"
            class="chip act"
            :class="[a.kind, { active: actions.includes(a.id) }]"
            @click="toggleAction(a.id)"
          >{{ a.label }}</button>
        </div>
        <div class="buttons">
          <button class="btn btn-ghost" @click="reset">Сбросить</button>
          <button class="btn btn-primary" :disabled="loading" @click="search">{{ loading ? 'Загрузка…' : 'Применить' }}</button>
        </div>
      </div>

      <div v-if="rid || recordset" class="row narrow">
        <span v-if="rid" class="tag">ID ресурса {{ rid }} <button @click="clearNarrow('rid')">×</button></span>
        <span v-if="recordset" class="tag">ID набора записей {{ recordset }} <button @click="clearNarrow('rs')">×</button></span>
      </div>
    </div>

    <div v-if="error" class="msg msg-err">{{ error }}</div>

    <!-- Сводка -->
    <div v-if="result" class="summary">
      <span class="count">
        Найдено <b>{{ result.total }}</b>
        <template v-if="result.total > records.length"> · загружены последние {{ records.length }} — сузь фильтр, чтобы увидеть остальные</template>
      </span>
      <span class="users">
        <button v-for="[n, c] in topUsers" :key="n" class="chip sm" title="показать только действия этого пользователя" @click="byUser(n)">{{ n || '—' }} <i>{{ c }}</i></button>
      </span>
      <span v-if="!api" class="hint" title="задай zbxapi_<env> и zbxapitoken_<env>">без Zabbix API — узлы сети и имена по ID не подтягиваются</span>
      <button class="btn btn-sm" :disabled="!records.length" @click="exportCsv">CSV</button>
    </div>

    <Skeleton v-if="loading && !result" :lines="6" :height="40" />
    <div v-else-if="result && !records.length" class="empty">Нет записей за выбранный период.</div>

    <!-- Таблица -->
    <div v-if="records.length" class="tbl-scroll" :class="{ dim: loading }">
      <table class="tbl">
        <thead>
          <tr><th>Время</th><th>Пользователь</th><th>IP</th><th>Ресурс</th><th>Действие</th><th>Подробности</th></tr>
        </thead>
        <tbody>
          <template v-for="row in rows" :key="row.more ? row.key : row.r.id">
            <!-- Свёрнутый хвост одной операции -->
            <tr v-if="row.more" class="more-set">
              <td colspan="6">
                <button class="btn btn-sm btn-ghost" @click="openSets = { ...openSets, [row.more]: true }">
                  ▾ ещё {{ row.count }} {{ plural(row.count, 'запись', 'записи', 'записей') }} этой же операции
                </button>
              </td>
            </tr>
            <template v-else>
            <template v-for="r in [row.r]" :key="r.id">
            <tr class="rec" :class="{ open: open[r.id] }" @click="open = { ...open, [r.id]: !open[r.id] }">
              <td class="mono nowrap">{{ r.time }}</td>
              <td class="mono">{{ r.user || '—' }}</td>
              <td class="mono sub nowrap">{{ r.ip }}</td>
              <td class="obj">
                <div class="sub">{{ r.rtype_label }}<span v-if="r.rid" class="mono"> · ID {{ r.rid }}</span></div>
                <div class="name">
                  <a v-if="r.ui_url" :href="r.ui_url" target="_blank" rel="noopener" title="открыть в Zabbix" @click.stop>{{ r.rname || '—' }} ↗</a>
                  <template v-else>{{ r.rname || '—' }}</template>
                </div>
                <div v-if="r.hosts.length" class="sub">узел сети: <span class="mono">{{ r.hosts.join(', ') }}</span></div>
              </td>
              <td><span class="badge" :class="r.action_kind">{{ r.action_label }}</span></td>
              <td class="sum">
                <span v-if="r.changes.length">{{ summary(r) }}</span>
                <span v-else class="sub">—</span>
              </td>
            </tr>
            <tr v-if="open[r.id]" class="details">
              <td colspan="6">
                <div class="links">
                  <a v-if="r.ui_url" class="btn btn-sm btn-primary" :href="r.ui_url" target="_blank" rel="noopener">Открыть в Zabbix ↗</a>
                  <button v-if="r.recordset" class="btn btn-sm" @click="byRecordset(r)" title="все записи, сохранённые одним действием">Набор записей</button>
                  <button v-if="r.rid" class="btn btn-sm" @click="byObject(r)">История ресурса</button>
                  <button class="btn btn-sm" @click="byUser(r.user)">Действия пользователя {{ r.user }}</button>
                </div>
                <table v-if="r.changes.length" class="diff">
                  <thead><tr><th>Поле</th><th>Изменение</th></tr></thead>
                  <tbody>
                    <template v-for="(b, i) in blocks(r)" :key="i">
                      <!-- Группа: заголовок элемента списка («Макрос {$DB_HOST} — добавлено») и его поля -->
                      <tr v-if="b.group" class="ghead">
                        <td colspan="2">
                          <span class="gtitle">{{ b.title }}</span>
                          <span v-if="b.op" class="gop" :class="opKind(b.op)">{{ b.op }}</span>
                          <span class="sub mono">{{ b.group }}</span>
                        </td>
                      </tr>
                      <tr v-for="(c, j) in (b.group ? b.items : [b.c])" :key="i + '-' + j" :class="{ gitem: b.group }">
                        <td>
                          <div :class="{ mono: c.label === c.field }">{{ c.label }}</div>
                          <div v-if="c.label !== c.field" class="sub mono">{{ c.field }}</div>
                        </td>
                        <td class="change">
                          <DiffValue :op="c.op" :op-label="b.group && c.op === 'add' && b.op ? '' : c.op_label" :old-val="shownVal(c, 'old')" :new-val="shownVal(c, 'new')" />
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
                <div v-else class="sub">Подробностей нет — Zabbix не записывает их для этого действия.</div>
              </td>
            </tr>
            </template>
            </template>
          </template>
        </tbody>
      </table>
      <button v-if="shown < records.length" class="btn more" @click="shown += PAGE">
        Показать ещё ({{ records.length - shown }})
      </button>
    </div>
  </div>
</template>

<style scoped>
.filters { padding: 16px 18px; margin-bottom: 16px; display: flex; flex-direction: column; gap: 12px; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.grid .wide { grid-column: 1 / -1; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field > span { font-size: 11px; color: var(--text-mute); text-transform: uppercase; letter-spacing: 0.06em; }
.dt { width: auto; }
.dash { color: var(--text-mute); }
.periods, .acts { display: flex; flex-wrap: wrap; gap: 6px; }
.buttons { margin-left: auto; display: flex; gap: 8px; }

.chip {
  background: var(--panel-2); border: 1px solid var(--border-soft); color: var(--text-dim);
  border-radius: 20px; padding: 5px 12px; font-size: 12px;
}
.chip:hover { border-color: var(--border); color: var(--text); }
.chip.active { color: var(--accent-bright); border-color: var(--accent); background: var(--accent-soft); }
.chip.sm { padding: 3px 9px; font-family: var(--mono); }
.chip i { font-style: normal; color: var(--text-mute); margin-left: 4px; }

.narrow .tag {
  font-family: var(--mono); font-size: 12px; background: var(--accent-soft); color: var(--accent-bright);
  border-radius: 6px; padding: 3px 4px 3px 9px; display: inline-flex; align-items: center; gap: 4px;
}
.tag button { background: transparent; border: none; color: inherit; font-size: 14px; padding: 0 4px; }

.summary { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-bottom: 12px; font-size: 13px; }
.count { color: var(--text-dim); }
.users { display: flex; flex-wrap: wrap; gap: 6px; }
.summary .btn { margin-left: auto; }
.hint { font-size: 12px; color: var(--text-mute); }

.tbl-scroll { overflow-x: auto; transition: opacity 0.15s; }
.dim { opacity: 0.55; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; }
.tbl > thead th {
  text-align: left; color: var(--text-mute); font-weight: 600; padding: 8px 12px;
  background: var(--panel-2); border-bottom: 1px solid var(--border-soft);
}
.rec td { padding: 8px 12px; border-top: 1px solid var(--border-soft); vertical-align: top; cursor: pointer; }
.rec:hover td, .rec.open td { background: var(--panel); }
.mono { font-family: var(--mono); }
.nowrap { white-space: nowrap; }
.sub { font-size: 12px; color: var(--text-mute); }
.obj .name { font-weight: 600; word-break: break-word; }
.sum { color: var(--text-dim); max-width: 380px; }

/* Цвет действия не берём из акцента: в пространстве Zabbix он красный, и
   «изменение» читалось бы как «удаление». Добавление — зелёный (как up в
   Targets), изменение — info, удаление и неудачный вход — danger, вход/выход и
   выполнение — нейтральные. */
.badge { display: inline-block; font-size: 12px; padding: 2px 9px; border-radius: 6px; white-space: nowrap; background: var(--chip); color: var(--text-dim); border: 1px solid var(--border-soft); }
.badge.add { color: #50fa7b; background: rgba(80, 250, 123, 0.12); border-color: transparent; }
.badge.update { color: var(--info); background: var(--info-soft); border-color: transparent; }
.badge.delete, .badge.fail { color: var(--danger); background: rgba(255, 85, 85, 0.12); border-color: transparent; }
.chip.act.active.add { color: #50fa7b; border-color: #50fa7b; background: rgba(80, 250, 123, 0.12); }
.chip.act.active.update { color: var(--info); border-color: var(--info); background: var(--info-soft); }
.chip.act.active.delete, .chip.act.active.fail { color: var(--danger); border-color: var(--danger); background: rgba(255, 85, 85, 0.12); }

.details > td { padding: 4px 12px 16px; background: var(--panel); }
.links { display: flex; gap: 8px; margin: 6px 0 10px; }
.diff { width: 100%; border-collapse: collapse; font-size: 13px; }
.diff th { text-align: left; font-weight: 600; color: var(--text-mute); padding: 6px 10px; font-size: 12px; }
.diff td { padding: 6px 10px; border-top: 1px solid var(--border-soft); vertical-align: top; }
/* Колонка диффа забирает всю оставшуюся ширину: скриптам и выражениям нужно место. */
.diff td.change { width: 100%; }
.diff td:first-child { white-space: nowrap; }
/* Группа элемента списка: заголовок на всю ширину, поля — с отступом под ним. */
.ghead td { padding-top: 10px; }
.gtitle { font-weight: 600; font-family: var(--mono); }
.gop { font-size: 12px; margin: 0 8px; }
.gop.add { color: #50fa7b; }
.gop.del { color: var(--danger); }
.ghead .sub { margin-left: 6px; }
.gitem td { border-top: none; }
.gitem td:first-child { padding-left: 26px; }
.obj .name a { color: inherit; text-decoration: none; border-bottom: 1px dashed var(--border); }
.obj .name a:hover { color: var(--accent-bright); border-bottom-color: var(--accent); }
a.btn { text-decoration: none; display: inline-flex; align-items: center; }

.more { margin: 12px auto; display: block; }
.more-set td { padding: 4px 12px 8px; border-top: none; }
.empty { color: var(--text-mute); font-size: 13px; padding: 16px 2px; }

@media (max-width: 900px) { .grid { grid-template-columns: 1fr 1fr; } }
</style>
