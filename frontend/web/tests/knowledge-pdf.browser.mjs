// Actual local PDF.js worker/canvas and HTTP transport with synthetic API responses.
// An explicit optional local PDF adds rendering evidence, not backend ACL or production acceptance.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtemp,writeFile,readFile,cp,mkdir,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from 'esbuild';
import {createSalesWebServer} from '../server.mjs';
import {buildOutput} from '../scripts/build-output.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const listen=server=>new Promise(resolve=>server.listen(0,'127.0.0.1',()=>resolve(`http://127.0.0.1:${server.address().port}`)));
const close=server=>new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});
// Two genuine pages, with a script action/link annotation that our canvas viewer
// deliberately never constructs as interactive HTML or runs.
function pdf(){
 const stream=text=>`<< /Length ${text.length} >>\nstream\n${text}\nendstream`;
 const objects=['<< /Type /Catalog /Pages 2 0 R /OpenAction 8 0 R >>','<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
 '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R /Annots [9 0 R] >>',
 '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
 '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',stream('0.8 0.1 0.1 rg 20 20 240 340 re f 0 g BT /F1 20 Tf 30 200 Td (SYNTHETIC PAGE ONE) Tj ET'),stream('0.1 0.2 0.8 rg 20 20 240 340 re f 0 g BT /F1 20 Tf 30 200 Td (SYNTHETIC PAGE TWO) Tj ET'),
 '<< /S /JavaScript /JS (app.alert("must not execute")) >>','<< /Type /Annot /Subtype /Link /Rect [0 0 300 400] /A << /S /URI /URI (https://forbidden.example/pdf-link) >> >>'];
 let body='%PDF-1.7\n',offsets=[0];objects.forEach((value,index)=>{offsets.push(Buffer.byteLength(body));body+=`${index+1} 0 obj\n${value}\nendobj\n`;});
 const xref=Buffer.byteLength(body);body+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`+offsets.slice(1).map(value=>`${String(value).padStart(10,'0')} 00000 n \n`).join('')+`trailer\n<< /Root 1 0 R /Size ${objects.length+1} >>\nstartxref\n${xref}\n%%EOF\n`;return Buffer.from(body);
}
const bytes=pdf();
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64');
let browser,dir;
before(async()=>{
 dir=await mkdtemp(join(tmpdir(),'sb-kb-pdf-'));await mkdir(join(dir,'knowledge-public'),{recursive:true});
 await cp(join(buildOutput(),'pdfjs'),join(dir,'pdfjs'),{recursive:true});
 const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import PublicShare from './department-ui/knowledge/PublicShare.jsx';import MaterialDrawer from './department-ui/knowledge/MaterialDrawer.jsx';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/pc/knowledge.css';
window.actor={workspaceId:'company',userId:'sales',permissionVersion:1};window.SalesRuntime={app:{globalData:{get session(){return actor;}}},require:name=>name==='utils/access'?{identity:s=>JSON.stringify(s)}:{}};
const service={getMaterial:async id=>{const r=await fetch('/api/v1/knowledge/materials/'+id,{headers:{Authorization:'Bearer synthetic-internal'}});if(!r.ok)throw Error('当前资料不可访问');return r.json();},getCatalog:async()=>({products:[]}),readFile:async ctx=>{const r=await fetch('/api/v1/knowledge/files/'+ctx.file_id+'/'+(ctx.preview?'preview':'download')+'?version_id='+ctx.version_id,{headers:{Authorization:'Bearer synthetic-internal'}});if(!r.ok)throw Error('当前文件不可访问');return {blob:await r.blob(),filename:window.fixtureDocumentName||'内部PDF.pdf'};}};
const node=createRoot(document.getElementById('root'));window.show=()=>node.render(location.hash.includes('internal')?<PcProvider><MaterialDrawer key={actor.userId} materialId="material" service={service} onClose={()=>node.render(null)} onChanged={()=>{}}/></PcProvider>:<PublicShare/>);show();
`},bundle:true,write:false,outdir:'fixture',define:{'process.env.NODE_ENV':'"production"'}});
 await writeFile(join(dir,'knowledge-public/app.js'),built.outputFiles.find(file=>file.path.endsWith('.js')).contents);await writeFile(join(dir,'knowledge-public/app.css'),built.outputFiles.find(file=>file.path.endsWith('.css')).contents);
 await writeFile(join(dir,'knowledge-share.html'),'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="knowledge-public/app.css"><div id="root"></div><script src="knowledge-public/app.js"></script>');
 browser=await chromium.launch({headless:true});
});
after(async()=>{await browser?.close();await rm(dir,{recursive:true,force:true});});
async function fixture(run,{internal=false,questionnaire=false,download=true,documentBytes=bytes,documentMime='application/pdf',documentName='合成PDF.pdf'}={}){
 const state={requests:[],revoked:false,bad:false,hold:false,release:null};
 const file={file_id:'pdf-file',filename:documentName,media_type:documentMime,preview_supported:documentMime==='application/pdf'||documentMime.startsWith('image/'),size_bytes:documentBytes.length};
 const upstream=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');state.requests.push({path:url.pathname,query:url.search,headers:req.headers,method:req.method});
  const json=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  if(state.revoked)return json(404,{detail:'KNOWLEDGE_SHARE_UNAVAILABLE'});
  if(url.pathname.includes('/materials/'))return json(200,{material_id:'material',material_revision:1,content_name:'内部合成材料',capabilities:{read:true,manage:false},versions:[{version_id:'version',version_no:1,state:'saved',files:[file],contribution:{content_name:'内部合成材料',selection:{content_category:'conference_speech'}}}]});
  if(url.pathname.endsWith('/share'))return json(200,{publication_id:'publication',content_name:'合成客户PDF',version_no:1,questionnaire_version:1,questionnaire:{enabled:questionnaire,fields:[{name:'name',enabled:true,required:true}]},allow_preview:true,allow_download:download,files:[file],has_cover:false});
  if(url.pathname.endsWith('/responses')){for await(const _ of req){}return json(200,{publication_id:'publication',questionnaire_version:1,grant_token:'synthetic-grant',expires_at:'2099-01-01T00:00:00Z'});}
  if(url.pathname.includes('/files/')){
   if(!internal&&req.headers['x-knowledge-grant']!=='synthetic-grant')return json(403,{detail:'KNOWLEDGE_QUESTIONNAIRE_REQUIRED'});
   if(internal&&req.headers.authorization!=='Bearer synthetic-internal')return json(403,{detail:'KNOWLEDGE_ACCESS_DENIED'});
   res.writeHead(200,{'Content-Type':documentMime,'Cache-Control':'no-store'});
   if(state.hold){state.release=()=>res.end(documentBytes);return;}return res.end(state.bad?Buffer.from('%PDF-broken'):documentBytes);
  }return json(404,{});
 });
 const target=await listen(upstream),server=createSalesWebServer({staticDir:dir,target:target+'/api/v1',localLogin:null}),base=await listen(server);
 const context=await browser.newContext({viewport:{width:internal?1280:390,height:900}}),page=await context.newPage(),errors=[],external=[],workers=[];page.setDefaultTimeout(5000);
 page.on('pageerror',error=>errors.push(error.message));page.on('worker',worker=>workers.push(worker.url()));page.on('dialog',async dialog=>{errors.push('Unexpected PDF script dialog');await dialog.dismiss();});
 await context.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){external.push(route.request().url());return route.abort();}return route.continue();});
 await page.addInitScript(name=>{window.fixtureDocumentName=name;}, documentName);
 try{await page.goto(base+'/web/knowledge-share.html#'+(internal?'internal=1':'token='+'a'.repeat(48)));await page.getByRole('heading',{name:internal?'内部合成材料':'合成客户PDF',exact:true}).waitFor();await run({page,state,workers,base});assert.deepEqual(errors,[]);assert.deepEqual(external,[]);}catch(error){console.error('PDF fixture failure',errors,await page.locator('body').innerText());throw error;}finally{await context.close();await close(server);await close(upstream);}
}
async function rendered(page){await page.waitForFunction(()=>{const canvas=document.querySelector('.kb-pdf canvas');return canvas&&canvas.width>0&&canvas.height>0&&document.querySelector('.kb-pdf-toolbar')&&!document.querySelector('.kb-pdf [role=status]');});}
async function evidence(page,name){if(process.env.KNOWLEDGE_PDF_EVIDENCE_DIR){await mkdir(process.env.KNOWLEDGE_PDF_EVIDENCE_DIR,{recursive:true});await page.screenshot({path:join(process.env.KNOWLEDGE_PDF_EVIDENCE_DIR,name+'.png'),fullPage:true});}}

