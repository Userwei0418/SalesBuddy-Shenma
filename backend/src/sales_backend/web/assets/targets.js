import {state, $, api, readForDialog, dialogIntent, esc, date, badge, options, field, dialog, table, pager, head, query, filters, search, bindCommon, hasPermission, requirePermission} from './core.js';

const kinds = {collection:'回款目标',collection_ppl:'PPL 储备目标',recognized:'确收',acv:'协助项目金额 / ACV',opportunity_count:'商机数量',visit_count:'拜访记录数',demo_count:'Demo 场景数'};
const periods = {quarter:'季度',year:'年度',month:'月',week:'周'};
const scopes = {person:'个人',team:'团队',department:'部门'};
const departments = {sales:'销售部门',fde:'FDE 部门'};
const today = () => new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const range = row => `${row.period_start} 至 ${row.period_end}`;
const select = (name, opts, label) => `<select name="${esc(name)}" aria-label="${esc(label)}">${opts}</select>`;
const objectName = row => row.user_name || row.team_name || departments[row.department_code || 'sales'];
const ownQuery = data => '?' + new URLSearchParams(Object.entries(data).filter(([,v])=>v!==null&&v!==undefined&&v!==''));
const actorKey = () => JSON.stringify([state.actor,state.company?.id]);
// The six legacy kinds have a fixed contract; custom codes never imply a unit.
const legacyUnits = {collection:['元',2],collection_ppl:['元',2],recognized:['元',2],acv:['元',2],opportunity_count:['个',0],visit_count:['次',0],demo_count:['次',0]};
const legacyKind = kind => Object.hasOwn(kinds,kind);
function metric(kind,directory) {
  const row=directory?.items.get(kind), fallback=legacyKind(kind)?legacyUnits[kind]:null;
  const name=String(row?.name||'').trim()||(fallback?kinds[kind]:`未知指标（${kind||'未记录代码'}）`);
  const unit=row?row.unit:fallback?.[0], precision=row?row.decimal_places:fallback?.[1];
  const valid=(!row||(typeof row.name==='string'&&!!row.name.trim()))&&typeof unit==='string'&&!!unit.trim()&&[0,2].includes(precision);
  return {name:name+(row?.active===false?'（已停用）':!valid?'（指标信息缺失）':''),unit:valid?unit.trim():null,precision,valid};
}
async function metricDirectory() {
  try {
    // core.api retains the same Bearer session and selected-company header.
    const result=await api('/api/v1/review/metrics');
    if(!Array.isArray(result.items))throw Error('指标字典返回不完整');
    const items=new Map();
    for(const row of result.items){
      if(typeof row?.code!=='string'||items.has(row.code))throw Error('指标字典返回不完整');
      items.set(row.code,row);
    }
    return {items,notice:''};
  }catch(error){
    // Session/company changes must never turn into a cached-data fallback.
    if(error.code)throw error;
    return {items:new Map(),notice:'指标字典读取失败：'+(error.message||'请稍后重试')+'。旧指标按既有口径显示；其他指标暂不能通过审批，仍可填写原因驳回。'};
  }
}
function targetNumber(value,precision) {
  if(value===null||value===undefined)return {text:'未设置',valid:true};
  const raw=String(value), match=/^(\d+)(?:\.(\d+))?$/.exec(raw);
  if(!match||![0,2].includes(precision)||(typeof value==='number'&&Math.abs(value)*10**precision>Number.MAX_SAFE_INTEGER))
    return {text:raw+'（数值精度待核对）',valid:false};
  const whole=match[1].replace(/^0+(?=\d)/,''), fraction=match[2]||'';
  if(/[1-9]/.test(fraction.slice(precision)))return {text:raw+'（数值精度待核对）',valid:false};
  return {text:whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(precision?'.'+fraction.slice(0,precision).padEnd(precision,'0'):''),valid:true};
}
const targetValue = (kind,value,directory) => {
  if(value===null||value===undefined)return '未设置';
  const info=metric(kind,directory);
  return info.valid?`${targetNumber(value,info.precision).text} ${info.unit}`:`${String(value)}（单位／精度待核对）`;
};
const amountValue = (item,key) => Object.hasOwn(item,key+'_text')?item[key+'_text']:item[key];
const canApprove = (row,directory) => (row.items||[row]).length>0&&(row.items||[row]).every(item=>{
  const info=metric(item.kind,directory);
  return info.valid&&amountValue(item,'proposed_amount')!=null&&targetNumber(amountValue(item,'proposed_amount'),info.precision).valid&&targetNumber(amountValue(item,'previous_amount'),info.precision).valid;
});
const dictionaryNotice = directory => directory.notice?`<div class="notice" role="status">${esc(directory.notice)}</div>`:'';

