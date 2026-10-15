const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../miniprogram');
const receipt=(extra={})=>({id:'00000000-0000-4000-8000-000000000001',customer_id:'customer',operation:'created',changed:true,version_no:1,event_id:'00000000-0000-4000-8000-000000000002',...extra});
const draft=()=>({action:'create',name:'private draft name',amount:12345,quarterly_forecasts:[{year:2026,quarter:4,recognized_amount:12}]});
function harness(){
 const storage=new Map([['salesApiAuth',{access_token:'test-token',actor:{workspace_id:'workspace',user_id:'user'}}]]),requests=[],timers=[],nav=[];
 let handle=o=>o.fail({errMsg:'network timeout'});
 const wx={getStorageSync:k=>structuredClone(storage.get(k)),setStorageSync:(k,v)=>storage.set(k,structuredClone(v)),removeStorageSync:k=>storage.delete(k),
  request:o=>{requests.push(o);handle(o);},showToast(){},showModal:o=>o.success({confirm:true}),setNavigationBarTitle(){},navigateBack:()=>nav.push('back'),switchTab:o=>nav.push(o.url)};
 function loadApi(){
  const identity={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'utils/requestIdentity.js'),'utf8'),{module:identity,wx});
  const api={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(root,'utils/apiClient.js'),'utf8'),{module:api,wx,Map,Set,Date,
   require:n=>n==='./requestIdentity'?identity.exports:require(path.resolve(root,'utils',n))});
  return api.exports;
 }
 let api=loadApi();
 const session={workspaceId:'workspace',userId:'user',role:'sales',permissionVersion:1};
 function page(existing=null){
  let p;const payload=draft(),form={data:{form:payload},prepare:async()=>structuredClone(payload)};
  if(existing)Object.assign(payload,{action:'update',opportunity_id:existing.id,version_no:existing.version_no});
  const bound={...api,getCompanyPresentation:async()=>({opportunity_amount:{definition:{schema_version:1,warning_threshold_wan:1000}}}),
    getCustomerReference:async()=>({id:'customer',name:'Customer'}),getOpportunityDetailOverview:async()=>({id:'customer',name:'Customer',primary_opportunity:existing})};
  vm.runInNewContext(fs.readFileSync(path.join(root,'pages/opportunity-create/index.js'),'utf8'),{Page:d=>p=d,wx,
   getApp:()=>({globalData:{session},ensureLogin:()=>true,guardPage:()=>true}),setTimeout:f=>{timers.push(f);return timers.length;},clearTimeout(){},
   require:n=>n==='../../utils/apiClient'?bound:require(path.resolve(root,'pages/opportunity-create',n))});
  p.data=structuredClone(p.data);p.setData=values=>Object.assign(p.data,values);p.selectComponent=()=>form;
  p.opportunityId=existing?existing.id:'';p.setData({customerId:'customer',customerName:'Customer',existing});
  return {p,payload};
 }
 return {storage,requests,timers,nav,session,wx,page,get api(){return api;},reload(){api=loadApi();},handle(fn){handle=fn;},
  success(result=receipt()){handle=o=>o.success({statusCode:200,data:result});},pending(){return api.getPendingOpportunitySave('customer');}};
}
const respond=(o,status,result)=>o.success({statusCode:200,data:{status,request_key:o.url.split('/').filter(Boolean).at(-1)==='cancel'?o.url.split('/').at(-2):o.url.split('/').at(-1),result}});

