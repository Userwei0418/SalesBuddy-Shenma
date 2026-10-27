export const PERIOD_LABELS = {week: '周', month: '月', quarter: '季度', year: '年'};
export const SOURCE_LABELS = {actual: '实际数据', forecast: '销售预测', manual: '手工维护', category: '任务类别'};

// crm.sales_target keeps one canonical amount in CNY (yuan).  Q4 targets
// imported from Feishu are business figures in ten-thousands of yuan, so the
// management UI converts only at this boundary.  Report aggregation continues
// to use the canonical yuan value and is therefore not double-scaled.
export const MONEY_TARGET_KINDS = new Set(['collection', 'collection_ppl', 'recognized', 'acv', 'forecast_collection', 'forecast_recognized']);
export function targetUnit(metric = {}) { return MONEY_TARGET_KINDS.has(metric.target_kind || metric.code) ? '万元' : metric.unit || ''; }
function decimalParts(value) {
  const text = String(value ?? '').trim(), match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return null;
  return {whole: match[1], fraction: match[2] || ''};
}
function scaleDecimal(value, factor, places = 2) {
  const parts = decimalParts(value);
  if (!parts) return value;
  const sourcePlaces = parts.fraction.length;
  const digits = BigInt(parts.whole + parts.fraction);
  if (factor === 10000) {
    const scaled = (digits * 10000n).toString().padStart(sourcePlaces + 1, '0');
    const head = sourcePlaces ? scaled.slice(0, -sourcePlaces) : scaled;
    const tail = sourcePlaces ? `.${scaled.slice(-sourcePlaces)}` : '';
    return `${head}${tail}`.replace(/\.0+$/, '');
  }
  if (factor === 1 / 10000) {
    const divisor = 10000n * 10n ** BigInt(sourcePlaces);
    const numerator = digits * 10n ** BigInt(places);
    const quotient = (numerator + divisor / 2n) / divisor;
    const text = quotient.toString().padStart(places + 1, '0');
    return `${text.slice(0, -places)}.${text.slice(-places)}`.replace(/\.0+$/, '');
  }
  return value;
}
export function targetDisplayAmount(value, metric = {}) {
  return MONEY_TARGET_KINDS.has(metric.target_kind || metric.code) ? scaleDecimal(value, 1 / 10000, metric.decimal_places) : value;
}
export function targetStorageAmount(value, metric = {}) {
  return MONEY_TARGET_KINDS.has(metric.target_kind || metric.code) ? scaleDecimal(value, 10000, metric.decimal_places) : value;
}

// Keep user amounts as decimal strings all the way to the API. No Number()
// conversion, floating-point rounding or silent truncation of entered cents.
export function positiveDecimal(value, decimalPlaces = 2, allowZero = false) {
  const input = String(value ?? '').trim();
  const expression = decimalPlaces === 0 ? /^\d+$/ : /^\d+(?:\.\d{1,2})?$/;
  if (!expression.test(input)) throw Error(decimalPlaces === 0 ? '目标值须为正整数' : '目标值须为正数，最多保留 2 位小数');
  const [whole, fraction = ''] = input.split('.'), normalized = whole.replace(/^0+(?=\d)/, '');
  if (normalized.length > 16) throw Error('目标值过大，请核对');
  if (!allowZero && !/[1-9]/.test(normalized + fraction)) throw Error('目标值须大于 0；未设目标请取消本行');
  return decimalPlaces === 0 ? normalized : `${normalized}.${fraction.padEnd(2, '0')}`;
}

export function decimalText(value, decimalPlaces = 2) {
  if (value === null || value === undefined || value === '') return '未设目标';
  const text = String(value), match = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!match) return text;
  const whole = match[1].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const fraction = (match[2] || '').padEnd(decimalPlaces, '0');
  return decimalPlaces === 0 && !/[1-9]/.test(fraction) ? whole : `${whole}.${fraction}`;
}

