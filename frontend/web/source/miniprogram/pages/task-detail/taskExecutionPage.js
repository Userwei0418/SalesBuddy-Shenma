const view=require('../../utils/taskExecutionPresentation');
const access=require('../../utils/access');
const requirements=require('./taskRequirements');
const taskExecutionMode=require('../../utils/taskExecutionMode');
const taskHistory=require('../../utils/taskHistory');
const checklistEditor=require('./taskChecklistEditor');
const eventBody=e=>e.body||e.payload||{};
const eventOrder=(a,b)=>(Date.parse(a.occurred_at)||0)-(Date.parse(b.occurred_at)||0)||String(a.id).localeCompare(String(b.id));
const RESUME_LABELS={pending_confirm:'待接受（由执行人重新确认）',pending_execution:'待执行',in_progress:'执行中'};
const submissionRow=item=>({...item,timeLabel:stamp(item.created_at||item.submitted_at),note:item.snapshot&&item.snapshot.note||'未填写',itemCount:(item.snapshot&&item.snapshot.items||[]).length,checklist:(item.snapshot&&item.snapshot.items||[]).map(row=>({...row,stateLabel:LABELS[STATES.indexOf(row.state)]||'待核对'}))});
// Merge fresh snapshots into already loaded history without rewinding its cursor.
const submissionOrder=(a,b)=>(Date.parse(b.created_at||b.submitted_at)||0)-(Date.parse(a.created_at||a.submitted_at)||0)||String(b.id).localeCompare(String(a.id));
function mergeSubmissions(old,head){return [...new Map([...(old||[]),...(head||[])].map(item=>[item.id,item])).values()].sort(submissionOrder).map(submissionRow);}
function submissionCursor(cursors,headCursor,old,head){
 if(!Object.prototype.hasOwnProperty.call(cursors,'submissions'))return headCursor;
 const loaded=new Set((old||[]).map(item=>item.id));
 // More than one page may have arrived since the last read. Reopen only this
 // newly discovered gap; retain every older snapshot already shown.
 if(headCursor&&(head||[]).length&&!(head||[]).some(item=>loaded.has(item.id)))return headCursor;
 return cursors.submissions;
}
const STATES=['not_started','in_progress','completed','rework'];
const LABELS=['未开始','进行中','已完成','返工'];
const stamp=x=>{const d=new Date(x);return x&&Number.isFinite(d.getTime())?`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`:'—';};
function feedbackRows(events){return (events||[]).filter(e=>e.kind==='append_feedback').slice().sort(eventOrder).reverse().map(e=>{const body=eventBody(e),command=body.command||{};return {id:e.id,actorUserId:e.actor_user_id||body.actor_user_id,authorLabel:body.actor_name||e.actor_name||'参与人',timeLabel:stamp(e.occurred_at),text:command.text||'',kind:command.feedback_kind||'progress',itemId:command.item_id||'',nextStep:command.next_step||'',blocker:command.blocker||'',evidenceUrls:Array.isArray(command.evidence_urls)?command.evidence_urls:[],relationLabel:command.corrects_event_id?'更正反馈':command.reply_to_event_id?'回复反馈':'',replyTo:command.reply_to_event_id||'',corrects:command.corrects_event_id||''};});}
function project(x,id){
 if(!x||x.task_id!==id||![1,2].includes(x.protocol_version)||!Array.isArray(x.items)||!Array.isArray(x.events)||!x.capabilities)throw Error('任务执行信息不完整，请刷新');
 const versions=view.versions(x),progress=view.progress(x.progress);if(!progress.available)throw Error('任务进度信息不完整，请刷新');
 if(x.progress.requirements_version!==versions.requirements_version)throw Error('任务要求版本不一致，请刷新');
 const submissionId=typeof x.current_submission==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x.current_submission)?x.current_submission:null;
 const submissions=submissionId&&Array.isArray(x.submissions)?x.submissions.filter(row=>row&&row.id===submissionId):[];
 const submission=submissions.length===1?submissions[0]:null;
 const completionNote=submission&&submission.snapshot&&typeof submission.snapshot.note==='string'?submission.snapshot.note.trim():'';
 const completionEvents=submission&&x.status==='completed'?x.events.filter(e=>{
  const body=eventBody(e),command=body.command||{};
  return body.to_status==='completed'&&((e.kind==='review_completion'&&command.submission_id===submissionId&&command.decision==='approve')
   ||(['submit_completion','complete_self'].includes(e.kind)&&(body.after||{}).current_submission===submissionId));
 }):[];
 const completedAt=completionEvents.length===1&&stamp(completionEvents[0].occurred_at)!=='—'?completionEvents[0].occurred_at:null;
 return {raw:x,versions,requirementsDiff:x.pending_requirements?requirements.differences(x.pending_requirements.before,x.pending_requirements.after):[],progress:x.progress,items:x.items.map(i=>({...i,id:i.item_id,stateLabel:LABELS[STATES.indexOf(i.state)]||'待核对',editable:x.capabilities.set_item_progress===true})),
  completionNote:completionNote||'当前完成说明暂不可用，请刷新',
  completedAt,
  feedback:feedbackRows(x.events),
  selfManaged:x.self_managed===true,reviewRequired:x.review_required!==false,coordinationRequired:!!x.coordination_required&&Object.values(x.coordination_required).some(Boolean),
  pause:x.pause?{...x.pause,active:true,recheckLabel:stamp(x.pause.recheck_at)}:null,
  executorLabel:x.executor&&x.executor.name||'待确认',reviewerLabel:x.self_managed===true?'自办 · 无需验收':x.reviewer&&x.reviewer.name||'待确认',caps:x.capabilities};
}
function projectLegacy(x,id){
 if(!x||x.task_id!==id||x.execution_mode!=='legacy'||!([2,3].includes(x.supported_protocol_version)||[2,3].includes(x.protocol_version))||!Array.isArray(x.events)||!x.capabilities||typeof x.capabilities.initialize_execution!=='boolean')throw Error('历史任务管理信息不完整，请刷新');
 return {raw:x,versions:view.versions(x),caps:x.capabilities,feedback:feedbackRows(x.events),items:[]};
}
function initializeVersions(x){
 const source=x.raw.initialize_versions||(x.raw.execution_mode==='legacy'?x.raw:null);
 if(!source||!Number.isSafeInteger(source.checklist_revision))throw Error('当前服务尚未提供接入版本，请刷新后重试');
 return view.versions(source);
}
function contribution(value){
 if(value===null)return null;
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).some(k=>!['metric_code','value','note'].includes(k)))throw Error('实际贡献字段不完整');
 if(typeof value.metric_code!=='string'||!value.metric_code.trim()||value.metric_code!==value.metric_code.trim()||value.metric_code.length>100||/[\x00-\x1f\x7f]/.test(value.metric_code))throw Error('请选择有效指标');
 if(typeof value.value!=='string'||value.value!==value.value.trim()||!/^(0|[1-9][0-9]{0,15})(?:\.[0-9]{1,2})?$/.test(value.value))throw Error('贡献量请填写非负数字串，最多 16 位整数和 2 位小数');
 if(typeof value.note!=='string'||!value.note.trim()||[...value.note.trim()].length>2000)throw Error('请填写 1–2000 字实际贡献说明');
 return {metric_code:value.metric_code,value:value.value,note:value.note.trim()};
}
function create(api,session=()=>getApp().globalData.session,normalizeTask){return {
 ...require('./taskLightweightPage').create(api,session,normalizeTask),
 async loadExecution(){
  if(!this.data.task||typeof api.getTaskExecution!=='function')return;
  const support=(session().capabilities||{}).task_execution;
  let mode;try{mode=taskExecutionMode.mode(this.data.task,support);}catch(error){this.setData({execution:null,executionLoading:false,executionError:error.message});return;}
  if(mode==='legacy'&&!taskExecutionMode.supportsVersion(support,2)){this.executionSeq=(this.executionSeq||0)+1;this.setData({execution:null,executionLoading:false,executionError:''});return;}
  if(!support||support.available!==true||!taskExecutionMode.supportsVersion(support,mode==='execution_v3'?3:mode==='execution_v1'?1:2)){this.setData({execution:null,executionLoading:false,executionError:'当前服务尚未提供新版任务执行能力，仅可查看；请等待配套更新'});return;}
  const id=this.data.taskId,identity=access.identity(session()),seq=this.executionSeq=(this.executionSeq||0)+1;
  const current=()=>!this.isUnloading&&this.taskPageVisible!==false&&seq===this.executionSeq&&id===this.data.taskId&&identity===access.identity(session());
  this.setData({executionLoading:true,executionError:''});
  try{const result=await api.getTaskExecution(id);if(!current())return;
   if(!result||!Array.isArray(result.events))throw Error('任务执行信息不完整，请刷新');
   if(mode==='execution_v3'){this.applyLightweight(result);return;}
   const history=this.data.executionHistory||[],combined={...result,events:(history.length?[...new Map([...history,...result.events].map(event=>[event.id,event])).values()]:result.events.slice()).sort(eventOrder)};
   const execution=mode==='legacy'?projectLegacy(combined,id):project(combined,id);
   const confirmed=result.requirements||{};
   const snapshot={...this.data.task,execution_mode:result.execution_mode||this.data.task.execution_mode,self_managed:result.self_managed,review_required:result.review_required,status:result.status,version_no:result.version_no,executionCompletionNote:execution.completionNote,executionCompletedAt:execution.completedAt,
    events:Object.prototype.hasOwnProperty.call(result,'events_next_cursor')?combined.events:taskHistory.mergeExecutionEvents(this.data.task.events,combined.events)};
   for(const key of ['title','description','deliverable','acceptance_criteria','due_at','priority_code'])if(Object.prototype.hasOwnProperty.call(confirmed,key))snapshot[key]=confirmed[key];
   if(mode!=='legacy'&&(Object.prototype.hasOwnProperty.call(result,'executor_user_id')||Object.prototype.hasOwnProperty.call(result,'executor'))){
    const executor=result.executor||{},userId=result.executor_user_id||executor.user_id||'';
    snapshot.owner=execution.executorLabel;snapshot.owner_name=execution.executorLabel;snapshot.ownerUserId=userId;
    snapshot.assignees=[{user_id:userId,name:execution.executorLabel,responsibility:'owner',team_id:executor.team_id||null},
     ...(Array.isArray(snapshot.assignees)?snapshot.assignees:[]).filter(person=>person.responsibility!=='owner'&&person.user_id!==userId)];
   }
   const task=normalizeTask?normalizeTask(snapshot,session()):snapshot;
   if(mode!=='legacy'&&task&&result.status==='pending_review'){
    task.canReview=result.capabilities.review_completion===true;
    task.statusLabel=result.current_reviewer===session().userId?'待我验收':'等待验收';
   }
   const cursors=this.data.executionHistoryCursors||{},historyPatch={};
   if(Object.prototype.hasOwnProperty.call(result,'events_next_cursor')&&!Object.prototype.hasOwnProperty.call(cursors,'events'))historyPatch.executionHistoryCursors={...cursors,events:result.events_next_cursor};
   if(Object.prototype.hasOwnProperty.call(result,'submissions_next_cursor')){
    const old=this.data.executionSubmissions||[],head=result.submissions||[];
    historyPatch.executionSubmissions=mergeSubmissions(old,head);
    historyPatch.executionHistoryCursors={...(historyPatch.executionHistoryCursors||cursors),submissions:submissionCursor(cursors,result.submissions_next_cursor,old,head)};
   }
   this.setData({...historyPatch,task,executionActorUserId:session().userId,taskMigration:mode==='legacy'?execution:null,execution:mode==='legacy'?null:execution,executionContext:JSON.stringify([identity,id]),executionError:'',executionSynced:stamp(new Date().toISOString()),executionOffline:false});
  }catch(error){if(current())this.setData({executionError:error.message||'执行信息读取失败',executionOffline:error.code==='NETWORK_ERROR'});}
  finally{if(current())this.setData({executionLoading:false});}
 },
 openTaskMigration(){const x=this.data.taskMigration||this.data.execution;if(x&&x.caps.initialize_execution===true&&!this.data.executionBusy){try{this.migrationVersions=initializeVersions(x);}catch(error){this.setData({executionError:error.message});return;}this.migrationContext=JSON.stringify([access.identity(session()),this.data.taskId]);this.setData({migrationOpen:true,migrationNote:'',migrationError:'',migrationResumeStatus:'',migrationResumeOptions:(x.raw.initialize_resume_options||[]).filter(value=>RESUME_LABELS[value]).map(value=>({value,label:RESUME_LABELS[value]})),migrationResumeIndex:-1});}},
 closeTaskMigration(){if(!this.data.executionBusy)this.setData({migrationOpen:false});},
 chooseMigrationResume(e){const index=Number(e.detail.value),option=(this.data.migrationResumeOptions||[])[index];if(option&&!this.data.executionBusy)this.setData({migrationResumeStatus:option.value,migrationResumeIndex:index,migrationError:''});},
 inputMigrationNote(e){this.setData({migrationNote:e.detail.value,migrationError:''});},
 async initializeTask(){
  const x=this.data.taskMigration||this.data.execution,reason=String(this.data.migrationNote||'').trim();
  if(!x||!reason||[...reason].length>1000){this.setData({migrationError:'请填写 1–1000 字接入原因'});return;}
  const options=x.raw.initialize_resume_options||[],resume=this.data.migrationResumeStatus;
  if(options.length&&!options.includes(resume)){this.setData({migrationError:'请明确选择历史延期任务继续的阶段'});return;}
  if(this.migrationContext!==JSON.stringify([access.identity(session()),this.data.taskId])||JSON.stringify(this.migrationVersions)!==JSON.stringify(initializeVersions(x))){this.setData({migrationError:'任务已变化，请关闭后重新核对'});return;}
  if(await this.sendExecution('initialize_execution',{...this.migrationVersions,reason,...(options.length?{resume_status:resume}:{})})){this.setData({migrationOpen:false});await this.loadTask();}else this.setData({migrationError:this.data.executionError||'未保存，原因已保留'});
 },
 openChecklist(){const x=this.data.execution;if(!x||x.caps.save_checklist!==true||this.data.executionBusy||this.data.executionError)return;this.checklistBase=checklistEditor.draft(x.items);this.checklistVersions={...x.versions};this.checklistContext=JSON.stringify([access.identity(session()),this.data.taskId]);this.setData({checklistDraft:checklistEditor.draft(x.items),checklistImpact:checklistEditor.impact(x.items,x.items),checklistReason:'',checklistError:'',checklistNeedsReview:false});},
 updateChecklistDraft(items){this.setData({checklistDraft:items,checklistImpact:checklistEditor.impact(items,this.data.execution&&this.data.execution.items||[]),checklistError:''});},
 moveChecklist(e){if(this.data.executionBusy||!this.data.checklistDraft)return;const items=this.data.checklistDraft.slice(),index=items.findIndex(item=>item.item_id===e.currentTarget.dataset.id),delta=Number(e.currentTarget.dataset.delta),next=index+delta;if(index<0||![-1,1].includes(delta)||next<0||next>=items.length)return;[items[index],items[next]]=[items[next],items[index]];this.updateChecklistDraft(items);},
 inputChecklist(e){if(this.data.executionBusy||!this.data.checklistDraft)return;const {id,key}=e.currentTarget.dataset;if(key==='reason'){this.setData({checklistReason:e.detail.value});return;}if(['title','acceptance_criteria'].includes(key))this.updateChecklistDraft(this.data.checklistDraft.map(item=>item.item_id===id?{...item,[key]:e.detail.value}:item));},
 addChecklist(){if(this.data.checklistDraft&&!this.data.executionBusy&&this.data.checklistDraft.length<100)this.updateChecklistDraft(this.data.checklistDraft.concat(checklistEditor.add()));},
 removeChecklist(e){if(this.data.checklistDraft&&!this.data.executionBusy)this.updateChecklistDraft(this.data.checklistDraft.filter(item=>item.item_id!==e.currentTarget.dataset.id));},
 closeChecklist(){if(!this.data.executionBusy)this.setData({checklistDraft:null,checklistNeedsReview:false});},
 confirmChecklistLatest(){const x=this.data.execution;if(!x||this.data.executionBusy||this.data.executionLoading||this.checklistContext!==JSON.stringify([access.identity(session()),this.data.taskId]))return;this.checklistBase=checklistEditor.draft(x.items);this.checklistVersions={...x.versions};this.setData({checklistNeedsReview:false,checklistError:''});},
 async saveChecklist(){
  const x=this.data.execution;if(!x||!this.data.checklistDraft||this.data.executionBusy)return;
  if(this.checklistContext!==JSON.stringify([access.identity(session()),this.data.taskId])||JSON.stringify(this.checklistVersions)!==JSON.stringify(x.versions)){this.setData({checklistNeedsReview:true,checklistError:'任务已变化，清单草稿保留；请对照当前清单后确认'});return;}
  try{const checklist=checklistEditor.prepare(this.data.checklistDraft,this.checklistBase),reason=String(this.data.checklistReason||'').trim();if(!reason||[...reason].length>1000)throw Error('请填写 1–1000 字调整原因');
   if(await this.sendExecution('save_checklist',{...this.checklistVersions,checklist,reason})){this.setData({checklistDraft:null});await this.loadExecution();}else this.setData({checklistNeedsReview:JSON.stringify(this.checklistVersions)!==JSON.stringify(this.data.execution?.versions),checklistError:this.data.executionError||'清单未保存，草稿已保留'});
  }catch(error){this.setData({checklistError:error.message});}
 },
 async loadExecutionHistory(e){
  const kind=e&&e.currentTarget&&e.currentTarget.dataset.kind||'events';if(!['events','submissions'].includes(kind)||this.data.executionHistoryLoading||!(this.data.execution||this.data.taskMigration)||typeof api.getTaskExecutionHistory!=='function')return;
  const id=this.data.taskId,identity=access.identity(session()),cursor=this.data.executionHistoryCursors&&this.data.executionHistoryCursors[kind];
  const current=()=>id===this.data.taskId&&identity===access.identity(session())&&!this.isUnloading;
  if(cursor===null)return;this.setData({executionHistoryLoading:true,executionHistoryError:''});
  try{const r=await api.getTaskExecutionHistory(id,{kind,limit:50,...(cursor?{cursor}:{})});if(!current())return;
   if(!r||!Array.isArray(r.items))throw Error('历史记录响应不完整');
   const key=kind==='events'?'executionHistory':'executionSubmissions',old=this.data[key]||[],seen=new Set(old.map(item=>item.id));
   this.setData({[key]:kind==='submissions'?mergeSubmissions(old,r.items):old.concat(r.items.filter(item=>!seen.has(item.id))),executionHistoryCursors:{...(this.data.executionHistoryCursors||{}),[kind]:r.next_cursor||null}});
   if(kind==='events'){const taskSnapshot={...this.data.task,events:taskHistory.mergeExecutionEvents(this.data.task.events,this.data.executionHistory)};this.setData({task:normalizeTask?normalizeTask(taskSnapshot,session()):taskSnapshot});const currentExecution=this.data.execution||this.data.taskMigration,merged=new Map([...(currentExecution.raw.events||[]),...(this.data.executionHistory||[])].map(item=>[item.id,item]));this.setData({[this.data.execution?'execution':'taskMigration']:{...currentExecution,feedback:feedbackRows([...merged.values()].sort(eventOrder))}});}
  }catch(error){if(current())this.setData({executionHistoryError:error.message||'历史加载失败'});}finally{if(current())this.setData({executionHistoryLoading:false});}
 },
 async executionFeedback(e){
  const panel=this.selectComponent('#taskExecutionPanel'),detail=e.detail;
  const result=await this.sendExecution('append_feedback',detail.command);
  if(panel)panel.settleFeedback({context:detail.context,success:result,error:result?'':this.data.executionError});
  if(result)await this.loadExecution();
 },
 async sendExecution(action,body){
  const x=this.data.execution||this.data.taskMigration;if(x&&x.lightweight)return false;if(!x||x.raw.task_id!==this.data.taskId||this.data.executionContext!==JSON.stringify([access.identity(session()),this.data.taskId])||this.data.executionBusy||this.data.executionError||x.caps[action]!==true)return false;
  if(JSON.stringify(view.versions(body))!==JSON.stringify(action==='initialize_execution'?initializeVersions(x):x.versions)){this.setData({executionError:'任务版本已变化，请刷新并重新确认'});return false;}
  const id=this.data.taskId,identity=access.identity(session());
  const current=()=>!this.isUnloading&&this.taskPageVisible!==false&&id===this.data.taskId&&identity===access.identity(session());
  this.executionSeq=(this.executionSeq||0)+1;this.setData({executionLoading:false,executionBusy:true,executionError:''});
  try{const result=await api.taskExecutionCommand(id,action,body);if(!current())return false;
   if(!result||!result.execution||result.execution.task_id!==id)throw Error('保存回执不完整，请用原内容重试');
   project(result.execution,id);return true;
  }catch(error){if(current()){
   this.setData({executionError:error.message||'保存失败，草稿已保留'});
   if(error.statusCode===403)this.setData({execution:{...x,caps:{}}});
   if(error.statusCode===409){await this.loadExecution();this.setData({executionError:'任务已变化，草稿已保留；请重新核对后提交'});}
  }return false;}finally{if(current())this.setData({executionBusy:false});}
 },
 executionAction(e){
  const detail=e.detail,x=this.data.execution;if(!x||this.data.executionBusy||this.data.executionError)return;
  const action={completeSelf:'complete_self',cancel:'cancel_task',progress:'set_item_progress',pause:'pause',recheck:'recheck_pause',recheck_pause:'recheck_pause',withdrawCompletion:'withdraw_completion',withdraw_completion:'withdraw_completion',resume:'resume',accept:'accept_task',decline:'decline_task',complete:'submit_completion',approve:'review_completion',reject:'review_completion',executorAccept:'respond_executor_handover',executorReject:'respond_executor_handover',reviewerAccept:'respond_reviewer_handover',reviewerReject:'respond_reviewer_handover',requirementsAccept:'respond_requirements',requirementsReject:'respond_requirements',requirementsWithdraw:'withdraw_requirements'}[detail.action];if(!action||x.caps[action]!==true)return;
  const item=x.items.find(i=>i.item_id===detail.itemId);
  if(action==='set_item_progress'&&!item)return;
  if(['resume','recheck_pause'].includes(action)&&!(x.pause&&x.pause.id))return;
  if(['withdraw_completion','complete_self','review_completion'].includes(action)&&!x.raw.current_submission)return;
  const handover=action==='respond_executor_handover'?x.raw.execution_handover:action==='respond_reviewer_handover'?x.raw.reviewer_handover:null;
  if(action.includes('handover')&&(!handover||handover.status!=='pending'))return;
  if(action.includes('requirements')&&!x.raw.pending_requirements)return;
  this.executionDraftVersions={...x.versions};
  this.executionDraftContext=JSON.stringify([access.identity(session()),this.data.taskId]);
  this.setData({executionDialog:{action,itemId:item&&item.item_id,definitionVersion:item&&item.definition_version,pauseId:x.pause&&x.pause.id,handoverId:handover&&handover.id,changeId:x.raw.pending_requirements&&x.raw.pending_requirements.id,decision:detail.action.endsWith('Reject')||detail.action==='reject'?'reject':action==='review_completion'?'approve':'accept',submissionId:x.raw.current_submission||x.raw.task_id},executionReworkIds:[],executionMissingDeliverables:'',executionMissingResolution:'',executionNote:'',executionDate:'',executionTime:'09:00',executionState:Math.max(0,STATES.indexOf(item&&item.state)),executionDialogError:'',executionStates:LABELS});
 },
 openExecutionHandover(e){
  const purpose=e.currentTarget.dataset.purpose,x=this.data.execution,action=purpose==='executor'?'initiate_executor_handover':purpose==='reviewer'?'initiate_reviewer_handover':null;
  if(!action||!x||x.caps[action]!==true||this.data.executionBusy||this.data.executionError)return;
  this.handoverVersions={...x.versions};this.handoverIdentity=access.identity(session());
  this.setData({handoverPurpose:purpose,handoverQuery:'',handoverCandidates:[],handoverOffset:0,handoverMore:false,handoverTarget:'',handoverTargetLabel:'',handoverReason:'',handoverError:''});return this.loadHandoverCandidates();
 },
 inputHandover(e){if(this.data.executionBusy)return;const key=e.currentTarget.dataset.key;if(['handoverQuery','handoverReason'].includes(key)){if(key==='handoverQuery'){this.handoverSeq=(this.handoverSeq||0)+1;this.setData({handoverCandidates:[],handoverMore:false,handoverLoading:false});}this.setData({[key]:e.detail.value,handoverError:''});}},
 async loadHandoverCandidates(e){
  const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more),purpose=this.data.handoverPurpose,id=this.data.taskId;
  if(!purpose||this.data.handoverLoading||this.data.executionBusy)return;
  const seq=this.handoverSeq=(this.handoverSeq||0)+1,identity=access.identity(session()),offset=more?this.data.handoverOffset:0,q=String(this.data.handoverQuery||'').trim();
  const current=()=>!this.isUnloading&&this.taskPageVisible!==false&&this.handoverSeq===seq&&this.data.handoverPurpose===purpose&&id===this.data.taskId&&identity===access.identity(session());
  this.setData({handoverLoading:true,handoverError:''});
  try{const r=await api.getTaskExecutionCandidates(id,{purpose,q,limit:20,offset});if(!current())return;
   if(!r||!Array.isArray(r.items)||r.items.length>20||r.limit!==20||r.offset!==offset||!Number.isSafeInteger(r.total)||r.total<0||r.items.some(i=>!i.user_id||typeof i.display_name!=='string'||!Array.isArray(i.teams)))throw Error('交接候选响应不完整，请刷新');
   const rows=more?this.data.handoverCandidates.slice():[],seen=new Set(rows.map(i=>i.user_id));for(const row of r.items)if(!seen.has(row.user_id)){rows.push(row);seen.add(row.user_id);}
   this.setData({handoverCandidates:rows,handoverOffset:offset+r.items.length,handoverMore:r.items.length===20&&offset+r.items.length<r.total});
  }catch(error){if(current())this.setData({handoverError:error.message||'候选读取失败'});}finally{if(current())this.setData({handoverLoading:false});}
 },
 selectHandoverCandidate(e){
  if(this.data.executionBusy||this.data.handoverLoading)return;const row=this.data.handoverCandidates.find(i=>i.user_id===e.currentTarget.dataset.id);
  if(row)this.setData({handoverTarget:row.user_id,handoverTargetLabel:row.display_name,handoverError:''});
 },
 closeExecutionHandover(){if(this.data.executionBusy)return;this.handoverSeq=(this.handoverSeq||0)+1;this.setData({handoverPurpose:'',handoverLoading:false});},
 async submitExecutionHandover(){
  const x=this.data.execution,purpose=this.data.handoverPurpose;if(!x||!purpose||this.data.executionBusy||this.data.handoverLoading||this.data.handoverError)return;
  if(this.handoverIdentity!==access.identity(session())||JSON.stringify(this.handoverVersions)!==JSON.stringify(x.versions)){this.setData({handoverError:'任务责任已变化，请关闭后重新核对；原因已保留'});return;}
  const reason=String(this.data.handoverReason||'').trim();if(!this.data.handoverTarget||!reason||[...reason].length>1000){this.setData({handoverError:'请选择接手人员并填写 1–1000 字交接原因'});return;}
  const action=purpose==='executor'?'initiate_executor_handover':'initiate_reviewer_handover';
  const ok=await this.sendExecution(action,{...this.handoverVersions,target_user_id:this.data.handoverTarget,reason});
  if(ok){this.closeExecutionHandover();await this.loadExecution();}else this.setData({handoverError:this.data.executionError||'交接未保存，原因已保留'});
 },
 openRequirements(){
  const x=this.data.execution;if(!x||x.caps.propose_requirements!==true||this.data.executionBusy||this.data.executionError)return;
  this.requirementsBase=requirements.draft(x.raw.requirements);this.requirementsVersions={...x.versions};this.requirementsContext=JSON.stringify([access.identity(session()),this.data.taskId]);
  const value=requirements.draft(this.requirementsBase),date=new Date(value.due_at);
  this.setData({requirementsDraft:value,requirementsReason:'',requirementsPreview:null,requirementsError:'',requirementsDate:Number.isFinite(date.getTime())?`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`:'',requirementsTime:Number.isFinite(date.getTime())?`${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`:'09:00',requirementsPriority:['normal','medium','high','urgent'].indexOf(value.priority_code),requirementsPriorities:['普通','中','高','紧急']});
 },
 inputRequirements(e){
  if(this.data.executionBusy||!this.data.requirementsDraft)return;
  const {key,id}=e.currentTarget.dataset,value=e.detail.value,draft=requirements.draft(this.data.requirementsDraft);
  if(key==='reason'){this.setData({requirementsReason:value,requirementsPreview:null});return;}
  if(id){if(!['title','acceptance_criteria'].includes(key))return;draft.checklist=draft.checklist.map(item=>item.item_id===id?{...item,[key]:value}:item);}
  else if(key==='priority_code'){draft.priority_code=['normal','medium','high','urgent'][Number(value)];this.setData({requirementsPriority:Number(value)});}
  else if(['date','time'].includes(key)){this.setData({[key==='date'?'requirementsDate':'requirementsTime']:value});const date=new Date(`${this.data.requirementsDate}T${this.data.requirementsTime}:00`);if(Number.isFinite(date.getTime()))draft.due_at=date.toISOString();}
  else if(['title','description','deliverable','acceptance_criteria'].includes(key))draft[key]=value;
  else return;
  this.setData({requirementsDraft:draft,requirementsPreview:null,requirementsError:''});
 },
 addRequirementItem(){if(this.data.executionBusy||!this.data.requirementsDraft||this.data.requirementsDraft.checklist.length>=100)return;this.setData({requirementsDraft:{...this.data.requirementsDraft,checklist:this.data.requirementsDraft.checklist.concat(requirements.addItem())},requirementsPreview:null});},
 removeRequirementItem(e){if(this.data.executionBusy||!this.data.requirementsDraft)return;this.setData({requirementsDraft:{...this.data.requirementsDraft,checklist:this.data.requirementsDraft.checklist.filter(i=>i.item_id!==e.currentTarget.dataset.id)},requirementsPreview:null});},
 closeRequirements(){if(!this.data.executionBusy)this.setData({requirementsDraft:null,requirementsPreview:null});},
 previewRequirements(){
  try{const value=requirements.prepare(this.data.requirementsDraft,this.requirementsBase,{independentChecklist:this.data.execution.raw.protocol_version===2}),diff=requirements.differences(this.data.execution.raw.protocol_version===2?{...this.requirementsBase,checklist:[]}:this.requirementsBase,value);
   if(!diff.length)throw Error('尚未修改任务要求');if(!String(this.data.requirementsReason||'').trim()||[...this.data.requirementsReason.trim()].length>1000)throw Error('请填写 1–1000 字变更原因');
   this.requirementsPrepared=value;this.setData({requirementsPreview:diff,requirementsError:''});
  }catch(error){this.setData({requirementsError:error.message});}
 },
 async submitRequirements(){
  if(!this.data.requirementsPreview||this.data.executionBusy)return;
  if(this.requirementsContext!==JSON.stringify([access.identity(session()),this.data.taskId])||JSON.stringify(this.requirementsVersions)!==JSON.stringify(this.data.execution.versions)){this.setData({requirementsError:'任务或责任已变化，当前草稿保留；请重新核对最新要求'});return;}
  const success=await this.sendExecution('propose_requirements',{...this.requirementsVersions,requirements:this.requirementsPrepared,reason:this.data.requirementsReason.trim()});
  if(success){this.setData({requirementsDraft:null,requirementsPreview:null});await this.loadExecution();}else this.setData({requirementsError:this.data.executionError||'变更申请未保存，草稿已保留'});
 },
 executionPageAction(e){return this.executionAction({detail:{action:e.currentTarget.dataset.action}});},
 executionRework(e){this.setData({executionReworkIds:e.detail.value});},
 executionInput(e){const key=e.currentTarget.dataset.key;if(['executionNote','executionDate','executionTime','executionState','executionMissingDeliverables','executionMissingResolution'].includes(key))this.setData({[key]:e.detail.value});},
 closeExecutionDialog(){if(!this.data.executionBusy)this.setData({executionDialog:null});},
 async submitExecutionDialog(event){
  const dialog=this.data.executionDialog,x=this.data.execution;if(!dialog||!x||this.data.executionBusy)return;
  if(this.executionDraftContext!==JSON.stringify([access.identity(session()),this.data.taskId])||JSON.stringify(this.executionDraftVersions)!==JSON.stringify(x.versions)){this.setData({executionDialogError:'任务版本已变化，请关闭后重新核对；说明已保留'});return;}
  const note=String(this.data.executionNote||'').trim(),optional=dialog.action==='accept_task'||(['respond_executor_handover','respond_reviewer_handover','respond_requirements','review_completion'].includes(dialog.action)&&dialog.decision!=='reject');if((!note&&!optional)||[...note].length>(['set_item_progress','submit_completion','complete_self','review_completion'].includes(dialog.action)?4000:1000)){this.setData({executionDialogError:'请填写有效的成果依据或原因'});return;}
  let body={...this.executionDraftVersions};
  if(dialog.action==='set_item_progress')body={...body,item_id:dialog.itemId,definition_version:dialog.definitionVersion,state:STATES[Number(this.data.executionState)],note};
  else if(dialog.action==='resume')body={...body,pause_id:dialog.pauseId,reason:note};
  else if(['respond_executor_handover','respond_reviewer_handover'].includes(dialog.action))body={...body,handover_id:dialog.handoverId,decision:dialog.decision,reason:note};
  else if(dialog.action==='respond_requirements')body={...body,change_id:dialog.changeId,decision:dialog.decision,reason:note};
  else if(dialog.action==='withdraw_requirements')body={...body,change_id:dialog.changeId,reason:note};
  else if(dialog.action==='withdraw_completion')body={...body,submission_id:dialog.submissionId,reason:note};
  else if(dialog.action==='complete_self')body={...body,note,submission_id:dialog.submissionId};
  else if(dialog.action==='cancel_task')body={...body,reason:note};
  else if(dialog.action==='submit_completion'){if(x.raw.missing_deliverables&&!String(this.data.executionMissingResolution||'').trim()){this.setData({executionDialogError:'请说明如何补齐上次遗漏的交付'});return;}body={...body,note,...(x.raw.missing_deliverables?{missing_deliverables_resolution:String(this.data.executionMissingResolution||'').trim()}: {})};if(event&&event.detail&&Object.prototype.hasOwnProperty.call(event.detail,'actual_contribution')&&event.detail.actual_contribution!==undefined){try{body.actual_contribution=contribution(event.detail.actual_contribution);}catch(error){this.setData({executionDialogError:error.message});return;}}}
  else if(['accept_task','decline_task'].includes(dialog.action))body={...body,reason:note};
  else if(dialog.action==='review_completion'){const ids=this.data.executionReworkIds||[];if(dialog.decision==='reject'&&x.items.length&&!ids.length&&!String(this.data.executionMissingDeliverables||'').trim()){this.setData({executionDialogError:'请选择返工项或说明遗漏的整体交付'});return;}body={...body,submission_id:dialog.submissionId,decision:dialog.decision,note,rework_item_ids:dialog.decision==='reject'?ids:[],...(x.raw.protocol_version===2?{missing_deliverables:String(this.data.executionMissingDeliverables||'').trim()}: {})};}
  else{const date=new Date(`${this.data.executionDate}T${this.data.executionTime}:00`);if(!Number.isFinite(date.getTime())||date.getTime()<=Date.now()){this.setData({executionDialogError:'请选择未来的复查日期和时间'});return;}body={...body,reason:note,recheck_at:date.toISOString(),...(dialog.action==='recheck_pause'?{pause_id:dialog.pauseId}:{})};}
  const success=await this.sendExecution(dialog.action,body);
  if(success){this.setData({executionDialog:null});await this.loadExecution();}
  else this.setData({executionDialogError:this.data.executionError||'未保存，请核对后重试'});
 },
};}
module.exports={create,project,feedbackRows};
