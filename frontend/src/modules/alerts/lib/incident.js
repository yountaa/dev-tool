// СГЕНЕРИРОВАНО из src/lib/incident.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

function getPath(obj, path) {
  if (obj == null) return undefined;
  if (!path) return obj;
  if (Object.prototype.hasOwnProperty.call(obj, path)) return obj[path];
  return path.split('.').reduce((a, k) => (a == null ? undefined : a[k]), obj);
}

function collectHits(resp) {
  const r = resp || {};
  return (r.hits && r.hits.hits) || (r.body && r.body.hits && r.body.hits.hits) || [];
}

/** Строка окна по map-алиасам + отдельно время события (не обязано быть в map). */
function toRow(hit, map, timeField) {
  const src = hit._source || {};
  const row = {};
  for (const [alias, path] of Object.entries(map || {})) {
    const v = getPath(src, path);
    row[alias] = v == null ? '' : v;
  }
  row._ts = getPath(src, timeField) || null;
  return row;
}

function incidentKeyOf(row, keyFields) {
  const parts = (keyFields || []).map((f) => String(row[f] == null ? '' : row[f]));
  // Все поля ключа пустые — не с чем сопоставлять. Без этой проверки
  // ключ из двух пустых полей даёт "||", и все такие строки слипаются
  // в один инцидент, который закрывает любая INFO без этих полей.
  if (parts.every((p) => p === '')) return '';
  return parts.join('||');
}

/**
 * Инциденты по составному ключу: первое событие статуса «открыть» с этим
 * ключом заводит инцидент, повторы того же ключа игнорируются, первое
 * событие статуса «закрыть» с тем же ключом закрывает его. Курсор не нужен —
 * окно логов приходит целиком (как у batch), а идемпотентность даёт сама
 * карта открытых инцидентов: повторная обработка того же события на стыке
 * перехлёстывающихся окон ничего не меняет.
 */
function evaluateIncident(resp, cfg, state, now) {
  const s = cfg.source || {};
  const r = cfg.rule || {};
  const timeField = s.timeField || '@timestamp';
  const statusField = r.statusField || 'status';
  const openValue = String(r.openValue != null ? r.openValue : 'ERROR');
  const closeValue = String(r.closeValue != null ? r.closeValue : 'INFO');
  const keyFields = Array.isArray(r.incidentKey) ? r.incidentKey : [];
  const noCloseField = r.noAutoCloseField || '';

  const hits = collectHits(resp);
  const incidents = Object.assign({}, (state && state.incidents) || {});
  const events = [];

  for (const h of hits) {
    const row = toRow(h, s.map || {}, timeField);
    const status = String(row[statusField] == null ? '' : row[statusField]);
    if (status !== openValue && status !== closeValue) continue;
    const key = incidentKeyOf(row, keyFields);
    if (!key) continue; // все поля ключа пустые — не с чем сопоставлять инцидент

    if (status === openValue) {
      if (incidents[key]) continue; // уже открыт этим ключом — повторов не шлём
      const noAutoClose = !!noCloseField && String(row[noCloseField] == null ? '' : row[noCloseField]) === '';
      const incident = {
        state: 'firing', openedAt: row._ts || null,
        fields: Object.assign({}, row), noAutoClose: noAutoClose,
      };
      incidents[key] = incident;
      events.push({ kind: 'open', key: key, row: row, incident: incident });
    } else if (status === closeValue) {
      const incident = incidents[key];
      if (!incident || incident.noAutoClose) continue;
      delete incidents[key];
      events.push({ kind: 'close', key: key, row: row, incident: incident });
    }
  }

  return {
    events: events,
    nextState: Object.assign({}, state || {}, { incidents: incidents }),
  };
}

export { evaluateIncident, incidentKeyOf, toRow, collectHits };