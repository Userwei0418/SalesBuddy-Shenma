// Real browser host + unchanged TDesign/source components. Static requests are
// fulfilled from disk: no listening port, real API, model or QA browser session.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = fileURLToPath(new URL('../', import.meta.url));
const staticRoot = path.join(root, '.runtime/web007-0b629ef-dist');
const runtime = process.env.CHECKBOX_BASELINE === '1'
  ? execFileSync('git', ['show', '0b629ef20499d4027e8010315b84233b8aac67d8:frontend/web/runtime.js'], {cwd: root})
  : fs.readFileSync(path.join(root, 'runtime.js'));
const base = 'https://sales-web-runtime.test';
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});

async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  const unexpected = [], errors = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/')) {unexpected.push(url.pathname); return route.abort();}
    if (url.pathname === '/web-capabilities') return route.fulfill({json: {previewOnly: true}});
    if (url.pathname === '/connection-status') return route.fulfill({json: {configured: false, reachable: false}});
    const relative = url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1);
    const file = path.resolve(staticRoot, relative);
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    const contentType = ({'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png'})[path.extname(file)] || 'application/octet-stream';
    return route.fulfill({body: relative === 'runtime.js' ? runtime : fs.readFileSync(file), contentType});
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index');
    await run(page);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  } finally {await context.close();}
}

test('TDesign checkbox relation exists before property observers and survives child updates', {timeout: 30000}, () => fixture(async page => {
  await page.evaluate(() => {
    const route = 'pages/runtime-checkbox/index';
    SALES_BUNDLE.pages[route] = {config: {usingComponents: {'t-checkbox-group': '/ui/tdesign/checkbox-group/checkbox-group'}}, css: '', tree: [{t: 't-checkbox-group', a: {id: 'checks', options: '{{options}}', value: '{{selected}}', disabled: '{{disabled}}', 'bind:change': 'changed'}, c: []}]};
    SALES_BUNDLE.modules[route] = `Page({data:{options:[{label:'合成选项甲',value:'a'},{label:'合成选项乙',value:'b'}],selected:['a'],disabled:false},changed(e){this.setData({selected:e.detail.value})}});`;
    SalesRuntime.route('/' + route);
  });
  await page.getByText('合成选项甲', {exact: true}).waitFor();
  assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  assert.deepEqual(await page.evaluate(() => {
    const group = SalesRuntime.current.selectComponent('#checks');
    return group.getChildren().map(child => [child.data.value, child.data.checked, child.$parent === group]);
  }), [['a', true, true], ['b', false, true]]);
  await page.evaluate(() => SalesRuntime.current.selectComponent('#checks').getChildren()[1].handleTap({target: {dataset: {}}, currentTarget: {dataset: {}}}));
  await page.waitForFunction(() => SalesRuntime.current.data.selected.join(',') === 'a,b');
  await page.evaluate(() => SalesRuntime.current.setData({disabled: true}));
  await page.waitForFunction(() => SalesRuntime.current.selectComponent('#checks').getChildren().every(child => child.data._disabled));
  await page.evaluate(() => SalesRuntime.current.selectComponent('#checks').getChildren()[1].handleTap({target: {dataset: {}}, currentTarget: {dataset: {}}}));
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.selected), ['a', 'b']);
  await page.evaluate(() => SalesRuntime.current.setData({options: [{label: '合成选项甲', value: 'a'}]}));
  await page.waitForFunction(() => SalesRuntime.current.selectComponent('#checks').getChildren().length === 1);
}));

test('visit entry fallback can select a customer and initialize first-visit controls without a toast error', {timeout: 30000}, () => fixture(async page => {
  await page.evaluate(() => {window.SalesDepartmentUI = {...SalesDepartmentUI, render: () => false}; SalesRuntime.route('/pages/visit-entry/index');});
  await page.waitForFunction(() => SalesRuntime.current.data.customerResults?.length > 0);
  await page.evaluate(() => {const p = SalesRuntime.current; p.chooseCustomer({currentTarget: {dataset: {id: p.data.customerResults[0].id}}});});
  await page.waitForFunction(() => SalesRuntime.current.data.customerConfirmed && document.querySelector('t-checkbox-group'));
  assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
}));
