// Человекочитаемый preview фильтра (стиль Kibana Add filter).
import { OPS } from './query.js'

function opLabel(op) {
  const spec = OPS.find((o) => o.op === op)
  if (!spec) return op
  const map = {
    is: 'is',
    is_not: 'is not',
    contains: 'contains',
    starts_with: 'starts with',
    one_of: 'is one of',
    regex: 'matches',
    exists: 'exists',
    missing: 'does not exist',
  }
  return map[op] || spec.label
}

function fmtVal(v) {
  if (Array.isArray(v)) return v.join(', ')
  return String(v ?? '')
}

function clausePreview(c) {
  if (!c?.field) return ''
  const op = c.op
  if (op === 'exists') return `${c.field}: exists`
  if (op === 'missing') return `NOT ${c.field}: exists`
  const v = fmtVal(c.value)
  if (!v && op !== 'exists' && op !== 'missing') return ''
  if (op === 'is') return `${c.field}: ${v}`
  if (op === 'is_not') return `NOT ${c.field}: ${v}`
  if (op === 'contains') return `${c.field}: "${v}"`
  if (op === 'starts_with') return `${c.field}: ${v}*`
  if (op === 'one_of') return `${c.field}: (${v})`
  if (op === 'regex') return `${c.field}: /${v}/`
  return `${c.field} ${opLabel(op)} ${v}`
}

/** Preview из конструктора условий. */
export function previewFromModel(model) {
  const conditions = (model?.conditions || []).filter((c) => c?.field)
  if (!conditions.length) return ''
  const joiner = model.combinator === 'any' ? ' OR ' : ' AND '
  const parts = conditions.map(clausePreview).filter(Boolean)
  if (!parts.length) return ''
  return parts.join(joiner)
}

/** Preview из KQL-строки (если задана отдельно). */
export function previewFromKql(kql) {
  return String(kql || '').trim()
}

/** Объединённый preview: filter + kql. */
export function previewFilter({ filter, kql, model }) {
  const parts = []
  const fromModel = previewFromModel(model)
  if (fromModel) parts.push(fromModel)
  const k = previewFromKql(kql)
  if (k && k !== fromModel) parts.push(k)
  return parts.join(' AND ')
}
