// Saved query conditions; financial periods remain independent of these dates.
export const OPPORTUNITY_DATE_FIELDS = [
  {value: 'business_created_on', label: '商机创建日期'},
  {value: 'expected_close_date', label: '预计关单日期'},
];

export function scopeSelection(scope = {}) {
  return {
    team_ids: [...new Set(scope.team_ids ?? (scope.team_id ? [scope.team_id] : []))],
    owner_ids: [...new Set(scope.owner_ids ?? (scope.owner_id ? [scope.owner_id] : []))],
  };
}

export function ownersInTeams(owners, teamIds) {
  return owners.filter(owner => !teamIds.length || owner.team_ids?.some(id => teamIds.includes(id)));
}

export function changeScopeSelection(scope, patch, owners) {
  const next = {...scopeSelection(scope), ...patch};
  if (patch.team_ids) {
    const available = new Set(ownersInTeams(owners, next.team_ids).map(owner => owner.id));
    next.owner_ids = next.owner_ids.filter(id => available.has(id));
  }
  // Do not send legacy singular keys alongside the new plural selection.
  return {kind: scope.kind, team_ids: [...new Set(next.team_ids)], owner_ids: [...new Set(next.owner_ids)]};
}

export function dateFilterError(value) {
  if (value == null) return '';
  if (!OPPORTUNITY_DATE_FIELDS.some(field => field.value === value.field)) return '请选择商机创建日期或预计关单日期';
  const valid = date => typeof date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(date)
    && Number.isFinite(Date.parse(`${date}T00:00:00Z`)) && new Date(`${date}T00:00:00Z`).toISOString().slice(0, 10) === date;
  if (!valid(value.start) || !valid(value.end)) return '请选择完整、有效的起止日期';
  if (value.start > value.end) return '开始日期不能晚于结束日期';
  return '';
}

export function dateFilterLabel(value) {
  if (!value) return '商机日期：不限';
  return `${OPPORTUNITY_DATE_FIELDS.find(field => field.value === value.field)?.label || '商机日期'}：${value.start} — ${value.end}`;
}

export function scopeFilterLabel(scope, teams, owners) {
  if (scope.kind === 'self') return '本人';
  const selected = scopeSelection(scope);
  const names = (ids, directory, fallback) => ids.map(id => directory.find(item => item.id === id)?.name || fallback).join('、');
  return [selected.team_ids.length ? names(selected.team_ids, teams, '不可用部门') : '全部可见部门',
    selected.owner_ids.length ? names(selected.owner_ids, owners, '不可用成员') : '全部可见成员'].join(' · ');
}
