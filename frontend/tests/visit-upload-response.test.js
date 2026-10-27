const test=require('node:test');
const assert=require('node:assert/strict');
function harness(){
  const storage=new Map(),uploads=[],requests=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>requests.push(o),uploadFile:o=>{uploads.push(o);return {};}};
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];
  const api=require('../miniprogram/utils/apiClient');
  const actor={workspace_id:'w',user_id:'a'};api.saveAuth({access_token:'test-only',refresh_token:'test-refresh',actor});
  return {api,uploads,requests,actor};
}
test('multipart保留原文件名、路径及超时，仅完整导入回执通过',async()=>{
  const h=harness();const result=h.api.uploadVisitFile('/saved/audio','访谈.mp3');
  assert.equal(h.uploads.length,1);const send=h.uploads[0];
  assert.equal(send.name,'file');assert.equal(send.filePath,'/saved/audio');assert.deepEqual(send.formData,{original_filename:'访谈.mp3'});assert.equal(send.timeout,120000);
  send.success({statusCode:201,data:JSON.stringify({id:'import-id',status:'queued'})});assert.deepEqual(await result,{id:'import-id',status:'queued'});
});
for(const body of ['<html>proxy error</html>','null','{}','{"id":"i"}','{"id":"","status":"queued"}','{"id":"i","status":"unknown"}'])test(`损坏成功回执保留可重试错误: ${body}`,async()=>{
  const h=harness();const rejected=assert.rejects(h.api.uploadVisitFile('/saved/audio','访谈.mp3'),{code:'UPLOAD_RECEIPT_INVALID'});
  h.uploads[0].success({statusCode:200,data:body});await rejected;
});
for(const [status,body,message] of [[500,'{"detail":"内部错误"}','内部错误'],[422,'{"detail":{"code":"FILE_INVALID","message":"文件格式不支持"}}','文件格式不支持'],[503,'<html>unavailable</html>','文件上传失败（503）']])test(`上传${status}保留状态和可读错误`,async()=>{
  const h=harness();const rejected=assert.rejects(h.api.uploadVisitFile('/saved/audio','访谈.mp3'),error=>error.statusCode===status&&error.message.includes(message));
  h.uploads[0].success({statusCode:status,data:body});await rejected;
});
test('非JSON的401仍执行一次刷新，在原路径/文件名重试',async()=>{
  const h=harness();const result=h.api.uploadVisitFile('/saved/audio','访谈.mp3');
  h.uploads[0].success({statusCode:401,data:'<html>unauthorized</html>'});await new Promise(r=>setImmediate(r));
  assert.equal(h.requests.length,1);assert.match(h.requests[0].url,/auth\/refresh$/);
  h.requests[0].success({statusCode:200,data:{access_token:'test-fresh',refresh_token:'test-refresh',actor:h.actor}});await new Promise(r=>setImmediate(r));
  assert.equal(h.uploads.length,2);assert.equal(h.uploads[1].filePath,h.uploads[0].filePath);assert.deepEqual(h.uploads[1].formData,h.uploads[0].formData);
  h.uploads[1].success({statusCode:201,data:'{"id":"i","status":"queued"}'});assert.equal((await result).id,'i');
});
test('服务端INTERNAL_ERROR保留可追溯编号，用户提示本地文件未丢失',async()=>{
  const h=harness();const rejected=assert.rejects(h.api.uploadVisitFile('/saved/audio','访谈.mp3'),error=>error.statusCode===500&&error.code==='INTERNAL_ERROR'&&error.requestId==='request-123'&&/本地文件已保留/.test(error.message)&&/错误编号：request-123/.test(error.message));
  h.uploads[0].success({statusCode:500,data:'{"detail":"INTERNAL_ERROR","request_id":"request-123"}'});await rejected;
});

