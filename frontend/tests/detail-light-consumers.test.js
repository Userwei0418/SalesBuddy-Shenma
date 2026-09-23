require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; }
function load(name, api) {
  const filename=path.resolve(__dirname, `../miniprogram/pages/${name}/index.js`);
  const app={ensureLogin:()=>true,globalData:{role:'sales',session:{workspaceId:'w',userId:'u',role:'sales',loginAt:'login-1',permissionVersion:1}}};
  const storage=new Map(), timers=new Map(), toasts=[], navigations=[], modals=[]; let page, timerId=0;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'), {
    Page:p=>page=p,getApp:()=>app,
    require:n=>n.endsWith('/apiClient')?{getBusinessOptions:async()=>require('./helpers/business-options.json'),...api}:require(path.resolve(path.dirname(filename),n)),
    setTimeout:f=>{const id=++timerId;timers.set(id,f);return id;},clearTimeout:id=>timers.delete(id),clearInterval(){},
    wx:{getStorageSync:k=>storage.get(k),removeStorageSync:k=>storage.delete(k),setStorageSync:(k,v)=>storage.set(k,v),showToast:x=>toasts.push(x),navigateTo:x=>navigations.push(x),showModal:x=>modals.push(x),navigateBack(){},setNavigationBarTitle(){}}
  });
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=(v,cb)=>{Object.assign(page.data,v);if(cb)cb();};
  return {page,app,storage,toasts,navigations,modals,runTimers(){const values=[...timers.values()];timers.clear();values.forEach(f=>f());}};
}
const customer={id:'c',name:'数据库客户',industry_code:'企业软件',customer_type_code:'潜在客户',level_code:'Tier-1',source_code:'合作伙伴',primary_partner_name:'伙伴',primary_contact:{id:'person',name:'联系人',title:'经理',relationship_role_code:'decision_maker'},summary:{task_status_counts:{},open_amount:0},profile:{dimensions:[]}};

