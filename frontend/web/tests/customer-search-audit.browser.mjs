/** Real customer picker with synthetic transport only; no live API or model. */
import {after, before, test} from 'node:test';
import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = fileURLToPath(new URL('../', import.meta.url));
const built = await build({absWorkingDir: root, stdin: {resolveDir: root, loader: 'jsx', contents: `
import React from 'react';import {createRoot} from 'react-dom/client';
import {PcProvider} from './department-ui/pc/PcProvider.jsx';import {CustomerPicker} from './department-ui/Knowledge.jsx';
import '@shandiant/tokens/css';import '@shandiant/ui-react/style.css';import './department-ui/shared.css';import './department-ui/pc/tokens.css';import './department-ui/pc/pc-theme.css';
window.flights=[];window.selected=null;window.owner='reader-A';
const api={listCustomers:options=>new Promise((resolve,reject)=>flights.push({options,owner,resolve,reject}))};
window.SalesRuntime={app:{globalData:{session:{}}},require:name=>name==='utils/access'?{identity:()=>owner}:api};
const node=createRoot(document.getElementById('root'));
window.renderPicker=()=>node.render(<PcProvider><CustomerPicker value={selected} onChange={row=>{window.selected=row;renderPicker();}}/></PcProvider>);
window.answer=(index,body,error)=>error?flights[index].reject(Error(error)):flights[index].resolve(body);
renderPicker();
`}, bundle: true, write: false, loader: {'.svg': 'dataurl'}, outdir: '.runtime/customer-search-audit/browser-bundle', define: {'process.env.NODE_ENV': '"production"'}});
const js = built.outputFiles.find(file => file.path.endsWith('.js')).text;
const css = built.outputFiles.find(file => file.path.endsWith('.css')).text;
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1100, height: 700}}), page = await context.newPage();
  const errors = [], unexpected = [];
  page.setDefaultTimeout(7000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    if (route.request().isNavigationRequest()) return route.fulfill({contentType: 'text/html', body: `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body><main class="department-ui" style="padding:24px"><div id="root"></div></main><script>${js}</script></body></html>`});
    unexpected.push(route.request().url()); return route.abort();
  });
  try {
    await page.goto('https://customer-search.test/'); await page.waitForFunction(() => flights.length === 1);
    const input = page.getByRole('combobox', {name: '选择客户'});
    await run(page, input);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  } finally {await context.close();}
}
const answer = (page, index, items) => page.evaluate(([i, rows]) => window.answer(i, {items: rows}), [index, items]);
const waitQuery = (page, query, count) => page.waitForFunction(([q, n]) => flights.length === n && flights[n - 1].options.q === q, [query, count]);

test('English query reaches the server and finds a customer outside the first 50 suggestions', () => fixture(async (page, input) => {
  await answer(page, 0, Array.from({length: 50}, (_, i) => ({id: String(i), name: '合成初始客户' + i})));
  await input.click(); await input.fill('ACME'); await waitQuery(page, 'ACME', 2);
  assert.equal(await page.locator('.ant-select-item-option').count(), 0, 'old suggestions are not selectable while searching');
  await answer(page, 1, [{id: 'beyond-50', name: 'ACME 合成客户'}]);
  await page.getByText('ACME 合成客户', {exact: true}).click();
  assert.equal(await page.evaluate(() => selected.id), 'beyond-50');
  assert.deepEqual(await page.evaluate(() => flights.map(row => row.options)), [{q: '', pageSize: 50}, {q: 'ACME', pageSize: 50}]);
}));

test('permission denial, retry, true empty and malformed response have distinct visible states', () => fixture(async (page, input) => {
  await answer(page, 0, []); await input.click(); await input.fill('跨越'); await waitQuery(page, '跨越', 2);
  await page.evaluate(() => answer(1, null, '当前没有客户档案读取权限'));
  await page.getByRole('alert').getByText('当前没有客户档案读取权限', {exact: false}).waitFor();
  assert.equal(await page.getByText('没有匹配的可读客户，请换个关键词', {exact: true}).count(), 0);
  await page.getByRole('button', {name: '重新搜索', exact: true}).click(); await waitQuery(page, '跨越', 3);
  await answer(page, 2, []); await page.getByText('没有匹配的可读客户，请换个关键词', {exact: true}).waitFor();
  await input.fill('结构异常'); await waitQuery(page, '结构异常', 4);
  await page.evaluate(() => answer(3, {})); await page.getByRole('alert').filter({hasText: '客户列表暂不可用，请重新搜索'}).waitFor();
  await fs.mkdir(root + '.runtime/customer-search-audit/screenshots', {recursive: true});
  await page.screenshot({path: root + '.runtime/customer-search-audit/screenshots/readable-customer-error.png'});
}));

test('late keyword responses and old-account responses cannot replace current choices', () => fixture(async (page, input) => {
  await input.click(); await input.fill('旧关键字'); await waitQuery(page, '旧关键字', 2);
  await input.fill('新关键字'); await waitQuery(page, '新关键字', 3);
  await answer(page, 2, [{id: 'new', name: '当前查询客户'}]); await page.getByText('当前查询客户', {exact: true}).waitFor();
  await answer(page, 0, [{id: 'initial', name: '过期初始客户'}]);
  await answer(page, 1, [{id: 'stale', name: '过期关键词客户'}]);
  assert.equal(await page.getByText('过期关键词客户', {exact: true}).count(), 0);
  await page.evaluate(() => {window.owner = 'reader-B'; renderPicker();});
  await page.waitForFunction(() => flights.length === 4);
  assert.equal(await page.getByText('当前查询客户', {exact: true}).count(), 0);
  await answer(page, 3, [{id: 'B', name: '新身份可读客户'}]);
  await page.getByText('新身份可读客户', {exact: true}).click();
  assert.equal(await page.evaluate(() => selected.id), 'B');
}));
