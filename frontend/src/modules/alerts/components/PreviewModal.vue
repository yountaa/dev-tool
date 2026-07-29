<script setup>
// Предпросмотр: показываем письма ровно такими, какими их соберёт движок.
// Ничего не отправляем и не трогаем состояние — это безопасная примерка.
import { ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  result: { type: Object, default: null }, // { emails, total, note, error }
})
defineEmits(['close'])

const active = ref(0)
watch(() => props.result, () => { active.value = 0 })
</script>

<template>
  <div v-if="open" class="backdrop" @click.self="$emit('close')">
    <div class="win">
      <div class="head">
        <b>Предпросмотр</b>
        <span v-if="result?.note" class="note">{{ result.note }}</span>
        <span class="spacer"></span>
        <button class="btn btn-ghost btn-sm" @click="$emit('close')">Закрыть</button>
      </div>

      <p v-if="result?.error" class="msg msg-err inner">{{ result.error }}</p>

      <template v-else-if="result?.emails?.length">
        <div v-if="result.emails.length > 1" class="tabs">
          <button
            v-for="(e, i) in result.emails" :key="i"
            class="tab" :class="{ on: i === active, skip: e.wouldSend === false }"
            @click="active = i"
          >{{ e.label }}</button>
        </div>
        <div class="subject">
          <span class="muted">Тема:</span> {{ result.emails[active].subject }}
          <span v-if="result.emails[active].wouldSend === false" class="skip-badge">письма не будет</span>
        </div>
        <iframe :srcdoc="result.emails[active].html" sandbox=""></iframe>
      </template>

      <p v-else class="empty inner">
        За выбранный период ничего не найдено — письма не будет.
      </p>
    </div>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed; inset: 0; z-index: 40; display: flex; align-items: center;
  justify-content: center; padding: 24px; background: rgba(10, 10, 18, 0.55);
}
.win {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  box-shadow: var(--shadow-pop); width: min(1200px, 96vw); height: min(88vh, 900px);
  display: flex; flex-direction: column; overflow: hidden;
}
.head { display: flex; align-items: center; gap: 12px; padding: 11px 16px; border-bottom: 1px solid var(--border); }
.note { color: var(--text-mute); font-size: 12px; }
.spacer { flex: 1; }
.tabs { display: flex; gap: 6px; padding: 8px 16px; border-bottom: 1px solid var(--border-soft); flex-wrap: wrap; }
.tab {
  border: 1px solid var(--border); background: var(--chip); color: var(--text-dim);
  border-radius: 8px; padding: 4px 10px; font-size: 12px; font-family: var(--sans);
}
.tab.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-bright); }
.tab.skip { opacity: .65; text-decoration: line-through; }
.subject { padding: 9px 16px; font-size: 12.5px; border-bottom: 1px solid var(--border-soft); }
.muted { color: var(--text-mute); }
.skip-badge { margin-left: 8px; color: var(--text-mute); font-size: 11.5px; }
iframe { flex: 1; width: 100%; border: none; background: #f6f7f9; }
.inner { margin: 16px; }
.empty { color: var(--text-mute); text-align: center; padding: 40px; }
</style>
