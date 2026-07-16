// Обёртка над fetch. Ходим относительными путями (/silences/...) — их проксирует
// Vite в dev и nginx в prod, поэтому адрес бэка в коде не зашит.
async function request(method, path, body, opts = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
    signal: opts.signal, // AbortController — для отмены долгих запросов
  })

  if (!res.ok) {
    // Текст ответа бэкенда — видно, в чём ошибка. FastAPI кладёт её в {"detail": …} —
    // достаём и показываем сам текст, а не сырой JSON-конверт.
    const text = await res.text()
    let msg = text
    try {
      const j = JSON.parse(text)
      if (j && j.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch (e) { /* не JSON — показываем как есть */ }
    throw new Error(`${res.status}: ${msg}`)
  }

  if (res.status === 204) return null
  return res.json()
}

export const http = {
  get: (path, opts) => request('GET', path, undefined, opts),
  post: (path, body, opts) => request('POST', path, body, opts),
  put: (path, body, opts) => request('PUT', path, body, opts),
  del: (path, opts) => request('DELETE', path, undefined, opts),
}
