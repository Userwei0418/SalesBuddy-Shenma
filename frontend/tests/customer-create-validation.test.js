const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const catalog = require('../miniprogram/utils/businessOptions');
const fixture = require('./helpers/business-options.json');

function apiHarness(statusCode, data) {
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  const module = {exports:{}};
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, require:name=>require(path.resolve(path.dirname(filename), name)),
    wx:{getStorageSync:()=>null,request:options=>options.success({statusCode,data})},
  });
  return module.exports;
}

test('422 retains bounded loc/type only, never input, ctx, raw msg or top-level message', async()=>{
  const detail = [
    {loc:['body','source'],type:'literal_error',input:'SECRET',msg:'SECRET',ctx:{expected:'SECRET'}},
    {loc:['body','items',0,'name'],type:'string_too_long',msg:'SECRET'},
    {loc:['body','source<script>'],type:'literal_error'},
    {loc:['body',{}],type:'missing'},
    {loc:['body',-1],type:'missing'},
    {loc:['body','source'],type:'<script>'},
    {loc:[],type:'missing'},
    {loc:Array(9).fill('body'),type:'missing'},
    {loc:['body','x'.repeat(101)],type:'missing'},
    {loc:['body','unknown_field'],type:'missing'},
    null,
  ];
  await assert.rejects(apiHarness(422,{message:'SECRET',detail}).createCustomerCreationRequest({}), error=>{
    assert.equal(error.code,'HTTP_422'); assert.equal(error.statusCode,422);
    assert.deepEqual(plain(error.validationErrors),[
      {loc:['body','source'],type:'literal_error'},
      {loc:['body','items',0,'name'],type:'string_too_long'},
      {loc:['body','unknown_field'],type:'missing'},
    ]);
    assert.doesNotMatch(error.message+JSON.stringify(error),/SECRET|script|expected|input/);
    return true;
  });
});

test('validation error count is bounded and ordinary HTTP errors retain existing semantics', async()=>{
  await assert.rejects(apiHarness(422,{detail:Array(60).fill({loc:['body','source'],type:'literal_error'})}).createCustomer({}),error=>{
    assert.equal(error.validationErrors.length,50); return true;
  });
  await assert.rejects(apiHarness(403,{detail:'没有当前操作权限',code:'ACCESS_DENIED'}).createCustomer({}),error=>{
    assert.equal(error.message,'没有当前操作权限'); assert.equal(error.serverCode,'ACCESS_DENIED');
    assert.equal(error.validationErrors,undefined); return true;
  });
});

const fieldValues = {
  customer_name:['客户名称','合成客户'],industry:['所属行业','企业软件'],customer_type:['客户类型','潜在客户'],
  level_code:['客户优先级','Tier-1'],lead_source:['客户来源','销售自拓'],target_team:['客户归属团队','南区'],
  partner_name:['合作伙伴',''],contact_name:['首要联系人','合成联系人'],contact_title:['联系人职位','经理'],
  contact_role:['联系人角色','决策者'],
};
function pageHarness({metadata}={}) {
  catalog.install(fixture);
  const filename = path.resolve(__dirname, '../miniprogram/pages/customer-create/index.js');
  let definition, reject;
  const pending = new Promise((resolve,no)=>{reject=no;});
  const calls=[],modals=[],toasts=[],storage=new Map(),metadataCalls=[];
  const session={userId:'user1',workspaceId:'company1',role:'sales',team:'南区',permissionVersion:'p1',permissions:{'customer.create_request':true}};
  const api={createCustomerCreationRequest:payload=>{calls.push(payload);return pending;},
    getBusinessOptions:async options=>{metadataCalls.push(options||{});return catalog.install(metadata?await metadata(options,metadataCalls.length):fixture);},
    getTeamDirectory:async()=>({teams:[{id:'south',name:'南区'}]}),listCustomerCreationRequests:async()=>({items:[]})};
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
    Page:p=>{definition=p;},getApp:()=>({ensureLogin:()=>true,globalData:{session}}),
    require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    wx:{showModal:m=>modals.push(m),showToast:t=>toasts.push(t),getStorageSync:k=>storage.get(k),
      setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),vibrateShort:()=>{}},
    setTimeout:()=>{},clearInterval,
  });
  const page={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch);}};
  page.draftKey='customerCreateDraft:company1:user1';
  page.writeIdentity=require('../miniprogram/utils/access').identity(session);
  page.setData({loading:false,creationOptionsReady:true,requestMode:true,fields:Object.entries(fieldValues).map(([key,[label,value]])=>
    ({key,label,value,required:!['industry','partner_name'].includes(key),...(key==='target_team'?{teamId:'south'}:{})}))});
  page.refresh(page.data.fields);page.saveDraft();
  const draft=JSON.stringify(storage.get(page.draftKey));
  return {page,calls,modals,toasts,storage,session,reject,draft,metadataCalls};
}
const validation = rows=>Object.assign(new Error('SECRET'),{statusCode:422,validationErrors:rows});