test('SAVE-RECOVERY unknown blocks changed payload and customer; explicit retry freezes original payload and key',async()=>{
 const h=harness(),{p,payload}=h.page();await p.submit();
 assert.equal(p.data.saveStatus,'unknown');assert.equal(p.data.canRetrySave,true);const first=h.requests[0];
 payload.name='changed name';payload.amount=999;p.selectCustomer({currentTarget:{dataset:{id:'other'}}});await p.submit();
 assert.equal(h.requests.length,1);assert.equal(p.data.customerId,'customer');
 const stored=JSON.stringify([...h.storage.entries()].filter(([k])=>k.startsWith('salesPending')));
 assert.doesNotMatch(stored,/private draft name|changed name|quarterly_forecasts|12345/);
 h.success();await p.retrySave();assert.equal(h.requests.length,2);
 assert.equal(h.requests[1].header['Idempotency-Key'],first.header['Idempotency-Key']);assert.equal(h.requests[1].data.name,'private draft name');
 assert.equal(h.requests[1].data.amount,12345);assert.equal(p.data.saveStatus,'succeeded');assert.equal(h.pending(),null);
 await p.submit();assert.equal(h.requests.length,2);p.openSavedOpportunity();assert.equal(h.nav.at(-1),'/pages/customers/index');
 assert.equal(h.storage.get('pendingOpenOpportunityId'),'00000000-0000-4000-8000-000000000001');
});

test('SAVE-RECOVERY partial 2xx keeps original key until complete queried receipt, all invalid success shapes stay unresolved',async()=>{
 for(const result of [{id:'00000000-0000-4000-8000-000000000001'},receipt({id:''}),receipt({id:'?'}),receipt({event_id:'?'}),receipt({version_no:0}),receipt({version_no:1.5}),receipt({changed:'true'}),receipt({event_id:''})]){
  const h=harness();h.success(result);const {p}=h.page();await p.submit();assert.equal(p.data.saveStatus,'unknown');
  const key=h.pending().key;assert.equal(key,h.requests[0].header['Idempotency-Key']);
  h.handle(o=>respond(o,'committed',receipt()));await p.checkSaveResult();
  assert.equal(p.data.saveStatus,'succeeded');assert.equal(p.data.saveRecoveryOpportunityId,'00000000-0000-4000-8000-000000000001');assert.equal(h.pending(),null);
  assert.equal(h.requests[1].method,'GET');assert.ok(h.requests[1].url.endsWith('/save-requests/'+key));
 }
});

test('SAVE-RECOVERY reload keeps scope metadata and blocks new creation; no payload retry after reload; unresolved and query errors stay locked',async()=>{
 const h=harness(),first=h.page();await first.p.submit();const key=h.pending().key;first.p.onUnload();h.reload();
 const {p,payload}=h.page();p.onLoad({customerId:'other'});await new Promise(r=>setImmediate(r));
 assert.equal(p.data.customerId,'customer');assert.equal(p.data.saveStatus,'unknown');assert.equal(p.data.canRetrySave,false);
 payload.name='new after reload';await p.submit();assert.equal(h.requests.length,1);
 await assert.rejects(h.api.retryOpportunitySave('customer'),/内存/);assert.equal(h.requests.length,1);
 for(const status of ['unresolved',403,404,401,422,500,'network']){
  h.handle(o=>typeof status==='number'?o.success({statusCode:status,data:{detail:'query denied'}}):status==='network'?o.fail({errMsg:'timeout'}):respond(o,status,null));
  await p.checkSaveResult();assert.equal(p.data.saveStatus,'unknown');assert.equal(h.pending().key,key);await p.submit();
 }
 assert.equal(h.requests.filter(o=>o.method==='POST').length,1);
});

test('SAVE-RECOVERY safe cancellation releases only confirmed cancelled; committed cancel opens original; no-body POST',async()=>{
 for(const status of ['cancelled','committed']){
  const h=harness(),{p}=h.page();await p.submit();const key=h.pending().key;
  h.handle(o=>respond(o,status,status==='committed'?receipt():null));await p.cancelSave();
  assert.equal(h.requests[1].method,'POST');assert.equal(h.requests[1].data,undefined);assert.ok(h.requests[1].url.endsWith('/'+key+'/cancel'));
  assert.equal(h.pending(),null);assert.equal(p.data.saveStatus,status==='committed'?'succeeded':'rejected');
  if(status==='cancelled'){h.success();await p.submit();assert.notEqual(h.requests[2].header['Idempotency-Key'],key);}
 }
 const h=harness(),{p}=h.page();await p.submit();const key=h.pending().key;
 h.wx.showModal=o=>o.success({confirm:false});await p.cancelSave();assert.equal(h.requests.length,1);
 h.wx.showModal=o=>o.success({confirm:true});await p.cancelSave();assert.equal(p.data.saveStatus,'unknown');assert.equal(h.pending().key,key);
 h.handle(o=>respond(o,'cancelled',null));await p.checkSaveResult();assert.equal(p.data.saveStatus,'rejected');assert.equal(h.pending(),null);
});

