// Real React/AntD components and service adapter; isolated synthetic API responses.
// These tests do not claim provider, PostgreSQL, production or customer acceptance.
import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {mkdir, readFile} from 'node:fs/promises';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const output = process.env.KNOWLEDGE_WORK_EVIDENCE_DIR;
if (output) await mkdir(output, {recursive: true});
const id = n => `00000000-0000-0000-0000-${String(n).padStart(12, '0')}`;
const bundle = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import Knowledge from './department-ui/Knowledge.jsx';import {PcProvider} from './department-ui/pc/PcProvider.jsx';
import {createKnowledgeService} from './department-ui/knowledge/service.js';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
const id=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
window.actor={workspaceId:id(1),userId:id(2),permissionVersion:1};
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:()=>true},require:name=>name==='utils/access'?{identity:s=>JSON.stringify(s)}:{}};
window.getApp=()=>SalesRuntime.app;
window.probe={calls:[],provider:false,statusFailure:false,mode:'normal',reads:0,receipts:new Map(),generationFailures:new Map(),revoked:false};
probe.work={work_id:id(3),job_id:id(3),owner_user_id:id(2),title:'客户方案作品',purpose:'梳理客户部署方案',format:'docx',audience:'internal',status:'succeeded',can_manage:true,revision:3,version_no:1,recipients:[{subject_kind:'user',subject_id:id(8),display_name:'原有接收人'}],sources:[{material_id:id(4),version_id:id(5),content_name:'参考资料',version_no:1}],files:[{file_id:id(6),filename:'客户方案.docx'}]};
const client={request:async options=>{
 probe.calls.push(structuredClone(options));const url=new URL('http://fixture'+options.path),p=url.pathname;
 if(p==='/knowledge/provider/status'){if(probe.statusFailure)throw Error('生成服务暂不可用');return {retrieval:{status:'unconfigured'},generation:{status:probe.provider?'available':'unconfigured',message:probe.provider?'已配置，生成时验证连接；当前开放 DOCX':'材料生成尚未配置',formats:probe.provider?['docx']:[]}};}
 if(p==='/knowledge/works'&&options.method==='POST'){if(probe.generationFailures.has(options.idempotencyKey))throw Object.assign(Error('private provider detail'),probe.generationFailures.get(options.idempotencyKey));if(probe.generationError){const error=probe.generationError;probe.generationError=null;if(probe.sealGenerationFailure)probe.generationFailures.set(options.idempotencyKey,error);throw Object.assign(Error('private provider detail'),error);}if(probe.mode==='generation-timeout'){probe.mode='normal';throw Object.assign(Error('request timeout'),{code:'NETWORK_ERROR'});}return {job_id:id(12),status:'succeeded'};}
 if(p==='/knowledge/works')return {items:actor.userId===id(2)?[structuredClone(probe.work)]:[],total:actor.userId===id(2)?1:0,limit:20,offset:Number(url.searchParams.get('offset'))};
 if(p==='/knowledge/materials')return {items:[{material_id:id(4),active_version_id:id(5),active_version_state:'retrievable',version_id:id(5),version_no:1,content_name:'参考资料'},{material_id:id(14),active_version_id:id(15),active_version_state:'retrievable',version_id:id(15),version_no:1,content_name:'补充材料'}],total:2,capabilities:{create:true}};
 if(p==='/knowledge/works/'+id(3)){probe.reads++;if(probe.revoked)throw Error('来源资料权限已撤回');return structuredClone(probe.work);}
 if(p.endsWith('/share-candidates')){const kind=url.searchParams.get('subject_kind'),q=url.searchParams.get('q'),offset=Number(url.searchParams.get('offset'));const rows=kind==='team'?[{subject_id:id(9),display_name:'客户方案团队'}]:[{subject_id:id(10),display_name:'新接收成员'}];const items=rows.filter(row=>!q||row.display_name.includes(q));return {work_id:id(3),subject_kind:kind,items:items.slice(offset,offset+20),total:items.length,limit:20,offset};}
 if(p.endsWith('/publish')){
  if(probe.mode==='pending')await new Promise(resolve=>window.releasePublish=resolve);
  if(probe.receipts.has(options.idempotencyKey))return structuredClone(probe.receipts.get(options.idempotencyKey));
  if(probe.mode==='conflict'){probe.work.revision++;probe.mode='normal';throw Object.assign(Error('KNOWLEDGE_WORK_CHANGED'),{serverCode:'KNOWLEDGE_WORK_CHANGED',statusCode:409});}
  if(probe.mode==='denied')throw Object.assign(Error('没有共享权限'),{statusCode:403});
  probe.work={...probe.work,revision:probe.work.revision+1,recipients:options.data.recipients.map(row=>({...row,display_name:row.subject_kind==='team'?'客户方案团队':row.subject_id===id(8)?'原有接收人':'新接收成员'}))};
  probe.receipts.set(options.idempotencyKey,structuredClone(probe.work));
  if(probe.mode==='timeout'){probe.mode='normal';throw Error('保存响应中断，请重试确认');}
  return structuredClone(probe.work);
 }
 throw Error('unexpected '+p);
},readKnowledgeFile:async(fileId,options)=>{probe.calls.push({binary:{fileId,...options}});if(probe.revoked)throw Error('来源资料权限已撤回');if(probe.mode==='pending-file')await new Promise(resolve=>window.releaseFile=resolve);return {data:new TextEncoder().encode('fixture archived document').buffer,contentType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',filename:'客户方案.docx'};}};
const service=createKnowledgeService(client),node=createRoot(document.getElementById('root'));
window.show=()=>node.render(<PcProvider><Knowledge service={service} page={{options:{tab:'works'}}}/></PcProvider>);show();
`},bundle: true, write: false, outdir: '.runtime/knowledge-work-sharing-browser', loader: {'.svg':'dataurl'}, define: {'process.env.NODE_ENV':'"production"'}});
const js = bundle.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = bundle.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel:'chrome', headless:true});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport:{width:1440,height:1000}}), page = await context.newPage(), errors = [];
  page.setDefaultTimeout(10000);page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => route.request().url() === 'http://localhost/' ? route.fulfill({contentType:'text/html; charset=utf-8',body:'<meta charset="UTF-8"><div id="root" class="department-ui"></div><style>'+css+'</style><script>'+js+'</script>'}) : route.abort());
  try {await page.goto('http://localhost/');await page.getByRole('heading',{name:'客户方案作品',exact:true}).waitFor();await run(page);assert.deepEqual(errors,[]);} finally {await context.close();}
}
async function openSharing(page) {await page.getByRole('button',{name:'管理作品共享',exact:true}).click();await page.getByText('成员：原有接收人',{exact:true}).waitFor();}
async function addTeam(page) {await page.getByRole('combobox',{name:'作品共享对象',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('客户方案团队',{exact:true}).click();await page.getByRole('button',{name:'添加接收对象',exact:true}).click();}
test('archived works refresh and download without a configured generation provider', () => fixture(async page => {
  await page.getByRole('button',{name:'新建材料',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'提交生成任务'}).isDisabled(),true);
  await page.locator('.ant-drawer-close').click();
  assert.equal(await page.getByRole('button',{name:'刷新作品'}).isEnabled(),true);
  const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'下载 客户方案.docx'}).click();const file=await downloaded;
  assert.equal(file.suggestedFilename(),'客户方案.docx');assert.equal(await readFile(await file.path(),'utf8'),'fixture archived document');
  const binary=await page.evaluate(()=>{const {fileId,workId,preview}=probe.calls.find(row=>row.binary).binary;return {fileId,workId,preview};});assert.deepEqual(binary,{fileId:id(6),workId:id(3),preview:false});
  await page.getByRole('button',{name:'刷新作品'}).click();await page.getByRole('heading',{name:'客户方案作品',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.path?.startsWith('/knowledge/works?')).length),2);
  if(output)await page.screenshot({path:output+'/archived-works.png',fullPage:true});
}));
test('sharing reads current work, preserves named recipients, and saves/revokes exact recipient scope', () => fixture(async page => {
  await openSharing(page);assert.equal(await page.evaluate(()=>probe.reads),1);await addTeam(page);
  await page.getByText('团队：客户方案团队',{exact:true}).waitFor();assert.equal(await page.locator('.ant-drawer').getByText(id(8),{exact:false}).count(),0);
  if(output){await page.getByRole('heading',{name:'当前接收范围',exact:true}).click();await page.waitForTimeout(400);await page.screenshot({path:output+'/work-sharing.png',fullPage:true});}
  await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText(/作品共享范围已保存/).waitFor();
  const call=await page.evaluate(()=>probe.calls.find(row=>row.method==='POST'));
  assert.equal(call.path,`/knowledge/works/${id(3)}/publish`);assert.equal(call.data.expected_revision,3);assert.deepEqual(call.data.recipients,[{subject_kind:'team',subject_id:id(9)},{subject_kind:'user',subject_id:id(8)}]);assert.match(call.idempotencyKey,/^[a-f0-9-]{36}$/);
  await openSharing(page);await page.getByRole('button',{name:'撤销全部共享'}).click();await page.getByText(/已撤销全部共享/).waitFor();
  const revoke=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').at(-1));assert.deepEqual(revoke.data,{expected_revision:4,recipients:[]});assert.notEqual(revoke.idempotencyKey,call.idempotencyKey);
}));
test('uncertain save displays failure and reuses the same intent key without premature success', () => fixture(async page => {
  await openSharing(page);await addTeam(page);await page.evaluate(()=>probe.mode='timeout');await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText('保存响应中断，请重试确认',{exact:true}).waitFor();
  assert.equal(await page.getByText(/作品共享范围已保存/).count(),0);await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText(/作品共享范围已保存/).waitFor();
  const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls.length,2);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(calls[0].data,calls[1].data);assert.equal(await page.evaluate(()=>probe.work.revision),4);
}));
test('version conflict rereads settings and requires a new explicit save with fresh revision', () => fixture(async page => {
  await openSharing(page);await addTeam(page);await page.evaluate(()=>probe.mode='conflict');await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText(/共享范围已发生变化/).waitFor();await page.getByText('成员：原有接收人',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'保存共享范围'}).isDisabled(),true);assert.equal(await page.evaluate(()=>probe.reads),2);assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
  await addTeam(page);await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText(/作品共享范围已保存/).waitFor();
  const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls[1].data.expected_revision,4);assert.notEqual(calls[0].idempotencyKey,calls[1].idempotencyKey);
}));
test('non-owner or revoked source cannot edit or download, even if the list is stale', () => fixture(async page => {
  await page.evaluate(()=>probe.work.can_manage=false);await page.getByRole('button',{name:'管理作品共享'}).click();await page.getByText('只有作品作者可以管理共享范围',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'保存共享范围'}).count(),0);
  await page.locator('.ant-drawer-close').click();await page.getByRole('button',{name:'刷新作品'}).click();await page.getByText('共享给我',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'管理作品共享'}).count(),0);assert.equal(await page.getByRole('button',{name:'生成新版本',exact:true}).count(),0);
  await page.evaluate(()=>probe.revoked=true);await page.getByRole('button',{name:'下载 客户方案.docx'}).click();await page.getByText('来源资料权限已撤回',{exact:true}).waitFor();
}));
test('account switch drops a pending share receipt and old work state', () => fixture(async page => {
  await openSharing(page);await addTeam(page);await page.evaluate(()=>probe.mode='pending');await page.getByRole('button',{name:'保存共享范围'}).click();await page.waitForFunction(()=>typeof releasePublish==='function');
  await page.evaluate(()=>{actor={...actor,userId:'00000000-0000-0000-0000-000000000099'};show();releasePublish();});await page.getByText('暂无作品',{exact:true}).waitFor();
  assert.equal(await page.getByText(/作品共享范围已保存/).count(),0);assert.equal(await page.getByText('客户方案作品',{exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'保存共享范围'}).count(),0);
}));
test('new version rereads owner work and submits lineage only after explicit generate action', () => fixture(async page => {
  await page.evaluate(()=>{probe.provider=true;actor={...actor,permissionVersion:2};show();});await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).inputValue(),'梳理客户部署方案');assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),0);
  assert.equal(await page.getByRole('button',{name:'提交生成新版本',exact:true}).isEnabled(),true);await page.getByText('已配置，生成时验证连接；当前开放 DOCX',{exact:true}).waitFor();
  await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/已收到任务/).waitFor();
  const call=await page.evaluate(()=>probe.calls.find(row=>row.method==='POST'));assert.equal(call.path,'/knowledge/works');assert.equal(call.data.parent_work_id,id(3));assert.equal(call.data.expected_parent_revision,3);assert.deepEqual(call.data.material_ids,[id(4)]);assert.equal('recipients' in call.data,false);
}));

test('provider status failure does not hide archives; member sharing resolves names and denied writes remain failures', () => fixture(async page => {
  await page.evaluate(()=>{probe.statusFailure=true;actor={...actor,permissionVersion:2};show();});await page.getByText('生成服务暂不可用',{exact:true}).waitFor();await openSharing(page);
  await page.locator('.ant-drawer').getByText('成员',{exact:true}).click();await page.getByRole('combobox',{name:'作品共享对象',exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText('新接收成员',{exact:true}).click();await page.getByRole('button',{name:'添加接收对象',exact:true}).click();
  await page.getByText('成员：新接收成员',{exact:true}).waitFor();await page.evaluate(()=>probe.mode='denied');await page.getByRole('button',{name:'保存共享范围'}).click();await page.getByText('没有共享权限',{exact:true}).waitFor();
  assert.equal(await page.getByText(/作品共享范围已保存/).count(),0);const call=await page.evaluate(()=>probe.calls.find(row=>row.method==='POST'));assert.deepEqual(call.data.recipients,[{subject_kind:'user',subject_id:id(8)},{subject_kind:'user',subject_id:id(10)}]);
}));

test('generation unknown outcome locks its input, leaves archive refresh available and retries the same intent', () => fixture(async page => {
  await page.evaluate(()=>{probe.provider=true;actor={...actor,permissionVersion:2};show();});await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();
  await page.evaluate(()=>probe.mode='generation-timeout');await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/生成响应尚未确认/).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'刷新作品'}).isEnabled(),true);assert.equal(await page.getByText(/已收到任务/).count(),0);
  await page.getByRole('button',{name:'确认原生成请求'}).click();await page.getByText('生成已完成，请在作品中核对归档文件。',{exact:true}).waitFor();
  const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls.length,2);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(calls[0].data,calls[1].data);assert.equal(calls[0].timeout,90000);
}));

for(const code of ['KNOWLEDGE_DOCUMENT_SOURCE_LIMIT','KNOWLEDGE_DOCUMENT_INPUT_INVALID'])test(`generation 422 ${code} allows correction and a new explicit intent`,()=>fixture(async page=>{
  await page.evaluate(code=>{probe.provider=true;probe.work.sources.push({material_id:'00000000-0000-0000-0000-000000000014',version_id:'00000000-0000-0000-0000-000000000015',content_name:'补充材料'});probe.generationError={serverCode:code,statusCode:422};actor={...actor,permissionVersion:2};show();},code);
  await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();
  await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/本次未发起文档生成/).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isEnabled(),true);assert.equal(await page.getByRole('combobox',{name:'选择参考材料'}).isEnabled(),true);
  assert.equal(await page.getByText(/private provider detail/).count(),0);assert.equal(await page.getByText(/已收到任务/).count(),0);assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
  await page.locator('.ant-select-selection-item').filter({hasText:'补充材料'}).locator('.ant-select-selection-item-remove').click();
  await page.getByRole('textbox',{name:'材料用途'}).fill('缩小范围后的方案');await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/已收到任务/).waitFor();
  const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls.length,2);assert.notEqual(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(calls[0].data.material_ids,[id(4),id(14)]);assert.deepEqual(calls[1].data.material_ids,[id(4)]);assert.equal(calls[1].data.purpose,'缩小范围后的方案');
}));

for(const code of ['KNOWLEDGE_DOCUMENT_RESULT_UNKNOWN','KNOWLEDGE_DOCUMENT_TIMEOUT','KNOWLEDGE_DOCUMENT_RESPONSE_INVALID','KNOWLEDGE_DOCUMENT_FUTURE_ERROR'])test(`generation 503 unknown ${code} keeps the original intent and does not unlock or automatically resend`,()=>fixture(async page=>{
  await page.evaluate(code=>{probe.provider=true;probe.generationError={serverCode:code,statusCode:503};actor={...actor,permissionVersion:2};show();},code);
  await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/生成响应尚未确认/).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isDisabled(),true);assert.equal(await page.getByRole('combobox',{name:'选择参考材料'}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'刷新作品'}).isEnabled(),true);
  assert.equal(await page.getByText(/本次未发起文档生成/).count(),0);assert.equal(await page.getByRole('button',{name:'准备新的生成请求'}).count(),0);assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
  await page.getByRole('button',{name:'确认原生成请求'}).click();await page.getByText(/已收到任务/).waitFor();const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls.length,2);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(calls[0].data,calls[1].data);
}));

for(const code of [
  'KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE','KNOWLEDGE_DOCUMENT_AUTH_UNAVAILABLE',
  'KNOWLEDGE_DOCUMENT_RUN_FAILED','KNOWLEDGE_DOCUMENT_RUN_STOPPED',
  'KNOWLEDGE_DOCUMENT_FILES_INVALID','KNOWLEDGE_DOCUMENT_FILES_LIMIT',
  'KNOWLEDGE_ARTIFACT_DOWNLOAD_URL_NOT_ALLOWED','KNOWLEDGE_ARTIFACT_DOWNLOAD_TIMEOUT',
  'KNOWLEDGE_ARTIFACT_UNSAFE_CONTENT',
])test(`generation 503 sealed ${code} reports no archive and needs an explicit new intent`,()=>fixture(async page=>{
  await page.evaluate(code=>{probe.provider=true;probe.sealGenerationFailure=true;probe.generationError={serverCode:code,statusCode:503};actor={...actor,permissionVersion:2};show();},code);
  await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();
  await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/未生成可下载作品/).waitFor();
  if(code==='KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE')await page.getByText(/文件未能取得/).waitFor();
  assert.equal(await page.getByText(/生成响应尚未确认|private provider detail|已收到任务/).count(),0);
  assert.equal(await page.getByRole('button',{name:'确认原生成请求'}).count(),0);
  assert.equal(await page.getByRole('button',{name:'提交生成新版本',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'生成新版本',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:'改为新建作品'}).isDisabled(),true);
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isDisabled(),true);
  await page.locator('.ant-drawer-close').click();await page.getByRole('button',{name:'刷新作品'}).click();await page.getByRole('heading',{name:'客户方案作品',exact:true}).waitFor();await page.getByRole('button',{name:'新建材料',exact:true}).click();
  assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
  await page.getByRole('button',{name:'准备新的生成请求'}).click();
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).inputValue(),'梳理客户部署方案');
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isEnabled(),true);
  assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
  await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/已收到任务/).waitFor();
  const calls=await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST'));assert.equal(calls.length,2);assert.notEqual(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(calls[0].data,calls[1].data);
}));

test('generation new intent cannot bypass an unconfigured provider after permissions refresh',()=>fixture(async page=>{
  await page.evaluate(()=>{probe.provider=true;probe.sealGenerationFailure=true;probe.generationError={serverCode:'KNOWLEDGE_DOCUMENT_DOWNLOAD_UNAVAILABLE',statusCode:503};actor={...actor,permissionVersion:2};show();});
  await page.getByRole('button',{name:'生成新版本',exact:true}).click();await page.getByRole('heading',{name:'生成作品新版本',exact:true}).waitFor();
  await page.getByRole('button',{name:'提交生成新版本',exact:true}).click();await page.getByText(/未生成可下载作品/).waitFor();
  await page.getByRole('button',{name:'准备新的生成请求'}).click();
  await page.evaluate(()=>{probe.provider=false;actor={...actor,permissionVersion:3};show();});await page.getByText('材料生成尚未配置',{exact:true}).waitFor();
  await page.getByRole('button',{name:'新建材料',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'提交生成任务',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'生成新版本',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('textbox',{name:'材料用途'}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'准备新的生成请求'}).count(),0);
  assert.equal(await page.evaluate(()=>probe.calls.filter(row=>row.method==='POST').length),1);
}));
