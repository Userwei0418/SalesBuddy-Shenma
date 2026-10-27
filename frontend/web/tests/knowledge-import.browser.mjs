/** Synthetic V267 API browser fixtures only; no LOCAL, backend or provider acceptance. */
import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import Knowledge from './department-ui/Knowledge.jsx';
import {createKnowledgeService} from './department-ui/knowledge/service.js';
import {catalog,id,importedMaterial} from './tests/knowledge-import-fixture.mjs';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.actor={workspaceId:id(1),userId:id(2),permissionVersion:1};window.calls=[];
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:()=>true},require:name=>name==='utils/access'?{identity:session=>JSON.stringify(session)}:{}};window.getApp=()=>SalesRuntime.app;
window.rows=[importedMaterial(10),importedMaterial(11,'待复核',false),importedMaterial(12,'已停用')];
const client={request:async options=>{calls.push(options);const url=new URL(options.path,'https://fixture.test'),path=url.pathname;
 if(path==='/knowledge/catalog')return catalog;
 if(path==='/knowledge/materials'){const selected=url.searchParams.get('product_code'),items=selected?rows.filter(row=>row.product_code===selected):rows;return {items,total:items.length,limit:20,offset:0,capabilities:{create:true},product_options:[{code:'current',label:'当前系统产品'},{code:'legacy_fixture_hash',label:'旧产品名称'},{code:'legacy_outside_page',label:'分页外导入产品'}]};}
 if(path==='/knowledge/provider/status')return {retrieval:{status:'available'},generation:{status:'available',formats:['docx']}};
 if(path==='/knowledge/works')return {items:[],total:0,limit:20,offset:0};
 if(path==='/knowledge/stats')return {source:'salesbuddy.internal_materials',observed_at:'2026-10-18T00:00:00Z',metrics:{total:{status:'available',count:rows.length,source:'salesbuddy.internal_materials',observed_at:'2026-10-18T00:00:00Z'}},products:[{code:'legacy_fixture_hash',label:'旧产品名称',count:3},{code:'current',label:'当前系统产品',count:0}]};
 const match=path.match(/^\\/knowledge\\/materials\\/([^/]+)(\\/versions)?$/),row=rows.find(item=>item.material_id===match?.[1]);
 if(row&&match[2]&&options.method==='POST'){const old=row.versions[0],version={...old,version_id:id(210),version_no:2,files:[],contribution:{company_id:id(1),content_name:'人工补齐的新版本',selection:options.data.contribution}};row.versions=[version,...row.versions];row.version_id=version.version_id;row.version_no=2;row.material_revision=2;row.content_name=version.contribution.content_name;row.source=null;return {material_id:row.material_id};}
 if(row&&!match[2])return row;
 throw Error('Unexpected fixture route: '+path);
 },listOpportunityCustomerReferences:async()=>({items:[{id:id(31),name:'标准客户甲'}]}),listOpportunities:async()=>({items:[]})};
