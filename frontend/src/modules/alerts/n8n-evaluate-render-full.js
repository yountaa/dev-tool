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



function normText(s) {
  return String(s == null ? '' : s).replace(/["\u201C\u201D']/g, '').toLowerCase();
}

function getPath(obj, path) {
  if (obj == null) return undefined;
  if (!path) return obj;
  // Сначала целый ключ («host.name» как поле), иначе вложенный путь host → name.
  if (Object.prototype.hasOwnProperty.call(obj, path)) return obj[path];
  return path.split('.').reduce((a, k) => (a == null ? undefined : a[k]), obj);
}

/** Путь ES (message, host.name) vs произвольный текст для письма. */
function isEsFieldPath(s) {
  const v = String(s == null ? '' : s).trim();
  return v !== '' && /^[@A-Za-z_][@A-Za-z0-9_.*-]*$/.test(v);
}

function resolveMessageField(r) {
  const raw = String((r && r.messageField) != null ? r.messageField : 'message').trim() || 'message';
  if (isEsFieldPath(raw)) return { field: raw, fallbackText: '' };
  // Проза в «Поле с текстом лога» — текст в письме; лог читаем из message.
  return { field: 'message', fallbackText: raw };
}

function isNoise(msg, patterns) {
  const m = normText(msg);
  return (patterns || []).some((p) => {
    const n = normText(p);
    return n !== '' && m.includes(n);
  });
}

// Возраст — по самому свежему логу (любому).
// Статус — по ближайшему ЗНАЧАЩЕМУ логу: шумовые строки пропускаются.
function inspectHost(bucket, cfg, now) {
  const s = cfg.source || {};
  const r = cfg.rule || {};
  const timeField = s.timeField || '@timestamp';
  const msgRef = resolveMessageField(r);
  const messageField = msgRef.field;
  const patterns = r.ignorePatterns || [];
  const marker = normText(r.standbyMarker || '');
  const thresholdMs = Number(r.thresholdMinutes || 3) * 60000;

  const hits = (bucket.last && bucket.last.hits && bucket.last.hits.hits) || [];
  const newest = hits[0];
  const hasDoc = !!newest;
  const src = hasDoc ? (newest._source || {}) : {};

  const lastTs = getPath(src, timeField) || null;
  const rawLast = getPath(src, messageField);
  const lastMsg = rawLast == null || rawLast === ''
    ? (msgRef.fallbackText || '')
    : rawLast;
  const lastMs = lastTs ? Date.parse(lastTs) : NaN;
  const ageMs = Number.isNaN(lastMs) ? Infinity : now - lastMs;
  const ageMin = ageMs === Infinity ? null : Math.floor(ageMs / 60000);

  let signalMsg = '';
  let signalTs = null;
  let signalFound = false;
  for (const h of hits) {
    const m = getPath(h._source || {}, messageField);
    if (isNoise(m, patterns)) continue;
    signalMsg = m == null || m === '' ? (msgRef.fallbackText || '') : m;
    signalTs = getPath(h._source || {}, timeField) || null;
    signalFound = true;
    break;
  }
  if (!signalFound && msgRef.fallbackText) {
    signalMsg = msgRef.fallbackText;
  }

  const isStandby = signalFound && marker !== '' && normText(signalMsg).includes(marker);
  const isDown = ageMs > thresholdMs && !isStandby;

  return { host: bucket.key, hasDoc: hasDoc, lastTs: lastTs, lastMsg: lastMsg, ageMin: ageMin,
    signalFound: signalFound, signalTs: signalTs, signalMsg: signalMsg,
    isStandby: isStandby, isDown: isDown, lastWasNoise: hasDoc && isNoise(getPath(src, messageField), patterns),
    fallbackText: msgRef.fallbackText || '', lastSource: src };
}

// Решение по узлу с учётом предыдущего состояния и подавления повторов.
function decideHost(info, prev, cfg, now) {
  const r = cfg.rule || {};
  const repeatMs = Number(r.repeatMinutes != null ? r.repeatMinutes : 15) * 60000;
  const notifyRecovery = r.notifyRecovery !== false;
  const st = prev || { state: 'ok', lastAlertAt: 0, downSince: null };
  let shouldSend = false;
  let kind = null;
  const next = { state: st.state, lastAlertAt: st.lastAlertAt || 0, downSince: st.downSince || null };

  if (r.forceSend) {
    return { shouldSend: true, kind: 'test', next: next };
  }

  if (info.isDown) {
    if (next.state !== 'down') {
      next.state = 'down';
      next.downSince = now;
      next.lastAlertAt = now;
      shouldSend = true;
      kind = 'down';
    } else if (repeatMs > 0 && (now - next.lastAlertAt) >= repeatMs) {
      next.lastAlertAt = now;
      shouldSend = true;
      kind = 'reminder';
    }
  } else if (next.state === 'down') {
    next.state = 'ok';
    next.downSince = null;
    shouldSend = notifyRecovery;
    kind = 'recovery';
  } else {
    next.state = 'ok';
  }

  return { shouldSend: shouldSend, kind: kind, next: next };
}

function evaluateSilence(resp, cfg, state, now) {
  const r = resp || {};
  const aggs = r.aggregations || (r.body && r.body.aggregations) || {};
  const buckets = (aggs.hosts && aggs.hosts.buckets) || [];
  const prevHosts = (state && state.hosts) || {};
  const nextHosts = {};
  const results = [];

  for (const b of buckets) {
    const info = inspectHost(b, cfg, now);
    const d = decideHost(info, prevHosts[info.host], cfg, now);
    nextHosts[info.host] = d.next;
    results.push(Object.assign({}, info, {
      shouldSend: d.shouldSend,
      kind: d.kind || (info.isStandby ? 'standby' : (info.isDown ? 'down' : 'ok'))
    }));
  }

  return { hosts: results, nextState: Object.assign({}, state || {}, { hosts: nextHosts }) };
}



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



// Рендер писем. layout: table | text | card (как silence/zabbix).

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function applyTemplate(tpl, vars) {
  return String(tpl == null ? '' : tpl).replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, name) => {
    const v = vars[name];
    return v == null ? '' : String(v);
  });
}

