const GARBAGE = '[object Object]'

export { GARBAGE }

function isGarbage(s) {
  return !s || s === GARBAGE
}

/** Одна строка — email или пусто. */
function emailListItem(item) {
  if (item == null) return ''
  if (typeof item === 'string') {
    const s = item.trim()
    return isGarbage(s) ? '' : s
  }
  if (Array.isArray(item)) {
    return normalizeEmailList(item).join(', ')
  }
  if (typeof item === 'object') {
    // Случайно сохранили блок email или поле из field_caps
    if (Array.isArray(item.to)) return normalizeEmailList(item.to).join(', ')
    if (typeof item.to === 'string') return emailListItem(item.to)
    const candidates = [item.email, item.address, item.value]
    for (const c of candidates) {
      const s = String(c || '').trim()
      if (s && !isGarbage(s)) return s
    }
    // Любое строковое значение, похожее на email
    for (const v of Object.values(item)) {
      if (typeof v === 'string' && v.includes('@') && !isGarbage(v.trim())) return v.trim()
    }
    return ''
  }
  const s = String(item).trim()
  return isGarbage(s) ? '' : s
}

/** Привести delivery.email.to к массиву строк. */
export function normalizeEmailList(raw) {
  if (raw == null) return []
  if (typeof raw === 'string') {
    const s = raw.trim()
    if (isGarbage(s)) return []
    return s.split(/[\n,;]+/).map((x) => x.trim()).filter((x) => !isGarbage(x))
  }
  if (Array.isArray(raw)) {
    const out = []
    for (const item of raw) {
      const part = emailListItem(item)
      if (!part) continue
      for (const line of part.split(/[\n,;]+/)) {
        const t = line.trim()
        if (!isGarbage(t)) out.push(t)
      }
    }
    return out
  }
  if (typeof raw === 'object') {
    return normalizeEmailList(emailListItem(raw) || [])
  }
  const s = String(raw).trim()
  return isGarbage(s) ? [] : [s]
}

/** Текст для textarea: убрать [object Object] и пустые строки. */
export function emailListToDraft(list) {
  return normalizeEmailList(list).join('\n')
}

/** Почистить то, что пользователь вводит в textarea. */
export function parseDraftEmails(text) {
  return normalizeEmailList(String(text || ''))
}

/** Починить delivery.email.to внутри конфига на месте. */
export function sanitizeDelivery(cfg) {
  if (!cfg || typeof cfg !== 'object') return cfg
  if (!cfg.delivery) cfg.delivery = {}
  if (!cfg.delivery.email) cfg.delivery.email = {}
  cfg.delivery.email.to = normalizeEmailList(cfg.delivery.email.to)
  return cfg
}
