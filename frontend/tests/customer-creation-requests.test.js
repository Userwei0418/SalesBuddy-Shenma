const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const roots = [path.resolve(__dirname, '../miniprogram'), path.resolve(__dirname, '../../business-web/source/miniprogram')];
function loadCommonJS(file) {
  const full = path.extname(file) ? file : `${file}.js`;
  const mod = {exports:{}};
  vm.runInNewContext(fs.readFileSync(full,'utf8'), {module:mod,exports:mod.exports,require:name=>loadCommonJS(path.resolve(path.dirname(full),name))}, {filename:full});
  return mod.exports;
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function user(role, codes) { return {role, userId:'user', workspaceId:'workspace', team:'测试团队', userName:'测试用户', permissions:Object.fromEntries(['access.mini_program', ...codes].map(code => [code,true]))}; }
function setup(root, session, extra = {}) {
  const file = path.join(root,'pages/customer-create/index.js');
  let definition; const store = new Map(), toasts = [], modals = [], navigations = [], writes = [];
  const api = {getBusinessOptions:async()=>({}), getTeamDirectory:async purpose=>{writes.push(["directory",purpose]);return {teams:[{id:'team',name:'测试团队'}]};},
    listCustomerCreationRequests:async()=>({items:[],total:0,has_more:false}),
    submitCustomerCreationRequest:async payload=>{writes.push(['request',payload]);return {id:'request-1'};},
    createCustomer:async payload=>{writes.push(['customer',payload]);return {id:'customer-1'};}, ...extra};
  const app = {globalData:{session}, ensureLogin:()=>true, guardPage:()=>true};
  const wx = {getStorageSync:key=>store.get(key),setStorageSync:(key,value)=>store.set(key,value),removeStorageSync:key=>store.delete(key),
    showToast:value=>toasts.push(value),showModal:value=>modals.push(value),navigateBack:()=>navigations.push('back'),
    navigateTo:value=>navigations.push(value.url),setNavigationBarTitle:()=>{},vibrateShort:()=>{}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:d=>definition=d,require:name=>name.endsWith('apiClient')?api:loadCommonJS(path.resolve(path.dirname(file),name)),getApp:()=>app,wx,Date,setTimeout:fn=>fn(),clearInterval,setInterval});
  const page = {...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values){Object.assign(this.data,values);}};
  return {page,store,toasts,modals,navigations,writes,api,app};
}
function fill(page) {
  const values={customer_name:'联调客户',company_reference:'S2-001',customer_type:'潜在客户',level_code:'A',lead_source:'销售线索',target_team:'测试团队',contact_name:'联系人',contact_title:'经理',contact_role:'决策人'};
  page.refresh(page.data.fields.map(field=>({...field,value:values[field.key]||'',...(field.key==='target_team'?{teamId:'team'}:{})})));
}
for(const root of roots) {
 const surface=root.includes('business-web')?'Web共享源码':'小程序';
 test(`${surface}：申请权限允许进入，未授予建档的账号不能通过角色名获得入口`,()=>{
  const access=loadCommonJS(path.join(root,'utils/access'));
  for(const role of ['sales','supervisor','manager']) {
   const session=user(role,['customer.create_request']);
   assert.equal(access.flags(session).canCreateCustomer,true);
   assert.equal(access.pageAllowed(session,'customer-create'),true);
   assert.equal(access.can(session,'customer.create'),false);
  }
  assert.equal(access.pageAllowed(user('sales',[]),'customer-create'),false);
  assert.equal(access.flags(user('operations',['customer.create'])).canCreateCustomer,true);
  const page={requestMode:true,submitCustomer(){this.called=true;}};
  access.protectActions({can:key=>access.can(user('sales',['customer.create_request']),key)},page,'customer-create');
  page.submitCustomer(); assert.equal(page.called,true);
 });
 test(`${surface}：销售提交仅创建申请，双击锁定且不写客户创建回执`,async()=>{
  let resolve; const f=setup(root,user('sales',['customer.create_request']),{submitCustomerCreationRequest:payload=>{f.writes.push(['request',payload]);return new Promise(r=>resolve=r);}});
  await f.page.onLoad(); await tick(); fill(f.page);
  assert.equal(f.page.data.requestMode,true); assert.equal(f.page.data.canVoiceCreate,false);
  assert.equal(f.writes.shift()[1],"creation_request");
  f.page.submitCustomer(); f.page.submitCustomer(); assert.equal(f.modals.length,1);
  const pending=f.modals[0].success({confirm:true}); await tick();
  f.page.submitCustomer(); assert.equal(f.writes.length,1); assert.equal(f.page.data.submitting,true);
  resolve({id:'request-1'}); await pending;
  assert.equal(f.writes[0][0],'request'); assert.equal(f.writes[0][1].company_reference,'S2-001');
  assert.equal(f.store.has('lastCreatedCustomer'),false);assert.equal(f.store.has(f.page.draftKey),false);
  assert.equal(f.page.data.activeTab,'requests'); assert.equal(f.page.data.submitting,false);
 });
 test(`${surface}：运营仍直接建档；失败保留草稿并可重试`,async()=>{
  const f=setup(root,user('operations',['customer.create']),{createCustomer:async()=>{throw Error('网络暂不可用');}});
  await f.page.onLoad(); await tick(); fill(f.page); assert.equal(f.page.requestMode,false); assert.equal(f.page.data.canVoiceCreate,true);
  assert.equal(f.writes.shift()[1],"assignment");
  f.page.submitCustomer();await f.modals[0].success({confirm:true});
  assert.equal(f.page.data.submitting,false);assert.equal(f.store.get(f.page.draftKey).fields.find(x=>x.key==='customer_name').value,'联调客户');
  assert.equal(f.page.data.fields.find(x=>x.key==='customer_name').value,'联调客户');
  f.api.createCustomer=async()=>({id:'customer-1'});f.page.submitCustomer();await f.modals[1].success({confirm:true});
  assert.equal(f.store.get('lastCreatedCustomer').id,'customer-1');assert.equal(f.store.has(f.page.draftKey),false);
 });
 test(`${surface}：我的申请保留驳回原因，分页不替换前页，只撤回待审批本人列表项`,async()=>{
  const calls=[],withdraw=[];
  const f=setup(root,user('supervisor',['customer.create_request','customer.claim']),{
   listCustomerCreationRequests:async({offset})=>{calls.push(offset);return offset?{items:[{id:'approved',status:'approved',payload:{name:'已批准'}}],total:3,has_more:false}:{items:[{id:'pending',status:'pending',payload:{name:'待审'}},{id:'rejected',status:'rejected',decision_reason:'需补充公司编号',payload:{name:'被驳回'}}],total:3,has_more:true,next_offset:2};},
   withdrawCustomerCreationRequest:async id=>{withdraw.push(id);return {id,status:'cancelled'};},
  });
  await f.page.onLoad();await tick();await f.page.showRequests();await f.page.loadMoreRequests();
  assert.deepEqual(calls,[0,2]);assert.equal(f.page.data.requests.length,3);assert.equal(f.page.data.requests[1].decision_reason,'需补充公司编号');
  f.page.withdrawRequest({currentTarget:{dataset:{id:'rejected'}}});assert.equal(f.modals.length,0);
  f.page.withdrawRequest({currentTarget:{dataset:{id:'pending'}}});f.page.withdrawRequest({currentTarget:{dataset:{id:'pending'}}});assert.equal(f.modals.length,1);
  await f.modals[0].success({confirm:true});assert.deepEqual(withdraw,['pending']);assert.equal(f.page.data.withdrawingId,'');
  f.page.openCustomerClaim();assert.equal(f.navigations.at(-1),'/pages/customer-claim/index');
 });
 test(`${surface}：加载失败不冒充空列表，离开页面后忽略旧响应`,async()=>{
  const f=setup(root,user('sales',['customer.create_request']),{listCustomerCreationRequests:async()=>{throw Error('读取失败');}});
  await f.page.onLoad();await f.page.showRequests();assert.equal(f.page.data.requestsError,'读取失败');
  let resolve;f.api.listCustomerCreationRequests=()=>new Promise(r=>resolve=r);const pending=f.page.showRequests();f.page.onUnload();resolve({items:[{id:'old',payload:{name:'不应出现'}}]});await pending;assert.equal(f.page.data.requests.length,0);
 });
 test(`${surface}：申请与撤回都保留自动幂等键，新建入口与结果列表实际绑定`,()=>{
  const identity=loadCommonJS(path.join(root,'utils/requestIdentity'));
  assert.equal(identity.supports({method:'POST',path:'/customers/creation-requests'}),true);
  assert.equal(identity.supports({method:'POST',path:'/customers/creation-requests/request-id/withdraw'}),true);
  const list=fs.readFileSync(path.join(root,'pages/customers/index.wxml'),'utf8');assert.match(list,/canCreateCustomer[\s\S]*bindtap="openCustomerCreate"/);
  const view=fs.readFileSync(path.join(root,'pages/customer-create/index.wxml'),'utf8');assert.match(view,/我的申请/);assert.match(view,/item.decision_reason/);assert.match(view,/客户归属需另行申请认领/);assert.match(view,/wx:if="\{\{canVoiceCreate\}\}" class="voice-card/);
 });
}
