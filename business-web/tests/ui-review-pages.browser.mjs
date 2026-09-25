/** UI review: local synthetic accounts only; no backend or Agent traffic. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const customer = '00000010-0000-4000-8000-000000000001';
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: '', localLogin: null});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function fixture(run, {role = 'sales', width = 1440, height = 1000} = {}) {
  const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block'});
  const forbidden = [], errors = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {
      forbidden.push(url.pathname); return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await paint(page); await run(page);
    assert.deepEqual(forbidden, []); assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function go(page, path) {
  await page.evaluate(path => SalesRuntime.route('/pages/' + path), path);
  await page.waitForFunction(() => !SalesRuntime.current.data.loading);
  await paint(page);
}
for (const role of ['sales', 'manager']) test(`${role}: compact overview metrics retain drilldown and meaningful accessible names`, () => fixture(async page => {
  await page.locator('.web-home-metric').first().waitFor();
  const metrics = await page.locator('.web-home-metric').evaluateAll(nodes => nodes.map(node => ({
    x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y,
    name: node.getAttribute('aria-label'), title: node.title,
  })));
  assert.equal(metrics.length, 3);
  assert.ok(metrics.every(metric => Math.abs(metric.y - metrics[0].y) < 1), JSON.stringify(metrics));
  assert.ok(metrics.every(metric => metric.name.includes('点击查看任务') && metric.title.includes('点击查看任务')));
  await page.locator('.web-home-metric[data-key=all_pending]').click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && !SalesRuntime.current.data.loading);
  assert.ok(page.url().includes('overview=all_pending'));
  await page.locator('.web-task-tab[data-key=completed]').waitFor();
  assert.equal(await page.locator('.web-task-tab').count(), 4);
}, {role}));

test('customer avatars skip sample prefix and punctuation without changing full names or filter values', () => fixture(async page => {
  await go(page, 'customers/index');
  await page.locator('.customer-card').first().waitFor();
  assert.equal((await page.locator('.customer-card .logo').first().textContent()).trim(), '星');
  assert.ok((await page.locator('.customer-card .name').first().textContent()).trim().length > 0);
  assert.deepEqual(await page.evaluate(() => ['星河制造', '（北京）智算', '(Acme)', '...'].map(SalesReviewPages.initial)), ['星', '北', 'A', '客']);
  const labels = await page.locator('.operating-picker-value').allTextContents();
  assert.ok(!labels.some(label => /全部象限|全部计划|全部优先级|金额：全部/.test(label)));
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.planOptions[0].label), '全部计划');
  await go(page, 'customer-detail/index?id=' + customer);
  await page.waitForFunction(() => SalesRuntime.current.data.customer && SalesRuntime.current.data.detailSummary.loaded);
  await paint(page);
  assert.equal((await page.locator('.customer-mark').textContent()).trim(), '星');
  const stage = page.locator('.opportunity-stage').first();
  if (await stage.count()) {
    assert.equal(await stage.isVisible(), false);
    assert.ok((await page.locator('.opportunity-meta').first().textContent()).includes('50%'));
  }
}));

test('analytics cards have equal presentation while exact money and chart data remain available', () => fixture(async page => {
  await go(page, 'bi/index');
  await page.waitForFunction(() => SalesRuntime.current.data.kpis.length === 8 && !SalesRuntime.current.data.loading);
  await page.locator('.web-review-kpi-heading').first().waitFor();
  assert.equal(await page.locator('.kpi-card').count(), 8);
  assert.deepEqual(await page.locator('.web-review-kpi-heading').allTextContents(), ['实绩与季度预测', '商机结构']);
  const cards = await page.locator('.kpi-card').evaluateAll(nodes => nodes.map(node => ({
    border: getComputedStyle(node).borderWidth, background: getComputedStyle(node).backgroundColor,
  })));
  assert.ok(cards.every(card => card.border === cards[0].border && card.background === cards[0].background));
  const value = page.locator('.web-kpi-value').first();
  assert.match(await value.getAttribute('aria-label'), /元/);
  await value.focus();
  assert.ok((await value.locator('.web-kpi-exact').textContent()).includes('元'));
  assert.ok(await page.locator('.web-chart-data').count());
  assert.equal((await page.locator('.page-heading').textContent()).trim(), '经营分析');
}));

for (const viewport of [{width:1440,height:1000},{width:1024,height:600},{width:390,height:844}]) test(`${viewport.width}px visit tools preserve input and show the selection precondition beside the customer`, () => fixture(async page => {
  await go(page, 'visit-entry/index');
  await page.locator('.note-input').waitFor();
  assert.ok(await page.locator('.web-review-selection-help').isVisible());
  assert.equal(await page.locator('.web-review-visit-steps [role=listitem]').count(), 3);
  assert.ok(await page.locator('.web-review-visit-steps').isVisible());
  assert.equal(await page.locator('.submit-button').isDisabled(), true);
  const transcript = '合成检查：沟通内容与下一步计划，保留草稿输入。';
  await page.locator('.note-input').fill(transcript); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.transcript), transcript);
  assert.equal(await page.locator('.submit-button').isDisabled(), true, 'typing does not bypass customer selection');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const box = await page.locator('.submit-button').boundingBox();
  if (viewport.width > 600) {
    assert.ok(box.y >= 0 && box.y + box.height <= viewport.height + 1, JSON.stringify(box));
    const root = await page.locator('#page-root').evaluate(node => ({client: node.clientHeight, scroll: node.scrollHeight}));
    assert.ok(root.scroll <= root.client + 2, JSON.stringify(root));
  }
  const textarea = await page.locator('.note-input').boundingBox();
  assert.ok(textarea.height <= 482 && textarea.height >= 158, JSON.stringify(textarea));
  await page.locator('[data-handler=switchInputMode][data-mode=file]').click();
  assert.equal(await page.locator('.note-input').inputValue(), transcript);
}, viewport));

test('task rejection guidance sits beside its required reason and empty reason still prevents a transition', () => fixture(async page => {
  await go(page, 'task-detail/index?id=00000015-0000-4000-8000-000000000001');
  await page.waitForFunction(() => SalesRuntime.current.data.task?.canRespond);
  const input = page.locator('.response-card textarea');
  await input.waitFor();
  assert.equal(await input.getAttribute('aria-describedby'), 'web-task-response-note');
  const position = await page.evaluate(() => {
    const notice = document.getElementById('web-task-response-note').getBoundingClientRect();
    const field = document.querySelector('.response-card textarea').getBoundingClientRect();
    return {notice: notice.bottom, field: field.top};
  });
  assert.ok(position.notice <= position.field, JSON.stringify(position));
  await page.locator('[data-handler=rejectTask]').click(); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.task.status), 'pending_confirm');
  assert.equal(await page.locator('.wx-modal').count(), 0);
  await input.fill('合成说明：时间冲突，建议调整截止时间。');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.responseComment), '合成说明：时间冲突，建议调整截止时间。');
}));