function splitJsonCell(raw) {
  return escapeHtml(raw).replace(/\},\{/g, '},<br>{');
}

function fmtDateTime(iso, tz) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('ru-RU', {
      timeZone: tz, day: '2-digit', month: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  } catch (_) {
    return String(iso);
  }
}

function periodLine(data, tz) {
  if (!data.from || !data.to) return '';
  return 'Период: <b>' + escapeHtml(fmtDateTime(data.from, tz)) + '</b> — <b>' +
    escapeHtml(fmtDateTime(data.to, tz)) + '</b><br>';
}

function reportDate(tz) {
  try {
    return new Date().toLocaleDateString('ru-RU', { timeZone: tz });
  } catch (_) {
    return new Date().toLocaleDateString('ru-RU');
  }
}

function mailVars(cfg, data) {
  const notify = cfg.notify || {};
  const date = reportDate(notify.timezone || 'Europe/Moscow');
  return {
    alertId: notify.alertId,
    title: notify.title,
    total: data.total,
    unique: data.unique,
    reportDate: date,
  };
}

function helpHtml(notify) {
  return notify.helpUrl
    ? '<br>Решение проблемы: <a href="' + escapeHtml(notify.helpUrl) +
      '" style="color:#2563eb;text-decoration:underline;">' +
      escapeHtml(notify.helpLabel || notify.helpUrl) + '</a>'
    : '';
}

function cellHtml(group, col, fontSize) {
  const raw = group[col.key];
  const content = col.format === 'jsonBreak' ? splitJsonCell(raw) : escapeHtml(raw);
  const align = col.align === 'center' ? 'text-align:center;' : col.align === 'right' ? 'text-align:right;' : '';
  const weight = col.key === 'count' ? 'font-weight:bold;' : '';
  return '<td style="padding:10px 14px;border-bottom:1px solid #eceef0;font-size:' + fontSize +
    'px;color:#333;word-break:break-all;' + align + weight + '">' + content + '</td>';
}

function renderEmailTable(cfg, data) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const pres = cfg.presentation || {};
  const columns = rule.columns || [];
  const fontSize = Number(pres.fontSize) || 15;
  const width = Number(pres.tableWidth) || 1600;
  const headerBg = pres.headerBg || '#1f2937';
  const vars = mailVars(cfg, data);
  const tz = notify.timezone || 'Europe/Moscow';

  const th = columns.map((c) => {
    const a = c.align === 'center' ? 'center' : c.align === 'right' ? 'right' : 'left';
    return '<th align="' + a + '" style="padding:10px 14px;font-size:' + fontSize +
      'px;color:#6b7280;border-bottom:2px solid #e6e8eb;">' + escapeHtml(c.label) + '</th>';
  }).join('');

  const trs = (data.groups || []).map((g, i) => {
    const bg = i % 2 ? '#ffffff' : '#f9fafb';
    return '<tr style="background:' + bg + ';vertical-align:top;">' +
      columns.map((c) => cellHtml(g, c, fontSize)).join('') + '</tr>';
  }).join('');

  const html = '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>' +
    '<body style="margin:0;padding:0;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;">' +
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f6f7f9">' +
    '<tr><td align="center" style="padding:24px 12px;">' +
    '<table width="' + width + '" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" ' +
    'style="border-radius:8px;overflow:hidden;border:1px solid #e6e8eb;">' +
    '<tr><td style="padding:20px 24px;background:' + headerBg +
    ';color:#ffffff;font-size:17px;font-weight:bold;">' + escapeHtml(notify.title) + '</td></tr>' +
    '<tr><td style="padding:18px 24px 4px 24px;color:#444;font-size:16px;line-height:1.5;">' +
    (data.notice ? '<div style="margin-bottom:10px;padding:10px 12px;background:#fbf1dd;' +
      'border-radius:6px;color:#7a5c12;font-size:14px;">' + escapeHtml(data.notice) + '</div>' : '') +
    periodLine(data, tz) +
    'Сформировано: <b>' + escapeHtml(vars.reportDate) + '</b>.<br>Всего событий: <b>' + data.total +
    '</b> &nbsp;·&nbsp; уникальных сочетаний: <b>' + data.unique + '</b>.' + helpHtml(notify) + '</td></tr>' +
    '<tr><td style="padding:12px 24px 24px 24px;">' +
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" ' +
    'style="border-collapse:collapse;border:1px solid #e6e8eb;">' +
    '<tr style="background:#f3f4f6;">' + th + '</tr>' + trs + '</table></td></tr>' +
    '<tr><td align="center" style="padding:14px;background:#fafafa;color:#888;font-size:12px;' +
    'border-top:1px solid #eee;">Это автоматическое уведомление.</td></tr>' +
    '</table></td></tr></table></body></html>';

  return {
    subject: applyTemplate(notify.subjectTemplate || '{{title}}: {{alertId}}', vars),
    html,
  };
}

