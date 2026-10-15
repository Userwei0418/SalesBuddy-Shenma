const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const tick=()=>new Promise(setImmediate);
const unescape=s=>String(s||'').replace(/&(?:amp|quot|lt|gt|#39);/g,c=>({'&amp;':'&','&quot;':'"','&lt;':'<','&gt;':'>','&#39;':"'"})[c]);
function matches(node,selector){
  if(selector[0]==='#')return node.id===selector.slice(1);
  if(selector[0]==='.')return (node.className||'').split(/\s+/).includes(selector.slice(1));
  const attr=selector.match(/^(\w+)?\[([^=\]]+)(?:=["']?([^"'\]]*)["']?)?\]$/);
  if(attr)return (!attr[1]||node.tagName===attr[1].toUpperCase())&&Object.hasOwn(node.attrs,attr[2])&&(attr[3]===undefined||String(node.attrs[attr[2]])===attr[3]);
  return node.tagName===selector.toUpperCase();
}
function tree(tag='div'){
  return {tagName:tag.toUpperCase(),children:[],attrs:{},listeners:{},dataset:{},value:'',checked:false,disabled:false,open:false,hidden:false,isConnected:true,_html:'',_text:'',
    set innerHTML(html){this._html=html;this.children=[];const stack=[this];let cursor=0;for(const m of html.matchAll(/<(\/?)([a-z][\w-]*)([^>]*)>/gi)){
      const preceding=html.slice(cursor,m.index);if(preceding)stack.at(-1)._text+=unescape(preceding);cursor=m.index+m[0].length;
      if(m[1]){while(stack.length>1){if(stack.pop().tagName===m[2].toUpperCase())break;}continue;}
      const child=tree(m[2]);for(const a of m[3].matchAll(/([\w-]+)(?:="([^"]*)")?/g))child.setAttribute(a[1],a[2]===undefined?true:unescape(a[2]));
      stack.at(-1).append(child);if(!['input','hr','img','br'].includes(m[2].toLowerCase()))stack.push(child);
    }
    for(const n of this.descendants()){
      if(n.tagName==='TEXTAREA')n.value=n._text;
      if(n.tagName==='SELECT'){const choices=n.querySelectorAll('option');n.value=(choices.find(c=>c.attrs.selected)||choices[0])?.value||'';}
    }},get innerHTML(){return this._html+this.children.map(child=>child.innerHTML).join('');},
    set textContent(value){this._text=value;this.children=[];},get textContent(){return this._text+this.children.map(c=>c.textContent).join('');},
    get firstElementChild(){return this.children[0]||null;},
    descendants(){return this.children.flatMap(c=>[c,...c.descendants()]);},querySelectorAll(selector){return this.descendants().filter(c=>matches(c,selector));},querySelector(selector){return this.querySelectorAll(selector)[0]||null;},
    setAttribute(key,value){this.attrs[key]=value;this[key==='class'?'className':key]=value;if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;},removeAttribute(key){delete this.attrs[key];delete this[key];},
    addEventListener(event,fn){(this.listeners[event]??=[]).push(fn);},dispatchEvent(event){for(const fn of this.listeners[event.type]||[])fn(event);},
    showModal(){this.open=true;},close(){this.open=false;},append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}},
    before(...nodes){const parent=this.parentElement;if(parent){for(const node of nodes)node.parentElement=parent;parent.children.splice(parent.children.indexOf(this),0,...nodes);}},
    remove(){this.isConnected=false;const parent=this.parentElement;if(parent)parent.children.splice(parent.children.indexOf(this),1);},scrollIntoView(){},
  };
}
async function harness(){
  const core=await import(pathToFileURL(path.join(assets,'core.js')).href),imports=await import(pathToFileURL(path.join(assets,'task-imports.js')).href),tasks=await import(pathToFileURL(path.join(assets,'tasks.js')).href);
  core.clearSession();Object.assign(core.state,{actor:{workspace_id:'w',user_id:'me',role:'manager',display_name:'本次发起人'},token:'fixture',company:{id:'w'},page:'tasks',filters:{},offset:0,
    permissions:{'task.read':true,'task.create_daily':true,'task.create_customer':true,'task.assign':true},refresh:async()=>{}});
  const d=tree('dialog'),content=tree('main');content.append(tree());
  global.document={querySelector:s=>s==='#dialog'?d:s==='#content'?content:d.querySelector(s),createElement:tree,dispatchEvent(){}};
  global.window={alert(){}};
  global.FormData=class{constructor(form){this.items=form.descendants().filter(n=>n.attrs.name&&!n.disabled&&(!['checkbox','radio'].includes(n.type)||n.checked)).map(n=>[n.attrs.name,n.type==='checkbox'?(n.value||'on'):n.value]);}get(key){return this.items.find(([name])=>name===key)?.[1]??null;}};
  const requests=[];global.fetch=(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}));
  return {core,imports,tasks,d,content,requests,get form(){return d.querySelector('#dialog-form');},
    async reply(request,data,status=200){request.resolve({ok:status<400,status,json:async()=>data});await tick();},
    async fail(request){request.reject(Error('network interrupted'));await tick();},
    submit(){const form=d.querySelector('#dialog-form');return form.onsubmit({preventDefault(){},currentTarget:form});},
  };
}
const person={id:'p',account_code:'PERSON',name:'明确接收人',team:'北区',team_id:'north'};
const sourceRow={item_id:'i1',source_line:1,source_text:'明确接收人跟进客户沟通事项',description:'联系正文提及的其他人，核对项目下一步',assignee_hint:'明确接收人',assignee_account_code:'PERSON',due_at:'2026-10-01T09:00:00Z',priority_code:'normal',association_kind:'daily',customer_id:null,opportunity_id:null,warnings:[],duplicates:[]};
const draft=(extra={})=>({id:'batch',batch_name:'会议跟进',meeting_date:'2026-09-28',version_no:1,status:'draft',parse_method:'rules',warnings:[],items:[{...sourceRow}],editable_items:[{...sourceRow,selected:true}],...extra});
const confirmed=()=>({...draft(),status:'confirmed',summary:{visible_task_count:1,completed:0,pending_review:1,overdue:1,cancelled:0,completion_rate:0},tasks:[{id:'task1',title:'任务',description:'原派发内容',status:'pending_review',due_at:sourceRow.due_at,original_assignee:{name:'原人',account_code:'OLD'},assignees:[{responsibility:'owner',name:'现人'}]}],assignees:[{user_id:'current',name:'现人',task_count:1,completed:0,pending_review:1,overdue:1,cancelled:0,completion_rate:0}]});
async function openDraft(h,batch=draft()){
  const loading=h.imports.showTaskImport('batch');await h.reply(h.requests.at(-1),batch);await h.reply(h.requests.at(-1),{items:[person],has_more:false});await loading;return h;
}
function input(h,field,value,index=0){const el=h.d.querySelector(`[data-import-row="${index}"]`).querySelector(`[data-field="${field}"]`);if(field==='selected')el.checked=value;else el.value=value;return el;}
async function review(h,batch=draft({version_no:2})){
  const pending=h.submit();assert.match(h.requests.at(-1).url,/\/draft$/);await h.reply(h.requests.at(-1),batch);await pending;assert.match(h.d.innerHTML,/最终核对/);
}