const validVersion = value => Number.isInteger(value)&&value>0&&value<=2147483647;
async function managementDirectory() {
  const directory=await metricDirectory();
  if(directory.notice)throw Error('指标字典读取失败，请刷新后重试。');
  return directory;
}
function createMetric() {
  requirePermission('target.manage');
  // Keep both the code and dialog idempotency key stable across a retry.
  const code='custom_'+crypto.randomUUID().replaceAll('-','');
  return dialog('新增指标','<p class="help">填写目标的名称、单位和计算口径。创建后口径固定；口径改变时请新增指标，保留历史目标含义。</p><div class="form-grid">'+
    field('指标名称','name','',{required:true})+field('单位','unit','',{required:true,placeholder:'例如：元、个、次、积分'})+
    field('数值精度','decimal_places','',{select:options([['0','整数'],['2','两位小数']],'0',null)})+
    field('统计口径','definition','',{type:'textarea',required:true,full:true,placeholder:'说明统计对象、范围和计算方式'})+'</div>',
    {submit:'创建指标',onSubmit:async(form,key)=>{
      requirePermission('target.manage');
      const body={code,active:true};
      for(const [name,label,max] of [['name','指标名称',80],['unit','单位',20],['definition','统计口径',2000]]){
        body[name]=String(form.get(name)||'').trim();
        if(!body[name]||body[name].length>max)throw Error(`${label}须填写，且不能超过 ${max} 个字符。`);
      }
      const precision=form.get('decimal_places');
      if(!['0','2'].includes(precision))throw Error('请选择整数或两位小数。');
      body.decimal_places=Number(precision);
      return api('/api/v1/review/metrics',{method:'POST',key,body});
    },afterRender:root=>{
      for(const [name,max] of [['name',80],['unit',20],['definition',2000]])$(`[name=${name}]`,root).maxLength=max;
    }});
}
async function toggleMetric(code) {
  requirePermission('target.manage');
  const directory=await readForDialog(managementDirectory);
  if(!directory)return;
  requirePermission('target.manage');
  const row=directory.items.get(code);
  if(!row||!validVersion(row.version_no)||typeof row.active!=='boolean')throw Error('指标状态不完整，请刷新后重试。');
  const active=!row.active, label=active?'启用':'停用';
  return dialog(label+'指标',`<p>确认${label}「${esc(row.name)}」？</p><p class="help">${active?'启用后可继续配置此指标的目标。':'停用后不能新建或调整此指标的目标；已生效目标及历史记录保留。'}</p>`,
    {submit:'确认'+label,onSubmit:async(_form,key)=>{
      requirePermission('target.manage');
      return api('/api/v1/review/metrics/'+encodeURIComponent(code),{method:'PATCH',key,body:{active,version_no:row.version_no}});
    }});
}
export async function manageMetrics() {
  requirePermission('target.manage');
  const directory=await readForDialog(managementDirectory);
  if(!directory)return;
  requirePermission('target.manage');
  return dialog('指标管理','<p class="help">公司指标统一定义名称、单位和统计口径。停用不删除历史目标。</p><p><button type="button" class="primary" data-action="new-metric">新增指标</button></p>'+
    table(['指标','单位与精度','统计口径','状态','操作'],[...directory.items.values()],row=>`<tr><td>${esc(row.name)}<small>${row.system_defined?'系统指标':'自定义指标'}</small></td><td>${esc(row.unit)} · ${row.decimal_places===0?'整数':row.decimal_places===2?'两位小数':'精度待核对'}</td><td>${esc(row.definition||'未记录')}</td><td>${row.active?'已启用':'已停用'}</td><td><button type="button" class="link" data-action="toggle-metric" data-id="${esc(row.code)}">${row.active?'停用':'启用'}</button></td></tr>`),
    {wide:true,afterRender:root=>bindCommon($('form',root),{'new-metric':createMetric,'toggle-metric':toggleMetric})});
}