const service=createKnowledgeService(client),node=createRoot(document.getElementById('root'));window.show=()=>node.render(<PcProvider><Knowledge service={service} page={{options:{tab:'materials'}}}/></PcProvider>);show();
`}, bundle: true, write: false, outdir: '.runtime/knowledge-import-browser', loader: {'.svg': 'dataurl'}, define: {'process.env.NODE_ENV': '"production"'}});
const js = built.outputFiles.find(file => file.path.endsWith('.js')).text, css = built.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true, timeout: 20000});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1400, height: 1050}}), page = await context.newPage(), errors = [];
  page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => route.request().url() === 'http://localhost/' ? route.fulfill({contentType: 'text/html; charset=utf-8', body: '<meta charset="UTF-8"><div class="department-ui" id="root"></div><style>'+css+'</style><script>'+js+'</script>'}) : route.abort());
  try {await page.goto('http://localhost/'); await page.getByRole('button', {name: '弹药库原名称 10 / 保留原文', exact: true}).waitFor(); await run(page); assert.deepEqual(errors, []);} catch (error) {throw Error(error.message+'\n'+await page.locator('body').innerText(), {cause: error});} finally {await context.close();}
}
async function select(page, label, text) {await page.getByRole('combobox', {name: label, exact: true}).click(); await page.locator('.ant-select-dropdown:visible').getByText(text, {exact: true}).click();}

test('legacy directory preserves source names/providers and separates all source statuses from saved state', () => fixture(async page => {
  for (const state of ['可用', '待复核', '已停用']) assert.equal(await page.getByText('原表：'+state, {exact: true}).count(), 1);
  assert.equal(await page.getByText('系统：目录已导入', {exact: true}).count(), 3);
  assert.equal(await page.getByText('原提供人甲', {exact: false}).count(), 3);
  assert.equal(await page.getByText('旧产品名称', {exact: false}).count(), 3);
  assert.equal(await page.getByText('文件已保存', {exact: true}).count(), 0);
  await page.getByText('知识看板', {exact: true}).first().click(); await page.getByText('可见材料', {exact: true}).waitFor();
  const card = page.locator('.pc-kpi').filter({has: page.getByText('可见材料', {exact: true})});
  assert.match(await card.innerText(), /3/);
}));

test('legacy detail shows source references without fabricated files or raw source tokens', () => fixture(async page => {
  await page.getByRole('button', {name: '弹药库原名称 10 / 保留原文', exact: true}).click();
  const drawer = page.locator('.ant-drawer-body'); await drawer.getByRole('heading', {name: '弹药库原名称 10 / 保留原文', exact: true}).waitFor();
  assert.ok(await drawer.getByText('原表状态：可用', {exact: true}).count());
  assert.ok(await drawer.getByText('原附件.pdf · 1,200 字节 · application/pdf · 未归档，无法在线预览', {exact: true}).count());
  assert.equal(await drawer.getByRole('button', {name: '下载', exact: true}).count(), 0);
  assert.equal(await drawer.getByRole('button', {name: '预览', exact: true}).count(), 0);
  assert.equal(await drawer.getByRole('button', {name: '浏览原材料（新页）', exact: true}).count(), 1);
  assert.ok(await drawer.getByText('https://example.test/original-material', {exact: true}).count());
  assert.equal((await drawer.innerText()).includes('fixture_source_token'), false);
  assert.ok(await drawer.getByText('客户尚未关联 CRM', {exact: true}).count());
}));

test('legacy manual new version retains links/remarks and requires explicit missing fields before POST', () => fixture(async page => {
  await page.getByRole('button', {name: '弹药库原名称 10 / 保留原文', exact: true}).click();
  await page.getByRole('button', {name: '新增版本', exact: true}).click();
  await page.getByText('补齐字段后保存为新版本', {exact: true}).waitFor();
  assert.equal(await page.getByRole('textbox', {name: '材料链接', exact: true}).inputValue(), 'https://example.test/original-material');
  assert.equal(await page.getByRole('textbox', {name: '备注', exact: true}).inputValue(), '原始备注完整保留');
  assert.equal(await page.getByText('原附件.pdf · 已归档', {exact: true}).count(), 0);
  await page.getByRole('button', {name: '确认新增版本并保存', exact: true}).click();
  await page.getByText('请搜索并选择 CRM 标准客户', {exact: true}).waitFor();
  assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 0);
  await select(page, '产品', '当前系统产品'); await select(page, '行业', '测试行业'); await select(page, 'CRM 标准客户', '标准客户甲');
  await select(page, '汇报对象岗位', '测试岗位'); await select(page, '汇报对象职级', '测试职级'); await select(page, '汇报对象决策角色', '决策者');
  await page.getByRole('button', {name: '确认新增版本并保存', exact: true}).click();
  await page.getByRole('heading', {name: '人工补齐的新版本', exact: true}).waitFor();
  const posts = await page.evaluate(() => calls.filter(row => row.method === 'POST'));
  assert.equal(posts.length, 1); assert.equal(posts[0].data.expected_material_revision, 1);
  assert.deepEqual(posts[0].data.contribution.content, {file_ids: [], links: ['https://example.test/original-material']});
  assert.equal(posts[0].data.contribution.product_code, 'current'); assert.equal(posts[0].data.contribution.remarks, '原始备注完整保留');
  assert.equal('source' in posts[0].data.contribution, false);
  await select(page, '材料版本', 'V1 · 目录已导入');
  await page.getByRole('heading', {name: '弹药库原名称 10 / 保留原文', exact: true}).waitFor();
  assert.ok(await page.getByText('原表状态：可用', {exact: true}).count());
}));

test('source-table available imports stay out of generation candidates with no active retrievable version', () => fixture(async page => {
  await page.getByText('生成材料', {exact: true}).first().click();
  await page.getByRole('button', {name: '新建材料', exact: true}).click();
  await page.getByRole('combobox', {name: '选择参考材料', exact: true}).click();
  await page.waitForFunction(() => calls.some(row => row.path.includes('retrievable=true')));
  assert.equal(await page.locator('.ant-select-item-option').count(), 0);
  assert.equal(await page.getByRole('button', {name: '提交生成任务', exact: true}).isDisabled(), true);
  assert.equal(await page.evaluate(() => calls.filter(row => row.method === 'POST').length), 0);
}));

test('imported products are filterable outside the current page and never become contribution products', () => fixture(async page => {
  await page.getByRole('combobox', {name: '筛选产品', exact: true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('当前系统产品', {exact: true}).waitFor();
  assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('当前系统产品', {exact: true}).count(), 1);
  await page.locator('.ant-select-dropdown:visible').getByText('分页外导入产品', {exact: true}).click();
  await page.waitForFunction(() => calls.some(row => row.path.startsWith('/knowledge/materials?') && new URL(row.path, 'http://fixture').searchParams.get('product_code') === 'legacy_outside_page'));
  await page.getByText('当前条件下没有可见资料', {exact: true}).waitFor();
  await page.getByRole('combobox', {name: '筛选产品', exact: true}).click();
  await page.locator('.ant-select-dropdown:visible').getByText('旧产品名称', {exact: true}).waitFor();
  assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('旧产品名称', {exact: true}).count(), 1);
  await page.keyboard.press('Escape');
  await page.getByText('知识看板', {exact: true}).first().click();
  await page.getByText('可见材料', {exact: true}).waitFor();
  await select(page, '看板产品', '旧产品名称');
  await page.waitForFunction(() => calls.some(row => row.path.startsWith('/knowledge/stats?') && new URL(row.path, 'http://fixture').searchParams.get('product_code') === 'legacy_fixture_hash'));
  await page.getByText('资料库', {exact: true}).first().click();
  await page.getByRole('button', {name: '贡献资料', exact: true}).click();
  await page.getByRole('combobox', {name: '产品', exact: true}).click();
  assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('旧产品名称', {exact: true}).count(), 0);
  assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('分页外导入产品', {exact: true}).count(), 0);
  await page.locator('.ant-select-dropdown:visible').getByText('当前系统产品', {exact: true}).waitFor();
  assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('当前系统产品', {exact: true}).count(), 1);
}));
