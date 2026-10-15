/** Department component candidate: original Page handlers + isolated synthetic API.
 * No real backend, account or microphone is used. The voice-stop test explicitly
 * stubs the recorder hardware only; task creation itself uses the preview store.
 */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({previewOnly: true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function fixture(run, viewport = {width: 1440, height: 1000}, role = 'manager') {
  const context = await browser.newContext({viewport, serviceWorkers: 'block'}), errors = [], blocked = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(15000); page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await page.waitForFunction(() => globalThis.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      window.departmentTaskWrites = [];
      const preview = SalesPreview;
      window.SalesPreview = {...preview, request(options = {}) {
        if (/POST|PATCH|PUT|DELETE/.test(options.method || 'GET')) departmentTaskWrites.push({url: options.url, data: options.data});
        if (window.departmentFailTaskCreate && options.method === 'POST' && new URL(options.url, location.href).pathname.endsWith('/tasks')) {
          queueMicrotask(() => {const response = {statusCode: 503, data: {detail: '合成发送失败，请重试'}}; options.success?.(response); options.complete?.(response);}); return {abort() {}};
        }
        return preview.request(options);
      }};
    });
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function go(page, path) {
  await page.evaluate(path => SalesRuntime.wx.navigateTo({url: path}), path);
  await page.waitForFunction(path => SalesRuntime.current.route === path.split('?')[0].replace(/^\//, ''), path);
}
async function create(page) {
  await go(page, '/pages/management-task-create/index'); await page.locator('.department-task-create').waitFor();
  await page.waitForFunction(() => SalesRuntime.current.data.members.length > 0 && !SalesRuntime.current.data.recipientLoading);
}
async function recipient(page) {
  await page.getByRole('combobox', {name: '任务负责人', exact: true}).click();
  await page.locator('.ant-select-item-option').first().click();
  await page.waitForFunction(() => Boolean(SalesRuntime.current.data.selectedMember));
  await page.locator('.department-task-delivery').waitFor();
}
async function confirm(page) {await page.locator('.wx-modal-mask').getByRole('button', {name: '确认下发', exact: true}).click();}

test('creation validates through shared handler, preserves cancel/failure input, and reads back a confirmed synthetic task', () => fixture(async page => {
  await create(page);
  await page.getByRole('button', {name: '发送任务', exact: true}).click();
  assert.equal(await page.locator('.wx-modal-mask').count(), 0);
  assert.ok(await page.locator('.ant-form-item-explain-error').count() >= 2);
  assert.deepEqual(await page.evaluate(() => departmentTaskWrites), []);
  const description = '合成组件验收：整理客户方案并提交评审。';
  await page.getByRole('textbox', {name: '任务描述', exact: true}).fill(description);
  await recipient(page);
  const target = await page.evaluate(() => ({id: SalesRuntime.current.data.selectedMember.id, account: SalesRuntime.current.data.selectedMember.account, due: SalesRuntime.current.getSelectedDueAt()}));
  assert.equal(target.id, await page.evaluate(() => SalesRuntime.current.data.members[SalesRuntime.current.data.selectedAssigneeIndex].id));
  await page.getByRole('button', {name: '发送任务', exact: true}).click();
  await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.getByRole('textbox', {name: '任务描述', exact: true}).inputValue(), description);
  assert.deepEqual(await page.evaluate(() => departmentTaskWrites), []);
  await page.evaluate(() => {window.departmentFailTaskCreate = true;});
  await page.getByRole('button', {name: '发送任务', exact: true}).click(); await confirm(page);
  await page.locator('.wx-toast').filter({hasText: '合成发送失败'}).waitFor();
  await page.waitForFunction(() => !SalesRuntime.current.data.submitting);
  assert.equal(await page.getByRole('textbox', {name: '任务描述', exact: true}).inputValue(), description);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedMember.id), target.id);
  await page.evaluate(() => {window.departmentFailTaskCreate = false;});
  await page.getByRole('button', {name: '发送任务', exact: true}).click(); await confirm(page);
  await page.waitForFunction(() => Boolean(SalesRuntime.wx.getStorageSync('lastManagementTaskCreated')?.id));
  const id = await page.evaluate(() => SalesRuntime.wx.getStorageSync('lastManagementTaskCreated').id);
  await page.waitForFunction(() => SalesRuntime.current.route !== 'pages/management-task-create/index');
  await go(page, '/pages/task-detail/index?id=' + encodeURIComponent(id));
  await page.waitForFunction(() => SalesRuntime.current.data.task?.id && !SalesRuntime.current.data.loading);
  const task = await page.evaluate(() => SalesRuntime.current.data.task);
  assert.equal(task.id, id); assert.equal(task.description, description); assert.equal(task.status, 'pending_confirm');
  const body = await page.evaluate(() => departmentTaskWrites.filter(item => new URL(item.url, location.href).pathname.endsWith('/tasks')).at(-1).data);
  assert.equal(body.assignee_account_code, target.account); assert.equal(body.due_at, new Date(target.due).toISOString());
  assert.equal(body.association_kind, 'daily'); assert.equal(body.customer_id, null); assert.equal(body.opportunity_id, null);
}));

test('datetime editing uses original date and time values; expired time cannot create a task', () => fixture(async page => {
  await create(page);
  const input = page.getByRole('textbox', {name: '任务截止时间'});
  const date = await page.evaluate(() => SalesRuntime.current.data.customDueDate);
  await input.click(); await page.locator('.ant-picker-dropdown').waitFor();
  assert.match(await page.locator('.ant-picker-month-btn').innerText(), /月/);
  await input.fill(date + ' 18:35'); await input.press('Enter');
  await page.waitForFunction(() => SalesRuntime.current.data.customDueTime === '18:35');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueDate), date);
  assert.equal(await page.evaluate(() => new Date(SalesRuntime.current.getSelectedDueAt()).getHours()), 18);
  assert.equal(await page.evaluate(() => new Date(SalesRuntime.current.getSelectedDueAt()).getMinutes()), 35);
  await page.getByRole('textbox', {name: '任务描述', exact: true}).fill('合成过期截止时间校验'); await recipient(page);
  // Advance the selected deadline into the past using the same shared handlers.
  // The UI itself already prevents choosing a date earlier than minDueDate.
  await page.evaluate(() => {SalesRuntime.current.changeDueDate({detail: {value: '2020-01-01'}}); SalesRuntime.current.changeDueTime({detail: {value: '10:00'}});});
  await page.getByRole('button', {name: '发送任务', exact: true}).click();
  assert.equal(await page.locator('.wx-modal-mask').count(), 0);
  assert.match(await page.locator('.department-task-settings').innerText(), /截止时间需要晚于当前时间/);
  assert.deepEqual(await page.evaluate(() => departmentTaskWrites), []);
}));