test('source and customer_type errors point to fixed Chinese fields and preserve draft and all values',async()=>{
  const h=pageHarness(),before=plain(h.page.data.fields);
  h.page.submitCustomer();h.modals[0].success({confirm:true});
  h.reject(validation([{loc:['body','source'],type:'literal_error',msg:'SECRET'},
    {loc:['body','customer_type'],type:'literal_error'}]));await tick();
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].source,'销售自拓');
  assert.equal(h.page.data.fields.find(f=>f.key==='lead_source').validationError,'客户来源：请选择有效选项');
  assert.equal(h.page.data.fields.find(f=>f.key==='customer_type').validationError,'客户类型：请选择有效选项');
  assert.equal(h.page.data.submissionError,'请修改标出的字段后重新提交');
  assert.equal(h.page.data.submitting,false);
  assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
  assert.deepEqual(plain(h.page.data.fields.map(({validationError,...f})=>f)),before);
  h.page.refresh(h.page.data.fields.map(f=>f.key==='lead_source'?{...f,value:'合作伙伴'}:f));
  assert.equal(h.page.data.fields.find(f=>f.key==='lead_source').validationError,'');
  assert.ok(h.page.data.fields.find(f=>f.key==='customer_type').validationError);
  assert.ok(h.page.data.submissionError);
  h.page.refresh(h.page.data.fields.map(f=>f.key==='customer_type'?{...f,value:'商机客户'}:f));
  assert.equal(h.page.data.submissionError,'');
  h.page.saveDraft();
  assert.equal(h.storage.get(h.page.draftKey).fields.find(f=>f.key==='customer_name').value,'合成客户');
});

test('nested, unknown and malicious field locations show safe summary without pointing to unrelated fields',async()=>{
  const h=pageHarness();h.page.submitCustomer();h.modals[0].success({confirm:true});
  h.reject(validation([{loc:['body','source','secret'],type:'literal_error'},
    {loc:['body','__proto__'],type:'missing'},{loc:['query','source'],type:'literal_error'},
    {loc:['body','unknown_field'],type:'missing'}]));await tick();
  assert.ok(h.page.data.fields.every(f=>!f.validationError));
  assert.equal(h.page.data.submissionError,'提交内容未通过校验，请检查填写内容后重试');
  assert.doesNotMatch(JSON.stringify(h.page.data)+JSON.stringify(h.toasts),/SECRET|__proto__|unknown_field/);
  assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
});

test('cancel and identity change before confirmation still send zero requests',async()=>{
  for(const mode of ['cancel','identity']){
    const h=pageHarness();h.page.submitCustomer();
    if(mode==='identity')h.session.userId='user2';
    h.modals[0].success({confirm:mode!=='cancel'});assert.equal(h.calls.length,0);
    assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
  }
});

test('new submission clears old errors before confirmation without clearing draft or changing cancel semantics',()=>{
  const h=pageHarness();
  h.page.setData({submissionError:'旧错误',fields:h.page.data.fields.map(f=>({...f,validationError:'旧错误'}))});
  h.page.submitCustomer();
  assert.equal(h.page.data.submissionError,'');
  assert.ok(h.page.data.fields.every(f=>!f.validationError));
  h.modals[0].success({confirm:false});
  assert.equal(h.calls.length,0);assert.equal(h.page.data.submitting,false);
  assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
});

test('identity reload clears old validation errors and restores no other users draft',async()=>{
  const h=pageHarness();
  h.page.setData({submissionError:'旧错误',fields:h.page.data.fields.map(f=>({...f,validationError:'旧错误'}))});
  h.session.userId='user2';
  await h.page.onLoad();await tick();
  assert.equal(h.page.data.submissionError,'');assert.ok(h.page.data.fields.every(f=>!f.validationError));
  assert.equal(h.page.data.fields.find(f=>f.key==='customer_name').value,'');
  assert.equal(h.calls.length,0);
  assert.equal(JSON.stringify(h.storage.get('customerCreateDraft:company1:user1')),h.draft);
});

test('late error after account change does not update the new account, and edited values do not receive stale field errors',async()=>{
  for(const change of ['account','field']){
    const h=pageHarness();h.page.submitCustomer();h.modals[0].success({confirm:true});
    if(change==='account')h.session.userId='user2';
    else h.page.refresh(h.page.data.fields.map(f=>f.key==='lead_source'?{...f,value:'合作伙伴'}:f));
    h.reject(validation([{loc:['body','source'],type:'literal_error'}]));await tick();
    assert.ok(h.page.data.fields.every(f=>!f.validationError));
    if(change==='account')assert.equal(h.page.data.submissionError,'');
    assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
  }
});