test('daily import entry uses effective capability and customer-only creators are not locked out',async()=>{
  const h=await harness();h.core.state.permissions['task.create_daily']=false;h.imports.newTaskImport();assert.match(h.d.innerHTML,/批量导入/);
  h.core.state.permissions['task.create_customer']=false;assert.throws(()=>h.imports.newTaskImport(),/未获/);
});
test('preview requires meeting metadata, sends one keyed write, displays rule parsing honestly and never dispatches',async()=>{
  const h=await harness();h.imports.newTaskImport();h.d.querySelector('[name=batch_name]').value='会议批次';h.d.querySelector('[name=meeting_date]').value='2026-09-28';h.d.querySelector('[name=source_text]').value='原文涉及其他人，不代表责任人';
  const saving=h.submit();await h.submit();assert.equal(h.requests.length,1);const post=h.requests[0];assert.match(post.url,/\/preview$/);assert(post.options.headers['Idempotency-Key']);
  assert.deepEqual(JSON.parse(post.options.body),{source_text:'原文涉及其他人，不代表责任人',batch_name:'会议批次',meeting_date:'2026-09-28'});
  await h.reply(post,draft());await h.reply(h.requests.at(-1),{items:[person],has_more:false});await saving;
  assert.match(h.d.innerHTML,/规则预拆草稿，未完成 AI 解析/);assert.match(h.d.innerHTML,/明确接收人 · PERSON · 北区/);assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,0);
});
test('new preview deduplication can return an already dispatched batch without opening another confirmation',async()=>{
  const h=await harness();h.imports.newTaskImport();for(const [name,value] of Object.entries({batch_name:'会议',meeting_date:'2026-09-28',source_text:'同一段测试原文'}))h.d.querySelector(`[name=${name}]`).value=value;
  const saving=h.submit();await h.reply(h.requests.at(-1),confirmed());await saving;
  assert.match(h.d.innerHTML,/批次进度/);assert.equal(h.requests.length,1);assert.equal(h.d.querySelector('button[type=submit]'),null);
});
test('explicit reparse posts the viewed version once, blocks concurrent saves, and returns only to a new AI draft',async()=>{
  const h=await harness();await openDraft(h);const oldForm=h.form,button=h.d.querySelector('#import-reparse'),before=h.requests.length;
  const pending=button.onclick(),request=h.requests.at(-1);assert.match(request.url,/\/batch\/reparse$/);assert.equal(request.options.method,'POST');
  assert.deepEqual(JSON.parse(request.options.body),{version_no:1});assert.equal(button.disabled,true);assert.equal(oldForm.inert,true);
  await button.onclick();await h.d.querySelector('#import-save-draft').onclick();assert.equal(h.requests.length,before+1);
  const fresh={...sourceRow,item_id:'ai-new',description:'AI重新整理后的待办内容'};
  await h.reply(request,draft({version_no:2,parse_method:'ai',items:[fresh],editable_items:[{...fresh,selected:true}]}));await pending;
  assert.notEqual(h.form,oldForm);assert.match(h.d.innerHTML,/AI 已生成草稿/);assert.doesNotMatch(h.d.innerHTML,/最终核对/);
  assert.equal(h.d.querySelector('[data-field="description"]').value,fresh.description);assert.equal(h.d.querySelector('#import-reparse').disabled,false);
  assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,0);
});
for(const [field,value] of [['description','尚未保存的人工修改内容'],['due_at','2026-10-02T18:00'],['assignee_account_code',''],['priority_code','high'],['selected',false],['association_kind','customer']])test('reparse refuses unsaved '+field+' without saving, discarding or replacing the form',async()=>{
  const h=await harness();await openDraft(h);input(h,field,value);const form=h.form,count=h.requests.length;
  await h.d.querySelector('#import-reparse').onclick();assert.equal(h.requests.length,count);assert.equal(h.form,form);
  const control=h.d.querySelector(`[data-field="${field}"]`);assert.equal(field==='selected'?control.checked:control.value,value);
  assert.match(h.d.querySelector('.form-error').textContent,/未保存的修改/);assert.equal(h.d.querySelector('#import-reparse').disabled,false);
});
test('an unapplied bulk deadline is also protected before AI reparse',async()=>{
  const h=await harness();await openDraft(h);h.d.querySelector('#import-bulk-date').value='2026-10-02T18:00';const count=h.requests.length;
  await h.d.querySelector('#import-reparse').onclick();assert.equal(h.requests.length,count);assert.equal(h.d.querySelector('#import-bulk-date').value,'2026-10-02T18:00');
  assert.match(h.d.querySelector('.form-error').textContent,/未保存的修改/);
});
for(const [message,changed] of [['草稿已有人工编辑，请保留当前内容；不能自动覆盖后重新整理',true],['版本已变化，请刷新后重试',false]])test('reparse 409 preserves the form and restores the button: '+message,async()=>{
  const h=await harness(),row={...sourceRow,description:changed?'服务器保存的人工修改内容':sourceRow.description,selected:true};
  await openDraft(h,draft({version_no:4,editable_items:[row]}));const form=h.form,pending=h.d.querySelector('#import-reparse').onclick(),request=h.requests.at(-1),count=h.requests.length;
  assert.deepEqual(JSON.parse(request.options.body),{version_no:4});await h.reply(request,{detail:message},409);await pending;
  assert.equal(h.form,form);assert.equal(h.d.querySelector('[data-field="description"]').value,row.description);assert.equal(h.d.querySelector('#import-reparse').disabled,false);
  assert.equal(form.inert,false);assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,new RegExp(message));
});
test('failed reparse preserves the original draft without automatically replaying or claiming its server state',async()=>{
  const h=await harness();await openDraft(h);const form=h.form,pending=h.d.querySelector('#import-reparse').onclick(),request=h.requests.at(-1),count=h.requests.length;
  await h.fail(request);await pending;assert.equal(h.form,form);assert.equal(h.requests.length,count);assert.equal(form.inert,false);
  assert.equal(h.d.querySelector('#import-reparse').disabled,false);assert.equal(h.d.querySelector('[data-field="description"]').value,sourceRow.description);
  assert.match(h.d.querySelector('.form-error').textContent,/本页草稿已保留.*核对批次最新状态/);
});
for(const mode of ['save','review'])test('pending '+mode+' prevents a concurrent AI reparse',async()=>{
  const h=await harness();await openDraft(h);const pending=mode==='save'?h.d.querySelector('#import-save-draft').onclick():h.submit(),request=h.requests.at(-1),count=h.requests.length;
  await h.d.querySelector('#import-reparse').onclick();assert.equal(h.requests.length,count);
  await h.reply(request,draft({version_no:2}));await pending;assert.equal(h.requests.some(r=>r.url.endsWith('/reparse')),false);
});
for(const change of ['close','replace','logout','tenant','permission'])test('late AI reparse result cannot reopen or replace a form after '+change,async()=>{
  const h=await harness();await openDraft(h);const pending=h.d.querySelector('#import-reparse').onclick(),request=h.requests.at(-1),count=h.requests.length;
  if(change==='close')h.d.close();else if(change==='replace')h.core.dialog('新编辑窗口','正在编辑其他内容');else if(change==='logout')h.core.clearSession();else if(change==='tenant')h.core.setCompany({id:'other'});else {h.core.state.permissions['task.create_daily']=false;h.core.state.permissions['task.create_customer']=false;}
  const form=h.form;await h.reply(request,draft({version_no:2,parse_method:'ai'}));await pending;
  assert.equal(h.form,form);assert.equal(h.requests.length,count);if(change==='close')assert.equal(h.d.open,false);if(change==='replace')assert.match(h.d.innerHTML,/新编辑窗口/);
});
test('saved editable rows restore exactly, and selected dates are changed only by explicit bulk action',async()=>{
  const h=await harness(),second={...sourceRow,item_id:'i2',description:'第二条独立待办内容',selected:false};
  await openDraft(h,draft({editable_items:[{...sourceRow,description:'服务器保存的修改',selected:true},second]}));
  assert.equal(h.d.querySelector('[data-field="description"]').value,'服务器保存的修改');
  h.d.querySelector('#import-bulk-date').value='2026-10-05T18:30';h.d.querySelector('#import-apply-date').onclick();
  assert.equal(h.d.querySelector('[data-import-row="0"]').querySelector('[data-field="due_at"]').value,'2026-10-05T18:30');assert.equal(h.d.querySelector('[data-import-row="1"]').querySelector('[data-field="due_at"]').value,'2026-10-01T17:00');
  const saved=h.d.querySelector('#import-save-draft').onclick(),request=h.requests.at(-1);assert.equal(request.options.method,'PATCH');const body=JSON.parse(request.options.body);
  assert.equal(body.items[0].due_at,'2026-10-05T10:30:00.000Z');assert.equal(body.items[1].selected,false);
  await h.reply(request,draft({version_no:2,editable_items:body.items}));await saved;assert.equal(h.requests.some(r=>r.url.endsWith('/confirm')),false);
});
test('source date evidence is escaped, separate from missing fields, and cleared after a corrected draft is saved',async()=>{
  const h=await harness(),original={...sourceRow,due_at:null,warnings:['原文未给出明确截止日期，请补充']};
  const current={...original,selected:true,warnings:['请补充截止日期与时间'],source_warnings:['日期 <script>unsafe</script> 需要人工核对']};
  await openDraft(h,draft({items:[original],editable_items:[current]}));
  assert.match(h.d.innerHTML,/原文日期提示：日期 &lt;script&gt;unsafe&lt;\/script&gt;/);
  assert.match(h.d.innerHTML,/需要核对：请补充截止日期与时间/);assert.doesNotMatch(h.d.innerHTML,/<script>/);
  input(h,'due_at','2026-10-05T18:30');const saving=h.d.querySelector('#import-save-draft').onclick(),request=h.requests.at(-1),body=JSON.parse(request.options.body);
  assert.equal(Object.hasOwn(body.items[0],'source_warnings'),false);assert.equal(Object.hasOwn(body.items[0],'warnings'),false);
  await h.reply(request,draft({version_no:2,items:[original],editable_items:body.items.map(item=>({...item,warnings:[],source_warnings:[]}))}));await saving;
  assert.doesNotMatch(h.d.innerHTML,/原文日期提示：|请补充截止日期与时间|原文未给出明确截止日期/);
});
test('default 23:59 source reminder remains visible in final review without being sent as a command',async()=>{
  const h=await harness(),row={...sourceRow,selected:true,source_warnings:['原文仅给日期，暂按北京时间23:59，请确认']};
  await openDraft(h,draft({editable_items:[row]}));await review(h,draft({version_no:2,editable_items:[row]}));
  assert.match(h.d.innerHTML,/原文日期提示：原文仅给日期，暂按北京时间23:59，请确认/);
  const saving=h.submit(),request=h.requests.at(-1),body=JSON.parse(request.options.body);
  assert.equal(Object.hasOwn(body.items[0],'source_warnings'),false);await h.reply(request,confirmed());await saving;
});
for(const [field,value,message] of [['assignee_account_code','','接收人'],['due_at','','截止'],['description','短','5–500']])test('invalid selected '+field+' blocks save-and-dispatch but not safe draft editing',async()=>{
  const h=await harness();await openDraft(h);input(h,field,value);const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,new RegExp(message));
});
test('final confirmation sends only selected items, explicit account identity, reviewed version and one idempotency key',async()=>{
  const h=await harness(),second={...sourceRow,item_id:'i2',selected:false};await openDraft(h,draft({editable_items:[{...sourceRow,selected:true},second]}));
  await review(h,draft({version_no:2,editable_items:[{...sourceRow,selected:true},second]}));const saving=h.submit(),request=h.requests.at(-1);await h.submit();
  assert.match(request.url,/\/confirm$/);assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,1);
  const body=JSON.parse(request.options.body);assert.equal(body.version_no,2);assert.equal(body.items.length,1);assert.equal(body.items[0].assignee_account_code,'PERSON');assert.equal(Object.hasOwn(body.items[0],'assignee_name'),false);
  assert(request.options.headers['Idempotency-Key']);await h.reply(request,confirmed());await saving;
  assert.match(h.d.innerHTML,/原人/);assert.match(h.d.innerHTML,/现人/);assert.match(h.d.innerHTML,/待验收/);assert.match(h.d.innerHTML,/逾期 1/);
});
test('fresh duplicate warnings require explicit per-row acknowledgement and can be cancelled back to draft',async()=>{
  const h=await harness();await openDraft(h);const duplicate={...sourceRow,selected:true,duplicates:[{task_id:'previous',description:'类似待办',assignee_name:'明确接收人'}]};await review(h,draft({version_no:2,editable_items:[duplicate]}));
  const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,/相似重复/);
  h.d.querySelector('[name=duplicate_0]').checked=true;const saving=h.submit(),request=h.requests.at(-1);assert.equal(JSON.parse(request.options.body).items[0].duplicate_acknowledged,true);await h.reply(request,confirmed());await saving;
});
for(const change of ['close','replace','logout','tenant','permission','page'])test('late draft read cannot open a form after '+change,async()=>{
  const h=await harness();h.core.dialog('当前窗口','等待');const loading=h.imports.showTaskImport('batch'),request=h.requests.at(-1);
  if(change==='close')h.d.close();else if(change==='replace')h.core.dialog('新窗口','新内容');else if(change==='logout')h.core.clearSession();else if(change==='tenant')h.core.setCompany({id:'other'});else if(change==='permission')h.core.state.permissions['task.read']=false;else h.core.state.page='customers';
  const before=h.d.innerHTML;await h.reply(request,draft());await loading;assert.equal(h.d.innerHTML,before);assert.equal(h.requests.length,1);
});
for(const change of ['close','replace','logout','tenant','permission'])test('old final form cannot dispatch after '+change,async()=>{
  const h=await harness();await openDraft(h);await review(h);const form=h.form,count=h.requests.length;
  if(change==='close')h.d.close();else if(change==='replace')h.core.dialog('新窗口','新内容');else if(change==='logout')h.core.clearSession();else if(change==='tenant')h.core.setCompany({id:'other'});else {h.core.state.permissions['task.create_daily']=false;h.core.state.permissions['task.create_customer']=false;}
  await form.onsubmit({preventDefault(){},currentTarget:form});assert.equal(h.requests.length,count);
});
test('unknown dispatch response reads batch once and restores confirmed receipt without reposting',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit(),post=h.requests.at(-1);await h.fail(post);const read=h.requests.at(-1);assert.equal(read.options.method,'GET');assert.match(read.url,/\/batch$/);
  await h.reply(read,confirmed());await saving;assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,1);assert.match(h.d.innerHTML,/批次进度/);
});
test('unknown dispatch still draft permits explicit same-key retry, never automatic replay',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit(),post=h.requests.at(-1),key=post.options.headers['Idempotency-Key'];await h.fail(post);await h.reply(h.requests.at(-1),draft({version_no:2}));await saving;
  assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,1);assert.match(h.d.querySelector('.form-error').textContent,/仍为草稿/);
  const retry=h.submit(),again=h.requests.at(-1);assert.equal(again.options.headers['Idempotency-Key'],key);await h.reply(again,confirmed());await retry;
});
test('confirmed batch progress never includes hidden source text and uses server current-owner totals',async()=>{
  const h=await harness(),data=confirmed();data.source_text=null;data.items=[];data.editable_items=[];const loading=h.imports.showTaskImport('batch');await h.reply(h.requests.at(-1),data);await loading;
  assert.match(h.d.innerHTML,/已完成 0/);assert.match(h.d.innerHTML,/完成率 0%/);assert.match(h.d.innerHTML,/原人/);assert.match(h.d.innerHTML,/现人/);assert.equal(h.requests.length,1);
});
test('customer-only creator can review a customer task but cannot dispatch an unauthorized daily row',async()=>{
  const h=await harness();h.core.state.permissions['task.create_daily']=false;await openDraft(h);await review(h);const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,/未获/);
});
test('Beijing deadline conversion rejects impossible dates and preserves exact minute',async()=>{
  const h=await harness();assert.equal(h.imports.deadlineValue('2026-10-01T17:00'),'2026-10-01T09:00:00.000Z');assert.throws(()=>h.imports.deadlineValue('2026-02-30T12:00'),/无效/);assert.equal(h.imports.deadlineValue(''),null);
});

