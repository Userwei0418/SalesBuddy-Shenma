const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const tick=()=>new Promise(setImmediate);
const target={id:'target',scope_type:'person',user_ref_id:'self',user_name:'本人',account_code:'SELF',kind:'collection',period_type:'year',period_start:'2032-01-01',period_end:'2032-12-31',amount:'100',amount_text:'100.00',version_no:1,can_manage:true,editable:true,can_history:false};

async function harness(permissions={}){
 const core=await import(pathToFileURL(path.join(assets,'core.js')).href);
 const pages=await import(pathToFileURL(path.join(assets,'pages.js')).href);
 const targets=await import(pathToFileURL(path.join(assets,'targets.js')).href);
 const profile=await import(pathToFileURL(path.join(assets,'customer-profile.js')).href);
 core.clearSession();Object.assign(core.state,{permissions,org:null,actor:{workspace_id:'w',user_id:'self',role:'sales'},token:'fixture',page:'customers',filters:{},offset:0,refresh:async()=>{}});
 const calls=[],nodes={};const node=s=>nodes[s]||=( {value:'',innerHTML:'',hidden:false,disabled:false,style:{},isConnected:true,setAttribute(){}} );
 const form={values:[],isConnected:true,addEventListener(){},querySelector:node};
 const dialog={content:'',open:false,querySelectorAll:()=>[],addEventListener(){},
  get innerHTML(){return this.content;},set innerHTML(value){this.content=value;
   for(const match of value.matchAll(/<select name="([^"]+)"[^>]*>(.*?)<\/select>/gs)){
    const options=[...match[2].matchAll(/<option value="([^"]*)"([^>]*)>/g)];
    node('[name='+match[1]+']').value=(options.find(m=>m[2].includes('selected'))||options[0])?.[1]||'';
   }
   for(const match of value.matchAll(/<input[^>]*name="([^"]+)"[^>]*value="([^"]*)"/g))node('[name='+match[1]+']').value=match[2];
  },querySelector:s=>['form','#dialog-form'].includes(s)?form:node(s),showModal(){this.open=true;},close(){this.open=false;}};
 global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?form:node(s),dispatchEvent(){}};
 global.FormData=class{constructor(f){this.values=f.values;}get(k){return this.values.find(([key])=>key===k)?.[1]??null;}has(k){return this.values.some(([key])=>key===k);}[Symbol.iterator](){return this.values[Symbol.iterator]();}};
 const h={core,pages,targets,profile,calls,nodes,dialog,form,
  customerOptions:{teams:[{id:'t',name:'授权团队'}]},targetOptions:{people:[{id:'self',name:'本人',account_code:'SELF'}],teams:[],departments:[]},
  customerRows:[{id:'c',name:'客户',ownership_state:'claimed',can_update:true,can_release:false,can_resolve_owner:false}],claimRows:[{id:'claim',customer_id:'c',customer_name:'客户',status:'pending',can_read_customer:false,can_review:false}],targetRows:[{...target}],profileRow:{id:'c',name:'客户',version_no:1,can_update:true}};
 global.fetch=async(url,options={})=>{
  calls.push({url,options});let data;
  if(['POST','PATCH','PUT'].includes(options.method))data={id:'saved',changed:true};
  else if(url.includes('/customers/create-options'))data=h.customerOptions;
  else if(url.includes('/targets/options'))data=h.targetOptions;
  else if(url.includes('/targets'))data={items:h.targetRows,total:h.targetRows.length,period:{start:'2032-01-01',end:'2032-12-31'},editable:true};
  else if(url.includes('/customers/c/overview'))data=h.profileRow;
  else if(url.includes('/customers'))data={items:h.customerRows,total:h.customerRows.length};
  else if(url.includes('/claims'))data={items:h.claimRows,total:h.claimRows.length};
  else if(url.includes('/summary'))data={customers:1,unclaimed:0,pending_claims:0,legacy_review:0};
  else if(url.includes('/ai/rules'))data={items:[{id:'rule',name:'提醒',period:'month',enabled:true,calls_limit:1}],alerts:[]};
  else if(url.includes('/ai/overview'))data={summary:{calls:0,succeeded:0,failed:0,cancelled:0},trend:[],roles:[],legacy_logical_calls:0};
  else data={items:[],total:0};
  return {ok:true,status:200,json:async()=>data};
 };
 h.render=async(name)=>{
  core.state.page=name;
  const view=await (name==='targets'?targets.targets:name==='ai'?pages.aiUsage:pages[name])();
  const regions={};for(const match of view.html.matchAll(/data-region="([^"]+)"/g))regions[match[1]]={innerHTML:'',setAttribute(){}};
  const root={querySelector:s=>regions[s.match(/data-region="([^"]+)"/)?.[1]]||null};
  await view.bind(root);
  return{html:view.html+Object.values(regions).map(n=>n.innerHTML).join(''),click:(action,id='')=>root.onclick({target:{closest:()=>({dataset:{action,id}})}})};
 };
 return h;
}

