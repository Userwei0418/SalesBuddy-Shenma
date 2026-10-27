const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const root = path.resolve(__dirname, '../miniprogram');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const customer = {id:'00000000-0000-4000-8000-000000000001', name:'跨越速运'};
function apiHarness(statusCode = 200) {
  const module = {exports:{}}, requests = [], storage = new Map();
  const result = {items:[customer],has_more:true,next_offset:20,total:30};
  const wx = {getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
    request:request=>{requests.push(request);request.success({statusCode,data:statusCode===200?result:{detail:'无公司客户目录权限'}});}};
  vm.runInNewContext(fs.readFileSync(path.join(root,'utils/apiClient.js'),'utf8'),{
    module,wx,Map,Set,Date,require:name=>require(path.resolve(root,'utils',name)),
  });
  return {api:module.exports,requests,result};
}

test('真实API封装：初始5条和关键词20条均走公司目录，编码搜索词且不改变普通客户列表',async()=>{
  const h=apiHarness();
  const initial=await h.api.listOpportunityCustomerReferences({pageSize:5});
  const a=new URL(h.requests[0].url);
  assert.equal(a.pathname,'/api/v1/customers/claim-pool');assert.equal(a.searchParams.get('q'),'');
  assert.equal(a.searchParams.get('page_size'),'5');assert.equal(a.searchParams.get('offset'),'0');
  assert.equal(initial.has_more,true);assert.equal(initial.next_offset,20);assert.equal(initial.items[0].id,customer.id);
  await h.api.listOpportunityCustomerReferences({q:'跨越 & + %_',pageSize:20});
  const b=new URL(h.requests[1].url);
  assert.equal(b.pathname,a.pathname);assert.equal(b.searchParams.get('q'),'跨越 & + %_');
  assert.equal(b.searchParams.get('page_size'),'20');assert.equal(b.searchParams.get('offset'),'0');
  assert.equal(b.searchParams.has('scope'),false);assert.equal(b.searchParams.has('purpose'),false);
  assert.equal(h.requests[1].method,'GET');
  await h.api.listCustomers({q:'跨越',pageSize:20});
  assert.equal(new URL(h.requests[2].url).pathname,'/api/v1/customers');
});

test('真实目录403显式拒绝，不回退经营列表、不改归属或发起认领',async()=>{
  const h=apiHarness(403);
  await assert.rejects(h.api.listOpportunityCustomerReferences({q:'跨越'}),e=>e.statusCode===403&&/当前账号没有此操作权限/.test(e.message));
  assert.equal(h.requests.length,1);assert.equal(h.requests[0].method,'GET');
  assert.equal(new URL(h.requests[0].url).pathname,'/api/v1/customers/claim-pool');
});

function harness(overrides = {}) {
  let definition, timer = 0;
  const timers = new Map(), calls = [], references = [], saves = [], navigations = [];
  const app = {ensureLogin:()=>true, guardPage:()=>true, globalData:{session:{workspaceId:'company',userId:'seller',permissionVersion:1,role:'sales'}}};
  const api = {
    getPendingOpportunitySave:()=>null,
    listCustomers:()=>{throw Error('经营列表不能用作商机客户选择');},
    listOpportunityCreateCustomers:async options=>{calls.push({...options});return {items:[customer]};},
    getOpportunityCreateAccess:async id=>{references.push(id);return {customer_reference:customer,can_create_opportunity:true,denial_reason:null};},
    getCustomer:()=>{throw Error('客户选择不能加载经营详情');},
    getCompanyPresentation:async()=>({opportunity_amount:{definition:{schema_version:1,warning_threshold_wan:1000}}}),
    createOpportunity:async(id,payload)=>{saves.push({id,payload});return {id:'00000000-0000-4000-8000-000000000002',customer_id:id,changed:true,version_no:1,event_id:'00000000-0000-4000-8000-000000000003'};},
    ...overrides,
  };
  const wx = {setNavigationBarTitle(){},setStorageSync(){},showToast(){},navigateBack:()=>navigations.push('back')};
  vm.runInNewContext(fs.readFileSync(path.join(root,'pages/opportunity-create/index.js'),'utf8'),{
    Page:x=>definition=x,getApp:()=>app,wx,
    require:name=>name.endsWith('/apiClient')?api:require(path.resolve(root,'pages/opportunity-create',name)),
    setTimeout:callback=>{timers.set(++timer,callback);return timer;},clearTimeout:id=>timers.delete(id),
  });
  const page = {...definition,data:structuredClone(definition.data),setData(values){Object.assign(this.data,values);}};
  page.onLoad({});page.onShow();
  const flush = async()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(f=>f());await tick();};
  const search = async q=>{page.inputCustomer({detail:{value:q}});await flush();};
  return {page,app,api,calls,references,saves,navigations,flush,search};
}

