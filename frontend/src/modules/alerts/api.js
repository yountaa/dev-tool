// Клиент: конфиги/preview → n8n (/webhook/alerts);
// история и кэш engine → FastAPI + Postgres (/alerts/*).
import { http } from '../../shared/api.js'

const ENDPOINT = '/webhook/alerts'
const META = '/alerts'

const FALLBACK_INDEX_PATTERNS = [
  'omni-prod-*',
  'n8n_stack-*',
  'logs-*',
  'filebeat-*',
  'kubernetes-*',
]

async function call(action, payload = {}) {
  const data = await http.post(ENDPOINT, { action, ...payload })
  if (data && data.ok === false) {
    throw new Error(data.message || data.error || 'ошибка n8n')
  }
  return data
}

/** n8n list отдаёт lastRunAt/lastError на корне; UI ждёт state.* */
function shapeAlert(a) {
  if (!a || typeof a !== 'object') return a
  const state = a.state || {
    lastRunAt: a.lastRunAt || null,
    lastSuccessAt: a.lastSuccessAt || null,
    lastError: a.lastError || null,
  }
  return {
    ...a,
    alertKey: a.alertKey || a.configId || a.config?.id || a.id,
    rowId: a.rowId || a.id || null,
    type: a.type || a.config?.type || 'batch',
    intervalMinutes: Number(a.intervalMinutes) || 30,
    state,
  }
}

/** Привести ответ preview (старый single html или новый emails[]) к виду PreviewModal. */
function shapePreview(r) {
  if (!r || typeof r !== 'object') return { emails: [], note: null, total: 0 }
  if (Array.isArray(r.emails)) {
    return {
      emails: r.emails,
      total: r.total ?? r.emails.length,
      note: r.note || null,
    }
  }
  if (r.html || r.subject) {
    return {
      emails: [{
        subject: r.subject || 'Preview',
        html: r.html || '',
        wouldSend: true,
        label: `событий: ${r.total ?? '?'}`,
      }],
      total: r.total ?? 0,
      note: r.note || (r.unique != null ? `уник: ${r.unique}` : null),
    }
  }
  return { emails: [], total: r.total ?? 0, note: r.note || null }
}

function shapeHistoryItem(h) {
  if (!h || typeof h !== 'object') return h
  return {
    ...h,
    time: h.time || h.ts || h.createdAt || null,
    user: h.user || h.who || h.author || '',
    action: h.action || h.kind || '',
    name: h.name || h.alertName || h.config?.notify?.title || '',
    alertKey: h.alertKey || h.config?.id || '',
    before: h.before || null,
    after: h.after || null,
  }
}

/** Статус движка: явный engine из list или эвристика по алертам. */
export function shapeEngine(data) {
  const alerts = (data?.alerts || []).map(shapeAlert)
  const explicit = data?.engine
  if (explicit?.lastRunAt) return explicit

  const heartbeatKeys = ['__engine__', '__heartbeat__', 'engine-heartbeat']
  const hb = alerts.find((a) => heartbeatKeys.includes(a.alertKey))
  if (hb?.state?.lastRunAt) {
    return {
      lastRunAt: hb.state.lastRunAt,
      lastError: hb.state.lastError || null,
    }
  }

  const times = alerts
    .map((a) => a.state?.lastRunAt)
    .filter(Boolean)
    .sort()
  if (times.length) {
    return {
      lastRunAt: times[times.length - 1],
      lastError: alerts.find((a) => a.state?.lastError)?.state?.lastError || null,
    }
  }

  return explicit || null
}

/** Выбрать более свежий из двух engine-снимков. */
export function newerEngine(a, b) {
  if (!a?.lastRunAt) return b || null
  if (!b?.lastRunAt) return a
  const ta = Date.parse(a.lastRunAt)
  const tb = Date.parse(b.lastRunAt)
  if (Number.isNaN(ta)) return b
  if (Number.isNaN(tb)) return a
  return ta >= tb ? a : b
}

function collectIndexPatterns(alerts, remote = []) {
  const set = new Set(FALLBACK_INDEX_PATTERNS)
  for (const p of remote) if (p) set.add(String(p))
  for (const a of alerts || []) {
    const idx = a.config?.source?.index || a.source?.index
    if (idx) set.add(String(idx))
  }
  return [...set].sort((a, b) => a.localeCompare(b))
}

