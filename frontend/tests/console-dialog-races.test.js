const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../../backend/src/sales_backend/web/assets');
const tick = () => new Promise(setImmediate);

async function harness() {
  const core = await import(pathToFileURL(path.join(assets, 'core.js')).href);
  const tasks = await import(pathToFileURL(path.join(assets, 'tasks.js')).href);
  core.clearSession();
  Object.assign(core.state, {actor:{workspace_id:'w',user_id:'admin',role:'administrator'},token:'fixture',page:'tasks',permissions:{'task.coordinate':true},refresh:async()=>{h.refreshes++;}});
  let form;
  const makeForm = () => {
    const nodes = {'button[type=submit]':{disabled:false,textContent:'保存'},'.form-error':{innerHTML:'',scrollIntoView(){}}};
    return {values:[],inert:false,addEventListener(){},querySelector:s=>nodes[s]};
  };
  const d = {open:false,_html:'',set innerHTML(value){this._html=value;form=makeForm();},get innerHTML(){return this._html;},
    querySelectorAll:()=>[],querySelector:()=>null,setAttribute(){},removeAttribute(){},showModal(){this.open=true;},close(){this.open=false;}};
  const content = {firstElementChild:{}};
  global.document = {querySelector:s=>({'#dialog':d,'#dialog-form':form,'#content':content})[s]||null,dispatchEvent(){}};
  global.FormData = class {constructor(f){this.values=f.values;}get(name){return this.values.find(([key])=>key===name)?.[1];}};
  const requests=[];
  global.fetch=(url,options)=>new Promise(resolve=>requests.push({url,options,resolve}));
  const h={core,tasks,d,content,requests,refreshes:0,get form(){return form;},
    async reply(request,data,status=200){request.resolve({ok:status<400,status,json:async()=>data});await tick();},
    submit(f=form){return f.onsubmit({preventDefault(){},currentTarget:f});}};
  return h;
}
const task = title => ({id:title,title,status:'pending_confirm',assignees:[],action_permissions:{},events:[]});

test('admin task detail honors the latest click even when the first response arrives last',async()=>{
  const h=await harness(),first=h.tasks.showTask('first'),second=h.tasks.showTask('second');
  await h.reply(h.requests[1],task('second'));await second;
  await h.reply(h.requests[0],task('first'));await first;
  assert.match(h.d.innerHTML,/<h3>second<\/h3>/);assert.doesNotMatch(h.d.innerHTML,/<h3>first/);
});
for(const change of ['navigation','replacement','close','new-page-render'])test(`late task coordination cannot replace dialog after ${change}`,async()=>{
  const h=await harness();h.core.dialog('原任务','详情');
  const pending=h.tasks.coordinateTask({id:'t',status:'in_progress',can_coordinate:true,assignees:[],action_permissions:{'task.coordinate':true}});
  if(change==='navigation')h.core.state.page='customers';
  if(change==='replacement')h.core.dialog('新的编辑窗口','新内容');
  if(change==='close')h.d.close();
  if(change==='new-page-render')h.content.firstElementChild={};
  const before=h.d.innerHTML;
  await h.reply(h.requests[0],{items:[],has_more:true,next_offset:100});await pending;
  assert.equal(h.requests.length,1);assert.equal(h.d.innerHTML,before);
});
test('old task detail errors are silent once a newer dialog owns the screen',async()=>{
  const h=await harness(),pending=h.tasks.showTask('old');
  h.core.dialog('新窗口','内容');await h.reply(h.requests[0],{detail:'旧请求失败'},500);await pending;
  assert.match(h.d.innerHTML,/新窗口/);
});
for(const change of ['session','identity','company','page','replacement','closed'])test(`stale admin forms cannot submit after ${change}`,async()=>{
  const h=await harness();let writes=0;h.core.dialog('修改资料','内容',{onSubmit:async()=>{writes++;}});const old=h.form;
  if(change==='session')h.core.clearSession();
  if(change==='identity')h.core.state.actor={...h.core.state.actor,user_id:'other'};
  if(change==='company')h.core.setCompany({id:'other'});
  if(change==='page')h.core.state.page='customers';
  if(change==='replacement')h.core.dialog('新窗口','内容');
  if(change==='closed')h.d.close();
  await h.submit(old);assert.equal(writes,0);assert.equal(h.refreshes,0);
});
test('admin saving locks the form and ignores duplicate submits without disabling future retry',async()=>{
  const h=await harness();let finish,writes=0;
  h.core.dialog('修改资料','内容',{onSubmit:()=>{writes++;return new Promise((resolve,reject)=>{finish={resolve,reject};});}});
  const f=h.form,first=h.submit();await h.submit();assert.equal(writes,1);assert.equal(f.inert,true);
  finish.reject(Error('网络不稳定'));await first;
  assert.equal(f.inert,false);assert.equal(f.querySelector('button[type=submit]').disabled,false);assert.match(f.querySelector('.form-error').innerHTML,/网络不稳定/);
  const retry=h.submit();finish.resolve();await retry;assert.equal(writes,2);assert.equal(h.refreshes,1);assert.equal(h.d.open,false);
});
test('late successful form receipt does not close or refresh a replacement dialog',async()=>{
  const h=await harness();let finish;
  h.core.dialog('旧编辑','内容',{onSubmit:()=>new Promise(resolve=>{finish=resolve;})});const pending=h.submit();
  h.core.dialog('新编辑','仍在编辑',{onSubmit:async()=>{}});finish();await pending;
  assert.equal(h.d.open,true);assert.match(h.d.innerHTML,/新编辑/);assert.equal(h.refreshes,0);assert.equal(h.form.inert,false);
});
test('task audit displays recorded actor name without changing the current owner',async()=>{
  const h=await harness(),pending=h.tasks.showTask('t');
  await h.reply(h.requests[0],{...task('t'),assignees:[{responsibility:'owner',name:'现负责人'}],events:[{actor_name:'现姓名',payload:{actor_name:'操作时姓名'},note:'转交'}]});await pending;
  assert.match(h.d.innerHTML,/负责人：现负责人/);assert.match(h.d.innerHTML,/操作时姓名/);assert.doesNotMatch(h.d.innerHTML,/现姓名/);
});