test('创建候选搜索向服务端发送关键词，不限于初始候选；选择核对创建能力且保存客户ID',async()=>{
  const h=harness();assert.equal(h.calls.length,0);
  h.page.setData({customers:[{id:'initial-five',name:'其他客户'}]});
  h.page.inputCustomer({detail:{value:'  跨越  '}});
  assert.equal(h.page.data.customerSearchLoading,true);assert.equal(h.page.data.customers.length,0);
  await h.flush();assert.deepEqual(h.calls,[{q:'跨越',pageSize:20}]);
  assert.equal(h.page.data.customerSearchLoading,false);assert.equal(h.page.data.customerSearchDone,true);
  await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});
  assert.deepEqual(h.references,[customer.id]);assert.equal(h.page.data.customerName,'跨越速运');
  const payload={action:'create',name:'客户选择验收',amount:1};
  h.page.selectComponent=()=>({data:{form:payload},prepare:async()=>payload});
  await h.page.submit();assert.equal(h.saves.length,1);assert.equal(h.saves[0].id,customer.id);
  assert.deepEqual(h.saves[0].payload,payload);assert.equal(h.page.data.saveStatus,'succeeded');
});

test('搜索无结果与403/网络失败明确区分，重试成功清除搜索错误且不碰保存错误',async()=>{
  let attempt=0;
  const h=harness({listOpportunityCreateCustomers:async()=>{attempt++;if(attempt===1)throw Object.assign(Error('无客户引用权限'),{statusCode:403});return {items:[]};}});
  h.page.setData({error:'保留原保存错误'});await h.search('跨越');
  assert.equal(h.page.data.customerSearchLoading,false);assert.equal(h.page.data.customerSearchDone,false);
  assert.equal(h.page.data.customerSearchError,'无客户引用权限');
  h.page.retryCustomerSearch();await h.flush();
  assert.equal(attempt,2);assert.equal(h.page.data.customerSearchError,'');assert.equal(h.page.data.customerSearchDone,true);
  assert.equal(h.page.data.error,'保留原保存错误');assert.equal(h.page.data.customers.length,0);
});

test('无效响应不能被当作成功空列表',async()=>{
  const h=harness({listOpportunityCreateCustomers:async()=>({items:null})});await h.search('跨越');
  assert.equal(h.page.data.customerSearchDone,false);assert.match(h.page.data.customerSearchError,/结果暂不可用/);
});

test('选中后reference已失效或无权引用时停止，不能仅凭目录结果显示可保存表单',async()=>{
  const h=harness({getOpportunityCreateAccess:async()=>{throw Object.assign(Error('客户不存在或无权引用'),{statusCode:404});}});
  await h.search('跨越');await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});
  assert.equal(h.page.data.customerName,'');assert.equal(h.page.data.loading,false);
  assert.match(h.page.data.error,/无权引用/);assert.equal(h.saves.length,0);
});

test('连续关键词乱序返回时，旧成功和旧失败均不能覆盖新结果',async()=>{
  for(const failOld of [false,true]){
    const old=deferred(),latest=deferred();let call=0;
    const h=harness({listOpportunityCreateCustomers:()=>++call===1?old.promise:latest.promise});
    await h.search('跨');await h.search('跨越');latest.resolve({items:[customer]});await tick();
    if(failOld)old.reject(Error('旧搜索失败'));else old.resolve({items:[{id:'old',name:'旧客户'}]});await tick();
    assert.equal(h.page.data.customers[0].id,customer.id);assert.equal(h.page.data.customerSearchError,'');
  }
});

test('清空关键词取消防抖和在途结果，不发公司全表请求',async()=>{
  const old=deferred();let calls=0;const h=harness({listOpportunityCreateCustomers:()=>{calls++;return old.promise;}});
  h.page.inputCustomer({detail:{value:'尚未发送'}});h.page.inputCustomer({detail:{value:'   '}});await h.flush();assert.equal(calls,0);
  await h.search('跨越');h.page.inputCustomer({detail:{value:''}});old.resolve({items:[customer]});await tick();
  assert.equal(calls,1);assert.equal(h.page.data.customers.length,0);assert.equal(h.page.data.customerSearchLoading,false);assert.equal(h.page.data.customerSearchDone,false);
});

