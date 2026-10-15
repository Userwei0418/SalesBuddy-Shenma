// Full built client, synthetic data and in-memory HTTP only. No LOCAL API or credentials.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const webRoot = fileURLToPath(new URL('../', import.meta.url));
const dist = path.resolve(webRoot, process.env.SALES_WEB_DIST_DIR || 'dist');
const base = 'https://workspace-performance.test';
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});

test('first load, q-only scheme changes and refresh dispatch immediately; typing remains coalesced', {timeout: 60000}, async () => {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  const calls = [], errors = [], samples = [];
  let fixtures, bootEnd = 0, denyQuery = false;
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, base);
    if (url.pathname === '/web-capabilities') return route.fulfill({json: {previewOnly: false}});
    if (url.pathname === '/connection-status') return route.fulfill({json: {configured: true, reachable: true}});
    if (url.pathname.startsWith('/api/')) {
      const at = performance.now(), body = route.request().postDataJSON();
      calls.push({path: url.pathname, at, body});
      if (['/api/v1/opportunity-views/options', '/api/v1/opportunity-views', '/api/v1/opportunity-views/preference'].includes(url.pathname)) bootEnd = at;
      if (denyQuery && url.pathname.endsWith('/query')) return route.fulfill({status: 403, json: {message: '当前商机查看权限已撤销'}});
      const value = fixtures?.[url.pathname];
      return route.fulfill({status: value ? 200 : 404, json: value || {message: 'Unexpected synthetic route'}});
    }
    const file = path.resolve(dist, url.pathname === '/' ? 'index.html' : url.pathname.slice(1));
    assert.ok(file.startsWith(dist + path.sep));
    if (!fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    return route.fulfill({body: fs.readFileSync(file), contentType: ({'.js':'text/javascript','.css':'text/css','.json':'application/json','.html':'text/html','.svg':'image/svg+xml','.png':'image/png'})[path.extname(file)] || 'application/octet-stream'});
  });
  const page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(`${base}/?mode=preview`);
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    fixtures = await page.evaluate(async () => {
      const request = (path, method = 'GET', data) => new Promise(resolve => SalesPreview.request({url: '/api/v1' + path, method, data, header: {Authorization: 'Bearer ' + SalesRuntime.require('utils/apiClient').getAuth().access_token}, success: response => resolve(response.data)}));
      const options = await request('/opportunity-views/options');
      return {'/api/v1/opportunity-views/options': options, '/api/v1/opportunity-views': await request('/opportunity-views'), '/api/v1/opportunity-views/preference': await request('/opportunity-views/preference'), '/api/v1/opportunity-views/query': await request('/opportunity-views/query', 'POST', {config: options.default_config, offset: 0, limit: 40})};
    });
    fixtures['/api/v1/opportunity-views'] = {total: 2, items: ['甲', '乙'].map((name, index) => ({id: `perf-view-${index}`, name: `性能方案${name}`, visibility: 'private', version_no: 1, config: {...fixtures['/api/v1/opportunity-views/options'].default_config, q: name}}))};
    await page.evaluate(() => {window.SALES_MODE = 'live'; SalesPlatform.install(SalesRuntime.wx);});
    const queryCalls = () => calls.filter(item => item.path.endsWith('/query'));
    const settled = () => page.waitForFunction(() => /已加载 \d+ \/ \d+ 条/.test(document.querySelector('.opp-table-footer')?.textContent || '') && !document.querySelector('.opp-workspace .ant-spin-spinning'));
    for (const route of ['workbench', 'opportunities', 'workbench', 'opportunities']) {
      const start = performance.now(), before = queryCalls().length;
      await page.evaluate(route => SalesRuntime.wx.navigateTo({url: `/pages/${route}/index`}), route);
      await settled();
      const query = queryCalls()[before];
      assert.equal(queryCalls().length, before + 1);
      samples.push({route, click_to_rows_ms: Math.round(performance.now() - start), bootstrap_to_query_ms: Math.round(query.at - bootEnd)});
    }
    const schemeSamples = [];
    for (const name of ['甲', '乙']) {
      await page.getByRole('combobox', {name: '选择看板方案'}).click();
      const option = page.locator('.ant-select-dropdown:visible').getByText(`性能方案${name} · 个人`, {exact: true});
      await option.waitFor();
      const before = queryCalls().length, start = performance.now();
      await option.evaluate(node => node.click());
      await settled();
      assert.equal(queryCalls().length, before + 1);
      assert.equal(queryCalls().at(-1).body.config.q, name);
      schemeSamples.push(Math.round(queryCalls().at(-1).at - start));
    }
    // Native click executes synchronously; the measured interval excludes Playwright actionability waits.
    let before = queryCalls().length;
    let start = performance.now();
    await page.evaluate(() => document.querySelector('[aria-label="刷新商机"]').click());
    await page.waitForFunction(() => !document.querySelector('.opp-workspace .ant-spin-spinning'));
    await settled();
    const refreshDispatch = queryCalls()[before].at - start;
    assert.equal(queryCalls().length, before + 1);
    await page.evaluate(() => SalesRuntime.wx.navigateTo({url: '/pages/workbench/index'}));
    await settled();
    before = queryCalls().length;
    const input = page.getByPlaceholder('搜索商机或客户');
    await input.fill('合'); await input.fill('合成'); await input.fill('合成测试');
    await page.waitForTimeout(300);
    await settled();
    assert.equal(queryCalls().length, before + 1, 'continuous typing sends only the final query');
    assert.equal(queryCalls().at(-1).body.config.q, '合成测试');
    denyQuery = true;
    await page.getByRole('button', {name: '刷新商机'}).click();
    await page.getByText('当前商机查看权限已撤销', {exact: true}).waitFor();
    assert.equal(await page.locator('.opp-name-link').count(), 0, 'denied refresh clears prior rows');
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({kind: 'synthetic_full_client', samples, q_only_scheme_dispatch_ms: schemeSamples, refresh_dispatch_ms: Math.round(refreshDispatch)}));
    if (!process.env.PERFORMANCE_BASELINE) {
      assert.ok(samples.every(item => item.bootstrap_to_query_ms < 150), 'initial query does not wait for search debounce');
      assert.ok(schemeSamples.every(value => value < 150), 'switching saved schemes that differ only in q does not wait for search debounce');
      assert.ok(refreshDispatch < 150, 'explicit refresh dispatches immediately');
    }
  } finally {await context.close();}
});
