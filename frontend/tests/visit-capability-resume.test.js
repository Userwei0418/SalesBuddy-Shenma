require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));

// Exercise the real app permission refresh and page lifecycle with a delayed
// /auth/me response, as happens when a previously signed-in account gains access.
function setup(route, role = 'supervisor') {
  let app, definition, resolveActor;
  const pages = [], storage = new Map(), calls = [], recorderBindings = [];
  const actorResponse = new Promise(resolve => { resolveActor = resolve; });
  const recorder = {onStart:fn=>recorderBindings.push(['start',fn]),onStop:fn=>recorderBindings.push(['stop',fn]),onError:fn=>recorderBindings.push(['error',fn])};
  const api = {
    getCurrentActor:()=>actorResponse, getAuth:()=>null,
    listCustomerClaimPool:async options=>{calls.push({kind:'claims',options});return {items:[{id:'customer-1',name:'可认领客户',can_claim:true}],total:1,has_more:false,next_offset:null};},
    getCustomerReference:async id=>{calls.push({kind:'customer',id});return {id,name:'授权客户'};},
    listCustomers:async options=>{calls.push({kind:'customers',options});return {items:[]};},
    request:async options=>{calls.push(options);return options.path==='/directory/colleagues'?{items:[]}:{id:'visit-1',customer_id:'customer-1',customer_name:'授权客户',recorder_name:'本人',version_no:2};},
  };
  const wx = {getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),getRecorderManager:()=>recorder,showToast(){},removeStorageSync:key=>storage.delete(key),navigateTo:options=>calls.push({kind:'navigate',url:options.url})};
  const accessModule={exports:{}};
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../miniprogram/utils/access.js'),'utf8'),{module:accessModule,wx});
  const evaluate=(file,globals)=>{
    const filename=path.resolve(__dirname,'../miniprogram',file);
    vm.runInNewContext(fs.readFileSync(filename,'utf8'),{...globals,require:name=>name.endsWith('apiClient')?api:name.endsWith('/access')?accessModule.exports:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,getCurrentPages:()=>pages,wx,Date,Set,Map,setTimeout,clearTimeout,setInterval,clearInterval},{filename});
  };
  evaluate('app.js',{App:value=>{definition=value;}});
  app={...definition,globalData:{...definition.globalData,role,session:{role,userId:'viewer',userName:'本人',workspaceId:'workspace',account:'ACCOUNT',permissionVersion:'old',capabilities:{'customer.claim':false,'visit.create':false,'visit.supplement':false}}}};
  evaluate(`pages/${route}/index.js`,{Page:value=>{definition=value;}});
  const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(values,callback){for(const [key,value] of Object.entries(values)){const keys=key.split('.');let current=this.data;for(const part of keys.slice(0,-1))current=current[part]||(current[part]={});current[keys.at(-1)]=value;}if(callback)callback();}};
  pages.push(page);
  return {app,page,calls,recorderBindings,async refresh(capabilities){resolveActor({actor:{user_id:'viewer',role,permission_version:'new',capabilities}});await app.refreshCapabilities(true);await tick();}};
}

for(const role of ['sales','supervisor','manager'])test(`${role}旧会话认领权限刷新后自动加载名单，未放行前不请求业务数据`,async()=>{
  const h=setup('customer-claim',role);h.page.onLoad();h.page.onShow();assert.equal(h.page.data.accessBlocked,true);assert.equal(h.calls.length,0);
  await h.refresh({'customer.claim':true});assert.equal(h.app.globalData.session.role,role);assert.equal(h.page.data.accessBlocked,false);assert.equal(h.page.data.loading,false);assert.equal(h.page.data.customers[0].id,'customer-1');assert.equal(h.calls.length,1);
  h.page.onShow();assert.equal(h.calls.length,1,'恢复初始化只执行一次');
});