test('customer questionnaire gates genuine PDF bytes; self-hosted worker renders two safe mobile pages without download permission',()=>fixture(async({page,state,workers})=>{
 assert.equal(state.requests.some(row=>row.path.includes('/files/')),false);assert.equal(await page.getByRole('button',{name:'下载资料',exact:true}).count(),0);
 await page.getByRole('button',{name:'查看正文',exact:true}).click();await page.getByLabel('姓名',{exact:true}).fill('合成客户');await page.getByRole('button',{name:'提交并访问资料',exact:true}).click();await rendered(page);
 await page.getByText('第 1 / 2 页',{exact:true}).waitFor();const first=await page.locator('canvas').evaluate(canvas=>canvas.toDataURL());
 await page.getByRole('button',{name:'下一页',exact:true}).click();await page.getByText('第 2 / 2 页',{exact:true}).waitFor();await rendered(page);const second=await page.locator('canvas').evaluate(canvas=>canvas.toDataURL());assert.notEqual(first,second);
 await page.getByRole('button',{name:'上一页',exact:true}).click();await rendered(page);assert.equal(await page.locator('.kb-file-preview a, .kb-file-preview iframe').count(),0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.ok(workers.some(url=>url.includes('/web/pdfjs/6.4.299/pdf.worker.min.js')));
 const request=state.requests.find(row=>row.path.endsWith('/preview'));assert.equal(request.headers['x-knowledge-grant'],'synthetic-grant');assert.equal(request.headers.authorization,undefined);await evidence(page,'customer-pdf-synthetic-mobile');
 const stopped=Promise.all(page.workers().map(worker=>new Promise(resolve=>worker.once('close',resolve))));await page.getByRole('button',{name:'关闭正文',exact:true}).click();await stopped;assert.equal(await page.locator('canvas').count(),0);
},{questionnaire:true,download:false}));

test('closing a pending PDF ignores its late bytes and permits the next explicit file read',()=>fixture(async({page,state})=>{
 state.hold=true;const firstRequest=page.waitForRequest(request=>request.url().includes('/preview'));await page.getByRole('button',{name:'查看正文',exact:true}).click();await page.getByText('正在读取文件…',{exact:true}).waitFor();const pending=await firstRequest;
 await page.getByRole('button',{name:'关闭正文',exact:true}).click();state.hold=false;
 await page.getByRole('button',{name:'查看正文',exact:true}).click();await rendered(page);await page.getByRole('button',{name:'下一页',exact:true}).click();await rendered(page);
 const oldResponse=page.waitForResponse(response=>response.request()===pending);state.release();await (await oldResponse).finished();await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));await page.getByText('第 2 / 2 页',{exact:true}).waitFor();assert.equal(state.requests.filter(row=>row.path.endsWith('/preview')).length,2);
}));

