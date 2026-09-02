// СГЕНЕРИРОВАНО из src/lib/render.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

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

export { renderEmail, renderSilenceEmail, applyTemplate, escapeHtml, fmtDateTime };