test('restored, voice and manual legacy values remain visible, flagged and unsubmitted instead of being silently translated',async()=>{
 for(const mode of ['restored','voice','manual'])for(const [key,value] of [
   ['lead_source','公司分配'],['lead_source','自主拓展'],['lead_source','未来来源'],
   ['customer_type','新拓客户'],['customer_type','存量客户'],
 ]){
  const h=pageHarness();h.page.session=h.session;
  if(mode==='restored'){
   const draft=structuredClone(h.storage.get(h.page.draftKey));draft.fields.find(f=>f.key===key).value=value;
   h.storage.set(h.page.draftKey,draft);await h.page.onLoad();await tick();
  }else if(mode==='voice')h.page.applyVoiceDraft({[key==='lead_source'?'source':key]:value});
  else{
   h.page.setData({editorIndex:h.page.data.fields.findIndex(f=>f.key===key),editorValue:value});h.page.saveEditor();
  }
  const field=h.page.data.fields.find(f=>f.key===key);assert.equal(field.value,value);
  assert.match(field.validationError,/请选择有效选项/);
  h.page.submitCustomer();assert.equal(h.calls.length,0);assert.equal(h.modals.length,0);
  h.page.saveDraft();assert.equal(h.storage.get(h.page.draftKey).fields.find(f=>f.key===key).value,value);
 }
});

test('missing creation section refreshes exactly once then recovers without losing draft',async()=>{
 const old=structuredClone(fixture);delete old.customer_creation;
 const h=pageHarness({metadata:async(options,n)=>n===1?old:fixture});
 await h.page.onLoad();await tick();
 assert.deepEqual(plain(h.metadataCalls),[{}, {fresh:true}]);
 assert.equal(h.page.data.creationOptionsReady,true);assert.equal(h.page.data.creationOptionsError,'');
 assert.equal(h.page.data.fields.find(f=>f.key==='customer_name').value,'合成客户');
 assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);assert.equal(h.calls.length,0);
});

test('real old server stays blocked without a request loop, manual refresh retains unsaved values and later recovers',async()=>{
 const old=structuredClone(fixture);delete old.customer_creation;let upgraded=false;
 const h=pageHarness({metadata:async()=>upgraded?fixture:old});
 await h.page.onLoad();await tick();assert.equal(h.metadataCalls.length,2);
 assert.equal(h.page.data.creationOptionsReady,false);assert.match(h.page.data.creationOptionsError,/请刷新后重试/);
 h.page.refresh(h.page.data.fields.map(f=>f.key==='customer_name'?{...f,value:'未保存的新名称'}:f));
 h.page.submitCustomer();assert.equal(h.calls.length,0);assert.equal(h.modals.length,0);assert.equal(h.metadataCalls.length,2);
 assert.ok(catalog.customer.source.includes('公司分配'),'historical catalog remains usable');
 await h.page.refreshCreationOptions();assert.equal(h.metadataCalls.length,3);assert.equal(h.page.data.creationOptionsReady,false);
 upgraded=true;await h.page.refreshCreationOptions();assert.equal(h.metadataCalls.length,4);
 assert.equal(h.page.data.creationOptionsReady,true);assert.equal(h.page.data.creationOptionsError,'');
 assert.equal(h.page.data.fields.find(f=>f.key==='customer_name').value,'未保存的新名称');
 assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);assert.equal(h.calls.length,0);
});

test('refresh failure and stale identity cannot unlock submission or erase draft, repeated refresh taps coalesce',async()=>{
 for(const kind of ['failure','identity']){
  let resolve,reject;const pending=new Promise((yes,no)=>{resolve=yes;reject=no;});
  const h=pageHarness({metadata:()=>pending});const refresh=h.page.refreshCreationOptions();
  await h.page.refreshCreationOptions();assert.equal(h.metadataCalls.length,1);
  h.page.submitCustomer();assert.equal(h.calls.length,0);
  if(kind==='identity'){h.session.userId='other';resolve(fixture);}else reject(Error('network failure'));
  await refresh;assert.equal(h.page.data.creationOptionsReady,false);
  h.page.submitCustomer();assert.equal(h.calls.length,0);assert.equal(h.modals.length,0);
  assert.equal(JSON.stringify(h.storage.get(h.page.draftKey)),h.draft);
  if(kind==='failure')assert.equal(h.page.data.creationOptionsRefreshing,false);
 }
});
