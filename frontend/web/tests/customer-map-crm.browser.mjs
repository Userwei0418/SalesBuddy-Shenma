/** Full CRM compatibility with fictional records only; no private runtime file is read. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const owner = 'synthetic-crm-owner';
const team = '00000003-0000-4000-8000-000000000001';
const scoredIndices = [0, 52, 54];
const source = (sheet, index) => ({source_kind: 'crm_export', source_ref: {sheet, row: index + 2, key: String(index + 1)}});
function dataset() {
  const customers = Array.from({length: 55}, (_, index) => ({
    ...source('合成客户', index), id: 'synthetic-crm-customer-' + index,
    name: `【合成】${scoredIndices.includes(index) ? '已有评分' : '缺评分'}客户 ${String(index).padStart(2, '0')}`,
    source_name: 'synthetic', owner_id: owner, owner_user_ref_id: owner, owner_name: '合成负责人', owner_team_id: team,
    team_name: '合成团队', level_code: 'Tier-2', potential_score: scoredIndices.includes(index) ? 85 : index === 1 ? 42 : null,
    relationship_score: scoredIndices.includes(index) ? 35 : null,
    quadrant_code: scoredIndices.includes(index) ? 'main_attack' : null,
    attributes: {}, version_no: 1, created_at: '2026-09-20T01:00:00Z', updated_at: '2026-09-20T01:00:00Z',
  }));
  const opportunities = [{...source('合成商机', 0), id: 'synthetic-crm-opportunity', customer_id: customers[0].id,
    name: '【合成】地图回归项目', customer_name: customers[0].name, owner_id: owner, owner_name: '合成负责人',
    amount: 10000, status: 'open', stage_code: 'qualified', probability: 30, fde_member_ids: [],
    source_snapshot: {'商机状态': '商机确认'}, expected_close_date: '2026-12-20', version_no: 1}];
  const visits = [{...source('合成跟进', 0), id: 'synthetic-crm-visit', customer_id: customers[0].id,
    opportunity_id: opportunities[0].id, opportunity_ids: [opportunities[0].id], visit_date: '2026-09-20',
    follow_up_record: '仅供隔离布局回归的合成记录', next_action: '检查页面', fde_participants: [], status: 'archived',
    recorder_id: owner, recorder_name: '合成负责人', created_at: '2026-09-20T01:00:00Z'}];
  return {format: 'sales-crm-preview-v1', source_kind: 'crm_export', scope: 'full', dataset_id: 'synthetic-map-layout-v1',
    label: '合成 CRM 兼容性回归数据', counts: {customers: customers.length, opportunities: 1, visits: 1},
    actors: [{user_id: owner, display_name: '合成负责人', role: 'sales', team_ids: [team], team_names: ['合成团队']}],
    role_bindings: {sales: owner}, state: {customers, opportunities, visits,
      ...Object.fromEntries(['contacts', 'tasks', 'actuals', 'notifications', 'claims', 'assignments', 'opportunityEvents', 'risks'].map(key => [key, []])),
      ...Object.fromEntries(['targets', 'conversations', 'runs', 'advice', 'idempotency'].map(key => [key, {}])), serial: 100}};
}
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: '', localLogin: null});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function paint(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1366, height: 768}, serviceWorkers: 'block'});
  const blocked = [], errors = [], reads = [], data = dataset();
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    if (url.pathname === '/local-preview-data') {reads.push(url.pathname); return route.fulfill({json: data});}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/customers/index');
    await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/customers/index' &&
      SalesRuntime.current._crmCustomers?.length === 55 && !SalesRuntime.current.data.acvLoading && !SalesRuntime.current.data.assetLoading);
    await page.locator('#crm-customer-pager').waitFor(); await paint(page);
    await run(page, data);
    assert.deepEqual(blocked, []); assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.deepEqual(reads, ['/local-preview-data']);
  } finally {await context.close();}
}
const plottedIds = page => page.locator('.plot-hit-area').evaluateAll(rows => rows.map(row => row.dataset.id));
const listedIds = page => page.locator('.customer-list > .customer-card').evaluateAll(rows => rows.map(row => row.dataset.id));

test('full CRM map preserves missing scores and all known points while the adjacent list pages by 50', {timeout: 30000}, () => fixture(async (page, data) => {
  assert.equal(await page.locator('.battle-map').isVisible(), true);
  assert.deepEqual(await listedIds(page), data.state.customers.slice(0, 50).map(row => row.id));
  assert.deepEqual(await plottedIds(page), scoredIndices.map(index => data.state.customers[index].id));
  assert.equal(await page.locator('.asset-list-count').innerText(), '55 家 · 全量筛选结果');
  assert.match(await page.locator('#crm-customer-pager').innerText(), /第 1 \/ 2 页 · 每页 50 家/);
  assert.equal(await page.locator('#crm-customer-prev').isDisabled(), true);
  const before = await page.locator('.battle-map').boundingBox();
  await page.locator('.web-customer-list-scroll').evaluate(node => node.scrollTop = node.scrollHeight);
  await paint(page);
  const last = await page.locator('.customer-list > .customer-card').last().boundingBox();
  assert.ok(last && last.y >= 0 && last.y + last.height <= 768, 'the final row is reachable inside the list pane');
  assert.deepEqual(await page.locator('.battle-map').boundingBox(), before, 'scrolling the list keeps the map in place');
  await page.locator('#crm-customer-next').click();
  await page.waitForFunction(() => SalesRuntime.current._crmPage === 1 && SalesRuntime.current.data.customers.length === 5);
  await paint(page);
  assert.deepEqual(await listedIds(page), data.state.customers.slice(50).map(row => row.id));
  assert.deepEqual(await plottedIds(page), scoredIndices.map(index => data.state.customers[index].id), 'pagination cannot remove points outside the current 5-row page');
  assert.equal(await page.locator('.asset-list-count').innerText(), '55 家 · 全量筛选结果');
  assert.match(await page.locator('#crm-customer-pager').innerText(), /第 2 \/ 2 页/);
  assert.equal(await page.locator('#crm-customer-next').isDisabled(), true);
  await page.locator('#crm-customer-prev').click();
  await page.waitForFunction(() => SalesRuntime.current._crmPage === 0 && SalesRuntime.current.data.customers.length === 50);
}));

test('full CRM search keeps unscored customers, accurate totals and reachable second page without inventing zero-score dots', {timeout: 30000}, () => fixture(async (page, data) => {
  const expected = data.state.customers.filter(row => row.name.includes('缺评分'));
  assert.equal(expected.length, 52);
  await page.locator('.map-search input').fill('缺评分');
  await page.waitForFunction(() => SalesRuntime.current._crmCustomers.length === 52 && SalesRuntime.current.data.plotCustomers.length === 0);
  await paint(page);
  assert.equal(await page.locator('.asset-list-count').innerText(), '52 家 · 全量筛选结果');
  assert.deepEqual(await listedIds(page), expected.slice(0, 50).map(row => row.id));
  assert.deepEqual(await plottedIds(page), []);
  assert.match(await page.locator('.map-empty').innerText(), /可在客户列表中查看/);
  assert.equal(await page.locator(`.customer-card[data-id="${expected[0].id}"]`).isVisible(), true, 'a customer missing only relationship score stays in the list');
  await page.locator('#crm-customer-next').click();
  await page.waitForFunction(() => SalesRuntime.current._crmPage === 1 && SalesRuntime.current.data.customers.length === 2);
  await paint(page);
  assert.deepEqual(await listedIds(page), expected.slice(50).map(row => row.id));
  assert.equal(await page.locator('.asset-list-count').innerText(), '52 家 · 全量筛选结果');
  assert.equal(await page.locator('#crm-customer-next').isDisabled(), true);
  await page.locator('.map-search input').fill('没有匹配的合成客户');
  await page.waitForFunction(() => SalesRuntime.current._crmCustomers.length === 0);
  await paint(page);
  assert.equal(await page.locator('.asset-list-count').innerText(), '0 家 · 全量筛选结果');
  assert.deepEqual(await plottedIds(page), []);
  assert.match(await page.locator('.map-empty').innerText(), /当前筛选范围暂无客户/);
  assert.match(await page.locator('.customer-list .empty').innerText(), /没有匹配的客户/);
  await page.locator('.map-search input').fill('');
  await page.waitForFunction(() => SalesRuntime.current._crmCustomers.length === 55 && SalesRuntime.current._crmPage === 0);
  await paint(page);
  assert.deepEqual(await listedIds(page), data.state.customers.slice(0, 50).map(row => row.id));
  assert.deepEqual(await plottedIds(page), scoredIndices.map(index => data.state.customers[index].id));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight), true);
}));
