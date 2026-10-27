const test=require('node:test'),assert=require('node:assert/strict');
const id='11111111-1111-4111-8111-111111111111',version='22222222-2222-4222-8222-222222222222';
const tick=()=>new Promise(r=>setImmediate(r));
const auth=(u,token=u)=>({access_token:token,refresh_token:'refresh-'+token,actor:{workspace_id:'w',user_id:u}});
function setup(){const store=new Map(),reads=[],uploads=[];global.wx={getStorageSync:k=>store.get(k),setStorageSync:(k,v)=>store.set(k,v),removeStorageSync:k=>store.delete(k),request:o=>reads.push(o),uploadFile:o=>uploads.push(o)};delete require.cache[require.resolve('../miniprogram/utils/apiClient')];const api=require('../miniprogram/utils/apiClient');api.saveAuth(auth('a'));return {api,reads,uploads};}
test('binary reads preserve bytes and metadata, fresh download and preview are distinct',async()=>{
 const {api,reads}=setup(),pending=api.readKnowledgeFile(id,{versionId:version,preview:true});assert.match(reads[0].url,new RegExp(`/preview\\?version_id=${version}$`));assert.equal(reads[0].responseType,'arraybuffer');
 const bytes=new Uint8Array([0,255,17]).buffer;reads[0].success({statusCode:200,data:bytes,header:{'Content-Type':'application/pdf','Content-Disposition':"inline; filename*=UTF-8''%E6%96%87%E4%BB%B6.pdf"}});const r=await pending;assert.equal(r.data,bytes);assert.equal(r.filename,'文件.pdf');assert.equal(r.contentType,'application/pdf');
 const download=api.readKnowledgeFile(id);assert.match(reads[1].url,/\/download$/);reads[1].success({statusCode:200,data:bytes});await download;
});
test('upload bridges the same file through one shared refresh, without setting multipart boundary',async()=>{
 const {api,reads,uploads}=setup(),file={name:'test.pdf'},pending=api.uploadKnowledgeFile(file,{idempotencyKey:id});assert.equal(uploads[0].filePath,file);assert.equal(uploads[0].name,'file');assert.equal(uploads[0].header['Content-Type'],undefined);
 uploads[0].success({statusCode:401,data:'{}'});await tick();assert.match(reads[0].url,/\/auth\/refresh$/);reads[0].success({statusCode:200,data:auth('a','renewed')});await tick();assert.equal(uploads.length,2);assert.equal(uploads[1].header.Authorization,'Bearer renewed');assert.equal(uploads[1].filePath,file);assert.equal(uploads[0].header['Idempotency-Key'],id);assert.equal(uploads[1].header['Idempotency-Key'],id);uploads[1].success({statusCode:201,data:'{"id":"file"}'});assert.deepEqual(await pending,{id:'file'});
});
test('binary 401 refreshes once; repeated denial is returned instead of looping',async()=>{
 const {api,reads}=setup(),pending=assert.rejects(api.readKnowledgeFile(id),{statusCode:401});reads[0].success({statusCode:401,data:new ArrayBuffer(0)});await tick();reads[1].success({statusCode:200,data:auth('a','renewed')});await tick();assert.equal(reads[2].responseType,'arraybuffer');reads[2].success({statusCode:401,data:new ArrayBuffer(0)});await pending;assert.equal(reads.length,3);
 assert.equal(reads[0].timeout,120000);assert.equal(reads[1].timeout,15000);assert.equal(reads[2].timeout,120000);
});
test('account switch rejects late upload and binary response without replaying under new account',async()=>{
 const {api,reads,uploads}=setup(),read=assert.rejects(api.readKnowledgeFile(id),{code:'SESSION_CHANGED'}),upload=assert.rejects(api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:id}),{code:'SESSION_CHANGED'});api.saveAuth(auth('b'));reads[0].success({statusCode:200,data:new ArrayBuffer(1)});uploads[0].success({statusCode:401,data:'{}'});await Promise.all([read,upload]);assert.equal(reads.length,1);assert.equal(uploads.length,1);
});
test('invalid identifiers never issue requests; upload 403 and malformed success are not retried',async()=>{
 const {api,reads,uploads}=setup();await assert.rejects(api.readKnowledgeFile('../x'));await assert.rejects(api.readKnowledgeFile(id,{versionId:'bad'}));assert.equal(reads.length,0);
 const denied=assert.rejects(api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:id}),{statusCode:403});uploads[0].success({statusCode:403,data:'{}'});await denied;
 const malformed=assert.rejects(api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:id}),{code:'UPLOAD_RECEIPT_INVALID'});uploads[1].success({statusCode:201,data:'not JSON'});await malformed;assert.equal(uploads.length,2);
});

