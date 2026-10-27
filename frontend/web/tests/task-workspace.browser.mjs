import test, {before, after} from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''}); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = 'http://127.0.0.1:' + server.address().port; browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function fixture(run, width = 1440) {
  const context = await browser.newContext({viewport: {width, height: 1000}, timezoneId: 'America/Los_Angeles'});
  const blocked = [], errors = [];
  await context.addInitScript(() => {sessionStorage.setItem('sales-web:preview-role', 'sales');});
  await context.route('**/*', route => {const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)/.test(url.pathname) || url.pathname === '/local-login') {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', e => errors.push(e.message));
  await page.clock.setFixedTime(new Date('2026-09-16T10:00:00+08:00'));
  try {
    await page.goto(base + '/?mode=preview'); await page.waitForFunction(() => window.SalesRuntime?.app?.globalData.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {window.taskCalls = []; const original = SalesPreview.request;
      window.SalesPreview = {...SalesPreview, request(options) {const url = new URL(options.url, location.href); if (url.pathname === '/api/v1/tasks') taskCalls.push(Object.fromEntries(url.searchParams)); return original(options);}};
    });
    await run(page); assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function go(page, query = '') {
  await page.evaluate(query => SalesRuntime.route('/pages/tasks/index' + query, {tab: true}), query);
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && !SalesRuntime.current.data.loading);
  await page.locator('.web-task-controls').waitFor();
}
test('sales queue defaults to earliest deadline, separates urgency and keeps confirmation and opportunity routes', () => fixture(async page => {
  await go(page);
  assert.equal(await page.evaluate(() => taskCalls.at(-1).order), 'due_asc');
  assert.deepEqual(await page.locator('.web-task-group h2').allTextContents(), ['已逾期', '今天到期', '后续安排']);
  assert.equal(await page.locator('.web-task-row').count(), 18);
  assert.equal(await page.locator('.web-task-description').count(), 0);
  assert.equal(await page.locator('.web-task-deadline').first().innerText(), '已逾期 17 小时\n9月15日 17:00');
  const id = await page.locator('.web-task-row').first().getAttribute('data-id');
  await page.locator('.web-task-action').first().focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(id => SalesRuntime.current.route === 'pages/task-detail/index' && SalesRuntime.current.data.task?.id === id, id);
  await page.locator('[data-handler="acceptTask"]').click();
  await page.locator('.wx-modal-buttons button').last().click();
  await page.waitForFunction(() => SalesRuntime.current.data.task?.status === 'pending_execution');
  await page.evaluate(() => SalesRuntime.wx.navigateBack());
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && !SalesRuntime.current.data.loading);
  await page.locator(`.web-task-row[data-id="${id}"] .web-task-state text`).filter({hasText: '已接受'}).waitFor();
  const opportunityId = await page.locator('.web-task-opportunity').first().getAttribute('data-opportunity-id');
  await page.locator('.web-task-opportunity').first().click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customers/index');
  assert.ok(opportunityId);
}));
test('sorting uses the mature selector and requests a fresh server page; tabs keep counts and completed tasks never look overdue', () => fixture(async page => {
  await go(page); await page.locator('.web-task-controls select').click();
  await page.locator('#web-select-dialog .option[data-value="1"]').click();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading && taskCalls.at(-1).order === 'due_desc');
  assert.equal(await page.locator('.web-task-group').count(), 0);
  await page.locator('.web-task-tab[data-key="completed"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.activeTab === 'completed' && !SalesRuntime.current.data.loading);
  await page.waitForFunction(() => document.querySelectorAll('.web-task-row').length === 4);
  assert.equal(await page.locator('.web-task-row').count(), 4); assert.equal(await page.locator('.time-overdue').count(), 0);
  assert.ok((await page.locator('.web-task-action').allTextContents()).every(text => text.includes('查看结果')));
  await page.locator('.web-task-tab[data-key="all"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.activeTab === 'all' && !SalesRuntime.current.data.loading);
  await page.waitForFunction(() => document.querySelectorAll('.web-task-row').length === 20);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.filteredTotal), 22);
  const firstIds = await page.locator('.web-task-row').evaluateAll(rows => rows.map(row => row.dataset.id));
  // Reaching the footer invokes the original onReachBottom handler; clicking
  // after auto-scrolling can race with its removal of the last-page button.
  const scrollHost = '.web-task-content';
  await page.locator(scrollHost).evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForFunction(() => !SalesRuntime.current.data.hasMore && !SalesRuntime.current.data.loadingMore);
  await page.waitForFunction(() => document.querySelectorAll('.web-task-row').length === 22);
  const allIds = await page.locator('.web-task-row').evaluateAll(rows => rows.map(row => row.dataset.id));
  assert.equal(allIds.length, 22); assert.equal(new Set(allIds).size, 22); assert.deepEqual(allIds.slice(0, 20), firstIds);
  assert.equal(await page.evaluate(() => taskCalls.at(-1).offset), '20');
  assert.equal(await page.evaluate(() => taskCalls.filter(call => call.tab === 'all' && call.offset === '20').length), 1);
}));
for (const width of [1440, 1024, 390, 320]) test(`${width}px task rows retain readable actions without horizontal overflow`, () => fixture(async page => {
  await go(page);
  await page.evaluate(() => {
    const tasks = SalesRuntime.current.data.filteredTasks.map((task, index) => index === 0 ? {...task, title: '确认需求_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ', customer: '客户名称很长也要能识别对应的业务对象', opportunityName: '商机名称很长也不能挤掉截止时间和处理入口'} : task);
    SalesRuntime.current.setData({filteredTasks: tasks});
  });
  await page.locator('.web-task-title').filter({hasText: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'}).waitFor();
  const layout = await page.evaluate(() => {
    const root = document.querySelector('#page-root'), row = document.querySelector('.web-task-row'), action = row.querySelector('.web-task-action');
    return {viewport: innerWidth, document: document.documentElement.scrollWidth, root: root.clientWidth, scroll: root.scrollWidth,
      row: row.getBoundingClientRect().toJSON(), action: action.getBoundingClientRect().toJSON()};
  });
  assert.ok(layout.document <= layout.viewport + 1); assert.ok(layout.scroll <= layout.root + 1, JSON.stringify(layout));
  assert.ok(layout.action.x >= layout.row.x && layout.action.right <= layout.row.right + 1);
  assert.equal(await page.locator('.web-task-title').first().isVisible(), true);
}, width));
