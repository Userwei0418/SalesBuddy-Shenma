const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const messages = require('../miniprogram/utils/errorMessages');
const policy = require('../miniprogram/utils/requestPolicy');

function harness() {
  const requests = [], uploads = [], storage = new Map();
  const wx = {getStorageSync:key=>storage.get(key), setStorageSync:(key,value)=>storage.set(key,value),
    removeStorageSync:key=>storage.delete(key), request:request=>requests.push(request),
    uploadFile:request=>{uploads.push(request);return {};}};
  global.wx = wx;
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js'), module = {exports:{}};
  vm.runInNewContext(fs.readFileSync(filename,'utf8'), {module, wx, Map, Set, Date, setTimeout, clearTimeout,
    require:name=>require(path.resolve(path.dirname(filename),name))});
  const api = module.exports;
  api.saveAuth({access_token:'synthetic-test-only', actor:{workspace_id:'synthetic',user_id:'synthetic-user'}});
  return {api, requests, uploads};
}

test('new and legacy error envelopes preserve business code and use registered Chinese copy', async () => {
  for (const body of [{code:'TASK_EXECUTION_FORBIDDEN',message:'field secret'},
    {detail:{code:'TASK_EXECUTION_FORBIDDEN',message:'raw SQL'}}, {detail:'TASK_EXECUTION_FORBIDDEN'}]) {
    const h = harness();
    const done = assert.rejects(h.api.request({path:'/tasks/id/execution'}), error =>
      error.code === 'HTTP_403' && error.statusCode === 403 && error.serverCode === 'TASK_EXECUTION_FORBIDDEN'
      && error.message === '当前账号没有执行此任务的权限' && error.requestId === 'test-request-1');
    h.requests[0].success({statusCode:403,data:body,header:{'X-Request-Id':'test-request-1'}});
    await done;
  }
});

