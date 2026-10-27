import test from 'node:test';
import assert from 'node:assert/strict';
import {aclGrants, changeCategory, contributionPayload, countText, emptyContribution, formatObservedAt, previewName, validateContribution} from '../department-ui/knowledge/model.js';
import {authorizeMaterialLink, createKnowledgeService, isGenerationInputError, runtimeKnowledgeService, unavailableService} from '../department-ui/knowledge/service.js';
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const option = (code, label = code, active = true) => ({code, label, active});
const catalog = {company_id: id(1), revision: 2, general_product_code: 'general', products: [option('general', '通用'), option('product', '测试产品'), option('old', '失效产品', false)], industries: [option('industry', '测试行业')], reporting_positions: [option('position')], reporting_levels: [option('level')], decision_roles: [option('role')]};
const form = () => ({...emptyContribution(), content_category: 'customer_solution', content_scope: 'customer', product_code: 'general', industry_code: 'industry', customer_id: id(2), reporting_position_code: 'position', reporting_level_code: 'level', decision_role_code: 'role', content: {file_ids: [], links: ['https://example.com/source']}});
test('complete customer solution serializes only contract input; auto name uses standard CRM and omits general product', () => {
 const input = {...form(), content_name: 'forged', company_id: id(9), uploaded_at: 'forged', acl: {public: true}};
 assert.equal(previewName(input, catalog, {id:id(2), name:'标准客户'}), '客户方案｜对客｜测试行业｜标准客户');
 const payload = contributionPayload(input, catalog);
 assert.equal(payload.expected_catalog_revision, 2);
 for (const key of ['content_name', 'company_id', 'uploaded_at', 'acl']) assert.equal(key in payload, false);
 assert.deepEqual(validateContribution(payload, catalog), {});
});
test('switching categories clears hidden customer and reporting fields while retaining the optional opportunity', () => {
 const initial = {...form(), opportunity_id:id(3)};
 const industry = changeCategory(initial, 'industry_solution');
 assert.equal(industry.industry_code, 'industry'); assert.equal(industry.customer_id, null); assert.equal(industry.reporting_level_code, null);
 const simple = changeCategory(industry, 'product_intro');
 assert.equal(simple.industry_code, null); assert.equal(simple.opportunity_id, id(3));
 assert.deepEqual(validateContribution(simple,catalog),{});
});
test('conditional fields, inactive enum, invalid CRM, missing or credential links block submit', () => {
 assert.ok(validateContribution({...form(), customer_id:'typed customer'},catalog).customer_id);
 assert.ok(validateContribution({...form(), reporting_position_code:null},catalog).reporting_position_code);
 assert.ok(validateContribution({...form(), product_code:'old'},catalog).product_code);
 assert.ok(validateContribution({...form(), content:{file_ids:[],links:[]}},catalog).content);
 assert.ok(validateContribution({...form(), content:{file_ids:[],links:['https://user:password@example.com']}},catalog).content);
 assert.ok(validateContribution({...form(), content:{file_ids:[],links:['https://example.com','https://example.com/']}},catalog).content);
 assert.ok(validateContribution({...form(), content:{file_ids:['pending-upload'],links:[]}},catalog).content);
});
test('ACL remains explicit and count zero differs from missing/unconfigured', () => {
 assert.deepEqual(aclGrants('personal',[]),[]);
 assert.deepEqual(aclGrants('team',[id(4)],['edit']),[{subject_kind:'team',subject_id:id(4),actions:['edit']}]);
 assert.deepEqual(aclGrants('company',[],['read']),[{subject_kind:'company',subject_id:null,actions:['read']}]);
 assert.equal(countText({status:'available',count:0,source:'API',observed_at:'2026-10-17T00:00:00Z'}),'0');
 assert.equal(countText({status:'unconfigured',count:7}),'—');
 assert.equal(countText({status:'available',count:7}),'—');
});
test('dashboard observation time is rendered as a readable Beijing timestamp', () => {
 assert.equal(formatObservedAt('2026-10-17T00:00:00Z'), '2026-10-17 08:00:00');
 assert.equal(formatObservedAt(null), '观测时间待确认');
 assert.equal(formatObservedAt('not-a-date'), '观测时间待确认');
});
test('runtime adapter sends fixed paths, explicit UUID keys, new version revision and fresh reads', async () => {
 const calls=[]; const client={request:async options=>{calls.push(options); if(options.path.endsWith('/acl'))return {grants:[],revision:3}; if(options.path === '/knowledge/materials/'+id(5))return {material_id:id(5),versions:[],can_manage:true}; return {material_id:id(5)};}};
 const service=createKnowledgeService(client), key=id(8), payload=contributionPayload(form(),catalog);
 await service.createMaterial(payload,key); await service.addVersion(id(5),{expected_material_revision:4,contribution:payload},key); const detail=await service.getMaterial(id(5)); await service.getProviderBinding(id(5),id(6));
 assert.equal(calls[0].path,'/knowledge/materials'); assert.equal(calls[0].idempotencyKey,key);
 assert.equal(calls[1].data.expected_material_revision,4); assert.equal(calls[2].fresh,true); assert.equal(calls[3].path,`/knowledge/materials/${id(5)}/acl`); assert.ok(detail.acl);
 assert.equal(calls[4].path,`/knowledge/materials/${id(5)}/versions/${id(6)}/provider-binding`);
});
test('adapter fails closed on absent capabilities, unknown pagination and provider absence', async () => {
 const service=createKnowledgeService({request:async()=>[]});
 await assert.rejects(service.listMaterials(), /分页/);
 await assert.rejects(service.uploadFile({size:1},id(1)),/正在接入/);
 await assert.rejects(service.readFile({}),/正在接入/);
 assert.equal((await unavailableService.status()).retrieval.status,'unconfigured');
 await assert.rejects(unavailableService.getProviderBinding(id(1),id(2)), /尚未接入/);
 await assert.rejects(unavailableService.ask({}), /尚未接入/);
});

