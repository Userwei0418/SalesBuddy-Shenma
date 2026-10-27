// Persist receipt metadata only: never copy prompts, audio, results or credentials.
const {sha256, canonical} = require('./requestIdentity');
const STORAGE_KEY = 'salesPendingAgentRuns:v1';
const read = () => wx.getStorageSync(STORAGE_KEY) || {};
function owner(baseUrl, actor) {
  if (!actor || !actor.workspace_id || !actor.user_id) return '';
  return sha256(JSON.stringify(canonical([baseUrl,actor.workspace_id,actor.user_id,actor.role,actor.scope_name,actor.permission_version,actor.team_ids,actor.permissions,actor.permission_grants,actor.capabilities])));
}
function pending(scope, kind) {
  return Object.values(read()).filter(r => r && r.owner === scope && (!kind || r.kind === kind));
}
function find(scope, key) { return pending(scope).find(r => r.key === key); }
function prepare(scope, kind, input, options = {}) {
  if (!scope) throw Object.assign(Error('请重新登录后继续'),{code:'AUTH_REQUIRED'});
  const contextSignature = options.contextSignature || '';
  if(contextSignature && !/^[a-f0-9]{64}$/.test(contextSignature))throw Error('AI 恢复上下文无效，请保留当前输入');
  const key = sha256(JSON.stringify(canonical([scope,kind,input,contextSignature])));
  const previous = find(scope,key);
  if (previous) return {record:previous,existing:true};
  // These labels select an existing action, never contain business input.
  const action=['report_personal_daily','report_team_daily','report_department_daily'].includes(options.action)?options.action:'';
  const meta = action ? {action,scope:options.scope === 'self' ? 'self' : ''} : {};
  const record = {key,owner:scope,kind,contextSignature,meta,phase:'conversation',runId:'',createdAt:Date.now()};
  const records=read();records[key]=record;wx.setStorageSync(STORAGE_KEY,records);
  return {record,existing:false};
}
function mark(record, update) {const records=read();const next={...record,...update};records[record.key]=next;wx.setStorageSync(STORAGE_KEY,records);Object.assign(record,update);}
function submitting(record) {mark(record,{phase:'submitted'});}
function completed(record) {mark(record,{phase:'completed'});}
function accepted(record, runId) {
  if (typeof runId !== 'string' || !/^[\w-]{1,128}$/.test(runId)) throw Error('AI 受理回执不完整，请先核对原任务');
  const records=read();record.runId=runId;records[record.key]=record;wx.setStorageSync(STORAGE_KEY,records);
}
function finish(record) {const records=read();delete records[record.key];wx.setStorageSync(STORAGE_KEY,records);}
function unknown(record) {
  return Object.assign(Error('AI 提交结果待确认，暂未取得任务编号；请先联系管理员核对，勿重复提交。请保留原输入。'),
    {code:'AGENT_ACCEPTANCE_UNKNOWN',outcomeUnknown:true,recoveryAction:'check_status',recoveryKey:record.key});
}
module.exports={owner,pending,find,prepare,submitting,accepted,completed,finish,unknown,STORAGE_KEY};