function renderEmailText(cfg, data) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const columns = rule.columns || [];
  const vars = mailVars(cfg, data);
  const tz = notify.timezone || 'Europe/Moscow';
  const accent = (cfg.presentation || {}).accentColor || '#374151';

  const th = columns.map((c) => {
    const a = c.align === 'center' ? 'center' : c.align === 'right' ? 'right' : 'left';
    return '<th align="' + a + '" style="padding:8px 12px;font-size:12px;color:#6b7280;' +
      'border-bottom:1px solid #d1d5db;font-weight:600;white-space:nowrap;">' +
      escapeHtml(c.label || c.key) + '</th>';
  }).join('');

  const trs = (data.groups || []).map((g, i) => {
    const bg = i % 2 ? '#ffffff' : '#f9fafb';
    const tds = columns.map((c) => {
      const raw = g[c.key];
      const content = c.format === 'jsonBreak' ? splitJsonCell(raw) : escapeHtml(raw);
      const align = c.align === 'center' ? 'text-align:center;' : c.align === 'right' ? 'text-align:right;' : '';
      const weight = c.key === 'count' ? 'font-weight:bold;color:#111827;' : 'color:#1f2937;';
      return '<td style="padding:8px 12px;border-bottom:1px solid #eceef0;font-size:13px;' +
        'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-word;' +
        'vertical-align:top;' + align + weight + '">' + content + '</td>';
    }).join('');
    return '<tr style="background:' + bg + ';">' + tds + '</tr>';
  }).join('');

  const tableBlock = columns.length
    ? '<div style="margin-top:14px;border:1px solid #e5e7eb;border-radius:8px;overflow:hidden;">' +
      '<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;background:#fff;">' +
      '<thead><tr style="background:#f3f4f6;">' + th + '</tr></thead>' +
      '<tbody>' + (trs || '<tr><td colspan="' + columns.length +
        '" style="padding:14px 12px;color:#9ca3af;font-family:ui-monospace,Consolas,monospace;">нет строк</td></tr>') +
      '</tbody></table></div>'
    : '<div style="margin-top:14px;color:#9ca3af;font-size:13px;">Колонки не заданы.</div>';

  const html = '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>' +
    '<body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">' +
    '<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f3f4f6">' +
    '<tr><td align="center" style="padding:24px 12px;">' +
    '<table width="900" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff" ' +
    'style="border-radius:8px;overflow:hidden;border:1px solid #e5e7eb;max-width:100%;">' +
    '<tr><td style="padding:18px 22px;background:' + accent +
    ';color:#ffffff;font-size:16px;font-weight:bold;">' + escapeHtml(notify.title || 'Алерт') + '</td></tr>' +
    '<tr><td style="padding:18px 22px 20px 22px;color:#374151;font-size:14px;line-height:1.55;">' +
    (data.notice ? '<div style="margin-bottom:12px;padding:10px 12px;background:#fef3c7;' +
      'border-radius:6px;color:#92400e;font-size:13px;">' + escapeHtml(data.notice) + '</div>' : '') +
    periodLine(data, tz) +
    'Сформировано: <b>' + escapeHtml(vars.reportDate) + '</b><br>' +
    'Событий: <b>' + data.total + '</b> · уникальных: <b>' + data.unique + '</b>' +
    helpHtml(notify) +
    tableBlock +
    '</td></tr>' +
    '<tr><td align="center" style="padding:12px;background:#fafafa;color:#9ca3af;font-size:12px;' +
    'border-top:1px solid #eee;">Автоматическое уведомление</td></tr>' +
    '</table></td></tr></table></body></html>';

  return {
    subject: applyTemplate(notify.subjectTemplate || '{{title}}: {{alertId}}', vars),
    html,
  };
}

