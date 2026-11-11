<script setup>
// Изменение одного поля из аудита — так, чтобы читалось с одного взгляда:
//   короткое значение   → одной строкой «выключено → включено», красное и
//                         зелёное; внутри подсвечен сам изменённый кусок;
//   длинное (скрипт,    → дифф как в GitLab: только изменённые строки с
//   выражение, JSON)      контекстом, неизменённое свёрнуто;
//   добавлено/удалено   → слово операции и значение целиком.
import { computed } from 'vue'
import { diffValues, inlineDiff } from '../lib/diff.js'

const props = defineProps({
  op: { type: String, default: 'update' },
  opLabel: { type: String, default: '' },
  oldVal: { type: String, default: '' },
  newVal: { type: String, default: '' },
})

// До этой длины значение показываем строкой «было → стало»; длиннее — блоком.
const SHORT = 80
const isShort = (v) => v.length <= SHORT && !v.includes('\n')

const mode = computed(() => {
  const o = props.oldVal
  const n = props.newVal
  if (o !== '' && n !== '') return isShort(o) && isShort(n) ? 'inline' : 'diff'
  if (o === '' && n === '') return 'word' // «добавлено» без значения (привязка в списке)
  return isShort(o || n) ? 'single' : 'block'
})

// Сторона одиночного значения: добавлено/привязано — зелёное, удалено/отвязано — красное.
const side = computed(() => (props.newVal !== '' ? 'add' : 'del'))

const inline = computed(() => (mode.value === 'inline' ? inlineDiff(props.oldVal, props.newVal) : null))

const rows = computed(() => {
  if (mode.value === 'diff') return diffValues(props.oldVal, props.newVal)
  if (mode.value === 'block') {
    return (props.newVal || props.oldVal).split('\n').map((line) => ({ type: side.value, segs: [{ text: line, hl: false }] }))
  }
  return []
})

// Слово операции показываем, только когда это не обычное изменение значения:
// у «было → стало» оно и так понятно из стрелки.
const showOp = computed(() => props.op !== 'update' && props.opLabel)

const SIGN = { del: '−', add: '+', ctx: ' ' }

// 1 строка, 2 строки, 5 строк
function lines(n) {
  const d = n % 10
  const dd = n % 100
  if (d === 1 && dd !== 11) return `${n} строка`
  if (d >= 2 && d <= 4 && (dd < 12 || dd > 14)) return `${n} строки`
  return `${n} строк`
}
</script>

<template>
  <div class="dv">
    <!-- было → стало одной строкой -->
    <div v-if="mode === 'inline'" class="inline">
      <span class="val del"><span v-for="(s, j) in inline.del" :key="j" :class="{ hl: s.hl }">{{ s.text }}</span></span>
      <span class="arrow">→</span>
      <span class="val add"><span v-for="(s, j) in inline.add" :key="j" :class="{ hl: s.hl }">{{ s.text }}</span></span>
    </div>

    <!-- добавлено / удалено: слово операции + значение -->
    <div v-else-if="mode === 'single' || mode === 'word'" class="inline">
      <span v-if="showOp" class="op" :class="side">{{ opLabel }}</span>
      <span v-if="mode === 'single'" class="val" :class="side">{{ newVal || oldVal }}</span>
    </div>

    <!-- длинное значение: построчно -->
    <template v-else>
      <div v-if="showOp" class="op" :class="side">{{ opLabel }}</div>
      <div class="block">
        <template v-for="(r, i) in rows" :key="i">
          <div v-if="r.type === 'skip'" class="skip">⋯ {{ lines(r.count) }} без изменений</div>
          <div v-else class="line" :class="r.type">
            <span class="sign">{{ SIGN[r.type] }}</span>
            <span class="text"><span
              v-for="(s, j) in r.segs"
              :key="j"
              :class="{ hl: s.hl }"
            >{{ s.text }}</span></span>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* Цвета — как у бейджей действий: добавленное зелёное, удалённое — danger.
   Фон значения бледный, изменённый кусок внутри — насыщеннее. */
.dv { font-family: var(--mono); font-size: 12.5px; line-height: 1.55; }

.inline { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 8px; }
.arrow { color: var(--text-mute); }
.val { padding: 1px 7px; border-radius: 5px; white-space: pre-wrap; word-break: break-word; }
.val.del { background: rgba(255, 85, 85, 0.10); color: var(--text-dim); }
.val.add { background: rgba(80, 250, 123, 0.10); color: var(--text); }
.val.del .hl { background: rgba(255, 85, 85, 0.32); border-radius: 3px; }
.val.add .hl { background: rgba(80, 250, 123, 0.30); border-radius: 3px; }

.op { font-family: var(--sans); font-size: 12px; }
.op.add { color: #50fa7b; }
.op.del { color: var(--danger); }
div.op { margin-bottom: 4px; }
:root[data-theme="light"] .op.add { color: #1a7f37; }

.block { border: 1px solid var(--border-soft); border-radius: 7px; overflow: hidden; background: var(--panel-2); }
.line { display: flex; }
.sign { flex: none; width: 22px; text-align: center; user-select: none; color: var(--text-mute); }
/* pre-wrap: отступы скриптов и JSON сохраняются, длинные строки переносятся. */
.text { white-space: pre-wrap; word-break: break-word; padding-right: 10px; min-width: 0; }
.line.del { background: rgba(255, 85, 85, 0.08); }
.line.del .sign { color: var(--danger); }
.line.del .hl { background: rgba(255, 85, 85, 0.32); border-radius: 3px; }
.line.add { background: rgba(80, 250, 123, 0.08); }
.line.add .sign { color: #50fa7b; }
.line.add .hl { background: rgba(80, 250, 123, 0.30); border-radius: 3px; }
.line.ctx .text { color: var(--text-mute); }
:root[data-theme="light"] .line.add .sign { color: #1a7f37; }
.skip {
  color: var(--text-mute); font-size: 11.5px; padding: 2px 10px 2px 22px;
  background: var(--panel); border-top: 1px dashed var(--border-soft); border-bottom: 1px dashed var(--border-soft);
}
</style>
