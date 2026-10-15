// Run original released code. Only the wx transport/storage boundary is adapted
// to real loopback HTTP; no success response or business API is mocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const base = new URL(input.base_url);
assert.equal(base.hostname, '127.0.0.1');
assert.equal(base.protocol, 'http:');
assert.equal(base.pathname, '/api/v1');
assert.notEqual(base.port, '18800');
const store = new Map([['salesApiBaseUrl', input.base_url]]);
const requests = [];
const checks = [];
global.wx = {
  getStorageSync: key => store.get(key),
  setStorageSync: (key, value) => store.set(key, value),
  removeStorageSync: key => store.delete(key),
  showModal(options) { options.success?.({confirm:true,cancel:false}); },
  getRandomValues: ({length}) => {
    const bytes=require('node:crypto').randomBytes(length);
    return {randomValues:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength)};
  },
  request(options) {
    const url = new URL(options.url);
    assert.equal(url.origin, base.origin, 'Only the disposable loopback API may receive requests');
    assert.ok(url.pathname.startsWith('/api/v1/'));
    const method = options.method || 'GET';
    let body;
    if (options.data && ['GET','HEAD'].includes(method)) {
      for (const [key,value] of Object.entries(options.data)) url.searchParams.set(key,value);
    } else if (options.data !== undefined) body = JSON.stringify(options.data);
    fetch(url, {method,headers:options.header,body,signal:AbortSignal.timeout(options.timeout || 15000)})
      .then(async response => {
        const raw = await response.text();
        let data; try {data = raw ? JSON.parse(raw) : null;} catch {data = raw;}
        requests.push({method,path:url.pathname,status:response.status});
        options.success({statusCode:response.status,data});
      }).catch(() => options.fail({errMsg:'Disposable loopback request failed'}));
    return {abort(){throw new Error('Transport abort not under test');}};
  },
  uploadFile(){throw new Error('Recording and upload require separate device/integration acceptance');},
};
const api = require(input.client);
const own = input.companies[0], other = input.companies[1];
const page = value => {assert.ok(value && Array.isArray(value.items)); return value;};
const object = value => {assert.ok(value && typeof value === 'object' && !Array.isArray(value));return value;};
async function run(name, fn) {
  try {await fn();checks.push({name,passed:true});}
  catch(error) {checks.push({name,passed:false,error:String(error.message),code:error.code});throw error;}
}
async function denied(fn, statuses) {
  await assert.rejects(fn, error => statuses.includes(error.statusCode));
}
const taskInput = (account,description,links={}) => ({description,assigneeAccount:account,
  dueAt:new Date(Date.now()+3*86400000).toISOString(),priority:'普通',associationKind:'daily',...links});