test('works adapter uses the explicit backend contract and preserves the mutation receipt', async () => {
 const calls=[]; const client={request:async options=>{calls.push(options); return options.method==='POST' ? {job_id:id(20),status:'queued'} : {items:[],total:0,limit:20,offset:0};}};
 const service=createKnowledgeService(client), key=id(21), work=id(22);
 await service.listWorks({limit:20,offset:40});
 await service.generate({purpose:'客户方案',material_ids:[id(3)],format:'docx',audience:'internal'},key);
 await service.getWork(work);
 await service.publishWork(work,{expected_revision:2,recipients:[{subject_kind:'team',subject_id:id(4)}]},key);
 assert.equal(calls[0].path,'/knowledge/works?limit=20&offset=40');
 assert.deepEqual(calls[1],{fresh:true,path:'/knowledge/works',method:'POST',data:{purpose:'客户方案',material_ids:[id(3)],format:'docx',audience:'internal'},idempotencyKey:key,timeout:90000});
 assert.equal(calls[2].path,`/knowledge/works/${work}`);
 assert.equal(calls[3].path,`/knowledge/works/${work}/publish`);
 assert.equal(calls[3].idempotencyKey,key);
 assert.deepEqual(calls[3].data,{expected_revision:2,recipients:[{subject_kind:'team',subject_id:id(4)}]});
});