const uploadKey='10000000-0000-4000-8000-000000000001',importId='20000000-0000-4000-8000-000000000001';
const intentReceipt=()=>({upload_intent_id:uploadKey,state:'registered',id:importId,status:'succeeded',filename:'meeting.m4a',replayed:true});
test('新版multipart带持久key，完整同意图registered回执回读当前状态',async()=>{
 const h=harness();const pending=h.api.uploadVisitFile('/saved/audio','meeting.m4a',null,uploadKey);assert.equal(h.uploads[0].header['Idempotency-Key'],uploadKey);h.uploads[0].success({statusCode:202,data:JSON.stringify(intentReceipt())});assert.equal((await pending).status,'succeeded');
});
for(const receipt of [{id:importId,status:'queued'}, {...intentReceipt(),upload_intent_id:'10000000-0000-4000-8000-000000000002'},{...intentReceipt(),filename:''},{...intentReceipt(),status:'unknown'}])test('新版不接受旧成功形状/错意图/不完整字段',async()=>{
 const h=harness();const p=assert.rejects(h.api.uploadVisitFile('/saved/audio','meeting.m4a',null,uploadKey),{code:'UPLOAD_RECEIPT_INVALID'});h.uploads[0].success({statusCode:202,data:JSON.stringify(receipt)});await p;
});
for(const state of ['processing','uncertain','retryable_failure'])test(`新版202 ${state}不伪造import记录`,async()=>{
 const h=harness();const pending=h.api.uploadVisitFile('/saved/audio','meeting.m4a',null,uploadKey);const receipt={upload_intent_id:uploadKey,state,id:null,status:null,filename:'meeting.m4a',retryable:state==='retryable_failure'};h.uploads[0].success({statusCode:202,data:JSON.stringify(receipt)});assert.equal((await pending).id,null);
});
test('新版401刷新仍用同上传key，查回绕过在途GET共享',async()=>{
 const h=harness();const pending=h.api.uploadVisitFile('/saved/audio','meeting.m4a',null,uploadKey);h.uploads[0].success({statusCode:401,data:'{}'});await new Promise(r=>setImmediate(r));h.requests[0].success({statusCode:200,data:{access_token:'fresh',refresh_token:'test-refresh',actor:h.actor}});await new Promise(r=>setImmediate(r));assert.equal(h.uploads[1].header['Idempotency-Key'],uploadKey);h.uploads[1].success({statusCode:202,data:JSON.stringify(intentReceipt())});await pending;
 const a=h.api.getVisitUploadIntent(uploadKey),b=h.api.getVisitUploadIntent(uploadKey);assert.equal(h.requests.length,3);assert.ok(h.requests[1].url.endsWith('/visit-imports/requests/'+uploadKey));h.requests[1].success({statusCode:200,data:intentReceipt()});h.requests[2].success({statusCode:200,data:intentReceipt()});await Promise.all([a,b]);
});
test('只接受服务器明确NOT_FOUND码，HTTP404旧路径不能冒充未登记',async()=>{
 const h=harness();const one=assert.rejects(h.api.getVisitUploadIntent(uploadKey),error=>error.statusCode===404&&error.serverCode==='UPLOAD_INTENT_NOT_FOUND');h.requests[0].success({statusCode:404,data:{detail:'未登记',code:'UPLOAD_INTENT_NOT_FOUND'}});await one;
 const two=assert.rejects(h.api.getVisitUploadIntent(uploadKey),error=>error.statusCode===404&&error.serverCode==='');h.requests[1].success({statusCode:404,data:{detail:'Not Found'}});await two;
});
test('识别retry使用独立显式key，超时后重试同key且没有上传流',async()=>{
 const h=harness();const retryKey='30000000-0000-4000-8000-000000000001';const first=assert.rejects(h.api.retryVisitImport(importId,retryKey),/timeout/);assert.equal(h.requests[0].header['Idempotency-Key'],retryKey);h.requests[0].fail({errMsg:'timeout'});await first;
 const second=h.api.retryVisitImport(importId,retryKey);assert.equal(h.requests[1].header['Idempotency-Key'],retryKey);assert.match(h.requests[1].url,/\/retry$/);h.requests[1].success({statusCode:200,data:{id:importId,status:'queued'}});assert.equal((await second).id,importId);assert.equal(h.uploads.length,0);
});
test('服务端文件冲突码保留，不能被泛化为普通网络失败',async()=>{
 const h=harness();const p=assert.rejects(h.api.uploadVisitFile('/saved/audio','meeting.m4a',null,uploadKey),{code:'UPLOAD_INTENT_CONFLICT',statusCode:409});h.uploads[0].success({statusCode:409,data:'{"detail":"文件不匹配","code":"UPLOAD_INTENT_CONFLICT"}'});await p;
});
