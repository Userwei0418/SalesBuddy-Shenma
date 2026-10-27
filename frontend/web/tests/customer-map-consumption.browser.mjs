/** Built Web route, synthetic portfolio transport; no real account or business writes. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser, server, base;
before(async () => {
  server = createSalesWebServer({previewOnly: true, ...(process.env.SALES_WEB_TEST_DIST ? {staticDir: process.env.SALES_WEB_TEST_DIST} : {})});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); server?.closeAllConnections(); await new Promise(resolve => server?.close(resolve));});

async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1920, height: 1080}, serviceWorkers: 'block'});
  const errors = [], blocked = [];
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'manager'));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      const provider = SalesPreview;
      window.mapProbe = {requests: [], held: [], holdNext: false, failNext: false};
      const rows = Array.from({length: 203}, (_, i) => ({
        id: 'map-fixture-' + i, name: `合成范围客户${String(i + 1).padStart(3, '0')}`,
        owner_name: '合成成员', team_name: '合成部门', can_open: i !== 202,
        activity_readable: true, opportunity_readable: true, map_readable: i !== 202,
        is_active: i === 0, latest_visit_at: null, max_open_probability: null, has_won: false,
        open_count: 0, potential_score: i === 0 ? 0 : i === 200 ? 85 : null,
        relationship_score: i === 0 ? 50 : i === 200 ? 90 : null,
      }));
      const scored = r => r.potential_score !== null && r.relationship_score !== null;
      window.SalesPreview = {...provider, request(o) {
        const url = new URL(o.url, location.href), path = url.pathname, method = o.method || 'GET';
        mapProbe.requests.push({path, method, query: Object.fromEntries(url.searchParams)});
        if (!path.endsWith('/customers/portfolio')) return provider.request(o);
        const q = url.searchParams;
        const selected = rows.filter(row => (q.get('segment') !== 'active' || row.is_active) &&
          (q.get('unscored') !== 'true' || row.map_readable && !scored(row)) &&
          (!q.get('q') || row.name.includes(q.get('q'))));
        const offset = Number(q.get('offset')), limit = Number(q.get('limit'));
        const data = {items: selected.slice(offset, offset + limit), total: selected.length, offset, limit,
          availability: {activity: true, opportunity: true, map: true, pool: true},
          summary: {claimed: 203, active: 1, pipeline_ge_10: 0, pipeline_ge_30: 0, pipeline_ge_50: 0, pipeline_ge_70: 0, pipeline_ge_90: 0, won: 0, pool_total: 209, activity_unavailable_count: 0, opportunity_unavailable_count: 0},
          map_points: selected.filter(r => r.map_readable && scored(r)),
          unscored_count: selected.filter(r => r.map_readable && !scored(r)).length,
          map_unavailable_count: selected.filter(r => !r.map_readable).length};
        const response = mapProbe.failNext ? {statusCode: 403, data: {detail: '合成范围未授权'}} : {statusCode: 200, data};
        mapProbe.failNext = false;
        const finish = () => {o.success?.(response); o.complete?.(response);};
        if (mapProbe.holdNext) {mapProbe.holdNext = false; mapProbe.held.push(finish);} else queueMicrotask(finish);
        return {abort() {}};
      }};
      SalesRuntime.wx.navigateTo({url: '/pages/customers/index'});
    });
    await page.getByRole('button', {name: '合成范围客户001', exact: true}).waitFor();
    await run(page);
    const requests = await page.evaluate(() => mapProbe.requests);
    assert.equal(requests.some(r => r.path.includes('/customer-assets/map')), false, 'Web must not start Mini scoring or pagination requests');
    assert.equal(requests.some(r => r.method !== 'GET' && !r.path.includes('/notifications/')), false, 'portfolio browsing stays read-only; shell may mark a synthetic notification read');
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } catch (error) {
    console.error('MAP_CONSUMPTION_DIAGNOSTIC', await page.evaluate(() => ({requests: mapProbe.requests, text: document.body.innerText.slice(-2500)})));
    throw error;
  } finally {await context.close();}
}
const query = page => page.evaluate(() => mapProbe.requests.filter(r => r.path.endsWith('/customers/portfolio')).at(-1).query);
async function period(page, label) {
  await page.locator('.sb-lselect').filter({has: page.locator('.sb-lselect-label').filter({hasText: /^活跃周期$/})}).locator('.ant-select').click();
  await page.locator('.ant-select-dropdown:visible').getByText(label, {exact: true}).click();
}
async function shot(page, name) {
  if (!process.env.MAP_CONSUMPTION_SHOTS) return;
  await mkdir(process.env.MAP_CONSUMPTION_SHOTS, {recursive: true});
  await page.screenshot({path: `${process.env.MAP_CONSUMPTION_SHOTS}/${name}.png`, animations: 'disabled'});
}

test('default claimed range retains unassessed and inactive rows; complete map totals survive table paging and server search', () => fixture(async page => {
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.pcRoute), true);
  assert.equal((await query(page)).segment, 'claimed');
  assert.equal(await page.locator('.ant-table-row').count(), 20);
  assert.match(await page.locator('.ds-customer-map-note').innerText(), /2 家已评分 · 200 家未评分 · 1 家地图未授权/);
  assert.equal(await page.locator('.sb-bmap-points > g').count(), 2);
  assert.match(await page.locator('.ant-table-row').nth(1).innerText(), /本年无跟进/);
  await page.locator('.ant-pagination-options .ant-select').click();
  await page.locator('.ant-select-dropdown:visible').getByText(/100 (条\/页|\/ page)/).click();
  await page.getByRole('button', {name: '合成范围客户100', exact: true}).waitFor();
  await page.locator('.ant-pagination-item-3').click();
  await page.getByRole('button', {name: '合成范围客户203', exact: true}).waitFor();
  assert.equal(await page.locator('.ant-table-row').count(), 3);
  assert.equal(await page.getByRole('button', {name: '合成范围客户203', exact: true}).isDisabled(), true);
  assert.equal((await query(page)).offset, '200');
  assert.equal(await page.locator('.sb-bmap-points > g').count(), 2);
  assert.equal(await page.locator('.ds-customer-count').innerText(), '203');
  await page.getByPlaceholder('搜索客户或负责人').fill('客户202');
  await page.waitForFunction(() => document.querySelector('.ds-customer-count')?.textContent === '1');
  assert.equal((await query(page)).offset, '0');
  assert.equal(await page.locator('.sb-bmap-points > g').count(), 0);
  assert.equal(await page.getByRole('button', {name: '合成范围客户202', exact: true}).isVisible(), true);
  await shot(page, 'unassessed-customer-search');
}));

test('quarter/year only change activity period; stale responses and forbidden results cannot restore another range', () => fixture(async page => {
  const before = await query(page);
  await period(page, '本季度');
  await page.getByRole('button', {name: '合成范围客户001', exact: true}).waitFor();
  const quarter = await query(page);
  const [year, month] = quarter.activity_end.split('-');
  assert.equal(quarter.activity_start, `${year}-${String(Math.floor((Number(month) - 1) / 3) * 3 + 1).padStart(2, '0')}-01`);
  assert.equal(quarter.segment, 'claimed'); assert.equal(quarter.scope, before.scope);
  assert.equal(await page.locator('.ds-customer-count').innerText(), '203');
  await page.evaluate(() => mapProbe.holdNext = true);
  await page.getByRole('button', {name: '本季度活跃客户 1家，查看详情', exact: true}).click();
  await page.waitForFunction(() => mapProbe.held.length === 1);
  await period(page, '本年');
  await page.getByRole('button', {name: '合成范围客户001', exact: true}).waitFor();
  assert.equal((await query(page)).activity_start, `${year}-01-01`);
  await page.getByRole('button', {name: '重置', exact: true}).click();
  await page.getByRole('button', {name: '合成范围客户020', exact: true}).waitFor();
  await page.evaluate(() => mapProbe.held.splice(0).forEach(f => f()));
  await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  assert.equal(await page.locator('.ds-customer-count').innerText(), '203');
  await page.evaluate(() => mapProbe.failNext = true);
  await page.locator('.ds-customer-tools').getByRole('button', {name: /^刷\s*新$/}).click();
  await page.getByText('客户范围加载失败', {exact: true}).waitFor();
  assert.equal(await page.locator('.ant-table-row').count(), 0);
  assert.equal(await page.locator('.sb-bmap-points > g').count(), 0);
  await shot(page, 'portfolio-forbidden');
}));
