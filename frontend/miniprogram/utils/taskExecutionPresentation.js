// G0 pure presentation only; no task lifecycle, authority, KR or HTTP mutations.
const positive=v=>Number.isSafeInteger(v)&&v>0;
const nonnegative=v=>Number.isSafeInteger(v)&&v>=0;
function versions(source){
 const keys=['version_no','requirements_version','responsibility_version'];
 if(source&&(source.protocol_version===2||Object.prototype.hasOwnProperty.call(source,'checklist_revision')))keys.push('checklist_revision');
 if(!source||!keys.every(key=>positive(source[key])))throw Error('任务版本信息不完整，请刷新后重试');
 return Object.fromEntries(keys.map(key=>[key,source[key]]));
}
function progress(source){
 const unknown={available:false,percent:null,countLabel:'—',label:'进度暂不可用',hint:'请刷新任务信息'};
 if(!source||!positive(source.requirements_version)||!nonnegative(source.completed)||!nonnegative(source.total)||source.completed>source.total)return unknown;
 const {basis,total,completed,percent}=source;
 if(basis==='missing_checklist')return total===0&&completed===0&&percent===null
  ? {available:true,percent:null,countLabel:'未设置清单',label:'推进依据待补充',hint:'尚无推进清单，不能计算进度'} : unknown;
 if(basis==='requires_revalidation')return total>0&&percent===null
  ? {available:true,percent:null,countLabel:`${completed}/${total} 项`,label:'推进项待复核',hint:'要求发生变化，确认推进项后再展示比例'} : unknown;
 if(basis!=='checklist'||total===0||!nonnegative(percent)||percent>100)return unknown;
 // Display the server projection; do not derive task completion or KR contribution.
 return {available:true,percent,countLabel:`${completed}/${total} 项`,label:`${percent}%`,
  hint:percent===100?'推进清单已完成，提交与验收状态以任务记录为准':'按当前有效推进清单统计'};
}
// Terminal outcome is distinct from the underlying task lifecycle status.
// Prefer the server's terminal-event projection; fallback only for older detail receipts.
function outcome(task={}) {
 const execution=task.execution||{},status=task.status||execution.status;
 const isEnded=['completed','cancelled','rejected'].includes(status);
 if(!isEnded)return {isEnded:false,isSuccessful:false,endReason:null,endedAt:null};
 let reason=task.end_reason??execution.end_reason;
 if(reason!=null&&!['completed','stopped','cancelled','rejected','unknown'].includes(reason))reason='unknown';
 if(!['completed','stopped','cancelled','rejected','unknown'].includes(reason)){
  if(status==='completed'){
   const v3=task.execution_mode==='execution_v3'||task.protocol_version===3||execution.protocol_version===3;
   const action=task.completion_action??execution.completion_action;
   reason=!v3?'completed':action==='close'?'stopped':['submit_completion','review_completion'].includes(action)?'completed':'unknown';
  }else{
   const event=task.last_event_type;
   reason=status==='rejected'||['reject','decline','decline_task'].includes(event)?'rejected':['cancel','cancel_task'].includes(event)?'cancelled':'unknown';
  }
 }
 return {isEnded:true,isSuccessful:reason==='completed',endReason:reason,
  statusLabel:{completed:'正常完成',stopped:'已停止',cancelled:'历史取消',rejected:'历史拒绝',unknown:'已结束 · 结果待核对'}[reason],
  endedAt:task.ended_at||execution.ended_at||(status==='completed'?(task.completed_at||execution.completed_at||task.completedAt):null)||null};
}
function summary(task={}){
 const execution=task.execution||{},flags=task.execution_flags||{},known=['execution_v1','execution_v2'].includes(task.execution_mode)||[1,2].includes(execution.protocol_version);
 if(task.execution_mode==='execution_v3'||execution.protocol_version===3){
  const status=task.status||execution.status,reason=task.processing_reason||execution.processing_reason;
  const label=outcome(task).statusLabel||{in_progress:'进行中',deferred:'已延期',needs_adjustment:'需调整'}[status]||'状态待核对';
  const processing={adjustment_request:'调整请求',paused:'已暂停',awaiting_review:'待验收'}[reason]||'';
  const latest=task.latest_feedback||execution.latest_feedback||{};
  return {showProgress:false,statusLabel:label,processingLabel:status==='needs_adjustment'?processing:'',latestFeedback:latest.text||'',nextStep:task.next_step||execution.next_step||'',completionResult:task.completion_result||execution.completion_result||'',responsibilityLabel:task.self_managed===true||execution.self_managed===true?'本人自办':''};
 }
 const status={pending_confirm:task.target_position?'待岗位领取':'待接受',pending_execution:'待执行',in_progress:'执行中',pending_review:'等待验收',completed:'已完成',cancelled:['cancel','cancel_task'].includes(task.last_event_type)?'已取消':'已拒绝',rejected:'已拒绝',deferred:'历史延期'}[task.status]||'待处理';
 const labels=[];if(!['completed','cancelled','rejected'].includes(task.status)){
  if(flags.paused)labels.push('已暂停');if(flags.execution_handover_pending)labels.push('执行待接手');if(flags.reviewer_handover_pending)labels.push('验收待接手');if(flags.requirements_pending)labels.push('要求待确认');if(task.handover_required)labels.push('需协调交接');
 }
 return {showProgress:known,progressView:progress(task.progress||execution.progress),statusLabel:outcome(task).statusLabel||[status,...labels].join(' · '),responsibilityLabel:task.self_managed===true||execution.self_managed===true?'本人自办':task.review_required===true||execution.review_required===true?'需整体验收':''};
}
module.exports={versions,progress,summary,outcome};