test('SAVE-RECOVERY original business rejection editable; idempotency conflict and denied uncertain retry preserve old key',async()=>{
 for(const [status,code,unknown] of [[422,'',false],[409,'',false],[409,'IDEMPOTENCY_CONFLICT',true]]){
  const h=harness();h.handle(o=>o.success({statusCode:status,data:{detail:'original rejected',code}}));const {p}=h.page();await p.submit();
  assert.equal(p.data.saveStatus,unknown?'unknown':'rejected');assert.equal(!!h.pending(),unknown);
 }
 const h=harness(),{p}=h.page();await p.submit();const key=h.pending().key;
 h.handle(o=>o.success({statusCode:403,data:{detail:'now forbidden'}}));await p.retrySave();assert.equal(h.pending().key,key);assert.equal(p.data.saveStatus,'unknown');
});

test('SAVE-RECOVERY query mismatched key, target or result cannot finish original intent',async()=>{
 for(const patch of [{request_key:'wrong'},{result:receipt({customer_id:'wrong'})},{result:receipt({operation:'bad'})},{result:receipt({version_no:null})}]){
  const h=harness(),{p}=h.page();await p.submit();const key=h.pending().key;
  h.handle(o=>o.success({statusCode:200,data:{status:'committed',request_key:key,result:receipt(),...patch}}));await p.checkSaveResult();
  assert.equal(p.data.saveStatus,'unknown');assert.equal(h.pending().key,key);
 }
 const h=harness(),{p}=h.page({id:'original',version_no:3,amount:12345});h.success(receipt({id:'other'}));await p.submit();
 assert.equal(p.data.saveStatus,'unknown');assert.ok(h.api.getPendingOpportunitySave('customer','original'));
});

test('SAVE-RECOVERY account switch or destroyed page ignores late query without releasing old scope',async()=>{
 const h=harness(),{p}=h.page();await p.submit();const key=h.pending().key;let respondLater;
 h.handle(o=>respondLater=o);const query=p.checkSaveResult();
 h.api.saveAuth({access_token:'other-test',actor:{workspace_id:'workspace',user_id:'other'}});h.session.userId='other';
 respond(respondLater,'committed',receipt());await query;assert.equal(h.storage.has('pendingOpenOpportunityId'),false);
 h.api.saveAuth({access_token:'test-token',actor:{workspace_id:'workspace',user_id:'user'}});h.session.userId='user';assert.equal(h.pending().key,key);
 const next=h.page();h.handle(o=>respondLater=o);const late=next.p.checkSaveResult();next.p.onUnload();respond(respondLater,'committed',receipt());await late;
 assert.equal(h.storage.has('pendingOpenOpportunityId'),false);assert.equal(next.p.data.saveRecoveryOpportunityId,'');
});

test('SAVE-RECOVERY unrelated mutations retain existing timeout/retry and 2xx handling',async()=>{
 const h=harness(),options={method:'POST',path:'/tasks',data:{description:'task'}};
 await assert.rejects(h.api.request(options));const key=h.requests[0].header['Idempotency-Key'];h.success({id:'task'});
 await h.api.request(options);assert.equal(h.requests[1].header['Idempotency-Key'],key);await h.api.request(options);assert.notEqual(h.requests[2].header['Idempotency-Key'],key);
});
