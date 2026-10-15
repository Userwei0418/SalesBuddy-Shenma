import {decimalText, subjectChoices} from '../model.js';

export const PERIODS = {week: '周', month: '月', quarter: '季度', year: '年'};
export const SCOPES = {person: '个人', team: '团队', department: '部门'};
export const SOURCES = {manual: '手工确认', actual: '实际业务', forecast: '业务预测'};
const iso = date => date.toISOString().slice(0, 10);
export function periodFor(kind, anchor) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(anchor || '')) throw Error('请选择有效日期');
  const date = new Date(`${anchor}T00:00:00Z`);
  if (Number.isNaN(date.valueOf()) || iso(date) !== anchor) throw Error('请选择有效日期');
  const year = date.getUTCFullYear(), month = date.getUTCMonth();
  let start, end;
  if (kind === 'week') {start = new Date(date); start.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7); end = new Date(start); end.setUTCDate(start.getUTCDate() + 6);}
  else if (kind === 'month') {start = new Date(Date.UTC(year, month, 1)); end = new Date(Date.UTC(year, month + 1, 0));}
  else if (kind === 'quarter') {const first = Math.floor(month / 3) * 3; start = new Date(Date.UTC(year, first, 1)); end = new Date(Date.UTC(year, first + 3, 0));}
  else if (kind === 'year') {start = new Date(Date.UTC(year, 0, 1)); end = new Date(Date.UTC(year, 12, 0));}
  else throw Error('请选择目标周期');
  return {type: kind, start: iso(start), end: iso(end)};
}

