import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createKnowledgeService} from '../department-ui/knowledge/service.js';
import {assertSourceFiles, assertSourceUpload, sameSourceRevision, sourceArchiveError, validateSourceFile} from '../department-ui/knowledge/source-archive.js';
const id=n=>`00000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
const attachment={source_token:'token1',name:'source.pdf',size_bytes:7,media_type:'application/pdf'};
const view=()=>({material_id:id(1),version_id:id(2),version_no:1,material_revision:3,acl_revision:4,source_revision:15,source_status:'可用',can_archive:true,attachments:[attachment],files:[],changed:false});
test('source-file endpoints retain fresh three revisions and same explicit key without contribution or publish calls',async()=>{
 const calls=[],service=createKnowledgeService({request:async options=>{calls.push(options);return view();}});
 const source=await service.getSourceFiles(id(1));const body={expected_material_revision:source.material_revision,expected_acl_revision:source.acl_revision,expected_source_revision:source.source_revision,attachments:[{source_token:'token1',name:'source.pdf',size_bytes:7,file_id:id(9)}]};
 await service.archiveSourceFiles(id(1),body,id(10));await service.archiveSourceFiles(id(1),body,id(10));
 assert.deepEqual(calls.map(row=>row.path),Array(3).fill(`/knowledge/materials/${id(1)}/source-files`));assert.equal(calls[0].fresh,true);assert.equal(calls[1].method,'POST');assert.deepEqual(calls[1].data,body);assert.equal(calls[1].idempotencyKey,calls[2].idempotencyKey);
});
test('matching requires source name and actual byte size, missing/oversize source is never guessed',()=>{
 validateSourceFile(attachment,{name:'source.pdf',size:7});
 for(const file of [{name:'other.pdf',size:7},{name:'source.pdf',size:8},{name:'source.pdf',size:0},{name:'source.pdf',size:50*1024*1024+1}])assert.throws(()=>validateSourceFile(attachment,file));
 assert.throws(()=>validateSourceFile({...attachment,size_bytes:null},{name:'source.pdf',size:7}),/准确文件大小/);
 const receipt={file_id:id(9),filename:'source.pdf',size_bytes:7,sha256:'a'.repeat(64)};assert.equal(assertSourceUpload(receipt,attachment),receipt);
 for(const bad of [{...receipt,filename:'wrong'},{...receipt,size_bytes:8},{...receipt,sha256:'guessed'},{...receipt,file_id:'source-token'}])assert.throws(()=>assertSourceUpload(bad,attachment));
});
test('source/ACL/version changes and malformed or foreign receipts fail closed',()=>{
 const source=view();assert.equal(assertSourceFiles(source,id(1)),source);
 for(const bad of [{...source,material_id:id(90)},{...source,can_archive:'true'},{...source,source_revision:-1},{...source,files:[{...attachment,file_id:id(9),sha256:'bad'}]}])assert.throws(()=>assertSourceFiles(bad,id(1)));
 for(const key of ['version_id','material_revision','acl_revision','source_revision'])assert.equal(sameSourceRevision(source,{...source,[key]:key==='version_id'?id(90):999}),false);
 assert.equal(sameSourceRevision(source,{...source,can_archive:false}),false);assert.equal(sameSourceRevision(source,{...source,attachments:[]}),false);assert.equal(sameSourceRevision(source,structuredClone(source)),true);
});
test('source errors use safe actionable copy and hide arbitrary provider text',()=>{
 assert.match(sourceArchiveError({code:'KNOWLEDGE_SOURCE_REVISION_CONFLICT'}),/重新读取/);
 assert.match(sourceArchiveError({code:'KNOWLEDGE_SOURCE_ATTACHMENT_MISMATCH'}),/不一致/);
 assert.match(sourceArchiveError({statusCode:403}),/权限/);
 assert.match(sourceArchiveError({code:'HTTP_413',message:'KNOWLEDGE_FILE_TOO_LARGE'}),/50 MiB/);
 assert.doesNotMatch(sourceArchiveError({message:'https://private-host/?secret',statusCode:503}),/private-host|secret/);
});