test('customer picker stays active after dynamic HTML updates and late first-customer opportunities cannot replace the second',async()=>{
  const h=await harness();await openDraft(h);input(h,'association_kind','customer');const row=h.d.querySelector('[data-import-row="0"]');row.querySelector('[data-choose-links]').onclick();
  assert.match(h.requests.at(-1).url,/\/tasks\/customers\?/);
  await h.reply(h.requests.at(-1),{items:[{id:'c1',name:'客户甲'},{id:'c2',name:'客户乙'}],has_more:false});
  const one=h.d.querySelector('[data-import-customer="c1"]').onclick(),old=h.requests.at(-1);
  const two=h.d.querySelector('[data-import-customer="c2"]').onclick(),fresh=h.requests.at(-1);
  assert.match(fresh.url,/customer_id=c2/);
  await h.reply(fresh,{items:[{id:'o2',name:'乙商机',customer_id:'c2'}],has_more:false});await two;
  await h.reply(old,{items:[{id:'o1',name:'旧甲商机',customer_id:'c1'}],has_more:false});await one;
  const choices=h.d.querySelector('#import-opportunity-choices');assert.match(choices.innerHTML,/乙商机/);assert.doesNotMatch(choices.innerHTML,/旧甲商机/);
  const radio=h.d.querySelector('[name=import_opportunity]');radio.checked=true;radio.onchange();await h.submit();assert.match(h.d.innerHTML,/核对导入/);
  const count=h.requests.length;await h.d.querySelector('#import-reparse').onclick();assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,/未保存的修改/);
  const saving=h.d.querySelector('#import-save-draft').onclick(),request=h.requests.at(-1),body=JSON.parse(request.options.body);
  assert.equal(body.items[0].customer_id,'c2');assert.equal(body.items[0].opportunity_id,'o2');assert.equal(body.items[0].duplicate_acknowledged,false);
  await h.reply(request,draft({version_no:2,editable_items:body.items}));await saving;
});
test('customer searches ignore the old response after a new search and no association is inferred from text',async()=>{
  const h=await harness();await openDraft(h);input(h,'association_kind','customer');h.d.querySelector('[data-choose-links]').onclick();const old=h.requests.at(-1);
  const search=h.d.querySelector('#import-link-q');search.value='乙';search.oninput();const freshAction=h.d.querySelector('#import-link-search').onclick(),fresh=h.requests.at(-1);
  await h.reply(fresh,{items:[{id:'c2',name:'新乙客户'}],has_more:false});await freshAction;await h.reply(old,{items:[{id:'c1',name:'旧甲客户'}],has_more:false});
  assert.match(h.d.querySelector('#import-customer-choices').innerHTML,/新乙客户/);assert.doesNotMatch(h.d.querySelector('#import-customer-choices').innerHTML,/旧甲客户/);
  const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);assert.match(h.d.querySelector('.form-error').textContent,/明确选择客户和商机/);
});
test('closing customer picker drops a late directory response and reopening the same batch does not revive it',async()=>{
  const h=await harness();await openDraft(h);input(h,'association_kind','customer');h.d.querySelector('[data-choose-links]').onclick();const old=h.requests.at(-1);h.d.close();
  await openDraft(h);const before=h.d.innerHTML;await h.reply(old,{items:[{id:'late',name:'迟到客户'}],has_more:false});assert.equal(h.d.innerHTML,before);
});
for(const dismissal of ['close','cancel','escape','back'])test('customer picker '+dismissal+' returns to unsaved draft and closing that draft still requires discard confirmation',async()=>{
  const h=await harness();await openDraft(h);
  input(h,'description','尚未保存的重要人工调整');input(h,'association_kind','customer');h.form.dispatchEvent(new Event('change'));
  h.d.querySelector('#import-bulk-date').value='2026-10-06T18:30';
  h.d.querySelector('[data-choose-links]').onclick();const late=h.requests.at(-1),count=h.requests.length;
  if(dismissal==='escape'){let prevented=false;h.d.oncancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);}
  else if(dismissal==='back')h.d.querySelector('#import-link-back').onclick();
  else h.d.querySelectorAll('[data-close]')[dismissal==='cancel'?1:0].onclick();
  assert.match(h.d.innerHTML,/核对导入/);assert.equal(h.d.open,true);
  assert.equal(h.d.querySelector('[data-field="description"]').value,'尚未保存的重要人工调整');
  assert.equal(h.d.querySelector('#import-bulk-date').value,'2026-10-06T18:30');
  const form=h.form;await h.reply(late,{items:[{id:'late',name:'迟到客户'}],has_more:false});assert.equal(h.form,form);assert.equal(h.requests.length,count);
  h.d.querySelector('[data-close]').onclick();assert.equal(h.d.open,true);assert(h.d.querySelector('.discard-confirm'));
  h.d.querySelector('[data-keep]').onclick();assert.equal(h.d.open,true);assert.equal(h.d.querySelector('.discard-confirm'),null);
  h.d.querySelector('[data-close]').onclick();h.d.querySelector('[data-discard]').onclick();assert.equal(h.d.open,false);
  assert.equal(h.requests.some(r=>['PATCH','POST'].includes(r.options.method)),false);
});
for(const change of ['logout','tenant','permission','replace'])test('customer picker never restores the old draft after '+change,async()=>{
  const h=await harness();await openDraft(h);input(h,'description','旧公司未保存的修改');input(h,'association_kind','customer');h.d.querySelector('[data-choose-links]').onclick();
  const close=h.d.querySelector('[data-close]').onclick,back=h.d.querySelector('#import-link-back').onclick,cancel=h.d.oncancel,late=h.requests.at(-1),count=h.requests.length;
  if(change==='logout'){h.core.clearSession();h.d.close();}
  else if(change==='tenant'){h.core.setCompany({id:'other'});h.d.close();}
  else if(change==='permission')h.core.state.permissions['task.create_customer']=false;
  else h.core.dialog('新窗口','其他内容');
  const form=h.form;close();back();cancel({preventDefault(){}});await h.reply(late,{items:[{id:'late',name:'迟到客户'}],has_more:false});
  assert.equal(h.form,form);assert.equal(h.requests.length,count);assert.equal(h.d.querySelector('[data-field="description"]'),null);
  if(change==='replace'){assert.equal(h.d.open,true);assert.match(h.d.innerHTML,/新窗口/);}else assert.equal(h.d.open,false);
});
test('live-form intent permits child markup updates but rejects replacing the form node',async()=>{
  const h=await harness();h.core.dialog('动态表单','<div id="dynamic"></div>');const current=h.core.dialogIntent({liveForm:true});
  h.d.querySelector('#dynamic').innerHTML='<p>新加载目录</p>';assert.equal(current(),true);
  h.core.dialog('动态表单','<div id="dynamic"></div>');assert.equal(current(),false);
});
test('lost confirm receipt after replacing dialog never refreshes or closes the new editor',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit(),request=h.requests.at(-1),count=h.requests.length;
  h.core.dialog('新的编辑窗口','用户正在编辑');await h.fail(request);await saving;
  assert.match(h.d.innerHTML,/新的编辑窗口/);assert.equal(h.d.open,true);assert.equal(h.requests.length,count);
});