export function listItems(result) {
  if (!Array.isArray(result?.items) || !Number.isInteger(result.total) || result.total < 0) throw Error('目标列表响应不完整，请重试');
  return result.items;
}
export function participantBody(draft, people) {
  const title = String(draft.title || '').trim();
  if (!title || title.length > 200) throw Error('请填写 1–200 字的标题');
  const ids = new Set((people || []).map(person => String(person.id)));
  // Keep existing references intact when a historical member falls outside the
  // current directory; the server validates them again on explicit save.
  if (draft.id) {if(draft._originalOwnerId)ids.add(draft._originalOwnerId);for(const id of draft._originalCollaboratorIds||[])ids.add(id);}
  if (!ids.has(draft.owner_user_id)) throw Error('请选择当前可见的负责人');
  const collaborators = [...new Set(draft.collaborator_user_ids || [])].filter(id => id !== draft.owner_user_id);
  if (collaborators.length > 50 || collaborators.some(id => !ids.has(id))) throw Error('协作人已变化，请重新选择');
  return {title, owner_user_id: draft.owner_user_id, collaborator_user_ids: collaborators, parent_id: draft.parent_id || null};
}
const version = row => {if (!Number.isInteger(row?.version_no) || row.version_no < 1) throw Error('版本信息不完整，请重新读取'); return row.version_no;};
export function objectiveBody(draft, {people, options, parents = []}) {
  const body = participantBody(draft, people);
  const period = draft.id ? {type: draft.period_type, start: draft.period_start, end: draft.period_end} : periodFor(draft.period_type, draft.anchor_date);
  if (body.parent_id && body.parent_id!==draft._originalParentId && !parentChoices(parents, draft.id, period).some(row => row.id === body.parent_id)) throw Error('上级目标须有效、周期覆盖当前目标，且不能循环对齐');
  if (draft.id) return {...body, version_no: version(draft), status: draft.status === 'archived' ? 'archived' : 'active'};
  if (!subjectChoices(options, draft.scope_type).some(row => row.id === draft.subjectId)) throw Error('请选择有权维护的目标归属');
  return {...body, period_type: draft.period_type, anchor_date: draft.anchor_date, scope_type: draft.scope_type,
    ...(draft.scope_type === 'person' ? {user_id: draft.subjectId} : draft.scope_type === 'team' ? {team_id: draft.subjectId} : {department_code: draft.subjectId})};
}
export function parentChoices(rows, currentId, period) {
  const map = new Map(rows.map(row => [row.id, row]));
  return rows.filter(row => {
    const range = row.period || {start: row.period_start, end: row.period_end};
    if (row.id === currentId || row.status !== 'active' || row.linkable===false || range.start > period.start || range.end < period.end || !range.start || !range.end) return false;
    const seen = new Set(); let cursor = row;
    while (cursor) {if (seen.has(cursor.id) || cursor.id === currentId) return false; seen.add(cursor.id); cursor = map.get(cursor.parent_id);}
    return true;
  });
}
export function targetChoices(rows, objective, metric) {
  return rows.filter(row => row.id && row.effective_amount !== null && row.effective_amount !== undefined && row.kind === metric?.target_kind &&
    row.scope_type === objective.scope_type && (row.user_ref_id || null) === (objective.user_ref_id || null) && (row.team_id || null) === (objective.team_id || null) &&
    (row.department_code || 'sales') === (objective.department_code || 'sales') && row.period_type === objective.period_type && row.period_start === objective.period_start && row.period_end === objective.period_end);
}
export function keyResultBody(draft, {people, objective, metrics, targets, parents = []}) {
  if (!objective?.id || objective.status !== 'active' || !objective.editable) throw Error('当前目标不可维护');
  const body = participantBody(draft, people);
  const period = {start: objective.period_start, end: objective.period_end};
  if (body.parent_id && body.parent_id!==draft._originalParentId && !parentChoices(parents, draft.id, period).some(row => row.id === body.parent_id)) throw Error('上级 KR 周期须覆盖当前周期，且不能循环对齐');
  if (draft.id) return {...body, version_no: version(draft), status: draft.status === 'archived' ? 'archived' : 'active'};
  const metric = metrics.find(row => row.code === draft.metric_code && row.active && row.source_kind !== 'category');
  if (!metric) throw Error('请选择有效指标');
  if (draft.target_id && !targetChoices(targets, objective, metric).some(row => row.id === draft.target_id)) throw Error('请选择同归属、周期和指标的生效目标');
  return {...body, objective_id: objective.id, metric_code: metric.code, target_id: draft.target_id || null};
}
export function progressBody(draft, row) {
  if (row.progress_mode !== 'manual' || !row.can_confirm_progress) throw Error('当前 KR 不支持手工确认进展');
  if (row.metric?.source_kind !== 'manual' || ['元','万元','人民币','CNY','RMB'].includes(row.metric?.unit)) throw Error('财务和自动指标不能手工填写进展');
  const value = String(draft.value ?? '').trim(), precision = row.metric.decimal_places === 0 ? 0 : 2;
  if (!new RegExp(`^(?:0|[1-9]\\d{0,15})${precision ? '(?:\\.\\d{1,2})?' : ''}$`).test(value)) throw Error(precision ? '请填写非负数，最多 2 位小数' : '请填写非负整数');
  const evidence = String(draft.evidence || '').trim();
  if (!evidence || evidence.length > 2000) throw Error('请填写 1–2000 字的进展依据');
  if (draft.confirmed !== true) throw Error('请明确确认此进展及依据');
  return {version_no: version(row), value, evidence, confirmed: true};
}
export function mentionBody(draft, row, people) {
  const recipients = [...new Set(draft.recipient_user_ids || [])], allowed = new Set(people.map(person => person.id));
  if (!recipients.length || recipients.length > 20 || recipients.some(id => !allowed.has(id))) throw Error('请从当前成员目录选择 1–20 位提醒接收人');
  return {version_no: version(row), recipient_user_ids: recipients};
}
export function editDraft(row) {return {...row, collaborator_user_ids: [...(row.collaborator_user_ids || [])], _originalOwnerId:row.owner_user_id,_originalCollaboratorIds:[...(row.collaborator_user_ids||[])],_originalParentId:row.parent_id};}
export function progressDraft(row) {const value=row.progress?.value==null?'':String(row.progress.value);return{value:row.metric?.decimal_places===0?value.replace(/\.0+$/,''):value,evidence:'',confirmed:false};}
export function canConfirmProgress(row,userId,options) {
  if(!row.editable||row.status!=='active'||row.objective_status!=='active'||row.progress_mode!=='manual'||!row.metric?.active||row.metric.source_kind!=='manual'||['元','万元','人民币','CNY','RMB'].includes(row.metric.unit))return false;
  if(typeof row.can_confirm_progress==='boolean')return row.can_confirm_progress;
  const scope=row.scope||{},subjectId=scope.scope_type==='person'?scope.user_id:scope.scope_type==='team'?scope.team_id:scope.department_code;
  return row.owner_user_id===userId||subjectChoices(options,scope.scope_type).some(subject=>subject.id===subjectId&&subject.can_manage===true);
}
export const errorText = error => error.statusCode === 409 ? '记录已更新，当前输入已保留。请刷新核对后重新编辑。' : error.message || '操作未完成，请重试';
export function progressText(row) {
  const progress = row.progress || {};
  const reliable = progress.status === 'confirmed' || progress.complete === true;
  return {value: reliable && progress.value !== null && progress.value !== undefined ? decimalText(String(progress.value), row.metric?.decimal_places) : '—',
    unit: row.metric?.unit || '', pending: !reliable,
    label: progress.status === 'confirmed' ? '已确认' : progress.complete === true ? '自动更新' : progress.status === 'incomplete' || progress.coverage === 'partial' ? '数据不完整' : '暂无可靠进展',
    subtotal: !reliable && progress.known_subtotal !== null && progress.known_subtotal !== undefined ? decimalText(String(progress.known_subtotal), row.metric?.decimal_places) : null};
}
export const reasonText = code => ({no_confirmed_progress:'尚未登记确认进展', no_actual_evidence:'尚无正式业务依据', source_scope_not_fully_authorized:'业务来源授权范围不完整', automatic_metric_projection_not_defined:'该指标尚无统一自动统计口径', unified_metric_projection_required:'等待统一业务统计', period_actual_not_allocated_to_selected_dates:'部分实际金额无法分配到所选日期', tax_basis_unresolved:'部分金额税口径尚未确认', source_confirmation_required:'部分来源仍需确认', incomplete_actual_period:'实际数据覆盖不完整', missing_material:'缺少材料依据'}[code] || '部分来源或口径尚未完整，请查看业务依据');
