const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const identity=require('../miniprogram/utils/customerIdentity'),catalog=require('../miniprogram/utils/businessOptions');
const edit=require('../miniprogram/utils/customerEditContract');
const fixture=structuredClone(require('./helpers/business-options.json'));
fixture.customer_creation.business_identities=identity.OPTIONS;
const tick=()=>new Promise(r=>setImmediate(r));
async function createPage() {
  catalog.install(fixture);let definition;
  const calls=[],storage=new Map(),modals=[],toasts=[],session={userId:'u',workspaceId:'w',role:'sales',team:'南区',permissions:{'customer.create_request':true,'partner.read':true}};
  const api={getBusinessOptions:async()=>catalog.install(fixture),getTeamDirectory:async()=>({teams:[{id:'t',name:'南区'}]}),listCustomerCreationRequests:async()=>({items:[]}),
    listPartners:async options=>{calls.push({kind:'partners',options});return {items:[{id:'p1',name:'本企业档案',linked_customer_id:null},{id:'p2',name:'外部伙伴',linked_customer_id:'another-enterprise'}],has_more:false,next_offset:null};},
    createCustomerCreationRequest:async body=>{calls.push({kind:'request',body:structuredClone(body)});return {id:'request1'};}};
  const file=path.resolve(__dirname,'../miniprogram/pages/customer-create/index.js');
  const wx={getStorageSync:k=>structuredClone(storage.get(k)),setStorageSync:(k,v)=>storage.set(k,structuredClone(v)),removeStorageSync:k=>storage.delete(k),showToast:v=>toasts.push(v),showModal:v=>modals.push(v),vibrateShort(){}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:d=>definition=d,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>({ensureLogin:()=>true,globalData:{session}}),wx,clearInterval,setTimeout:()=>0});
  const page={...definition,data:structuredClone(definition.data),setData(v){for(const [key,value]of Object.entries(v)){const parts=key.split('.');let x=this.data;for(const part of parts.slice(0,-1))x=x[part];x[parts.at(-1)]=value;}}};
  await page.onLoad();await tick();
  const values={customer_name:'合成企业',industry:fixture.customer_creation.industry[0],level_code:'Tier-1',lead_source:fixture.customer_creation.source[0],target_team:'南区',contact_name:'联系人',contact_title:'经理',contact_role:'决策者'};
  page.refresh(page.data.fields.map(f=>({...f,value:values[f.key]===undefined?f.value:values[f.key],...(f.key==='target_team'?{teamId:'t'}:{})})));
  return {page,calls,storage,modals,toasts,session,async submit(){page.submitCustomer();assert.ok(modals.length);modals.pop().success({confirm:true});await tick();}};
}
test('业务身份目录独立于客户类型，元数据失效时清除新身份选项',()=>{
  catalog.install(fixture);assert.deepEqual(catalog.businessIdentities,identity.OPTIONS);assert.equal(catalog.customerCreation.customer_type.includes('合作伙伴'),false);
  const old=structuredClone(fixture);delete old.customer_creation.business_identities;catalog.install(old);assert.deepEqual(catalog.businessIdentities,[]);catalog.install(fixture);
});
test('纯伙伴走原申请链，客户类型不强填；不授予直建或伙伴管理权限',async()=>{
  const h=await createPage();h.page.changeBusinessIdentities({detail:{value:['partner']}});
  const type=h.page.data.fields.find(x=>x.key==='customer_type');assert.equal(type.required,false);assert.equal(type.hidden,true);assert.equal(h.page.data.requestMode,true);
  await h.submit();const body=h.calls.find(x=>x.kind==='request').body;assert.deepEqual(body.business_identities,['partner']);assert.equal(body.customer_type,null);assert.equal(h.session.permissions['partner.manage'],undefined);
});
test('同企业双身份只提交一次建档，旧来源与两种伙伴关联分开',async()=>{
  const h=await createPage();h.page.changeBusinessIdentities({detail:{value:['partner','customer']}});
  await h.page.openPartnerPicker({currentTarget:{dataset:{mode:'profile'}}});assert.deepEqual(Array.from(h.page.data.partnerPickerItems,x=>x.id),['p1']);h.page.chooseIdentityPartner({currentTarget:{dataset:{id:'p1'}}});
  await h.page.openPartnerPicker({currentTarget:{dataset:{mode:'associated'}}});assert.deepEqual(Array.from(h.page.data.partnerPickerItems,x=>x.id),['p2']);h.page.chooseIdentityPartner({currentTarget:{dataset:{id:'p2'}}});
  await h.submit();const body=h.calls.find(x=>x.kind==='request').body;assert.deepEqual(body.business_identities,['customer','partner']);assert.equal(body.customer_type,'潜在客户');assert.equal(body.partner_profile_id,'p1');assert.equal(body.partner_id,'p2');assert.equal(h.calls.filter(x=>x.kind==='request').length,1);
});
test('身份与稳定伙伴ID随建档草稿保存；空身份被拒绝',async()=>{
  const h=await createPage();h.page.changeBusinessIdentities({detail:{value:['partner']}});h.page.setData({partnerProfileId:'p1',partnerProfileName:'已有档案'});h.page.saveDraft();
  assert.deepEqual(h.storage.get(h.page.draftKey).businessIdentity.business_identities,['partner']);assert.equal(h.storage.get(h.page.draftKey).businessIdentity.partner_profile_id,'p1');
  h.page.changeBusinessIdentities({detail:{value:[]}});assert.deepEqual(Array.from(h.page.data.businessIdentities),['partner']);assert.match(h.toasts.at(-1).title,/至少/);
});
test('无partner.read不请求目录，已有自身档案不允许替换',async()=>{
  const h=await createPage();h.session.permissions['partner.read']=false;await h.page.openPartnerPicker({currentTarget:{dataset:{mode:'profile'}}});assert.equal(h.calls.length,0);
  h.session.permissions['partner.read']=true;h.page.initBusinessIdentity({partner_profile_id:'bound',business_identities:['partner'],name:'本企业'});await h.page.openPartnerPicker({currentTarget:{dataset:{mode:'profile'}}});assert.equal(h.calls.length,0);h.page.clearIdentityPartner({currentTarget:{dataset:{mode:'profile'}}});assert.equal(h.page.data.partnerProfileId,'bound');
});
test('取消身份保留历史自身档案，关联外部伙伴不能是自己',()=>{
  const data={...identity.state({business_identities:['customer'],partner_profile_id:'self'}),associatedPartnerId:'other',partnerAssociationChanged:true};
  assert.equal(identity.payload(data,'潜在客户').partner_profile_id,'self');assert.throws(()=>identity.payload({...data,associatedPartnerId:'self'},'潜在客户'),/自身/);
});
test('纯伙伴编辑发送null类型和明确关联清除，不把未改字段写成空',()=>{
  const original={industry:'软件',level_code:'Tier-1',source:'合作伙伴',customer_type:'潜在客户',partner_name:'旧伙伴',contact_name:'',contact_title:'',contact_role:'使用者'};
  const result=edit.payload({...original,partner_name:'',business_identities:['partner'],customer_type:null,partner_id:null,partner_profile_id:'self'},original);
  assert.equal(result.customer_type,null);assert.equal(result.partner_id,null);assert.equal(result.partner_profile_id,'self');assert.equal(result.source,'合作伙伴');
});
test('纯伙伴读回不画客户评分；双身份保留原客户分数和类型',()=>{
  const normalize=require('../miniprogram/utils/customerDetail').normalizeCustomerSummary;
  const pure=normalize({id:'p',name:'伙伴',business_identities:['partner'],potential_score:70,relationship_score:65});
  assert.equal(pure.hasMapScores,false);assert.equal(pure.potentialText,'不适用');assert.equal(pure.quadrant,'不适用');
  const dual=normalize({id:'dual',business_identities:['customer','partner'],potential_score:70,relationship_score:65});assert.equal(dual.hasMapScores,true);assert.equal(dual.businessIdentityText,'客户、合作伙伴');
});
test('新商机选择目录强制customer身份，统一档案默认不限制',async()=>{
  const urls=[],file=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{module,require:n=>require(path.resolve(path.dirname(file),n)),wx:{getStorageSync:()=>null,request:o=>{urls.push(o.url);o.success({statusCode:200,data:{items:[]}});}}});
  await module.exports.listOpportunityCustomerReferences({q:'测试'});assert.match(urls[0],/business_identity=customer/);
  await module.exports.listCustomerClaimPool();assert.doesNotMatch(urls[1],/business_identity/);
  await module.exports.listCustomerClaimPool({businessIdentity:'partner'});assert.match(urls[2],/business_identity=partner/);
});
test('地图拒绝把纯伙伴计入待评估客户，双身份只按一条企业记录接收',()=>{
 const {validateMapPage}=require('../miniprogram/utils/customerMap');
 const page={items:[{id:'dual',business_identities:['customer','partner']}],as_of:'2026-10-13',activity_window:'all',activity_since:null,refresh_allowed:false};
 assert.equal(validateMapPage(page,{activity_window:'all',business_identity:'partner'}).items.length,1);
 assert.throws(()=>validateMapPage({...page,items:[{id:'pure',business_identities:['partner']}]},{activity_window:'all'}),/身份范围/);
 assert.throws(()=>validateMapPage({...page,items:[{id:'customer',business_identities:['customer']}]},{activity_window:'all',business_identity:'partner'}),/身份范围/);
});