test('recipient pagination preserves accounts beyond the first page with explicit names and teams',async()=>{
  const h=await harness(),loading=h.imports.showTaskImport('batch');await h.reply(h.requests.at(-1),draft());
  await h.reply(h.requests.at(-1),{items:[{...person,id:'old',account_code:'OTHER'}],has_more:true,next_offset:100});assert.match(h.requests.at(-1).url,/offset=100$/);
  await h.reply(h.requests.at(-1),{items:[person],has_more:false});await loading;
  assert.equal(h.d.querySelector('[data-field="assignee_account_code"]').value,'PERSON');assert.match(h.d.innerHTML,/明确接收人 · PERSON · 北区/);
});
test('closing an existing dialog during recipient paging stops the next page and suppresses stale errors',async()=>{
  const h=await harness();h.core.dialog('当前窗口','查看中');const loading=h.imports.showTaskImport('batch');await h.reply(h.requests.at(-1),draft());const request=h.requests.at(-1),count=h.requests.length;h.d.close();
  await h.reply(request,{items:[person],has_more:true,next_offset:100});await loading;assert.equal(h.requests.length,count);assert.equal(h.d.open,false);
});
test('unresolved dispatch recovery never auto-repeats a write or claims success',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit();await h.fail(h.requests.at(-1));await h.fail(h.requests.at(-1));await saving;
  assert.equal(h.requests.filter(r=>r.url.endsWith('/confirm')).length,1);assert.match(h.d.querySelector('.form-error').textContent,/派发结果暂未确认/);assert.match(h.d.innerHTML,/最终核对/);
});
test('batch list links keep the existing inbox intact and display server completion counts only',async()=>{
  const h=await harness();h.core.state.filters.view='imports';h.core.state.offset=50;
  const loading=h.tasks.taskInbox();assert.match(h.requests.at(-1).url,/task-imports\?page_size=50&offset=50/);
  await h.reply(h.requests.at(-1),{items:[confirmed(),draft({id:'draft2',batch_name:'未派发草稿'})],has_more:true,next_offset:100});const page=await loading;
  assert.match(page.html,/已完成 0/);assert.match(page.html,/待验收 1/);assert.match(page.html,/未派发草稿/);assert.match(page.html,/data-import-id="batch"/);
  const root=tree();root.innerHTML=page.html;page.bind(root);root.querySelector('#task-import-inbox').onclick();assert.equal(h.core.state.filters.view,undefined);assert.equal(h.core.state.offset,0);
});
test('batch detail opens existing task actions rather than creating a new task workflow',async()=>{
  const h=await harness(),loading=h.imports.showTaskImport('batch');await h.reply(h.requests.at(-1),confirmed());await loading;
  const open=h.d.querySelector('[data-import-task="task1"]').onclick();await tick();assert.match(h.requests.at(-1).url,/\/tasks\/task1$/);
  await h.reply(h.requests.at(-1),{id:'task1',title:'既有任务详情',status:'pending_review',creator_user_ref_id:'me',assignees:[],action_permissions:{},events:[]});await open;
  assert.match(h.d.innerHTML,/待办详情/);assert.match(h.d.innerHTML,/既有任务详情/);assert.equal(h.requests.some(r=>r.options.method==='POST'),false);
});
test('urgent drafts remain editable, and final deadline display explicitly uses Beijing time',async()=>{
  const h=await harness(),urgent={...sourceRow,priority_code:'urgent',selected:true};await openDraft(h,draft({editable_items:[urgent]}));assert.equal(h.d.querySelector('[data-field="priority_code"]').value,'urgent');
  await review(h,draft({version_no:2,editable_items:[urgent]}));assert.match(h.d.innerHTML,/紧急优先级/);assert.match(h.d.innerHTML,/17:00:00（北京时间）/);
});

