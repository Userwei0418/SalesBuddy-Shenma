require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const identity = require('../miniprogram/utils/requestIdentity');
const flow = require('../miniprogram/utils/visitFlow');
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
const fields = {follow_up_record:'合成试点材料待核实',next_action:'10月12日测试销售提交样本清单',
  interaction_at:'2026-10-09',created_date:'2026-10-10',contact_name:'合成客户代表'};

// A receipt-aware synthetic transport. It proves client keys/run reuse, not server DB uniqueness.
function transport() {
  const storage=new Map(), calls=[], receipts=new Map();let sequence=0;
  const control={dropPost:false,readError:false,runStatus:'waiting_human'};
  const wx={getStorageSync:key=>clone(storage.get(key)),setStorageSync:(key,value)=>storage.set(key,clone(value)),
    removeStorageSync:key=>storage.delete(key),pageScrollTo(){},showToast(){},setNavigationBarTitle(){},
    request:options=>{
      const route=new URL(options.url).pathname.replace('/api/v1','');
      calls.push({route,method:options.method || 'GET',body:clone(options.data),key:options.header['Idempotency-Key']});
      if(options.method==='POST') {
        assert.ok(['/visit-flow/structure','/visit-flow/quality'].includes(route));
        const key=options.header['Idempotency-Key'];assert.match(key,/^[a-f0-9-]{36}$/);
        let accepted=receipts.get(key);
        if(!accepted){accepted={run_id:`run-${++sequence}`};receipts.set(key,accepted);}
        if(control.dropPost)return options.fail({errMsg:'accepted response lost'});
        return options.success({statusCode:202,data:accepted});
      }
      assert.match(route,/^\/agent\/runs\/run-/);
      if(control.readError)return options.fail({errMsg:'read disconnected'});
      options.success({statusCode:200,data:{id:route.split('/').at(-1),status:control.runStatus,error_code:'NETWORK_ERROR',
        result:{visit_stage:'quality',fields,quality_review:{follow_up_score:85,next_action:{passed:true}}}}});
    }};
  global.wx=wx;
  const load=()=>{delete require.cache[require.resolve('../miniprogram/utils/apiClient')];return require('../miniprogram/utils/apiClient');};
  const api=load();api.saveAuth({access_token:'synthetic-only',actor:{workspace_id:'w',user_id:'u'}});
  return {api,load,wx,storage,calls,receipts,control};
}

function page(stage, h) {
  let definition;
  const file=path.resolve(__dirname,`../miniprogram/pages/${stage==='structure'?'visit-entry':'visit-confirm'}/index.js`);
  const app={globalData:{session:{workspaceId:'w',userId:'u',role:'sales'}},ensureLogin:()=>true};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:value=>definition=value,getApp:()=>app,wx:h.wx,
    require:name=>name.endsWith('apiClient')?h.api:require(path.resolve(path.dirname(file),name)),
    setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout});
  const scope=require('../miniprogram/utils/visitDraft').scope(app.globalData.session,h.api);
  const p={...definition,data:clone(definition.data),userKey:scope,draftKey:`${stage==='structure'?'visitEntryV2':'visitConfirmV2'}:${scope}`,
    draftReady:true,draftId:'synthetic-draft',entryDraftId:'synthetic-draft',setData(values){Object.assign(this.data,values);}};
  if(stage==='quality')Object.assign(p.data,{values:clone(fields),customerId:'customer',customerName:'合成客户',customerConfirmed:true,
    sourceRunId:'source-run',collaboratorIds:[]},flow.restoreReview({},h.storage.get(p.draftKey) || {values:clone(fields)}));
  return p;
}
const structureBody = {customer_id:'customer',text:'合成试点材料待核实',source_import_id:'source-import'};
const posts=h=>h.calls.filter(call=>call.method==='POST');

test('only two exact visit stage POSTs join request identity',()=>{
  for(const route of ['/visit-flow/structure','/visit-flow/quality'])assert.equal(identity.supports({path:route,method:'POST'}),true);
  for(const [method,route] of [['GET','/visit-flow/structure'],['PUT','/visit-flow/quality'],['POST','/visit-flow/structure/extra'],
    ['POST','/visit-flow/quality?x=1'],['POST','/conversations'],['POST','/conversations/c/messages'],['POST','/advice']]) {
    assert.equal(identity.supports({path:route,method}),false);
  }
});