function kvRow(label, value) {
  return '<tr><td style="color:#6b7280;padding:3px 16px 3px 0;white-space:nowrap;">' + escapeHtml(label) +
    '</td><td style="color:#333;">' + value + '</td></tr>';
}

/** Компактная карточка в духе silence/zabbix: цветной заголовок + список строк. */
function renderEmailCard(cfg, data) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const pres = cfg.presentation || {};
  const columns = rule.columns || [];
  const accent = pres.accentColor || '#2563eb';
  const vars = mailVars(cfg, data);
  const tz = notify.timezone || 'Europe/Moscow';

  const groupBlocks = (data.groups || []).map((g, i) => {
    const rows = columns.map((c) => {
      const raw = g[c.key];
      const content = c.format === 'jsonBreak' ? splitJsonCell(raw) : escapeHtml(raw);
      return kvRow(c.label || c.key, content);
    }).join('');
    return '<div style="margin-top:' + (i ? '14px' : '0') + ';padding:12px 14px;background:#f9fafb;' +
      'border-radius:8px;border:1px solid #e6e8eb;">' +
      '<table cellpadding="0" cellspacing="0" style="font-size:14px;">' + rows + '</table></div>';
  }).join('');

  const html = '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"></head>' +
    '<body style="margin:0;padding:0;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;">' +
    '<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">' +
    '<table width="720" cellpadding="0" cellspacing="0" bgcolor="#ffffff" ' +
    'style="border-radius:8px;overflow:hidden;border:1px solid #e6e8eb;">' +
    '<tr><td style="padding:18px 24px;background:' + accent +
    ';color:#fff;font-size:17px;font-weight:bold;">' + escapeHtml(notify.title || 'Алерт') + '</td></tr>' +
    '<tr><td style="padding:18px 24px;font-size:15px;line-height:1.6;">' +
    '<table cellpadding="0" cellspacing="0" style="font-size:15px;">' +
    kvRow('Алерт', escapeHtml(notify.alertId || '')) +
    kvRow('Событий', '<b>' + data.total + '</b> · уник. <b>' + data.unique + '</b>') +
    (data.from && data.to
      ? kvRow('Период', escapeHtml(fmtDateTime(data.from, tz) + ' — ' + fmtDateTime(data.to, tz)))
      : '') +
    '</table>' +
    (data.notice
      ? '<div style="margin-top:10px;padding:8px 12px;background:#fbf1dd;border-radius:6px;' +
        'color:#7a5c12;font-size:13px;">' + escapeHtml(data.notice) + '</div>'
      : '') +
    helpHtml(notify) +
    '<div style="margin-top:14px;color:#6b7280;font-size:13px;">Строки отчёта:</div>' +
    (groupBlocks || '<div style="margin-top:8px;color:#888;">Нет строк</div>') +
    '</td></tr>' +
    '<tr><td align="center" style="padding:12px;background:#fafafa;color:#888;font-size:12px;' +
    'border-top:1px solid #eee;">Автоматическое уведомление</td></tr>' +
    '</table></td></tr></table></body></html>';

  return {
    subject: applyTemplate(notify.subjectTemplate || '{{title}}: {{alertId}}', vars),
    html,
  };
}

function renderEmail(cfg, data) {
  const layout = ((cfg.presentation || {}).layout || 'table').toLowerCase();
  if (layout === 'text') return renderEmailText(cfg, data);
  if (layout === 'card') return renderEmailCard(cfg, data);
  return renderEmailTable(cfg, data);
}

const SILENCE_STYLE = {
  down: { accent: '#b23b3b', tag: 'АЛЕРТ', head: 'Нет логов дольше порога' },
  reminder: { accent: '#b23b3b', tag: 'ВСЁ ЕЩЁ МОЛЧИТ', head: 'Узел по-прежнему молчит' },
  recovery: { accent: '#2f7d5b', tag: 'ВОССТАНОВЛЕНО', head: 'Логи снова поступают' },
  standby: { accent: '#5b6b82', tag: 'STANDBY', head: 'Последний лог standby, алерт подавлен' },
  test: { accent: '#8a6d1b', tag: 'ТЕСТ', head: 'Тестовое письмо' },
  ok: { accent: '#2f7d5b', tag: 'OK', head: 'Логи поступают штатно' },
};

function row(label, value) {
  return kvRow(label, value);
}

/** Плоские поля из _source для {{field}} в тексте silence. */
function flattenSourceVars(src, prefix) {
  const out = {};
  if (!src || typeof src !== 'object') return out;
  for (const [k, v] of Object.entries(src)) {
    const key = prefix ? prefix + '.' + k : k;
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      Object.assign(out, flattenSourceVars(v, key));
    } else if (v != null && v !== '') {
      out[key] = String(v);
    }
  }
  return out;
}

/** Имя поля узла (Worker из hostField) → алиас для {{Worker}} = значение узла. */
function hostFieldAlias(hostField) {
  const f = String(hostField || '').trim();
  if (!f || !/^[@A-Za-z_][@A-Za-z0-9_.*-]*$/.test(f)) return '';
  const parts = f.split('.');
  return parts[parts.length - 1] || '';
}

