<script setup>
// Маленький знак вопроса с всплывающим описанием. Для необязательных пояснений,
// которые не должны занимать место в форме постоянно.
import { ref } from 'vue'
defineProps({ text: { type: String, required: true } })
const open = ref(false)
</script>

<template>
  <span class="hint-wrap" @mouseenter="open = true" @mouseleave="open = false">
    <button type="button" class="q" @click="open = !open" aria-label="Пояснение">?</button>
    <span v-if="open" class="bubble">{{ text }}</span>
  </span>
</template>

<style scoped>
.hint-wrap { position: relative; display: inline-flex; vertical-align: middle; margin-left: 5px; }
.q {
  width: 16px; height: 16px; border-radius: 50%; border: 1px solid var(--border);
  background: var(--chip); color: var(--text-mute); font-size: 11px; line-height: 1;
  padding: 0; display: inline-flex; align-items: center; justify-content: center;
}
.q:hover { color: var(--accent-bright); border-color: var(--accent); }
.bubble {
  position: absolute; left: 50%; bottom: calc(100% + 6px); transform: translateX(-50%);
  width: max-content; max-width: 280px; z-index: 20;
  background: var(--panel); color: var(--text-dim); border: 1px solid var(--border);
  border-radius: 8px; box-shadow: var(--shadow-pop); padding: 8px 10px;
  font-size: 12px; line-height: 1.5; font-family: var(--sans); font-weight: 400;
  white-space: normal; text-align: left;
}
</style>
