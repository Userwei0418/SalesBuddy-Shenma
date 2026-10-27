// Built shared apiClient/reader/controller and actual React UI, frozen synthetic wire fixture.
// All files and API responses are served in memory; no running app or business service is used.
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const staticRoot = fileURLToPath(new URL('../dist/', import.meta.url));
const wire = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/opportunity-change-history.v1.json', import.meta.url), 'utf8'));
const base = 'https://opportunity-history.test';
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
const history = page => page.getByRole('region', {name: '变更历史', exact: true});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function open(page, id = opportunity) {
  await page.evaluate(({customer, id}) => SalesRuntime.wx.navigateTo({url: `/pages/customer-assets/index?customer_id=${customer}&opportunity_id=${id}&period=all&readonly=1`}), {customer, id});
  await page.waitForFunction(id => SalesRuntime.current.data.opportunity?.id === id && !SalesRuntime.current.data.opportunityLoading, id);
  await page.getByRole('tab', {name: '商机进展', exact: true}).click();
  await history(page).waitFor();
}
async function ready(page) {
  await page.waitForFunction(() => SalesRuntime.current.data.changeHistory.loaded && !SalesRuntime.current.data.changeHistory.loading);
  await paint(page);
}
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1366, height: 900}, serviceWorkers: 'block'});
  const errors = [], blocked = [];
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'sales'));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    if (url.pathname === '/web-capabilities') return route.fulfill({json: {previewOnly: true}});
    const file = path.resolve(staticRoot, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1));
    if (!file.startsWith(staticRoot) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    return route.fulfill({body: fs.readFileSync(file), contentType: ({'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml'})[path.extname(file)] || 'application/octet-stream'});
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(({wire, opportunity}) => {
      const sample = wire.changes.items[0];
      // No AI permission: history must still load using opportunity read access.
      for (const key of ['advice.read', 'advice.opportunity']) {
        SalesRuntime.app.globalData.session.permissions[key] = false;
        SalesRuntime.app.globalData.session.capabilities[key] = false;
      }
      const items = Array.from({length: 13}, (_, i) => ({...structuredClone(sample),
        id: `11111111-0000-4000-8000-${String(100 - i).padStart(12, '0')}`,
        version_no: 30 - i, created_at: `2026-10-20T07:${String(30 - i).padStart(2, '0')}:00Z`,
        reason: `合成保存记录 ${30 - i}`, ...(i ? {fields: sample.fields.slice(0, 1), detail_status: 'recorded', unavailable_field_count: 0} : {}),
      }));
      items[0].fields.find(field => field.field === 'material_lines').after.truncated = true;
      const original = SalesPreview, probe = window.historyProbe = {
        items, reads: [], calls: [], held: [], hold: false, failure: 0, revision: 'a'.repeat(64), opportunity,
      };
      const respond = (options, response) => {
        const done = () => {options.success?.(response); options.complete?.(response);};
        if (probe.hold) probe.held.push(done); else queueMicrotask(done);
        return {abort() {}};
      };
      window.SalesPreview = {...original, request(options) {
        const url = new URL(options.url, location.href), pathname = url.pathname, method = (options.method || 'GET').toUpperCase();
        probe.calls.push({pathname, method});
        const match = pathname.match(/\/opportunities\/([^/]+)\/change-history$/);
        if (match) {
          const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit'));
          probe.reads.push({id: match[1], offset, limit, revision: url.searchParams.get('expected_history_revision')});
          if (probe.failure) return respond(options, {statusCode: probe.failure, data: {detail: probe.failure === 403 ? '已无权读取当前商机历史' : probe.failure === 409 ? '历史已有更新' : '合成历史暂不可用'}});
          const end = Math.min(offset + limit, probe.items.length);
          const data = {...structuredClone(wire), opportunity_id: match[1], history_revision: probe.revision,
            changes: {...wire.changes, items: structuredClone(probe.items.slice(offset, end)), offset, limit, has_more: end < probe.items.length, next_offset: end < probe.items.length ? end : null}};
          return respond(options, {statusCode: 200, data});
        }
        return original.request(options);
      }};
    }, {wire, opportunity});
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    // The preview homepage can acknowledge its seeded notifications during startup.
    // Opportunity/history/advice interactions themselves must remain read-only.
    assert.deepEqual(await page.evaluate(() => historyProbe.calls.filter(c => c.method !== 'GET' && /^\/api\/v1\/(opportunities|advice)(\/|$)/.test(c.pathname))), []);
  } catch (error) {
    console.error('HISTORY_DIAGNOSTIC', await page.evaluate(() => ({route: window.SalesRuntime?.current?.route, state: window.SalesRuntime?.current?.data?.changeHistory, reads: window.historyProbe?.reads})));
    throw error;
  } finally {await context.close();}
}

