require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {adviceState,adviceAllowed}=require('../miniprogram/utils/businessAdviceState');
const access=require('../miniprogram/utils/access');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise(r=>setImmediate(r));
const result=(kind='visit',id='v',section='overview',extra={})=>({id:'analysis',subject_kind:kind,subject_id:id,customer_id:kind==='customer'?id:'c',section,status:'succeeded',summary:'server advice',suggestions:[],...extra});
function apiHarness(value,status=200){
  const file=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}},calls=[],storage=new Map();
  const wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:options=>{calls.push(options);options.success({statusCode:status,data:value});}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{module,wx,Map,Set,Date,require:n=>require(path.resolve(path.dirname(file),n))});
  return {api:module.exports,calls};
}
test('latest GET carries exact kind/id/section; repeated refresh stays a GET',async()=>{
  const h=apiHarness({state:'succeeded',analysis:result()});
  for(let i=0;i<2;i++)assert.equal((await h.api.getLatestBusinessAdvice('visit','v','overview')).id,'analysis');
  assert.equal(h.calls.length,2);for(const call of h.calls){assert.equal(call.method,'GET');const u=new URL(call.url);assert.equal(u.pathname,'/api/v1/advice/latest');assert.equal(u.searchParams.get('subject_kind'),'visit');assert.equal(u.searchParams.get('subject_id'),'v');assert.equal(u.searchParams.get('section'),'overview');}
});
test('only explicit none/null means no prior advice',async()=>{
  assert.equal(await apiHarness({state:'none',analysis:null}).api.getLatestBusinessAdvice('visit','v'),null);
  for(const value of [null,{},[],{state:'none'},{state:'none',analysis:result()},{state:'succeeded',analysis:null},{state:'queued',analysis:result()},{state:'succeeded',analysis:result('visit','other')},{state:'succeeded',analysis:result('visit','v','tasks')}]){
    await assert.rejects(apiHarness(value).api.getLatestBusinessAdvice('visit','v'),e=>e.code==='ADVICE_CONTEXT_MISMATCH');
  }
});
for(const status of [403,404,422,500])test(`latest HTTP ${status} remains error, never empty`,async()=>{
  await assert.rejects(apiHarness({detail:'read failed'},status).api.getLatestBusinessAdvice('visit','v'),e=>e.statusCode===status);
});
test('request returns queued ID immediately; never starts a polling loop',async()=>{
  const h=apiHarness(result('visit','v','tasks',{status:'queued'}));const r=await h.api.requestBusinessAdvice('visit','v','tasks',true);
  assert.equal(r.id,'analysis');assert.equal(r.status,'queued');assert.equal(h.calls.length,1);assert.equal(h.calls[0].method,'POST');
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0].data)),{subject_kind:'visit',subject_id:'v',section:'tasks',retry:true});
});
test('advice presenter rejects missing/mismatched records and invalid completed data',()=>{
  assert.throws(()=>adviceState(null,'visit','v','overview'));
  assert.equal(adviceState(null,'visit','v','overview',{allowEmpty:true}).status,'idle');
  for(const patch of [{subject_kind:'customer'},{subject_id:'other'},{section:'tasks'},{status:'unknown'},{summary:null}])assert.throws(()=>adviceState(result('visit','v','overview',patch),'visit','v','overview'));
  assert.equal(adviceState(result('visit','v','overview',{status:'superseded'}),'visit','v','overview').status,'stale');
  assert.equal(adviceState(result('visit','v','overview',{stale:true}),'visit','v','overview').status,'stale');
});
test('read and generate permissions stay separate, explicit denies always win',()=>{
  const session={permissions:{'advice.read':true,'advice.customer':true,'advice.request':false}};
  assert.equal(adviceAllowed(session,'customer'),true);assert.equal(adviceAllowed(session,'customer',true),false);
  assert.equal(access.flags(session).canReadCustomerAdvice,true);assert.equal(access.flags(session).canCustomerAdvice,false);
  session.permissions['advice.request']=true;assert.equal(adviceAllowed(session,'customer',true),true);
  session.permissionGrants=[{permission_code:'advice.customer',effect:'deny'}];assert.equal(adviceAllowed(session,'customer'),false);
});
function pageHarness(name,api={}){
  let page;const app={ensureLogin:()=>true,globalData:{session:{userId:'u',workspaceId:'w',role:'sales',permissionVersion:1,permissions:{'advice.read':true,'advice.request':true,'advice.visit':true,'advice.customer':true}}}};
  const file=path.resolve(__dirname,`../miniprogram/pages/${name}/index.js`),calls=[];
  const client={getLatestBusinessAdvice:async()=>null,requestBusinessAdvice:async(kind,id,section)=>result(kind,id,section,{status:'queued'}),...api};
  for(const method of ['getLatestBusinessAdvice','requestBusinessAdvice']){const original=client[method];client[method]=(...args)=>{calls.push([method.startsWith('get')?'GET':'POST',...args]);return original(...args);};}
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>page=p,getApp:()=>app,require:n=>n.endsWith('apiClient')?client:require(path.resolve(path.dirname(file),n)),wx:{showTabBar(){},hideLoading(){},getStorageSync(){},setStorageSync(){}},setTimeout,clearTimeout,clearInterval});
  page.data=structuredClone(page.data);page.setData=(v,cb)=>{Object.assign(page.data,v);if(cb)cb();};
  if(name==='customers'){page._adviceGeneration=1;page._adviceCustomer={id:'c',can_read_advice:true,can_request_advice:true};page.data.selectedCustomer={id:'c'};}
  else{page.data.visitId='v';page.data.visit={id:'v'};page.data.archived=true;}
  return {page,app,calls,client};
}
for(const name of ['customers','visit-detail'])test(`${name}: entry and changed are reads, explicit generate once, pending refresh stays read`,async()=>{
  const h=pageHarness(name),customer=name==='customers',read=customer?'loadCustomerAdvice':'loadVisitAdvice',write=customer?'requestCustomerAdvice':'requestVisitAdvice';
  const action={currentTarget:{dataset:{adviceAction:'generate',customerId:'c',visitId:'v',section:'overview'}}};
  await h.page[read]();await h.page[read](action);assert.deepEqual(h.calls.map(c=>c[0]),['GET','GET']);
  await h.page[write]({currentTarget:{dataset:{}}});assert.equal(h.calls.length,2);
  await h.page[write](action);assert.equal(h.calls.at(-1)[0],'POST');
  await h.page[write](action);assert.equal(h.calls.filter(c=>c[0]==='POST').length,1);assert.equal(h.calls.at(-1)[0],'GET');
});
test('customer ready advice only rechecks freshness, explains unchanged result, and stale advice permits an explicit update',async()=>{
  let stale=false;
  const h=pageHarness('customers',{getLatestBusinessAdvice:async()=>result('customer','c','overview',stale?{status:'superseded'}:{})});
  const action={currentTarget:{dataset:{adviceAction:'generate',customerId:'c',section:'overview'}}};
  await h.page.loadCustomerAdvice();assert.equal(h.page.data.detailAdvice.overview.status,'ready');
  await h.page.loadCustomerAdvice({type:'tap'});
  assert.match(h.page.data.detailAdvice.overview.feedback,/资料未变化，沿用当前建议/);
  await h.page.requestCustomerAdvice(action);assert.deepEqual(h.calls.map(call=>call[0]),['GET','GET','GET']);
  stale=true;await h.page.loadCustomerAdvice({type:'tap'});assert.equal(h.page.data.detailAdvice.overview.status,'stale');
  await h.page.requestCustomerAdvice(action);assert.equal(h.calls.at(-1)[0],'POST');
  assert.equal(h.page.data.detailAdvice.overview.status,'queued');
  assert.match(h.page.data.detailAdvice.overview.feedback,/后台分析/);
  const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customers/index.wxml'),'utf8');
  assert.doesNotMatch(wxml,/重新生成建议/);
  assert.match(wxml,/status === 'idle' \|\| detailAdvice\[detailTab\]\.status === 'stale' \|\| detailAdvice\[detailTab\]\.status === 'error'/);
});
for(const name of ['customers','visit-detail'])for(const dimension of ['userId','workspaceId','role','permissionVersion'])test(`${name}: late ${dimension} response cannot populate another context`,async()=>{
  const d=deferred(),h=pageHarness(name,{getLatestBusinessAdvice:()=>d.promise}),customer=name==='customers';
  const promise=h.page[customer?'loadCustomerAdvice':'loadVisitAdvice']();h.app.globalData.session[dimension]='changed';
  d.resolve(result(customer?'customer':'visit',customer?'c':'v'));await promise;
  assert.notEqual((customer?h.page.data.detailAdvice.overview:h.page.data.visitAdvice).summary,'server advice');
});
test('visit overview and archived tasks can both recover after a cold reopen, section switch is GET only',async()=>{
  const h=pageHarness('visit-detail',{getLatestBusinessAdvice:async(kind,id,section)=>result(kind,id,section,{summary:section})});
  await h.page.loadVisitAdvice();assert.equal(h.page.data.visitAdvice.summary,'overview');
  await h.page.selectVisitAdviceSection({currentTarget:{dataset:{section:'tasks'}}});assert.equal(h.page.data.visitAdvice.summary,'tasks');
  assert.deepEqual(h.calls.map(c=>c[0]),['GET','GET']);
  await h.page.requestVisitAdvice({currentTarget:{dataset:{adviceAction:'generate',visitId:'v',section:'overview'}}});assert.equal(h.calls.length,2);
});
test('archive pending survives read; fresh instance can discover it without POST or re-archive',async()=>{
  const h=pageHarness('visit-confirm',{getLatestBusinessAdvice:async()=>result('visit','v','tasks',{status:'running'})});
  await h.page.loadAdvice();assert.equal(h.page.data.adviceStatus,'queued');assert.equal(h.page.data.advice.id,'analysis');
  await h.page.loadAdvice();assert.equal(h.page.data.adviceStatus,'running');assert.deepEqual(h.calls.map(c=>c[0]),['POST','GET']);
  const fresh=pageHarness('visit-confirm',{getLatestBusinessAdvice:async()=>result('visit','v','tasks')});await fresh.page.reloadAdvice();assert.equal(fresh.page.data.adviceStatus,'ready');assert.deepEqual(fresh.calls.map(c=>c[0]),['GET']);
});
test('archive read failure clears stale content; refreshing failure never creates model jobs',async()=>{
  const h=pageHarness('visit-confirm',{getLatestBusinessAdvice:async()=>{throw Object.assign(Error('denied'),{statusCode:403});}});
  h.page.data.advice=result('visit','v','tasks');h.page.data.adviceStatus='ready';await h.page.reloadAdvice();await h.page.reloadAdvice();
  assert.equal(h.page.data.advice,null);assert.equal(h.page.data.adviceStatus,'error');assert.deepEqual(h.calls.map(c=>c[0]),['GET','GET']);
});