test('customer task links use native permitted directories and task type clears the association', () => fixture(async page => {
  await create(page); await page.locator('.ant-radio-button-wrapper').filter({hasText: '客户任务'}).click();
  await page.getByRole('button', {name: '选择客户', exact: false}).click();
  await page.locator('.department-task-selector-row').first().waitFor();
  await page.locator('.department-task-selector-row').first().click();
  await page.waitForFunction(() => Boolean(SalesRuntime.current.data.customerId));
  const customerId = await page.evaluate(() => SalesRuntime.current.data.customerId);
  await page.getByRole('button', {name: '选择商机', exact: false}).click();
  await page.locator('.department-task-selector-row').first().waitFor();
  const opportunity = await page.evaluate(() => SalesRuntime.current.data.selectorRows[0]);
  await page.locator('.department-task-selector-row').first().click();
  await page.waitForFunction(() => SalesRuntime.current.data.linkVerified);
  assert.equal(opportunity.customer_id, customerId); assert.equal(await page.evaluate(() => SalesRuntime.current.data.opportunityId), opportunity.id);
  await page.locator('.ant-radio-button-wrapper').filter({hasText: '日常工作任务'}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), ''); assert.equal(await page.evaluate(() => SalesRuntime.current.data.opportunityId), '');
  assert.deepEqual(await page.evaluate(() => departmentTaskWrites), []);
}));

