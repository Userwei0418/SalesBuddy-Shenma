import {state, $, api, esc, date, num, badge, options, people, field, dialog, table, pager, head, query, filters, search, bindCommon} from './core.js';

const kinds = {collection:'回款',recognized:'确收',acv:'协助项目金额 / ACV',opportunity_count:'商机数量',visit_count:'拜访记录数',demo_count:'Demo 场景数'};
const periods = {quarter:'季度',year:'年度',month:'月',week:'周'};
const scopes = {person:'个人',team:'团队',department:'部门'};
const departments = {sales:'销售部门',fde:'FDE 部门'};
const today = () => new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const range = row => `${row.period_start} 至 ${row.period_end}`;
const select = (name, opts, label) => `<select name="${esc(name)}" aria-label="${esc(label)}">${opts}</select>`;
const objectName = row => row.user_name || row.team_name || departments[row.department_code || 'sales'];
const ownQuery = data => '?' + new URLSearchParams(Object.entries(data).filter(([,v])=>v!==null&&v!==undefined&&v!==''));
const actorKey = () => JSON.stringify(state.actor);
const targetValue = (kind,value) => value===null || value===undefined ? '未设置' : `${num(value)} ${kind.endsWith('_count')?'个':'元'}`;

function editTarget(row = {}) {
  const editing=!!row.id, identity=actorKey();
  let loaded=null, ready=false, serial=0;
  const choose = (root,name) => $(`[name=${name}]`,root).value;
  const subject = root => {
    const scope=editing?row.scope_type:choose(root,'scope');
    return {scope, user_id:scope==='person'?(editing?row.user_ref_id:choose(root,'user_id')):null,
      team_id:scope==='team'?(editing?row.team_id:choose(root,'team_id')):null,
      department_code:scope==='department'?(editing?row.department_code||'sales':choose(root,'department_code')):'sales',
      period_type:editing?row.period_type:choose(root,'period_type'),
      anchor_date:editing?row.period_start:choose(root,'anchor_date')};
  };
  return dialog(editing?'调整生效目标':'配置目标',
    '<p class="help">记录与负责人商定的目标。部门、团队和个人分别设置；不同周期独立维护。</p>'+
    '<div class="form-grid">'+
    field('目标范围','scope','',{required:true,disabled:editing,select:options(Object.entries(scopes),row.scope_type||'person',null)})+
    `<div id="target-person">${field('目标人员','user_id','',{disabled:editing,select:people(row.user_ref_id||'','请选择人员')})}</div>`+
    `<div id="target-team">${field('目标团队','team_id','',{disabled:editing,select:options((state.org?.departments||state.org?.teams||[]).filter(x=>x.status==='active').map(x=>[x.id,x.name]),row.team_id||'','请选择团队')})}</div>`+
    `<div id="target-department">${field('目标部门','department_code','',{disabled:editing,select:options(Object.entries(departments),row.department_code||'sales',null)})}</div>`+
    field('目标周期','period_type','',{required:true,disabled:editing,select:options(Object.entries(periods),row.period_type||state.filters.period_type||'quarter',null)})+
    field('周期内日期','anchor_date',row.period_start||state.filters.anchor_date||today(),{type:'date',required:true,disabled:editing,help:'按日期自动确定完整自然季度、年度、月或周。'})+
    field('目标指标','mode','',{full:true,select:options([['financial','回款与确收'],...Object.entries(kinds).filter(([k])=>!['collection','recognized'].includes(k))],!row.kind||['collection','recognized'].includes(row.kind)?'financial':row.kind,null)})+
    '<div id="target-period-note" class="notice full" role="status">请选择目标对象。</div>'+
    '<div id="target-values" class="full form-grid"></div>'+
    field('设置／调整原因','reason','',{type:'textarea',required:true,full:true,placeholder:'说明已商定的目标依据，或本次调整的原因',help:'保存后立即生效。若修改涉及待审批目标，对应整张申请会关闭并保留记录。'})+
    '</div>', {wide:true,submit:'保存并生效',onSubmit:async(form,key,root)=>{
      if(identity!==actorKey())throw Error('登录身份已变化，请重新打开表单');
      const selected=subject(root);
      if(!ready||!loaded||loaded.key!==JSON.stringify(selected))throw Error('请等待目标加载完成后再保存');
      const mode=form.get('mode'), list=mode==='financial'?['collection','recognized']:[mode];
      const items=list.map(kind=>({kind,amount:form.get('amount_'+kind),version_no:loaded.rows.find(r=>r.kind===kind)?.version_no??null}));
      return api('/targets/batch',{method:'POST',key,body:{...selected,reason:String(form.get('reason')||'').trim(),items}});
    },afterRender:root=>{
      const formNode=$('form',root);
      const isCurrent=()=>root.open&&formNode.isConnected&&identity===actorKey();
      $('[name=reason]',root).maxLength=2000;
      const values=()=>{
        const mode=choose(root,'mode'), list=mode==='financial'?['collection','recognized']:[mode];
        $('#target-values',root).innerHTML=list.map(kind=>{
          const value=loaded?.rows.find(r=>r.kind===kind), count=kind.endsWith('_count');
          return field(kinds[kind]+(count?'（个）':'（元）'),'amount_'+kind,value?.amount_text??value?.amount??'',
            {required:true,type:'number',min:count?'1':'0.01',step:count?'1':'0.01',help:'当前生效：'+targetValue(kind,value?.amount)});
        }).join('');
      };
      const sync=async()=>{
        const current=++serial, selected=subject(root);ready=false;loaded=null;
        $('#target-person',root).hidden=selected.scope!=='person';$('#target-team',root).hidden=selected.scope!=='team';$('#target-department',root).hidden=selected.scope!=='department';
        $('[name=user_id]',root).required=selected.scope==='person'&&!editing;$('[name=team_id]',root).required=selected.scope==='team'&&!editing;
        const note=$('#target-period-note',root), button=$('button[type=submit]',root);button.disabled=true;values();
        if((selected.scope==='person'&&!selected.user_id)||(selected.scope==='team'&&!selected.team_id)||!selected.anchor_date){note.textContent='请选择目标对象和周期。';return;}
        note.textContent='正在读取已生效目标…';
        try {
          const result=await api('/targets'+ownQuery({...selected,limit:100}));
          if(current!==serial||!isCurrent())return;
          loaded={key:JSON.stringify(selected),rows:result.items||[]};ready=true;values();
          note.textContent=`${result.period.start} 至 ${result.period.end} · ${loaded.rows.length?'已加载生效目标':'尚未设置目标'}`+
            ((result.pending_batch_total||result.pending_total)?'。存在待审批申请，保存改动时请核对影响。':'');
        }catch(error){if(current===serial&&isCurrent())note.textContent=error.message||'目标加载失败，请重新选择或重试';}
        finally{if(current===serial&&isCurrent())button.disabled=!ready;}
      };
      ['scope','user_id','team_id','department_code','period_type','anchor_date'].forEach(name=>$(`[name=${name}]`,root).onchange=sync);
      $('[name=mode]',root).onchange=values;sync();
    }});
}

