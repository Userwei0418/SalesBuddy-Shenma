import {api, dayKey, session, runtime} from './data.js';

export function taskScopeParams(scope, user = session()) {
  if (scope.kind === 'self') {
    if (!user?.userId) throw Error('请先登录');
    return {view: 'self', member_id: user.userId};
  }
  if (scope.kind === 'member') {
    if (!scope.memberId) throw Error('请选择成员');
    return {view: 'team', member_id: scope.memberId};
  }
  return {view: 'team', ...(scope.teamId && scope.teamId !== 'all' ? {team_id: scope.teamId} : {})};
}

export function riskScopeParams(scope) {
  if (scope.kind === 'member') {
    if (!scope.memberId) throw Error('请选择成员');
    return {scope: 'person', member_id: scope.memberId};
  }
  if (scope.kind === 'self') return {scope: 'self'};
  return scope.teamId && scope.teamId !== 'all' ? {scope: 'team', team_id: scope.teamId} : {scope: 'all'};
}

function count(value) {
  if (!Number.isInteger(value) || value < 0) throw Error('工作台统计接口尚未就绪，请刷新后重试');
  return value;
}

export async function loadWorkspaceTasks(scope, {overview = 'all_pending', offset = 0} = {}) {
  const response = await api().listTaskPage({...taskScopeParams(scope), tab: overview === 'today_completed' ? 'completed' : 'pending',
    overview, order: 'due_asc', page_size: 50, offset});
  const summary = response?.summary || {};
  const total = count(summary.filtered_total);
  const counts = {pending: count(summary.pending_count), overdue: count(summary.overdue_count),
    todayPending: count(summary.today_pending_count), todayCompleted: count(summary.today_completed_count)};
  if (!Array.isArray(response.items)) throw Error('任务列表暂未加载完成，请重试');
  const items = response.items.map(task => ({...runtime().require('utils/taskExecutionPresentation').summary(task),id: String(task.id),
    title: task.title || task.description || '任务', status: task.status,
    dueDay: dayKey(task.due_at), completedDay: dayKey(task.completed_at),
    overdue: !['completed', 'cancelled'].includes(task.status) && Boolean(task.due_at) && Date.parse(task.due_at) < Date.now(),
    customerName: task.customer_name || '', owner: task.owner_name || task.assignee_name || (task.assignees || []).map(a => a.name).filter(Boolean).join('、')}));
  return {total, counts, items, overdue: items.filter(task => task.overdue), partial: response.has_more === true,
    hasMore: response.has_more === true};
}

export async function loadWorkspaceRisks(scope, {offset = 0} = {}) {
  const params = new URLSearchParams({...riskScopeParams(scope), status: 'open', page_size: '50', offset: String(offset)});
  const response = await api().request({path: `/risks?${params}`});
  const total = count(response?.total);
  if (!Array.isArray(response.items)) throw Error('风险列表暂未加载完成，请重试');
  return {total, items: response.items.map(risk => ({id: String(risk.id), title: risk.title || '风险',
    customerName: risk.customer_name || '', opportunityName: risk.opportunity_name || '', owner: risk.owner_name || ''})),
    hasMore: response.has_more === true};
}
