// СГЕНЕРИРОВАНО из src/lib/query.js — не редактировать вручную.
// Правь исходник и запускай npm run build.

// Двусторонний перевод между простым списком условий и ES DSL.
//
// Форма условия: { field, op, value }
//   is / is_not      — точное совпадение (term)
//   contains         — query_string "фраза" по полю
//                      (wildcard на analyzed text часто даёт 0 hits)
//   starts_with      — префикс (prefix)
//   one_of           — любое из списка (terms), value — массив
//   exists / missing — поле заполнено / пустое
//   regex            — регулярное выражение (regexp)
//
// Всё, что не раскладывается в этот набор, остаётся сырым JSON.

var OPS = [
  { op: 'is', label: 'равно', needsValue: true },
  { op: 'is_not', label: 'не равно', needsValue: true },
  { op: 'contains', label: 'содержит', needsValue: true },
  { op: 'starts_with', label: 'начинается с', needsValue: true },
  { op: 'one_of', label: 'одно из', needsValue: true, multi: true },
  { op: 'regex', label: 'регулярное выражение', needsValue: true },
  { op: 'exists', label: 'заполнено', needsValue: false },
  { op: 'missing', label: 'пустое', needsValue: false }
];

function singleKey(obj) {
  if (!obj || typeof obj !== 'object') return null;
  var keys = Object.keys(obj);
  return keys.length === 1 ? keys[0] : null;
}

