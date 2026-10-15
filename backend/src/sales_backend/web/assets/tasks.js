import {taskManagement} from './task-management.js';
import {newTaskImport,taskImportBatches} from './task-imports.js';
import {state,$,api,esc,date,options,field,dialog,dialogIntent,hasPermission,requirePermission} from './core.js';
const labels={pending_confirm:'待领取 / 接受',pending_review:'待发起人确认',pending_execution:'待执行',in_progress:'执行中',deferred:'延期',completed:'已完成',cancelled:'已取消'};
const positions={self:'自己',supervisor:'主管',manager:'总经理',operations:'运营',fde:'FDE',fde_lead:'FDE主管'};

const eventPermissions={accept:'task.accept',reject:'task.decline',complete:'task.complete',approve_completion:'task.review',reject_completion:'task.review',reassign:'task.coordinate',cancel:'task.cancel'};
export function taskActions(task) {
  const allowed=permission=>hasPermission(permission) && task.action_permissions?.[permission]===true;
  const owner=(task.assignees||[]).find(a=>a.responsibility==='owner');
  const respond=task.status==='pending_confirm' && task.requires_action;
  const coordinate=task.can_coordinate && !['completed','cancelled','pending_review'].includes(task.status);
  return {
    accept:!!respond && allowed('task.accept'), reject:!!respond && allowed('task.decline'),
    complete:['pending_execution','in_progress'].includes(task.status) && owner?.user_id===state.actor.user_id && !task.handover_required && allowed('task.complete'),
    approve_completion:task.status==='pending_review' && task.creator_user_ref_id===state.actor.user_id && allowed('task.review'),
    reject_completion:task.status==='pending_review' && task.creator_user_ref_id===state.actor.user_id && allowed('task.review'),
    reassign:!!coordinate && allowed('task.coordinate'), cancel:!!coordinate && allowed('task.cancel'),
  };
}
function requireTaskAction(task,event) {
  requirePermission(eventPermissions[event]);
  if(!taskActions(task)[event])throw Error('当前账号不能执行此任务操作，请刷新任务后重试。');
}
export async function showTask(id) {
  const current=dialogIntent();
  let task;
  try { task=await api('/api/v1/tasks/'+encodeURIComponent(id)); }
  catch(error) { if(current())throw error;return; }
  if(!current())return;
  const owner=(task.assignees||[]).find(a=>a.responsibility==='owner'), actions=taskActions(task);
  const responseChoices=[['accept',task.target_position?'领取任务':'接受任务'],['reject','不领取 / 拒绝']].filter(([event])=>actions[event]);
  const respond=responseChoices.length>0,complete=actions.complete,review=actions.approve_completion;
  const coordinate=actions.reassign || actions.cancel;
  const events=(task.events||[]).map(e=>`<li><strong>${esc(e.payload?.actor_name||e.actor_name||'系统')}</strong> · ${esc(e.note||e.event_type)}<span class="muted"> ${date(e.occurred_at)}</span></li>`).join('');
  return dialog('待办详情',`<h3>${esc(task.title)}</h3><p>${esc(task.description)}</p><div class="notice">${esc(labels[task.status])} · ${esc(task.target_position?positions[task.target_position]+'岗位':'指定同事')} · 负责人：${esc(owner?.name||'等待领取')}<br>截止 ${date(task.due_at)}</div>${task.handover_required?'<p class="notice">接收人已失去原账号或岗位资格，任务待交接；原负责人不能继续执行。</p>':''}<p class="help">岗位任务由一人领取完成。拒绝仅代表本人不领取，所有候选人都拒绝才取消。</p>${respond?field('处理方式','event_type','',{select:options(responseChoices,responseChoices[0][0],null)}):''}${task.status==='pending_review'?`<h4>完成说明</h4><p>${esc(task.completion_note||'')}</p>`:''}${review?field('验收结果','event_type','',{select:options([['approve_completion','确认完成'],['reject_completion','驳回继续执行']],'approve_completion',null)}):''}${respond||complete||review?field(complete?'完成说明（必填）':review?'验收意见（驳回必填）':'补充说明（拒绝必填）','note','',{type:'textarea',required:complete}):''}<h4>处理记录</h4><ul>${events||'<li>暂无记录</li>'}</ul>`,{
    footer:coordinate?`<button type="button" id="coordinate-task">${actions.reassign?(actions.cancel?'转交 / 取消':'转交任务'):'取消任务'}</button>`:'',
    afterRender:root=>{const button=root.querySelector('#coordinate-task');if(button)button.onclick=()=>coordinateTask(task);},
    submit:complete?'提交完成':'确认处理',onSubmit:respond||complete||review?async(form,key)=>{
      const event=complete?'complete':form.get('event_type'),note=String(form.get('note')||'').trim();
      requireTaskAction(task,event);
      if(['reject','reject_completion','complete'].includes(event)&&!note)throw Error('请填写完成说明或拒绝原因');
      await api('/api/v1/tasks/'+encodeURIComponent(id)+'/events',{method:'POST',key,body:{event_type:event,note,version_no:task.version_no}});
    }:null,
  });
}

export async function coordinateTask(task) {
  const current=dialogIntent();
  const actions=taskActions(task), choices=[['reassign','转交'],['cancel','取消']].filter(([event])=>actions[event]);
  if(!choices.length)throw Error('当前账号不能转交或取消此任务。');
  const people={items:[]};
  let offset=0;
  while(actions.reassign){
    let page;
    try { page=await api('/api/v1/tasks/'+encodeURIComponent(task.id)+'/reassignment-options?page_size=100&offset='+offset); }
    catch(error) { if(current())throw error;return; }
    if(!current())return;
    people.items.push(...page.items);
    if(!page.has_more)break;
    if(!Number.isInteger(page.next_offset)||page.next_offset<=offset)throw Error('人员目录已变化，请重试');
    offset=page.next_offset;
  }
  return dialog('任务交接', '<p class="notice">转交后由新接收人重新接受。取消保留任务和全部处理记录。</p>'+field('处理方式','event_type','',{select:options(choices,choices[0][0],null)})+(actions.reassign?field('新接收人','account','',{select:options((people.items||[]).map(p=>[p.account_code,p.name+' · '+p.team]),'', '请选择；取消无需选择')}):'')+field('原因','note','',{type:'textarea',required:true}), {
    submit:'确认处理',onSubmit:async(form,key)=>{
      const event_type=form.get('event_type'),note=String(form.get('note')||'').trim();
      requireTaskAction(task,event_type);
      if(!note)throw Error('请填写转交或取消原因');
      const body={event_type,note,version_no:task.version_no};
      if(event_type==='reassign'){body.assignee_account_code=form.get('account');if(!body.assignee_account_code)throw Error('请选择新接收人');}
      await api('/api/v1/tasks/'+task.id+'/events',{method:'POST',key,body});
    },
  });
}

export async function taskInbox(context = {}) {
  if (state.filters.view === 'imports') return taskImportBatches();
  return taskManagement({showTask, newTaskImport}, context);
}
