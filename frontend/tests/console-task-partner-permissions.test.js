const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assets=path.resolve(__dirname,'../../backend/src/sales_backend/web/assets');
const permissions=['task.accept','task.decline','task.complete','task.review','task.cancel','task.coordinate'];
const pending={id:'t',title:'测试任务',status:'pending_confirm',requires_action:true,can_coordinate:true,creator_user_ref_id:'self',version_no:2,
  assignees:[{user_id:'self',responsibility:'owner',name:'本人'}],action_permissions:Object.fromEntries(permissions.map(p=>[p,true]))};
async function harness(overrides={},task={...pending}){
  const core=await import(pathToFileURL(path.join(assets,'core.js')).href);
  const tasks=await import(pathToFileURL(path.join(assets,'tasks.js')).href);
  const partners=await import(pathToFileURL(path.join(assets,'partners.js')).href);
  core.clearSession();Object.assign(core.state,{actor:{user_id:'self',role:'sales',workspace_id:'w'},permissions:{...pending.action_permissions,...overrides},token:'fixture',filters:{},offset:0,org:null,refresh:async()=>{}});
  const nodes={},input=s=>nodes[s]||=( {value:'',innerHTML:'',disabled:false,scrollIntoView(){}} );
  const form={values:[],addEventListener(){},querySelector:s=>input(s)};
  const dialog={innerHTML:'',open:false,removeAttribute(){},setAttribute(){},querySelectorAll:()=>[],querySelector(s){
    if(s==='#coordinate-task'&&!this.innerHTML.includes('id="coordinate-task"'))return null;
    return input(s);
  },showModal(){this.open=true;},close(){this.open=false;}};
  global.document={querySelector:s=>s==='#dialog'?dialog:s==='#dialog-form'?form:input(s),dispatchEvent(){}};
  global.FormData=class{constructor(f){this.values=f.values;}get(n){return this.values.find(([k])=>k===n)?.[1]??null;}};
  const calls=[];
  global.fetch=async(url,options={})=>{calls.push({url,options});const result=url.includes('/reassignment-options')?{items:[{name:'同事',account_code:'PEER',team:'本组'}],has_more:false}:url.includes('/partners')?{items:[{id:'p',name:'伙伴',status:'active'}],total:1}:task;
    return{ok:true,status:200,json:async()=>result};};
  const submit=async values=>{form.values=Object.entries(values);await form.onsubmit({preventDefault(){},currentTarget:form});};
  return{core,tasks,partners,dialog,form,nodes,calls,submit};
}

test('task permissions require both effective feature and per-object authorization, never display role',async()=>{
  const h=await harness({'task.accept':false});
  assert.equal(h.tasks.taskActions(pending).accept,false);
  assert.equal(h.tasks.taskActions(pending).reject,true);
  h.core.state.actor.role='administrator';assert.equal(h.tasks.taskActions(pending).accept,false);
  h.core.state.permissions['task.accept']=true;
  assert.equal(h.tasks.taskActions({...pending,action_permissions:{}}).accept,false);
  assert.equal(h.tasks.taskActions({...pending,requires_action:false}).accept,false);
});

test('only permitted pending response is offered, and disabled action cannot be submitted',async()=>{
  const h=await harness({'task.accept':false});await h.tasks.showTask('t');
  assert.doesNotMatch(h.dialog.innerHTML,/value="accept"/);assert.match(h.dialog.innerHTML,/value="reject" selected/);
  await h.submit({event_type:'accept',note:'test'});
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
  assert.match(h.nodes['.form-error'].innerHTML,/未获/);
  await h.submit({event_type:'reject',note:'已有其他安排'});
  const write=h.calls.find(c=>c.options.method==='POST');assert.equal(JSON.parse(write.options.body).event_type,'reject');
});

test('completion and review require current state, identity and object permission',async()=>{
  const h=await harness();
  const active={...pending,status:'in_progress'};
  assert.equal(h.tasks.taskActions(active).complete,true);
  assert.equal(h.tasks.taskActions({...active,handover_required:true}).complete,false);
  assert.equal(h.tasks.taskActions({...active,assignees:[{responsibility:'owner',user_id:'other'}]}).complete,false);
  assert.equal(h.tasks.taskActions({...pending,status:'pending_review'}).approve_completion,true);
  h.core.state.permissions['task.review']=false;
  assert.equal(h.tasks.taskActions({...pending,status:'pending_review'}).approve_completion,false);
  for(const status of ['completed','cancelled','pending_review'])assert.equal(h.tasks.taskActions({...pending,status}).cancel,false);
});

test('cancel-only coordination never reads recipients and cannot reassign through a stale form',async()=>{
  const h=await harness({'task.coordinate':false});await h.tasks.coordinateTask(pending);
  assert.equal(h.calls.length,0);assert.doesNotMatch(h.dialog.innerHTML,/name="account"|value="reassign"/);
  await h.submit({event_type:'reassign',account:'PEER',note:'test'});assert.equal(h.calls.length,0);
  await h.submit({event_type:'cancel',note:'验收取消'});
  assert.equal(JSON.parse(h.calls[0].options.body).event_type,'cancel');
});

test('reassign-only coordination fetches recipients and sends current version',async()=>{
  const h=await harness({'task.cancel':false});await h.tasks.coordinateTask(pending);
  assert.match(h.calls[0].url,/\/tasks\/t\/reassignment-options\?/);
  assert.equal(h.calls.some(c=>c.url.includes('/tasks/recipients')),false);
  assert.doesNotMatch(h.dialog.innerHTML,/value="cancel"/);assert.match(h.dialog.innerHTML,/value="PEER"/);
  await h.submit({event_type:'reassign',account:'PEER',note:'验收交接'});
  const sent=JSON.parse(h.calls.find(c=>c.options.method==='POST').options.body);
  assert.deepEqual(sent,{event_type:'reassign',assignee_account_code:'PEER',note:'验收交接',version_no:2});
});

test('revoked permissions stop an already open partner form, and read-only directory has no mutation controls',async()=>{
  const h=await harness({'partner.manage':false});
  const page=await h.partners.partners();assert.doesNotMatch(page.html,/data-action="(?:create|edit)"/);
  assert.throws(()=>h.partners.editPartner(),/未获/);
  h.core.state.permissions['partner.manage']=true;h.partners.editPartner({id:'p',version_no:3});
  h.core.state.permissions['partner.manage']=false;
  await h.submit({name:'改名',status:'active'});
  assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0);
  assert.match(h.nodes['.form-error'].innerHTML,/未获/);
});
