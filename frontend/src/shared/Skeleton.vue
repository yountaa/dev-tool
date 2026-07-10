<script setup>
// Загрузочная плашка-«скелет»: серые полосы с бегущим бликом вместо контента,
// пока данные грузятся. Общая для всех модулей — цвета из переменных темы.
defineProps({
  lines: { type: Number, default: 3 },   // сколько полос
  height: { type: Number, default: 40 }, // высота одной полосы, px
})
</script>

<template>
  <div class="skel" aria-label="Загрузка…">
    <div v-for="i in lines" :key="i" class="skel-bar" :style="{ height: height + 'px' }"></div>
  </div>
</template>

<style scoped>
.skel { display: flex; flex-direction: column; gap: 10px; }
.skel-bar {
  border-radius: 9px;
  background: linear-gradient(90deg, var(--panel) 25%, var(--panel-2) 37%, var(--panel) 63%);
  background-size: 400% 100%;
  animation: skel-shine 1.4s ease infinite;
}
@keyframes skel-shine {
  0% { background-position: 100% 50%; }
  100% { background-position: 0 50%; }
}
</style>
