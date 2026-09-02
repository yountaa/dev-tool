<script setup>
// Создание / переименование / удаление папки — вместо prompt/confirm.
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'create' }, // create | rename | delete
  initialName: { type: String, default: '' },
  folderName: { type: String, default: '' },
  memberCount: { type: Number, default: 0 },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'confirm'])

const name = ref('')
const inputRef = ref(null)

const title = computed(() => {
  if (props.mode === 'rename') return 'Переименовать папку'
  if (props.mode === 'delete') return 'Удалить папку?'
  return 'Новая папка'
})

const hint = computed(() => {
  if (props.mode === 'delete') {
    const n = props.memberCount
    if (n > 0) {
      return `Папка «${props.folderName}» будет удалена. ${n} алерт(ов) останутся без папки.`
    }
    return `Папка «${props.folderName}» будет удалена без восстановления.`
  }
  if (props.mode === 'rename') return 'Новое имя отобразится в дереве слева.'
  return 'Папки помогают сгруппировать алерты. Их можно переименовать и перетаскивать алерты между ними.'
})

const canSubmit = computed(() => {
  if (props.mode === 'delete') return !props.busy
  return name.value.trim().length > 0 && !props.busy
})

watch(
  () => props.open,
  async (v) => {
    if (!v) return
    name.value = props.mode === 'rename' ? (props.initialName || '') : ''
    await nextTick()
    inputRef.value?.focus()
    if (props.mode === 'rename') inputRef.value?.select()
  },
)

function submit() {
  if (!canSubmit.value) return
  if (props.mode === 'delete') {
    emit('confirm', { mode: 'delete' })
    return
  }
  const trimmed = name.value.trim()
  if (!trimmed) return
  emit('confirm', { mode: props.mode, name: trimmed })
}

function onKeydown(e) {
  if (e.key === 'Escape') emit('close')
  if (e.key === 'Enter') submit()
}
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="backdrop" @click.self="emit('close')">
      <div class="win" role="dialog" aria-modal="true" @keydown="onKeydown">
        <div class="head">
          <span class="ico" aria-hidden="true">{{ mode === 'delete' ? '🗑' : '📁' }}</span>
          <b>{{ title }}</b>
        </div>

        <p class="hint">{{ hint }}</p>

        <label v-if="mode !== 'delete'" class="field">
          <span>Название</span>
          <input
            ref="inputRef"
            v-model="name"
            class="input"
            type="text"
            placeholder="Например: Production"
            :disabled="busy"
            @keydown.enter.prevent="submit"
          />
        </label>

        <div class="actions">
          <button type="button" class="btn btn-ghost" :disabled="busy" @click="emit('close')">
            Отмена
          </button>
          <button
            type="button"
            class="btn"
            :class="mode === 'delete' ? 'btn-danger' : 'btn-primary'"
            :disabled="!canSubmit"
            @click="submit"
          >
            {{ mode === 'delete' ? 'Удалить' : (mode === 'rename' ? 'Сохранить' : 'Создать') }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.backdrop {
  position: fixed; inset: 0; z-index: 50;
  display: flex; align-items: center; justify-content: center;
  padding: 24px; background: rgba(10, 10, 18, 0.55);
}
.win {
  width: min(420px, 96vw);
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: var(--shadow-pop);
  padding: 18px 20px 16px;
}
.head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
.ico { font-size: 18px; line-height: 1; }
.hint {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-dim);
}
.field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
.field > span { font-size: 12.5px; color: var(--text-mute); }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.btn-danger {
  background: var(--danger);
  border-color: var(--danger);
  color: #fff;
}
.btn-danger:hover:not(:disabled) { filter: brightness(1.05); }
.btn-danger:disabled { opacity: 0.5; }
</style>
