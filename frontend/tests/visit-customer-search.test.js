const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.resolve(__dirname,'../miniprogram');
const tick=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const customer={id:'customer',name:'跨越测试客户'};
function session(role='sales',scope='self',directory=true){return {workspaceId:'w',userId:'u',role,permissionVersion:1,permissions:{'visit.create':true,'customer.claim_directory':directory},permissionGrants:[{permission_code:'visit.create',effect:'allow',scope_code:scope}]};}
function harness(name,actor=session(),handler=async()=>({items:[customer]})){
 const calls=[],timers=new Map();let timer=0,page;const app={globalData:{session:actor}};
 const filename=path.join(root,'pages',name,'index.js');
 const api={listCustomers:options=>{calls.push({kind:'read',...options});return handler();},listCustomerClaimPool:options=>{calls.push({kind:'directory',...options});return handler();}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,wx:{setStorageSync(){},getStorageSync(){},showToast(){}},
 require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),n)),
 setTimeout:f=>{timers.set(++timer,f);return timer;},clearTimeout:id=>timers.delete(id),clearInterval(){}});
 page.data=structuredClone(page.data);page.setData=x=>Object.assign(page.data,x);
 for(const method of ['persist','refresh','refreshGate','invalidateOpportunityReads','loadOpportunities'])page[method]=()=>{};
 page.draftKey='test';page.loadGeneration=1;
 const search=()=>name==='visit-entry'?page.searchDepartmentCustomers():page.searchCustomers();
 const input=value=>name==='visit-entry'?page.inputCustomerQuery({detail:{value}}):page.inputCustomer({detail:{value}});
 const rows=()=>name==='visit-entry'?page.data.customerResults:page.data.customers;
 const setRows=value=>page.setData({[name==='visit-entry'?'customerResults':'customers']:value});
 return {page,app,calls,timers,search,input,rows,setRows,flush:async()=>{const all=[...timers.values()];timers.clear();all.forEach(f=>f());await tick();}};
}
for(const name of ['visit-entry','visit-confirm']){
 test(`${name}: 按实际权限分路，角色不能扩大assigned，也不强制增加目录权限`,async()=>{
  const cases=[
   [session('sales','assigned',true),'read','mine'],
   [session('fde','self',true),'directory',undefined],
   [session('sales','self',false),'read','company'],
   [{workspaceId:'w',userId:'u',role:'fde',capabilities:{'customer.claim_directory':true}},'read','mine'],
   [{workspaceId:'w',userId:'u',role:'sales',capabilities:{'customer.claim_directory':true}},'directory',undefined],
   [{...session(),permissionGrants:[{permission_code:'visit.create',effect:'allow',scope_code:'self'},{permission_code:'customer.claim_directory',effect:'deny',scope_code:'workspace'}]},'read','company'],
  ];
  for(const [actor,kind,scope]of cases){const h=harness(name,actor);h.page.data.customerQuery=' 跨越 & + ';await h.search();
   assert.equal(h.calls.length,1);assert.equal(h.calls[0].kind,kind);assert.equal(h.calls[0].scope,scope);
   assert.equal(h.calls[0].q,'跨越 & +');assert.equal(h.calls[0].pageSize,100);if(kind==='directory')assert.equal(h.calls[0].offset,0);
   assert.equal(h.rows()[0].id,customer.id);assert.equal(h.page.data.customerSearchDone,true);
  }
 });
 test(`${name}: 403清旧结果并明确拒绝，不降级；重试成功空列表与失败区分`,async()=>{
  let fail=true;const h=harness(name,session(),async()=>{if(fail)throw Object.assign(Error('denied'),{statusCode:403});return {items:[]};});
  h.setRows([customer]);h.page.setData({errorText:'原文校验错误'});await h.search();
  assert.equal(h.rows().length,0);assert.match(h.page.data.customerSearchError,/无权/);assert.equal(h.page.data.customerSearchDone,false);assert.equal(h.calls.length,1);assert.equal(h.calls[0].kind,'directory');
  fail=false;await h.page.retryCustomerSearch();assert.equal(h.page.data.customerSearchError,'');assert.equal(h.page.data.customerSearchDone,true);assert.equal(h.page.data.searching,false);assert.equal(h.rows().length,0);assert.equal(h.page.data.errorText,'原文校验错误');
 });
 test(`${name}: 防抖前清旧结果并阻止旧选择，显式搜索取消待执行定时器`,async()=>{
  const h=harness(name);h.setRows([customer]);h.input('  跨越  ');
  assert.equal(h.rows().length,0);assert.equal(h.page.data.searching,true);assert.equal(h.page.data.customerSearchDone,false);
  h.page.chooseCustomer({currentTarget:{dataset:{id:customer.id}}});assert.equal(h.page.data.customerId,'');
  await h.page.retryCustomerSearch();await h.flush();assert.equal(h.calls.length,1);assert.equal(h.calls[0].q,'跨越');
 });
 test(`${name}: 迟到成功与错误不能覆盖新查询`,async()=>{
  for(const failOld of [false,true]){const old=deferred(),fresh=deferred();let call=0;const h=harness(name,session(),()=>++call===1?old.promise:fresh.promise);
   h.page.data.customerQuery='旧';const a=h.search();h.page.data.customerQuery='新';const b=h.search();fresh.resolve({items:[customer]});await b;
   if(failOld)old.reject(Error('old'));else old.resolve({items:[{id:'old',name:'旧'}]});await a;
   assert.equal(h.rows()[0].id,customer.id);assert.equal(h.page.data.customerSearchError,'');
  }
 });
 test(`${name}: 无效响应不能冒充成功空结果`,async()=>{
  for(const response of [null,{}, {items:null}]){const h=harness(name,session(),async()=>response);await h.search();assert.ok(h.page.data.customerSearchError);assert.equal(h.page.data.customerSearchDone,false);assert.equal(h.rows().length,0);}
 });
 test(`${name}: 换号后丢弃旧响应，已显示的旧候选不能被选择`,async()=>{
  const old=deferred(),h=harness(name,session(),()=>old.promise);const pending=h.search();h.app.globalData.session.userId='other';old.resolve({items:[customer]});await pending;assert.equal(h.rows().length,0);
  const ready=harness(name);await ready.search();ready.app.globalData.session.permissionVersion=2;ready.page.chooseCustomer({currentTarget:{dataset:{id:customer.id}}});assert.equal(ready.page.data.customerId,'');
 });
 test(`${name}: 空查询保持原目录加载，输入最多100个字符且不接受非字符串`,async()=>{
  const h=harness(name);h.input('客😀'.repeat(60));await h.flush();assert.equal([...h.calls[0].q].length,100);
  h.input(undefined);await h.flush();assert.equal(h.calls[1].q,'');
 });
}
