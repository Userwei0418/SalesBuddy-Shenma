const options=require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const draft=require('../miniprogram/utils/visitDraft'),snapshot=require('../miniprogram/utils/visitSnapshot');
const clone=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const fields={follow_up_record:'合成沟通记录，完整保留正文。',next_action:'10月21日测试销售发送方案',
  interaction_at:'2026-10-20',created_date:'2026-10-20',contact_name:'合成联系人'};

// In-memory transport only: exercise the real page, draft store and API idempotency.
function setup(){
  const storage=new Map(),calls=[],toasts=[];
  const control={status:500,message:'INTERNAL_ERROR',network:false,storageFails:false};
  const wx={getStorageSync:key=>clone(storage.get(key)),
    setStorageSync(key,value){if(control.storageFails&&key.startsWith('visitConfirmV2:'))throw Error('storage unavailable');storage.set(key,clone(value));},
    removeStorageSync:key=>storage.delete(key),showToast:value=>toasts.push(value.title),setNavigationBarTitle(){},
    request(request){
      calls.push({url:request.url,method:request.method,key:request.header['Idempotency-Key'],body:clone(request.data)});
      assert.equal(new URL(request.url).pathname,'/api/v1/visits');
      assert.equal(request.method,'POST');
      if(control.network)return request.fail({errMsg:'request:fail timeout'});
      request.success({statusCode:control.status,data:control.status===200?{id:'synthetic-visit',fields}:{detail:control.message}});
    }};
  global.wx=wx;
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];
  const api=require('../miniprogram/utils/apiClient');
  wx.setStorageSync('salesApiBaseUrl','https://synthetic.invalid/api/v1');
  api.saveAuth({access_token:'synthetic-only',actor:{workspace_id:'synthetic-workspace',user_id:'synthetic-user'}});
  options.catalog.install(options.value);
  const app={globalData:{session:{workspaceId:'synthetic-workspace',userId:'synthetic-user'}}};
  let definition;
  const file=path.resolve(__dirname,'../miniprogram/pages/visit-confirm/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,wx,getApp:()=>app,
    require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(file),name))});
  const userKey=draft.scope(app.globalData.session,api),draftKey=draft.key('visitConfirmV2',userKey);
  const page={...definition,data:clone(definition.data),userKey,draftKey,draftReady:true,entryDraftId:'synthetic-draft',
    setData(values){Object.assign(this.data,values);}};
  Object.assign(page.data,{isFde:true,values:clone(fields),customerId:'synthetic-customer',customerName:'合成客户',customerConfirmed:true,
    sourceRunId:'synthetic-source',reviewRunId:'synthetic-quality',reviewPayload:{fields:clone(fields)},flowStep:'result',
    quality:{follow_up_score:85,next_action:{passed:true}}});
  page.data.reviewedContent=snapshot.signature(page.data);page.refresh();
  assert.equal(page.data.canSubmit,true);
  return {page,storage,calls,toasts,control,draftKey};
}
function retained(h){
  assert.equal(h.page.data.archived,false);assert.equal(h.page.data.busy,false);
  assert.equal(h.page.data.canSubmit,true);assert.equal(h.page.data.flowStep,'result');
  assert.equal(h.page.data.reviewRunId,'synthetic-quality');assert.equal(h.page.data.quality.follow_up_score,85);
  assert.deepEqual(clone(h.page.data.values),fields);
  const saved=h.storage.get(h.draftKey);
  assert.deepEqual(saved.values,fields);assert.equal(saved.reviewRunId,'synthetic-quality');
  assert.equal(saved.quality.follow_up_score,85);assert.equal(saved.reviewPayload.fields.follow_up_record,fields.follow_up_record);
  assert.equal(h.toasts.at(-1),h.page.data.errorText);
}

test('generic archive server failures retain the reviewed draft and require checking the uncertain result',async()=>{
  for(const [status,message] of [[500,'INTERNAL_ERROR'],[500,'Internal Server Error'],[503,undefined],[500,'服务端异常']]){
    const h=setup();Object.assign(h.control,{status,message});
    await h.page.submitArchive();await tick();retained(h);
    assert.match(h.page.data.errorText,/暂未确认保存结果.*草稿已保留.*核对处理结果.*勿重复提交/);
    assert.equal(h.calls.length,1);
  }
});
test('lost archive response stays uncertain; only manual retry resends the same payload and idempotency key',async()=>{
  const h=setup();h.control.network=true;
  await h.page.submitArchive();await tick();retained(h);
  assert.match(h.page.data.errorText,/暂未确认保存结果，草稿已保留/);
  assert.doesNotMatch(h.page.data.errorText,/未保存|未落库|归档失败/);
  assert.equal(h.calls.length,1);assert.match(h.calls[0].key,/^[a-f0-9-]{36}$/);
  h.control.network=false;h.control.status=200;
  await h.page.submitArchive();await tick();
  assert.equal(h.calls.length,2);assert.equal(h.calls[1].key,h.calls[0].key);assert.deepEqual(h.calls[1].body,h.calls[0].body);
  assert.equal(h.page.data.archived,true);assert.equal(h.page.data.errorText,'');assert.equal(h.storage.has(h.draftKey),false);
});
test('specific permission and validation errors remain unchanged',async()=>{
  for(const [status,message] of [[403,'当前无权归档此客户拜访'],[422,'质检正文与提交内容不一致，请重新核对']]){
    const h=setup();Object.assign(h.control,{status,message});
    await h.page.submitArchive();await tick();retained(h);
    assert.equal(h.page.data.errorText,message);
  }
});
test('draft storage failure blocks archive and never claims the draft was saved',async()=>{
  const h=setup();h.control.storageFails=true;
  await h.page.submitArchive();await tick();
  assert.equal(h.calls.length,0);assert.match(h.page.data.draftSaveError,/草稿未能保存/);
  assert.equal(h.page.data.errorText,'');assert.equal(h.toasts.length,0);
});