test('unknown diagnostics, provider responses and objects never become user-facing text', () => {
  for (const detail of ['INTERNAL_ERROR', 'field required: customer_id', '<html>Bad Gateway</html>',
    '数据库错误：select password from private_table', {field:'password',input:'private-test'}, ['raw input']]) {
    for (const status of [400,422,500]) {
      const body = {code:'NEW_UNREGISTERED_ERROR',message:typeof detail === 'string'?detail:undefined,detail};
      const text = messages.messageFor(body,status);
      assert.doesNotMatch(text,/INTERNAL_ERROR|field|customer_id|html|select|password|private|\[object/);
    }
  }
  assert.equal(messages.messageFor({detail:'当前无权归档此客户拜访'},403),'当前无权归档此客户拜访');
  assert.equal(messages.messageFor({code:'CONFLICT',detail:'本次参与人员已修改，请重新审核后归档'},409),'本次参与人员已修改，请重新审核后归档');
  assert.match(messages.messageFor({code:'VISIT_REVIEW_STALE',detail:'field private'},409),/重新质检/);
  assert.match(messages.messageFor({detail:'VISIT_REVIEW_REQUIRED'},409),/质检/);
});

test('field validation uses only known labels and types; raw input, ctx and unknown field names stay out', async () => {
  const h = harness(), detail = [
    {loc:['body','customer_id'],type:'missing',msg:'field required',input:'private-input',ctx:{raw:'private'}},
    {loc:['body','internal_secret_field'],type:'missing',msg:'SQL failure'},
  ];
  const done = assert.rejects(h.api.request({path:'/visits',method:'POST',data:{}}),error=>{
    assert.equal(error.serverCode,'VALIDATION_ERROR');
    assert.match(error.message,/客户/);
    assert.doesNotMatch(error.message,/field|internal|private|SQL/);
    assert.deepEqual(JSON.parse(JSON.stringify(error.validationErrors)),detail.map(({loc,type})=>({loc,type})));
    return true;
  });
  h.requests[0].success({statusCode:422,data:{code:'VALIDATION_ERROR',detail}}); await done;
  assert.equal(messages.validationMessage([{loc:['body','unknown_field'],type:'missing',msg:'raw'}]),'请检查填写内容后重试');
});

test('collection business rejections retain actionable Chinese messages through the shared request layer', async () => {
  const cases = [
    ['OPPORTUNITY_TRADE_RELATION_REQUIRED',422,'请先在交易物料中选择交易关系，再保存实际回款'],
    ['OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN',422,'下游商机不能登记非零我方回款，请核对交易关系与收款归属'],
    ['OPPORTUNITY_COLLECTION_VERSION_CONFLICT',409,'实际回款或来源已变化，请刷新回款状态并核对后保存'],
    ['OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED',409,'请确认替换本季度现有回款来源后再保存'],
    ['OPPORTUNITY_COLLECTION_FORBIDDEN',403,'当前账号无权修改此商机的实际回款'],
    ['OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED',422,'本次保存标识缺失，请重新打开商机后保存'],
    ['OPPORTUNITY_COLLECTION_INPUT_INVALID',422,'请检查季度实际回款金额、回款依据和备注'],
    ['OPPORTUNITY_COLLECTION_PERIOD_INVALID',422,'请选择有效的回款年份和季度'],
  ];
  for (const [code,status,message] of cases) {
    const h = harness(), done = assert.rejects(h.api.request({path:'/opportunities/id',method:'PATCH',data:{}}), error =>
      error.serverCode === code && error.statusCode === status && error.message === message && error.outcomeUnknown === false);
    h.requests[0].success({statusCode:status,data:{code,message:'SQL field required private'}});
    await done;
  }
});

test('nested actual collection validation names visible fields without leaking diagnostics or technical revision fields', () => {
  const detail = [
    {loc:['body','collection_actuals',0,'amount'],type:'decimal_parsing',msg:'invalid SQL input',input:'private'},
    {loc:['body','collection_actuals',0,'source_ref'],type:'missing',msg:'Field required'},
    {loc:['body','collection_actuals',1,'quarter'],type:'less_than_equal',ctx:{raw:'private'}},
  ];
  assert.equal(messages.messageFor({code:'VALIDATION_ERROR',detail},422),'请检查季度实际回款金额、回款依据、回款季度的填写内容');
  assert.equal(messages.validationMessage([{loc:['body','amount'],type:'missing'}]),'请填写金额');
  assert.equal(messages.validationMessage([{loc:['body','collection_actuals',0,'source_ref'],type:'missing'}]),'请填写回款依据');
  assert.equal(messages.validationMessage([{loc:['body','collection_actuals',0,'expected_actual_revision'],type:'missing'}]),messages.MESSAGES.OPPORTUNITY_COLLECTION_VERSION_CONFLICT);
});

test('read 500 offers manual recovery; mutation uncertainty never advertises replay', async () => {
  for (const method of ['GET','POST']) {
    const h = harness(), done = assert.rejects(h.api.request({path:'/example',method}),error=>{
      assert.equal(error.retryable,method==='GET');
      assert.equal(error.outcomeUnknown,method==='POST');
      assert.equal(error.recoveryAction,method==='GET'?'retry_read':'check_status');
      assert.equal(error.message,method==='GET'?'服务暂时异常，请稍后重试':'提交结果待确认，请先查看记录，勿重复提交');
      return true;
    });
    h.requests[0].success({statusCode:500,data:{detail:'INTERNAL_ERROR',retryable:true,recovery_action:'retry_read'}});
    await done; assert.equal(h.requests.length,1);
  }
  assert.equal(policy.retryDelay({path:'/directory/members'}, {statusCode:500,retryable:true,retryAfterMs:null},0),null);
  const refused = policy.recovery({method:'POST'},503,{code:'AI_SERVICE_NOT_CONFIGURED',retryable:true},{});
  assert.equal(refused.outcomeUnknown,false);assert.equal(refused.retryable,false);assert.equal(refused.recoveryAction,'contact_admin');
});

test('all upload error paths map diagnostics without losing status and request receipts', async () => {
  for (const start of [h=>h.api.transcribeAudio('synthetic-path','visit'),
    h=>h.api.uploadVisitFile('synthetic-path','synthetic.txt'),
    h=>h.api.uploadKnowledgeFile({name:'synthetic.txt'},{idempotencyKey:'11111111-1111-4111-8111-111111111111'})]) {
    const h = harness(), done = assert.rejects(start(h),error=>{
      assert.equal(error.statusCode,422); assert.equal(error.serverCode,'VALIDATION_ERROR');
      assert.equal(error.requestId,'upload-test-1'); assert.doesNotMatch(error.message,/field|private|SQL/); return true;
    });
    h.uploads[0].success({statusCode:422,data:JSON.stringify({code:'VALIDATION_ERROR',detail:'field SQL private',request_id:'upload-test-1'})});
    await done;assert.equal(h.uploads.length,1);
  }
});

test('a failed asynchronous run uses its code, never raw diagnostic detail', async () => {
  const h=harness(), done=assert.rejects(h.api.resumeRun('synthetic-run'),error=>
    error.code==='RUN_FAILED' && error.runStatus==='failed' && error.message==='智能处理未完成，请检查输入后重试');
  h.requests[0].success({statusCode:200,data:{status:'failed',error_code:'RUN_FAILED',error_detail:'field private provider SQL'}});
  await done;assert.equal(h.requests.length,1);
});

test('report job failure is a service error and never exposes worker diagnostics', () => {
  const expected = '报告材料准备或生成失败，请稍后重试；持续失败请联系管理员';
  for (const key of ['code', 'serverCode', 'error_code']) {
    const failure = {[key]:'WORK_REPORT_JOB_FAILED',message:'permission denied for table private_table',detail:'SQL worker diagnostics'};
    assert.equal(messages.businessMessage(failure,'生成未完成，请检查输入后重试'),expected);
  }
  assert.equal(messages.messageFor({code:'WORK_REPORT_JOB_FAILED',detail:'SQL worker diagnostics'},500),expected);
});

test('network timeout copy is Chinese and preserves recoverability metadata', () => {
  const read = policy.transportError({method:'GET'},{errMsg:'request:fail fail:time out'});
  const write = policy.transportError({method:'POST'},{errMsg:'request:fail timeout'});
  assert.equal(read.timeout,true);assert.equal(write.outcomeUnknown,true);
  assert.doesNotMatch(read.message+write.message,/timeout|request:fail/);
});

test('failed advice states never use raw diagnostic error text', () => {
  const {adviceState}=require('../miniprogram/utils/businessAdviceState');
  const result={id:'synthetic',subject_kind:'customer',subject_id:'customer',section:'overview',status:'failed',error:'field SQL private'};
  assert.equal(adviceState(result,'customer','customer','overview').error,'本次分析未完成，可主动重新生成');
  assert.equal(adviceState({...result,error_code:'AI_SERVICE_NOT_CONFIGURED'},'customer','customer','overview').error,'智能服务尚未配置，请联系管理员');
});

test('uncertain audio upload queries the new receipt without replaying the file', async () => {
  const h=harness(),done=assert.rejects(h.api.transcribeAudio('synthetic-path','visit'),error=>{
    assert.equal(error.outcomeUnknown,true);assert.equal(error.recoveryAction,'check_status');
    assert.match(error.message,/查询处理结果/);assert.doesNotMatch(error.message,/timeout|手动重试/);return true;
  });
  h.uploads[0].fail({errMsg:'uploadFile:fail timeout'});await new Promise(resolve=>setImmediate(resolve));
  const sent=h.uploads[0].formData;
  h.requests[0].success({statusCode:200,data:{status:'unknown',operation_id:sent.operation_id,session_id:sent.session_id,purpose:'visit'}});
  await done;assert.equal(h.uploads.length,1);
});

test('knowledge shutdown is a known rejection for reads and writes, never an uncertain result or retry', () => {
  const body = {code:'KNOWLEDGE_TEMPORARILY_UNAVAILABLE',retryable:false};
  for (const method of ['GET','POST']) {
    const recovery = policy.recovery({method,path:'/knowledge/materials'},503,body,{});
    assert.equal(recovery.outcomeUnknown,false);
    assert.equal(recovery.retryable,false);
    assert.equal(messages.messageFor(body,503,recovery),'知识库暂不能使用');
  }
});


test('customer creation claim denials have safe actionable messages without leaking internal data',()=>{
 for(const [code,pattern] of [['OPPORTUNITY_CUSTOMER_CLAIM_REQUIRED',/先认领/],['OPPORTUNITY_CUSTOMER_CLAIM_SCOPE_DENIED',/认领人.*范围/],['OPPORTUNITY_CREATE_FORBIDDEN',/无权/],['OPPORTUNITY_CUSTOMER_NOT_FOUND',/不存在或当前不可访问/]]){
  const text=messages.messageFor({code,message:'private SQL customer_id'},403);assert.match(text,pattern);assert.doesNotMatch(text,/private|SQL|customer_id/);
 }
});