test('customer readonly page shows neither mutation nor export entries, and stale create does not read directories',async()=>{
 const h=await harness({'customer.read':true});const page=await h.render('customers');
 assert.doesNotMatch(page.html,/data-action="(?:create|edit|release|resolve|export)"/);
 const count=h.calls.length;await assert.rejects(h.pages.customerForm(),/未获/);assert.equal(h.calls.length,count);
});

test('customer create without organization uses authorized IDs and retains name compatibility when submitting',async()=>{
 const h=await harness({'customer.create':true});await h.pages.customerForm();
 assert.match(h.dialog.innerHTML,/name="target_team_id"/);assert.match(h.dialog.innerHTML,/value="t"/);
 assert(!h.calls.some(r=>r.url.includes('organization')||r.url.includes('/directory/')));
 h.form.values=Object.entries({name:'新客户',target_team_id:'t',contact_name:'联系人',company_reference:'APPROVED'});
 await h.form.onsubmit({preventDefault(){},currentTarget:h.form});
 const data=JSON.parse(h.calls.find(r=>r.options.method==='POST').options.body);
 assert.equal(data.target_team_id,'t');assert.equal(data.target_team,'授权团队');
});

test('missing or empty customer choices give an actionable error; changed identity does not reopen a form',async()=>{
 const h=await harness({'customer.create':true});h.customerOptions={};
 await assert.rejects(h.pages.customerForm(),/选项未完整/);assert.equal(h.dialog.open,false);
 h.customerOptions={teams:[]};await assert.rejects(h.pages.customerForm(),/没有可建档/);
 let reply;global.fetch=()=>new Promise(resolve=>{reply=resolve;});
 const loading=h.pages.customerForm();h.core.state.page='targets';reply({ok:true,status:200,json:async()=>({teams:[{id:'t',name:'过期'}]})});
 await loading;assert.equal(h.dialog.open,false);
});

test('customer editing requires both the feature and the fresh row grant; other granted row controls remain visible',async()=>{
 const h=await harness({'customer.read':true,'customer.update':true,'customer.release':true});
 const page=await h.render('customers');assert.match(page.html,/data-action="edit"/);assert.doesNotMatch(page.html,/data-action="release"/);
 h.profileRow.can_update=false;await assert.rejects(h.profile.editCustomerProfile('c'),/修改资料的授权范围/);
 assert.equal(h.dialog.open,false);
});

