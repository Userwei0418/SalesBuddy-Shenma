const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const fixture=require('./helpers/business-options.json');
const catalog=require('../miniprogram/utils/businessOptions');
function client() {
 const requests=[],storage=new Map(),filename=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,Map,Set,Date,require:n=>require(path.resolve(path.dirname(filename),n)),wx:{getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>requests.push(r)}});
 const api=module.exports;api.saveAuth({access_token:'test',actor:{workspace_id:'one',user_id:'one'}});
 return {api,requests};
}
const tick=()=>new Promise(r=>setImmediate(r));
test('runtime catalog begins empty, validates before replacement and has no production fallback',()=>{
 catalog.clear();assert.equal(catalog.isReady(),false);assert.deepEqual(catalog.stages,[]);
 catalog.install(fixture);const ref=catalog.stages;assert.equal(ref.length,7);
 assert.throws(()=>catalog.install({contract_version:1}),/目录不可用/);assert.equal(ref.length,7);
 const custom=JSON.parse(JSON.stringify(fixture));custom.customer.industry=['后台新增行业'];custom.opportunity.stages[0].label='后台阶段名';catalog.install(custom);
 assert.equal(ref[0].label,'后台阶段名');assert.deepEqual(catalog.customer.industry,['后台新增行业']);
 catalog.clear();assert.equal(ref.length,0);
});
test('parallel readers share metadata, API failure is retryable and business read cannot use missing choices',async()=>{
 const {api,requests}=client();const a=api.listOpportunities(),b=api.getCustomerOverview('c');
 await tick();assert.equal(requests.length,1);assert.match(requests[0].url,/metadata\/business-options$/);
 const settled=Promise.allSettled([a,b]);requests[0].success({statusCode:503,data:{detail:'目录维护中'}});
 assert.ok((await settled).every(r=>r.status==='rejected'));assert.equal(catalog.isReady(),false);
 const retry=api.listOpportunities();await tick();requests[1].success({statusCode:200,data:fixture});await tick();
 assert.match(requests[2].url,/opportunities/);requests[2].success({statusCode:200,data:{items:[]}});await retry;
 assert.equal(catalog.isReady(),true);
});
test('old identity metadata cannot overwrite new account choices',async()=>{
 const {api,requests}=client();const old=api.getBusinessOptions();await tick();
 const rejected=assert.rejects(old,/登录状态已变更/);
 api.saveAuth({access_token:'other',actor:{workspace_id:'two',user_id:'two'}});
 const next=api.getBusinessOptions();await tick();const custom=JSON.parse(JSON.stringify(fixture));custom.customer.industry=['当前企业'];
 requests[1].success({statusCode:200,data:custom});await next;
 requests[0].success({statusCode:200,data:fixture});await rejected;
 assert.deepEqual(catalog.customer.industry,['当前企业']);
});
test('legacy role names cannot grant actions; explicit server capabilities decide',()=>{
 const {can,pageAllowed}=require('../miniprogram/utils/access');
 for(const role of ['sales','supervisor','manager'])assert.equal(can({role},'customer.create'),false);
 assert.equal(can({role:'manager',capabilities:{'opportunity.edit':false}},'opportunity.edit'),false);
 assert.equal(pageAllowed({role:'operations',capabilities:{'customer.create':true}},'customer-assign-confirm'),true);
});
test('a page created before metadata arrives refreshes its visible stage and grade selectors',()=>{
 catalog.clear();let definition;
 const filename=path.resolve(__dirname,'../miniprogram/pages/opportunities/index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:d=>definition=d,require:n=>require(path.resolve(path.dirname(filename),n)),getApp:()=>({globalData:{session:{role:'sales'}}})});
 const page={...definition,pageRequest:{},data:JSON.parse(JSON.stringify(definition.data)),setData(value){Object.assign(this.data,value);}};
 assert.equal(page.data.stageOptions.length,0);assert.equal(page.data.gradeOptions.length,1);
 const custom=JSON.parse(JSON.stringify(fixture));custom.opportunity.stages[0].text='服务端阶段名';custom.opportunity.grades[0].label='服务端等级名';catalog.install(custom);
 page.acceptPage({items:[],summary:{total:0,open_amount:0},has_more:false,team_options:[],facets:{owners:[]}},false);
 assert.equal(page.data.stageOptions[0].label,'服务端阶段名');assert.equal(page.data.gradeOptions[1].label,'服务端等级名');
});

test('creation choices are separate stable arrays, missing or malformed sections never widen them to history choices',()=>{
 catalog.install(fixture);const ref=catalog.customerCreation.source;
 assert.deepEqual(ref,fixture.customer_creation.source);assert.equal(catalog.isCustomerCreationReady(),true);
 assert.ok(catalog.customer.source.includes('公司分配'));assert.ok(!ref.includes('公司分配'));
 const old=structuredClone(fixture);delete old.customer_creation;
 const malformed=structuredClone(fixture);malformed.customer_creation.source=['销售自拓','销售自拓'];
 for(const value of [old,malformed]){
  catalog.install(value);assert.equal(catalog.isReady(),true);assert.equal(catalog.isCustomerCreationReady(),false);
  assert.equal(catalog.customerCreation.source,ref);assert.deepEqual(ref,[]);
  assert.ok(catalog.customer.source.includes('公司分配'));
 }
 catalog.install(fixture);assert.equal(catalog.customerCreation.source,ref);assert.equal(ref.length,6);
 catalog.clear();assert.equal(catalog.isCustomerCreationReady(),false);assert.equal(ref.length,0);
});

test('explicit metadata refresh bypasses the cached old response and recovers the new creation catalog',async()=>{
 const {api,requests}=client();const old=structuredClone(fixture);delete old.customer_creation;
 const first=api.getBusinessOptions();await tick();requests[0].success({statusCode:200,data:old});await first;
 await api.getBusinessOptions();assert.equal(requests.length,1);assert.equal(catalog.isCustomerCreationReady(),false);
 const refreshed=api.getBusinessOptions({fresh:true});await tick();assert.equal(requests.length,2);
 requests[1].success({statusCode:200,data:fixture});await refreshed;
 assert.equal(catalog.isCustomerCreationReady(),true);assert.deepEqual(catalog.customerCreation.source,fixture.customer_creation.source);
 await api.getBusinessOptions();assert.equal(requests.length,2);
});

test('a superseded metadata response cannot overwrite an explicit same-identity refresh',async()=>{
 const {api,requests}=client();const old=api.getBusinessOptions();await tick();const rejection=assert.rejects(old,/目录已刷新/);
 const refreshed=api.getBusinessOptions({fresh:true});await tick();assert.equal(requests.length,2);
 requests[1].success({statusCode:200,data:fixture});await refreshed;
 const oldBody=structuredClone(fixture);delete oldBody.customer_creation;
 requests[0].success({statusCode:200,data:oldBody});await rejection;
 assert.equal(catalog.isCustomerCreationReady(),true);
});
