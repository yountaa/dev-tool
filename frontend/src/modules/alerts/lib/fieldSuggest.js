// Подсказки имён полей ELK без отдельного action fields в n8n:
// копим то, что уже встречалось в алертах и что пользователь вводил сам.

const SEED = [
  '@timestamp',
  'message',
  'MessageTemplate',
  'host.name',
  'log.file.path',
  'kubernetes.container.name',
  'kubernetes.namespace',
  'kubernetes.pod.name',
  'kubernetes.deployment.name',
  'event.dataset',
  'log.level',
  'service.name',
  'error.message',
  'requestPath',
  'responseBody',
  'hoff_zone',
  'hoff_business_unit_id',
]

function walkQueryFields(query, into) {
  if (!query || typeof query !== 'object') return
  if (Array.isArray(query)) {
    for (const item of query) walkQueryFields(item, into)
    return
  }
  for (const [k, v] of Object.entries(query)) {
    if (k === 'field' && typeof v === 'string') into.add(v)
    else if (v && typeof v === 'object') {
      // term / match / exists: { "host.name": ... }
      if (['term', 'terms', 'match', 'match_phrase', 'wildcard', 'prefix', 'regexp', 'exists'].includes(k)) {
        for (const fk of Object.keys(v)) {
          if (fk !== 'boost' && fk !== 'value') into.add(fk)
        }
      }
      walkQueryFields(v, into)
    }
  }
}

/** Собрать имена полей из одного конфига. */
export function harvestFromConfig(cfg) {
  const into = new Set()
  if (!cfg) return into
  const src = cfg.source || {}
  const map = src.map || {}
  for (const path of Object.values(map)) if (path) into.add(String(path))
  for (const alias of Object.keys(map)) if (alias) into.add(String(alias))
  for (const f of src.sourceFields || []) if (f) into.add(String(f))
  if (src.timeField) into.add(src.timeField)
  walkQueryFields(src.query, into)
  const rule = cfg.rule || {}
  if (rule.hostField) into.add(rule.hostField)
  if (rule.messageField) into.add(rule.messageField)
  for (const g of rule.groupBy || []) if (g) into.add(String(g))
  for (const p of rule.parseJson || []) if (p) into.add(String(p))
  for (const d of rule.dedupeBy || []) if (d) into.add(String(d))
  return into
}

/** Начальный список + поля из всех алертов. */
export function buildFieldHints(alerts = []) {
  const into = new Set(SEED)
  for (const a of alerts) {
    for (const name of harvestFromConfig(a.config || a)) into.add(name)
  }
  return [...into].filter(Boolean).sort((a, b) => a.localeCompare(b))
}

export function mergeFieldHints(current, extra) {
  const into = new Set(current || [])
  for (const n of extra || []) if (n) into.add(String(n).trim())
  return [...into].filter(Boolean).sort((a, b) => a.localeCompare(b))
}

/** Для datalist / ConditionsEditor: [{ name }]. */
export function asFieldOptions(names) {
  return (names || []).map((name) => ({ name }))
}
