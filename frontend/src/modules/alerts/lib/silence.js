// СГЕНЕРИРОВАНО из src/lib/silence.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

function normText(s) {
  return String(s == null ? '' : s).replace(/["\u201C\u201D']/g, '').toLowerCase();
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
  const messageField = r.messageField || 'message';
  const patterns = r.ignorePatterns || [];
  const marker = normText(r.standbyMarker || '');
  const thresholdMs = Number(r.thresholdMinutes || 3) * 60000;

  const hits = (bucket.last && bucket.last.hits && bucket.last.hits.hits) || [];
  const newest = hits[0];
  const hasDoc = !!newest;
  const src = hasDoc ? (newest._source || {}) : {};

  const lastTs = src[timeField] || null;
  const lastMsg = src[messageField] == null ? '' : src[messageField];
  const lastMs = lastTs ? Date.parse(lastTs) : NaN;
  const ageMs = Number.isNaN(lastMs) ? Infinity : now - lastMs;
  const ageMin = ageMs === Infinity ? null : Math.floor(ageMs / 60000);

  let signalMsg = '';
  let signalTs = null;
  let signalFound = false;
  for (const h of hits) {
    const m = (h._source || {})[messageField];
    if (isNoise(m, patterns)) continue;
    signalMsg = m == null ? '' : m;
    signalTs = (h._source || {})[timeField] || null;
    signalFound = true;
    break;
  }

  const isStandby = signalFound && marker !== '' && normText(signalMsg).includes(marker);
  const isDown = ageMs > thresholdMs && !isStandby;

  return { host: bucket.key, hasDoc: hasDoc, lastTs: lastTs, lastMsg: lastMsg, ageMin: ageMin,
    signalFound: signalFound, signalTs: signalTs, signalMsg: signalMsg,
    isStandby: isStandby, isDown: isDown, lastWasNoise: hasDoc && isNoise(lastMsg, patterns) };
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

export { evaluateSilence, inspectHost, decideHost, isNoise, normText };