test('账号或权限切换后丢弃旧候选、旧响应及迟到选择',async()=>{
  for(const field of ['userId','permissionVersion']){
    const old=deferred();const h=harness({listOpportunityCreateCustomers:()=>old.promise});await h.search('跨越');
    h.app.globalData.session[field]='changed';
    await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});assert.equal(h.references.length,0);
    h.page.onShow();old.resolve({items:[customer]});await tick();
    assert.equal(h.page.data.query,'');assert.equal(h.page.data.customers.length,0);assert.equal(h.page.data.customerSearchLoading,false);
  }
});

test('已选择客户或卸载后，旧搜索不能恢复候选或错误',async()=>{
  for(const close of [false,true]){
    const old=deferred();const h=harness({listOpportunityCreateCustomers:()=>old.promise});await h.search('跨越');
    if(close)h.page.onUnload();else await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});
    old.reject(Error('迟到失败'));await tick();assert.equal(h.page.data.customerSearchError,'');assert.equal(h.page.data.customers.length,0);
  }
});

test('保存未确认期间不能更换客户或开始新搜索',async()=>{
  const h=harness();h.page.setData({saveStatus:'unknown'});await h.search('跨越');
  await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});
  assert.equal(h.calls.length,0);assert.equal(h.references.length,0);assert.equal(h.page.data.customerId,'');
});

test('关键词长度按Unicode字符受限，非字符串输入不发请求',async()=>{
  const h=harness();await h.search('客😀'.repeat(60));assert.equal([...h.calls[0].q].length,100);
  await h.search(['不是关键词']);assert.equal(h.calls.length,1);assert.equal(h.page.data.query,'');
});


test('创建目录独立于普通引用目录，单条能力请求不要求详情读取',async()=>{
  const h=apiHarness();await h.api.listOpportunityCreateCustomers({q:'客户 & 公司',pageSize:5,offset:10});
  const url=new URL(h.requests[0].url);assert.equal(url.pathname,'/api/v1/opportunities/create-customers');
  assert.equal(url.searchParams.get('q'),'客户 & 公司');assert.equal(url.searchParams.get('limit'),'5');assert.equal(url.searchParams.get('offset'),'10');
  Object.assign(h.result,{can_create_opportunity:true,customer_reference:customer,denial_reason:null});
  assert.equal((await h.api.getOpportunityCreateAccess(customer.id)).can_create_opportunity,true);
  assert.equal(new URL(h.requests[1].url).pathname,'/api/v1/opportunities/create-access');
  assert.equal(new URL(h.requests[1].url).searchParams.get('customer_id'),customer.id);
});
test('深链中的公海或范围外客户不能开启表单，且能重选',async()=>{
  for(const denial_reason of ['OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED','OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED']){
    const h=harness({getOpportunityCreateAccess:async()=>({can_create_opportunity:false,customer_reference:null,denial_reason})});
    h.page.onLoad({customerId:customer.id});await tick();assert.equal(h.page.data.createAccessAllowed,false);assert.equal(h.page.data.customerName,'');
    assert.match(h.page.data.error,/认领/);h.page.changeCustomer();assert.equal(h.page.data.customerId,'');assert.equal(h.saves.length,0);
  }
});
test('选中后认领范围变化时拒绝保存并保留填写，不落入结果未知',async()=>{
  let allowed=true;const h=harness({getOpportunityCreateAccess:async()=>({can_create_opportunity:allowed,customer_reference:allowed?customer:null,denial_reason:allowed?null:'OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED'})});
  await h.page.selectCustomer({currentTarget:{dataset:{id:customer.id}}});
  const form={data:{form:{name:'已填草稿'}},prepare:async()=>({action:'create',name:'已填草稿'})};h.page.selectComponent=()=>form;
  allowed=false;await h.page.submit();assert.equal(h.saves.length,0);assert.equal(h.page.data.saveStatus,'idle');assert.equal(form.data.form.name,'已填草稿');assert.match(h.page.data.error,/认领人/);
});

test('已有商机更新不读取客户创建资格，认领范围限制只作用新建',async()=>{
 const id='00000000-0000-4000-8000-000000000002';
 const h=harness({getOpportunityCreateAccess:()=>{throw Error('更新不应查询创建资格');},getOpportunityDetailOverview:async()=>({...customer,primary_opportunity:{id,version_no:1,amount:1}})});
 h.page.opportunityId=id;h.page.setData({customerId:customer.id});await h.page.load();
 const payload={action:'update',opportunity_id:id,version_no:1,name:'保留更新权限',amount:1};h.page.selectComponent=()=>({data:{form:payload},prepare:async()=>payload});
 await h.page.submit();assert.equal(h.saves.length,1);assert.equal(h.saves[0].payload.action,'update');assert.equal(h.page.data.saveStatus,'succeeded');
});
