/** Actual Playbook advice panel; every transport is synthetic and off-origin access is blocked. */
import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const sharedSources = Object.fromEntries(['customerAdvice', 'visitDates', 'demoDisplay'].map(name => [name, readFileSync(root + 'source/miniprogram/utils/' + name + '.js', 'utf8')]));
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import KnowledgeAdvice from './department-ui/KnowledgeAdvice.jsx';
const definitions=${JSON.stringify(sharedSources)}, modules={};
const load=name=>{if(name.startsWith('./'))name=name.slice(2);if(!modules[name]){const module={exports:{}};modules[name]=module;new Function('require','module','exports',definitions[name])(load,module,module.exports);}return modules[name].exports;};
const adviceView=load('customerAdvice');
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.flights=[];window.customer='customer-A';window.visible=true;
window.overviewOverride=window.initialIdentity?{id:customer,business_identities:initialIdentity}:undefined;
window.actor={workspaceId:'workspace-A',userId:'actor-A',role:'sales',permissionVersion:1};
window.permissions=['advice.read','advice.customer','advice.request'];
const send=(method,kind,id,section,retry)=>new Promise((resolve,reject)=>flights.push({method,kind,id,section,retry,owner:{...actor},resolve,reject}));
const client={getLatestBusinessAdvice:(...args)=>send('GET',...args),requestBusinessAdvice:(...args)=>send('POST',...args)};
window.SalesRuntime={app:{globalData:{get session(){return actor;}},can:permission=>permissions.includes(permission)},require:name=>name==='utils/access'?{identity:session=>JSON.stringify(session)}:name==='utils/customerAdvice'?adviceView:client};
window.getApp=()=>SalesRuntime.app;
const node=createRoot(document.getElementById('root'));
window.show=()=>node.render(<PcProvider>{visible&&<KnowledgeAdvice customerId={customer} customerOverview={overviewOverride || {id:customer}}/>}</PcProvider>);
window.answer=(index,value,error)=>error?flights[index].reject(Error(error)):flights[index].resolve(value);
show();
`}, bundle: true, write: false, loader: {'.svg': 'dataurl'}, outdir: '.runtime/knowledge-advice/browser-bundle', define: {'process.env.NODE_ENV': '"production"'}});
const js = built.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = built.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
async function fixture(run, initialIdentity) {
  const context = await browser.newContext({viewport: {width: 1100, height: 780}}), page = await context.newPage();
  const errors = [], unexpected = [];
  if(initialIdentity)await page.addInitScript(value=>{window.initialIdentity=value;},initialIdentity);
  page.setDefaultTimeout(7000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    if (route.request().isNavigationRequest()) return route.fulfill({contentType: 'text/html', body: `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body><main class="department-ui" style="max-width:600px"><div id="root"></div></main><script>${js}</script></body></html>`});
    unexpected.push(route.request().url()); return route.abort();
  });
  try {
    await page.goto('https://knowledge-advice.test/');
    if(initialIdentity && !initialIdentity.includes('customer'))await page.getByText('当前企业不适用客户经营建议。',{exact:true}).waitFor();else await count(page,1);
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  } finally {await context.close();}
}
const count = (page, n) => page.waitForFunction(n => flights.length === n, n);
const answer = (page, index, value, error) => page.evaluate(args => window.answer(...args), [index, value, error]);
const row = (customerId = 'customer-A', status = 'succeeded', summary = '当前客户的已有建议') => ({
  id: 'analysis-' + customerId, subject_kind: 'customer', subject_id: customerId, customer_id: customerId,
  section: 'overview', status, summary, suggestions: [],
});
const methods = page => page.evaluate(() => flights.map(f => f.method));
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));

test('mount, refresh and reentry discover existing advice using GET only', () => fixture(async page => {
  await answer(page, 0, row()); await page.getByText('当前客户的已有建议', {exact: true}).waitFor();
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click(); await count(page, 2);
  await answer(page, 1, row());
  await page.evaluate(() => {visible = false; show();}); await paint(page);
  await page.evaluate(() => {visible = true; show();}); await count(page, 3);
  await answer(page, 2, row()); await page.getByText('当前客户的已有建议', {exact: true}).waitFor();
  assert.deepEqual(await methods(page), ['GET', 'GET', 'GET']);
}));

test('explicit generation submits once, keeps pending state, and refreshes without another POST', () => fixture(async page => {
  await answer(page, 0, null);
  await page.getByRole('button', {name: '生成经营建议', exact: true}).click(); await count(page, 2);
  assert.deepEqual(await page.evaluate(() => {const {method,kind,id,section,retry,owner}=flights[1];return {method,kind,id,section,retry,owner};}), {
    method: 'POST', kind: 'customer', id: 'customer-A', section: 'overview', retry: false,
    owner: {workspaceId: 'workspace-A', userId: 'actor-A', role: 'sales', permissionVersion: 1},
  });
  assert.equal(await page.getByRole('button', {name: '生成经营建议', exact: true}).count(), 0);
  await answer(page, 1, row('customer-A', 'queued')); await page.getByText('排队中', {exact: true}).waitFor();
  await page.getByRole('button', {name: '刷新进度', exact: true}).click(); await count(page, 3);
  await answer(page, 2, row()); await page.getByText('当前客户的已有建议', {exact: true}).waitFor();
  assert.deepEqual(await methods(page), ['GET', 'POST', 'GET']);
}));

test('switching customer, workspace or actor hides old content and rejects late responses', () => fixture(async page => {
  await page.evaluate(() => {customer = 'customer-B'; show();}); await count(page, 2);
  await answer(page, 1, row('customer-B', 'succeeded', 'B客户建议')); await page.getByText('B客户建议', {exact: true}).waitFor();
  await answer(page, 0, row('customer-A', 'succeeded', '迟到A建议')); await paint(page);
  assert.equal(await page.getByText('迟到A建议', {exact: true}).count(), 0);
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click(); await count(page, 3);
  await page.evaluate(() => {actor = {...actor, workspaceId:'workspace-B'}; show();}); await count(page, 4);
  await answer(page, 3, row('customer-B', 'succeeded', '新公司建议')); await page.getByText('新公司建议', {exact: true}).waitFor();
  await answer(page, 2, row('customer-B', 'succeeded', '旧公司迟到')); await paint(page);
  assert.equal(await page.getByText('旧公司迟到', {exact: true}).count(), 0);
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click(); await count(page, 5);
  await page.evaluate(() => {actor = {...actor, userId:'actor-B'}; show();}); await count(page, 6);
  await answer(page, 5, row('customer-B', 'succeeded', '新用户建议')); await page.getByText('新用户建议', {exact: true}).waitFor();
  await answer(page, 4, row('customer-B', 'succeeded', '旧用户迟到')); await paint(page);
  assert.equal(await page.getByText('旧用户迟到', {exact: true}).count(), 0);
  assert.deepEqual(await methods(page), Array(6).fill('GET'));
}));

test('lost generation response and read errors recover through explicit GET retry', () => fixture(async page => {
  await answer(page, 0, null); await page.getByRole('button', {name: '生成经营建议', exact: true}).click(); await count(page, 2);
  await answer(page, 1, null, '合成网络中断'); await page.getByRole('alert').waitFor();
  assert.equal(await page.getByRole('button', {name: '生成经营建议', exact: true}).count(), 0);
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await count(page, 3);
  await answer(page, 2, null, '合成读取失败'); await page.getByText('合成读取失败', {exact: true}).waitFor();
  await page.getByRole('button', {name: '重新读取', exact: true}).click(); await count(page, 4);
  await answer(page, 3, row('customer-A', 'running')); await page.getByText('处理中', {exact: true}).waitFor();
  assert.deepEqual(await methods(page), ['GET', 'POST', 'GET', 'GET']);
}));

test('failed analysis permits an explicit retry; wrong-subject results are never displayed', () => fixture(async page => {
  await answer(page, 0, {...row('customer-A', 'failed'), error:'合成分析失败'});
  await page.getByRole('button', {name: '重试生成', exact: true}).click(); await count(page, 2);
  assert.equal(await page.evaluate(() => flights[1].retry), true);
  await answer(page, 1, row('customer-B', 'succeeded', '错误客户内容'));
  await page.getByText('建议与当前客户不匹配，请重新读取', {exact: true}).waitFor();
  assert.equal(await page.getByText('错误客户内容', {exact: true}).count(), 0);
}));

test('permission loss and unmount invalidate pending generation results', () => fixture(async page => {
  await answer(page, 0, null); await page.getByRole('button', {name: '生成经营建议', exact: true}).click(); await count(page, 2);
  await page.evaluate(() => {permissions = []; actor = {...actor, permissionVersion:2}; show();}); await paint(page);
  await answer(page, 1, row('customer-A', 'succeeded', '权限撤销后的迟到结果')); await paint(page);
  assert.equal(await page.getByText('权限撤销后的迟到结果', {exact: true}).count(), 0);
  assert.deepEqual(await methods(page), ['GET', 'POST']);
  await page.evaluate(() => {permissions = ['advice.read','advice.customer']; show();}); await count(page, 3);
  await page.evaluate(() => {visible = false; show();}); await paint(page);
  await answer(page, 2, row()); await paint(page);
  await page.evaluate(() => {visible = true; show();}); await count(page, 4);
  await answer(page, 3, row()); await page.getByText('当前客户的已有建议', {exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '重新生成建议', exact: true}).count(), 0);
}));

 test('a stale success is not displayed as current and an empty POST receipt requires discovery', () => fixture(async page => {
  await answer(page,0,{...row(),stale:true});
  await page.getByText('客户资料已变化，原建议已失效，需要时可重新生成。',{exact:true}).waitFor();
  assert.equal(await page.getByText('当前客户的已有建议',{exact:true}).count(),0);
  await page.getByRole('button',{name:'重新生成建议',exact:true}).click();await count(page,2);
  await answer(page,1,null);
  await page.getByText('提交结果尚未确认，请先重新读取已有建议。',{exact:true}).waitFor();
  await page.getByRole('button',{name:'重新读取',exact:true}).click();await count(page,3);
  await answer(page,2,row('customer-A','running'));
  await page.getByText('处理中',{exact:true}).waitFor();
  assert.deepEqual(await methods(page),['GET','POST','GET']);
  if(process.env.ADVICE_SCREENSHOT_DIR)await page.screenshot({path:process.env.ADVICE_SCREENSHOT_DIR+'/knowledge-pending.png',fullPage:true});
}));

test('partner-only overview never reads or generates customer advice; dual identity remains eligible', () => fixture(async page => {
  assert.deepEqual(await methods(page),[]);
  assert.equal(await page.getByRole('button',{name:/生成|刷新/}).count(),0);
  await page.evaluate(()=>{overviewOverride={id:customer,business_identities:['customer','partner']};show();});await count(page,1);
  await answer(page,0,null);await page.getByRole('button',{name:'生成经营建议',exact:true}).click();await count(page,2);
  await answer(page,1,row('customer-A','queued'));await page.getByText('排队中',{exact:true}).waitFor();
  assert.deepEqual(await methods(page),['GET','POST']);
}, ['partner']));

test('changing a selected customer to partner-only invalidates pending advice and hides stale content', () => fixture(async page => {
  await page.evaluate(()=>{overviewOverride={id:customer,business_identities:['partner'],customer_analysis_applicable:false};show();});
  await page.getByText('当前企业不适用客户经营建议。',{exact:true}).waitFor();
  await answer(page,0,row('customer-A','succeeded','旧客户迟到建议'));await paint(page);
  assert.equal(await page.getByText('旧客户迟到建议',{exact:true}).count(),0);
  assert.deepEqual(await methods(page),['GET']);
  assert.equal(await page.getByRole('button',{name:/生成|刷新/}).count(),0);
  await page.evaluate(()=>{overviewOverride={id:'wrong-customer',business_identities:['customer']};show();});await paint(page);
  assert.deepEqual(await methods(page),['GET']);
}));