function escapeQsPhrase(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

/** Содержит → query_string по полю (как строка поиска в Kibana). */
function containsClause(field, value) {
  return {
    query_string: {
      default_field: field,
      query: '"' + escapeQsPhrase(value) + '"',
      default_operator: 'AND',
    },
  };
}

// Один элемент must/must_not → условие, или null если не раскладывается.
function clauseToCondition(clause, negated) {
  var kind = singleKey(clause);
  if (!kind) return null;
  var inner = clause[kind];

  if (kind === 'term' || kind === 'match_phrase' || kind === 'prefix' || kind === 'regexp' ||
      kind === 'wildcard' || kind === 'terms' || kind === 'exists') {
    if (kind === 'exists') {
      var f = inner && inner.field;
      if (!f) return null;
      return { field: f, op: negated ? 'missing' : 'exists', value: '' };
    }

    var field = singleKey(inner);
    if (!field) return null;
    var raw = inner[field];
    var val = (raw && typeof raw === 'object' && !Array.isArray(raw) && 'value' in raw)
      ? raw.value : raw;

    if (kind === 'terms') {
      if (!Array.isArray(val)) return null;
      if (negated) return null;
      return { field: field, op: 'one_of', value: val };
    }
    if (kind === 'term') return { field: field, op: negated ? 'is_not' : 'is', value: val };
    if (negated) return null;

    // Старые match_phrase → contains (тот же смысл для пользователя).
    if (kind === 'match_phrase') return { field: field, op: 'contains', value: val };
    if (kind === 'prefix') return { field: field, op: 'starts_with', value: val };
    if (kind === 'regexp') return { field: field, op: 'regex', value: val };
    if (kind === 'wildcard') {
      var s = String(val == null ? '' : val);
      if (s.length > 2 && s.charAt(0) === '*' && s.charAt(s.length - 1) === '*' &&
          s.indexOf('*', 1) === s.length - 1) {
        return { field: field, op: 'contains', value: s.slice(1, -1) };
      }
      if (s.length > 1 && s.charAt(s.length - 1) === '*' && s.indexOf('*') === s.length - 1) {
        return { field: field, op: 'starts_with', value: s.slice(0, -1) };
      }
      return null;
    }
  }

  // query_string: default_field + "фраза" → contains
  if (kind === 'query_string' && inner && typeof inner.query === 'string' && !negated) {
    var q = inner.query.trim();
    var df = inner.default_field;
    var mPhrase = q.match(/^"([\s\S]+)"$/);
    if (df && mPhrase) return { field: df, op: 'contains', value: mPhrase[1] };
    // field:"value" или field:*CODE*
    var mField = q.match(/^([\w.@-]+):\*([^*"]+)\*$/);
    if (mField) return { field: mField[1], op: 'contains', value: mField[2] };
    var mFieldPhrase = q.match(/^([\w.@-]+):"([\s\S]+)"$/);
    if (mFieldPhrase) return { field: mFieldPhrase[1], op: 'contains', value: mFieldPhrase[2] };
    return null;
  }

  return null;
}

function toConditions(query) {
  if (!query || typeof query !== 'object') {
    return { mode: 'simple', combinator: 'all', conditions: [] };
  }
  var bool = query.bool;
  if (!bool) {
    var single = clauseToCondition(query, false);
    if (single) return { mode: 'simple', combinator: 'all', conditions: [single] };
    return { mode: 'raw' };
  }

  var allowed = ['must', 'must_not', 'should', 'minimum_should_match'];
  for (var k in bool) {
    if (Object.prototype.hasOwnProperty.call(bool, k) && allowed.indexOf(k) < 0) return { mode: 'raw' };
  }

  var must = bool.must || [];
  var mustNot = bool.must_not || [];
  var should = bool.should || [];
  if (!Array.isArray(must) || !Array.isArray(mustNot) || !Array.isArray(should)) return { mode: 'raw' };

  if (should.length && (must.length || mustNot.length)) return { mode: 'raw' };

  var combinator = should.length ? 'any' : 'all';
  var list = should.length ? should : must;
  var out = [];

  for (var i = 0; i < list.length; i++) {
    var c = clauseToCondition(list[i], false);
    if (!c) return { mode: 'raw' };
    out.push(c);
  }
  for (var j = 0; j < mustNot.length; j++) {
    var n = clauseToCondition(mustNot[j], true);
    if (!n) return { mode: 'raw' };
    out.push(n);
  }

  return { mode: 'simple', combinator: combinator, conditions: out };
}

function conditionToClause(c) {
  var field = c.field;
  var v = c.value;
  switch (c.op) {
    case 'is': return { clause: { term: { [field]: v } } };
    case 'is_not': return { clause: { term: { [field]: v } }, negate: true };
    case 'contains': return { clause: containsClause(field, v) };
    case 'starts_with': return { clause: { prefix: { [field]: v } } };
    case 'matches_phrase': return { clause: containsClause(field, v) }; // legacy op → contains
    case 'regex': return { clause: { regexp: { [field]: v } } };
    case 'one_of': return { clause: { terms: { [field]: Array.isArray(v) ? v : [v] } } };
    case 'exists': return { clause: { exists: { field: field } } };
    case 'missing': return { clause: { exists: { field: field } }, negate: true };
    default: return null;
  }
}

function toQuery(model) {
  var conditions = (model && model.conditions) || [];
  var combinator = (model && model.combinator) || 'all';
  var must = [];
  var mustNot = [];
  var should = [];

  for (var i = 0; i < conditions.length; i++) {
    var c = conditions[i];
    if (!c || !c.field) continue;
    var needsValue = true;
    for (var k = 0; k < OPS.length; k++) if (OPS[k].op === c.op) needsValue = OPS[k].needsValue;
    if (needsValue && (c.value === '' || c.value == null ||
        (Array.isArray(c.value) && !c.value.length))) continue;

    var built = conditionToClause(c);
    if (!built) continue;
    if (built.negate) mustNot.push(built.clause);
    else if (combinator === 'any') should.push(built.clause);
    else must.push(built.clause);
  }

  var bool = {};
  if (must.length) bool.must = must;
  if (mustNot.length) bool.must_not = mustNot;
  if (should.length) {
    bool.should = should;
    bool.minimum_should_match = 1;
  }
  if (!Object.keys(bool).length) return { bool: { must: [] } };
  return { bool: bool };
}

function describe(model) {
  var conditions = (model && model.conditions) || [];
  if (!conditions.length) return 'Без фильтра: под правило попадут все документы индекса.';
  var joiner = (model.combinator === 'any') ? ' ИЛИ ' : ' И ';
  var parts = conditions.filter(function (c) { return c && c.field; }).map(function (c) {
    var label = c.op;
    for (var i = 0; i < OPS.length; i++) if (OPS[i].op === c.op) label = OPS[i].label;
    if (c.op === 'exists' || c.op === 'missing') return c.field + ' ' + label;
    var v = Array.isArray(c.value) ? c.value.join(', ') : c.value;
    return c.field + ' ' + label + ' «' + v + '»';
  });
  if (!parts.length) return 'Условия не заполнены.';
  return parts.join(joiner);
}

/**
 * Свободный поиск как в Kibana (верхняя строка).
 * Примеры:
 *   "Не подобраны услуги доставки"
 *   kubernetes.deployment.name:prod-shipping-v1 AND "Не подобраны услуги доставки"
 */
function kibanaToQuery(text) {
  var q = String(text == null ? '' : text).trim();
  if (!q) return { bool: { must: [] } };
  return {
    bool: {
      must: [{ query_string: { query: q, default_operator: 'AND' } }],
    },
  };
}

/** Достать текст Kibana-строки из query, если это единственный query_string без default_field. */
function queryToKibana(query) {
  var must = query && query.bool && Array.isArray(query.bool.must) ? query.bool.must : null;
  if (!must || must.length !== 1) return null;
  var qs = must[0] && must[0].query_string;
  if (!qs || typeof qs.query !== 'string') return null;
  if (qs.default_field) return null;
  return qs.query;
}

export { toConditions, toQuery, describe, OPS, kibanaToQuery, queryToKibana, containsClause };