test('refresh after revocation clears the rendered PDF and rejects further file access',()=>fixture(async({page,state})=>{
 await page.getByRole('button',{name:'查看正文',exact:true}).click();await rendered(page);state.revoked=true;await page.getByRole('button',{name:'重新读取资料',exact:true}).click();await page.getByRole('alert').waitFor();assert.equal(await page.locator('canvas').count(),0);assert.equal(await page.getByRole('button',{name:'查看正文',exact:true}).count(),0);
}));

test('malformed PDF shows a recoverable loading error and close removes the failed document',()=>fixture(async({page,state})=>{
 state.bad=true;await page.getByRole('button',{name:'查看正文',exact:true}).click();await page.getByText('PDF 加载失败，请关闭后重新读取；若仍失败，请联系资料提供人。',{exact:true}).waitFor();await page.getByRole('button',{name:'关闭正文',exact:true}).click();state.bad=false;await page.getByRole('button',{name:'查看正文',exact:true}).click();await rendered(page);
}));

test('internal material preview uses authenticated version bytes and clears on denied refresh',()=>fixture(async({page,state})=>{
 await page.getByRole('button',{name:/^预\s*览$/}).click();await rendered(page);const request=state.requests.find(row=>row.path.endsWith('/preview'));assert.equal(request.headers.authorization,'Bearer synthetic-internal');assert.equal(request.query,'?version_id=version');assert.equal(request.headers['x-knowledge-share'],undefined);await evidence(page,'internal-pdf-synthetic-desktop');
 state.revoked=true;await page.getByRole('button',{name:'重新读取',exact:true}).click();await page.getByText('当前资料不可访问',{exact:true}).waitFor();assert.equal(await page.locator('canvas').count(),0);
},{internal:true}));

