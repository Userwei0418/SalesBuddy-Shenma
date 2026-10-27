import {state,$,api,esc,options,field,dialog,dialogIntent,table,head,hasPermission,requirePermission} from './core.js';

const ROOT='/api/v1/task-imports';
const priorityNames={normal:'普通',medium:'中',high:'高',urgent:'紧急'};
const date=value=>value?new Date(value).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}).replace(/\//g,'-'):'—';
const statusNames={pending_confirm:'待接受',pending_execution:'待执行',in_progress:'执行中',deferred:'延期',pending_review:'待验收',completed:'已完成',needs_adjustment:'需调整',cancelled:'已取消'};
const json=value=>JSON.stringify(value);
const accountKey=value=>String(value||'').trim().toUpperCase();
const batchPath=id=>ROOT+'/'+encodeURIComponent(id);
const warningText=value=>typeof value==='string'?value:value?.message||value?.description||'需要人工核对';
const errorText=(root,message)=>{const e=root.querySelector('.form-error');if(e)e.textContent=message;};
function canCreate(){return hasPermission('task.create_daily')||hasPermission('task.create_customer');}
function assertCreate(){if(!canCreate())requirePermission('task.create_daily');}
function activeDialog(root,permission='task.read') {
  const current=dialogIntent({liveForm:true});
  return ()=>current()&&root.open!==false&&(typeof permission==='function'?permission():hasPermission(permission));
}
function guard(current){if(!current())throw Error('当前页面或权限已变化，请重新打开批次。');}
function selectedRows(items){return items.filter(item=>item.selected!==false);}

export function deadlineInput(value) {
  if(!value)return '';
  const timestamp=Date.parse(value);
  return Number.isFinite(timestamp)?new Date(timestamp+8*3600000).toISOString().slice(0,16):'';
}
export function deadlineValue(value) {
  if(!value)return null;
  if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))throw Error('请填写有效的截止日期和时间。');
  const timestamp=Date.parse(value+':00+08:00');
  if(!Number.isFinite(timestamp)||deadlineInput(new Date(timestamp).toISOString())!==value)throw Error('截止日期和时间无效。');
  return new Date(timestamp).toISOString();
}
export function draftRows(batch) {
  const originals=new Map((batch.items||[]).map(item=>[item.item_id,item]));
  return (batch.editable_items?.length?batch.editable_items:batch.items||[]).map(item=>({
    ...originals.get(item.item_id),...item,
    selected:item.selected!==false,duplicate_acknowledged:item.duplicate_acknowledged===true,
    description:item.description||'',deliverable:item.deliverable||'',acceptance_criteria:item.acceptance_criteria||'',assignee_account_code:item.assignee_account_code||'',
    priority_code:item.priority_code||'normal',association_kind:item.association_kind||'daily',
    customer_id:item.customer_id||null,opportunity_id:item.opportunity_id||null,
  }));
}
function editPayload(item){return {execution_protocol_version:3,checklist:item.checklist||[],deliverable:item.deliverable||null,acceptance_criteria:item.acceptance_criteria||null,item_id:item.item_id,selected:item.selected!==false,description:item.description,
  assignee_account_code:item.assignee_account_code||null,due_at:item.due_at||null,priority_code:item.priority_code,
  association_kind:item.association_kind,customer_id:item.association_kind==='customer'?item.customer_id:null,
  opportunity_id:item.association_kind==='customer'?item.opportunity_id:null,duplicate_acknowledged:item.duplicate_acknowledged===true};}