/** Снимок для истории — читаемые поля без гигантских query/map. */
export function historySnapshot(cfg, extra = {}) {
  if (!cfg || typeof cfg !== 'object') return { ...extra }
  const email = cfg.delivery?.email || {}
  return {
    name: extra.name ?? cfg.notify?.title ?? '',
    alertKey: extra.alertKey ?? cfg.id ?? '',
    id: cfg.id,
    type: cfg.type,
    enabled: !!cfg.enabled,
    source: {
      index: cfg.source?.index || '',
      timeField: cfg.source?.timeField || '',
      kql: cfg.source?.kql || '',
    },
    notify: {
      alertId: cfg.notify?.alertId || '',
      title: cfg.notify?.title || '',
      subjectTemplate: cfg.notify?.subjectTemplate || '',
    },
    delivery: {
      from: email.from || '',
      to: Array.isArray(email.to) ? email.to : (email.to ? [email.to] : []),
    },
    schedule: {
      intervalMinutes: cfg.schedule?.intervalMinutes,
      trigger: cfg.schedule?.trigger || 'interval',
    },
    rule: cfg.rule ? {
      minCount: cfg.rule.minCount,
      mode: cfg.rule.mode,
      groupBy: cfg.rule.groupBy,
      emptyPolicy: cfg.rule.emptyPolicy,
      thresholdMinutes: cfg.rule.thresholdMinutes,
    } : undefined,
    presentation: cfg.presentation ? {
      layout: cfg.presentation.layout,
    } : undefined,
  }
}

export const api = {
  list: async () => {
    const data = await call('list')
    const alerts = (data.alerts || []).map(shapeAlert)
    return {
      alerts,
      engine: shapeEngine({ ...data, alerts }),
    }
  },

  create: (payload) => call('create', payload),

  update: (key, payload) => call('update', { ...payload, alertKey: key || payload.alertKey }),

  remove: (alertKey, extra = {}) => call('delete', { alertKey, ...extra }),

  preview: async (config) => shapePreview(await call('preview', { config })),

  fields: async (index) => {
    const data = await call('fields', { body: { index: String(index || '').trim() } })
    const raw = (data && (data.fields || data.names || data.fieldNames)) || []
    return [...new Set(
      raw.map((f) => (typeof f === 'string' ? f : f?.name || f?.field || '')).filter(Boolean),
    )].sort((a, b) => a.localeCompare(b))
  },

  /**
   * Живой поиск индексов/паттернов в ELK через n8n (action: indices).
   * q — префикс/подстрока; пустая строка → популярные/все (лимит на стороне n8n).
   */
  searchIndices: async (q = '') => {
    const data = await call('indices', { body: { q: String(q || '').trim() } })
    const remote = data.patterns || data.indices || data.names || []
    return [...new Set(remote.map(String).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b))
  },

  indexPatterns: async (alerts = []) => {
    try {
      const remote = await api.searchIndices('')
      return collectIndexPatterns(alerts, remote)
    } catch {
      return collectIndexPatterns(alerts, [])
    }
  },

  // --- Postgres (FastAPI /alerts/*) -----------------------------------------

  /** Полный кэш UI: alerts + engine + indexPatterns. */
  getCache: async () => {
    try {
      const data = await http.get(`${META}/cache`)
      return {
        alerts: (data.alerts || []).map(shapeAlert),
        engine: data.engine || null,
        indexPatterns: data.indexPatterns || [],
      }
    } catch {
      return { alerts: [], engine: null, indexPatterns: [] }
    }
  },

  /** Записать зеркало после list из n8n. */
  putCache: async ({ alerts, engine, indexPatterns }) => {
    try {
      await http.put(`${META}/cache`, {
        alerts: alerts || [],
        engine: engine || null,
        indexPatterns: indexPatterns || [],
      })
    } catch { /* кэш необязателен */ }
  },

  getEngine: async () => {
    try {
      const data = await http.get(`${META}/engine`)
      return data?.engine || null
    } catch {
      return null
    }
  },

  putEngine: async (engine) => {
    if (!engine?.lastRunAt) return null
    try {
      const data = await http.put(`${META}/engine`, {
        lastRunAt: engine.lastRunAt,
        lastError: engine.lastError || null,
      })
      return data?.engine || engine
    } catch {
      return engine
    }
  },

  history: async () => {
    try {
      const data = await http.get(`${META}/history`)
      const items = Array.isArray(data) ? data : (data.history || data.items || [])
      return items.map(shapeHistoryItem)
    } catch {
      return []
    }
  },

  recordHistory: async (entry) => {
    try {
      return await http.post(`${META}/history`, entry)
    } catch {
      return null
    }
  },
}