(async () => {
  await run('old password login without company code', async()=>{
    const auth = await api.loginWithAccount('ASALES',input.password);
    assert.equal(auth.actor.workspace_id,own.id);
    assert.equal(auth.actor.role,'sales');
    assert.equal(typeof auth.actor.capabilities['opportunity.edit'],'boolean');
  });
  await run('automatic 401 refresh keeps company and identity', async()=>{
    const previous=api.getAuth();
    api.saveAuth({...previous,access_token:'expired-test-token'});
    assert.equal((await api.getCurrentActor()).user_id,previous.actor.user_id);
    assert.notEqual(api.getAuth().access_token,'expired-test-token');
    assert.ok(requests.some(r=>r.path==='/api/v1/auth/refresh'&&r.status===200));
  });
  const reads = [
    ['business catalog',()=>api.getBusinessOptions(),v=>assert.equal(v.contract_version,1)],
    ['home',()=>api.getAssistantHome(),object],
    ['workbench',()=>api.getWorkbench(),object],
    ['teams',()=>api.getTeamDirectory(),object],
    ['members',()=>api.getDirectoryMembers(),page],
    ['claim options',()=>api.listCustomerClaimOptions(),object],
    ['claim search',()=>api.listCustomerClaimPool({q:'hecheng'}),page],
    ['own customers',()=>api.listCustomers(),v=>{page(v);assert.ok(v.items.some(x=>x.id===own.customer_id));assert.ok(!v.items.some(x=>x.id===other.customer_id));}],
    ['customer header',()=>api.getCustomerHeader(own.customer_id),v=>assert.equal(v.id,own.customer_id)],
    ['customer overview',()=>api.getCustomerOverview(own.customer_id),object],
    ['contacts',()=>api.listCustomerContacts(own.customer_id),page],
    ['customer map',()=>api.getCustomerMap(),v=>{object(v);assert.equal(v.activity_window,'year');assert.match(v.activity_since,/-01-01$/);}],
    ['opportunity create options',()=>api.getOpportunityCreateOptions(),object],
    ['opportunity list',()=>api.listOpportunities(),page],
    ['dashboard options',()=>api.getDashboardOptions(),object],
    ['dashboard',()=>api.getDashboard(true),v=>{assert.equal(v.data_source,'database');assert.ok(Array.isArray(v.opportunities));}],
    ['rankings',()=>api.getDashboardRankings({year:2026,quarters:[1,2,3,4],personal:true}),object],
    ['profile scopes',()=>api.getProfileScopeOptions(),object],
    ['performance',()=>api.getProfilePerformance({scope:'self',year:2026,quarter:3}),object],
    ['followup history',()=>api.listVisits({customer_id:own.customer_id,sort:'created_desc'}),v=>{page(v);assert.equal(v.sort,'created_desc');}],
    ['task recipients',()=>api.listTaskRecipients(),page],
    ['task customers',()=>api.listTaskCustomers(),page],
    ['task page',()=>api.listTaskPage(),page],
    ['notifications',()=>api.listNotifications(),page],
  ];
  for(const [name,call,verify] of reads) await run(name,async()=>verify(await call()));
  if(input.check_new_windows) {
    for(const window of ['today','3d','week','month']) {
      await run('1.1.2 explicit map window '+window,async()=>{
        const value=await api.getCustomerMap({activity_window:window});
        assert.equal(value.activity_window,window);
        assert.ok(value.activity_start&&value.activity_end);
      });
    }
  }
  await run('other company customer denied',()=>denied(()=>api.getCustomerHeader(other.customer_id),[404]));
  const opportunityBody={name:'旧版客户端兼容合成商机',amount:1000,probability:10,
    expected_close_date:'2027-01-01',sales_channel:'direct'};
  let opportunity;
  await run('create and read opportunity',async()=>{
    opportunity=await api.createOpportunity(own.customer_id,opportunityBody);
    assert.ok(opportunity.id&&opportunity.event_id&&opportunity.changed);
    assert.equal(typeof opportunity.version_no,'number');
    const header=await api.getOpportunityDetailHeader(opportunity.id);
    assert.equal(header.id,own.customer_id);
    assert.equal(header.primary_opportunity.id,opportunity.id);
    const list=page(await api.listCustomerOpportunities(own.customer_id));
    assert.ok(list.items.some(x=>x.id===opportunity.id));
  });
  await run('cross company opportunity mutation denied',()=>denied(
    ()=>api.createOpportunity(other.customer_id,{...opportunityBody,name:'不得写入乙公司'}),[404]));
  if(input.probe_historical_large_amount_page) await run('old large-amount page keeps unsaved form and shows upgrade instructions without retry',async()=>{
    const file=path.resolve(path.dirname(input.client),'../pages/opportunity-create/index.js');
    const form={...opportunityBody,name:'旧版大额拒绝样例',amount:10000000};
    let definition;
    vm.runInNewContext(fs.readFileSync(file,'utf8'),{
      require:createRequire(file),Page:value=>definition=value,wx:global.wx,
      getApp:()=>({globalData:{session:{userId:api.getAuth().actor.user_id,workspaceId:own.id,role:'sales'}}}),
      setTimeout:()=>{throw new Error('Rejected save must not schedule navigation');},clearTimeout(){},
    });
    const instance={...definition,data:{...definition.data,customerId:own.customer_id},
      setData(patch){Object.assign(this.data,patch);},selectComponent(){return{prepare:async()=>form};}};
    const original=JSON.stringify(form),requestCount=requests.length;
    await instance.submit();
    assert.equal(JSON.stringify(form),original);
    assert.equal(instance.data.existing,null);assert.equal(instance.data.busy,false);
    assert.match(instance.data.error,/10,000,000.00 元/);
    assert.match(instance.data.error,/先复制保留当前填写内容/);
    assert.match(instance.data.error,/新版体验版入口/);
    assert.match(instance.data.error,/有权限的管理员/);
    assert.equal(requests.length-requestCount,1);
    assert.equal(requests.at(-1).status,422);
    const list=page(await api.listCustomerOpportunities(own.customer_id));
    assert.equal(list.items.length,1);assert.equal(list.items[0].id,opportunity.id);
  });
  await run('old large-amount edit is atomic and ordinary edit remains available',async()=>{
    const update={...opportunityBody,action:'update',opportunity_id:opportunity.id,
      version_no:opportunity.version_no,name:'旧版安全编辑',amount:10000000};
    await denied(()=>api.createOpportunity(own.customer_id,update),[422]);
    const rejected=(await api.getOpportunityDetailOverview(opportunity.id)).primary_opportunity;
    assert.equal(Number(rejected.amount),1000);assert.equal(rejected.version_no,opportunity.version_no);
    const saved=await api.createOpportunity(own.customer_id,{...update,amount:1000});
    assert.equal(saved.changed,true);assert.equal(Number(saved.amount),1000);
    opportunity=saved;
  });
  let daily;
  await run('self daily task independent completion',async()=>{
    daily=await api.createTask({...taskInput(null,'旧版自建日常待办'),targetPosition:'self'});
    const accepted=await api.respondTask(daily.id,'accept',null,daily.version_no);
    const done=await api.completeTask(daily.id,'合成验收完成',accepted.version_no);
    assert.equal(done.status,'completed');
    assert.equal((await api.getTask(daily.id)).status,'completed');
  });
  let batch;
  await run('batch creates independent customer tasks for each recipient',async()=>{
    const body=taskInput('ASALES','旧版批量客户',{associationKind:'customer',customerId:own.customer_id,opportunityId:opportunity.id});
    batch=page(await api.createTasks([body,{...body,assigneeAccount:'AMANAGER'}]));
    assert.equal(batch.items.length,2);assert.notEqual(batch.items[0].id,batch.items[1].id);
  });
  await run('unreviewed visit is still rejected',()=>denied(
    ()=>api.createVisit(own.customer_id,{summary:'未质检的合成正文'}),[403,409,422]));
  await api.logout();
  await run('second company sees only its own data',async()=>{
    await api.loginWithAccount('BSALES',input.password);
    const list=page(await api.listCustomers());
    assert.ok(list.items.some(x=>x.id===other.customer_id));
    assert.ok(!list.items.some(x=>x.id===own.customer_id));
    await denied(()=>api.getOpportunityDetailHeader(opportunity.id),[404]);
    await denied(()=>api.getTask(daily.id),[404]);
  });
  await api.logout();
  await run('manager receives assigned task',async()=>{
    await api.loginWithAccount('AMANAGER',input.password);
    const task=await api.getTask(batch.items[1].id);
    assert.equal(task.association_kind,'customer');
    const accepted=await api.respondTask(task.id,'accept',null,task.version_no);
    const submitted=await api.completeTask(task.id,'经理已处理',accepted.version_no);
    assert.equal(submitted.status,'pending_review');
  });
  await api.logout();
  await run('creator approves completion independently',async()=>{
    await api.loginWithAccount('ASALES',input.password);
    const submitted=await api.getTask(batch.items[1].id);
    const done=await api.respondTask(submitted.id,'approve_completion','验收通过',submitted.version_no);
    assert.equal(done.status,'completed');
    assert.notEqual((await api.getTask(batch.items[0].id)).status,'completed');
  });
  await api.logout();
  await run('FDE retains role and cannot create sales opportunity by default',async()=>{
    const auth=await api.loginWithAccount('AFDE',input.password);
    assert.equal(auth.actor.role,'fde');
    object(await api.getFdeDashboard());object(await api.getFdeProfile());
    await denied(()=>api.createOpportunity(own.customer_id,opportunityBody),[403,404]);
  });
  await api.logout();
  await run('ambiguous old login requires company instead of choosing one',()=>denied(
    ()=>api.loginWithAccount('SHARED',input.password),[401]));
  process.stdout.write(JSON.stringify({status:'passed',checks,requests}));
})().catch(()=>{
  // Do not dump auth, request payloads or raw responses, even on test failure.
  process.stdout.write(JSON.stringify({status:'failed',checks,requests}));process.exitCode=1;
});
