// IDs refer to effective server objects. Metric codes are derived for display only.
const unique = values => [...new Set((values || []).filter(Boolean).map(String))];
export const emptyAlignment = () => ({basis: 'objects', target_ids: [], key_result_ids: [], other_reason: '', expected_contribution: null});
export function alignmentDraft(alignment) {
  return {basis: alignment?.contract_version === 2 && alignment.basis === 'other' ? 'other' : 'objects', target_ids: unique(alignment?.target_ids), key_result_ids: unique(alignment?.key_result_ids), other_reason: alignment?.contract_version === 2 ? alignment.other_reason || '' : '',
    expected_contribution: alignment?.expected_contribution ? {metric_code: alignment.expected_contribution.metric_code, value: String(alignment.expected_contribution.value ?? ''), note: alignment.expected_contribution.note || ''} : null};
}
export const targetMetricCode = row => row?.metric_code || row?.metric?.code || row?.kind;
export function targetSelectable(row, metrics = []) {
  const metric = metrics.find(item => item.code === targetMetricCode(row)) || row?.metric;
  return Boolean(row?.id && row.state !== 'pending_new' && metric?.active === true && metric?.goal_selectable === true);
}
export function effectiveMetricCodes(draft, keyResults = [], targets = []) {
  if (draft?.basis === 'other') return [];
  const selectedKrs = new Set(draft?.key_result_ids || []), selectedTargets = new Set(draft?.target_ids || []);
  return unique([...targets.filter(row => selectedTargets.has(row.id)).map(targetMetricCode), ...keyResults.filter(row => selectedKrs.has(row.id)).map(row => row.metric_code)]);
}
export function objectPeriod(row) {
  const period = row?.period || {type: row?.period_type, start: row?.period_start, end: row?.period_end};
  const names = {week:'周',month:'月',quarter:'季度',year:'年'};
  return period.start && period.end ? `${names[period.type] || period.type || '周期'} ${String(period.start).slice(0,10)} 至 ${String(period.end).slice(0,10)}` : '周期未返回';
}
export function objectScope(row) {
  const scope = row?.scope || row || {};
  // Nested scope describes the linked object. Its nullable names must not be
  // replaced with a task/KR owner's name or a name from another directory.
  if (scope.scope_type === 'team') return scope.team_name || `团队（${scope.team_id || '归属未返回'}）`;
  if (scope.scope_type === 'department') return ({sales:'销售部门',fde:'FDE 部门'})[scope.department_code] || scope.department_code || '部门未返回';
  if (scope.scope_type === 'person') return scope.user_name || `个人（${scope.user_id || scope.user_ref_id || '归属未返回'}）`;
  return '归属未返回';
}
export function contributionError(value, metrics, codes, actual = false) {
  if (value == null) return '';
  const metric = metrics.find(row => row.code === value.metric_code);
  if (!codes.includes(value.metric_code) || !metric || metric.active !== true || metric.source_kind === 'category') return '请选择任务已关联且启用的数值指标';
  const amount = String(value.value ?? '').trim();
  if (!/^(0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?$/.test(amount)) return '贡献量请填写非负数，最多 16 位整数和 2 位小数';
  const places = amount.split('.')[1]?.length || 0;
  if (places > Math.min(Number(metric.decimal_places ?? 2), 2)) return metric.decimal_places === 0 ? '计数指标请填写整数' : '贡献量超过该指标的小数精度';
  if (actual && !String(value.note || '').trim()) return '请填写实际贡献说明';
  if ([...String(value.note || '')].length > 2000) return '贡献说明最多 2000 字';
  return '';
}
export function alignmentError(draft, metrics = [], keyResults = [], targets = []) {
  const targetIds = unique(draft?.target_ids), krIds = unique(draft?.key_result_ids);
  if (targetIds.length > 20 || krIds.length > 20) return '业绩目标与 KR 各最多选择 20 项';
  if (draft?.basis === 'other') {
    if (targetIds.length || krIds.length || draft.expected_contribution != null) return '填写其他原因时，请清空目标、KR 和期望贡献';
    const reason = String(draft.other_reason || '').trim();
    if (!reason) return '请说明此任务暂不关联目标或 KR 的原因';
    if ([...reason].length > 2000) return '其他原因最多 2000 字';
    return '';
  }
  if (String(draft?.other_reason || '').trim()) return '关联目标或 KR 时不能同时填写其他原因';
  if (!targetIds.length && !krIds.length) return '请选择至少一个业绩目标或 KR，或填写其他原因';
  if (targetIds.some(id => !targets.some(row => row.id === id && targetSelectable(row, metrics)))) return '已选业绩目标未生效、不可见或已停用，请重新选择';
  if (krIds.some(id => !keyResults.some(row => row.id === id && row.linkable === true))) return '已选 KR 已归档、不可见或不可关联，请重新选择';
  const codes = effectiveMetricCodes(draft, keyResults, targets);
  if (codes.some(code => !metrics.some(row => row.code === code && row.active === true))) return '已选对象的指标已停用或不可用，请重新选择';
  return contributionError(draft?.expected_contribution, metrics, codes);
}
export function contributionPayload(value) {
  return value == null ? null : {metric_code: value.metric_code, value: String(value.value ?? '').trim(), note: String(value.note || '').trim() || null};
}
export function alignmentPayload(draft, metrics, keyResults = [], targets = []) {
  const error = alignmentError(draft, metrics, keyResults, targets);
  if (error) throw Error(error);
  return {target_ids: unique(draft.target_ids), key_result_ids: unique(draft.key_result_ids), other_reason: draft.basis === 'other' ? String(draft.other_reason).trim() : null,
    expected_contribution: draft.basis === 'other' ? null : contributionPayload(draft.expected_contribution)};
}

// Canonicalize before both the shared request receipt and original submit fingerprint.
export function normalizeTaskAlignment(value) {
  if (!value || typeof value !== 'object' || Object.keys(value).some(key=>!['target_ids','key_result_ids','other_reason','expected_contribution'].includes(key))) throw Error('任务对齐需要目标、KR 或其他原因，请重新选择');
  const target_ids=unique(value.target_ids),key_result_ids=unique(value.key_result_ids),other_reason=String(value.other_reason || '').trim() || null;
  if(target_ids.length>20 || key_result_ids.length>20)throw Error('业绩目标与 KR 各最多选择 20 项');
  if(Boolean(target_ids.length+key_result_ids.length) === Boolean(other_reason))throw Error('关联目标、KR 与其他原因必须二选一');
  if(other_reason && ([...other_reason].length>2000 || value.expected_contribution != null))throw Error('其他原因最多 2000 字，且不能填写期望贡献');
  return {target_ids,key_result_ids,other_reason,expected_contribution:contributionPayload(value.expected_contribution)};
}