test('archive explicit regeneration of completed advice sets retry without duplicating save',async()=>{
  const h=pageHarness('visit-confirm');h.page.data.advice=result('visit','v','tasks');h.page.data.adviceStatus='ready';
  await h.page.loadAdvice();assert.deepEqual(h.calls,[['POST','visit','v','tasks',true]]);assert.equal(h.page.data.archived,true);
});

test('new page replaces an invalid in-flight reader and is not rejected by the old page cleanup',async()=>{
  const {AdvicePool}=require('../miniprogram/utils/advicePool');
  const pool=new AdvicePool(1),oldResponse=deferred();let oldVisible=true,newCalls=0;
  const old=pool.request('same-object-read',()=>oldResponse.promise,{owner:{},valid:()=>oldVisible});
  await tick();oldVisible=false;
  const fresh=pool.request('same-object-read',async()=>{newCalls++;return {status:'succeeded',id:'fresh'};},{owner:{},valid:()=>true,replaceInvalid:true});
  const oldRejected=assert.rejects(old);oldResponse.resolve({status:'succeeded',id:'old'});
  await oldRejected;assert.equal((await fresh).id,'fresh');assert.equal(newCalls,1);
  assert.equal(pool.peek('same-object-read').id,'fresh');
});
test('old request settling last cannot clear a newer successful cache entry',async()=>{
  const {AdvicePool}=require('../miniprogram/utils/advicePool');
  const pool=new AdvicePool(2),oldResponse=deferred();let valid=true;
  const old=pool.request('key',()=>oldResponse.promise,{owner:{},valid:()=>valid});await tick();valid=false;
  const oldRejected=assert.rejects(old);
  assert.equal((await pool.request('key',async()=>({status:'succeeded',id:'new'}),{owner:{},replaceInvalid:true})).id,'new');await tick();
  oldResponse.resolve({status:'succeeded',id:'old'});await oldRejected;await tick();
  assert.equal(pool.peek('key').id,'new');assert.equal(pool.active,0);assert.equal(pool.jobs.size,0);
});