function silenceMailVars(cfg, info, tz) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const vars = {
    host: info.host || '',
    alertId: notify.alertId || '',
    title: notify.title || '',
    thresholdMinutes: String(rule.thresholdMinutes != null ? rule.thresholdMinutes : 3),
    index: (cfg.source || {}).index || '',
    ageMin: info.ageMin == null ? '' : String(info.ageMin),
    lastLog: info.hasDoc ? fmtDateTime(info.lastTs, tz) : '',
    reportDate: reportDate(tz),
  };
  const alias = hostFieldAlias(rule.hostField);
  if (alias) vars[alias] = vars.host;
  Object.assign(vars, flattenSourceVars(info.lastSource || {}));
  return vars;
}

function silenceRowHidden(cfg, key) {
  const hidden = (cfg.presentation || {}).silenceHiddenRows;
  return Array.isArray(hidden) && hidden.indexOf(key) >= 0;
}

/** Сниппет в silence-письме: произвольный текст + {{переменные}}; иначе — из лога. */
function silenceSnippet(cfg, info, tz) {
  let raw;
  if (info.fallbackText) raw = info.fallbackText;
  else {
    raw = (info.signalFound && info.signalTs !== info.lastTs) ? info.signalMsg : info.lastMsg;
    raw = raw || '';
  }
  if (raw.indexOf('{{') < 0) return raw;
  return applyTemplate(raw, silenceMailVars(cfg, info, tz));
}

function renderSilenceEmail(cfg, info) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const tz = notify.timezone || 'Europe/Moscow';
  const st = SILENCE_STYLE[info.kind] || SILENCE_STYLE.ok;
  const ageText = info.ageMin == null ? 'нет данных' : info.ageMin + ' мин назад';

  const standbyNote = info.isStandby
    ? '<div style="margin-top:10px;padding:8px 12px;background:#fbf1dd;border-radius:6px;' +
      'color:#7a5c12;font-size:13px;">Последний значащий лог узла — standby: в рабочем режиме ' +
      'алерт по этому узлу подавляется.</div>'
    : '';

  const metaRows = [];
  if (!silenceRowHidden(cfg, 'host')) {
    metaRows.push(row('Узел', '<b>' + escapeHtml(info.host) + '</b>'));
  }
  if (!silenceRowHidden(cfg, 'alertId')) {
    metaRows.push(row('Алерт', escapeHtml(notify.alertId || '')));
  }
  if (!silenceRowHidden(cfg, 'threshold')) {
    metaRows.push(row('Порог тишины', escapeHtml(String(rule.thresholdMinutes || 3)) + ' мин'));
  }
  if (!silenceRowHidden(cfg, 'lastLog')) {
    metaRows.push(row('Последний лог', (info.hasDoc ? escapeHtml(fmtDateTime(info.lastTs, tz)) : '—') +
      ' &nbsp;(' + escapeHtml(ageText) + ')'));
  }
  if (info.signalFound && info.signalTs && info.signalTs !== info.lastTs && !silenceRowHidden(cfg, 'signal')) {
    metaRows.push(row('Значащий лог', escapeHtml(fmtDateTime(info.signalTs, tz)) +
      ' <span style="color:#8a97a8;">(шумовые пропущены)</span>'));
  }
  if (!silenceRowHidden(cfg, 'index')) {
    metaRows.push(row('Индекс', escapeHtml((cfg.source || {}).index || '')));
  }

  const snippet = escapeHtml(String(silenceSnippet(cfg, info, tz)).slice(0, 400)) || '—';

  const html = '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"></head>' +
    '<body style="margin:0;padding:0;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;">' +
    '<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">' +
    '<table width="720" cellpadding="0" cellspacing="0" bgcolor="#ffffff" ' +
    'style="border-radius:8px;overflow:hidden;border:1px solid #e6e8eb;">' +
    '<tr><td style="padding:18px 24px;background:' + st.accent +
    ';color:#fff;font-size:17px;font-weight:bold;">' + escapeHtml(st.head + ' · ' + info.host) + '</td></tr>' +
    '<tr><td style="padding:18px 24px;font-size:15px;line-height:1.6;">' +
    (metaRows.length ? '<table cellpadding="0" cellspacing="0" style="font-size:15px;">' + metaRows.join('') + '</table>' : '') +
    standbyNote +
    helpHtml(notify) +
    '<div style="margin-top:12px;color:#6b7280;font-size:13px;">Текст лога:</div>' +
    '<div style="margin-top:4px;font-family:ui-monospace,Consolas,monospace;font-size:12px;' +
    'background:#f3f4f6;border-radius:6px;padding:10px 12px;word-break:break-word;">' + snippet + '</div>' +
    '</td></tr>' +
    '<tr><td align="center" style="padding:12px;background:#fafafa;color:#888;font-size:12px;' +
    'border-top:1px solid #eee;">Автоматическое уведомление</td></tr>' +
    '</table></td></tr></table></body></html>';

  const vars = silenceMailVars(cfg, info, tz);
  vars.total = 1;
  vars.unique = 1;
  const base = applyTemplate(notify.subjectTemplate || '{{title}}: {{host}}', vars);

  return { subject: st.tag + ' · ' + base, html };
}

