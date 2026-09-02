// Заготовки на каждый тип: с них удобнее начинать, чем с пустой формы.
export const TEMPLATES = {
  batch: () => ({
    schemaVersion: 3, id: '', type: 'batch', enabled: false,
    schedule: { intervalMinutes: 30 },
    window: { lookbackMinutes: 1440, overlapMinutes: 2, maxHours: 48 },
    source: {
      index: 'omni-prod-*', timeField: '@timestamp', maxRows: 10000,
      sourceFields: ['@timestamp'], query: { bool: { must: [] } }, map: {},
    },
    rule: {
      groupBy: [], parseJson: [],
      columns: [{ key: 'count', label: 'Сколько раз', align: 'center', format: 'plain' }],
      minCount: 1, emptyPolicy: 'skip', sortDir: 'desc',
    },
    notify: {
      alertId: '', title: '', subjectTemplate: '{{title}}: {{alertId}}',
      helpUrl: '', helpLabel: 'инструкция', timezone: 'Europe/Moscow',
    },
    delivery: { email: { from: 'noreply-n8nalerts@hoff.ru', to: [] } },
    presentation: { layout: 'table', tableWidth: 1600, fontSize: 15, headerBg: '#1f2937' },
  }),
  silence: () => ({
    schemaVersion: 3, id: '', type: 'silence', enabled: false,
    schedule: { intervalMinutes: 1 },
    source: { index: '', timeField: '@timestamp', query: { bool: { must: [] } }, map: {} },
    rule: {
      hostField: 'host.name', messageField: 'message',
      thresholdMinutes: 3, repeatMinutes: 15, notifyRecovery: true, forceSend: false,
      standbyMarker: '', ignorePatterns: [], scopeHours: 24, lookbackHits: 50, maxHosts: 100,
    },
    notify: {
      alertId: '', title: '', subjectTemplate: '{{title}} · {{host}}',
      helpUrl: '', helpLabel: 'инструкция', timezone: 'Europe/Moscow',
    },
    delivery: { email: { from: 'noreply-n8nalerts@hoff.ru', to: [] } },
    presentation: { layout: 'card', silenceHiddenRows: [] },
  }),
  any: () => ({
    schemaVersion: 3, id: '', type: 'any', enabled: false,
    schedule: { intervalMinutes: 2 },
    window: { lookbackMinutes: 60 },
    source: {
      index: 'omni-prod-*', timeField: '@timestamp', maxRows: 1000,
      sourceFields: ['@timestamp'], query: { bool: { must: [] } }, map: {},
    },
    rule: {
      mode: 'capped', maxEmailsPerRun: 10, panicThreshold: 1000, dedupeBy: [],
      columns: [{ key: 'count', label: 'Сколько раз', align: 'center', format: 'plain' }],
    },
    notify: { alertId: '', title: '', subjectTemplate: '{{title}}: {{alertId}}', timezone: 'Europe/Moscow' },
    delivery: { email: { from: 'noreply-n8nalerts@hoff.ru', to: [] } },
    presentation: { layout: 'table', tableWidth: 1200, fontSize: 15, headerBg: '#1f2937' },
  }),
}

// Короткие пояснения на человеческом — показываются при выборе типа.
export const TYPE_INFO = {
  batch: {
    title: 'Сводка за период',
    hint: 'Раз в период собирает все подходящие события, схлопывает одинаковые и шлёт одно письмо с таблицей. Подходит, когда ошибок много.',
  },
  silence: {
    title: 'Пропажа логов',
    hint: 'Следит, что логи идут. Если по узлу ничего нет дольше порога — шлёт алерт. Считает по каждому узлу отдельно.',
  },
  any: {
    title: 'Каждое событие',
    hint: 'Шлёт письмо на каждое новое подходящее событие. Помнит отправленное, дублей не будет. Для редких важных ошибок.',
  },
}
