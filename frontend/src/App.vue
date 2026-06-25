<script setup>
// Каркас приложения: слева панель модулей (свёрнута до иконок, раскрывается по
// наведению), сверху — имя сервиса. Справа — активный модуль. Плюс переключатель
// светлой/тёмной темы (запоминается в localStorage).
import { ref, computed, watchEffect, onMounted } from 'vue'
import { modules } from './modules/registry.js'
import { http } from './shared/api.js'
import { setTz } from './shared/time.js'

const activeId = ref(modules[0]?.id)
const active = computed(() => modules.find((m) => m.id === activeId.value))

// Кто залогинен. auth=true — вход через Keycloak (oauth2-proxy), показываем имя.
// auth=false — локальный режим без входа (имя создателя вводится руками).
const me = ref('')
const auth = ref(false)

const theme = ref('dark')
onMounted(async () => {
  theme.value = localStorage.getItem('theme') || 'dark'
  try {
    const info = await http.get('/silences/me')
    me.value = info.name || ''
    auth.value = !!info.auth
    setTz(info.tz) // таймзона показа дат (МСК) — общая для всех вкладок
  } catch (e) {
    /* бэкенд недоступен — оставляем пустым */
  }
})
watchEffect(() => {
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('theme', theme.value)
})
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}
</script>

<template>
  <div class="app">
    <nav class="rail">
      <!-- Имя сервиса -->
      <div class="brand">
        <span class="brand-logo">
          <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 7l9-4 9 4-9 4-9-4Z" /><path d="M3 12l9 4 9-4" /><path d="M3 17l9 4 9-4" />
          </svg>
        </span>
        <span class="brand-name">Observability tool</span>
      </div>

      <!-- Список модулей -->
      <div class="modules">
        <button
          v-for="m in modules"
          :key="m.id"
          class="mod"
          :class="{ active: m.id === activeId }"
          @click="activeId = m.id"
        >
          <span class="mod-ico">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="m.icon" /></svg>
          </span>
          <span class="mod-name">{{ m.title }}</span>
        </button>
      </div>

      <!-- Переключатель темы: иконка слева + подпись текущего состояния (как у модулей).
           Солнце = light, луна = dark. -->
      <button class="mod theme" :title="'Тема: ' + theme" @click="toggleTheme">
        <span class="mod-ico">
          <svg v-if="theme === 'light'" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
          <svg v-else viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
          </svg>
        </span>
        <span class="mod-name">{{ theme }}</span>
      </button>
    </nav>

    <main class="content" v-if="active">
      <header class="head">
        <span class="head-title">{{ active.title }}</span>
        <span class="head-sub">— {{ active.subtitle }}</span>

        <!-- Кто вошёл -->
        <div v-if="auth" class="user">
          <span class="u-ico">
            <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 4-6 8-6s8 2 8 6" /></svg>
          </span>
          <span class="u-name">{{ me || '—' }}</span>
        </div>
        <div v-else class="user dim" title="Вход не настроен — имя создателя вводится вручную">
          <span class="u-name">локальный режим</span>
        </div>
      </header>
      <component :is="active.component" :me="me" :auth="auth" />
    </main>
  </div>
</template>

<style scoped>
.app { min-height: 100vh; padding-left: 60px; }

.rail {
  position: fixed;
  top: 0; left: 0; bottom: 0;
  width: 60px;
  overflow: hidden;
  background: var(--bg-rail);
  border-right: 1px solid var(--border-soft);
  display: flex;
  flex-direction: column;
  padding: 10px;
  transition: width 0.16s ease;
  z-index: 20;
}
.rail:hover { width: 230px; box-shadow: 8px 0 30px rgba(0, 0, 0, 0.22); }

.brand {
  display: flex; align-items: center; gap: 12px;
  height: 40px; padding: 0 6px; margin-bottom: 14px;
  color: var(--text);
}
.brand-logo { color: var(--accent-bright); flex: none; display: flex; }
.brand-name { font-weight: 700; font-size: 15px; white-space: nowrap; opacity: 0; transition: opacity 0.12s; }

.modules { display: flex; flex-direction: column; gap: 4px; flex: 1; }
.mod {
  display: flex; align-items: center; gap: 12px;
  height: 40px; padding: 0 8px; width: 100%;
  border: 1px solid transparent; border-radius: 9px;
  background: transparent; color: var(--text-mute);
  text-align: left;
}
.mod:hover { color: var(--text); background: var(--panel); }
.mod.active { color: var(--accent-bright); background: var(--panel); border-color: var(--border); }
.mod-ico { flex: none; width: 24px; display: flex; align-items: center; justify-content: center; }
.mod-name { font-size: 13px; white-space: nowrap; opacity: 0; transition: opacity 0.12s; }

/* Текст показываем только когда панель раскрыта — иначе буквы выглядывают. */
.rail:hover .brand-name,
.rail:hover .mod-name { opacity: 1; }

/* Тема — обычный пункт как модули: иконка слева, подпись справа. */
.theme { margin-top: auto; color: var(--text-dim); }
.theme .mod-name { text-transform: uppercase; letter-spacing: 0.04em; font-size: 12px; }

.content { max-width: 1040px; margin: 0 auto; padding: 26px 30px; }
.head { margin-bottom: 22px; display: flex; align-items: center; }
.head-title { font-size: 17px; font-weight: 700; }
.head-sub { color: var(--text-mute); font-family: var(--mono); font-size: 13px; margin-left: 8px; }

/* Плашка пользователя справа в шапке */
.user {
  margin-left: auto; flex: none; display: inline-flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  border-radius: 20px; padding: 6px 14px; font-size: 13px;
  white-space: nowrap;
}
.user .u-ico { color: var(--accent-bright); display: flex; flex: none; }
.user .u-name { font-family: var(--mono); color: var(--text); }
.user.dim { padding: 6px 14px; }
.user.dim .u-name { color: var(--text-mute); }
</style>
