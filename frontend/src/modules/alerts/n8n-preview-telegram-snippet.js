// ─── Вставка в ноду «Preview Render1» (n8n) ─────────────────────────────
// Цель: в каждом элементе emails[] добавить telegramText — тогда UI
// покажет вкладку Telegram в предпросмотре.
//
// 1) Если в Preview Render1 ещё нет renderTelegramText / renderSilenceTelegramText /
//    buildTelegramPayload — скопируйте их из «Evaluate + Render» (или из
//    frontend/src/modules/alerts/lib/render.js). Важно: join('\n'), не '\\n'.
//
// 2) В трёх местах сборки emails замените объекты так:

// ── silence ──
// было:
//   return { subject: mail.subject, html: mail.html, wouldSend: h.shouldSend,
//     label: h.host + ' — ' + h.kind };
// стало:
/*
    const tgText = renderSilenceTelegramText(cfg, h);
    return {
      subject: mail.subject,
      html: mail.html,
      telegramText: tgText,
      wouldSend: h.shouldSend,
      label: h.host + ' — ' + h.kind,
    };
*/

// ── any ──
// было:
//   return { subject: mail.subject, html: mail.html, wouldSend: true,
//     label: e.kind + ' · событий: ' + e.total };
// стало:
/*
    const tgText = renderTelegramText(cfg, data);
    return {
      subject: mail.subject,
      html: mail.html,
      telegramText: tgText,
      wouldSend: true,
      label: e.kind + ' · событий: ' + e.total,
    };
*/

// ── batch ──
// было:
//   emails: [{ subject: mail.subject, html: mail.html, wouldSend: wouldSend,
//     label: 'дайджест · событий: ' + data.total }],
// стало:
/*
  const tgText = isEmpty ? String((cfg.notify || {}).title || '') : renderTelegramText(cfg, data);
  return [{ json: base({
    emails: [{
      subject: mail.subject,
      html: mail.html,
      telegramText: tgText,
      wouldSend: wouldSend,
      label: 'дайджест · событий: ' + data.total,
    }],
    total: data.total,
    unique: data.unique,
    note: data.total === 0 ? 'За окно событий не найдено' : null,
  }) }];
*/
