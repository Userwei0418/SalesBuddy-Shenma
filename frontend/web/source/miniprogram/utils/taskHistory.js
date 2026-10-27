// Pure task history projection shared by the Mini Program and the Web adapter.
// The task_event table carries both legacy lifecycle events and execution_v1
// commands. Keep the source event and actor intact; this module only chooses a
// readable label/note and never infers a state transition.
const LABELS = {
  created_in_progress:'创建任务',initialize_v3:'接入轻量任务',adopt_v3:'接入轻量任务',created_v3:'创建任务',defer:'延期任务',request_adjustment:'提出调整请求',revise:'调整任务安排',continue_original:'按原安排继续',close:'结束任务',
  created:'创建任务',created_self_accepted:'本人确认并直接接收',
  initialize_execution:'开启任务进度管理',save_checklist:'调整推进清单',complete_self:'完成本人任务',cancel_task:'取消任务',
  accept: '接受任务', accept_task: '接受任务', decline: '拒绝任务', decline_task: '拒绝任务', reject: '驳回',
  // Keep the legacy lifecycle labels stable while adding readable labels for
  // execution_v1 events; existing saved task pages rely on these strings.
  complete: '完成', submit_completion: '提交完成', approve_completion: '确认完成',
  reject_completion: '驳回', withdraw_completion: '撤回完成提交', set_item_progress: '更新推进项',
  append_feedback: '新增过程反馈', pause: '暂停任务', recheck_pause: '调整复查时间', resume: '恢复任务',
  reassign: '转交任务', initiate_executor_handover: '发起执行责任交接',
  respond_executor_handover: '处理执行责任交接', initiate_reviewer_handover: '发起验收责任交接',
  respond_reviewer_handover: '处理验收责任交接', propose_requirements: '申请修改任务要求',
  respond_requirements: '确认任务要求变更', withdraw_requirements: '撤回任务要求变更', review_completion: '处理验收', cancel: '取消任务',
};
const EXECUTION_EVENT_TYPES = new Set(Object.keys(LABELS));
function commandOf(event) {
  const body = event && (event.body || event.payload);
  return body && typeof body === 'object' && body.command && typeof body.command === 'object'
    ? body.command : body && typeof body === 'object' ? body : {};
}
function noteOf(event) {
  const command = commandOf(event);
  for (const value of [event && event.note, command.note, command.text, command.reason]) {
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  const kind=event&&(event.event_type||event.kind),body=event&&(event.body||event.payload)||{};
  const source=body.source_code||(body.payload||{}).source_code||(body.payload||{}).source||command.source_code;
  if(kind==='created_self_accepted')return source==='business_advice'
    ? '本人明确采纳 AI 建议并指定自己，创建时已接收，进入待执行'
    : source==='mini_program'?'本人确认创建给自己，创建时已接收，进入待执行':'本人已确认并接收任务，进入待执行';
  if(kind==='created_in_progress'||kind==='created'&&(body.execution_protocol_version===3||body.execution_mode==='execution_v3'))return '任务已保存，进入进行中';
  if(kind==='created')return source==='business_advice'?'来源：人工采纳 AI 建议':source==='mini_program'?'来源：手工创建':'任务已创建，接收状态以接收记录为准';
  if (command.decision === 'accept') return '已确认接手';
  if (command.decision === 'reject') return '已拒绝接手';
  return '';
}
function eventTypeOf(event) {
  const raw = event && (event.event_type || event.kind);
  const command = commandOf(event);
  if (raw === 'review_completion' && command.decision === 'approve') return 'approve_completion';
  if (raw === 'review_completion' && command.decision === 'reject') return 'reject_completion';
  return raw;
}
function changesOf(event) {
  const kind=event.event_type||event.kind;
  if(!['revise','defer'].includes(kind))return [];
  const body=event.body||event.payload||{},before=body.before||{},after=body.after||{};
  const result=[];
  const fields=kind==='defer'?[['due_at','截止时间（延期）']]:[['description','任务内容'],['due_at','截止时间'],['executor_user_id','执行人']];
  for(const [field,label] of fields){
    if(!Object.prototype.hasOwnProperty.call(before,field)||!Object.prototype.hasOwnProperty.call(after,field)||before[field]===after[field])continue;
    const display=source=>{
      if(field==='executor_user_id')return source.executor_name||(source.executor||{}).name||'人员已变更';
      if(field==='due_at'){const date=new Date(source[field]);return Number.isFinite(date.getTime())?`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')} ${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`:'未设置';}
      return String(source[field]||'未填写');
    };
    result.push({field,label,before:display(before),after:display(after)});
  }
  return result;
}
function fromEvents(events) {
  if (!Array.isArray(events)) return [];
  return events.map(event => ({event, eventType: eventTypeOf(event)}))
    .filter(({event, eventType}) => event && typeof event === 'object' && EXECUTION_EVENT_TYPES.has(eventType))
    .map(({event, eventType}) => {
      const nextStep=eventType==='append_feedback'?commandOf(event).next_step:null;
      return {...event, event_type: eventType, label: LABELS[eventType], note: noteOf(event),changes:changesOf(event),
        ...(typeof nextStep==='string'&&nextStep.trim()?{nextStep:nextStep.trim()}:{})};
    });
}
function mergeExecutionEvents(taskEvents, executionEvents) {
  const rows = Array.isArray(taskEvents) ? taskEvents.slice() : [];
  const indexes = new Map(rows.map((event, index) => event && event.id ? [event.id, index] : []).filter(pair => pair.length));
  for (const event of Array.isArray(executionEvents) ? executionEvents : []) {
    if (!event || !event.id) continue;
    const body = event.body || event.payload || {};
    const existingIndex = indexes.get(event.id);
    if (existingIndex !== undefined) {
      const existing = rows[existingIndex];
      rows[existingIndex] = {...existing, body: existing.body || body,
        payload: {...(existing.payload || {}), ...(body && body.command ? {command: body.command} : {})},
        actor_name: existing.actor_name || event.actor_name || body.actor_name};
      continue;
    }
    rows.push({...event, event_type: event.event_type || event.kind, payload: body, actor_name: event.actor_name || body.actor_name});
    indexes.set(event.id, rows.length - 1);
  }
  return rows.sort((left, right) => {
    const a = Date.parse(left && left.occurred_at || '') || 0;
    const b = Date.parse(right && right.occurred_at || '') || 0;
    return a - b || String(left.id||'').localeCompare(String(right.id||''));
  });
}
module.exports = {fromEvents, mergeExecutionEvents, LABELS};