test('reopening cannot replace or automatically repeat an already dispatched generation',async()=>{
  const {AdvicePool}=require('../miniprogram/utils/advicePool');
  const pool=new AdvicePool(2),pending=deferred();let valid=true,calls=0;
  const first=pool.request('generation',()=>{calls++;return pending.promise;},{owner:{},valid:()=>valid});await tick();valid=false;
  const second=pool.request('generation',async()=>{calls++;return {status:'queued',id:'duplicate'};},{owner:{}});
  assert.equal(second,first);const rejected=assert.rejects(first);pending.resolve({status:'queued',id:'accepted'});await rejected;assert.equal(calls,1);
});
test('pure partner header cannot read or generate customer advice; dual identity remains eligible',async()=>{
  for(const identities of [['partner'],['customer','partner']]){
    const h=pageHarness('customers');h.page._adviceCustomer.business_identities=identities;
    await h.page.loadCustomerAdvice();await h.page.requestCustomerAdvice({currentTarget:{dataset:{adviceAction:'generate',customerId:'c',section:'overview'}}});
    assert.equal(h.calls.length,identities.includes('customer')?2:0);
  }
});

{
  const action={currentTarget:{dataset:{adviceAction:'generate',customerId:'c',section:'overview'}}};
  test(`customer advice: missing or denied customer advice capabilities make no requests`,async()=>{
    for(const capabilities of [{},{can_read_advice:false,can_request_advice:true}]){
      const h=pageHarness('customers');h.page._adviceCustomer={id:'c',...capabilities};
      await h.page.loadCustomerAdvice();await h.page.requestCustomerAdvice(action);
      assert.equal(h.calls.length,0);assert.equal(h.page.data.detailCanReadAdvice,false);assert.equal(h.page.data.detailCanRequestAdvice,false);
    }
  });
  test(`customer advice: customer read-only advice keeps GET and hides generation`,async()=>{
    const h=pageHarness('customers');h.page._adviceCustomer.can_request_advice=false;
    await h.page.loadCustomerAdvice();await h.page.requestCustomerAdvice(action);
    assert.deepEqual(h.calls.map(c=>c[0]),['GET']);assert.equal(h.page.data.detailCanReadAdvice,true);assert.equal(h.page.data.detailCanRequestAdvice,false);
    h.page._adviceCustomer.can_request_advice=true;await h.page.requestCustomerAdvice(action);
    assert.deepEqual(h.calls.map(c=>c[0]),['GET','POST']);assert.equal(h.page.data.detailCanRequestAdvice,true);
  });
  test(`customer advice: object permission cannot override a session deny or a revoked pending read`,async()=>{
    const h=pageHarness('customers');h.app.globalData.session.permissions['advice.read']=false;
    await h.page.loadCustomerAdvice();assert.equal(h.calls.length,0);assert.equal(h.page.data.detailCanReadAdvice,false);
    const d=deferred(),pending=pageHarness('customers',{getLatestBusinessAdvice:()=>d.promise});
    const request=pending.page.loadCustomerAdvice();pending.page._adviceCustomer.can_read_advice=false;
    d.resolve(result('customer','c'));await request;
    assert.notEqual(pending.page.data.detailAdvice.overview.summary,'server advice');
  });
  test(`customer advice: allowed customer advice HTTP errors stay errors, never an empty result`,async()=>{
    const h=pageHarness('customers',{getLatestBusinessAdvice:async()=>{throw Object.assign(Error('建议当前不可读取'),{statusCode:404});}});
    await h.page.loadCustomerAdvice();assert.equal(h.page.data.detailAdvice.overview.status,'error');
    assert.equal(h.page.data.detailAdvice.overview.error,'建议当前不可读取');assert.deepEqual(h.calls.map(c=>c[0]),['GET']);
  });
}