export async function editReviewTarget(row=null) {
  requirePermission('target.manage');
  if(row&&(row.can_manage!==true||row.editable!==true))throw Error('此目标不在运营维护的授权范围，或指标已停用。');
  const context=await readForDialog(async()=>{
    const [directory,subjects]=await Promise.all([managementDirectory(),api('/api/v1/review/targets/options')]);
    return {directory,subjects};
  });
  if(!context)return;
  requirePermission('target.manage');
  const {directory,subjects}=context, keys={person:'people',team:'teams',department:'departments'};
  if(!Object.values(keys).every(key=>Array.isArray(subjects[key])))throw Error('目标选项未完整返回，请刷新后重试。');
  const allowed=Object.fromEntries(Object.values(keys).map(key=>[key,subjects[key].filter(item=>item.can_manage===true)]));
  const availableScopes=Object.entries(scopes).filter(([scope])=>allowed[keys[scope]].length);
  const selectable=[...directory.items.values()].filter(item=>!legacyKind(item.code)&&item.active===true&&item.goal_selectable===true&&metric(item.code,directory).valid);
  if(!availableScopes.length)throw Error('当前没有可维护且可查看的目标对象。');
  if(!selectable.length||row&&!selectable.some(item=>item.code===row.kind))throw Error('没有可配置的自定义指标，请先创建或启用指标。');
  const choose=(root,name)=>$(`[name=${name}]`,root).value;
  const subject=root=>{
    const scope=row?.scope_type||choose(root,'scope');
    return {scope,user_id:scope==='person'?(row?.user_ref_id||choose(root,'user_id')):null,
      team_id:scope==='team'?(row?.team_id||choose(root,'team_id')):null,
      department_code:scope==='department'?(row?.department_code||choose(root,'department_code')):'sales',
      period_type:row?.period_type||choose(root,'period_type'),anchor_date:row?.period_start||choose(root,'anchor_date'),
      kind:row?.kind||choose(root,'kind')};
  };
  const authorized=selected=>allowed[keys[selected.scope]||'']?.some(item=>item.id===
    (selected.scope==='person'?selected.user_id:selected.scope==='team'?selected.team_id:selected.department_code));
  let loaded=null, serial=0;
  return dialog(row?'调整自定义目标':'配置自定义目标','<div class="form-grid">'+
    field('目标范围','scope','',{disabled:!!row,select:options(availableScopes,row?.scope_type||availableScopes[0][0],null)})+
    `<div id="review-person">${field('目标人员','user_id','',{disabled:!!row,select:options(allowed.people.map(p=>[p.id,p.name+' · '+p.account_code]),row?.user_ref_id||'','请选择人员')})}</div>`+
    `<div id="review-team">${field('目标团队','team_id','',{disabled:!!row,select:options(allowed.teams.map(t=>[t.id,t.name]),row?.team_id||'','请选择团队')})}</div>`+
    `<div id="review-department">${field('目标部门','department_code','',{disabled:!!row,select:options(allowed.departments.map(d=>[d.id,d.name]),row?.department_code||allowed.departments[0]?.id||'',null)})}</div>`+
    field('目标周期','period_type','',{disabled:!!row,select:options(Object.entries(periods),row?.period_type||state.filters.period_type||'quarter',null)})+
    field('周期内日期','anchor_date',row?.period_start||state.filters.anchor_date||today(),{type:'date',required:true,disabled:!!row})+
    field('目标指标','kind','',{disabled:!!row,select:options(selectable.map(m=>[m.code,m.name+'（'+m.unit+'）']),row?.kind||selectable[0].code,null)})+
    field('目标值','amount','',{required:true,help:'按所选指标的单位与精度填写正数。'})+
    '<div id="review-target-note" class="notice full" role="status">请选择目标对象。</div>'+
    field('设置／调整原因','reason','',{type:'textarea',required:true,full:true,help:'保存后立即生效。若修改涉及待审批目标，对应整张申请会关闭并保留记录。'})+'</div>',
    {wide:true,submit:'保存并生效',onSubmit:async(form,key,root)=>{
      requirePermission('target.manage');
      const selected=subject(root), info=metric(selected.kind,directory);
      if(!loaded||loaded.key!==JSON.stringify(selected)||!authorized(selected))throw Error('请重新选择目标对象，并等待目标加载完成。');
      const amount=String(form.get('amount')||'').trim(), reason=String(form.get('reason')||'').trim();
      // Never round money or counts through a JavaScript Number.
      const match=/^(\d{1,16})(?:\.(\d{1,2}))?$/.exec(amount);
      if(!info.valid||!match||!/[1-9]/.test(amount)||(info.precision===0&&/[1-9]/.test(match[2]||'')))throw Error('目标值须为正数，最多 16 位整数；请遵守指标的小数精度。');
      if(!reason||reason.length>2000)throw Error('请填写设置／调整原因，最多 2000 个字符。');
      return api('/api/v1/review/targets',{method:'POST',key,body:{...selected,amount,reason,version_no:loaded.version}});
    },afterRender:root=>{
      const current=dialogIntent({liveForm:true});
      $('[name=reason]',root).maxLength=2000;
      $('[name=amount]',root).inputMode='decimal';
      const sync=async()=>{
        const request=++serial, selected=subject(root), note=$('#review-target-note',root), button=$('button[type=submit]',root);
        loaded=null;button.disabled=true;$('[name=amount]',root).value='';
        for(const scope of Object.keys(keys))$('#review-'+scope,root).hidden=selected.scope!==scope;
        $('[name=user_id]',root).required=selected.scope==='person'&&!row;$('[name=team_id]',root).required=selected.scope==='team'&&!row;
        if(!authorized(selected)||!selected.anchor_date){note.textContent='请选择有维护权限的目标对象和周期。';return;}
        note.textContent='正在核对目标及待审批申请…';
        try {
          const result=await api('/api/v1/review/targets'+ownQuery({...selected,limit:2}));
          if(request!==serial||!current())return;
          const items=result.items, info=metric(selected.kind,directory);
          if(!Array.isArray(items)||items.length>1||result.total!==items.length||result.editable!==true)throw Error('目标不可维护或返回不完整，请刷新后重试。');
          const saved=items[0];
          if(saved&&(saved.kind!==selected.kind||saved.scope_type!==selected.scope||saved.period_type!==selected.period_type||
            saved.period_start!==result.period.start||(saved.user_ref_id||null)!==selected.user_id||(saved.team_id||null)!==selected.team_id||
            (saved.department_code||'sales')!==selected.department_code||saved.can_manage!==true||saved.editable!==true||
            (saved.id?!validVersion(saved.version_no):saved.version_no!=null)))throw Error('目标范围或版本已变化，请刷新后重试。');
          const amount=saved?amountValue(saved,'amount'):null;
          if(saved?.id&&amount==null||amount!=null&&(typeof amount!=='string'||!targetNumber(amount,info.precision).valid))throw Error('生效目标的数值精度待核对。');
          loaded={key:JSON.stringify(selected),version:saved?.version_no??null};
          $('[name=amount]',root).value=amount??'';
          note.textContent=`${result.period.start} 至 ${result.period.end} · 当前生效：${targetValue(selected.kind,amount,directory)}。`+
            `按${info.unit}填写，${info.precision===0?'只接受整数':'最多两位小数'}。`+
            (saved?.pending_change?` 待审批：${targetValue(selected.kind,saved.pending_change.proposed_amount,directory)}；不计入生效目标。保存改动会关闭对应整张申请。`:'');
        }catch(error){if(request===serial&&current())note.textContent=error.message||'目标读取失败，请重新选择或重试。';}
        finally{if(request===serial&&current())button.disabled=!loaded;}
      };
      ['scope','user_id','team_id','department_code','period_type','anchor_date','kind'].forEach(name=>$(`[name=${name}]`,root).onchange=sync);
      sync();
    }});
}