export async function targets() {
  if(!Object.keys(state.filters).length)state.filters={period_type:'quarter',anchor_date:today()};
  const data=await api('/targets'+query({limit:50,offset:state.offset}));
  return {html:head('目标管理','部门、团队、个人目标独立维护；本人首次提交生效，后续变更由运营审批。','<button class="primary" data-action="create">配置目标</button>')+
    `<div class="notice">${esc(data.period.start)} 至 ${esc(data.period.end)} · 完成率使用已生效目标，待审批值不参与计算。</div>`+
    `<section class="card">${filters(search('搜索姓名 / 账号 / 团队')+select('period_type',options(Object.entries(periods),state.filters.period_type||'quarter',null),'周期')+
      `<input name="anchor_date" type="date" aria-label="周期内日期" value="${esc(state.filters.anchor_date||today())}">`+
      select('kind',options(Object.entries(kinds),state.filters.kind||''),'目标指标'))}`+
    table(['目标对象','指标','周期','生效目标','设置依据','更新时间','操作'],data.items,r=>`<tr><td><strong>${esc(objectName(r))}</strong><small>${esc(scopes[r.scope_type])} ${esc(r.account_code||'')}</small></td><td>${esc(kinds[r.kind])}</td><td>${esc(periods[r.period_type])}<small>${esc(range(r))}</small></td><td>${esc(targetValue(r.kind,r.amount))}</td><td>${esc(r.change_reason||'历史目标')}</td><td>${date(r.updated_at)}</td><td><button class="link" data-action="edit" data-id="${esc(r.id)}">调整</button><button class="link" data-action="history" data-id="${esc(r.id)}">变更记录</button></td></tr>`)+pager(data.total)+'</section>',
    bind:root=>bindCommon(root,{create:()=>editTarget(),edit:id=>editTarget(data.items.find(r=>r.id===id)),history:async id=>{
      const response=await api(`/targets/${encodeURIComponent(id)}/history`);
      dialog('目标变更记录',table(['时间','操作人','变更前','变更后','原因'],response.items,r=>`<tr><td>${date(r.occurred_at)}</td><td>${esc(r.actor_name||'系统')}</td><td>${num(r.before_snapshot?.amount)}</td><td>${num(r.after_snapshot?.amount)}</td><td>${esc(r.after_snapshot?.change_reason||'历史记录')}</td></tr>`),{wide:true});
    }})};
}

