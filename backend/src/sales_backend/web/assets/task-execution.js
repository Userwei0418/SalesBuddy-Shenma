import {api,esc,date,field,options,dialog,dialogIntent,state} from './core.js';
import {deadlineInput,deadlineValue} from './task-imports.js';

const path=id=>'/api/v1/tasks/'+encodeURIComponent(id)+'/execution';
const labels={initialize_execution:'接入新版任务流程',save_checklist:'维护推进清单',set_item_progress:'记录推进成果',append_feedback:'发布反馈',pause:'暂停',resume:'恢复执行',recheck_pause:'延后复查',initiate_executor_handover:'执行人交接',respond_executor_handover:'回应执行交接',initiate_reviewer_handover:'验收人交接',respond_reviewer_handover:'回应验收交接',propose_requirements:'修改整体要求',respond_requirements:'确认要求变更',withdraw_requirements:'撤回要求变更',submit_completion:'提交整体验收',review_completion:'验收本次提交',withdraw_completion:'撤回完成提交',complete_self:'确认自办完成',accept_task:'接受任务',decline_task:'拒绝任务',cancel_task:'取消任务'};
const states={not_started:'未开始',in_progress:'进行中',completed:'已完成',rework:'待返工'};
const taskStates={pending_confirm:'待接受',pending_execution:'待执行',in_progress:'执行中',pending_review:'待验收',completed:'已完成',cancelled:'已取消'};
const text=(name,label,value='',required=true)=>field(label,name,value,{type:'textarea',required});
const reasons=(required=true)=>text('reason','本次操作原因','',required);
const def=item=>({item_id:item.item_id,definition_version:item.definition_version,title:item.title,acceptance_criteria:item.acceptance_criteria});
export function executionVersions(execution){
  const value={version_no:execution.version_no,requirements_version:execution.requirements_version,responsibility_version:execution.responsibility_version};
  if(execution.protocol_version===2||execution.execution_mode==='legacy')value.checklist_revision=execution.checklist_revision||1;
  return value;
}
function requirementsHTML(value={}){
  const names={title:'标题',description:'任务描述',deliverable:'整体交付物',acceptance_criteria:'验收标准',due_at:'截止时间',priority_code:'优先级'};
  return '<dl>'+Object.entries(names).map(([key,label])=>`<dt>${label}</dt><dd>${esc(key==='due_at'?date(value[key]):value[key]||'未填写')}</dd>`).join('')+'</dl>';
}
function snapshotHTML(value={}){
  return requirementsHTML(value.requirements)+`<ul>${(value.items||[]).map(item=>`<li><strong>${esc(item.title)}</strong> · ${esc(states[item.state]||item.state||'未开始')}<p>${esc(item.acceptance_criteria)}</p><p>${esc(item.note||'')}</p></li>`).join('')}</ul>${value.missing_deliverables?`<p>原验收遗漏：${esc(value.missing_deliverables)}</p><p>补齐说明：${esc(value.missing_deliverables_resolution||'未填写')}</p>`:''}`;
}
function eventHTML(event){
  const envelope=event.body||event.payload||{},b={...envelope,...(envelope.command||{})};
  return `<li><strong>${esc(b.actor_name||event.actor_name||'历史记录')}</strong> · ${esc(labels[b.action||event.kind]||event.kind)} · ${esc(date(event.occurred_at))}<p>${esc(b.text||b.note||b.reason||'')}</p>${b.next_step?`<p>下一步：${esc(b.next_step)}</p>`:''}${b.blocker?`<p>阻碍：${esc(b.blocker)}</p>`:''}${b.reply_to_event_id?'<small>回复已有反馈；原记录保留</small>':''}${b.corrects_event_id?'<small>更正已有反馈；原记录保留</small>':''}${(b.evidence_urls||[]).map(url=>/^https?:\/\//.test(url)?`<p><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看成果资料</a></p>`:'').join('')}</li>`;
}
export async function showExecutionTask(task){
  const intent=dialogIntent(),execution=await api(path(task.id));if(!intent())return;
  const caps=execution.capabilities||{},requirements=execution.requirements||{},progress=execution.progress||{};
  const actionable=Object.keys(labels).filter(action=>caps[action]===true);
  const root=dialog('任务执行 · '+(requirements.title||task.title),`<div class="notice">${esc(taskStates[execution.status]||execution.status)} · ${execution.self_managed?'自办任务':'协作任务'} · ${progress.percent==null?'尚无可确认进度':`${progress.completed}/${progress.total} 项 · ${progress.percent}%`}<br>要求版本 ${esc(execution.requirements_version)} · 清单版本 ${esc(execution.checklist_revision||1)} · 责任版本 ${esc(execution.responsibility_version)}</div>${execution.requirements_incomplete?'<p class="error-box">整体交付物或验收标准尚未补齐，请先明确整体要求。</p>':''}${Object.values(execution.coordination_required||{}).some(Boolean)?'<p class="notice">责任资格或待确认流程需要协调，请检查交接和要求变更。</p>':''}<h3>整体要求</h3><p>${esc(requirements.description||task.description)}</p><p>交付物：${esc(requirements.deliverable||'待补充')}</p><p>验收标准：${esc(requirements.acceptance_criteria||'待补充')}</p><p>截止 ${esc(date(requirements.due_at||task.due_at))}</p>${execution.pause?`<p class="notice">已暂停：${esc(execution.pause.reason)}；复查 ${esc(date(execution.pause.recheck_at))}</p>`:''}${execution.pending_requirements?`<p class="notice">有待确认的整体要求变更；生效前仍按当前版本执行。</p><details><summary>查看变更后的完整要求</summary><h4>变更前</h4>${requirementsHTML(execution.pending_requirements.before)}<h4>变更后</h4>${requirementsHTML(execution.pending_requirements.after)}</details>`:''}${execution.missing_deliverables?`<p class="error-box">验收遗漏：${esc(execution.missing_deliverables)}</p>`:''}<h3>推进清单</h3><ul>${(execution.checklist||[]).filter(i=>!i.deleted).map(i=>`<li><strong>${esc(i.title)}</strong> · ${esc(states[i.state]||i.state||'未开始')}${i.requires_revalidation?' · 待重新复核':''}<p>${esc(i.acceptance_criteria)}</p><p>${esc(i.note||i.evidence||'')}</p></li>`).join('')||'<li>执行人尚未建立清单。未建清单不显示 0% 或 100%。</li>'}</ul><div class="actions">${actionable.map(action=>`<button type="button" data-execution-action="${action}">${esc(action==='submit_completion'&&execution.self_managed?'确认自办完成':labels[action])}</button>`).join('')}</div><h3>完整处理历史</h3><div class="actions"><button type="button" id="execution-events">反馈与流转记录</button><button type="button" id="execution-submissions">提交与验收快照</button></div><ul>${(execution.events||[]).slice(0,15).map(eventHTML).join('')||'<li>暂无记录</li>'}</ul>`,{wide:true});
  const current=dialogIntent({liveForm:true});
  const safe=work=>async()=>{try{if(current())await work();}catch(error){if(current())root.querySelector('.form-error').textContent=error.message;}};
  root.querySelectorAll('[data-execution-action]').forEach(button=>button.onclick=safe(()=>commandDialog(task,execution,button.dataset.executionAction)));
  root.querySelector('#execution-events').onclick=safe(()=>historyDialog(task,execution,'events'));
  root.querySelector('#execution-submissions').onclick=safe(()=>historyDialog(task,execution,'submissions'));
  return root;
}
async function historyDialog(task,execution,kind){
  const intent=dialogIntent(),first=await api(path(task.id)+'/history?kind='+kind);if(!intent())return;
  let items=first.items||[],cursor=first.next_cursor,busy=false;
  const render=()=>kind==='events'?items.map(event=>eventHTML(event)+(event.kind==='append_feedback'&&execution.capabilities?.append_feedback?`<li><button type="button" data-reply="${esc(event.id)}">回复这条反馈</button>${event.actor_user_id===state.actor?.user_id?`<button type="button" data-correct="${esc(event.id)}">更正本人反馈</button>`:''}</li>`:'')).join(''):items.map(item=>`<li><strong>${esc(date(item.created_at))}</strong><p>${esc(item.snapshot?.note||'历史完成提交')}</p><details><summary>查看当时的整体要求、清单及成果快照</summary>${snapshotHTML(item.snapshot)}</details></li>`).join('');
  const root=dialog(kind==='events'?'反馈与流转历史':'完成提交快照',`<ul id="execution-history">${render()}</ul><button type="button" id="execution-more" ${cursor?'':'hidden'}>加载更早记录</button>`,{wide:true,footer:'<button type="button" id="execution-return">返回任务</button>'});
  const current=dialogIntent({liveForm:true});
  const bindReplies=()=>{
    root.querySelectorAll('[data-reply],[data-correct]').forEach(button=>button.onclick=async()=>{
      try{if(current())await commandDialog(task,{...execution,events:items},'append_feedback',button.dataset.reply?{reply_to_event_id:button.dataset.reply}:{corrects_event_id:button.dataset.correct});}catch(error){if(current())root.querySelector('.form-error').textContent=error.message;}
    });
  };bindReplies();
  root.querySelector('#execution-return').onclick=()=>{if(current())showExecutionTask(task);};
  root.querySelector('#execution-more').onclick=async()=>{
    if(!current()||!cursor||busy)return;busy=true;const button=root.querySelector('#execution-more');button.disabled=true;
    try{const data=await api(path(task.id)+'/history?kind='+kind+'&cursor='+encodeURIComponent(cursor));if(!current())return;items.push(...data.items);cursor=data.next_cursor;root.querySelector('#execution-history').innerHTML=render();bindReplies();button.hidden=!cursor;}
    catch(error){if(current())root.querySelector('.form-error').textContent=error.message;}
    finally{busy=false;if(current())button.disabled=false;}
  };
}
function checklistEditor(execution){
  const items=(execution.checklist||[]).filter(i=>!i.deleted).map(def);
  const html=`<p class="help">执行人独立维护清单；删除、重排或修改完成标准都会留存版本。修改完成标准后须重新复核，不能继承原完成比例。</p><div id="checklist-rows"></div><button type="button" id="checklist-add">新增推进项</button>${reasons()}`;
  let root;
  const rowHTML=item=>`<section data-item="${esc(item.item_id)}"><label>推进项<input data-title maxlength="500" value="${esc(item.title)}" required></label><label>完成标准<textarea data-criteria maxlength="2000" required>${esc(item.acceptance_criteria)}</textarea></label><button type="button" data-up>上移</button><button type="button" data-down>下移</button><button type="button" data-remove>移除此项</button></section>`;
  const read=()=>[...root.querySelectorAll('[data-item]')].map(row=>{const before=items.find(i=>i.item_id===row.dataset.item);const title=row.querySelector('[data-title]').value.trim(),acceptance_criteria=row.querySelector('[data-criteria]').value.trim();if(!title||!acceptance_criteria)throw Error('每个推进项须填写标题和完成标准。');return {item_id:row.dataset.item,definition_version:before?.definition_version||1,title,acceptance_criteria};});
  const bind=()=>root.querySelectorAll('[data-item]').forEach(row=>{
    row.querySelector('[data-remove]').onclick=()=>row.remove();
    row.querySelector('[data-up]').onclick=()=>{if(row.previousElementSibling)row.previousElementSibling.before(row);};
    row.querySelector('[data-down]').onclick=()=>{if(row.nextElementSibling)row.nextElementSibling.after(row);};
  });
  return {html,read,afterRender(value){root=value;root.querySelector('#checklist-rows').innerHTML=items.map(rowHTML).join('');bind();root.querySelector('#checklist-add').onclick=()=>{if(root.querySelectorAll('[data-item]').length>=100)return;root.querySelector('#checklist-rows').insertAdjacentHTML('beforeend',rowHTML({item_id:crypto.randomUUID(),title:'',acceptance_criteria:''}));bind();};}};
}
async function commandDialog(task,execution,action,reference={}){
  if(execution.capabilities?.[action]!==true)throw Error('当前任务不能执行此操作，请刷新后查看。');
  let html='',payload=()=>({}),afterRender=null;
  const items=(execution.checklist||[]).filter(i=>!i.deleted),note=()=>text('note','成果 / 处理说明');
  if(action==='save_checklist'){
    const editor=checklistEditor(execution);html=editor.html;afterRender=editor.afterRender;
    payload=form=>({reason:String(form.get('reason')||'').trim(),checklist:editor.read().map(item=>{const old=items.find(i=>i.item_id===item.item_id);return {...item,definition_version:old&&(old.title!==item.title||old.acceptance_criteria!==item.acceptance_criteria)?old.definition_version+1:item.definition_version};})});
  }else if(action==='set_item_progress'){
    html=field('推进项','item_id','',{select:options(items.map(i=>[i.item_id,i.title]),'', '请选择')})+field('进度','state','',{select:options(Object.entries(states),'in_progress',null)})+note();
    payload=form=>{const item=items.find(i=>i.item_id===form.get('item_id'));if(!item)throw Error('请选择有效推进项');return {item_id:item.item_id,definition_version:item.definition_version,state:form.get('state'),note:form.get('note')};};
  }else if(action==='append_feedback'){
    const feedback=(execution.events||[]).filter(e=>e.kind==='feedback'||e.kind==='feedback_recorded'||e.kind==='append_feedback');
    html=text('text','反馈内容')+field('反馈类型','feedback_kind','',{select:options([['comment','讨论反馈'],['progress','执行进展']],'comment',null)})+field('关联推进项','item_id','',{select:options(items.map(i=>[i.item_id,i.title]),'','不关联')})+text('next_step','下一步','',false)+text('blocker','阻碍','',false)+text('evidence_urls','成果资料链接（每行一个 HTTP/HTTPS 链接）','',false)+field('回复已有反馈','reply_to_event_id','',{select:options(feedback.map(e=>[e.id,(e.body?.command?.text||e.body?.text||e.kind).slice(0,100)]),reference.reply_to_event_id||'','不回复')})+field('更正本人反馈','corrects_event_id','',{select:options(feedback.filter(e=>e.actor_user_id===state.actor?.user_id).map(e=>[e.id,(e.body?.command?.text||e.body?.text||e.kind).slice(0,100)]),reference.corrects_event_id||'','不更正')});
    payload=form=>({text:form.get('text'),...(execution.protocol_version===2?{feedback_kind:form.get('feedback_kind'),next_step:form.get('next_step'),blocker:form.get('blocker'),evidence_urls:String(form.get('evidence_urls')||'').split('\n').map(x=>x.trim()).filter(Boolean),...(form.get('item_id')?{item_id:form.get('item_id')}:{})}:{}),reply_to_event_id:form.get('reply_to_event_id')||null,corrects_event_id:form.get('corrects_event_id')||null});
  }else if(action==='propose_requirements'){
    const req=execution.requirements||{};
    html='<p class="help">整体要求与推进清单独立维护。自办任务本人明确保存后生效；协作任务由当前责任人按规则确认。</p>'+field('标题','title',req.title||task.title,{required:true})+text('description','任务描述',req.description||task.description)+text('deliverable','整体交付物',req.deliverable||'')+text('acceptance_criteria','整体验收标准',req.acceptance_criteria||'')+field('截止时间（北京时间）','due_at',deadlineInput(req.due_at||task.due_at),{type:'datetime-local',required:true})+field('优先级','priority_code',req.priority_code||'normal',{select:options([['normal','普通'],['medium','中'],['high','高'],['urgent','紧急']],req.priority_code||'normal',null)})+reasons();
    payload=form=>({reason:form.get('reason'),requirements:{title:form.get('title'),description:form.get('description'),deliverable:form.get('deliverable'),acceptance_criteria:form.get('acceptance_criteria'),due_at:deadlineValue(form.get('due_at')),priority_code:form.get('priority_code'),...(execution.protocol_version===1?{checklist:items.map(def)}:{})}});
  }else if(action.startsWith('initiate_')){
    const purpose=action.includes('executor')?'executor':'reviewer',intent=dialogIntent();
    let people=[],offset=0;
    do{const page=await api(path(task.id)+'/candidates?purpose='+purpose+'&limit=100&offset='+offset);if(!intent())return;people.push(...(page.items||[]));const next=offset+(page.items||[]).length;if(next>=page.total)break;if(next<=offset)throw Error('候选人列表已变化，请重试');offset=next;}while(true);
    html=field('接任人','target_user_id','',{select:options(people.map(p=>[p.user_id||p.id,`${p.name||p.display_name||p.account_code} · ${p.account_code||''}`]),'','请选择有资格的同事')})+reasons();
    payload=form=>({target_user_id:form.get('target_user_id'),reason:form.get('reason')});
  }else if(['respond_executor_handover','respond_reviewer_handover','respond_requirements'].includes(action)){
    html=field('处理结果','decision','',{select:options([['accept','接受'],['reject','拒绝']],'accept',null)})+reasons(false);
    payload=form=>({...action==='respond_requirements'?{change_id:execution.pending_requirements.id}:{handover_id:execution[action.includes('executor')?'execution_handover':'reviewer_handover'].id},decision:form.get('decision'),reason:form.get('reason')});
  }else if(action==='review_completion'){
    html=field('验收结果','decision','',{select:options([['approve','通过'],['reject','驳回']],'approve',null)})+note()+`<fieldset><legend>返工项（驳回时选择）</legend>${items.map(i=>`<label><input type="checkbox" name="rework_item_ids" value="${esc(i.item_id)}">${esc(i.title)}</label>`).join('')}</fieldset>`+(execution.protocol_version===2?text('missing_deliverables','遗漏的交付物（可不属于现有清单）','',false):'');
    payload=form=>({submission_id:execution.current_submission,decision:form.get('decision'),note:form.get('note'),rework_item_ids:form.getAll('rework_item_ids'),...(execution.protocol_version===2?{missing_deliverables:form.get('missing_deliverables')||null}:{})});
  }else if(['submit_completion','complete_self'].includes(action)){
    html=note()+(execution.missing_deliverables?text('missing_deliverables_resolution','本次如何补齐遗漏交付物'):'');
    payload=form=>({note:form.get('note'),...(action==='complete_self'?{submission_id:execution.current_submission}:{}),...(execution.missing_deliverables&&action==='submit_completion'?{missing_deliverables_resolution:form.get('missing_deliverables_resolution')}:{})});
  }else{
    html=(action==='initialize_execution'?'<p class="notice">仅接入新版执行机制；原状态、完成成果和历史保持，不会代替本人接受或伪造验收。</p>':'')+reasons(action!=='accept_task');
    if(action==='initialize_execution'&&execution.status==='deferred')html+=field('明确恢复阶段','resume_status','',{select:options((execution.initialize_resume_options||[]).map(value=>[value,taskStates[value]]),'','请选择已核实的阶段')});
    if(['pause','recheck_pause'].includes(action))html+=field('下次复查（北京时间）','recheck_at','',{type:'datetime-local',required:true});
    payload=form=>({reason:form.get('reason'),...(action==='initialize_execution'&&execution.status==='deferred'?{resume_status:form.get('resume_status')}:{}),...(['resume','recheck_pause'].includes(action)?{pause_id:execution.pause.id}:{}),...(['pause','recheck_pause'].includes(action)?{recheck_at:deadlineValue(form.get('recheck_at'))}:{}),...(action==='withdraw_requirements'?{change_id:execution.pending_requirements.id}:{}),...(action==='withdraw_completion'?{submission_id:execution.current_submission}:{})});
  }
  const root=dialog(labels[action],html,{wide:true,submit:'确认'+(action==='submit_completion'&&execution.self_managed?'自办完成':labels[action]),afterRender,onSubmit:async(form,key)=>{
    if(!current())throw Error('页面或账号已变化，请重新打开任务。');
    const versions=action==='initialize_execution'?execution.initialize_versions:executionVersions(execution);
    await api(path(task.id)+'/'+action,{method:'POST',key,body:{...versions,...payload(form)}});
    if(current())await showExecutionTask(task);
  }});
  const current=dialogIntent({liveForm:true});return root;
}