export function confirmationItems(items,recipients) {
  const selected=selectedRows(items);if(!selected.length)throw Error('请至少勾选一条需要派发的待办。');
  if(selected.length>100)throw Error('一次最多派发 100 条待办。');
  return selected.map((item,index)=>{
    const person=recipients.find(p=>accountKey(p.account_code)===accountKey(item.assignee_account_code));
    if(!person)throw Error(`第 ${index+1} 条：请选择当前有效的接收人。`);
    if(!item.description?.trim()||item.description.trim().length<5||item.description.trim().length>500)throw Error(`第 ${index+1} 条：待办内容须为 5–500 字。`);
    if(!item.due_at||!Number.isFinite(Date.parse(item.due_at)))throw Error(`第 ${index+1} 条：请填写截止日期和时间。`);
    if(!Object.hasOwn(priorityNames,item.priority_code))throw Error('请选择有效优先级。');
    if(!['daily','customer'].includes(item.association_kind))throw Error('请选择有效任务类型。');
    if(item.association_kind==='customer'&&(!item.customer_id||!item.opportunity_id))throw Error(`第 ${index+1} 条：客户任务必须人工选择客户及商机。`);
    const row=editPayload({...item,description:item.description.trim(),assignee_account_code:person.account_code});
    // Old draft annotations stay in the draft; the reviewed task content is the
    // sole V3 requirement. Do not send legacy confirmation/checklist fields.
    for(const key of ['selected','checklist','deliverable','acceptance_criteria'])delete row[key];
    return row;
  });
}
async function allRecipients(current) {
  const rows=[];let offset=0;
  do {
    const page=await api('/api/v1/tasks/recipients?page_size=100&offset='+offset);guard(current);
    if(!Array.isArray(page.items))throw Error('接收人目录返回异常，请重试。');
    rows.push(...page.items);if(!page.has_more)return rows;
    if(!Number.isInteger(page.next_offset)||page.next_offset<=offset)throw Error('接收人目录已变化，请重新打开。');offset=page.next_offset;
  } while(true);
}
function personLabel(person){return `${person.name} · ${person.account_code} · ${person.team||'未分组'}`;}
function countSummary(summary={}) {
  const n=key=>Number.isInteger(summary[key])?summary[key]:'—';
  const rate=typeof summary.completion_rate==='number'?`${Number((summary.completion_rate*100).toFixed(1))}%`:'—';
  return `<div class="import-counts"><span>任务 ${n('visible_task_count')}</span><span>已完成 ${n('completed')}</span><span>完成率 ${rate}</span><span>待验收 ${n('pending_review')}</span><span>逾期 ${n('overdue')}</span><span>取消 ${n('cancelled')}</span></div>`;
}
function batchButtons(){return `<button id="task-import-new" ${canCreate()?'':'disabled'}>发放周任务</button><button id="task-import-inbox">返回任务管理</button>`;}
export async function taskImportBatches() {
  requirePermission('task.read');
  const offset=state.offset||0,data=await api(ROOT+'?page_size=50&offset='+offset);
  return {html:head('批次与进度','按批次查看待办进度。完成率按已完成计算，待验收不计完成；统计仅包含你有权查看的任务。',batchButtons())+
    `<section class="card">${table(['批次','会议日期','状态','进度','操作'],data.items||[],batch=>`<tr><td><strong>${esc(batch.batch_name)}</strong><div class="muted">${esc(batch.creator_name||'')}</div></td><td>${esc(batch.meeting_date||'—')}</td><td>${batch.status==='confirmed'?'已派发':'草稿'}</td><td>${batch.status==='confirmed'?countSummary(batch.summary):'尚未创建任务'}</td><td><button class="link" data-import-id="${esc(batch.id)}">${batch.status==='confirmed'?'查看进度':'继续核对'}</button></td></tr>`)}<div class="pager"><button id="import-prev" ${offset?'':'disabled'}>上一页</button><button id="import-next" ${data.has_more?'':'disabled'}>下一页</button></div></section>`,bind:root=>{
    $('#task-import-new',root).onclick=()=>newTaskImport();
    $('#task-import-inbox',root).onclick=()=>{delete state.filters.view;state.offset=0;state.refresh();};
    root.querySelectorAll('[data-import-id]').forEach(button=>button.onclick=()=>showTaskImport(button.dataset.importId).catch(e=>{if(root.isConnected!==false)window.alert(e.message);}));
    $('#import-prev',root).onclick=()=>{state.offset=Math.max(0,offset-50);state.refresh();};
    $('#import-next',root).onclick=()=>{state.offset=data.next_offset;state.refresh();};
  }};
}
export function newTaskImport() {
  assertCreate();
  return dialog('发放周任务 · 批量导入',`<p class="notice">粘贴会议纪要或任务清单，先生成预览，核对后才派发。派发后直接进行中；自己给自己的任务可直接完成，他人执行的任务由下发人验收。请明确任务内容、唯一执行人和截止时间。</p><div class="form-grid">${field('批次名称','batch_name','',{required:true,full:true})}${field('会议日期','meeting_date','',{type:'date',required:true})}${field('会议纪要 / 待办原文','source_text','',{type:'textarea',required:true,full:true,help:'仅用于生成待核对草稿。正文提及的其他人不会自动成为负责人。'})}</div>`,{
    wide:true,submit:'生成待核对预览',afterRender:root=>{
      const source=root.querySelector('[name=source_text]');if(source)source.maxLength=30000;
      const name=root.querySelector('[name=batch_name]');if(name)name.maxLength=120;
    },onSubmit:async(form,key,element)=>{
      assertCreate();const current=dialogIntent();
      const source_text=String(form.get('source_text')||'').trim(),batch_name=String(form.get('batch_name')||'').trim(),meeting_date=form.get('meeting_date');
      if(source_text.length<5||source_text.length>30000||!batch_name||batch_name.length>120||!meeting_date)throw Error('请填写批次名称、会议日期和 5–30,000 字的原文。');
      const batch=await api(ROOT+'/preview',{method:'POST',key,body:{source_text,batch_name,meeting_date}});guard(current);assertCreate();
      if(batch.status==='confirmed'){renderProgress(batch);return;}
      const people=await allRecipients(()=>current()&&canCreate());guard(current);assertCreate();renderDraft(batch,people);
    },
  });
}
export async function showTaskImport(id) {
  requirePermission('task.read');const current=dialogIntent();
  let batch;try{batch=await api(batchPath(id));}catch(error){if(current())throw error;return;}
  if(!current()||!hasPermission('task.read'))return;
  if(batch.status==='confirmed')return renderProgress(batch);
  assertCreate();let people;try{people=await allRecipients(()=>current()&&canCreate());}catch(error){if(current()&&canCreate())throw error;return;}if(!current())return;assertCreate();return renderDraft(batch,people);
}
function warningsHTML(item){return (item.source_warnings||[]).map(w=>`<p class="help">原文日期提示：${esc(warningText(w))}</p>`).join('')+
  (item.warnings||[]).map(w=>`<p class="help">需要核对：${esc(warningText(w))}</p>`).join('');}