/** Плоский текст для Telegram (batch/any): заголовок + строки колонок. */
function renderTelegramText(cfg, data) {
  const notify = cfg.notify || {};
  const columns = ((cfg.rule || {}).columns) || [];
  const title = notify.title || notify.alertId || 'Алерт';
  const lines = [];
  lines.push(String(title));

  for (const g of (data.groups || [])) {
    const parts = [];
    for (const c of columns) {
      if (c.key === 'count') {
        const n = Number(g.count);
        if (n > 1) parts.push('(x' + n + ')');
        continue;
      }
      const v = g[c.key];
      if (v == null || v === '') continue;
      parts.push(String(v));
    }
    if (parts.length) lines.push(' ' + parts.join(' , '));
  }

  if (notify.helpUrl) {
    lines.push('');
    lines.push((notify.helpLabel || 'инструкция') + ': ' + notify.helpUrl);
  }

  const text = lines.join('\n');
  // Telegram limit 4096 — режем с запасом, движок может слать частями.
  return text.length > 3500 ? text.slice(0, 3490) + '\n…' : text;
}

/** Текст Telegram для silence-карточки. */
function renderSilenceTelegramText(cfg, info) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const tz = notify.timezone || 'Europe/Moscow';
  const st = SILENCE_STYLE[info.kind] || SILENCE_STYLE.ok;
  const lines = [
    st.head + ' · ' + (info.host || ''),
    'Алерт: ' + (notify.alertId || ''),
    'Порог: ' + String(rule.thresholdMinutes || 3) + ' мин',
  ];
  if (info.hasDoc) lines.push('Последний лог: ' + fmtDateTime(info.lastTs, tz));
  const snip = silenceSnippet(cfg, info, tz);
  if (snip) {
    lines.push('');
    lines.push(String(snip).slice(0, 500));
  }
  if (notify.helpUrl) {
    lines.push('');
    lines.push((notify.helpLabel || 'инструкция') + ': ' + notify.helpUrl);
  }
  return lines.join('\n');
}

/** Payload для alarmbot sendTextFromService. */
function buildTelegramPayload(cfg, text) {
  const tg = ((cfg.delivery || {}).telegram) || {};
  return {
    Authorization: String(tg.authorization || ''),
    groupList: Array.isArray(tg.groupList) ? tg.groupList.slice() : [],
    text: String(text || ''),
  };
}

/**
 * Строки incident-карточки: время, затем — по одной строке на каждое
 * выбранное в правиле ключевое поле (под своим алиасом), пустые пропускаются.
 * «Server» — тоже необязательный: алиас server, если он есть в источнике,
 * попадает в карточку сам, отдельного требования на него нет.
 * Никаких зашитых имён вроде groupName/processorName — ровно то, что выбрал
 * пользователь в «Инцидент — один и тот же, если совпадают».
 */
function incidentRowHidden(cfg, key) {
  const hidden = ((cfg && cfg.presentation) || {}).incidentHiddenRows;
  return Array.isArray(hidden) && hidden.indexOf(key) >= 0;
}

/**
 * Порядок строк карточки. Базовый набор — время, server, поля ключа и
 * presentation.incidentExtraRows; presentation.incidentRowOrder задаёт
 * порядок (что не перечислено — идёт следом), incidentHiddenRows убирает.
 */
function incidentRowList(cfg, keyFields) {
  const pres = (cfg && cfg.presentation) || {};
  const extra = Array.isArray(pres.incidentExtraRows) ? pres.incidentExtraRows : [];
  const order = Array.isArray(pres.incidentRowOrder) ? pres.incidentRowOrder : [];
  const base = ['time', 'server'].concat(keyFields || [], extra);
  const out = [];
  for (const f of order.concat(base)) {
    // В order могут остаться поля, убранные из ключа, — их пропускаем.
    if (!f || base.indexOf(f) < 0) continue;
    if (incidentRowHidden(cfg, f) || out.indexOf(f) >= 0) continue;
    out.push(f);
  }
  return out;
}

function incidentLines(head, row, keyFields, incident, cfg) {
  const val = (k) => (row[k] != null && row[k] !== '' ? row[k] : (incident && incident.fields && incident.fields[k]));
  const lines = [head];
  for (const f of incidentRowList(cfg, keyFields)) {
    if (f === 'time') {
      lines.push('Time: ' + (row._ts || ''));
      continue;
    }
    const v = val(f);
    if (v == null || v === '') continue;
    lines.push((f === 'server' ? 'Server' : f) + ': ' + v);
  }
  return lines;
}