export async function editTarget(row = {}) {
  requirePermission('target.manage');
  if(row.kind&&!legacyKind(row.kind))return editReviewTarget(row);
  const editing=!!(row.id||row.row_key), identity=actorKey();
  if(editing && row.can_manage!==true)throw Error('此目标不在运营维护的授权范围');
  const page=state.page;
  const directory=editing ? {
    people:row.scope_type==='person' ? [{id:row.user_ref_id,name:row.user_name,account_code:row.account_code}] : [],
    teams:row.scope_type==='team' ? [{id:row.team_id,name:row.team_name}] : [],
    departments:row.scope_type==='department' ? [{id:row.department_code||'sales',name:departments[row.department_code||'sales']}] : [],
  } : await readForDialog('/targets/options');
  if(!directory)return;
  if(identity!==actorKey() || page!==state.page)return;
  if(!['people','teams','departments'].every(k=>Array.isArray(directory[k])))throw Error('目标选项未完整返回，请刷新后重试');
  const availableScopes=Object.entries(scopes).filter(([scope])=>directory[{person:'people',team:'teams',department:'departments'}[scope]].length);
  if(!availableScopes.length)throw Error('当前没有可维护且可查看的目标对象，请联系本公司管理员核对目标授权范围');
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
    field('目标范围','scope','',{required:true,disabled:editing,select:options(availableScopes,row.scope_type||availableScopes[0][0],null)})+
    `<div id="target-person">${field('目标人员','user_id','',{disabled:editing,select:options(directory.people.map(p=>[p.id,p.name+' · '+p.account_code]),row.user_ref_id||'','请选择人员')})}</div>`+
    `<div id="target-team">${field('目标团队','team_id','',{disabled:editing,select:options(directory.teams.map(t=>[t.id,t.name]),row.team_id||'','请选择团队')})}</div>`+
    `<div id="target-department">${field('目标部门','department_code','',{disabled:editing,select:options(directory.departments.map(d=>[d.id,d.name]),row.department_code||directory.departments[0]?.id||'',null)})}</div>`+
    field('目标周期','period_type','',{required:true,disabled:editing,select:options(Object.entries(periods),row.period_type||state.filters.period_type||'quarter',null)})+
    field('周期内日期','anchor_date',row.period_start||state.filters.anchor_date||today(),{type:'date',required:true,disabled:editing,help:'按日期自动确定完整自然季度、年度、月或周。'})+
    field('目标指标','mode','',{full:true,select:options([['financial','回款与确收'],...Object.entries(kinds).filter(([k])=>!['collection','recognized'].includes(k))],!row.kind||['collection','recognized'].includes(row.kind)?'financial':row.kind,null)})+
    '<div id="target-period-note" class="notice full" role="status">请选择目标对象。</div>'+
    '<div id="target-values" class="full form-grid"></div>'+
    field('设置／调整原因','reason','',{type:'textarea',required:true,full:true,placeholder:'说明已商定的目标依据，或本次调整的原因',help:'保存后立即生效。若修改涉及待审批目标，对应整张申请会关闭并保留记录。'})+
    '</div>', {wide:true,submit:'保存并生效',onSubmit:async(form,key,root)=>{
      requirePermission('target.manage');
      if(identity!==actorKey())throw Error('登录身份已变化，请重新打开表单');
      const selected=subject(root);
      if(!ready||!loaded||loaded.key!==JSON.stringify(selected))throw Error('请等待目标加载完成后再保存');
      const mode=form.get('mode'), list=mode==='financial'?['collection','recognized']:[mode];
      if(mode!=='financial'&&!legacyKind(mode))throw Error('请选择此表单支持的目标指标。');
      const items=list.map(kind=>({kind,amount:form.get('amount_'+kind),version_no:loaded.rows.find(r=>r.kind===kind)?.version_no??null}));
      return api('/targets/batch',{method:'POST',key,body:{...selected,reason:String(form.get('reason')||'').trim(),items}});
    },afterRender:root=>{
      const formNode=$('form',root);
      const isCurrent=()=>root.open&&formNode.isConnected&&identity===actorKey();
      $('[name=reason]',root).maxLength=2000;
      const values=()=>{
        const mode=choose(root,'mode'), list=mode==='financial'?['collection','recognized']:[mode];
        $('#target-values',root).innerHTML=list.map(kind=>{
          const value=loaded?.rows.find(r=>r.kind===kind), info=metric(kind), count=info.precision===0;
          const amount=amountValue(value||{},'amount');
          return field(kinds[kind]+`（${info.unit}）`,'amount_'+kind,amount??'',
            {required:true,type:'number',min:count?'1':'0.01',step:count?'1':'0.01',help:'当前生效：'+targetValue(kind,amount)});
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
  const identity=actorKey(), page=state.page;
  if(!Object.keys(state.filters).length)state.filters={period_type:'quarter',anchor_date:today()};
  const data=await api('/api/v1/review/targets'+query({limit:50,offset:state.offset}));
  const directory=await metricDirectory();
  if(identity!==actorKey()||page!==state.page)throw Error('登录身份、公司或页面已变化，请重新加载目标。');
  const listedKinds=new Map(Object.entries(kinds));
  for(const code of directory.items.keys())listedKinds.set(code,metric(code,directory).name);
  for(const item of data.items)if(!listedKinds.has(item.kind))listedKinds.set(item.kind,metric(item.kind,directory).name);
  return {html:head('目标管理','部门、团队、个人目标独立维护；本人首次提交生效，后续变更由运营审批。',hasPermission('target.manage') ? '<button data-action="metrics">指标管理</button><button data-action="create-custom">配置自定义目标</button><button class="primary" data-action="create">配置目标</button>' : '')+
    dictionaryNotice(directory)+
    `<div class="notice">${esc(data.period.start)} 至 ${esc(data.period.end)} · 完成率使用已生效目标，待审批值不参与计算。</div>`+
    `<section class="card">${filters(search('搜索姓名 / 账号 / 团队')+select('period_type',options(Object.entries(periods),state.filters.period_type||'quarter',null),'周期')+
      `<input name="anchor_date" type="date" aria-label="周期内日期" value="${esc(state.filters.anchor_date||today())}">`+
      select('kind',options([...listedKinds],state.filters.kind||''),'目标指标'))}`+
    table(['目标对象','指标','周期','生效目标','待审批目标','设置依据','更新时间','操作'],data.items,r=>`<tr><td><strong>${esc(objectName(r))}</strong><small>${esc(scopes[r.scope_type])} ${esc(r.account_code||'')}</small></td><td>${esc(metric(r.kind,directory).name)}</td><td>${esc(periods[r.period_type])}<small>${esc(range(r))}</small></td><td>${esc(targetValue(r.kind,amountValue(r,'amount'),directory))}</td><td>${r.pending_change?esc(targetValue(r.kind,r.pending_change.proposed_amount,directory))+'<small>待审批，不计入生效目标</small>':'—'}</td><td>${esc(r.change_reason||(r.id?'历史目标':'尚未生效'))}</td><td>${date(r.updated_at)}</td><td>${hasPermission('target.manage') && r.can_manage===true && r.editable===true ? `<button class="link" data-action="edit" data-id="${esc(r.id||r.row_key)}">调整</button>` : ''}${hasPermission('target.history') && r.id && r.can_history ? `<button class="link" data-action="history" data-id="${esc(r.id)}">变更记录</button>` : ''}</td></tr>`)+pager(data.total)+'</section>',
    bind:root=>bindCommon(root,{create:()=>editTarget(),'create-custom':()=>editReviewTarget(),metrics:manageMetrics,edit:id=>{
      const row=data.items.find(r=>(r.id||r.row_key)===id);
      if(!row||row.can_manage!==true||row.editable!==true)throw Error('此目标当前不可维护，请刷新列表。');
      return editTarget(row);
    },history:async id=>{
      requirePermission('target.history');
      if(!data.items.find(r=>r.id===id)?.can_history)throw Error('此目标不在历史查看的授权范围');
      const response=await readForDialog(`/targets/${encodeURIComponent(id)}/history`);
      if(!response)return;
      const kind=data.items.find(r=>r.id===id).kind;
      dialog('目标变更记录',table(['时间','操作人','变更前','变更后','原因'],response.items,r=>`<tr><td>${date(r.occurred_at)}</td><td>${esc(r.actor_name||'系统')}</td><td>${esc(targetValue(kind,r.before_snapshot?.amount,directory))}</td><td>${esc(targetValue(kind,r.after_snapshot?.amount,directory))}</td><td>${esc(r.after_snapshot?.change_reason||'历史记录')}</td></tr>`),{wide:true});
    }})};
}

const changes = (r,directory) => (r.items||[r]).map(item=>`<div><strong>${esc(metric(item.kind,directory).name)}</strong><small>${esc(targetValue(item.kind,amountValue(item,'previous_amount'),directory))} → ${esc(targetValue(item.kind,amountValue(item,'proposed_amount'),directory))}</small></div>`).join('');
export async function targetRequests() {
  requirePermission('target.approve');
  const identity=actorKey(), page=state.page;
  if(!Object.keys(state.filters).length)state.filters={status:'pending',format:'batch'};
  const legacy=state.filters.format==='legacy', path=legacy?'/target-requests':'/target-batches';
  const data=await api(path+ownQuery({status:state.filters.status,limit:50,offset:state.offset}));
  const directory=await metricDirectory();
  if(identity!==actorKey()||page!==state.page)throw Error('登录身份、公司或页面已变化，请重新加载审批列表。');
  requirePermission('target.approve');
  return {html:head('目标修改审批','查看调整原因与各项前后对照。整单通过后生效，审核前继续使用原目标。')+
    dictionaryNotice(directory)+
    `<section class="card">${filters(select('status',options([['pending','待审批'],['approved','已通过'],['rejected','已驳回'],['cancelled','已关闭']],state.filters.status),'审批状态')+
      select('format',options([['batch','目标表单'],['legacy','历史单项申请']],legacy?'legacy':'batch',null),'申请类型'))}`+
    table(['申请人','周期','目标调整','申请原因','申请时间','状态与意见','操作'],data.items,r=>`<tr><td><strong>${esc(r.applicant_name)}</strong><small>${esc(r.account_code)}</small></td><td>${esc(range(r))}</td><td>${changes(r,directory)}</td><td>${esc(r.reason||'历史申请未记录原因')}</td><td>${date(r.created_at)}</td><td>${badge(r.status)}<small>${esc(r.reviewer_name||'')} ${esc(r.decision_reason||'')}</small></td><td>${r.status==='pending'?`<button class="link" data-action="review" data-id="${esc(r.id)}">审核</button>`:''}</td></tr>`)+pager(data.total)+'</section>',
    bind:root=>bindCommon(root,{review:async id=>{
      requirePermission('target.approve');
      if(identity!==actorKey()||page!==state.page)throw Error('登录身份、公司或页面已变化，请重新加载审批列表。');
      const r=data.items.find(item=>item.id===id);
      if(!r||r.status!=='pending')throw Error('此申请已不可审核，请刷新列表。');
      const currentDirectory=await readForDialog(metricDirectory);
      if(!currentDirectory)return;
      requirePermission('target.approve');
      const allowed=canApprove(r,currentDirectory);
      dialog('审核目标变更',`<p><strong>${esc(r.applicant_name)}</strong> · ${esc(range(r))}</p><div class="notice">申请原因：${esc(r.reason||'历史申请未记录原因')}</div>`+
        dictionaryNotice(currentDirectory)+(!allowed?'<div class="notice">指标单位或数值精度未能完整核对，暂不能通过审批；仍可填写原因驳回申请。</div>':'')+
        table(['指标','原目标','申请目标'],r.items||[r],item=>`<tr><td>${esc(metric(item.kind,currentDirectory).name)}</td><td>${esc(targetValue(item.kind,amountValue(item,'previous_amount'),currentDirectory))}</td><td>${esc(targetValue(item.kind,amountValue(item,'proposed_amount'),currentDirectory))}</td></tr>`)+
        '<div class="form-grid">'+field('审核结果','decision','',{select:options([...(allowed?[['approved','整单通过并生效']]:[]),['rejected','驳回申请']],allowed?'approved':'rejected',null)})+
        field('审核意见','reason','',{type:'textarea',full:true,help:'驳回必须填写原因。生效目标已变化时，系统会阻止过期申请通过。'})+'</div>',
        {wide:true,submit:'确认审核',onSubmit:async(form,key)=>{
          requirePermission('target.approve');
          if(identity!==actorKey()||page!==state.page)throw Error('登录身份、公司或页面已变化，请重新加载审批列表。');
          const decision=form.get('decision'), reason=String(form.get('reason')||'').trim();
          if(decision==='approved'&&!allowed)throw Error('请先核对完整指标单位和数值精度，再通过审批。');
          if(decision==='rejected'&&!reason)throw Error('驳回必须填写原因。');
          return api(`${path}/${encodeURIComponent(id)}/decision`,{method:'POST',key,body:{decision,reason}});
        },afterRender:d=>{$('[name=reason]',d).maxLength=2000;$('[name=decision]',d).onchange=()=>{$('[name=reason]',d).required=$('[name=decision]',d).value==='rejected';};$('[name=decision]',d).onchange();}});
    }})};
}
