import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../browser-platform.js',import.meta.url),'utf8');
function setup(fetch){
  const store=()=>({getItem:()=>null,setItem(){},removeItem(){}});
  const window={SALES_MODE:'live',location:{href:'http://localhost:5277/',origin:'http://localhost:5277'}};
  vm.runInNewContext(source,{window,fetch,sessionStorage:store(),localStorage:store(),navigator:{},URL,Blob,File,FormData,Headers,AbortController,setTimeout,clearTimeout,queueMicrotask,screen:{}});
  const wx={};window.SalesPlatform.install(wx);return {wx,platform:window.SalesPlatform};
}
const request=(wx,options,upload=false)=>new Promise((resolve,reject)=>wx[upload?'uploadFile':'request']({...options,success:resolve,fail:reject}));
test('direct File upload keeps filename, bytes, multipart boundary and supplied auth header',async()=>{
  let observed;const {wx}=setup(async(url,options)=>{observed={url,options};return new Response('{"id":"synthetic-file"}',{status:200});});
  const file=new File([new Uint8Array([0,255,17,128])],'资料.pdf',{type:'application/pdf'});
  const result=await request(wx,{url:'/api/v1/knowledge/files',filePath:file,name:'file',header:{Authorization:'Bearer synthetic','Idempotency-Key':'83a169b0-2662-4f79-874c-10b655e36772','Content-Type':'application/json'}},true);
  assert.equal(result.statusCode,200);assert.equal(observed.options.headers.get('Content-Type'),null);assert.equal(observed.options.headers.get('Authorization'),'Bearer synthetic');
  assert.equal(observed.options.headers.get('Idempotency-Key'),'83a169b0-2662-4f79-874c-10b655e36772');
  const sent=observed.options.body.get('file');assert.equal(sent.name,'资料.pdf');assert.deepEqual(new Uint8Array(await sent.arrayBuffer()),new Uint8Array(await file.arrayBuffer()));
});
test('registered path uploads remain compatible and anonymous Blob gets attachment filename',async()=>{
  const names=[];const {wx,platform}=setup(async(_url,options)=>{names.push(options.body.get('file').name);return new Response('{}');});
  await request(wx,{url:'/api/v1/visit-imports',filePath:platform.registerLocalFile(new File(['a'],'legacy.txt'))},true);
  await request(wx,{url:'/api/v1/knowledge/files',filePath:new Blob(['b'])},true);assert.deepEqual(names,['legacy.txt','attachment']);
});
test('binary success preserves non-UTF8 bytes and headers; non2xx JSON remains structured',async()=>{
  const bytes=new Uint8Array([0,255,0,128,13,10]);let forbidden=false;
  const {wx}=setup(async()=>forbidden?new Response('{"detail":"无权下载"}',{status:403,headers:{'Content-Type':'application/json'}}):new Response(bytes,{headers:{'Content-Type':'application/pdf','Content-Disposition':'attachment; filename="report.pdf"'}}));
  const options={url:'/api/v1/knowledge/files/id/download',responseType:'arraybuffer'};
  const result=await request(wx,options);assert.deepEqual(new Uint8Array(result.data),bytes);assert.equal(result.header['content-type'],'application/pdf');
  forbidden=true;const denied=await request(wx,options);assert.equal(denied.statusCode,403);assert.equal(denied.data.detail,'无权下载');
});
test('cross-origin upload is rejected before fetch and no real network is made',async()=>{
  let calls=0;const {wx}=setup(async()=>{calls++;return new Response('{}');});
  await assert.rejects(request(wx,{url:'https://other.invalid/api/v1/knowledge/files',filePath:new File(['x'],'x.txt')},true));assert.equal(calls,0);
});
