// V3 task controller lives in the existing detail subpackage. Shared by Mini/Web.
const access=require('../../utils/access');
const presentation=require('../../utils/taskExecutionPresentation');
const history=require('../../utils/taskHistory');
const ACTIONS=[['append_feedback','补充反馈'],['submit_completion','提交完成'],['defer','延期'],['request_adjustment','提出调整'],['pause','暂停'],['resume','恢复推进'],['revise','修改安排'],['continue_original','按原安排继续'],['review_approve','验收通过'],['review_reject','退回继续'],['close','不再继续']];
const capability=action=>action.startsWith('review_')?'review_completion':action==='adopt'?'adopt_v3':action;
const stamp=value=>{const d=new Date(value);return value&&Number.isFinite(d.getTime())?`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`:'—';};
const context=(page,session)=>JSON.stringify([access.identity(session()),page.data.taskId]);
function options(x){return ACTIONS.filter(([action])=>x.caps[capability(action)]===true).map(([value,label])=>({value,label:value==='submit_completion'&&x.selfManaged?'完成任务':label}));}
function project(raw,id){
 if(!raw||raw.task_id!==id||raw.protocol_version!==3||raw.execution_mode!=='execution_v3'||!Array.isArray(raw.events)||!raw.capabilities||!['in_progress','completed','deferred','needs_adjustment'].includes(raw.status))throw Error('任务信息不完整，请刷新后重试');
 if(raw.status==='needs_adjustment'&&!['adjustment_request','paused','awaiting_review'].includes(raw.processing_reason))throw Error('任务待处理事项不完整，请刷新');
 const versions=presentation.versions(raw),summary=presentation.summary(raw),detail=raw.processing_detail||{};
 const current=(raw.submissions||[]).find(row=>row.id===raw.current_submission);
 const result={raw,versions,caps:raw.capabilities,lightweight:true,...summary,processingNote:detail.reason||detail.note||'',
  currentSubmissionNote:current&&current.snapshot&&current.snapshot.note||'',
  completionResult:raw.completion_result||'',completionAction:raw.completion_action||'',completionLabel:presentation.outcome(raw).endReason==='stopped'?'停止说明':presentation.outcome(raw).isSuccessful?'完成结果':'结束记录',executorLabel:raw.executor&&raw.executor.name||'待核对',creatorLabel:raw.creator&&raw.creator.name||'待核对',
  selfManaged:raw.self_managed===true,reviewRequired:raw.review_required===true,
  feedback:raw.events.filter(e=>e.kind==='append_feedback').map(e=>{const b=e.body||e.payload||{},c=b.command||{};return {id:e.id,text:c.text||'',nextStep:c.next_step||'',authorLabel:e.actor_name||b.actor_name||'参与人',timeLabel:stamp(e.occurred_at)};}).reverse()};
 result.canUpdate=options(result).length>0;
 return result;
}
function bodyFor(draft,raw){
 const text=(value,label,max=4000,required=true)=>{const result=String(value||'').trim();if((required&&!result)||[...result].length>max)throw Error(`请填写${label}${max?'（最多 '+max+' 字）':''}`);return result;};
 const date=()=>{const value=new Date(`${draft.dueDate}T${draft.dueTime||'17:00'}`);if(!draft.dueDate||!Number.isFinite(value.getTime()))throw Error('请选择有效截止时间');return value.toISOString();};
 switch(draft.action){
 case 'append_feedback':return {text:text(draft.text,'最新反馈'),next_step:text(draft.next_step,'下一步',2000,raw.status==='in_progress')};
 case 'defer':{const due=date();if(Date.parse(due)<=Date.parse(raw.requirements&&raw.requirements.due_at||raw.due_at))throw Error('新截止日期应晚于当前截止日期');return {reason:text(draft.reason,'延期原因',1000),due_at:due};}
 case 'request_adjustment':case 'pause':case 'resume':case 'close':return {reason:text(draft.reason,({request_adjustment:'实际调整情况',pause:'暂停说明',resume:'恢复说明',close:'结束原因'})[draft.action],1000)};
 case 'submit_completion':return {note:text(draft.note,'完成结果')};
 case 'review_approve':case 'review_reject':if(!raw.current_submission)throw Error('本轮完成提交已变化，请刷新');return {submission_id:raw.current_submission,decision:draft.action==='review_approve'?'approve':'reject',note:text(draft.note,'退回原因',4000,draft.action==='review_reject')};
 case 'continue_original':return {reason:text(draft.reason,'处理说明',1000,false)};
 case 'revise':{const before=raw.requirements||{},body={};const description=text(draft.description,'任务内容',500);if([...description].length<5)throw Error('任务内容至少填写 5 字');if(description!==String(before.description||raw.description||''))body.description=description;const due=date();if(stamp(before.due_at||raw.due_at)!==`${draft.dueDate} ${draft.dueTime||'17:00'}`)body.due_at=due;const executor=raw.executor_user_id||raw.executor&&raw.executor.user_id;if(draft.executor_user_id&&draft.executor_user_id!==executor)body.executor_user_id=draft.executor_user_id;const reason=text(draft.reason,'调整说明',1000,false);if(reason)body.reason=reason;if(!['description','due_at','executor_user_id'].some(key=>Object.prototype.hasOwnProperty.call(body,key)))body.description=description;return body;}
 case 'adopt':return {note:text(draft.reason,'接入说明',1000,false)};
 default:throw Error('请选择有效任务操作');
 }
}
function create(api,session,normalizeTask){return {
 applyLightweight(raw){
  const previous=this.data.executionHistory||[],combined={...raw,events:history.mergeExecutionEvents(previous,raw.events)};
  const x=project(combined,this.data.taskId),base=this.data.task,requirements=raw.requirements||{};
  const events=history.mergeExecutionEvents(base.events,raw.events),executor=raw.executor||{};
  const snapshot={...base,...requirements,status:raw.status,execution_mode:'execution_v3',execution:raw,version_no:raw.version_no,processing_reason:raw.processing_reason,latest_feedback:raw.latest_feedback,next_step:raw.next_step,completion_result:raw.completion_result,completion_action:raw.completion_action,end_reason:raw.end_reason,ended_at:raw.ended_at,self_managed:raw.self_managed,review_required:raw.review_required,events,
   owner:executor.name||base.owner,owner_name:executor.name||base.owner,creator_name:raw.creator&&raw.creator.name||base.creator_name,
   assignees:executor.user_id?[{user_id:executor.user_id,name:executor.name,responsibility:'owner'}]:base.assignees};
  const cursors=this.data.executionHistoryCursors||{},old=this.data.executionSubmissions||[],head=raw.submissions||[],seen=new Set(old.map(row=>row.id));
  const submissions=[...new Map([...old,...head].map(row=>[row.id,row])).values()].sort((a,b)=>(Date.parse(b.created_at||b.submitted_at)||0)-(Date.parse(a.created_at||a.submitted_at)||0)||String(b.id).localeCompare(String(a.id))).map(row=>({...row,timeLabel:stamp(row.created_at||row.submitted_at),note:row.snapshot&&row.snapshot.note||''}));
  const submissionCursor=!Object.prototype.hasOwnProperty.call(cursors,'submissions')||(raw.submissions_next_cursor&&head.length&&!head.some(row=>seen.has(row.id)))?raw.submissions_next_cursor??null:cursors.submissions;
  this.setData({task:normalizeTask?normalizeTask(snapshot,session()):snapshot,execution:x,taskMigration:null,executionContext:context(this,session),executionActorUserId:session().userId,executionError:'',executionOffline:false,executionSynced:stamp(new Date().toISOString()),
   executionSubmissions:submissions,executionHistoryCursors:{...cursors,...(!Object.prototype.hasOwnProperty.call(cursors,'events')?{events:raw.events_next_cursor??null}:{}),submissions:submissionCursor}});
 },
 openLightweightUpdate(e){
  const x=this.data.execution;if(!x||!x.lightweight||this.data.executionBusy||this.data.executionError)return;
  const choices=options(x),requested=e&&((e.currentTarget||{}).dataset||(e.dataset||{})).action,action=requested||choices[0]&&choices[0].value;
  if(!choices.some(o=>o.value===action))return;
  const r=x.raw,req=r.requirements||{},due=new Date(req.due_at||r.due_at||this.data.task.due_at),valid=Number.isFinite(due.getTime());
  this.lightweightBase={context:context(this,session),versions:{...x.versions},submission:r.current_submission||null,raw:r};
  const draft={action,text:'',next_step:r.next_step||'',reason:r.status==='deferred'?x.processingNote:'',note:'',description:req.description||this.data.task.description||'',executor_user_id:r.executor_user_id||r.executor&&r.executor.user_id||'',dueDate:valid?`${due.getFullYear()}-${String(due.getMonth()+1).padStart(2,'0')}-${String(due.getDate()).padStart(2,'0')}`:'',dueTime:valid?`${String(due.getHours()).padStart(2,'0')}:${String(due.getMinutes()).padStart(2,'0')}`:'17:00',minimumDueDate:valid?`${due.getFullYear()}-${String(due.getMonth()+1).padStart(2,'0')}-${String(due.getDate()).padStart(2,'0')}`:'2000-01-01'};
  this.setData({lightweightDialog:draft,lightweightActionOptions:choices,lightweightActionIndex:choices.findIndex(o=>o.value===action),lightweightDialogError:'',lightweightRecipients:[],lightweightRecipientsLoading:false,lightweightRecipientsError:''});
  if(action==='revise')this.loadLightweightRecipients();
 },
 chooseLightweightAction(e){
  if(this.data.executionBusy||!this.data.lightweightDialog)return;const value=e.detail.value,choices=this.data.lightweightActionOptions||[],selected=choices.find(o=>o.value===value)||choices[Number(value)];if(!selected)return;
  this.setData({lightweightDialog:{...this.data.lightweightDialog,action:selected.value},lightweightActionIndex:choices.indexOf(selected),lightweightDialogError:''});if(selected.value==='revise')this.loadLightweightRecipients();
 },
 inputLightweight(e){if(this.data.executionBusy||!this.data.lightweightDialog)return;const key=(e.currentTarget&&e.currentTarget.dataset||e.dataset||{}).key;if(!['text','next_step','reason','note','description','dueDate','dueTime','executor_user_id'].includes(key))return;this.setData({lightweightDialog:{...this.data.lightweightDialog,[key]:e.detail.value},lightweightDialogError:''});},
 closeLightweightUpdate(){if(!this.data.executionBusy){this.lightweightRecipientSeq=(this.lightweightRecipientSeq||0)+1;this.setData({lightweightDialog:null,lightweightRecipientsLoading:false});}},
 async loadLightweightRecipients(){
  if(!this.data.lightweightDialog||this.data.lightweightDialog.action!=='revise'||this.data.lightweightRecipientsLoading)return;
  const id=this.data.taskId,identity=context(this,session),seq=this.lightweightRecipientSeq=(this.lightweightRecipientSeq||0)+1;
  const current=()=>!this.isUnloading&&this.taskPageVisible!==false&&identity===context(this,session)&&seq===this.lightweightRecipientSeq&&this.data.lightweightDialog&&this.data.lightweightDialog.action==='revise';
  this.setData({lightweightRecipientsLoading:true,lightweightRecipientsError:''});
  try{const rows=[],seen=new Set();let offset=0;while(current()){
   const result=await api.getTaskExecutionCandidates(id,{purpose:'executor',limit:50,offset});if(!current())return;
   if(!result||!Array.isArray(result.items)||result.items.length>50||result.offset!==offset||result.limit!==50||!Number.isSafeInteger(result.total)||result.total<0||result.items.some(row=>!row.user_id||typeof row.display_name!=='string'))throw Error('执行人目录不完整，请重试');
   for(const row of result.items){if(seen.has(row.user_id))throw Error('人员目录已变化，请重新读取');seen.add(row.user_id);rows.push({user_id:row.user_id,name:row.display_name});}
   offset+=result.items.length;if(offset>=result.total)break;if(!result.items.length)throw Error('人员目录分页不完整，请重试');
  }if(current())this.setData({lightweightRecipients:rows,lightweightExecutorIndex:rows.findIndex(row=>row.user_id===this.data.lightweightDialog.executor_user_id)});
  }catch(error){if(current())this.setData({lightweightRecipientsError:error.message||'人员读取失败'});}finally{if(current())this.setData({lightweightRecipientsLoading:false});}
 },
 chooseLightweightExecutor(e){if(this.data.executionBusy||this.data.lightweightRecipientsLoading||this.data.lightweightRecipientsError)return;const row=(this.data.lightweightRecipients||[])[Number(e.detail.value)];if(row)this.setData({lightweightDialog:{...this.data.lightweightDialog,executor_user_id:row.user_id},lightweightExecutorIndex:Number(e.detail.value)});},
 async submitLightweight(){
  const x=this.data.execution||this.data.taskMigration,d=this.data.lightweightDialog,base=this.lightweightBase;if(!x||!d||!base||this.data.executionBusy)return;
  if(base.context!==context(this,session)||JSON.stringify(base.versions)!==JSON.stringify(d.action==='adopt'?(x.raw.adopt_versions||x.versions):x.versions)||(d.action!=='adopt'&&(x.raw.current_submission||null)!==base.submission)){this.setData({lightweightDialogError:'任务已变化，填写内容已保留。请关闭后重新打开，核对最新状态再保存。'});return;}
  const cap=capability(d.action);if(x.caps[cap]!==true||this.data.executionError){this.setData({lightweightDialogError:'当前操作已不可用，请刷新任务后重试'});return;}
  let body;try{body={...base.versions,...bodyFor(d,base.raw)};if(body.executor_user_id&&(this.data.lightweightRecipientsLoading||this.data.lightweightRecipientsError||!(this.data.lightweightRecipients||[]).some(row=>row.user_id===body.executor_user_id)))throw Error('请从最新人员目录选择执行人');}catch(error){this.setData({lightweightDialogError:error.message});return;}
  const id=this.data.taskId,identity=base.context,action=d.action.startsWith('review_')?'review_completion':d.action;
  const current=()=>!this.isUnloading&&this.taskPageVisible!==false&&identity===context(this,session)&&this.data.lightweightDialog===d;
  this.executionSeq=(this.executionSeq||0)+1;this.setData({executionBusy:true,executionLoading:false,lightweightDialogError:''});
  const write=this.beginTaskWrite?this.beginTaskWrite(id,access.identity(session())):null;let applied=false;
  try{const result=await api.taskLightweightCommand(id,action,body);if(!current())return;
   if(!result||typeof result.changed!=='boolean'||!result.execution||result.execution.task_id!==id)throw Error('保存结果尚未确认，请保留填写内容并重试');
   this.applyLightweight(result.execution);applied=true;this.setData({lightweightDialog:null});
   wx.showToast({title:result.changed?'任务已更新':'安排未变化',icon:'success'});
  }catch(error){if(current()){this.setData({lightweightDialogError:error.message||'保存结果待确认，请按原内容重试'});if(error.statusCode===409)await this.loadExecution();}}
  finally{if(identity===context(this,session)&&!this.isUnloading)this.setData({executionBusy:false});if(write)this.finishTaskWrite(write,applied);}
 },
 openLightweightAdopt(){
  const x=this.data.execution||this.data.taskMigration;if(!x||x.caps.adopt_v3!==true||this.data.executionBusy)return;
  const versions=x.raw.adopt_versions||x.versions;
  try{presentation.versions(versions);}catch(error){this.setData({executionError:error.message});return;}
  this.lightweightBase={context:context(this,session),versions,submission:x.raw.current_submission||null,raw:x.raw};
  this.setData({lightweightDialog:{action:'adopt',reason:''},lightweightActionOptions:[{value:'adopt',label:'接入轻量流程'}],lightweightActionIndex:0,lightweightDialogError:''});
 }
};}
module.exports={create,project,bodyFor,options};