test('generation provider absence remains a visible contract error', async () => {
 const service=createKnowledgeService({request:async()=>{const error=new Error('KNOWLEDGE_PROVIDER_GENERATION_UNCONFIGURED');error.serverCode=error.message;error.statusCode=409;throw error;}});
 await assert.rejects(service.listWorks(), /材料生成尚未配置真实供应商/);
});
test('generation input errors preserve the 422 contract, provide safe correction text and never retry',async()=>{
 for(const [code,message] of [['KNOWLEDGE_DOCUMENT_SOURCE_LIMIT','减少参考材料'],['KNOWLEDGE_DOCUMENT_INPUT_INVALID','检查参考材料和用途']]){
  let calls=0;const service=createKnowledgeService({request:async()=>{calls++;throw Object.assign(Error('private provider url and token'),{serverCode:code,statusCode:422});}});
  await assert.rejects(service.generate({purpose:'fixture',material_ids:[id(3)],format:'docx',audience:'internal'},id(8)),error=>{
   assert.equal(error.code,code);assert.equal(error.statusCode,422);assert.equal(isGenerationInputError(error),true);assert.ok(error.message.includes(message));assert.equal(error.message.includes('private'),false);return true;
  });assert.equal(calls,1);
  assert.equal(isGenerationInputError({code,statusCode:503}),false);
 }
 assert.equal(isGenerationInputError({code:'KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN',statusCode:503}),false);
});
test('fixed shared binary transport uses fileId/options and validates ArrayBuffer before Blob', async () => {
 const calls=[]; const service=createKnowledgeService({request:async()=>{}, readKnowledgeFile:async(...args)=>{calls.push(args);return {data:new TextEncoder().encode('evidence').buffer,contentType:'text/plain',filename:'evidence.txt'};}});
 const result=await service.readFile({material_id:id(3),version_id:id(4),file_id:id(5),preview:true});
 assert.deepEqual(calls,[[id(5),{versionId:id(4),preview:true}]]); assert.equal(await result.blob.text(),'evidence'); assert.equal(result.filename,'evidence.txt');
 const work=await service.readFile({work_id:id(8),file_id:id(5),preview:true});
 assert.deepEqual(calls[1],[id(5),{workId:id(8),preview:false}]);assert.equal(await work.blob.text(),'evidence');
 const malformed=createKnowledgeService({request:async()=>{},readKnowledgeFile:async()=>({data:'not bytes'})});
 await assert.rejects(malformed.readFile({file_id:id(5),version_id:id(4)}),/二进制/);
});

test('work share candidates bind their response to exact work, kind and page',async()=>{
 const calls=[],value={work_id:id(8),subject_kind:'team',items:[{subject_id:id(4),display_name:'授权团队'}],total:21,limit:20,offset:20};
 const service=createKnowledgeService({request:async options=>{calls.push(options);return value;}});
 assert.deepEqual(await service.listWorkShareCandidates(id(8),{subject_kind:'team',q:'授权 & 团队',offset:20}),value);
 const url=new URL('http://fixture'+calls[0].path);assert.equal(url.pathname,`/knowledge/works/${id(8)}/share-candidates`);assert.equal(url.searchParams.get('q'),'授权 & 团队');assert.equal(url.searchParams.get('offset'),'20');assert.equal(calls[0].fresh,true);
 for(const bad of [{...value,work_id:id(9)},{...value,subject_kind:'user'},{...value,items:[{subject_id:id(4),display_name:''}]},{...value,items:[value.items[0],value.items[0]]},{...value,limit:undefined}]){
   const broken=createKnowledgeService({request:async()=>bad});await assert.rejects(broken.listWorkShareCandidates(id(8),{subject_kind:'team',offset:20}),/当前作品/);
 }
 for(const args of [{subject_kind:'company'},{subject_kind:'team',limit:101},{subject_kind:'team',offset:-1},{subject_kind:'user',q:'x'.repeat(101)}])await assert.rejects(service.listWorkShareCandidates(id(8),args),/查询条件/);
 assert.equal(calls.length,1);
});