test('simulated recording can always stop before any form fields are complete', () => fixture(async page => {
  await create(page);
  // Recorder hardware is a deliberate stub; no actual microphone permission or
  // real audio transcription is requested by this synthetic UI regression.
  await page.evaluate(() => {const page = SalesRuntime.current; window.departmentRecorderStops = 0; page.recorderManager = {stop() {departmentRecorderStops++; page.setData({isRecording: false, isStopping: false});}}; page.setData({isRecording: true, recordingTime: '00:10'});});
  await page.getByRole('button', {name: '结束录音', exact: true}).click();
  assert.equal(await page.evaluate(() => departmentRecorderStops), 1);
  assert.deepEqual(await page.evaluate(() => departmentTaskWrites), []);
}));

test('overview keeps status tabs, local pages only loaded rows, and sort resets the visual page', () => fixture(async page => {
  await go(page, '/pages/tasks/index?overview=all_pending'); await page.locator('.department-tasks').waitFor();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading);
  assert.equal(await page.getByRole('tab').count(), 4); assert.equal(await page.locator('.department-task-overview').count(), 1);
  await page.getByRole('tab', {name: /全部/}).click(); await page.waitForFunction(() => !SalesRuntime.current.data.loading && SalesRuntime.current.data.activeTab === 'all');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.overviewFilter), ''); assert.ok(!page.url().includes('overview='));
  const before = await page.evaluate(() => ({count: SalesRuntime.current.data.filteredTasks.length, more: SalesRuntime.current.data.hasMore, total: SalesRuntime.current.data.filteredTotal}));
  assert.ok(await page.locator('.ant-table-tbody .ant-table-row').count() <= 6);
  if (before.count > 6) {
    const first = await page.locator('.department-task-title').first().innerText();
    await page.locator('.ant-pagination-item-2').click();
    assert.notEqual(await page.locator('.department-task-title').first().innerText(), first);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.filteredTasks.length), before.count);
  }
  if (before.more) {
    await page.getByRole('button', {name: '加载更多', exact: true}).click();
    await page.waitForFunction(count => !SalesRuntime.current.data.loadingMore && SalesRuntime.current.data.filteredTasks.length > count, before.count);
  }
  assert.match(await page.locator('.department-task-pagination').innerText(), /已加载/);
  await page.getByRole('combobox', {name: '任务排序'}).click(); await page.locator('.ant-select-item-option').filter({hasText: '最近创建优先'}).click();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading && SalesRuntime.current.data.sortOptions[SalesRuntime.current.data.sortIndex].key === 'created_desc');
  assert.equal(await page.locator('.ant-pagination-item-active').getAttribute('title'), '1');
}, {width: 1440, height: 1000}, 'sales'));

for (const viewport of [{width: 1024, height: 600}, {width: 390, height: 844}]) test(`task workspace and form remain reachable at ${viewport.width}x${viewport.height}`, () => fixture(async page => {
  await go(page, '/pages/tasks/index'); await page.locator('.department-tasks').waitFor(); await page.waitForFunction(() => !SalesRuntime.current.data.loading);
  assert.ok(await page.locator('.ant-table-tbody .ant-table-row').count() <= (viewport.width <= 600 ? 6 : 4));
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await create(page);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
  await page.getByRole('button', {name: '发送任务', exact: true}).scrollIntoViewIfNeeded();
  assert.equal(await page.getByRole('button', {name: '发送任务', exact: true}).isVisible(), true);
}, viewport));

test('sales task pagination stays within the first 1366x768 desktop screen', () => fixture(async page => {
  await go(page, '/pages/tasks/index'); await page.locator('.department-task-pagination').waitFor();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading);
  assert.equal(await page.locator('.ant-table-tbody .ant-table-row').count(), 4);
  const geometry = await page.locator('.department-task-pagination').evaluate(element => ({bottom: element.getBoundingClientRect().bottom, height: innerHeight}));
  assert.ok(geometry.bottom <= geometry.height, JSON.stringify(geometry));
}, {width: 1366, height: 768}, 'sales'));