test('real shared wire renders all field types, provenance and restrictions without AI permission or generation', () => fixture(async page => {
  await open(page); await ready(page);
  assert.equal(await history(page).locator('article').count(), 10);
  assert.equal(await history(page).getByText('记录版本 30', {exact: true}).count(), 1);
  assert.match(await history(page).innerText(), /当前姓名/);
  assert.doesNotMatch(await history(page).innerText(), /（当前姓名）（当前姓名）|\[object Object\]|"rows"|"before"/);
  const amount = history(page).getByRole('row').filter({has: page.getByRole('rowheader', {name: 'ACV（万元）', exact: true})});
  assert.deepEqual(await amount.getByRole('cell').allTextContents(), ['变更前10', '变更后20']);
  for (const label of ['2026 Q4 预测回款（万元）', '销售参与者', 'FDE参与者', '长期FDE名单', '保存时的物料名称', '数量：2；成交单价（元）：5000；用途：自用', '保存时的产品线名称']) {
    assert.ok((await history(page).innerText()).includes(label), label);
  }
  assert.equal(await history(page).getByText('无权查看此字段（财务实际）', {exact: true}).count(), 2);
  assert.equal(await history(page).getByText('3 个字段信息不完整，仅展示已保存的内容。', {exact: true}).count(), 1);
  assert.equal(await history(page).getByText('此历史值较长，已显示部分内容。', {exact: true}).count(), 1);
  assert.equal(await page.getByRole('button', {name: /生成建议/}).count(), 0);
  assert.equal(await page.getByRole('heading', {name: /已记录动态/}).count(), 1);
  assert.deepEqual(await page.evaluate(() => historyProbe.reads), [{id: opportunity, offset: 0, limit: 10, revision: null}]);
  if (process.env.SALES_HISTORY_SCREENSHOTS) {
    fs.mkdirSync(process.env.SALES_HISTORY_SCREENSHOTS, {recursive: true});
    await page.screenshot({path: `${process.env.SALES_HISTORY_SCREENSHOTS}/history-runtime.png`});
  }
}));

test('failed earlier page retains history; retry uses the same offset and revision then appends in server order', () => fixture(async page => {
  await open(page); await ready(page);
  await page.evaluate(() => {historyProbe.failure = 503;});
  await history(page).getByRole('button', {name: '加载更早变更', exact: true}).click();
  await history(page).getByRole('alert').waitFor();
  assert.equal(await history(page).locator('article').count(), 10);
  await page.evaluate(() => {historyProbe.failure = 0;});
  await history(page).getByRole('button', {name: '重试读取历史', exact: true}).click(); await ready(page);
  assert.equal(await history(page).locator('article').count(), 13);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.changeHistory.items.map(i => i.versionLabel)), Array.from({length: 13}, (_, i) => `记录版本 ${30 - i}`));
  assert.deepEqual(await page.evaluate(() => historyProbe.reads.slice(-2).map(({offset, revision}) => ({offset, revision}))), [
    {offset: 10, revision: 'a'.repeat(64)}, {offset: 10, revision: 'a'.repeat(64)},
  ]);
  assert.equal(await history(page).getByRole('button', {name: '加载更早变更', exact: true}).count(), 0);
}));

