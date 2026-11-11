const GARBAGE = '[object Object]'

export { GARBAGE }

const DEFAULT_TG_URL = 'http://alarmbot.hoff.ru/sendService/api/telegram/sendTextFromService'

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

/** Группы Telegram: строки через Enter / запятую. */
export function normalizeGroupList(raw) {
  if (raw == null) return []
  if (Array.isArray(raw)) {
    return raw.map((x) => String(x || '').trim()).filter(Boolean)
  }
  return String(raw)
    .split(/[\n,;]+/)
    .map((x) => x.trim())
    .filter(Boolean)
}

export function groupListToDraft(list) {
  return normalizeGroupList(list).join('\n')
}

export function parseDraftGroups(text) {
  return normalizeGroupList(String(text || ''))
}

/** Email включён? Старые конфиги без enabled → да. */
export function emailEnabled(delivery) {
  const email = (delivery && delivery.email) || {}
  if (email.enabled === false) return false
  return true
}

/** Telegram включён? */
export function telegramEnabled(delivery) {
  const tg = (delivery && delivery.telegram) || {}
  return tg.enabled === true
}

/** Починить delivery.email / delivery.telegram внутри конфига на месте. */
export function sanitizeDelivery(cfg) {
  if (!cfg || typeof cfg !== 'object') return cfg
  if (!cfg.delivery) cfg.delivery = {}
  if (!cfg.delivery.email) cfg.delivery.email = {}
  const email = cfg.delivery.email
  email.to = normalizeEmailList(email.to)
  if (email.enabled === undefined) email.enabled = true
  if (!email.from) email.from = 'noreply-n8nalerts@hoff.ru'

  if (!cfg.delivery.telegram) cfg.delivery.telegram = {}
  const tg = cfg.delivery.telegram
  if (tg.enabled === undefined) tg.enabled = false
  tg.groupList = normalizeGroupList(tg.groupList)
  tg.authorization = String(tg.authorization || '').trim()
  tg.url = String(tg.url || '').trim() || DEFAULT_TG_URL
  return cfg
}

export { DEFAULT_TG_URL }