test('upload requires caller UUID before network; uncertain retry retains supplied key',async()=>{
 const {api,uploads}=setup();for(const key of [undefined,null,123,'','bad',' '+id,id+'\n'])await assert.rejects(api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:key}),{code:'INVALID_IDEMPOTENCY_KEY'});assert.equal(uploads.length,0);
 const first=assert.rejects(api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:id}),{code:'NETWORK_ERROR'});uploads[0].fail({errMsg:'timeout'});await first;
 const retry=api.uploadKnowledgeFile('/tmp/test.pdf',{idempotencyKey:id});assert.equal(uploads[1].header['Idempotency-Key'],uploads[0].header['Idempotency-Key']);uploads[1].success({statusCode:200,data:'{"id":"same-file"}'});await retry;
});

test('work downloads use the work permission route with binary auth, never the material route',async()=>{
 const {api,reads}=setup(),bytes=new Uint8Array([80,75,3,4]).buffer;
 const first=api.readKnowledgeFile(id,{workId:version});
 assert.match(reads[0].url,new RegExp(`/knowledge/works/${version}/files/${id}/download$`));assert.equal(reads[0].responseType,'arraybuffer');assert.equal(reads[0].header.Authorization,'Bearer a');
 reads[0].success({statusCode:200,data:bytes,header:{'content-type':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','content-disposition':'attachment; filename="proposal.docx"'}});
 const file=await first;assert.equal(file.data,bytes);assert.equal(file.filename,'proposal.docx');
 const revoked=assert.rejects(api.readKnowledgeFile(id,{workId:version}),{statusCode:403});reads[1].success({statusCode:403,data:new ArrayBuffer(0)});await revoked;assert.equal(reads.length,2);
});

test('work download rejects mixed identities and late bytes from an old account',async()=>{
 const {api,reads}=setup();
 for(const options of [{workId:'bad'},{workId:version,versionId:version},{workId:version,preview:true}])await assert.rejects(api.readKnowledgeFile(id,options));assert.equal(reads.length,0);
 const pending=assert.rejects(api.readKnowledgeFile(id,{workId:version}),{code:'SESSION_CHANGED'});api.saveAuth(auth('b'));reads[0].success({statusCode:200,data:new ArrayBuffer(4)});await pending;assert.equal(reads.length,1);
});

test('account change during segmented transfer prevents further reads and discards late parts',async()=>{
 const {api,reads}=setup(),part=256*1024,total=part*5,etag='"'+'a'.repeat(64)+'"';
 const pending=assert.rejects(api.readKnowledgeFile(id,{versionId:version}),{code:'SESSION_CHANGED'});
 const respond=(request,start)=>request.success({statusCode:206,data:new ArrayBuffer(part),header:{ETag:etag,'Content-Range':`bytes ${start}-${start+part-1}/${total}`}});
 respond(reads[0],0);await tick();assert.equal(reads.length,4);
 for(const request of reads.slice(1))assert.equal(request.header['If-Match'],etag);
 api.saveAuth(auth('b'));
 reads.slice(1).forEach((request,index)=>respond(request,(index+1)*part));
 await pending;await tick();assert.equal(reads.length,4);
});

test('bounded per-request timeouts preserve the default and reject invalid overrides before network',async()=>{
 const {api,reads}=setup();
 const normal=api.request({path:'/knowledge/works',fresh:true});assert.equal(reads[0].timeout,15000);reads[0].success({statusCode:200,data:{items:[]}});await normal;
 for(const timeout of [0,999,120001,Infinity,'45000',45000.5,null])await assert.rejects(api.request({path:'/knowledge/works',fresh:true,timeout}),{code:'INVALID_REQUEST_TIMEOUT'});
 assert.equal(reads.length,1);
});

test('file deadlines are scoped to material and work bytes; a timeout is not retried automatically',async()=>{
 const {api,reads}=setup();
 for(const options of [{versionId:version,preview:true},{versionId:version},{workId:version}]){
  const pending=assert.rejects(api.readKnowledgeFile(id,options),{code:'NETWORK_ERROR'});
  const request=reads.at(-1),count=reads.length;assert.equal(request.timeout,120000);
  request.fail({errMsg:'request:fail timeout'});await pending;await tick();assert.equal(reads.length,count);
 }
 const query=api.request({path:'/knowledge/materials',fresh:true});assert.equal(reads.at(-1).timeout,15000);
 reads.at(-1).success({statusCode:200,data:{items:[]}});await query;
});

test('real file transport preserves a large binary response after the ordinary query deadline',async()=>{
 const {createServer}=require('node:http'),{once}=require('node:events'),{createHash}=require('node:crypto');
 const bytes=Buffer.alloc(22*1024*1024,0x2e);bytes.write('%PDF-1.4\n');
 const expectedHash=createHash('sha256').update(bytes).digest('hex');
 const {api}=setup(),seen=[],timeouts=[];
 const server=createServer(async(req,res)=>{
  seen.push({url:req.url,authorization:req.headers.authorization});
  await new Promise(resolve=>setTimeout(resolve,16000));
  res.writeHead(200,{'content-type':'application/pdf','content-length':bytes.length,'content-disposition':"inline; filename*=UTF-8''%E8%B5%84%E6%96%99.pdf"});res.end(bytes);
 });
 server.listen(0,'127.0.0.1');await once(server,'listening');wx.setStorageSync('salesApiBaseUrl',`http://127.0.0.1:${server.address().port}`);
 wx.request=options=>{
  timeouts.push(options.timeout);
  fetch(options.url,{method:options.method,headers:options.header,signal:AbortSignal.timeout(options.timeout)})
   .then(async response=>options.success({statusCode:response.status,data:await response.arrayBuffer(),header:Object.fromEntries(response.headers)}))
   .catch(error=>options.fail({errMsg:error.message}));
 };
 try{
  const start=Date.now(),result=await api.readKnowledgeFile(id,{versionId:version,preview:true});
  assert.ok(Date.now()-start>=15000);assert.deepEqual(timeouts,[120000]);
  assert.equal(result.data.byteLength,bytes.length);assert.equal(createHash('sha256').update(Buffer.from(result.data)).digest('hex'),expectedHash);
  assert.equal(result.contentType,'application/pdf');assert.equal(result.filename,'资料.pdf');
  assert.deepEqual(seen,[{url:`/knowledge/files/${id}/preview?version_id=${version}`,authorization:'Bearer a'}]);
 }finally{await new Promise(resolve=>server.close(resolve));}
});

test('generation deadline reaches shared transport without changing other calls or retrying timeout',async()=>{
 const {createKnowledgeService}=await import('../web/department-ui/knowledge/service.js');
 const {api,reads}=setup(),service=createKnowledgeService(api),body={purpose:'fixture',material_ids:[version],format:'docx',audience:'internal'};
 const pending=assert.rejects(service.generate(body,id),{code:'NETWORK_ERROR'});
 assert.equal(reads.length,1);assert.equal(reads[0].timeout,90000);assert.deepEqual(reads[0].data,body);assert.equal(reads[0].header['Idempotency-Key'],id);
 reads[0].fail({errMsg:'request:fail timeout'});await pending;await tick();assert.equal(reads.length,1);
 const normal=service.listWorks();assert.equal(reads[1].timeout,15000);reads[1].success({statusCode:200,data:{items:[]}});await normal;
 const ask=service.ask({question:'fixture'},version);assert.equal(reads[2].timeout,30000);reads[2].success({statusCode:200,data:{answer:'fixture'}});await ask;
});

test('real shared transport accepts a generation response beyond 15 seconds with its scoped deadline',async()=>{
 // A local HTTP fixture tests the real service -> shared client path and real timeout enforcement.
 // It is not an external-provider or production acceptance test.
 const {createServer}=require('node:http'),{once}=require('node:events');
 const {createKnowledgeService}=await import('../web/department-ui/knowledge/service.js');
 const {api}=setup(),seen=[];
 const server=createServer(async(req,res)=>{seen.push({url:req.url,key:req.headers['idempotency-key'],authorization:req.headers.authorization});await new Promise(r=>setTimeout(r,16000));res.writeHead(202,{'content-type':'application/json'});res.end(JSON.stringify({job_id:id,status:'succeeded'}));});
 server.listen(0,'127.0.0.1');await once(server,'listening');wx.setStorageSync('salesApiBaseUrl',`http://127.0.0.1:${server.address().port}`);
 const timeouts=[];
 wx.request=options=>{timeouts.push(options.timeout);fetch(options.url,{method:options.method,headers:options.header,body:JSON.stringify(options.data),signal:AbortSignal.timeout(options.timeout)})
  .then(async response=>options.success({statusCode:response.status,data:await response.json()})).catch(error=>options.fail({errMsg:error.message}));};
 try {
  const service=createKnowledgeService(api),began=Date.now();
  const receipt=await service.generate({purpose:'fixture',material_ids:[version],format:'docx',audience:'internal'},id);
  assert.ok(Date.now()-began>=15000);assert.equal(receipt.status,'succeeded');assert.deepEqual(timeouts,[90000]);assert.deepEqual(seen,[{url:'/knowledge/works',key:id,authorization:'Bearer a'}]);
 } finally {await new Promise(resolve=>server.close(resolve));}
});
