// Synthetic API fixtures for UI behavior. Real V274 API/RLS acceptance is separate.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir} from 'node:fs/promises';
import {join} from 'node:path';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const built=await build({absWorkingDir:root,stdin:{resolveDir:root,loader:'jsx',contents:`
import React from 'react';import {createRoot} from 'react-dom/client';import {PcProvider} from './department-ui/pc/PcProvider.jsx';import MaterialDrawer from './department-ui/knowledge/MaterialDrawer.jsx';import {createKnowledgeService} from './department-ui/knowledge/service.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/pc/knowledge.css';
window.id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');window.actor={workspaceId:id(1),userId:id(2),permissionVersion:1};window.manager=true;window.calls=[];window.changed=0;window.postFail=false;window.postDenied=false;window.delayUpload=false;window.uploadFail=false;
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:()=>manager},require:name=>name==='utils/access'?{identity:s=>JSON.stringify(s),scopeFor:()=>({scopes:manager?['workspace']:[]})}:{}};
const attachment={source_token:'source1',name:'source.pdf',size_bytes:7,media_type:'application/pdf'},existing={source_token:'source2',name:'older.pdf',size_bytes:9,media_type:'application/pdf',file_id:id(20),sha256:'b'.repeat(64)};
window.source={material_id:id(4),version_id:id(5),version_no:1,material_revision:3,acl_revision:4,source_revision:15,source_status:'可用',can_archive:true,attachments:[attachment,existing],files:[existing],changed:false};
const contribution={kind:'legacy_import',company_id:id(1),content_name:'原表合成材料',selection:{product_code:'legacy_product',content_category:'product_intro',content_scope:'internal'},source:{provider:'feishu_base',base_id:'base',table_id:'table',record_id:'record',source_revision:7,original_name:'原表合成材料',product_label:'原产品',category_label:'产品介绍',scope_label:'内部',source_status:'可用',provider_name:'合成维护人',links:[],attachments:[attachment,existing],remarks:'保留原字段',validation_issues:[]}};
window.row={material_id:id(4),material_revision:3,acl_revision:4,content_name:'原表合成材料',can_edit:false,can_manage:true,source:contribution.source,versions:[{version_id:id(5),version_no:1,state:'saved',contribution,files:[{file_id:id(20),filename:'older.pdf'}]}]};window.receipts={};
const client={uploadKnowledgeFile:async(file,options)=>{calls.push({upload:file.name,size:file.size,key:options.idempotencyKey});if(delayUpload)await new Promise(resolve=>window.releaseUpload=resolve);if(uploadFail){uploadFail=false;throw Object.assign(Error('upload incomplete'),{statusCode:503});}return {file_id:id(6),filename:file.name,size_bytes:file.size,sha256:'a'.repeat(64),media_type:file.type};},request:async options=>{calls.push(options);const path=options.path.split('?')[0];
 if(path==='/knowledge/catalog')return {products:[]};
 if(path.endsWith('/acl-candidates'))return {material_id:id(4),subject_kind:'team',items:[],total:0,limit:20,offset:0};
 if(path.endsWith('/acl'))return {grants:[],denied_user_ids:[]};
 if(path==='/knowledge/materials/'+id(4))return structuredClone({...row,can_manage:manager});
 if(path.endsWith('/source-files')){if(!options.method)return structuredClone(source);if(postDenied)throw Object.assign(Error('KNOWLEDGE_SOURCE_ARCHIVE_DENIED'),{statusCode:403});
  if(receipts[options.idempotencyKey])return structuredClone(receipts[options.idempotencyKey]);
  const added={...options.data.attachments[0],sha256:'a'.repeat(64),media_type:'application/pdf'};source={...source,version_id:id(7),version_no:2,material_revision:4,files:[existing,added],changed:true};row={...row,material_revision:4,versions:[{...row.versions[0],version_id:id(7),version_no:2,files:[{file_id:id(20),filename:'older.pdf'},{file_id:id(6),filename:'source.pdf'}]},...row.versions]};receipts[options.idempotencyKey]=structuredClone(source);if(postFail){postFail=false;throw Object.assign(Error('https://private-provider/?secret'),{statusCode:503});}return structuredClone(source);
 }throw Error('Unexpected fixture route '+path);
}};
const service=createKnowledgeService(client),node=createRoot(document.getElementById('root'));window.show=()=>node.render(<PcProvider><MaterialDrawer key={actor.userId+manager} materialId={id(4)} service={service} onClose={()=>node.render(null)} onChanged={()=>{changed++;}}/></PcProvider>);show();
`},bundle:true,write:false,outdir:'fixture',define:{'process.env.NODE_ENV':'"production"'}});
const js=built.outputFiles.find(file=>file.path.endsWith('.js')).text,css=built.outputFiles.find(file=>file.path.endsWith('.css')).text;
let browser;before(async()=>{browser=await chromium.launch({headless:true});});after(async()=>{await browser?.close();});
async function fixture(run){const context=await browser.newContext({viewport:{width:1300,height:1000}}),page=await context.newPage(),errors=[];page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));await context.route('**/*',route=>route.request().url()==='http://localhost/'?route.fulfill({contentType:'text/html',body:'<meta charset="utf-8"><div id="root" class="department-ui"></div><style>'+css+'</style><script>'+js+'</script>'}):route.abort());try{await page.goto('http://localhost/');await page.getByRole('heading',{name:'原表合成材料',exact:true}).waitFor();await run(page);assert.deepEqual(errors,[]);}catch(error){throw Error(error.message+'\n'+await page.locator('body').innerText(),{cause:error});}finally{await context.close();}}
async function open(page){await page.getByRole('button',{name:'归档源附件',exact:true}).click();await page.getByText('原表状态：可用 · 当前材料 V1',{exact:true}).waitFor();}
async function select(page,name='source.pdf',bytes='1234567'){await page.getByLabel('选择源附件 source.pdf',{exact:true}).setInputFiles({name,mimeType:'application/pdf',buffer:Buffer.from(bytes)});}
const posts=page=>page.evaluate(()=>calls.filter(row=>row.method==='POST'));