/** Текст Telegram для открытия инцидента (incident). Формат зафиксирован по ТЗ. */
function renderIncidentOpenTelegramText(cfg, ev) {
  const row = ev.row || {};
  const rule = cfg.rule || {};
  const status = row[rule.statusField] || rule.openValue || 'ERROR';
  return incidentLines('🚨 ' + status, row, rule.incidentKey, ev.incident, cfg).join('\n');
}

/** Текст Telegram для авто-закрытия инцидента (incident). Аналог открытия, тег RESOLVED. */
function renderIncidentCloseTelegramText(cfg, ev) {
  const row = ev.row || {};
  const rule = cfg.rule || {};
  const status = row[rule.statusField] || rule.closeValue || 'INFO';
  return incidentLines('✅ RESOLVED (' + status + ')', row, rule.incidentKey, ev.incident, cfg).join('\n');
}

/** Email/card-эквивалент incident: та же карточка, что и silence, другие поля. */
function renderIncidentEmail(cfg, ev) {
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const tz = notify.timezone || 'Europe/Moscow';
  const row = ev.row || {};
  const incident = ev.incident;
  const val = (k) => (row[k] != null && row[k] !== '' ? row[k] : (incident && incident.fields && incident.fields[k]));
  const isOpen = ev.kind === 'open';
  const accent = isOpen ? '#b23b3b' : '#2f7d5b';
  const tag = isOpen ? 'ИНЦИДЕНТ' : 'ЗАКРЫТО';
  const status = row[rule.statusField] || (isOpen ? rule.openValue : rule.closeValue) || '';
  const head = (isOpen ? '🚨 ' : '✅ ') + status;

  const rows = [];
  for (const f of incidentRowList(cfg, rule.incidentKey)) {
    if (f === 'time') {
      rows.push(kvRow('Время', escapeHtml(fmtDateTime(row._ts, tz))));
      continue;
    }
    const v = val(f);
    if (v == null || v === '') continue;
    rows.push(kvRow(f === 'server' ? 'Сервер' : f, escapeHtml(String(v))));
  }

  const html = '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"></head>' +
    '<body style="margin:0;padding:0;background:#f6f7f9;font-family:Arial,Helvetica,sans-serif;">' +
    '<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px;">' +
    '<table width="720" cellpadding="0" cellspacing="0" bgcolor="#ffffff" ' +
    'style="border-radius:8px;overflow:hidden;border:1px solid #e6e8eb;">' +
    '<tr><td style="padding:18px 24px;background:' + accent +
    ';color:#fff;font-size:17px;font-weight:bold;">' + escapeHtml(head) + '</td></tr>' +
    '<tr><td style="padding:18px 24px;font-size:15px;line-height:1.6;">' +
    '<table cellpadding="0" cellspacing="0" style="font-size:15px;">' + rows.join('') + '</table>' +
    helpHtml(notify) +
    '</td></tr>' +
    '<tr><td align="center" style="padding:12px;background:#fafafa;color:#888;font-size:12px;' +
    'border-top:1px solid #eee;">Автоматическое уведомление</td></tr>' +
    '</table></td></tr></table></body></html>';

  // Префикс «ИНЦИДЕНТ ·» / «ЗАКРЫТО ·» отключается галочкой в форме.
  // В шаблоне темы доступны {{tag}} и {{status}}, если нужен свой порядок.
  const vars = {
    host: val('server') || '', alertId: notify.alertId || '',
    title: notify.title || '', tag: tag, status: status,
  };
  const base = applyTemplate(notify.subjectTemplate || '{{title}} · {{host}}', vars);
  const withTag = ((cfg.presentation || {}).incidentSubjectTag) !== false;
  return { subject: withTag ? tag + ' · ' + base : base, html: html };
}



const ctxItems = $('Select Due + Build Body').all();
const now = Date.now();
const out = [];

function baseItem(ctx, cfg, extra) {
  const deliveryRoot = cfg.delivery || {};
  const emailCfg = deliveryRoot.email || {};
  const tgCfg = deliveryRoot.telegram || {};
  const emailOn = emailCfg.enabled !== false;
  const tgOn = tgCfg.enabled === true;
  const toEmail = (emailCfg.to || []).join(', ');

  return Object.assign({
    rowId: ctx.rowId,
    to: ctx.to,
    shouldSend: false,
    failed: false,
    lastError: '',
    stateJson: '',
    subject: '',
    html: '',
    total: 0,
    unique: 0,
    toEmail: toEmail,
    fromEmail: emailCfg.from || 'noreply-n8nalerts@hoff.ru',
    sendEmail: false,
    sendTelegram: false,
    telegramUrl: tgCfg.url || 'http://alarmbot.hoff.ru/sendService/api/telegram/sendTextFromService',
    telegramPayload: null,
    _emailOn: emailOn,
    _tgOn: tgOn,
  }, extra || {});
}