for(const failure of [false,true])test(`account permission dialog drops earlier ${failure?'failure':'success'} after another account is selected`,async()=>{
  const h=await harness();h.core.state.page='permissions';
  const page=await import(pathToFileURL(path.join(assets,'permissions.js')).href);
  const loading=page.permissionsPage();
  const fixtures={catalog:{permissions:[]},roles:{roles:[]},options:{teams:[],accounts:[{id:'A',name:'甲',account_code:'A'},{id:'B',name:'乙',account_code:'B'}]},
    me:{permissions:{'authorization.accounts_manage':true}},modules:{modules:[]},delegations:{company_administrator:true,user_id:'admin',items:[]}};
  for(const req of [...h.requests])await h.reply(req,fixtures[req.url.split('/').at(-1)]);
  const view=await loading;
  const buttons=['A','B'].map(id=>({dataset:{permissionAction:'account',id},disabled:false}));
  view.bind({querySelector:()=>({}),querySelectorAll:()=>buttons});
  const earlier=buttons[0].onclick(),later=buttons[1].onclick();
  await h.reply(h.requests.at(-1),{roles:[],overrides:[],permissions:{},grants:[]});await later;
  assert.match(h.d.innerHTML,/账号权限 · 乙/);
  await h.reply(h.requests.at(-2),failure?{detail:'旧读取失败'}:{roles:[],overrides:[],permissions:{},grants:[]},failure?500:200);await earlier;
  assert.match(h.d.innerHTML,/账号权限 · 乙/);assert.doesNotMatch(h.d.innerHTML,/甲|旧读取失败/);
});

for(const endpoint of ['/customers/create-options','/customers/customer-a','/opportunities/opportunity-a','/targets/options','/targets/target-a/history','/activities/activity-a','/company-data/options','/model-api-configs/text/releases']) {
  for(const failure of [false,true])test(`${endpoint} discards late dialog ${failure?'failure':'data'}`,async()=>{
    const h=await harness(),pending=h.core.readForDialog(endpoint);
    h.core.dialog('新窗口','正在处理别的对象');
    await h.reply(h.requests[0],failure?{detail:'旧请求失败'}:{id:'old'},failure?500:200);
    assert.equal(await pending,null);assert.match(h.d.innerHTML,/新窗口/);
  });
}
test('current dialog data reads still return data and actionable errors',async()=>{
  const h=await harness(),success=h.core.readForDialog('/targets/options');
  await h.reply(h.requests[0],{people:[],teams:[],departments:[]});assert.deepEqual(await success,{people:[],teams:[],departments:[]});
  const failed=h.core.readForDialog('/targets/options'),rejected=assert.rejects(failed,/目录加载失败/);
  await h.reply(h.requests[1],{detail:'目录加载失败'},500);await rejected;
});
for(const kind of ['customer','target','opportunity'])test(`slow ${kind} creation options do not replace another editor`,async()=>{
  const h=await harness();Object.assign(h.core.state.permissions,{'customer.create':true,'target.manage':true,'opportunity.create':true,'customer.read':true});
  const module=await import(pathToFileURL(path.join(assets,kind==='target'?'targets.js':'pages.js')).href);
  const pending=kind==='customer'?module.customerForm():kind==='target'?module.editTarget():module.opportunityForm();
  h.core.dialog('已选中的编辑窗口','当前对象');
  for(const req of [...h.requests])await h.reply(req,{people:[],teams:[{id:'t',name:'团队'}],departments:[]});await pending;
  assert.match(h.d.innerHTML,/已选中的编辑窗口/);
});
