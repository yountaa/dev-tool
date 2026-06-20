// Обёртка над fetch. Ходим относительными путями (/silences/...) — их проксирует
// Vite в dev и nginx в prod, поэтому адрес бэка в коде не зашит.
async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    // текст ответа бэкенда — видно, в чём ошибка
    const text = await res.text()
    throw new Error(`${res.status}: ${text}`)
  }

  if (res.status === 204) return null
  return res.json()
}

export const http = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
}
