// Состояние страницы — в query-параметрах URL: активный модуль (m), окружение,
// вкладка, выражения запросов и т.д. Такую ссылку можно отправить коллеге — он
// увидит тот же вид. Кнопка «ссылка» в шапке (App.vue) укорачивает её через
// бэкенд (POST /share → /s/<id>).
//
// Пишем через history.replaceState: URL меняется без перезагрузки и без записей
// в историю браузера (кнопка «назад» не гоняет по каждому клику фильтров).

export function urlParams() {
  return new URLSearchParams(window.location.search)
}

// Обновить параметры URL: patch = { ключ: значение | [значения] | null }.
// null/undefined/'' удаляет ключ; массив пишет несколько значений (q=a&q=b);
// ключи, которых нет в patch, не трогаем.
export function setUrlParams(patch) {
  const p = new URLSearchParams(window.location.search)
  for (const [k, v] of Object.entries(patch)) {
    p.delete(k)
    if (v === null || v === undefined || v === '') continue
    if (Array.isArray(v)) {
      for (const item of v) if (item !== '') p.append(k, item)
    } else {
      p.set(k, String(v))
    }
  }
  const qs = p.toString()
  window.history.replaceState(null, '', qs ? `${window.location.pathname}?${qs}` : window.location.pathname)
}
