// СГЕНЕРИРОВАНО из src/lib/any.js — не редактировать вручную.
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

// Межпрогонная дедупликация: события строго новее курсора.
// На границе (тот же timestamp) отбрасываем уже виденные _id.
function dropSeen(hits, cursor, timeField) {
  const lastTs = cursor && cursor.lastEventTs;
  if (!lastTs) return hits;
  const seen = new Set((cursor.idsAtLastTs || []));
  return hits.filter((h) => {
    const ts = getPath(h._source || {}, timeField);
    if (ts === lastTs && seen.has(h._id)) return false;
    return true;
  });
}

function makeCursor(hits, timeField, fallbackIso) {
  if (!hits.length) return { lastEventTs: fallbackIso, idsAtLastTs: [] };
  let maxTs = null;
  for (const h of hits) {
    const ts = getPath(h._source || {}, timeField);
    if (ts && (maxTs === null || ts > maxTs)) maxTs = ts;
  }
  const ids = hits.filter((h) => getPath(h._source || {}, timeField) === maxTs).map((h) => h._id);
  return { lastEventTs: maxTs || fallbackIso, idsAtLastTs: ids };
}

function toRows(hits, map) {
  const fields = Object.entries(map || {});
  return hits.map((h) => {
    const src = h._source || {};
    const row = {};
    for (const [outKey, esPath] of fields) {
      const v = getPath(src, esPath);
      row[outKey] = v == null ? '' : v;
    }
    return row;
  });
}

// Внутрипрогонная дедупликация: схлопывание одинаковых событий за один прогон.
function groupRows(rows, dedupeBy) {
  if (!Array.isArray(dedupeBy) || !dedupeBy.length) {
    return rows.map((r) => Object.assign({}, r, { count: 1 }));
  }
  const map = new Map();
  for (const row of rows) {
    const key = dedupeBy.map((f) => String(row[f] == null ? '' : row[f])).join('||');
    let g = map.get(key);
    if (!g) {
      g = Object.assign({}, row, { count: 0 });
      map.set(key, g);
    }
    g.count += 1;
  }
  return Array.from(map.values());
}

function evaluateAny(resp, cfg, state, now, win) {
  const s = cfg.source || {};
  const r = cfg.rule || {};
  const timeField = s.timeField || '@timestamp';
  const mode = r.mode || 'capped';
  const maxEmails = Number(r.maxEmailsPerRun != null ? r.maxEmailsPerRun : 10);
  const panicThreshold = Number(r.panicThreshold != null ? r.panicThreshold : 1000);
  const cursor = (state && state.cursor) || {};
  const nowIso = new Date(now).toISOString();
  const winTo = (win && win.to) || nowIso;

  const all = collectHits(resp);
  const fresh = dropSeen(all, cursor, timeField);
  const nextCursor = makeCursor(all.length ? all : [], timeField, cursor.lastEventTs || winTo);
  const nextState = Object.assign({}, state || {}, { cursor: nextCursor });

  // Холодный старт: курсора не было — не рассылаем историю, только отмечаемся.
  if (!cursor.lastEventTs) {
    return {
      coldStart: true,
      total: fresh.length,
      emails: [{ kind: 'activation', groups: [], total: 0, unique: 0,
        notice: 'Алерт активирован, отсчёт начат. События до этого момента не рассылаются.' }],
      nextState: Object.assign({}, nextState, { cursor: makeCursor(all, timeField, winTo) })
    };
  }

  if (!fresh.length) {
    return { coldStart: false, total: 0, emails: [], nextState: nextState };
  }

  const rows = toRows(fresh, s.map || {});
  const groups = groupRows(rows, r.dedupeBy);

  // Предохранитель: слишком много совпадений — не рассылаем поштучно.
  if (fresh.length > panicThreshold) {
    return {
      coldStart: false, panic: true, total: fresh.length,
      emails: [{ kind: 'panic', groups: groups.slice(0, 50), total: fresh.length, unique: groups.length,
        notice: 'Сработал предохранитель: ' + fresh.length + ' совпадений за прогон при пороге ' +
          panicThreshold + '. Поштучная рассылка отменена, вероятно фильтр слишком широкий.' }],
      nextState: nextState
    };
  }

  if (mode === 'digestPerRun') {
    return {
      coldStart: false, total: fresh.length,
      emails: [{ kind: 'digest', groups: groups, total: fresh.length, unique: groups.length }],
      nextState: nextState
    };
  }

  // perEvent и capped: по письму на группу, но не больше maxEmails
  const emails = [];
  const head = groups.slice(0, maxEmails);
  for (const g of head) {
    emails.push({ kind: 'event', groups: [g], total: g.count, unique: 1 });
  }
  const tail = groups.slice(maxEmails);
  if (tail.length) {
    const suppressed = tail.reduce((sum, g) => sum + g.count, 0);
    emails.push({ kind: 'summary', groups: tail, total: suppressed, unique: tail.length,
      notice: 'Подавлено ' + suppressed + ' событий (' + tail.length +
        ' сочетаний): достигнут лимит ' + maxEmails + ' писем за прогон.' });
  }

  return { coldStart: false, total: fresh.length, emails: emails, nextState: nextState };
}

export { evaluateAny, dropSeen, makeCursor, groupRows };