$input.all().forEach((item, i) => {
  const ctx = (ctxItems[i] && ctxItems[i].json) || {};
  const cfg = ctx.cfg || {};
  const notify = cfg.notify || {};
  const rule = cfg.rule || {};
  const type = cfg.type || 'batch';
  const canSend = ctx.canSend !== false;

  if (ctx.invalid || item.json.error) {
    const raw = item.json.error;
    out.push({ json: baseItem(ctx, cfg, {
      failed: true,
      lastError: ctx.error || String((raw && raw.message) || raw || 'неизвестная ошибка')
    }) });
    return;
  }

  if (type === 'silence') {
    const res = evaluateSilence(item.json, cfg, ctx.state || {}, now);
    const stateJson = JSON.stringify(res.nextState);
    const sending = res.hosts.filter(function (h) { return h.shouldSend; });

    if (!sending.length) {
      out.push({ json: baseItem(ctx, cfg, { stateJson: stateJson }) });
      return;
    }
    for (const h of sending) {
      const mail = renderSilenceEmail(cfg, h);
      const outItem = baseItem(ctx, cfg, {
        shouldSend: canSend, stateJson: stateJson,
        subject: mail.subject, html: mail.html, total: 1, unique: 1
      });
      const tgText = renderSilenceTelegramText(cfg, h);
      outItem.sendEmail = canSend && outItem._emailOn && !!outItem.toEmail.trim();
      outItem.sendTelegram = canSend && outItem._tgOn;
      outItem.telegramPayload = outItem.sendTelegram ? buildTelegramPayload(cfg, tgText) : null;
      out.push({ json: outItem });
    }
    return;
  }

  if (type === 'incident') {
    const res = evaluateIncident(item.json, cfg, ctx.state || {}, now);
    const stateJson = JSON.stringify(res.nextState);

    if (!res.events.length) {
      out.push({ json: baseItem(ctx, cfg, { stateJson: stateJson }) });
      return;
    }
    for (const ev of res.events) {
      const mail = renderIncidentEmail(cfg, ev);
      const outItem = baseItem(ctx, cfg, {
        shouldSend: canSend, stateJson: stateJson,
        subject: mail.subject, html: mail.html, total: 1, unique: 1
      });
      const tgText = ev.kind === 'open'
        ? renderIncidentOpenTelegramText(cfg, ev)
        : renderIncidentCloseTelegramText(cfg, ev);
      outItem.sendEmail = canSend && outItem._emailOn && !!outItem.toEmail.trim();
      outItem.sendTelegram = canSend && outItem._tgOn;
      outItem.telegramPayload = outItem.sendTelegram ? buildTelegramPayload(cfg, tgText) : null;
      out.push({ json: outItem });
    }
    return;
  }

  if (type === 'any') {
    const res = evaluateAny(item.json, cfg, ctx.state || {}, now, { from: ctx.from, to: ctx.to });
    const stateJson = JSON.stringify(res.nextState);

    if (!res.emails.length) {
      out.push({ json: baseItem(ctx, cfg, { stateJson: stateJson }) });
      return;
    }
    for (const e of res.emails) {
      const data = { groups: e.groups, total: e.total, unique: e.unique,
        from: ctx.from, to: ctx.to, notice: e.notice };
      const mail = renderEmail(cfg, data);
      const outItem = baseItem(ctx, cfg, {
        shouldSend: canSend, stateJson: stateJson,
        subject: mail.subject, html: mail.html, total: e.total, unique: e.unique
      });
      const tgText = renderTelegramText(cfg, data);
      outItem.sendEmail = canSend && outItem._emailOn && !!outItem.toEmail.trim();
      outItem.sendTelegram = canSend && outItem._tgOn;
      outItem.telegramPayload = outItem.sendTelegram ? buildTelegramPayload(cfg, tgText) : null;
      out.push({ json: outItem });
    }
    return;
  }

  const rows = mapHits(item.json, (cfg.source || {}).map || {});
  const data = aggregateBatch(rows, rule);
  data.from = ctx.from;
  data.to = ctx.to;

  const minCount = Number(rule.minCount != null ? rule.minCount : 1);
  const isEmpty = data.total === 0;
  const shouldSend = isEmpty ? (rule.emptyPolicy === 'send_empty') : (data.total >= minCount);
  const mail = renderEmail(cfg, data);

  const outItem = baseItem(ctx, cfg, {
    shouldSend: shouldSend && canSend,
    subject: isEmpty ? (notify.title + ': событий нет') : mail.subject,
    html: isEmpty ? '' : mail.html,
    total: data.total,
    unique: data.unique
  });
  const tgText = isEmpty ? String(notify.title || '') : renderTelegramText(cfg, data);
  outItem.sendEmail = outItem.shouldSend && outItem._emailOn && !!outItem.toEmail.trim();
  outItem.sendTelegram = outItem.shouldSend && outItem._tgOn;
  outItem.telegramPayload = outItem.sendTelegram ? buildTelegramPayload(cfg, tgText) : null;
  out.push({ json: outItem });
});

return out;