test('conflicting pagination keeps existing records until explicit reload restarts at zero with the new revision', () => fixture(async page => {
  await open(page); await ready(page);
  await page.evaluate(() => {historyProbe.failure = 409;});
  await history(page).getByRole('button', {name: '加载更早变更', exact: true}).click();
  await history(page).getByRole('button', {name: '重新读取历史', exact: true}).waitFor();
  assert.equal(await history(page).locator('article').count(), 10);
  assert.equal(await history(page).getByRole('button', {name: '加载更早变更', exact: true}).count(), 0);
  await page.evaluate(() => {historyProbe.failure = 0; historyProbe.revision = 'b'.repeat(64); historyProbe.items = [{...historyProbe.items[0], version_no: 31, reason: '新的保存记录'}];});
  await history(page).getByRole('button', {name: '重新读取历史', exact: true}).click(); await ready(page);
  assert.equal(await history(page).locator('article').count(), 1);
  assert.equal(await history(page).getByText('记录版本 31', {exact: true}).count(), 1);
  assert.deepEqual(await page.evaluate(() => historyProbe.reads.at(-1)), {id: opportunity, offset: 0, limit: 10, revision: null});
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.changeHistory.revision), 'b'.repeat(64));
}));

test('permission revocation clears old records and recovery reads again; empty result stays distinct from failure', () => fixture(async page => {
  await open(page); await ready(page);
  await page.evaluate(() => {historyProbe.failure = 403;});
  await history(page).getByRole('button', {name: '刷新历史', exact: true}).click();
  await history(page).getByRole('alert').waitFor();
  assert.equal(await history(page).locator('article').count(), 0);
  assert.equal(await history(page).getByText('暂无可展示的变更历史', {exact: true}).count(), 0);
  await page.evaluate(() => {historyProbe.failure = 0; historyProbe.items = [];});
  await history(page).getByRole('button', {name: '重试读取历史', exact: true}).click(); await ready(page);
  assert.equal(await history(page).getByText('暂无可展示的变更历史', {exact: true}).count(), 1);
  assert.ok((await history(page).innerText()).includes('不补造缺失历史'));
}));

test('late responses cannot leak across opportunity navigation or account reload', () => fixture(async page => {
  await page.evaluate(() => {historyProbe.hold = true;});
  await open(page);
  await page.waitForFunction(() => historyProbe.held.length === 1);
  await page.evaluate(() => {historyProbe.hold = false; historyProbe.items = [{...historyProbe.items[0], reason: '第二商机专属记录'}];});
  const second = '00000011-0000-4000-8000-000000000002';
  await open(page, second); await ready(page);
  await page.evaluate(() => {historyProbe.held.shift()();}); await paint(page);
  assert.equal(await history(page).locator('article').count(), 1);
  assert.ok((await history(page).innerText()).includes('第二商机专属记录'));
  assert.doesNotMatch(await history(page).innerText(), /合成保存记录/);
  await page.evaluate(() => {historyProbe.hold = true;});
  await history(page).getByRole('button', {name: '刷新历史', exact: true}).click();
  await page.waitForFunction(() => historyProbe.held.length === 1);
  await page.evaluate(() => {
    historyProbe.hold = false; historyProbe.items = [];
    SalesRuntime.app.globalData.session.userId = 'synthetic-other-user';
    SalesRuntime.current.onHide(); SalesRuntime.current.onShow();
  });
  await ready(page);
  await page.evaluate(() => {historyProbe.held.shift()();}); await paint(page);
  assert.equal(await history(page).locator('article').count(), 0);
  assert.equal(await history(page).getByText('暂无可展示的变更历史', {exact: true}).count(), 1);
}));