for(const stage of ['structure','quality'])test(`${stage}: lost first202 persists key across module reload; body/identity/new intent remain separate`,async()=>{
  const h=transport(),body=stage==='structure'?structureBody:{customer_id:'customer',source_run_id:'source-run',fields,summary:'合成'};
  h.control.dropPost=true;await assert.rejects(h.api.submitVisitStage(stage,body),/response lost/);
  const first=posts(h)[0].key;assert.equal(h.receipts.size,1);
  h.api=h.load();h.control.dropPost=false;
  const accepted=await h.api.submitVisitStage(stage,body);assert.equal(accepted.run_id,'run-1');assert.equal(posts(h)[1].key,first);assert.equal(h.receipts.size,1);
  await h.api.submitVisitStage(stage,body);assert.notEqual(posts(h)[2].key,first); // successful prior intent has ended
  h.control.dropPost=true;await assert.rejects(h.api.submitVisitStage(stage,body));const unresolved=posts(h).at(-1).key;
  const changed=stage==='structure'?{...body,text:body.text+' 已修改'}:{...body,fields:{...fields,next_action:'10月13日改为技术人员核对'}};
  await assert.rejects(h.api.submitVisitStage(stage,changed));assert.notEqual(posts(h).at(-1).key,unresolved);
  await assert.rejects(h.api.submitVisitStage(stage,body));assert.equal(posts(h).at(-1).key,unresolved);
  h.api.saveAuth({access_token:'synthetic-other',actor:{workspace_id:'w',user_id:'other'}});
  await assert.rejects(h.api.submitVisitStage(stage,body));assert.notEqual(posts(h).at(-1).key,unresolved);
  assert.ok(!JSON.stringify(h.storage.get(identity.STORAGE_KEY)).includes('合成'));
});

test('structure: accepted run survives read disconnection/page recreation; edited body gets a distinct run',async()=>{
  const h=transport();let p=page('structure',h);h.control.readError=true;
  await assert.rejects(p.structureVisit(structureBody),error=>error.code==='NETWORK_ERROR' && error.runStatus===undefined);
  assert.equal(h.storage.get(`visitStructureRun:${p.userKey}`).id,'run-1');
  h.api=h.load();p=page('structure',h);h.control.readError=false;
  const result=await p.structureVisit(structureBody);assert.equal(result.id,'run-1');assert.equal(posts(h).length,1);
  await p.structureVisit({...structureBody,text:'明确修改后的另一份原文'});assert.equal(posts(h).length,2);assert.equal(h.storage.get(`visitStructureRun:${p.userKey}`).id,'run-2');
});

test('quality: accepted run persists in draft through disconnection; restore re-reads it, edit re-reviews',async()=>{
  const h=transport();let p=page('quality',h);h.control.readError=true;await p.review();
  assert.equal(p.data.pendingReviewId,'run-1');assert.equal(h.storage.get(p.draftKey).pendingReviewId,'run-1');assert.equal(p.data.canSubmit,false);
  h.api=h.load();p=page('quality',h);h.control.readError=false;await p.review();
  assert.equal(posts(h).length,1);assert.equal(p.data.reviewRunId,'run-1');assert.equal(p.data.canSubmit,true);
  p.data.values.next_action='10月14日测试人员核对新增事实';p.refreshGate();assert.equal(p.data.canSubmit,false);
  await p.review();assert.equal(posts(h).length,2);assert.equal(p.data.reviewRunId,'run-2');
});

for(const stage of ['structure','quality'])for(const terminal of ['failed','cancelled'])test(`${stage}: observed ${terminal} clears only this accepted run and permits explicit new attempt`,async()=>{
  const h=transport(),p=page(stage,h);h.control.runStatus=terminal;
  if(stage==='structure') {
    await assert.rejects(p.structureVisit(structureBody),error=>error.runStatus===terminal && error.code==='NETWORK_ERROR');
    assert.equal(h.storage.has(`visitStructureRun:${p.userKey}`),false);
  } else {await p.review();assert.equal(p.data.pendingReviewId,'');assert.equal(p.data.canSubmit,false);}
  h.control.runStatus='waiting_human';
  if(stage==='structure')await p.structureVisit(structureBody);else await p.review();
  assert.equal(posts(h).length,2);assert.notEqual(posts(h)[0].key,posts(h)[1].key);
});