test('archive uses fresh source revision, adds selected file to existing material, keeps old files and fields',()=>fixture(async page=>{
 await open(page);await select(page);await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByText('源附件已归档为 V2',{exact:false}).waitFor();
 const requests=await posts(page);assert.equal(requests.length,1);assert.equal(requests[0].path,'/knowledge/materials/00000000-0000-0000-0000-000000000004/source-files');assert.deepEqual(requests[0].data,{expected_material_revision:3,expected_acl_revision:4,expected_source_revision:15,attachments:[{source_token:'source1',name:'source.pdf',size_bytes:7,file_id:'00000000-0000-0000-0000-000000000006'}]});
 assert.equal(await page.evaluate(()=>calls.filter(row=>row.upload).length),1);assert.equal(await page.evaluate(()=>row.versions[0].files.length),2);assert.equal(await page.evaluate(()=>row.versions[0].contribution.source.remarks),'保留原字段');assert.equal(await page.evaluate(()=>changed),1);await page.getByText('已归档附件可在上方按权限预览或下载',{exact:false}).waitFor();
}));
test('ordinary reader never opens the maintenance API or sees archive controls',()=>fixture(async page=>{
 await page.evaluate(()=>{manager=false;show();});await page.getByRole('button',{name:'归档源附件',exact:true}).waitFor({state:'detached'});assert.equal(await page.evaluate(()=>calls.filter(row=>row.path?.endsWith('/source-files')).length),0);
}));
test('server can_archive=false disables selection without inventing permission',()=>fixture(async page=>{
 await page.evaluate(()=>{source.can_archive=false;});await open(page);assert.equal(await page.getByLabel('选择源附件 source.pdf').isDisabled(),true);assert.equal(await page.getByRole('button',{name:'确认归档源附件',exact:true}).isDisabled(),true);assert.equal((await posts(page)).length,0);
}));
test('local mismatched name/size fails before bytes upload',()=>fixture(async page=>{
 await open(page);await select(page,'renamed.pdf');await page.getByText('所选文件名称或大小与原表不一致',{exact:false}).waitFor();assert.equal(await page.getByRole('button',{name:'确认归档源附件',exact:true}).isDisabled(),true);assert.equal(await page.evaluate(()=>calls.filter(row=>row.upload).length),0);
}));
test('a source or permission revision change before save stops before uploading',()=>fixture(async page=>{
 await open(page);await select(page);await page.evaluate(()=>{source.acl_revision++;});await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByText('材料、共享权限或飞书来源已变化',{exact:false}).waitFor();assert.equal((await posts(page)).length,0);assert.equal(await page.evaluate(()=>calls.filter(row=>row.upload).length),0);
}));
test('unknown archive result retains exact body/key and confirms once without reupload or auto retry',()=>fixture(async page=>{
 await open(page);await select(page);await page.evaluate(()=>{postFail=true;});await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByRole('button',{name:'确认原归档',exact:true}).waitFor();assert.equal((await posts(page)).length,1);assert.doesNotMatch(await page.locator('body').innerText(),/private-provider|secret/);assert.equal(await page.getByLabel('选择源附件 source.pdf').isDisabled(),true);
 await page.getByRole('button',{name:'确认原归档',exact:true}).click();await page.getByText('源附件已归档为 V2',{exact:false}).waitFor();const requests=await posts(page);assert.equal(requests.length,2);assert.equal(requests[0].idempotencyKey,requests[1].idempotencyKey);assert.deepEqual(requests[0].data,requests[1].data);assert.equal(await page.evaluate(()=>calls.filter(row=>row.upload).length),1);
}));
test('identity switching during upload cannot attach the prior account file',()=>fixture(async page=>{
 await open(page);await select(page);await page.evaluate(()=>{delayUpload=true;});await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.waitForFunction(()=>typeof releaseUpload==='function');await page.evaluate(()=>{actor={...actor,userId:id(99)};show();});await page.getByRole('button',{name:'归档源附件',exact:true}).waitFor();await page.evaluate(()=>releaseUpload());assert.equal((await posts(page)).length,0);assert.equal(await page.evaluate(()=>changed),0);
}));
test('server permission denial after upload never renders success',()=>fixture(async page=>{
 await open(page);await select(page);await page.evaluate(()=>{postDenied=true;});await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByText('当前没有归档源附件的权限',{exact:false}).waitFor();assert.equal(await page.evaluate(()=>changed),0);assert.equal(await page.getByText('源附件已归档为 V2',{exact:false}).count(),0);
}));