test('a newer draft recovered after confirmation failure must be reviewed and cannot replay stale fields',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit();await h.reply(h.requests.at(-1),{detail:'版本变化'},409);
  await h.reply(h.requests.at(-1),draft({version_no:3,editable_items:[{...sourceRow,description:'服务器的新草稿内容',selected:true}]}));await saving;
  assert.match(h.d.querySelector('.form-error').textContent,/已更新/);const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);
  h.d.querySelector('#import-review-back').onclick();assert.equal(h.d.querySelector('[data-field="description"]').value,'服务器的新草稿内容');
});
test('new duplicate warnings on recovery require a fresh explicit check rather than an old acknowledgement',async()=>{
  const h=await harness();await openDraft(h);await review(h);const saving=h.submit();await h.reply(h.requests.at(-1),{detail:'发现相似任务'},409);
  const changed={...sourceRow,selected:true,duplicates:[{task_id:'new-existing',description:'新发现的同类任务'}]};await h.reply(h.requests.at(-1),draft({version_no:2,editable_items:[changed]}));await saving;
  assert.match(h.d.querySelector('.form-error').textContent,/重复提醒已更新/);const count=h.requests.length;await h.submit();assert.equal(h.requests.length,count);
});

// Optional real-browser regressions; the normal dependency-free Node suite above
// remains available. Point SALESBUDDY_PLAYWRIGHT_MODULE at an installed Playwright.
async function browserDraft(check){
  const http=require('node:http'),fs=require('node:fs/promises');
  const {chromium}=require(process.env.SALESBUDDY_PLAYWRIGHT_MODULE||'playwright');
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/'){res.setHeader('Content-Type','text/html');res.end('<main id="content"><p>任务验收</p></main><dialog id="dialog"></dialog>');return;}
    if(!['/assets/core.js','/assets/task-imports.js'].includes(req.url)){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type','application/javascript');res.end(await fs.readFile(path.join(assets,path.basename(req.url))));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{
    browser=await chromium.launch({headless:true});const page=await browser.newPage(),requests=[];
    await page.route('**/api/**',async route=>{
      const request=route.request();requests.push({url:request.url(),method:request.method()});
      const payload=request.url().includes('/tasks/recipients')?{items:[person],has_more:false}:request.url().includes('/tasks/customers')?{items:[],has_more:false}:draft();
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(payload)});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.evaluate(async()=>{
      const core=await import('/assets/core.js');window.testCore=core;
      Object.assign(core.state,{actor:{workspace_id:'w',user_id:'me',role:'manager'},token:'fixture',company:{id:'w'},page:'tasks',permissions:{'task.read':true,'task.create_daily':true,'task.create_customer':true},refresh:async()=>{}});
      await (await import('/assets/task-imports.js')).showTaskImport('batch');
    });
    await check(page,requests);
  }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
}
const browserOptions={skip:process.env.SALESBUDDY_BROWSER_DOM_TESTS!=='1'};
test('real DOM: picker cancel and Escape return to the edited draft without writing',browserOptions,async()=>browserDraft(async(page,requests)=>{
  await page.locator('[data-field="description"]').fill('真实浏览器中尚未保存的文字');
  await page.locator('[data-field="association_kind"]').selectOption('customer');
  await page.locator('#import-bulk-date').fill('2026-10-06T18:30');
  for(const dismissal of ['close','cancel','escape']){
    await page.locator('[data-choose-links]').click();
    if(dismissal==='escape')await page.keyboard.press('Escape');
    else await page.locator('[data-close]').nth(dismissal==='cancel'?1:0).click();
    assert.equal(await page.locator('[data-field="description"]').inputValue(),'真实浏览器中尚未保存的文字');
    assert.equal(await page.locator('#import-bulk-date').inputValue(),'2026-10-06T18:30');
    assert.match(await page.locator('#dialog-title').textContent(),/核对导入/);
  }
  assert.equal(requests.some(r=>r.method!=='GET'),false);
}));
test('real DOM: returning draft retains discard confirmation and tenant/logout close never restores it',browserOptions,async()=>browserDraft(async(page,requests)=>{
  await page.locator('[data-field="description"]').fill('退出前必须明确保留或丢弃的修改');
  await page.locator('[data-field="association_kind"]').selectOption('customer');
  await page.locator('[data-choose-links]').click();await page.locator('#import-link-back').click();
  await page.locator('[data-close]').first().click();assert.equal(await page.locator('.discard-confirm').count(),1);
  assert.equal(await page.locator('#dialog').evaluate(d=>d.open),true);
  await page.locator('[data-keep]').click();assert.equal(await page.locator('.discard-confirm').count(),0);
  await page.keyboard.press('Escape');assert.equal(await page.locator('.discard-confirm').count(),1);
  await page.locator('[data-keep]').click();await page.locator('[data-choose-links]').click();
  await page.evaluate(()=>{const d=document.querySelector('#dialog'),oldClose=d.querySelector('[data-close]').onclick;window.testCore.setCompany({id:'other'});d.close();oldClose();window.testCore.clearSession();d.close();});
  assert.equal(await page.locator('#dialog').evaluate(d=>d.open),false);assert.equal(await page.locator('[data-field="description"]').count(),0);
  assert.equal(requests.some(r=>r.method!=='GET'),false);
}));
