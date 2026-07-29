// СГЕНЕРИРОВАНО из src/lib/batch.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

function getByPath(obj, path) {
  if (obj == null) return undefined
  if (!path) return obj
  if (Object.prototype.hasOwnProperty.call(obj, path)) return obj[path]
  return path.split('.').reduce((a, k) => (a == null ? undefined : a[k]), obj)
}

/** Значение для таблицы: массивы/объекты не превращаем в "[object Object]". */
function cellValue(v) {
  if (v == null) return ''
  if (Array.isArray(v)) {
    if (!v.length) return ''
    return v.map(cellValue).filter((x) => x !== '').join(', ')
  }
  if (typeof v === 'object') {
    try { return JSON.stringify(v) } catch (_) { return String(v) }
  }
  return v
}

/**
 * Достаёт поля из hits.
 * В строку пишем и alias, и путь ES — чтобы groupBy/columns находили значение
 * в любом виде (частая причина пустых ячеек).
 */
function mapHits(resp, map) {
  const r = resp || {}
  const hits = (r.hits && r.hits.hits) || (r.body && r.body.hits && r.body.hits.hits) || []
  const fields = Object.entries(map || {})
  return hits.map((h) => {
    const src = h._source || h
    const row = {}
    for (const [outKey, esPath] of fields) {
      const raw = getByPath(src, esPath)
      const val = cellValue(raw)
      if (outKey) row[outKey] = val
      if (esPath && esPath !== outKey) row[esPath] = val
    }
    return row
  })
}

function parseJsonField(raw) {
  const s = String(raw == null ? '' : raw).trim()
  if (!s) return null
  try {
    const parsed = JSON.parse(s)
    return Array.isArray(parsed) ? parsed : [parsed]
  } catch (_) { /* ниже */ }
  try {
    return JSON.parse('[' + s + ']')
  } catch (_) {
    return null
  }
}

function canonicalValue(v) {
  if (Array.isArray(v)) return v.map(canonicalValue)
  if (v && typeof v === 'object') {
    const out = {}
    for (const k of Object.keys(v).sort()) out[k] = canonicalValue(v[k])
    return out
  }
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return v
}

function canonicalKey(raw) {
  const items = parseJsonField(raw)
  if (!items) return 'raw::' + String(raw == null ? '' : raw)
  return JSON.stringify(canonicalValue(items))
}

function rowField(row, f) {
  if (!row) return ''
  if (row[f] != null && row[f] !== '') return row[f]
  return row[f] == null ? '' : String(row[f])
}

function aggregateBatch(rows, rule) {
  const groupBy = (rule && rule.groupBy) || []
  const parseSet = new Set((rule && rule.parseJson) || [])
  const sortDir = (rule && rule.sortDir) || 'desc'
  const map = new Map()

  for (const row of rows || []) {
    const key = groupBy
      .map((f) => (parseSet.has(f) ? canonicalKey(rowField(row, f)) : String(rowField(row, f))))
      .join('||')
    let g = map.get(key)
    if (!g) {
      g = { count: 0 }
      for (const f of groupBy) g[f] = String(rowField(row, f))
      map.set(key, g)
    }
    g.count += 1
  }

  const groups = Array.from(map.values())
  groups.sort((a, b) => (sortDir === 'asc' ? a.count - b.count : b.count - a.count))
  const total = groups.reduce((s, g) => s + g.count, 0)

  return { groups, total, unique: groups.length }
}

export { mapHits, aggregateBatch, getByPath, parseJsonField, canonicalKey, cellValue };