const changes = r => (r.items||[r]).map(item=>`<div><strong>${esc(kinds[item.kind])}</strong><small>${esc(targetValue(item.kind,item.previous_amount))} → ${esc(targetValue(item.kind,item.proposed_amount))}</small></div>`).join('');
export async function targetRequests() {
  if(!Object.keys(state.filters).length)state.filters={status:'pending',format:'batch'};
  const legacy=state.filters.format==='legacy', path=legacy?'/target-requests':'/target-batches';
  const data=await api(path+ownQuery({status:state.filters.status,limit:50,offset:state.offset}));
  return {html:head('目标修改审批','查看调整原因与各项前后对照。整单通过后生效，审核前继续使用原目标。')+
    `<section class="card">${filters(select('status',options([['pending','待审批'],['approved','已通过'],['rejected','已驳回'],['cancelled','已关闭']],state.filters.status),'审批状态')+
      select('format',options([['batch','目标表单'],['legacy','历史单项申请']],legacy?'legacy':'batch',null),'申请类型'))}`+
    table(['申请人','周期','目标调整','申请原因','申请时间','状态与意见','操作'],data.items,r=>`<tr><td><strong>${esc(r.applicant_name)}</strong><small>${esc(r.account_code)}</small></td><td>${esc(range(r))}</td><td>${changes(r)}</td><td>${esc(r.reason||'历史申请未记录原因')}</td><td>${date(r.created_at)}</td><td>${badge(r.status)}<small>${esc(r.reviewer_name||'')} ${esc(r.decision_reason||'')}</small></td><td>${r.status==='pending'?`<button class="link" data-action="review" data-id="${esc(r.id)}">审核</button>`:''}</td></tr>`)+pager(data.total)+'</section>',
    bind:root=>bindCommon(root,{review:id=>{
      const r=data.items.find(item=>item.id===id);
      dialog('审核目标变更',`<p><strong>${esc(r.applicant_name)}</strong> · ${esc(range(r))}</p><div class="notice">申请原因：${esc(r.reason||'历史申请未记录原因')}</div>`+
        table(['指标','原目标','申请目标'],r.items||[r],item=>`<tr><td>${esc(kinds[item.kind])}</td><td>${esc(targetValue(item.kind,item.previous_amount))}</td><td>${esc(targetValue(item.kind,item.proposed_amount))}</td></tr>`)+
        '<div class="form-grid">'+field('审核结果','decision','',{select:options([['approved','整单通过并生效'],['rejected','驳回申请']],'approved',null)})+
        field('审核意见','reason','',{type:'textarea',full:true,help:'驳回必须填写原因。生效目标已变化时，系统会阻止过期申请通过。'})+'</div>',
        {wide:true,submit:'确认审核',onSubmit:async(form,key)=>api(`${path}/${encodeURIComponent(id)}/decision`,{method:'POST',key,body:{decision:form.get('decision'),reason:String(form.get('reason')||'').trim()}}),
          afterRender:d=>{$('[name=reason]',d).maxLength=2000;$('[name=decision]',d).onchange=()=>{$('[name=reason]',d).required=$('[name=decision]',d).value==='rejected';};}});
    }})};
}
