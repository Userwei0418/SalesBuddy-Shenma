const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const policy = require('../miniprogram/utils/requestPolicy');
const flush = () => new Promise(resolve => setImmediate(resolve));
function harness() {
  const storage = new Map(), requests = [], timers = []; let now = 0;
  const wx = {getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>{requests.push(r);return {abort(){}};}};
  global.wx = wx;
  const filename = path.resolve(__dirname,'../miniprogram/utils/apiClient.js'), module = {exports:{}};
  class Clock extends Date {static now(){return now;}}
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,wx,Map,Set,Date:Clock,
    require:n=>require(path.resolve(path.dirname(filename),n)),setTimeout:(fn,ms)=>timers.push({fn,ms}),clearTimeout(){}});
  const api=module.exports;
  api.saveAuth({access_token:'test-only',actor:{workspace_id:'test-workspace',user_id:'test-user'}});
  return {api,requests,timers,storage,advance:ms=>{now+=ms;}};
}
test('aggregate GET budgets are explicit; writes, lightweight reads and uploads keep their budgets',()=>{
  for(const p of ['/customer-assets/map?offset=50','/customer-assets','/customers?scope=team','/customers/claim-pool','/customers/portfolio','/profile/performance','/dashboard','/opportunities']) assert.equal(policy.timeoutFor({path:p}),45000,p);
  for(const p of ['/auth/me','/agent/runs/id','/directory/members','/tasks','/customer-assets/map/refresh']) assert.equal(policy.timeoutFor({path:p}),15000,p);
  assert.equal(policy.timeoutFor({path:'/customers',method:'POST'}),15000);
  assert.equal(policy.timeoutFor({path:'/customer-assets/map',timeout:20000}),20000);
});
test('map transport timeout does not retry; submission timeout preserves the same idempotency key',async()=>{
  const h=harness();
  const map=assert.rejects(h.api.request({path:'/customer-assets/map'}),e=>e.timeout&&e.retryable&&!e.outcomeUnknown&&e.recoveryAction==='retry_read');
  assert.equal(h.requests[0].timeout,45000);h.requests[0].fail({errMsg:'request:fail fail:time out'});await map;
  assert.equal(h.requests.length,1);assert.equal(h.timers.length,0);
  const options={path:'/tasks',method:'POST',data:{description:'synthetic'}};
  const first=assert.rejects(h.api.request(options),e=>e.outcomeUnknown&&!e.retryable&&e.recoveryAction==='check_status');
  const key=h.requests[1].header['Idempotency-Key'];h.requests[1].fail({errMsg:'timeout'});await first;
  const second=h.api.request(options);assert.equal(h.requests[2].header['Idempotency-Key'],key);
  h.requests[2].success({statusCode:201,data:{id:'synthetic'}});await second;
});
test('parallel directory reads share one bounded HTTP retry, honor cooldown, and retain error receipt',async()=>{
  const h=harness(), options={path:'/directory/members'};
  const a=h.api.request(options),b=h.api.request(options);
  const done=assert.rejects(Promise.all([a,b]),e=>e.statusCode===503&&e.requestId==='request-test-1'&&e.recoveryAction==='retry_read');
  assert.equal(h.requests.length,1);
  h.requests[0].success({statusCode:503,data:{code:'DATABASE_BUSY',retryable:true,request_id:'request-test-1'},header:{'Retry-After':'5'}});
  await flush();assert.equal(h.timers.length,1);assert.ok(h.timers[0].ms>=5000&&h.timers[0].ms<5250);
  h.timers.shift().fn();await flush();assert.equal(h.requests.length,2);
  h.requests[1].success({statusCode:503,data:{code:'DATABASE_BUSY',request_id:'request-test-1'}});
  await done;assert.equal(h.timers.length,0);
});
test('long Retry-After, timeout, denied access and mutation 504 never trigger automatic replay',async()=>{
  for(const row of [
    {path:'/directory/members',status:429,header:{'Retry-After':'60'}},
    {path:'/directory/members',status:403},
    {path:'/customer-assets/map',status:504},
    {path:'/customer-assets/map/refresh',method:'POST',status:504},
  ]){
    const h=harness(),done=assert.rejects(h.api.request(row),e=>e.statusCode===row.status&&(!row.method||e.outcomeUnknown&&e.recoveryAction==='check_status'));
    h.requests[0].success({statusCode:row.status,header:row.header,data:{retryable:true}});await done;
    assert.equal(h.requests.length,1);assert.equal(h.timers.length,0);
  }
});
test('session change during backoff prevents an old request from running for the new account',async()=>{
  const h=harness(),done=assert.rejects(h.api.request({path:'/directory/members'}),e=>e.code==='SESSION_CHANGED');
  h.requests[0].success({statusCode:503,data:{retryable:true}});await flush();
  h.api.saveAuth({access_token:'other-test',actor:{workspace_id:'other',user_id:'other'}});
  h.timers.shift().fn();await done;assert.equal(h.requests.length,1);
});
test('different budgets and binary/JSON response contracts never share an in-flight transport',async()=>{
  const h=harness(),a=h.api.request({path:'/example',timeout:1000}),b=h.api.request({path:'/example',timeout:2000}),c=h.api.request({path:'/example',timeout:2000,includeHeaders:true});
  assert.equal(h.requests.length,3);
  h.requests.forEach(r=>r.success({statusCode:200,data:{ok:true}}));await Promise.all([a,b,c]);
});
test('Agent wait timeout carries the same resumable run and never resubmits a model job',async()=>{
  const h=harness(),done=assert.rejects(h.api.resumeRun('existing-run',{timeout:1000}),e=>e.code==='RUN_TIMEOUT'&&e.runId==='existing-run'&&e.recoveryAction==='resume_run');
  h.advance(1001);h.requests[0].success({statusCode:200,data:{status:'running'}});await done;
  const resumed=h.api.resumeRun('existing-run');h.requests[1].success({statusCode:200,data:{status:'succeeded',result:{ok:true}}});await resumed;
  assert.ok(h.requests.every(r=>r.method==='GET'&&r.url.endsWith('/agent/runs/existing-run')));
});
test('Agent polling stops before issuing another request after logout',async()=>{
  const h=harness(),done=assert.rejects(h.api.resumeRun('existing-run'),e=>e.code==='SESSION_CHANGED');
  h.requests[0].success({statusCode:200,data:{status:'running'}});await flush();
  h.api.saveAuth(null);h.timers.shift().fn();await done;assert.equal(h.requests.length,1);
});
test('accepted AI receipt survives a polling failure; repeated input resumes GET without another model job',async()=>{
  const h=harness(),original='synthetic private prompt';
  const first=assert.rejects(h.api.queryChatBI(original),e=>e.runId==='run-recover'&&e.recoveryAction==='resume_run');
  h.requests[0].success({statusCode:200,data:{id:'conversation-1'}});await flush();
  h.requests[1].success({statusCode:202,data:{run_id:'run-recover'}});await flush();
  assert.equal(h.api.pendingAgentRuns()[0].runId,'run-recover');
  assert.ok(!JSON.stringify(h.storage.get('salesPendingAgentRuns:v1')).includes(original));
  h.requests[2].fail({errMsg:'timeout'});await first;
  const retry=h.api.queryChatBI(original);assert.equal(h.requests.length,4);
  assert.equal(h.requests[3].method,'GET');assert.ok(h.requests[3].url.endsWith('/agent/runs/run-recover'));
  h.requests[3].success({statusCode:200,data:{status:'succeeded',result:{ok:true}}});const recovered=await retry;
  assert.equal(h.api.pendingAgentRuns().length,1,'keep success until page consumption');
  h.api.acknowledgeAgentResult(recovered.recoveryKey);assert.equal(h.api.pendingAgentRuns().length,0);
});
test('lost AI acceptance never recreates the same input; changed input and accounts are isolated',async()=>{
  const h=harness(),first=assert.rejects(h.api.queryChatBI('first input'),e=>e.code==='AGENT_ACCEPTANCE_UNKNOWN');
  h.requests[0].success({statusCode:200,data:{id:'conversation-1'}});await flush();
  h.requests[1].fail({errMsg:'timeout'});await first;
  await assert.rejects(h.api.queryChatBI('first input'),e=>e.code==='AGENT_ACCEPTANCE_UNKNOWN');
  assert.equal(h.requests.length,2);const key=h.api.pendingAgentRuns()[0].key;
  const changed=h.api.queryChatBI('changed input');assert.equal(h.requests.length,3);
  const changedDone=assert.rejects(changed,e=>e.statusCode===403);
  h.requests[2].success({statusCode:403,data:{detail:'denied'}});await changedDone;
  h.api.saveAuth({access_token:'test-other',actor:{workspace_id:'other',user_id:'other'}});
  assert.equal(h.api.pendingAgentRuns().length,0);
  await assert.rejects(h.api.resumePendingAgent(key),e=>e.code==='AGENT_RECOVERY_MISSING');
  assert.equal(h.requests.length,3);
});
test('parallel identical AI actions share creation and a session change cannot send an old conversation as new user',async()=>{
  const h=harness(),a=h.api.queryTodayTasks(),b=h.api.queryTodayTasks();assert.equal(a,b);assert.equal(h.requests.length,1);
  const done=assert.rejects(a,e=>e.code==='SESSION_CHANGED');
  h.api.saveAuth({access_token:'test-other',actor:{workspace_id:'other',user_id:'other'}});
  h.requests[0].success({statusCode:200,data:{id:'old-conversation'}});await done;
  assert.equal(h.requests.length,1);
});
test('completed opportunity draft keeps its receipt when a parallel visit step may still fail',async()=>{
  const h=harness(),options={contextSignature:'a'.repeat(64)};
  const first=h.api.runAgent('opportunity_draft','same source','customer',null,options);
  h.requests[0].success({statusCode:200,data:{id:'conversation'}});await flush();
  h.requests[1].success({statusCode:202,data:{run_id:'draft-run'}});await flush();
  h.requests[2].success({statusCode:200,data:{status:'succeeded',result:{}}});await first;
  const again=h.api.runAgent('opportunity_draft','same source','customer',null,options);
  assert.equal(h.requests.length,4);assert.equal(h.requests[3].method,'GET');
  assert.ok(h.requests[3].url.endsWith('/agent/runs/draft-run'));
  h.requests[3].success({statusCode:200,data:{status:'succeeded',result:{}}});await again;
});

test('failed conversation creation never marks a model job unknown and permits a manual retry',async()=>{
  const h=harness(),first=assert.rejects(h.api.queryChatBI('same input'),e=>e.code==='AGENT_NOT_SUBMITTED'&&!e.outcomeUnknown);
  h.requests[0].fail({errMsg:'timeout'});await first;assert.equal(h.api.pendingAgentRuns().length,0);
  const retry=assert.rejects(h.api.queryChatBI('same input'),e=>e.statusCode===403);
  assert.equal(h.requests.length,2);assert.ok(h.requests[1].url.endsWith('/conversations'));
  h.requests[1].success({statusCode:403,data:{detail:'denied'}});await retry;
});