function legacyDraftNote(item){
  if(!item.deliverable&&!item.acceptance_criteria&&!(item.checklist||[]).length)return '';
  return `<details class="help"><summary>旧草稿参考（需要执行的要求请写入任务内容）</summary><p>${esc(item.deliverable||'')}</p><p>${esc(item.acceptance_criteria||'')}</p>${(item.checklist||[]).map(entry=>`<p>${esc(entry.title||entry.description||'')} · ${esc(entry.acceptance_criteria||'')}</p>`).join('')}</details>`;
}
function rowHTML(item,index,people){
  const known=people.some(p=>accountKey(p.account_code)===accountKey(item.assignee_account_code));
  return `<section class="import-row" data-import-row="${index}"><div class="import-row-head"><label><input type="checkbox" data-field="selected" ${item.selected?'checked':''}> 派发第 ${index+1} 条</label><span class="muted">原文第 ${esc(item.source_line||index+1)} 条</span></div><p class="help">原始片段：${esc(item.source_text||'')}</p>${warningsHTML(item)}<label class="field"><b>待办内容（5–500 字）</b><textarea data-field="description" rows="3">${esc(item.description)}</textarea></label>${legacyDraftNote(item)}<div class="form-grid"><label class="field"><b>执行人（姓名 · 账号 · 团队）</b><select data-field="assignee_account_code">${options(people.map(p=>[p.account_code,personLabel(p)]),known?people.find(p=>accountKey(p.account_code)===accountKey(item.assignee_account_code)).account_code:'','请明确选择接收人')}</select>${!known&&item.assignee_hint?`<small>原文提示：${esc(item.assignee_hint)}；尚未确认账号</small>`:''}</label><label class="field"><b>截止时间（北京时间）</b><input type="datetime-local" data-field="due_at" value="${esc(deadlineInput(item.due_at))}"></label><label class="field"><b>优先级</b><select data-field="priority_code">${options(Object.entries(priorityNames),item.priority_code,null)}</select></label><label class="field"><b>任务类型</b><select data-field="association_kind">${options([['daily',hasPermission('task.create_daily')?'日常任务':'日常任务（未授权，请改选客户任务）'],...(hasPermission('task.create_customer')?[['customer','客户任务']]:[])],item.association_kind,null)}</select></label></div><div data-links ${item.association_kind==='customer'?'':'hidden'}><p class="help">客户任务需人工选中有权关联的客户及其商机。</p><button type="button" data-choose-links>选择客户与商机</button><span class="muted" data-link-label>${esc(item.opportunity_name||item.opportunity_id||'尚未关联')}</span></div></section>`;
}
function readRows(root,previous) {
  return previous.map((item,index)=>{
    const row=root.querySelector(`[data-import-row="${index}"]`);if(!row)return item;
    const get=field=>row.querySelector(`[data-field="${field}"]`),association_kind=get('association_kind').value;
    const next={...item,selected:get('selected').checked,description:get('description').value,
      assignee_account_code:get('assignee_account_code').value,due_at:deadlineValue(get('due_at').value),priority_code:get('priority_code').value,association_kind};
    if(association_kind==='daily'){next.customer_id=null;next.opportunity_id=null;}
    if(['description','deliverable','acceptance_criteria','assignee_account_code','due_at','association_kind','customer_id','opportunity_id'].some(key=>next[key]!==item[key]))next.duplicate_acknowledged=false;
    return next;
  });
}
function renderDraft(batch,people,savedFingerprint=null,bulkDeadline='') {
  let items=draftRows(batch),busy=false;
  const parse=batch.parse_method==='ai'?'AI 已生成草稿，仍需逐条人工核对。':'规则预拆草稿，未完成 AI 解析；请逐条核对负责人、内容和日期。';
  const root=dialog('核对导入 · '+batch.batch_name,`<p class="notice">${parse}<br>创建者就是发起与验收人。只有勾选行会派发；保存草稿不会创建任务。</p>${(batch.warnings||[]).map(w=>`<p class="help">${esc(warningText(w))}</p>`).join('')}<p class="help">重新 AI 整理只用于尚未人工调整的草稿；已有人工修改的草稿会保留，不能自动覆盖。</p><div class="import-bulk"><label>统一截止时间（北京时间） <input id="import-bulk-date" type="datetime-local"></label><button type="button" id="import-apply-date">应用到已勾选行</button></div>${items.map((item,i)=>rowHTML(item,i,people)).join('')}`,{
    wide:true,submit:'保存草稿并核对派发',footer:'<button type="button" id="import-reparse">重新 AI 整理</button><button type="button" id="import-save-draft">仅保存草稿</button>',
    onSubmit:async(form,key)=>{
      guard(current);assertCreate();if(busy)throw Error('草稿正在处理，请稍后再试。');
      items=readRows(root,items);confirmationItems(items,people);
      busy=true;
      try{const saved=await api(batchPath(batch.id)+'/draft',{method:'PATCH',key,body:{version_no:batch.version_no,items:items.map(editPayload)}});guard(current);assertCreate();renderConfirmation(saved,people);}
      finally{busy=false;}
    },afterRender:()=>{},
  });
  const current=activeDialog(root,canCreate);
  root.querySelector('#import-bulk-date').value=bulkDeadline;
  const fingerprint=()=>json({items:readRows(root,items).map(editPayload),bulkDeadline:root.querySelector('#import-bulk-date').value});
  // Keep the persisted snapshot across customer-picker round trips. A rebuilt
  // form must not make unsaved edits look like the saved draft.
  savedFingerprint??=fingerprint();
  if(fingerprint()!==savedFingerprint)root.querySelector('#dialog-form').dispatchEvent(new Event('change',{bubbles:true}));
  root.querySelector('#import-reparse').onclick=async()=>{
    if(!current()||busy)return;
    try{if(fingerprint()!==savedFingerprint)throw Error('当前有未保存的修改，已保留本页内容。不能重新 AI 整理；请继续核对或保存草稿。');}
    catch(error){errorText(root,error.message);return;}
    busy=true;const button=root.querySelector('#import-reparse'),form=root.querySelector('#dialog-form');
    button.disabled=true;button.textContent='正在重新整理…';form.inert=true;root.setAttribute('aria-busy','true');errorText(root,'');
    try{const fresh=await api(batchPath(batch.id)+'/reparse',{method:'POST',body:{version_no:batch.version_no}});guard(current);assertCreate();renderDraft(fresh,people);}
    catch(error){if(current())errorText(root,error.message+'；本页草稿已保留。请核对批次最新状态后再试。');}
    finally{busy=false;if(current()){button.disabled=false;button.textContent='重新 AI 整理';form.inert=false;root.removeAttribute('aria-busy');}}
  };
  const save=async()=>{
    if(!current()||busy)return;busy=true;const button=root.querySelector('#import-save-draft');button.disabled=true;
    const form=root.querySelector('#dialog-form');form.inert=true;
    try{items=readRows(root,items);const saved=await api(batchPath(batch.id)+'/draft',{method:'PATCH',key:crypto.randomUUID(),body:{version_no:batch.version_no,items:items.map(editPayload)}});guard(current);renderDraft(saved,people);}
    catch(error){if(current())errorText(root,error.message);}
    finally{busy=false;if(current()){button.disabled=false;form.inert=false;}}
  };
  root.querySelector('#import-save-draft').onclick=save;
  root.querySelector('#import-apply-date').onclick=()=>{
    if(!current()||busy)return;const value=root.querySelector('#import-bulk-date').value;
    try{if(!deadlineValue(value))throw Error('先填写统一截止时间。');root.querySelectorAll('[data-import-row]').forEach(row=>{if(row.querySelector('[data-field="selected"]').checked)row.querySelector('[data-field="due_at"]').value=value;});root.querySelector('#dialog-form').dispatchEvent(new Event('change',{bubbles:true}));}
    catch(error){errorText(root,error.message);}
  };
  root.querySelectorAll('[data-import-row]').forEach((row,index)=>{
    row.querySelector('[data-field="association_kind"]').onchange=()=>{if(current())row.querySelector('[data-links]').hidden=row.querySelector('[data-field="association_kind"]').value!=='customer';};
    row.querySelector('[data-choose-links]').onclick=()=>{
      if(!current()||busy)return;try{items=readRows(root,items);requirePermission('task.create_customer');showLinkPicker(batch,items,index,people,savedFingerprint,root.querySelector('#import-bulk-date').value);}catch(error){errorText(root,error.message);}
    };
  });
  return root;
}
// Link selection uses the same scoped directories as single-task creation, not CRM-wide guesses.
function showLinkPicker(batch,items,index,people,savedFingerprint,bulkDeadline) {
  let customers=[],opportunities=[],selectedCustomer=null,selectedOpportunity=null,serial=0;
  const root=dialog('关联第 '+(index+1)+' 条客户任务',`<p class="help">选择客户，再选择该客户的已有商机。选人不授予客户或商机访问权。</p><div class="import-search"><input id="import-link-q" placeholder="搜索客户名称"><button type="button" id="import-link-search">搜索</button></div><div id="import-customer-choices"></div><button type="button" id="import-customer-more" hidden>加载更多客户</button><div id="import-opportunity-choices"></div><button type="button" id="import-opportunity-more" hidden>加载更多商机</button>`,{
    wide:true,submit:'使用所选客户与商机',footer:'<button type="button" id="import-link-back">返回草稿</button>',onSubmit:async()=>{
      guard(current);requirePermission('task.create_customer');if(!selectedCustomer||!selectedOpportunity)throw Error('请明确选择客户和商机。');
      items[index]={...items[index],customer_id:selectedCustomer.id,opportunity_id:selectedOpportunity.id,customer_name:selectedCustomer.name,opportunity_name:selectedOpportunity.name,duplicate_acknowledged:false};
      renderDraft({...batch,editable_items:items},people,savedFingerprint,bulkDeadline);
    },
  });
  const current=activeDialog(root,'task.create_customer');let customerMore=null,opportunityMore=null;
  const pickerForm=root.querySelector('#dialog-form');
  // User dismissal returns to the preserved draft. Programmatic close on
  // logout/company change must never reopen the previous company's editor.
  const returnToDraft=()=>{
    if(current())renderDraft({...batch,editable_items:items},people,savedFingerprint,bulkDeadline);
    else if(root.querySelector('#dialog-form')===pickerForm)root.close();
  };
  root.querySelectorAll('[data-close]').forEach(button=>button.onclick=returnToDraft);
  root.oncancel=event=>{event.preventDefault();returnToDraft();};
  const safe=work=>async()=>{try{await work();}catch(error){if(current())errorText(root,error.message);}};
  async function loadCustomers(offset=0){
    const ticket=++serial,q=root.querySelector('#import-link-q').value.trim();
    if(!offset){customers=[];selectedCustomer=null;selectedOpportunity=null;root.querySelector('#import-opportunity-choices').innerHTML='';root.querySelector('#import-opportunity-more').hidden=true;}
    const result=await api('/api/v1/tasks/customers?page_size=20&offset='+offset+'&q='+encodeURIComponent(q));if(!current()||ticket!==serial)return;
    customers=offset?[...customers,...result.items]:result.items;customerMore=result.has_more?result.next_offset:null;
    root.querySelector('#import-customer-choices').innerHTML=customers.map(c=>`<button type="button" data-import-customer="${esc(c.id)}">${esc(c.name)}</button>`).join('')||'<p class="help">没有可关联的客户。</p>';
    root.querySelector('#import-customer-more').hidden=customerMore===null;
    root.querySelectorAll('[data-import-customer]').forEach(b=>b.onclick=safe(async()=>{selectedCustomer=customers.find(c=>c.id===b.dataset.importCustomer);selectedOpportunity=null;opportunities=[];await loadOpportunities();}));
  }
  async function loadOpportunities(offset=0){
    if(!selectedCustomer)return;const ticket=++serial,customer=selectedCustomer;
    const result=await api('/api/v1/tasks/opportunities?customer_id='+encodeURIComponent(customer.id)+'&page_size=20&offset='+offset);if(!current()||ticket!==serial||selectedCustomer!==customer)return;
    opportunities=offset?[...opportunities,...result.items]:result.items;opportunityMore=result.has_more?result.next_offset:null;
    root.querySelector('#import-opportunity-choices').innerHTML=`<h4>${esc(customer.name)} · 选择商机</h4>`+opportunities.map(o=>`<label class="import-choice"><input type="radio" name="import_opportunity" value="${esc(o.id)}">${esc(o.name)}</label>`).join('');
    root.querySelector('#import-opportunity-more').hidden=opportunityMore===null;
    root.querySelectorAll('[name=import_opportunity]').forEach(input=>input.onchange=()=>{if(current())selectedOpportunity=opportunities.find(o=>o.id===input.value);});
  }
  root.querySelector('#import-link-back').onclick=returnToDraft;
  root.querySelector('#import-link-search').onclick=safe(()=>loadCustomers());
  root.querySelector('#import-link-q').oninput=()=>{serial++;};
  root.querySelector('#import-customer-more').onclick=safe(()=>customerMore!==null?loadCustomers(customerMore):undefined);
  root.querySelector('#import-opportunity-more').onclick=safe(()=>opportunityMore!==null?loadOpportunities(opportunityMore):undefined);
  safe(()=>loadCustomers())();return root;
}
function duplicateHTML(item,index){
  if(!(item.duplicates||[]).length)return '';
  return `<div class="notice"><strong>发现可能相似的待办，请核对是否重复</strong>${item.duplicates.map(d=>`<p>${esc(d.description||d.title||warningText(d))}${d.assignee_name||d.owner_name?' · '+esc(d.assignee_name||d.owner_name):''}${d.status?' · '+esc(statusNames[d.status]||d.status):''}</p>`).join('')}<label><input type="checkbox" name="duplicate_${index}" ${item.duplicate_acknowledged?'checked':''}> 我已核对，仍需派发这条待办</label></div>`;
}
function renderConfirmation(batch,people) {
  let latestBatch=batch,needsReview=false;
  const items=selectedRows(draftRows(batch));confirmationItems(items,people);
  const root=dialog('最终核对 · '+batch.batch_name,`<p class="notice">将派发 ${items.length} 条独立待办，每条由所选接收人独立处理。发起与验收人：${esc(state.actor?.display_name||state.actor?.account_code||'当前账号')}。确认后直接进入进行中；原始派发记录保留。自办任务直接完成，他人完成后由下发人验收，后续调整另留历史。</p>${items.map((item,i)=>{const person=people.find(p=>accountKey(p.account_code)===accountKey(item.assignee_account_code));return `<section class="import-row"><h3>${i+1}. ${esc(personLabel(person))}</h3><p class="import-description">${esc(item.description)}</p>${legacyDraftNote(item)}<p>截止 ${date(item.due_at)}（北京时间） · ${esc(priorityNames[item.priority_code])}优先级 · ${item.association_kind==='daily'?'日常任务':'客户任务：'+esc(item.customer_name||item.customer_id)+' / '+esc(item.opportunity_name||item.opportunity_id)}</p>${warningsHTML(item)}${duplicateHTML(item,i)}</section>`;}).join('')}`,{
    wide:true,submit:'确认派发所选待办',footer:'<button type="button" id="import-review-back">返回修改</button>',onSubmit:async(form,key)=>{
      guard(current);assertCreate();if(needsReview)throw Error('草稿或重复提醒已更新，请返回修改并重新核对。');const confirmed=items.map((item,index)=>({...item,duplicate_acknowledged:(item.duplicates||[]).length?form.get('duplicate_'+index)==='on':false}));
      if(confirmed.some(item=>(item.duplicates||[]).length&&!item.duplicate_acknowledged))throw Error('请逐条确认相似重复提醒，或返回修改。');
      const payload=confirmationItems(confirmed,people);
      for(const item of payload)requirePermission(item.association_kind==='customer'?'task.create_customer':'task.create_daily');
      try{const result=await api(batchPath(batch.id)+'/confirm',{method:'POST',key,body:{version_no:batch.version_no,items:payload}});guard(current);renderProgress(result);}
      catch(error){
        if(!current())return;
        // Never blindly repeat a dispatch. Its write receipt may have been lost.
        let recovered;try{recovered=await api(batchPath(batch.id));}catch(_){if(current())throw Error('派发结果暂未确认。请先到导入批次刷新查看；不要另建批次重复派发。');return;}
        guard(current);if(recovered.status==='confirmed'){renderProgress(recovered);return;}
        latestBatch=recovered;const refreshed=draftRows(recovered);
        needsReview=recovered.version_no!==batch.version_no||items.some(item=>json(item.duplicates||[])!==json(refreshed.find(row=>row.item_id===item.item_id)?.duplicates||[]));
        if(needsReview)throw Error('服务器草稿或重复提醒已更新；未继续派发，请返回修改并重新核对。');
        throw Error(error.message+'；已读取批次，当前仍为草稿。核对后可使用本次提交标识明确重试，或返回修改。');
      }
    },
  });
  const current=activeDialog(root,canCreate);root.querySelector('#import-review-back').onclick=()=>{if(current())renderDraft(latestBatch,people);};return root;
}
function renderProgress(batch) {
  const owners=batch.assignees||[];
  return dialog('批次进度 · '+batch.batch_name,`<p class="notice">按当前负责人统计，转交后随当前负责人更新。已完成、待验收、逾期及取消分列，待验收不计完成；仅显示当前授权可见任务。</p>${countSummary(batch.summary)}${table(['当前负责人','任务数','已完成','待验收','逾期','取消','完成率'],owners,o=>`<tr><td>${esc(o.name)}</td><td>${esc(o.task_count)}</td><td>${esc(o.completed)}</td><td>${esc(o.pending_review)}</td><td>${esc(o.overdue??'—')}</td><td>${esc(o.cancelled)}</td><td>${typeof o.completion_rate==='number'?Number((o.completion_rate*100).toFixed(1))+'%':'—'}</td></tr>`)}<h3>派发记录与当前状态</h3>${table(['任务','原始接收人','当前负责人','状态','截止时间（北京时间）','操作'],batch.tasks||[],t=>{const owner=(t.assignees||[]).find(a=>a.responsibility==='owner');return `<tr><td><strong>${esc(t.title)}</strong><div class="muted">${esc(t.description)}</div></td><td>${esc(t.original_assignee?.name||'—')}<div class="muted">${esc(t.original_assignee?.account_code||'')}</div></td><td>${esc(owner?.name||t.owner_name||'待交接')}</td><td>${esc(t.handover_required?'待交接':statusNames[t.status]||t.status)}</td><td>${date(t.due_at)}</td><td><button type="button" class="link" data-import-task="${esc(t.id)}">查看 / 处理</button></td></tr>`;})}`,{wide:true,footer:'<button type="button" id="import-progress-refresh">刷新进度</button>',afterRender:root=>{const current=activeDialog(root);root.querySelector('#import-progress-refresh').onclick=()=>{if(current())showTaskImport(batch.id).catch(e=>{if(current())errorText(root,e.message);});};root.querySelectorAll('[data-import-task]').forEach(button=>button.onclick=async()=>{if(!current())return;try{const {showTask}=await import('./tasks.js');if(current())await showTask(button.dataset.importTask);}catch(error){if(current())errorText(root,error.message);}});}});
}
