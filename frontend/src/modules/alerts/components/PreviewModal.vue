<script setup>
// Предпросмотр: письмо и текст Telegram так, как их соберёт движок.
// Тестовая отправка в TG — по кнопке, без сдвига состояния алерта.
import { computed, ref, watch } from 'vue'
import { telegramEnabled } from '../lib/emailList.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  result: { type: Object, default: null }, // { emails, total, note, error }
  cfg: { type: Object, default: null },
  testBusy: { type: Boolean, default: false },
})
const emit = defineEmits(['close', 'test-telegram'])

const active = ref(0)
const channel = ref('email') // 'email' | 'telegram'

watch(() => props.result, () => {
  active.value = 0
  channel.value = 'email'
})

const emails = computed(() => props.result?.emails || [])
const current = computed(() => emails.value[active.value] || null)
const tgOn = computed(() => telegramEnabled(props.cfg?.delivery))
const showTgTab = computed(() => tgOn.value || emails.value.some((e) => e?.telegramText))

const telegramText = computed(() => {
  const e = current.value
  if (!e) return ''
  return String(e.telegramText || '').trim()
})

const canTestTg = computed(() => {
  if (!tgOn.value || props.testBusy) return false
  if (!telegramText.value) return false
  const tg = props.cfg?.delivery?.telegram || {}
  if (!String(tg.authorization || '').trim()) return false
  if (!Array.isArray(tg.groupList) || !tg.groupList.length) return false
  return true
})

function onTestTelegram() {
  if (!canTestTg.value) return
  emit('test-telegram', { text: telegramText.value })
}
</script>

<template>
  <div v-if="open" class="backdrop" @click.self="$emit('close')">
    <div class="win">
      <div class="head">
        <b>Предпросмотр</b>
        <span v-if="result?.note" class="note">{{ result.note }}</span>
        <span class="spacer"></span>
        <button
          v-if="showTgTab && channel === 'telegram'"
          type="button"
          class="btn btn-ghost btn-sm"
          :disabled="!canTestTg"
          :title="!canTestTg
            ? 'Нужны текст TG, authorization и хотя бы одна группа'
            : 'Отправить этот текст в группы из конфига'"
          @click="onTestTelegram"
        >{{ testBusy ? 'Отправка…' : 'Тест в Telegram' }}</button>
        <button class="btn btn-ghost btn-sm" @click="$emit('close')">Закрыть</button>
      </div>

      <p v-if="result?.error" class="msg msg-err inner">{{ result.error }}</p>

      <template v-else-if="emails.length">
        <div v-if="emails.length > 1" class="tabs">
          <button
            v-for="(e, i) in emails" :key="i"
            type="button"
            class="tab" :class="{ on: i === active, skip: e.wouldSend === false }"
            @click="active = i"
          >{{ e.label }}</button>
        </div>

        <div v-if="showTgTab" class="channels">
          <button type="button" class="ch" :class="{ on: channel === 'email' }" @click="channel = 'email'">Письмо</button>
          <button type="button" class="ch" :class="{ on: channel === 'telegram' }" @click="channel = 'telegram'">Telegram</button>
        </div>

        <div class="subject">
          <span class="muted">Тема:</span> {{ current?.subject }}
          <span v-if="current?.wouldSend === false" class="skip-badge">уведомления не будет</span>
        </div>

        <iframe
          v-if="channel === 'email'"
          :srcdoc="current?.html || ''"
          sandbox=""
        ></iframe>

        <div v-else class="tg-pane">
          <pre v-if="telegramText" class="tg-text">{{ telegramText }}</pre>
          <p v-else class="empty">
            Текст для Telegram пока не пришёл из preview.
            Обновите ноду <code>Preview Render1</code> в n8n (добавьте поле
            <code>telegramText</code> в каждый email) — сниппет в
            <code>n8n-preview-telegram-snippet.js</code>.
          </p>
        </div>
      </template>

      <p v-else class="empty inner">
        За выбранный период ничего не найдено — уведомления не будет.
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
.channels {
  display: flex; gap: 0; padding: 0 16px;
  border-bottom: 1px solid var(--border-soft);
}
.ch {
  padding: 8px 14px; font-size: 12.5px; font-family: var(--sans);
  background: transparent; border: none; border-bottom: 2px solid transparent;
  color: var(--text-dim);
}
.ch.on { color: var(--accent-bright); border-bottom-color: var(--accent); }
.subject { padding: 9px 16px; font-size: 12.5px; border-bottom: 1px solid var(--border-soft); }
.muted { color: var(--text-mute); }
.skip-badge { margin-left: 8px; color: var(--text-mute); font-size: 11.5px; }
iframe { flex: 1; width: 100%; border: none; background: #f6f7f9; }
.tg-pane {
  flex: 1; overflow: auto; padding: 16px;
  background: var(--panel-2);
}
.tg-text {
  margin: 0; white-space: pre-wrap; word-break: break-word;
  font-family: var(--mono); font-size: 13px; line-height: 1.5;
  color: var(--text);
}
.inner { margin: 16px; }
.empty { color: var(--text-mute); text-align: center; padding: 40px; line-height: 1.6; }
.empty code { font-size: 12px; }
</style>