for(const role of ['supervisor','manager'])test(`${role}本人拜访权限刷新后保留带入客户商机并只绑定一次录音回调`,async()=>{
  const h=setup('visit-entry',role);h.page.onLoad({customerId:'customer-1',opportunityId:'opportunity-1'});h.page.onShow();assert.equal(h.page.data.accessBlocked,true);assert.equal(h.calls.length,0);assert.equal(h.recorderBindings.length,0);
  await h.refresh({'visit.create':true});assert.equal(h.page.data.accessBlocked,false);assert.equal(h.page.data.customerId,'customer-1');assert.equal(h.page.data.opportunityId,'opportunity-1');assert.equal(h.page.data.customerConfirmed,true);assert.match(h.page.draftKey,/workspace:viewer/);assert.equal(h.recorderBindings.length,3);
  h.page.onShow();assert.equal(h.recorderBindings.length,3);assert.equal(h.calls.length,1);
});

test('仅补充权限恢复时保留原visitId，不误用创建权限也不降级成新拜访',async()=>{
  const h=setup('visit-confirm','manager');h.page.onLoad({visitId:'visit-1'});h.page.onShow();assert.equal(h.page.data.accessBlocked,true);assert.equal(h.calls.length,0);
  await h.refresh({'visit.create':false,'visit.supplement':true});assert.equal(h.page.data.accessBlocked,false);assert.equal(h.page.data.editing,true);assert.equal(h.page.data.visitId,'visit-1');assert.equal(h.page.data.version,2);assert.equal(h.page.data.customerId,'customer-1');assert.equal(h.page._accessOptions.visitId,'visit-1');assert.equal(h.calls.filter(call=>call.path==='/visits/visit-1').length,1);
  h.page.onShow();assert.equal(h.calls.filter(call=>call.path==='/visits/visit-1').length,1);assert.equal(h.page.data.canRecordVisit,false);
});

for(const role of ['supervisor','manager'])test(`${role}本人拜访确认权限恢复后初始化本人的确认草稿`,async()=>{
  const h=setup('visit-confirm',role);h.page.onLoad({});h.page.onShow();assert.equal(h.calls.length,0);
  await h.refresh({'visit.create':true,'visit.supplement':true});assert.equal(h.page.data.accessBlocked,false);assert.equal(h.page.confirmInitialized,true);assert.equal(h.page.data.editing,false);assert.equal(h.page.data.recorderName,'本人');assert.match(h.page.draftKey,/workspace:viewer/);assert.equal(h.page.data.canRecordVisit,true);
  const requests=h.calls.length;h.page.onShow();assert.equal(h.calls.length,requests);
});

test('FDE刷新后仍无认领能力时持续阻断，不能由身份标签自行放行',async()=>{
  const h=setup('customer-claim','fde_lead');h.page.onLoad();await h.refresh({'customer.claim':false,'visit.create':true,'task.create':true});h.page.onShow();assert.equal(h.page.data.accessBlocked,true);assert.equal(h.calls.length,0);assert.equal(h.app.globalData.session.role,'fde_lead');
});

for (const role of ['sales','supervisor','manager']) test(`${role}补充本人记录以记录人ID判断，同名不放行、改名不误拦`,()=>{
  const {normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
  const h=setup('customer-detail',role);
  h.app.globalData.session.capabilities={'visit.supplement':true};
  h.app.guardPage(h.page,'customer-detail');
  h.page.setData({customer:normalizeCustomerDetail({id:'customer-1',name:'授权客户',visits:[
    {id:'same-name',creator_name:'本人',recorder_id:'other-person'},
    {id:'renamed',creator_name:'旧姓名',recorder_id:'viewer'},
    {id:'missing-id',creator_name:'本人'},
  ]})});
  const supplement=id=>h.page.supplementVisit({currentTarget:{dataset:{id}}});
  supplement('same-name');supplement('missing-id');supplement('unknown');
  assert.equal(h.calls.length,0,'显示名相同、缺失记录人及不存在的记录不能获得补充入口');
  supplement('renamed');assert.equal(h.calls.length,1);
  assert.equal(h.calls[0].url,'/pages/visit-confirm/index?visitId=renamed');
  h.app.globalData.session.capabilities={'visit.supplement':false};
  supplement('renamed');assert.equal(h.calls.length,1,'记录人本人也需要当前有效的补充权限');
});
