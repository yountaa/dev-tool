<script setup>
// Каркас приложения: слева панель модулей (свёрнута до иконок, раскрывается по
// наведению), сверху — имя сервиса. Справа — активный модуль. Плюс переключатель
// светлой/тёмной темы (запоминается в localStorage).
import { ref, computed, watchEffect, onMounted } from 'vue'
import { modules } from './modules/registry.js'
import { http } from './shared/api.js'
import { setTz } from './shared/time.js'
import { urlParams, setUrlParams } from './shared/urlstate.js'

// Кто залогинен. auth=true — вход через Keycloak (oauth2-proxy), показываем имя.
// auth=false — локальный режим без входа (имя создателя вводится руками).
const me = ref('')
const auth = ref(false)

// RBAC: список id модулей, доступных пользователю (приходит из /access/me).
// null = ограничений нет/бэкенд недоступен — показываем все вкладки (dev-режим).
const allowed = ref(null)
const visible = computed(() =>
  allowed.value ? modules.filter((m) => allowed.value.includes(m.id)) : modules,
)

// Активный модуль: сперва из URL (?m=… — ссылка от коллеги), затем из localStorage
// (своя последняя вкладка), иначе первый. Валидируем — модуль мог исчезнуть.
const savedModule = urlParams().get('m') || localStorage.getItem('activeModule')
const activeId = ref(modules.some((m) => m.id === savedModule) ? savedModule : modules[0]?.id)
const active = computed(() => visible.value.find((m) => m.id === activeId.value))

const theme = ref('dark')
onMounted(async () => {
  theme.value = localStorage.getItem('theme') || 'dark'
  try {
    // Один запрос уровня приложения: имя, авторизация, таймзона и доступные вкладки.
    const info = await http.get('/access/me')
    me.value = info.name || ''
    auth.value = !!info.auth
    setTz(info.tz) // таймзона показа дат (МСК) — общая для всех вкладок
    allowed.value = Array.isArray(info.modules) ? info.modules : null
    // Если активная вкладка недоступна пользователю — переключаемся на первую доступную.
    if (!visible.value.some((m) => m.id === activeId.value)) {
      activeId.value = visible.value[0]?.id
    }
  } catch (e) {
    /* бэкенд недоступен — оставляем пустым, показываем все вкладки */
  }
})
watchEffect(() => {
  document.documentElement.setAttribute('data-theme', theme.value)
  localStorage.setItem('theme', theme.value)
})
// Пространство-цвет: активная вкладка задаёт data-space на <html>, а theme.css
// перекрашивает под неё всю палитру (акцент/рельс). Ощущение отдельного места.
// Заодно запоминаем вкладку (localStorage — для себя, ?m= в URL — для ссылок).
watchEffect(() => {
  document.documentElement.setAttribute('data-space', activeId.value || '')
  if (activeId.value) {
    localStorage.setItem('activeModule', activeId.value)
    setUrlParams({ m: activeId.value })
  }
})
function toggleTheme() {
  theme.value = theme.value === 'dark' ? 'light' : 'dark'
}

// Короткая ссылка на текущий вид. Всё состояние (модуль/кластер/вкладка/запросы)
// уже лежит в URL — бэкенд сохраняет длинный путь и отдаёт /s/<id>, его копируем
// в буфер. Кнопка на пару секунд показывает результат.
const shared = ref('') // '' | 'ok' | 'err'
async function shareLink() {
  try {
    const r = await http.post('/share', { path: window.location.pathname + window.location.search })
    const short = `${window.location.origin}/s/${r.id}`
    try {
      await navigator.clipboard.writeText(short)
    } catch (e) {
      window.prompt('Скопируй короткую ссылку:', short) // буфер недоступен (не-HTTPS)
    }
    shared.value = 'ok'
  } catch (e) {
    shared.value = 'err'
  }
  setTimeout(() => { shared.value = '' }, 2000)
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
          v-for="m in visible"
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

        <!-- Короткая ссылка на текущий вид (фильтры/запрос — из URL) -->
        <button
          class="share"
          :class="{ ok: shared === 'ok' }"
          title="Скопировать короткую ссылку на этот вид"
          @click="shareLink"
        >
          <svg v-if="shared !== 'ok'" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></svg>
          <svg v-else viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5" /></svg>
          <span>{{ shared === 'ok' ? 'скопировано' : shared === 'err' ? 'ошибка' : 'ссылка' }}</span>
        </button>

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

    <!-- RBAC: доступных вкладок нет (пустой modules из /access/me) — не пустой экран,
         а понятное сообщение. active становится undefined только в этом случае. -->
    <main class="content" v-else>
      <div class="noaccess">
        <h2>Нет доступа</h2>
        <p>У вашей учётной записи нет доступа ни к одной вкладке. Нужен доступ к
          соответствующей группе — обратитесь к администратору.</p>
        <p v-if="me" class="who">вы вошли как <b>{{ me }}</b></p>
      </div>
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
/* На узких экранах ужимаем поля, чтобы не съедать и без того малую ширину. */
@media (max-width: 720px) { .content { padding: 18px 14px; } }
.head { margin-bottom: 22px; display: flex; align-items: center; }
.head-title { font-size: 17px; font-weight: 700; }
.head-sub { color: var(--text-mute); font-family: var(--mono); font-size: 13px; margin-left: 8px; }

/* Кнопка «ссылка» — короткий URL текущего вида; та же капсула, что и плашка юзера. */
.share {
  margin-left: auto; flex: none; display: inline-flex; align-items: center; gap: 7px;
  background: var(--panel); border: 1px solid var(--border-soft); color: var(--text-dim);
  border-radius: 20px; padding: 6px 13px; font-size: 12px; white-space: nowrap;
}
.share:hover { color: var(--accent-bright); border-color: var(--accent); }
.share.ok { color: var(--accent-bright); border-color: var(--accent); background: var(--accent-soft); }

/* Плашка пользователя справа в шапке (рядом с кнопкой «ссылка») */
.user {
  margin-left: 10px; flex: none; display: inline-flex; align-items: center; gap: 8px;
  background: var(--panel); border: 1px solid var(--border-soft);
  border-radius: 20px; padding: 6px 14px; font-size: 13px;
  white-space: nowrap;
}
.user .u-ico { color: var(--accent-bright); display: flex; flex: none; }
.user .u-name { font-family: var(--mono); color: var(--text); }
.user.dim { padding: 6px 14px; }
.user.dim .u-name { color: var(--text-mute); }

/* Экран «нет доступа» (RBAC не пустил ни к одной вкладке). */
.noaccess { max-width: 460px; margin: 12vh auto 0; text-align: center; }
.noaccess h2 { font-size: 20px; margin: 0 0 10px; }
.noaccess p { color: var(--text-dim); font-size: 14px; line-height: 1.5; margin: 0 0 8px; }
.noaccess .who { color: var(--text-mute); font-family: var(--mono); font-size: 13px; margin-top: 14px; }
</style>
