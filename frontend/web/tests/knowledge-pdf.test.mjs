import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {buildOutput} from '../scripts/build-output.mjs';
import {createKnowledgeService,errorText} from '../department-ui/knowledge/service.js';
test('Web accepts 44MiB and exact 50MiB without converting size; rejects 50MiB+1 and empty files before upload',async()=>{
 const calls=[];const service=createKnowledgeService({uploadKnowledgeFile:async(file,options)=>{calls.push({file,options});return {file_id:'stored'};}});
 for(const size of [44*1024*1024,50*1024*1024])await service.uploadFile({name:'large.pdf',size},'same-operation');
 for(const size of [0,50*1024*1024+1])await assert.rejects(service.uploadFile({name:'bad.pdf',size},'bad'),/50 MiB/);
 assert.deepEqual(calls.map(row=>row.file.size),[46137344,52428800]);assert.equal(calls[0].options.idempotencyKey,'same-operation');
});
test('PDF library is pinned and the worker and non-Latin map are copied unchanged into the built site',async()=>{
 const pkg=JSON.parse(await readFile(new URL('../package.json',import.meta.url)));assert.equal(pkg.dependencies['pdfjs-dist'],'6.4.299');
 const hash=value=>createHash('sha256').update(value).digest('hex');
 for(const [source,target]of [['legacy/build/pdf.worker.min.mjs','pdf.worker.min.js'],['cmaps/UniGB-UTF16-H.bcmap','cmaps/UniGB-UTF16-H.bcmap'],['LICENSE','LICENSE']])assert.equal(hash(await readFile(new URL('../node_modules/pdfjs-dist/'+source,import.meta.url))),hash(await readFile(join(buildOutput(),'pdfjs/6.4.299',target))));
});

test('upload intent accepts the actual 44MiB and 50MiB Blob while retaining the uncertain upload key',async()=>{
 const {prepareUploadIntent,completeUploadIntent}=await import('../department-ui/knowledge/upload-intent.js');
 for(const size of [44*1024*1024,50*1024*1024]){
  const file=new File([new Uint8Array(size)],'source.pdf',{type:'application/pdf'});
  const one=await prepareUploadIntent(file,'synthetic-owner',null),two=await prepareUploadIntent(file,'synthetic-owner',null);
  assert.equal(one.key,two.key);completeUploadIntent(one);
 }
 let read=false;for(const size of [0,50*1024*1024+1])await assert.rejects(prepareUploadIntent({size,arrayBuffer(){read=true;}},'synthetic-owner',null),/50 MiB/);assert.equal(read,false);
});
test('upload boundary responses produce product text instead of raw HTTP or server codes',()=>{
 assert.match(errorText({code:'HTTP_413',message:'KNOWLEDGE_FILE_TOO_LARGE'}),/50 MiB/);
 assert.match(errorText({code:'HTTP_422',serverCode:'KNOWLEDGE_FILE_SIZE_INVALID'}),/附件为空/);
});
