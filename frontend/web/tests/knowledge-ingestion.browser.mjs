/** Synthetic V268 HTTP-shape fixtures: UI/transport verification, not provider or LOCAL acceptance. */
import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import Knowledge from './department-ui/Knowledge.jsx';
import {createKnowledgeService} from './department-ui/knowledge/service.js';import {catalog,id,importedMaterial} from './tests/knowledge-import-fixture.mjs';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.actor={workspaceId:id(1),userId:id(2),permissionVersion:1};window.calls=[];window.postFail=false;window.delayState=false;window.autoQueue=true;
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:()=>true},require:name=>name==='utils/access'?{identity:session=>JSON.stringify(session)}:{}};window.getApp=()=>SalesRuntime.app;
const selection={expected_catalog_revision:4,product_code:'general',content_category:'product_intro',content_scope:'internal',content:{file_ids:[id(6)],links:[]}};
window.row={material_id:id(4),material_revision:2,content_name:'解析验收资料',version_id:id(5),version_no:1,state:'saved',source:null,can_edit:false,can_manage:false,active_version_id:null,versions:[{version_id:id(5),version_no:1,state:'saved',files:[{file_id:id(6),filename:'fixture.txt'}],contribution:{selection,content_name:'解析验收资料'}}]};
window.ingestion={material_id:id(4),version_id:id(5),ingestion_id:null,state:'saved',availability:'ready',can_retry:true,error_code:null,processed_files:0,total_files:1,observed_at:null};
window.setStatus=next=>{ingestion={...ingestion,...next};row={...row,state:ingestion.state,versions:[{...row.versions[0],state:ingestion.state}],...(ingestion.state==='retrievable'?{active_version_id:id(5),active_version_state:'retrievable'}:{})};};
window.useLegacy=()=>{row=importedMaterial(10);};
const client={uploadKnowledgeFile:async(file,options)=>{calls.push({upload:file.name,key:options.idempotencyKey});return {file_id:id(6),filename:file.name};},request:async options=>{calls.push(options);const url=new URL(options.path,'https://fixture.test'),path=url.pathname;
 if(path==='/knowledge/catalog')return catalog;
 if(path==='/knowledge/materials'&&options.method==='POST'){row={...row,content_name:'新附件资料',versions:[{...row.versions[0],contribution:{selection:options.data,content_name:'新附件资料'}}]};if(autoQueue)setStatus({ingestion_id:id(7),state:'syncing',availability:'running',can_retry:false});return {material_id:row.material_id};}
 if(path==='/knowledge/materials')return {items:[row],total:1,limit:20,offset:0,capabilities:{create:true},product_options:[]};
 if(path.endsWith('/ingestion')){if(options.method==='POST'){if(postFail)throw Object.assign(Error('https://private-provider/?secret'),{statusCode:503});setStatus({ingestion_id:id(7),state:'syncing',availability:'running',can_retry:false});return {...ingestion};}if(delayState)await new Promise(resolve=>window.releaseState=()=>{delayState=false;resolve();});return {...ingestion};}
 if(path==='/knowledge/materials/'+row.material_id)return structuredClone(row);
 throw Error('Unexpected fixture route: '+path);
 }};