test('internal archived image preview renders a safe blob and releases its object URL',()=>fixture(async({page})=>{
 await page.getByRole('button',{name:/^预\s*览$/}).click();
 await page.locator('.kb-file-preview-image').waitFor();
 assert.equal(await page.locator('.kb-file-preview-image').getAttribute('alt'),'内部PNG.png');
 assert.equal(await page.locator('.kb-file-preview [role=alert]').count(),0);
 await page.getByRole('button',{name:'关闭正文',exact:true}).click();
 assert.equal(await page.locator('.kb-file-preview').count(),0);
},{internal:true,documentBytes:png,documentMime:'image/png',documentName:'内部PNG.png'}));

test('internal archived file exposes a fresh ACL-backed new-page browser action',()=>fixture(async({page,state})=>{
 const popup=page.waitForEvent('popup');
 const previewResponse=page.waitForResponse(response=>response.url().includes('/knowledge/files/pdf-file/preview'));
 await page.getByRole('button',{name:'新页浏览',exact:true}).click();
 const opened=await popup;
 await previewResponse;
 await opened.waitForLoadState();
 assert.equal(state.requests.filter(row=>row.path.endsWith('/preview')).length,1);
 assert.equal(state.requests.find(row=>row.path.endsWith('/preview')).headers.authorization,'Bearer synthetic-internal');
 await opened.close();
},{internal:true}));

test('internal identity switch ignores a previous preview still in flight',()=>fixture(async({page,state})=>{
 state.hold=true;await page.getByRole('button',{name:/^预\s*览$/}).click();await page.getByText('正在读取文件…',{exact:true}).waitFor();await page.evaluate(()=>{actor={...actor,userId:'other'};show();});await page.locator('.kb-file-preview').waitFor({state:'detached'});await page.getByRole('heading',{name:'内部合成材料',exact:true}).waitFor();state.release();await page.getByRole('button',{name:/^预\s*览$/}).waitFor();assert.equal(await page.locator('.kb-file-preview').count(),0);
},{internal:true}));

// Optional local-only real document evidence. Never copies the PDF or its rendered
// pages into repository/public artifacts and makes no external request.
test('authorized real large PDF renders first/middle/last mobile pages and releases its worker', {skip:!process.env.KNOWLEDGE_PDF_REAL_FILE}, async()=>{
 const documentBytes=await readFile(process.env.KNOWLEDGE_PDF_REAL_FILE);
 await fixture(async({page})=>{
  await page.getByRole('button',{name:'查看正文',exact:true}).click();await rendered(page);
  const count=Number((await page.locator('.kb-pdf-toolbar span').innerText()).match(/\/ (\d+) 页/)[1]);assert.equal(count,31);
  const first=await page.locator('canvas').evaluate(c=>c.toDataURL());
  for(let i=1;i<16;i++)await page.getByRole('button',{name:'下一页',exact:true}).click();await rendered(page);
  await page.getByText('第 16 / 31 页',{exact:true}).waitFor();const middle=await page.locator('canvas').evaluate(c=>c.toDataURL());
  for(let i=16;i<count;i++)await page.getByRole('button',{name:'下一页',exact:true}).click();await rendered(page);
  await page.getByText('第 31 / 31 页',{exact:true}).waitFor();const last=await page.locator('canvas').evaluate(c=>c.toDataURL());
  assert.notEqual(first,middle);assert.notEqual(middle,last);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  const stopped=Promise.all(page.workers().map(worker=>new Promise(resolve=>worker.once('close',resolve))));await page.getByRole('button',{name:'关闭正文',exact:true}).click();await stopped;assert.equal(await page.locator('canvas').count(),0);
 },{documentBytes});
});
