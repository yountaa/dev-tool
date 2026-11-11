
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
