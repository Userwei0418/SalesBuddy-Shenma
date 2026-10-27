import {api, runtime} from '../../pc/data.js';
import {reviewWrite} from '../targets/api.js';
import {normalizeTaskAlignment} from './model.js';

export async function loadTaskAlignmentCapabilities() {
  const result = await api().request({path: '/tasks/alignment-capabilities', fresh: true});
  if (!Array.isArray(result?.supported_versions) || !result.supported_versions.includes(2)) throw Object.assign(Error('当前服务尚不支持任务目标关联，请完成配套升级后再下发或编辑任务'), {statusCode: 501});
  return result;
}
export async function loadTaskTargets(query, {current = () => true, onProgress = () => {}} = {}) {
  const items = []; let offset = 0;
  while (current()) {
    const params = new URLSearchParams({period_type: query.period_type, anchor_date: query.anchor_date, limit: '100', offset: String(offset)});
    const result = await api().request({path: `/review/targets?${params}`, fresh: true});
    if (!current()) return null;
    if (!Array.isArray(result?.items) || !Number.isInteger(result.total) || result.total < 0) throw Error('业绩目标目录返回异常，请重试');
    for (const row of result.items) {
      if (!row?.period_type || !row.period_start || !row.period_end || !row.scope_type || (!row.id && row.state !== 'pending_new')) throw Error('业绩目标缺少实际周期或归属，请完成配套升级');
      items.push(row);
    }
    offset += result.items.length;
    onProgress({loaded: offset, total: result.total});
    if (offset >= result.total) return items;
    if (!result.items.length) throw Error('业绩目标目录未完整返回，请重试');
  }
  return null;
}
export async function loadTaskMetrics() {
  const result = await api().request({path: '/review/metrics', fresh: true});
  if (!Array.isArray(result?.items)) throw Error('指标目录返回异常，请重试');
  return result.items;
}
export async function loadTaskKeyResults({current = () => true, onProgress = () => {}} = {}) {
  const items = new Map(); let offset = 0;
  // total includes archived/ineligible rows. Never terminate because the
  // current page has no selectable candidates.
  while (current()) {
    const result = await api().request({path: `/review/key-results?limit=100&offset=${offset}`, fresh: true});
    if (!current()) return null;
    if (!Array.isArray(result?.items) || !Number.isInteger(result.total) || result.total < 0) throw Error('KR 目录返回异常，请重试');
    for (const row of result.items) {
      if (!row?.id || typeof row.linkable !== 'boolean') throw Error('当前 KR 目录缺少可关联状态，请刷新后重试');
      if(row.linkable && (!row.period?.type || !row.period?.start || !row.period?.end || !row.scope?.scope_type))throw Error('KR 缺少实际周期或归属，请完成配套升级');
      items.set(row.id, row);
    }
    offset += result.items.length;
    onProgress({loaded: offset, total: result.total});
    if (offset >= result.total) return [...items.values()];
    if (!result.items.length) throw Error('KR 目录未完整返回，请重试');
  }
  return null;
}
export function saveTaskAlignment(task, alignment) {
  return reviewWrite(`/tasks/${encodeURIComponent(task.id)}/alignment`, {version_no: task.version_no, alignment_contract_version: 2, alignment: normalizeTaskAlignment(alignment)}, 'PATCH');
}
export function completeAlignedTask(task, note, actualContribution) {
  return api().request({path: `/tasks/${encodeURIComponent(task.id)}/events`, method: 'POST',
    data: {event_type: 'complete', note, version_no: task.version_no, actual_contribution: actualContribution}});
}
export function taskBody(input, alignment) {
  return {...runtime().require('utils/taskExecutionMode').creation(input), ...(input.executionMode==='execution_v2'?{deliverable:String(input.deliverable||'').trim(),acceptance_criteria:String(input.acceptanceCriteria||'').trim()}:{}), description: input.description, association_kind: input.associationKind, assignee_account_code: input.targetPosition ? null : input.assigneeAccount,
    target_position: input.targetPosition || null, due_at: new Date(input.dueAt).toISOString(), priority_code: ({'普通': 'normal', '中': 'medium', '高': 'high'})[input.priority] || 'normal',
    customer_id: input.customerId || null, opportunity_id: input.opportunityId || null, alignment_contract_version: 2, alignment: normalizeTaskAlignment(alignment)};
}
export async function createAlignedTasks(inputs, source, alignment) {
  const tasks = inputs.map(input => taskBody(input, alignment));
  if (source) {
    const result = await api().decideSuggestion(source.id, {decision: 'adopted', version_no: source.version, ...(tasks.length === 1 ? {task: tasks[0]} : {tasks})});
    return result.tasks || [result.task];
  }
  if (tasks.length === 1) return [await api().request({path: '/tasks', method: 'POST', data: tasks[0]})];
  const result = await api().request({path: '/tasks/batch', method: 'POST', data: {tasks}});
  return result.items;
}