test('unknown upload uses the same upload key on explicit confirmation and never attaches missing bytes',()=>fixture(async page=>{
 await open(page);await select(page);await page.evaluate(()=>{uploadFail=true;});await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByText('本次处理结果尚未确认',{exact:false}).waitFor();assert.equal((await posts(page)).length,0);assert.equal(await page.evaluate(()=>calls.filter(row=>row.upload).length),1);
 await page.getByRole('button',{name:'确认归档源附件',exact:true}).click();await page.getByText('源附件已归档为 V2',{exact:false}).waitFor();const uploads=await page.evaluate(()=>calls.filter(row=>row.upload));assert.equal(uploads.length,2);assert.equal(uploads[0].key,uploads[1].key);assert.equal((await posts(page)).length,1);
}));

test('Chinese file button opens the picker and a matching choice enables explicit confirmation',()=>fixture(async page=>{
 await open(page);const chooserReady=page.waitForEvent('filechooser');await page.getByRole('button',{name:'选择原文件 source.pdf',exact:true}).click();const chooser=await chooserReady;
 await chooser.setFiles({name:'source.pdf',mimeType:'application/pdf',buffer:Buffer.from('1234567')});await page.getByText('已选择：source.pdf',{exact:true}).waitFor();
 await page.waitForFunction(()=>!document.querySelector('[aria-label="确认归档源附件"]').disabled);assert.equal(await page.getByRole('button',{name:'确认归档源附件',exact:true}).isEnabled(),true);
 assert.equal(await page.getByLabel('选择源附件 source.pdf',{exact:true}).isVisible(),false);assert.equal((await posts(page)).length,0);
 if(process.env.KNOWLEDGE_ARCHIVE_EVIDENCE_DIR){await mkdir(process.env.KNOWLEDGE_ARCHIVE_EVIDENCE_DIR,{recursive:true});await page.getByRole('button',{name:'确认归档源附件',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:join(process.env.KNOWLEDGE_ARCHIVE_EVIDENCE_DIR,'source-archive-synthetic-desktop.png'),fullPage:true,animations:'disabled'});}
}));