test('claim controls respect each customer scope and recheck review permission before saving',async()=>{
 const h=await harness({'customer.read':true,'customer.claim_review':true});
 let page=await h.render('claims');assert.doesNotMatch(page.html,/data-action="(?:detail|review)"/);
 await page.click('review','claim');assert.equal(h.dialog.open,false);
 Object.assign(h.claimRows[0],{can_read_customer:true,can_review:true});page=await h.render('claims');
 assert.match(page.html,/data-action="detail"/);assert.match(page.html,/data-action="review"/);
 await page.click('review','claim');assert.equal(h.dialog.open,true);
 h.core.state.permissions['customer.claim_review']=false;
 h.form.values=Object.entries({decision:'approved',reason:'确认'});
 await h.form.onsubmit({preventDefault(){},currentTarget:h.form});assert(!h.calls.some(r=>r.options.method==='POST'));
});

test('target readonly page hides management/history; explicit management options work without org data',async()=>{
 const h=await harness({'target.read':true});let page=await h.render('targets');assert.doesNotMatch(page.html,/data-action="(?:create|edit|history)"/);
 await assert.rejects(h.targets.editTarget(),/未获/);
 h.core.state.permissions['target.manage']=true;page=await h.render('targets');assert.match(page.html,/data-action="create"/);assert.match(page.html,/data-action="edit"/);
 await h.targets.editTarget();assert.match(h.dialog.innerHTML,/value="self"/);assert(!h.calls.some(r=>r.url.includes('organization')));
 h.nodes['[name=user_id]'].value='self';h.nodes['[name=anchor_date]'].value='2032-03-01';
 await h.nodes['[name=user_id]'].onchange();
 h.form.values=Object.entries({mode:'collection',amount_collection:'200',reason:'确认'});
 await h.form.onsubmit({preventDefault(){},currentTarget:h.form});
 const saved=h.calls.find(r=>r.options.method==='POST');assert(saved);
 const body=JSON.parse(saved.options.body);assert.equal(body.scope,'person');assert.equal(body.user_id,'self');assert.equal(body.items[0].version_no,1);
});

test('target edit reuses fixed subject and refuses a row outside management scope',async()=>{
 const h=await harness({'target.read':true,'target.manage':true});h.core.state.page='targets';
 await h.targets.editTarget({...target});await tick();assert.match(h.dialog.innerHTML,/本人/);
 assert(!h.calls.some(r=>r.url.includes('/targets/options')||r.url.includes('/organization')));
 await assert.rejects(h.targets.editTarget({...target,can_manage:false}),/运营维护/);
 h.targetOptions={people:[],teams:[],departments:[]};await assert.rejects(h.targets.editTarget(),/没有可维护且可查看/);
});

for(const [page,permission] of [['audit','audit.export'],['systemLogs','audit.events_export'],['ai','ai.usage_export']])test(`${page} exports require ${permission}`,async()=>{
 const h=await harness({});let view=await h.render(page);assert.doesNotMatch(view.html,/data-action="export(?:-roles)?"/);
 const count=h.calls.length;await view.click('export');assert.equal(h.calls.length,count);
 h.core.state.permissions[permission]=true;view=await h.render(page);assert.match(view.html,/data-action="export"/);
});

test('AI reminder and configuration links follow separate permissions; a denied reminder never submits',async()=>{
 const h=await harness({});let view=await h.render('ai');assert.doesNotMatch(view.html,/data-action="(?:rule|edit-rule)"|href="#modelApis"/);
 const count=h.calls.length;await view.click('rule');assert.equal(h.calls.length,count);assert.equal(h.dialog.open,false);
 Object.assign(h.core.state.permissions,{'ai.usage_rules_manage':true,'ai.config_read':true});view=await h.render('ai');
 assert.match(view.html,/data-action="rule"/);assert.match(view.html,/data-action="edit-rule"/);assert.match(view.html,/href="#modelApis"/);
 await view.click('rule');h.core.state.permissions['ai.usage_rules_manage']=false;
 h.form.values=Object.entries({name:'受限提醒',period:'month',calls_limit:'1'});
 await h.form.onsubmit({preventDefault(){},currentTarget:h.form});assert(!h.calls.some(r=>r.options.method==='POST'));
});