test('external source link rechecks exact version membership and current access on every click', async () => {
 const link='https://example.com/source';let reads=0,revoked=false;
 const service={getMaterial:async material_id=>{reads++;if(revoked)throw Error('revoked');return {material_id,versions:[{version_id:id(4),state:'saved',contribution:{selection:{content:{links:[link]}}}}]};}};
 assert.equal(await authorizeMaterialLink(service,{material_id:id(3),version_id:id(4),link}),link);
 await assert.rejects(authorizeMaterialLink(service,{material_id:id(3),version_id:id(9),link}),/不可访问/);
 revoked=true;await assert.rejects(authorizeMaterialLink(service,{material_id:id(3),version_id:id(4),link}),/revoked/);assert.equal(reads,3);
});

test('G2 forwards all filters before paging and preserves authoritative create capability and stats', async () => {
 const calls=[];const stats={source:'salesbuddy.internal_materials',observed_at:'2026-10-17T00:00:00Z',metrics:{total:{status:'available',count:0,source:'salesbuddy.internal_materials',observed_at:'2026-10-17T00:00:00Z'}}};
 const service=createKnowledgeService({request:async options=>{calls.push(options);return options.path.startsWith('/knowledge/stats')?stats:{items:[],total:0,limit:20,offset:40,capabilities:{create:false}};}});
 const filters={scope:'team',q:'客户 &方案',content_category:'customer_solution',product_code:'test.product',state:'saved',period:'30d',retrievable:true};
 const page=await service.listMaterials({...filters,page_size:20,offset:40});assert.equal(page.capabilities.create,false);
 const query=new URL('https://fixture.test'+calls[0].path).searchParams;for(const [key,value] of Object.entries(filters))assert.equal(query.get(key),String(value));assert.equal(query.get('offset'),'40');
 assert.deepEqual(await service.getStats(filters),stats);assert.equal(new URL('https://fixture.test'+calls[1].path).searchParams.get('retrievable'),'true');
});
test('uncertain upload keeps an actor-and-content bound key across repeated file selection until a confirmed receipt', async () => {
 const {prepareUploadIntent,completeUploadIntent}=await import('../department-ui/knowledge/upload-intent.js');
 const values=new Map(),storage={getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
 const file=new File(['same bytes'],'evidence.txt',{type:'text/plain'});
 const a=await prepareUploadIntent(file,'actor-A',storage),retry=await prepareUploadIntent(new File(['same bytes'],'evidence.txt',{type:'text/plain'}),'actor-A',storage);
 assert.equal(a.key,retry.key); assert.equal(values.size,1);
 assert.notEqual((await prepareUploadIntent(file,'actor-B',storage)).key,a.key);
 assert.notEqual((await prepareUploadIntent(new File(['changed'],'evidence.txt',{type:'text/plain'}),'actor-A',storage)).key,a.key);
 completeUploadIntent(a);assert.notEqual((await prepareUploadIntent(file,'actor-A',storage)).key,a.key);
 for(const key of values.keys())assert.ok(!key.includes('actor')&&!key.includes('evidence.txt'));
});

// Exercise the production port through the approved shared wrapper, not a separate auth implementation.
test('runtime port consumes shared file wrapper with stable upload options and exact version bytes', async () => {
 const previous=globalThis.SalesRuntime,calls=[],file=new File(['runtime bytes'],'runtime.txt',{type:'text/plain'});
 const client={request:async()=>{},uploadKnowledgeFile:async(...args)=>{calls.push(['upload',...args]);return {file_id:id(5)};},readKnowledgeFile:async(...args)=>{calls.push(['read',...args]);return {data:await file.arrayBuffer(),contentType:file.type,filename:file.name};}};
 globalThis.SalesRuntime={require:name=>{assert.equal(name,'utils/apiClient');return client;}};
 try {
   const service=runtimeKnowledgeService();await service.uploadFile(file,id(8));const bytes=await service.readFile({file_id:id(5),version_id:id(4),preview:false});
   assert.deepEqual(calls,[['upload',file,{idempotencyKey:id(8)}],['read',id(5),{versionId:id(4),preview:false}]]);assert.equal(await bytes.blob.text(),'runtime bytes');
 } finally {globalThis.SalesRuntime=previous;}
});
