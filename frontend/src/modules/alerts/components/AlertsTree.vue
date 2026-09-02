<script setup>
// Дерево алертов в стиле VS Code Explorer: папки сворачиваются, алерты перетаскиваются.
import { computed, ref } from 'vue'
import { TYPE_INFO } from '../templates.js'

const props = defineProps({
  alerts: { type: Array, default: () => [] },
  folders: { type: Array, default: () => [] },
  selected: { type: String, default: null },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits([
  'pick',
  'create-folder',
  'rename-folder',
  'delete-folder',
  'move-alert',
])

/** Свернутые узлы (по умолчанию всё развёрнуто). */
const collapsed = ref(new Set())
const dragKey = ref(null)
const dropTarget = ref(null) // folder id | 'unfiled' | null

const folderOf = computed(() => {
  const map = new Map()
  for (const f of props.folders) {
    for (const k of f.alertKeys || []) map.set(k, f.id)
  }
  return map
})

const unfiled = computed(() =>
  props.alerts.filter((a) => !folderOf.value.has(a.alertKey)),
)

function alertsInFolder(folder) {
  const keys = new Set(folder.alertKeys || [])
  return props.alerts.filter((a) => keys.has(a.alertKey))
}

function isCollapsed(id) {
  return collapsed.value.has(id)
}

function toggle(id) {
  const next = new Set(collapsed.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  collapsed.value = next
}

function typeLabel(t) {
  return TYPE_INFO[t]?.title || t || '—'
}

function onDragStart(alertKey, e) {
  dragKey.value = alertKey
  if (e.dataTransfer) {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', alertKey)
  }
}

function onDragEnd() {
  dragKey.value = null
  dropTarget.value = null
}

function onDragOver(targetId, e) {
  if (!dragKey.value) return
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'
  dropTarget.value = targetId
}

function onDragLeave(targetId) {
  if (dropTarget.value === targetId) dropTarget.value = null
}

function onDrop(targetId, e) {
  e.preventDefault()
  const key = dragKey.value || e.dataTransfer?.getData('text/plain')
  dragKey.value = null
  dropTarget.value = null
  if (!key) return
  const current = folderOf.value.get(key)
  const nextId = targetId === 'unfiled' ? null : targetId
  if (current === nextId) return
  if (targetId !== 'unfiled' && !props.folders.some((f) => f.id === targetId)) return
  emit('move-alert', { alertKey: key, folderId: nextId })
}

function isDropTarget(id) {
  return dropTarget.value === id && !!dragKey.value
}
</script>

<template>
  <div class="tree">
    <div class="tree-head">
      <span class="tree-title">Алерты</span>
      <button
        type="button"
        class="tree-btn"
        title="Новая папка"
        @click="emit('create-folder')"
      >+</button>
    </div>

    <div class="tree-body">
      <p v-if="loading && !alerts.length" class="empty">Загрузка…</p>
      <p v-else-if="!alerts.length" class="empty">Пока ни одного алерта</p>

      <template v-else>
        <section
          v-for="f in folders"
          :key="'f-' + f.id"
          class="tree-section"
        >
          <div
            class="tree-row folder-row"
            :class="{
              collapsed: isCollapsed(f.id),
              'drop-over': isDropTarget(f.id),
            }"
            @click="toggle(f.id)"
            @dragover="onDragOver(f.id, $event)"
            @dragleave="onDragLeave(f.id)"
            @drop="onDrop(f.id, $event)"
          >
            <span class="chev" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M9 6l6 6-6 6"/></svg>
            </span>
            <span class="folder-ico" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><path d="M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2z"/></svg>
            </span>
            <span class="label">{{ f.name }}</span>
            <span class="cnt">{{ alertsInFolder(f).length }}</span>
            <span class="row-ops" @click.stop>
              <button type="button" class="op" title="Переименовать" @click="emit('rename-folder', f)">✎</button>
              <button type="button" class="op danger" title="Удалить" @click="emit('delete-folder', f)">×</button>
            </span>
          </div>

          <div v-show="!isCollapsed(f.id)" class="tree-kids">
            <button
              v-for="a in alertsInFolder(f)"
              :key="a.alertKey"
              type="button"
              class="tree-row alert-row"
              :class="{ on: a.alertKey === selected, dragging: dragKey === a.alertKey }"
              draggable="true"
              @click="emit('pick', a)"
              @dragstart="onDragStart(a.alertKey, $event)"
              @dragend="onDragEnd"
            >
              <span class="chev spacer" aria-hidden="true"></span>
              <span class="dot" :class="{ live: a.enabled, err: a.state?.lastError }"></span>
              <span class="alert-main">
                <span class="nm">{{ a.name }}</span>
                <span class="meta">{{ typeLabel(a.type) }}</span>
              </span>
            </button>
            <p v-if="!alertsInFolder(f).length" class="empty-kid">перетащите алерт сюда</p>
          </div>
        </section>

        <section class="tree-section">
          <div
            class="tree-row folder-row"
            :class="{
              collapsed: isCollapsed('unfiled'),
              'drop-over': isDropTarget('unfiled'),
            }"
            @click="toggle('unfiled')"
            @dragover="onDragOver('unfiled', $event)"
            @dragleave="onDragLeave('unfiled')"
            @drop="onDrop('unfiled', $event)"
          >
            <span class="chev" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M9 6l6 6-6 6"/></svg>
            </span>
            <span class="folder-ico muted" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
            </span>
            <span class="label">Без папки</span>
            <span class="cnt">{{ unfiled.length }}</span>
          </div>

          <div v-show="!isCollapsed('unfiled')" class="tree-kids">
            <button
              v-for="a in unfiled"
              :key="a.alertKey"
              type="button"
              class="tree-row alert-row"
              :class="{ on: a.alertKey === selected, dragging: dragKey === a.alertKey }"
              draggable="true"
              @click="emit('pick', a)"
              @dragstart="onDragStart(a.alertKey, $event)"
              @dragend="onDragEnd"
            >
              <span class="chev spacer" aria-hidden="true"></span>
              <span class="dot" :class="{ live: a.enabled, err: a.state?.lastError }"></span>
              <span class="alert-main">
                <span class="nm">{{ a.name }}</span>
                <span class="meta">{{ typeLabel(a.type) }}</span>
              </span>
            </button>
            <p v-if="!unfiled.length" class="empty-kid">нет алертов</p>
          </div>
        </section>
      </template>
    </div>

    <p v-if="alerts.length" class="tree-hint">Перетащите алерт на папку, чтобы переместить</p>
  </div>
</template>

<style scoped>
.tree {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid var(--border-soft);
  border-radius: 10px;
  background: var(--panel);
  overflow: hidden;
}
.tree-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--panel-2);
}
.tree-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--text-mute);
}
.tree-btn {
  width: 24px; height: 24px;
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  background: var(--panel);
  color: var(--text-dim);
  font-size: 16px;
  line-height: 1;
  font-family: var(--sans);
}
.tree-btn:hover { border-color: var(--accent); color: var(--accent-bright); }
.tree-body {
  flex: 1;
  overflow: auto;
  max-height: calc(100vh - 220px);
  padding: 4px 0 6px;
}
.tree-section { margin-bottom: 2px; }
.tree-row {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  min-height: 26px;
  padding: 2px 8px 2px 4px;
  border: none;
  background: transparent;
  color: var(--text);
  font-family: var(--sans);
  text-align: left;
  border-radius: 0;
  cursor: default;
  user-select: none;
}
.folder-row { cursor: pointer; color: var(--text-dim); font-size: 13px; }
.folder-row:hover { background: var(--panel-2); color: var(--text); }
.folder-row.drop-over { background: var(--accent-soft); outline: 1px dashed var(--accent); }
.chev {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  flex: none;
  color: var(--text-mute);
  transition: transform 0.12s ease;
}
.folder-row:not(.collapsed) .chev { transform: rotate(90deg); }
.chev.spacer { visibility: hidden; }
.folder-ico { display: flex; color: var(--accent-bright); flex: none; opacity: 0.9; }
.folder-ico.muted { color: var(--text-mute); }
.label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}
.cnt {
  font-size: 11px;
  color: var(--text-mute);
  font-family: var(--mono);
  min-width: 14px;
  text-align: right;
}
.row-ops {
  display: flex;
  gap: 0;
  opacity: 0;
  transition: opacity 0.1s;
}
.folder-row:hover .row-ops { opacity: 1; }
.op {
  border: none;
  background: transparent;
  color: var(--text-mute);
  padding: 0 4px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.op:hover { color: var(--text); }
.op.danger:hover { color: var(--danger); }
.tree-kids { padding-left: 6px; }
.alert-row {
  cursor: pointer;
  padding-top: 5px;
  padding-bottom: 5px;
  min-height: 36px;
}
.alert-row:hover { background: var(--panel-2); }
.alert-row.on { background: var(--accent-soft); }
.alert-row.dragging { opacity: 0.45; }
.dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--track);
  flex: none;
  margin-right: 2px;
}
.dot.live { background: #50c878; }
.dot.err { background: var(--danger); }
.alert-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.nm {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  font-size: 11px;
  color: var(--text-mute);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.empty, .empty-kid {
  margin: 0;
  padding: 10px 12px;
  font-size: 12px;
  color: var(--text-mute);
}
.empty-kid { padding: 6px 12px 6px 28px; font-style: italic; }
.tree-hint {
  margin: 0;
  padding: 6px 10px 8px;
  font-size: 11px;
  color: var(--text-mute);
  border-top: 1px solid var(--border-soft);
  background: var(--panel-2);
}
</style>
