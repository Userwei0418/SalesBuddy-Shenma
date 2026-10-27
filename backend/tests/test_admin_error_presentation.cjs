const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {pathToFileURL} = require('node:url');
const assets = path.resolve(__dirname, '../src/sales_backend/web/assets');
const load = name => import(pathToFileURL(path.join(assets,name)).href);
const response = (status,body) => ({status,ok:status>=200&&status<300,headers:new Headers({'X-Request-ID':'synthetic-request-42'}),json:async()=>body});

test('knowledge pause is a definite safe rejection for reads and writes',async()=>{
 const {apiError}=await load('error-presentation.js');
 const {knowledgeUnavailable}=await load('knowledge-availability.js');
 for(const method of ['GET','POST','PUT']){
  const error=apiError({status:503,method,body:{code:'KNOWLEDGE_TEMPORARILY_UNAVAILABLE'}});
  assert.equal(error.message,'知识库暂不能使用');assert.equal(error.retryable,false);assert.equal(error.outcomeUnknown,false);
 }
 const page=await knowledgeUnavailable();
 assert.match(page.html,/知识库暂不能使用/);assert.doesNotMatch(page.html,/<(button|input|a)\b/);
});

test('legacy code shapes and current top-level code keep logic without displaying wire values',async()=>{
 const {apiError}=await load('error-presentation.js');
 for(const body of [{code:'PASSWORD_CHANGE_REQUIRED',detail:'field required'}, {detail:{code:'PASSWORD_CHANGE_REQUIRED'}}, {detail:'PASSWORD_CHANGE_REQUIRED'}]) {
  const error=apiError({status:403,body});
  assert.equal(error.serverCode,'PASSWORD_CHANGE_REQUIRED');assert.equal(error.code,'PASSWORD_CHANGE_REQUIRED');
  assert.equal(error.message,'请先修改初始密码。');
 }
 for(const body of [{message:'field required',detail:'SELECT password FROM secret'}, {code:'UNKNOWN_INTERNAL_CODE',message:'失败：account_code field required'}, {detail:{message:'secret'}}, '<html>Proxy failure</html>']) {
  const error=apiError({status:422,body});
  assert.equal(error.message,'请检查填写内容是否完整、格式是否正确。');
 }
});

test('validation uses approved field labels and ignores unknown paths, input, msg, ctx',async()=>{
 const {apiError}=await load('error-presentation.js');
 const body={detail:[{loc:['body','account_code'],type:'missing',msg:'Field required',input:'PRIVATE'}, {loc:['body','private_column'],msg:'secret',ctx:{secret:'PRIVATE'}}]};
 const error=apiError({status:422,body});assert.match(error.message,/账号/);assert.doesNotMatch(error.message,/account_code|private_column|Field|secret|PRIVATE/);
 const unknown=apiError({status:422,body:{detail:[{loc:['body','private_column'],msg:'Field required'}]}});
 assert.equal(unknown.message,'请检查填写内容是否完整、格式是否正确。');
});

test('collection failures have actionable copy and controlled nested field names',async()=>{
 const {apiError}=await load('error-presentation.js');
 for(const [code,status,copy] of [
  ['OPPORTUNITY_TRADE_RELATION_REQUIRED',422,'交易关系'],
  ['OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN',422,'下游商机'],
  ['OPPORTUNITY_COLLECTION_VERSION_CONFLICT',409,'刷新回款状态'],
  ['OPPORTUNITY_COLLECTION_SOURCE_CONFIRMATION_REQUIRED',409,'确认替换'],
  ['OPPORTUNITY_COLLECTION_FORBIDDEN',403,'无权修改'],
  ['OPPORTUNITY_COLLECTION_IDEMPOTENCY_KEY_REQUIRED',422,'重新打开商机'],
  ['OPPORTUNITY_COLLECTION_INPUT_INVALID',422,'回款依据'],
  ['OPPORTUNITY_COLLECTION_PERIOD_INVALID',422,'年份和季度'],
 ]) {
  const error=apiError({status,method:'POST',body:{code,message:'PRIVATE SQL Field required'}});
  assert.ok(error.message.includes(copy));assert.equal(error.serverCode,code);
  assert.equal(error.recoveryAction,status===403?'contact_admin':'correct_request');
  assert.equal(error.retryable,false);assert.equal(error.outcomeUnknown,false);
  assert.doesNotMatch(error.message,/PRIVATE|SQL|Field|required/);
 }
 const detail=[{loc:['body','collection_actuals',1,'source_ref'],msg:'PRIVATE',input:'PRIVATE',ctx:{secret:'PRIVATE'}}];
 const error=apiError({status:422,body:{code:'VALIDATION_ERROR',detail}});
 assert.match(error.message,/第 2 条季度实际回款的回款依据/);
 assert.doesNotMatch(error.message,/collection_actuals|source_ref|PRIVATE/);
 const revision=apiError({status:422,body:{detail:[{loc:['body','collection_actuals',0,'expected_actual_revision'],type:'missing'}]}});
 assert.match(revision.message,/刷新回款状态并核对后保存/);
 assert.doesNotMatch(revision.message,/expected_actual_revision|格式/);
 for(const loc of [['body','collection_actuals',-1,'amount'],['body','collection_actuals','0','amount'],['body','collection_actuals',0,'private_column']]) {
  assert.equal(apiError({status:422,body:{detail:[{loc,msg:'PRIVATE'}]}}).message,'请检查填写内容是否完整、格式是否正确。');
 }
});

