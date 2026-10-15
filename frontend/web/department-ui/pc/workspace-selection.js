// 工作台下钻保留统计周期与记录人范围，不套用客户/商机负责人的默认筛选。
export function readScope(options = {}) {
  if (options.scope === 'self') return {kind: 'self'};
  if (options.scope === 'member' && options.member) return {kind: 'member', memberId: options.member};
  if (options.scope === 'team') return {kind: 'team', teamId: options.team || 'all'};
  return null;
}

export function workspacePath(page, range, scope, extra = {}) {
  const query = new URLSearchParams({from: 'workspace', period: range.kind, scope: scope.kind,
    ...(scope.teamId ? {team: scope.teamId} : {}), ...(scope.memberId ? {member: scope.memberId} : {}), ...extra});
  return `pages/${page}/index?${query}`;
}

export function recordSelection(options = {}) {
  return {period: options.period || 'week', scope: readScope(options),
    view: ['time', 'person', 'customer', 'opportunity', 'table'].includes(options.view) ? options.view : 'time',
    linkedOnly: options.linked === '1'};
}
