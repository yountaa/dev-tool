// СГЕНЕРИРОВАНО из src/lib/normalize.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

const NOTIFY_KEYS = ['alertId', 'title', 'subjectTemplate', 'helpUrl', 'helpLabel', 'timezone'];
const BATCH_RULE_KEYS = ['groupBy', 'parseJson', 'columns', 'minCount', 'emptyPolicy', 'sortDir'];
const SILENCE_SOURCE_KEYS = ['hostField', 'messageField', 'scopeHours', 'lookbackHits'];

function normalizeConfig(raw) {
  if (!raw || typeof raw !== 'object') return { schemaVersion: 3, type: 'batch', source: {}, rule: {}, notify: {} };
  if (Number(raw.schemaVersion) === 3) return raw;

  const cfg = JSON.parse(JSON.stringify(raw));
  const digest = cfg.digest || {};
  const type = cfg.type || 'batch';
  const source = cfg.source || {};
  const rule = {};
  const notify = {};

  if (source.pathQuery && !source.query) source.query = source.pathQuery;
  delete source.pathQuery;

  if (type === 'silence') {
    Object.assign(rule, cfg.silence || {});
    for (const k of SILENCE_SOURCE_KEYS) {
      if (source[k] !== undefined) rule[k] = source[k];
      delete source[k];
    }
  } else {
    for (const k of BATCH_RULE_KEYS) {
      if (digest[k] !== undefined) rule[k] = digest[k];
    }
  }

  for (const k of NOTIFY_KEYS) {
    if (digest[k] !== undefined) notify[k] = digest[k];
  }

  delete cfg.digest;
  delete cfg.silence;

  return Object.assign(cfg, { schemaVersion: 3, type, source, rule, notify });
}

export { normalizeConfig };