test('read and write 5xx have different recovery; explicit safe rejections remain failures',async()=>{
 const {apiError}=await load('error-presentation.js');
 for(const status of [0,408,500,502,503,504]) {
  const read=apiError({status,method:'GET',body:{detail:'INTERNAL_ERROR'}});
  assert.equal(read.retryable,true);assert.equal(read.outcomeUnknown,false);assert.equal(read.recoveryAction,'retry_read');
  const write=apiError({status,method:'POST',body:{retryable:true,recovery_action:'retry_read',detail:'INTERNAL_ERROR'}});
  assert.equal(write.retryable,false);assert.equal(write.outcomeUnknown,true);assert.equal(write.uncertain,true);assert.equal(write.recoveryAction,'check_status');assert.match(write.message,/勿重复提交/);
 }
 const rejected=apiError({status:503,method:'POST',body:{code:'AI_SERVICE_NOT_CONFIGURED'}});
 assert.equal(rejected.outcomeUnknown,false);assert.equal(rejected.retryable,false);assert.match(rejected.message,/配置/);
});

test('request ids are safe metadata and never shown as raw error text',async()=>{
 const {apiError}=await load('error-presentation.js');
 const error=apiError({status:500,body:{request_id:'unsafe\nvalue'},headers:new Headers({'X-Request-ID':'safe-id-42'})});
 assert.equal(error.requestId,'safe-id-42');assert.equal(error.statusCode,500);assert.doesNotMatch(error.message,/unsafe|safe-id/);
});

test('actual admin core handles validation, nested codes, malformed bodies and network mutations without replay',async()=>{
 const core=await load('core.js'),original=global.fetch;
 core.clearSession();let calls=0;
 try {
  global.fetch=async()=>{calls++;return response(422,{detail:[{loc:['body','account_code'],msg:'Field required'}]});};
  await assert.rejects(core.api('/synthetic',{method:'POST',retry:false}),error=>error.statusCode===422&&/账号/.test(error.message)&&!error.message.includes('field'));
  global.fetch=async()=>{calls++;return response(403,{detail:{code:'TASK_EXECUTION_FORBIDDEN'}});};
  await assert.rejects(core.api('/synthetic',{retry:false}),error=>error.serverCode==='TASK_EXECUTION_FORBIDDEN'&&/权限/.test(error.message));
  global.fetch=async()=>{calls++;return {...response(200,{}),json:async()=>{throw SyntaxError('unexpected private_field');}};};
  await assert.rejects(core.api('/synthetic',{method:'POST',retry:false}),error=>error.outcomeUnknown&&!error.message.includes('private_field'));
  global.fetch=async()=>{calls++;throw TypeError('private_endpoint failed');};
  await assert.rejects(core.api('/synthetic',{method:'POST',retry:false}),error=>error.outcomeUnknown&&error.recoveryAction==='check_status');
  assert.equal(calls,4);
 } finally {global.fetch=original;core.clearSession();}
});

test('platform actual request path preserves reauth and version conflict for both protocol versions',async()=>{
 const {apiError}=await load('error-presentation.js');
 const source=fs.readFileSync(path.join(assets,'platform.js'),'utf8');
 const apiSource=source.slice(source.indexOf('async function api('),source.indexOf('\nfunction loggedOut()'));
 let verified=0,loggedOut=0,current=response(403,{code:'PLATFORM_REAUTH_REQUIRED'});
 const context={apiError,generation:0,errors:{PLATFORM_REAUTH_REQUIRED:'请完成敏感操作验证。',VERSION_CONFLICT:'请刷新记录。'},fetch:async()=>current,requestVerification:()=>verified++,loggedOut:()=>loggedOut++};
 vm.runInNewContext(apiSource+';globalThis.invoke=api;',context);
 await assert.rejects(context.invoke('/synthetic',{}),error=>error.code==='PLATFORM_REAUTH_REQUIRED'&&error.message==='请完成敏感操作验证。');assert.equal(verified,1);
 current=response(409,{detail:'VERSION_CONFLICT'});
 await assert.rejects(context.invoke('/synthetic',{}),error=>error.code==='VERSION_CONFLICT');assert.equal(loggedOut,0);
 current=response(401,{detail:'AUTH_REQUIRED'});
 await assert.rejects(context.invoke('/synthetic'));assert.equal(loggedOut,1);
});

test('closed company export shows a controlled fallback for hostile or malformed error details',async()=>{
 const {createClosedCompanyExportPage}=await load('closed-company-export.js');
 const nodes=new Map();const node=id=>{if(!nodes.has(id))nodes.set(id,{hidden:false,disabled:false,value:'',textContent:'',classList:{toggle(){}},addEventListener(){},reset(){},replaceChildren(){}});return nodes.get(id);};
 const document={getElementById:node,addEventListener(){}};
 const ui=createClosedCompanyExportPage({document,fetch:async()=>response(500,{detail:'Field required SELECT private_table'}),URL,setTimeout:()=>1,clearTimeout(){}});
 await ui.start();assert.match(node('notice').textContent,/服务暂时异常/);assert.doesNotMatch(node('notice').textContent,/Field|required|SELECT|private_table/);
});
