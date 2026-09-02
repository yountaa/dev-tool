// Дописывает digest/silence из schema v3 (rule/notify), чтобы движок n8n,
// который ещё читает cfg.digest.emptyPolicy / groupBy / …, получал те же значения.
// Форма и preview живут в v3; без этого «слать пустой отчёт» в UI не доходит до shouldSend.

const NOTIFY_KEYS = ['alertId', 'title', 'subjectTemplate', 'helpUrl', 'helpLabel', 'timezone']
const BATCH_RULE_KEYS = ['groupBy', 'parseJson', 'columns', 'minCount', 'emptyPolicy', 'sortDir']

export function forEngine(cfg) {
  if (!cfg || typeof cfg !== 'object') return cfg
  const out = JSON.parse(JSON.stringify(cfg))
  const type = out.type || 'batch'
  const notify = out.notify || {}
  const rule = out.rule || {}

  const digest = { ...(out.digest || {}) }
  for (const k of NOTIFY_KEYS) {
    if (notify[k] === undefined) continue
    // Пустая строка должна сбрасывать digest.*, иначе старый helpUrl остаётся в n8n.
    if (notify[k] !== '') digest[k] = notify[k]
    else delete digest[k]
  }

  if (type === 'silence') {
    const silence = { ...(out.silence || {}), ...rule }
    out.silence = silence
    // движок silence тоже берёт title/alertId из digest
    out.digest = digest
  } else {
    for (const k of BATCH_RULE_KEYS) {
      if (rule[k] !== undefined) digest[k] = rule[k]
    }
    out.digest = digest
  }

  if (out.source) {
    const filter = out.source.filter
    const kql = String(out.source.kql || '').trim()
    const must = []
    if (filter && filter.bool) {
      if (Array.isArray(filter.bool.must) && filter.bool.must.length) must.push(...filter.bool.must)
      else if (hasAnyBool(filter.bool)) must.push(filter)
    } else if (filter && typeof filter === 'object' && Object.keys(filter).length) {
      must.push(filter)
    }
    if (kql) must.push({ query_string: { query: kql, default_operator: 'AND' } })
    if (must.length) {
      const bool = { must }
      if (filter?.bool?.must_not?.length) bool.must_not = filter.bool.must_not
      out.source.query = { bool }
    } else if (!out.source.query) {
      out.source.query = { bool: { must: [] } }
    }
  }

  // UI хранит lookbackMinutes; n8n пока читает lookbackHours (дробные значения — ок).
  if (out.window && out.window.lookbackMinutes != null) {
    out.window.lookbackHours = Number(out.window.lookbackMinutes) / 60
  }

  return out
}

function hasAnyBool(b) {
  return !!(
    (Array.isArray(b.must) && b.must.length) ||
    (Array.isArray(b.must_not) && b.must_not.length) ||
    (Array.isArray(b.should) && b.should.length)
  )
}
