import test from 'node:test';import assert from 'node:assert/strict';
import {createDistributionService,shareUrl,DISTRIBUTION_SOURCE} from '../department-ui/knowledge/distribution-service.js';
import {createPublicService,shareToken,browserIdentifier} from '../department-ui/knowledge/public-service.js';
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`,token='a'.repeat(48);
const publication=()=>({material_id:id(1),publication_id:id(2),version_id:id(3),revision:1,can_publish:true,can_share:true,files:[],questionnaire:{enabled:true,fields:[]}});
test('internal transport binds publication revision, version and identity key using normal auth transport',async()=>{
 const calls=[],service=createDistributionService({request:async options=>{calls.push(options);return options.path.endsWith('/shares')?{...publication(),share_id:id(4),sales_user_id:id(5)}:publication();}});
 await service.getPublication(id(1));await service.publish(id(1),{expected_revision:1,version_id:id(3),enabled:true},id(6));await service.share(id(1),id(2),id(7));
 assert.equal(calls[0].fresh,true);assert.equal(calls[1].method,'PUT');assert.equal(calls[1].idempotencyKey,id(6));assert.deepEqual(calls[2].data,{publication_id:id(2)});
});
test('list requires customer distribution source and filtering carries timezone and version metadata',async()=>{
 const calls=[],service=createDistributionService({request:async options=>{calls.push(options);return {source:DISTRIBUTION_SOURCE,items:[],total:0,limit:20,offset:0};}});
 await service.list({q:'销售',date_from:'2026-10-18T00:00:00+08:00'});await service.responses({version_id:id(3),scope:'self'});
 assert.match(calls[0].path,/date_from=2026-10-18T00%3A00%3A00%2B08%3A00/);assert.match(calls[1].path,/version_id=/);
 await assert.rejects(createDistributionService({request:async()=>({source:'salesbuddy.internal_materials',items:[],total:114,limit:20,offset:0})}).list());
});
test('malformed share, foreign origins, errors and unknown responses never become success',async()=>{
 assert.throws(()=>shareUrl({share_path:'https://evil.test/#token='+token},'https://example.test'));
 assert.equal(shareUrl({share_path:'/web/knowledge-share.html#token='+token},'https://example.test'),'https://example.test/web/knowledge-share.html#token='+token);
 await assert.rejects(createDistributionService({request:async()=>({...publication(),share_id:id(4),sales_user_id:id(5),publication_id:id(99)})}).share(id(1),id(2),id(6)));
 let count=0;await assert.rejects(createDistributionService({request:async()=>{count++;throw Object.assign(Error('private endpoint secret'),{serverCode:'KNOWLEDGE_PUBLICATION_CONFLICT',statusCode:409});}}).publish(id(1),{},id(6)),error=>!error.message.includes('secret')&&error.code==='KNOWLEDGE_PUBLICATION_CONFLICT');assert.equal(count,1);
});
test('public transport sends only share/grant headers; never auth cookies, query tokens, login or redirect follow',async()=>{
 const calls=[],service=createPublicService(token,async(url,options)=>{calls.push({url,options});return new Response(JSON.stringify({ok:true}),{headers:{'content-type':'application/json'}});});
 await service.read('visitor_identifier_123');await service.respond({browser_id:'visitor_identifier_123',questionnaire_version:1,answers:{}},id(8));await service.file(id(1),'download','grant-token');
 for(const {url,options} of calls){assert.equal(url.includes(token),false);assert.equal(options.credentials,'omit');assert.equal(options.redirect,'error');assert.equal(options.referrerPolicy,'no-referrer');assert.equal(options.headers.Authorization,undefined);assert.equal(options.headers['X-Knowledge-Share'],token);}
 assert.equal(calls[1].options.headers['Idempotency-Key'],id(8));assert.equal(calls[2].options.headers['X-Knowledge-Grant'],'grant-token');assert.throws(()=>service.file(id(1),'download',''));
});
test('public failure is safely mapped and never automatically resubmits',async()=>{
 let calls=0;const service=createPublicService(token,async()=>{calls++;return new Response(JSON.stringify({detail:'KNOWLEDGE_QUESTIONNAIRE_CHANGED'}),{status:409});});
 await assert.rejects(service.respond({},id(1)),error=>error.code==='KNOWLEDGE_QUESTIONNAIRE_CHANGED'&&error.message.includes('问卷已更新'));assert.equal(calls,1);
});
test('public nested errors and mutation uncertainty retain safe code and recovery receipts',async()=>{
 let calls=0;const service=createPublicService(token,async()=>{calls++;return new Response(JSON.stringify({detail:{code:'KNOWLEDGE_QUESTIONNAIRE_CHANGED',message:'field private SQL'}}),{status:409});});
 await assert.rejects(service.respond({},id(1)),error=>error.serverCode==='KNOWLEDGE_QUESTIONNAIRE_CHANGED'&&error.message.includes('问卷已更新'));assert.equal(calls,1);
 const uncertain=createPublicService(token,async()=>new Response(JSON.stringify({code:'INTERNAL_ERROR',detail:'private SQL',retryable:true,request_id:'synthetic-receipt'}),{status:500}));
 await assert.rejects(uncertain.respond({},id(1)),error=>error.outcomeUnknown===true&&!error.retryable&&error.recoveryAction==='check_status'&&error.requestId==='synthetic-receipt'&&!error.message.includes('SQL'));
 const {distributionError}=await import('../department-ui/knowledge/distribution-model.js');
 const wrapped=distributionError({code:'HTTP_500',statusCode:500,outcomeUnknown:true,recoveryAction:'check_status',requestId:'synthetic-receipt'});
 assert.equal(wrapped.outcomeUnknown,true);assert.equal(wrapped.recoveryAction,'check_status');assert.match(wrapped.message,/勿重复提交/);
});
test('only a browser identifier is persisted, not questionnaire answers or share grants',()=>{
 const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};
 assert.equal(browserIdentifier(storage,()=>id(9)),id(9));assert.equal(browserIdentifier(storage,()=>id(8)),id(9));assert.deepEqual([...values.keys()],['salesbuddy:knowledge-visitor']);assert.equal(shareToken('#token='+token),token);assert.throws(()=>shareToken('#token=bad'));
});

test('internal card deep link only selects a UUID material; never treats a public token as identity',async()=>{
 const {distributionEntry}=await import('../department-ui/knowledge/distribution-model.js');
 assert.deepEqual(distributionEntry({material_id:id(1),distribution:'1'}),{materialId:id(1),distribution:true});
 assert.equal(distributionEntry({material_id:'https://evil.test'}),null);assert.equal(distributionEntry({token}),null);
 assert.deepEqual(distributionEntry({material_id:id(1)}),{materialId:id(1),distribution:false});
});

test('unavailable source publishing uses safe actionable copy without raw details',async()=>{
 const {distributionError}=await import('../department-ui/knowledge/distribution-model.js');
 const error=distributionError({serverCode:'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED',statusCode:422,message:'private SQL details'});
 assert.match(error.message,/来源状态/);assert.doesNotMatch(error.message,/private|SQL/);assert.equal(error.code,'KNOWLEDGE_DISTRIBUTION_CONTENT_REQUIRED');
});

test('public file keeps transferring beyond a total timeout while chunks arrive',async()=>{
 let calls=0;const progress=[];
 const service=createPublicService(token,async(_url,{signal})=>{
  calls++;let n=0,timer;
  return new Response(new ReadableStream({start(controller){
   signal.addEventListener('abort',()=>{clearInterval(timer);controller.error(new DOMException('signal timed out','AbortError'));});
   timer=setInterval(()=>{controller.enqueue(new Uint8Array([++n]));if(n===8){clearInterval(timer);controller.close();}},15);
  }}),{headers:{'Content-Type':'application/pdf','Content-Length':'8'}});
 },{idleTimeoutMs:60});
 const blob=await service.file(id(1),'preview','grant',p=>progress.push(p));
 assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())],[1,2,3,4,5,6,7,8]);assert.equal(blob.type,'application/pdf');assert.equal(progress.at(-1).received,8);assert.equal(calls,1);
});
test('stalled body produces actionable Chinese error and allows retry with same grant',async()=>{
 let calls=0;const service=createPublicService(token,async(_url,{signal,headers})=>{
  assert.equal(headers['X-Knowledge-Grant'],'same-grant');calls++;
  if(calls===2)return new Response('complete');
  return new Response(new ReadableStream({start(controller){signal.addEventListener('abort',()=>controller.error(new DOMException('signal timed out','AbortError')));}}));
 },{idleTimeoutMs:20});
 await assert.rejects(service.file(id(1),'download','same-grant'),e=>e.code==='NETWORK_ERROR'&&e.message.includes('无需重复填写问卷')&&!e.message.includes('signal'));
 assert.equal(await (await service.file(id(1),'download','same-grant')).text(),'complete');assert.equal(calls,2);
});

test('knowledge shutdown maps a rejected public write without unknown-result or retry instructions', async () => {
  const service=createPublicService(token, async()=>new Response(JSON.stringify({code:'KNOWLEDGE_TEMPORARILY_UNAVAILABLE',retryable:false}),{status:503}));
  await assert.rejects(service.respond({},id(1)), error=>error.message==='知识库暂不能使用' && error.outcomeUnknown===false && error.retryable===false && error.recoveryAction==='contact_admin');
});