test('customer edit reads overview primary contact without downloading contacts/history',async()=>{
  let read;const h=load('customer-edit',{getCustomerOverview:async id=>{read=id;return customer;},getCustomer(){throw Error('full history forbidden');}});
  h.page.data.customerId='c';await h.page.loadCustomer();assert.equal(read,'c');assert.equal(h.page.data.form.contact_name,'联系人');assert.equal(h.page.data.form.contact_role,'决策者');assert.equal(h.page.data.loading,false);
});
test('customer edit rejects older errors and permission-changed responses',async()=>{
  const a=deferred(),b=deferred();let call=0;const h=load('customer-edit',{getCustomerOverview:()=>[a,b][call++].promise});h.page.data.customerId='c';
  const first=h.page.loadCustomer(),second=h.page.loadCustomer();b.resolve(customer);await second;a.reject(Error('late'));await first;h.runTimers();assert.equal(h.toasts.length,0);assert.equal(h.page.data.form.name,customer.name);
  const late=deferred();const other=load('customer-edit',{getCustomerOverview:()=>late.promise});other.page.data.customerId='c';const p=other.page.loadCustomer();other.app.globalData.session.permissionVersion=2;late.resolve(customer);await p;assert.equal(other.page.data.form.name,'');
});
test('opportunity editor reads one complete target including all forecast quarters and FDEs',async()=>{
  const existing={id:'o',version_no:3,amount:10000,fde_members:[{id:'fde'}],quarterly_forecasts:[{year:2025,quarter:1},{year:2026,quarter:4}]};
  let id;const h=load('opportunity-create',{getOpportunityDetailOverview:async value=>{id=value;return {id:'c',name:'客户',primary_opportunity:existing};},getCustomer(){throw Error('full customer history forbidden');}});
  h.page.data.customerId='c';h.page.opportunityId='o';await h.page.load();assert.equal(id,'o');assert.equal(h.page.data.existing,existing);assert.equal(h.page.data.existing.quarterly_forecasts.length,2);assert.equal(h.page.data.error,'');
});
test('new opportunity needs only authorized customer reference; wrong customer target is rejected',async()=>{
  const h=load('opportunity-create',{getCustomerReference:async id=>({id,name:'客户引用'}),getOpportunityDetailOverview:async()=>({id:'different',name:'不匹配',primary_opportunity:{id:'o'}})});
  h.page.data.customerId='c';await h.page.load();assert.equal(h.page.data.customerName,'客户引用');assert.equal(h.page.data.existing,null);
  h.page.opportunityId='o';await h.page.load();assert.match(h.page.data.error,/无权查看/);assert.equal(h.page.data.existing,null);assert.equal(h.page.data.customerName,'');
});
test('opportunity load cannot populate a different selected customer or unloaded page',async()=>{
  const waits=[];const h=load('opportunity-create',{getCustomerReference:id=>{const d=deferred();waits.push([id,d]);return d.promise;}});
  h.page.data.customerId='old';const old=h.page.load();const latest=h.page.selectCustomer({currentTarget:{dataset:{id:'new'}}});waits[1][1].resolve({id:'new',name:'新客户'});await latest;waits[0][1].resolve({id:'old',name:'旧客户'});await old;assert.equal(h.page.data.customerName,'新客户');
  const after=h.page.load();h.page.onUnload();waits[2][1].resolve({id:'new',name:'离页迟到'});await after;assert.equal(h.page.data.customerName,'');
});
test('search is invalidated when typing, before debounce, and when selecting a customer',async()=>{
  const waits=[];const h=load('opportunity-create',{listCustomers:()=>{const d=deferred();waits.push(d);return d.promise;},getCustomerReference:async id=>({id,name:'已选择'})});
  h.page.inputCustomer({detail:{value:'旧'}});h.runTimers();h.page.inputCustomer({detail:{value:'新'}});waits[0].resolve({items:[{id:'old'}]});await tick();assert.equal(h.page.data.customers.length,0);
  h.runTimers();await h.page.selectCustomer({currentTarget:{dataset:{id:'c'}}});waits[1].resolve({items:[{id:'new'}]});await tick();assert.equal(h.page.data.customers.length,0);
});
test('home pending customer uses overview and ignores results after permission change',async()=>{
  const d=deferred();const h=load('index',{getCustomerOverview:()=>d.promise});h.storage.set('pendingCustomerContext',{customerId:'c',mode:'record'});const p=h.page.consumePendingCustomerContext();h.app.globalData.session.permissionVersion=2;d.resolve(customer);await p;assert.equal(h.page.data.messages.length,0);
});
test('home saved-customer receipt checks login generation and reads only overview',async()=>{
  const d=deferred();const h=load('index',{getCustomerOverview:()=>d.promise});h.storage.set('lastCreatedCustomer',{id:'c',ownerUserId:'u',workspaceId:'w'});const p=h.page.consumeCreatedCustomerSuccess();h.app.globalData.session.loginAt='login-2';d.resolve(customer);await p;assert.equal(h.page.data.messages.length,0);assert.equal(h.storage.has('lastCreatedCustomer'),false);
});

test('home membership notification reads only target overview and opens that project',async()=>{
  let read;const h=load('index',{getOpportunityDetailOverview:async id=>{read=id;return {id:'c'};},getOpportunityDetail(){throw Error('full history forbidden');}});
  await h.page.handleCardAction({currentTarget:{dataset:{action:'open_fde_membership',opportunityId:'o'}}});
  assert.equal(read,'o');assert.equal(h.navigations.length,1);assert.match(h.navigations[0].url,/customer_id=c&opportunity_id=o/);
});
test('home membership navigation ignores responses after permission change, hiding or unload',async()=>{
  for(const change of [h=>h.app.globalData.session.permissionVersion++,h=>h.page.onHide(),h=>{h.page.onHide();h.page.homeVisible=true;},h=>h.page.onUnload()]){
    for(const rejects of [false,true]){
      const d=deferred(),h=load('index',{getOpportunityDetailOverview:()=>d.promise});
      const p=h.page.handleCardAction({currentTarget:{dataset:{action:'open_fde_membership',opportunityId:'o'}}});
      change(h);if(rejects)d.reject(Error('late'));else d.resolve({id:'c'});await p;
      assert.equal(h.navigations.length,0);assert.equal(h.modals.length,0);
    }
  }
});
