const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function pageHarness(route,api){
  const app={globalData:{role:'sales',session:{workspaceId:'w',userId:'u',role:'sales',scope:'self',loginAt:1}},can:()=>true};
  const events=[],storage=new Map(),wx={showToast:o=>events.push(o.title),vibrateShort(){},navigateTo:o=>events.push(o.url),
    getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k)};
  const filename=path.resolve(__dirname,`../miniprogram/pages/${route}/index.js`);let page;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,wx,setTimeout,clearTimeout,setInterval,clearInterval,
    require:n=>n.endsWith('apiClient')?api:n==='../../config'?{HOME_CHATBI_ENABLED:true}:require(path.resolve(path.dirname(filename),n))});
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=(u,cb)=>{Object.assign(page.data,u);if(cb)cb();};
  return {page,app,events};
}
test('home exposes pending tasks and resume passes the original receipt instead of a new request',async()=>{
  const calls=[],receipt={kind:'today_tasks',key:'same-key',runId:'same-run',meta:{}};
  const h=pageHarness('index',{pendingAgentRuns:()=>[receipt],queryTodayTasks:async options=>{calls.push(options);throw Error('仍在处理，请继续查看结果');}});
  await h.page.runTodayTasksAgent();assert.equal(h.page.data.pendingAgents[0].key,'same-key');
  await h.page.resumeHomeAgent({currentTarget:{dataset:{key:'same-key'}}});
  assert.equal(calls[1].recoveryKey,'same-key');
  receipt.runId='';h.page.refreshPendingAgents();h.page.resumeHomeAgent({currentTarget:{dataset:{key:'same-key'}}});
  assert.equal(calls.length,2);assert.match(h.events.at(-1),/核对/);
});
test('customer voice resume queries the old run and never overwrites fields edited while waiting',async()=>{
  const pending=deferred(),calls=[],receipt={key:'voice-key',runId:'voice-run',kind:'customer_create'};
  const h=pageHarness('customer-create',{pendingAgentRuns:()=>[receipt],resumePendingAgent:key=>{calls.push(key);return pending.promise;}});
  h.page.draftKey='customerCreateDraft:w:u';h.page.data.fields=[{key:'customer_name',value:'original'}];
  receipt.contextSignature=h.page.voiceFormSignature();let applied=0;h.page.applyVoiceDraft=()=>applied++;
  const result=h.page.resumeVoiceDraft();h.page.data.fields[0].value='edited';pending.resolve({result:{customer_name:'old'}});await result;
  assert.equal(calls[0],'voice-key');assert.equal(applied,0);assert.equal(h.page.data.fields[0].value,'edited');assert.match(h.events.at(-1),/旧结果未覆盖/);
});
test('uncertain opportunity draft keeps the original text and blocks forwarding until the same input is recovered',async()=>{
  let signature='';const receipt={kind:'opportunity_draft',key:'draft-key',runId:'draft-run'};
  const h=pageHarness('visit-entry',{pendingAgentRuns:()=>[receipt],runAgent:async(mode,text,id,opportunity,options)=>{
    signature=options.contextSignature;receipt.contextSignature=signature;throw Object.assign(Error('继续查看结果'),{runId:'draft-run',recoveryKey:'draft-key',recoveryAction:'resume_run'});
  }});
  h.page.draftId='draft';h.page.data.customerId='customer';h.page.data.transcript='original visit';h.page.persist=()=>true;h.page.operationToken=()=>({});h.page.operationCurrent=()=>true;
  h.page.structureVisit=async()=>({result:{},id:'structure-run'});
  await h.page.submitTranscript();assert.equal(h.page.data.transcript,'original visit');assert.equal(h.page.data.opportunityRecoveryPending,true);
  assert.match(h.page.data.errorText,/继续查看/);assert.ok(!h.events.some(x=>x.startsWith('/pages/visit-confirm')));
  const first=signature;await h.page.submitTranscript();assert.equal(signature,first);
});

test('an unknown voice receipt blocks only its original form; changed input can start recording',()=>{
  let asked=0;const receipt={key:'old',runId:'',kind:'customer_create',contextSignature:'a'.repeat(64)};
  const h=pageHarness('customer-create',{pendingAgentRuns:()=>[receipt]});
  h.page.draftKey='draft';h.page.data.fields=[{key:'customer_name',value:'original'}];receipt.contextSignature=h.page.voiceFormSignature();
  h.page.requestRecordPermission=()=>asked++;
  h.page.toggleVoice();assert.equal(asked,0);
  h.page.data.fields[0].value='new customer';h.page.toggleVoice();assert.equal(asked,1);
  assert.equal(h.page.data.voiceOtherRecoveries[0].key,'old');
});
test('permission changes release old thinking state without an old callback clearing the next action',async()=>{
  const first=deferred(),second=deferred();let calls=0;
  const h=pageHarness('index',{pendingAgentRuns:()=>[],queryPersonalRisks:()=>++calls===1?first.promise:second.promise,
    getAssistantHome:async()=>({}),listCustomers:async()=>({items:[]})});
  h.app.globalData.roles={sales:{name:'sales',scope:'self'}};h.page.refreshHomeTaskState=async()=>{};
  const old=h.page.runPersonalRiskAgent();assert.equal(h.page.data.isThinking,true);
  h.app.globalData.session.permissionVersion='v2';await h.page.syncRole();assert.equal(h.page.data.isThinking,false);
  const next=h.page.runPersonalRiskAgent();first.reject(Object.assign(Error('changed'),{code:'SESSION_CHANGED'}));await old;
  assert.equal(h.page.data.isThinking,true);second.reject(Error('done'));await next;assert.equal(h.page.data.isThinking,false);
});
test('voice result is acknowledged only after application, never after the consumer page unloads',async()=>{
  for(const unload of [false,true]){
    const pending=deferred(),receipt={key:'voice-key',runId:'voice-run',kind:'customer_create'};let ack=0,applied=0;
    const h=pageHarness('customer-create',{pendingAgentRuns:()=>[receipt],resumePendingAgent:()=>pending.promise,acknowledgeAgentResult:()=>ack++});
    h.page.draftKey='voice-draft';h.page.data.fields=[{key:'customer_name',value:'same'}];receipt.contextSignature=h.page.voiceFormSignature();
    h.page.applyVoiceDraft=()=>applied++;const operation=h.page.resumeVoiceDraft();if(unload)h.page.onUnload();
    pending.resolve({result:{},recoveryKey:'voice-key'});await operation;
    assert.equal(ack,unload?0:1);assert.equal(applied,unload?0:1);
  }
});
