const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const catalog=require('../miniprogram/utils/businessOptions'),identity=require('../miniprogram/utils/customerIdentity');
const tick=()=>new Promise(r=>setImmediate(r));
async function setup(source='销售自拓') {
 const metadata=structuredClone(require('./helpers/business-options.json'));metadata.customer_creation.business_identities=identity.OPTIONS;catalog.install(metadata);
 const session={workspaceId:'w',userId:'u',role:'operations',team:'南区',permissions:{'customer.create':true,'partner.read':true}},calls=[],modals=[],memory=new Map();let definition;
 const values={customer_name:'合成企业',industry:'企业软件',customer_type:'潜在客户',level_code:'Tier-1',lead_source:source,partner_name:'',contact_name:'合成联系人',contact_title:'经理',contact_role:'决策者',assigned_sales:'合成销售',assigned_team:'南区',first_action:'本周联系'};
 memory.set('managementCustomerDraft:w:u',{fields:Object.entries(values).map(([key,value])=>({key,value}))});
 const api={getBusinessOptions:async()=>catalog.install(metadata),getDirectoryMembers:async()=>({items:[{id:'s',name:'合成销售',account_code:'SALES',role:'sales',team:'南区'}]}),
   createCustomer:async body=>{calls.push({kind:'create',body:structuredClone(body)});return {id:'c'};},assignCustomer:async(id,body)=>{calls.push({kind:'assign',id,body});throw Error('下发失败');}};
 const file=path.resolve(__dirname,'../miniprogram/pages/customer-assign-confirm/index.js');
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>definition=p,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>({ensureLogin:()=>true,globalData:{session}}),
  wx:{getStorageSync:k=>structuredClone(memory.get(k)),setStorageSync:(k,v)=>memory.set(k,structuredClone(v)),removeStorageSync:k=>memory.delete(k),showModal:m=>modals.push(m),showToast(){},vibrateShort(){}},setTimeout:()=>0});
 const page={...definition,data:structuredClone(definition.data),setData:o=>Object.assign(page.data,o)};await page.onLoad();await tick();
 return {page,calls,modals,memory,async submit(){page.confirmArchive();assert.ok(modals.length);modals.pop().success({confirm:true});await tick();}};
}
test('下发建档旧稿公司分配不猜映射，原文保留并要求重选',async()=>{
 const h=await setup('公司分配');const source=h.page.data.fields.find(f=>f.key==='lead_source');assert.equal(source.value,'公司分配');assert.match(source.validationError,/重选|重新选择/);h.page.confirmArchive();assert.equal(h.calls.length,0);assert.equal(h.modals.length,0);
 assert.equal(h.memory.get(h.page.draftKey).fields.find(f=>f.key==='lead_source').value,'公司分配');
});
test('纯伙伴直接建档只创建一次，失败重试仅继续下发且身份草稿保留',async()=>{
 const h=await setup();h.page.changeBusinessIdentities({detail:{value:['partner']}});assert.equal(h.page.data.missingCount,0);await h.submit();
 assert.deepEqual(h.calls[0].body.business_identities,['partner']);assert.equal(h.calls[0].body.customer_type,null);assert.equal(h.memory.get(h.page.draftKey).createdCustomerId,'c');
 h.page.changeBusinessIdentities({detail:{value:['customer']}});assert.deepEqual(Array.from(h.page.data.businessIdentities),['partner']);
 await h.submit();assert.equal(h.calls.filter(c=>c.kind==='create').length,1);assert.equal(h.calls.filter(c=>c.kind==='assign').length,2);
});
