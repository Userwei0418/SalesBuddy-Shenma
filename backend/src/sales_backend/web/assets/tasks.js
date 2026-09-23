import {state,$,api,esc,date,options,field,dialog,table,head,query,filters,bindCommon} from './core.js';
const labels={pending_confirm:'待领取 / 接受',pending_review:'待发起人确认',pending_execution:'待执行',in_progress:'执行中',deferred:'延期',completed:'已完成',cancelled:'已取消'};
const positions={self:'自己',supervisor:'主管',manager:'总经理',operations:'运营',fde:'FDE',fde_lead:'FDE主管'};

async function showTask(id) {
  const task=await api('/api/v1/tasks/'+encodeURIComponent(id));
  const me=state.actor.user_id, owner=(task.assignees||[]).find(a=>a.responsibility==='owner');
  const respond=task.status==='pending_confirm' && task.requires_action;
  const complete=['pending_execution','in_progress'].includes(task.status) && owner?.user_id===me && !task.handover_required;
  const review=task.status==='pending_review' && task.creator_user_ref_id===me;
  const coordinate=task.can_coordinate && !['completed','cancelled','pending_review'].includes(task.status);
  const events=(task.events||[]).map(e=>`<li><strong>${esc(e.actor_name||'系统')}</strong> · ${esc(e.note||e.event_type)}<span class="muted"> ${date(e.occurred_at)}</span></li>`).join('');
  return dialog('待办详情',`<h3>${esc(task.title)}</h3><p>${esc(task.description)}</p><div class="notice">${esc(labels[task.status])} · ${esc(task.target_position?positions[task.target_position]+'岗位':'指定同事')} · 负责人：${esc(owner?.name||'等待领取')}<br>截止 ${date(task.due_at)}</div>${task.handover_required?'<p class="notice">接收人已失去原账号或岗位资格，任务待交接；原负责人不能继续执行。</p>':''}<p class="help">岗位任务由一人领取完成。拒绝仅代表本人不领取，所有候选人都拒绝才取消。</p>${respond?field('处理方式','event_type','',{select:options([['accept',task.target_position?'领取任务':'接受任务'],['reject','不领取 / 拒绝']],'accept',null)}):''}${task.status==='pending_review'?`<h4>完成说明</h4><p>${esc(task.completion_note||'')}</p>`:''}${review?field('验收结果','event_type','',{select:options([['approve_completion','确认完成'],['reject_completion','驳回继续执行']],'approve_completion',null)}):''}${respond||complete||review?field(complete?'完成说明（必填）':review?'验收意见（驳回必填）':'补充说明（拒绝必填）','note','',{type:'textarea',required:complete}):''}<h4>处理记录</h4><ul>${events||'<li>暂无记录</li>'}</ul>`,{
    footer:coordinate?'<button type="button" id="coordinate-task">转交 / 取消</button>':'',
    afterRender:root=>{const button=root.querySelector('#coordinate-task');if(button)button.onclick=()=>coordinateTask(task);},
    submit:complete?'提交完成':'确认处理',onSubmit:respond||complete||review?async(form,key)=>{
      const event=complete?'complete':form.get('event_type'),note=String(form.get('note')||'').trim();
      if(['reject','reject_completion','complete'].includes(event)&&!note)throw Error('请填写完成说明或拒绝原因');
      await api('/api/v1/tasks/'+encodeURIComponent(id)+'/events',{method:'POST',key,body:{event_type:event,note,version_no:task.version_no}});
    }:null,
  });
}

async function coordinateTask(task) {
  const people={items:[]};
  let offset=0;
  while(true){
    const page=await api('/api/v1/tasks/recipients?page_size=100&offset='+offset);
    people.items.push(...page.items);
    if(!page.has_more)break;
    if(!Number.isInteger(page.next_offset)||page.next_offset<=offset)throw Error('人员目录已变化，请重试');
    offset=page.next_offset;
  }
  return dialog('任务交接', '<p class="notice">转交后由新接收人重新接受。取消保留任务和全部处理记录。</p>'+field('处理方式','event_type','',{select:options([['reassign','转交'],['cancel','取消']],'reassign',null)})+field('新接收人','account','',{select:options((people.items||[]).map(p=>[p.account_code,p.name+' · '+p.team]),'', '请选择；取消无需选择')})+field('原因','note','',{type:'textarea',required:true}), {
    submit:'确认处理',onSubmit:async(form,key)=>{
      const event_type=form.get('event_type'),note=String(form.get('note')||'').trim();
      const body={event_type,note,version_no:task.version_no};
      if(event_type==='reassign'){body.assignee_account_code=form.get('account');if(!body.assignee_account_code)throw Error('请选择新接收人');}
      await api('/api/v1/tasks/'+task.id+'/events',{method:'POST',key,body});
    },
  });
}

export async function taskInbox() {
  const result=await api('/api/v1/tasks'+query({inbox:state.filters.scope!=='coordination',page_size:50,offset:state.offset,status:state.filters.status||undefined}));
  return {html:head('我的待办','查看发送给你的任务和岗位待办，领取后负责完成，过程与销售端同步。')+`<section class="card">${filters(`<select name="scope" aria-label="任务范围">${options([['inbox','我的待办'],['coordination','任务协调']],state.filters.scope||'inbox',null)}</select><select name="status" aria-label="任务状态">${options(Object.entries(labels),state.filters.status||'')}</select>`)}${table(['任务','接收方式','负责人','截止时间','状态','操作'],result.items,t=>`<tr><td><strong>${esc(t.title)}</strong><div class="muted">${esc(t.creator_name)} 发起</div></td><td>${esc(positions[t.target_position]||'指定同事')}</td><td>${esc(t.owner_name||'等待领取')}</td><td>${date(t.due_at)}</td><td>${esc(t.handover_required?'待交接':labels[t.status])}</td><td><button class="link" data-action="detail" data-id="${esc(t.id)}">${t.status==='pending_confirm'&&t.requires_action?'领取 / 处理':'查看'}</button></td></tr>`)}<div class="pager"><button id="task-prev" ${state.offset?'':'disabled'}>上一页</button><span>第 ${Math.floor(state.offset/50)+1} 页</span><button id="task-next" ${result.has_more?'':'disabled'}>下一页</button></div></section>`,bind:root=>{
    bindCommon(root,{detail:showTask});
    $('#task-prev',root).onclick=()=>{state.offset=Math.max(0,state.offset-50);state.refresh();};
    $('#task-next',root).onclick=()=>{state.offset=result.next_offset;state.refresh();};
  }};
}
