// СГЕНЕРИРОВАНО из src/lib/esbody.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

function buildBatchBody(cfg, win) {
  const s = cfg.source || {};
  const timeField = s.timeField || '@timestamp';
  const body = {
    size: Number(s.maxRows) || 10000,
    track_total_hits: true,
    sort: [{ [timeField]: 'asc' }],
    query: {
      bool: {
        must: [
          s.query || { match_all: {} },
          { range: { [timeField]: { gte: win.from, lte: win.to } } }
        ]
      }
    }
  };
  if (Array.isArray(s.sourceFields) && s.sourceFields.length) body._source = s.sourceFields;
  return { index: s.index, body };
}

// silence: последние логи по каждому узлу одним запросом (terms + top_hits)
function silenceMessageField(r) {
  const raw = String((r && r.messageField) != null ? r.messageField : 'message').trim() || 'message';
  // Произвольный текст письма — не путь ES; в _source берём обычный message.
  if (!/^[@A-Za-z_][@A-Za-z0-9_.*-]*$/.test(raw)) return 'message';
  return raw;
}

function buildSilenceBody(cfg) {
  const s = cfg.source || {};
  const r = cfg.rule || {};
  const timeField = s.timeField || '@timestamp';
  const hostField = r.hostField;
  const messageField = silenceMessageField(r);
  const scopeHours = Number(r.scopeHours) || 24;
  const lookbackHits = Number(r.lookbackHits) || 50;

  const must = [];
  if (s.query) must.push(s.query);
  must.push({ range: { [timeField]: { gte: 'now-' + scopeHours + 'h' } } });

  return {
    index: s.index,
    body: {
      size: 0,
      query: { bool: { must: must } },
      aggs: {
        hosts: {
          terms: { field: hostField, size: Number(r.maxHosts) || 100 },
          aggs: {
            last: {
              top_hits: {
                size: lookbackHits,
                sort: [{ [timeField]: 'desc' }],
                _source: [timeField, messageField, hostField]
              }
            }
          }
        }
      }
    }
  };
}

// any: события строго новее курсора, по возрастанию времени
function buildAnyBody(cfg, win, state) {
  const s = cfg.source || {};
  const timeField = s.timeField || '@timestamp';
  const cursor = (state && state.cursor) || {};
  const gte = cursor.lastEventTs || win.from;

  const body = {
    size: Number(s.maxRows) || 1000,
    track_total_hits: true,
    sort: [{ [timeField]: 'asc' }],
    query: {
      bool: {
        must: [
          s.query || { match_all: {} },
          { range: { [timeField]: { gte: gte, lte: win.to } } }
        ]
      }
    }
  };
  if (Array.isArray(s.sourceFields) && s.sourceFields.length) body._source = s.sourceFields;
  return { index: s.index, body };
}

function buildEsBody(cfg, win, state) {
  const type = (cfg && cfg.type) || 'batch';
  if (type === 'silence') return buildSilenceBody(cfg);
  if (type === 'any') return buildAnyBody(cfg, win, state);
  return buildBatchBody(cfg, win);
}

export { buildEsBody, buildBatchBody, buildSilenceBody, buildAnyBody };