const service=createKnowledgeService(client),node=createRoot(document.getElementById('root'));window.show=()=>node.render(<PcProvider><Knowledge service={service} page={{options:{tab:'materials'}}}/></PcProvider>);show();
`}, bundle: true, write: false, outdir: '.runtime/knowledge-ingestion-browser', loader: {'.svg': 'dataurl'}, define: {'process.env.NODE_ENV': '"production"'}});
const js = built.outputFiles.find(file => file.path.endsWith('.js')).text, css = built.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true, timeout: 20000});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1400, height: 1000}}), page = await context.newPage(), errors = [];
  page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => route.request().url() === 'http://localhost/' ? route.fulfill({contentType: 'text/html; charset=utf-8', body: '<meta charset="UTF-8"><div class="department-ui" id="root"></div><style>'+css+'</style><script>'+js+'</script>'}) : route.abort());
  try {await page.goto('http://localhost/'); await page.getByRole('button', {name: '解析验收资料', exact: true}).waitFor(); await run(page); assert.deepEqual(errors, []);} catch (error) {throw Error(error.message+'\n'+await page.locator('body').innerText(), {cause: error});} finally {await context.close();}
}
async function open(page) {await page.getByRole('button', {name: '解析验收资料', exact: true}).click(); await page.getByRole('heading', {name: '解析验收资料', exact: true}).waitFor();}
async function select(page, label, text) {await page.getByRole('combobox', {name: label, exact: true}).click(); await page.locator('.ant-select-dropdown:visible').getByText(text, {exact: true}).click();}

test('server can_retry controls explicit ingestion, then existing refresh shows complete without polling', () => fixture(async page => {
  await open(page); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.getByText('正在同步和解析附件', {exact: false}).waitFor();
  const posts = await page.evaluate(() => calls.filter(row => row.method === 'POST'));
  assert.equal(posts.length, 1); assert.deepEqual(posts[0].data, {expected_material_revision: 2}); assert.ok(posts[0].idempotencyKey);
  assert.equal(await page.getByRole('button', {name: '开始解析', exact: true}).count(), 0);
  await page.evaluate(() => setStatus({state: 'retrievable', availability: 'complete', processed_files: 1, observed_at: '2026-10-18T00:00:00Z'}));
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await page.getByText('该版本已完成解析和检索验证。', {exact: true}).waitFor();
  assert.ok(await page.getByText('已处理附件 1 / 1', {exact: true}).count());
  assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 1);
}));

test('unconfigured and link-only saved materials explain the gap without a retry button', () => fixture(async page => {
  await page.evaluate(() => setStatus({availability: 'unconfigured', can_retry: false})); await open(page);
  await page.getByText('检索服务尚未配置', {exact: false}).waitFor(); assert.equal(await page.getByRole('button', {name: '开始解析', exact: true}).count(), 0);
  await page.evaluate(() => setStatus({availability: 'links_not_archived', total_files: 0}));
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await page.getByText('链接已保存，但不会自动抓取', {exact: false}).waitFor();
  assert.equal(await page.getByRole('button', {name: '重试解析', exact: true}).count(), 0);
}));

test('safe parsing failure can retry while unknown provider upload outcome cannot', () => fixture(async page => {
  await page.evaluate(() => setStatus({state: 'failed', availability: 'retryable', ingestion_id: '00000000-0000-0000-0000-000000000007', error_code: 'SAFE_PARSE_FAILURE'})); await open(page);
  await page.getByRole('button', {name: '重试解析', exact: true}).waitFor();
  await page.evaluate(() => setStatus({availability: 'unknown', can_retry: false, error_code: 'https://secret-provider/?token'}));
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await page.getByText('附件上传至检索服务的结果未确认', {exact: false}).waitFor();
  assert.equal(await page.getByRole('button', {name: '重试解析', exact: true}).count(), 0); assert.equal((await page.locator('body').innerText()).includes('secret-provider'), false);
}));

test('revision and permission changes between display and click prevent POST', () => fixture(async page => {
  await open(page); await page.getByRole('button', {name: '开始解析', exact: true}).waitFor();
  await page.evaluate(() => {row.material_revision = 3;}); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.getByText('材料版本或处理状态已变化', {exact: false}).waitFor(); assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 0);
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await page.getByRole('button', {name: '开始解析', exact: true}).waitFor();
  await page.evaluate(() => {ingestion.can_retry = false;}); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.getByText('材料版本或处理状态已变化', {exact: false}).waitFor(); assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 0);
}));

test('uncertain POST never auto repeats and an explicit retry retains the same request key', () => fixture(async page => {
  await open(page); await page.evaluate(() => {postFail = true;}); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.getByText('系统不会自动重复提交', {exact: false}).waitFor(); await page.getByRole('button', {name: '开始解析', exact: true}).waitFor();
  assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 1); assert.equal((await page.locator('body').innerText()).includes('private-provider'), false);
  await page.evaluate(() => {postFail = false;}); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.getByText('正在同步和解析附件', {exact: false}).waitFor();
  const posts = await page.evaluate(() => calls.filter(row => row.method === 'POST')); assert.equal(posts.length, 2); assert.equal(posts[0].idempotencyKey, posts[1].idempotencyKey);
}));

test('identity change during preflight drops the pending action', () => fixture(async page => {
  await open(page); await page.getByRole('button', {name: '开始解析', exact: true}).waitFor();
  await page.evaluate(() => {delayState = true;}); await page.getByRole('button', {name: '开始解析', exact: true}).click();
  await page.waitForFunction(() => typeof releaseState === 'function');
  await page.evaluate(() => {actor = {...actor, userId: '00000000-0000-0000-0000-000000000099'}; show(); releaseState();});
  await page.getByRole('button', {name: '贡献资料', exact: true}).waitFor(); assert.equal(await page.getByRole('heading', {name: '解析验收资料', exact: true}).count(), 0);
  assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 0);
}));

test('uploaded archive receipt reaches save and automatic queue status without a second POST', () => fixture(async page => {
  await page.getByRole('button', {name: '贡献资料', exact: true}).click(); await select(page, '内容类别', '会议演讲'); await select(page, '内容范围', '仅内部'); await select(page, '产品', '通用');
  await page.locator('input[type=file]').setInputFiles({name: 'fixture.txt', mimeType: 'text/plain', buffer: Buffer.from('fixture bytes')});
  await page.getByText('fixture.txt · 已归档', {exact: true}).waitFor(); await page.getByRole('button', {name: '保存材料', exact: true}).click();
  await page.getByRole('heading', {name: '新附件资料', exact: true}).waitFor(); await page.getByText('正在同步和解析附件', {exact: false}).waitFor();
  const posts = await page.evaluate(() => calls.filter(row => row.method === 'POST')); assert.equal(posts.length, 1); assert.equal(posts[0].path, '/knowledge/materials'); assert.deepEqual(posts[0].data.content.file_ids, ['00000000-0000-0000-0000-000000000006']);
}));