export function periodText(row) {
  const start = row.period_start || row.anchor_date || '';
  if (row.period_type === 'quarter') return `${start.slice(0, 4)} Q${Math.floor((Number(start.slice(5, 7)) - 1) / 3) + 1}`;
  if (row.period_type === 'year') return start.slice(0, 4);
  if (row.period_type === 'month') return start.slice(0, 7);
  return `${start} — ${row.period_end || ''}`;
}

export function targetRows(payload) {
  if (!Array.isArray(payload?.items)) throw Error('目标列表响应不完整，请刷新重试');
  // V1 returns pending_new in the authoritative, paginated items. Never rebuild
  // this set from the legacy, capped pending preview or replace effective values.
  return payload.items.map(row => ({...row, key: row.row_key || row.id,
    effective_amount: Object.hasOwn(row, 'effective_amount') ? row.effective_amount : row.amount_text ?? row.amount ?? null,
  }));
}

export function subjectChoices(options, scope) {
  const key = {team: 'teams', person: 'people', department: 'departments'}[scope];
  return key ? options?.[key] || [] : [];
}

export function rowSubject(row) {
  if (row.scope_type === 'team') return row.team_name || '团队';
  if (row.scope_type === 'department') return row.department_code === 'fde' ? 'FDE 部门' : '销售部门';
  return row.user_name || '个人';
}

export function rowDraft(row, metric) {
  const amount = String(targetDisplayAmount(row.effective_amount, metric) ?? '');
  return {rowKey: row.key, scope: row.scope_type, subjectId: row.team_id || row.user_ref_id || row.department_code,
    period_type: row.period_type, anchor_date: row.period_start, kind: row.kind,
    metricCode: metric?.code || row.kind, amount: metric?.decimal_places === 0 ? amount.replace(/\.0+$/, '') : amount, reason: '', version_no: row.version_no};
}

export function targetBody(draft, metrics, options) {
  const metric = metrics.find(item => item.code === draft.metricCode && item.active && item.goal_selectable);
  if (!metric) throw Error('请选择有效且可设置目标的指标');
  if (!subjectChoices(options, draft.scope).some(item => item.id === draft.subjectId)) throw Error('请选择当前有权维护的目标对象');
  if (!PERIOD_LABELS[draft.period_type] || !/^\d{4}-\d{2}-\d{2}$/.test(draft.anchor_date || '') || Number(draft.anchor_date.slice(0, 4)) < 2000 || Number(draft.anchor_date.slice(0, 4)) > 2100) throw Error('请选择 2000 至 2100 年的有效周期日期');
  const reason = String(draft.reason || '').trim();
  if (!reason) throw Error('请填写目标设置或调整原因');
  if (reason.length > 2000) throw Error('调整原因不能超过 2000 字');
  return {scope: draft.scope, ...({team: {team_id: draft.subjectId}, person: {user_id: draft.subjectId}, department: {department_code: draft.subjectId}}[draft.scope]),
    period_type: draft.period_type, anchor_date: draft.anchor_date, kind: metric.target_kind || metric.code,
    amount: targetStorageAmount(positiveDecimal(draft.amount, metric.decimal_places, ['collection', 'collection_ppl', 'recognized', 'acv', 'opportunity_count', 'visit_count', 'demo_count'].includes(metric.target_kind || metric.code)), metric), reason,
    ...(draft.version_no != null ? {version_no: draft.version_no} : {}),
  };
}

export function targetError(error) {
  if (error?.statusCode === 409) return '目标已被更新，或已有同范围的待审申请。你的输入已保留，请刷新核对最新值后重新编辑。';
  if (error?.statusCode === 403) return '当前账号无权维护这个范围的目标，输入已保留。';
  if (error?.statusCode === 422) return '目标字段未通过校验，请检查对象、指标、金额与调整原因。';
  return error?.message || '保存未完成，输入已保留，请刷新核对后重试。';
}
