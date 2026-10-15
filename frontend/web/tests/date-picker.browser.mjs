/** Isolated checks against the real page/component bindchange contract. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
async function isolated(run, options = {}) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, ...options});
  const errors = [], forbidden = [], page = await context.newPage();
  page.on('pageerror', e => errors.push(e.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/') || url.pathname === '/local-login') { forbidden.push(url.pathname); return route.abort(); }
    return route.continue();
  });
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && SalesRuntime.current.data.messages?.length);
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(forbidden, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally { await context.close(); }
}
async function route(page, path) {
  await page.evaluate(path => SalesRuntime.route('/pages/' + path + '/index'), path);
  await page.waitForFunction(path => SalesRuntime.current.route === 'pages/' + path + '/index', path);
}
const dateTrigger = page => page.locator('button[data-date-mode="date"]');
const timeTrigger = page => page.locator('button[data-date-mode="time"]');
const dialog = page => page.locator('#web-date-dialog');
const input = page => page.locator('.wx-date-input');

test('task date: cancel/manual validation/bounds/same-day confirmation and render lifecycle', {timeout: 45000}, () => isolated(async page => {
  await route(page, 'management-task-create'); await dateTrigger(page).waitFor();
  const initial = await page.evaluate(() => {
    const owner = SalesRuntime.current, change = owner.changeDueDate;
    owner.__dateChanges = 0; owner.changeDueDate = function (...args) { this.__dateChanges++; return change.apply(this, args); };
    return {date: owner.data.customDueDate, min: owner.data.minDueDate, max: owner.data.maxDueDate};
  });
  await dateTrigger(page).click(); await dialog(page).waitFor();
  assert.equal(await page.evaluate(() => SalesRuntime.current.__dateChanges), 0);
  await input(page).fill(initial.max); await dialog(page).getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueDate), initial.date);
  assert.equal(await page.evaluate(() => SalesRuntime.current.__dateChanges), 0, 'blur while cancelling must not commit manual input');
  await dateTrigger(page).click();
  await input(page).fill('2026-02-31'); await dialog(page).getByRole('button', {name: '使用输入日期'}).click();
  assert.equal(await page.locator('.wx-date-error').isVisible(), true);
  await input(page).fill('1900-01-01'); await input(page).press('Enter');
  assert.equal(await page.locator('.wx-date-error').isVisible(), true);
  assert.equal(await page.evaluate(() => SalesRuntime.current.__dateChanges), 0);
  await page.keyboard.press('Escape'); await dateTrigger(page).click();
  await page.locator('.flatpickr-day.selected').click();
  await dialog(page).waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => SalesRuntime.current.__dateChanges), 1, 'confirming the highlighted initial cursor must emit change');
  await dateTrigger(page).click();
  await page.evaluate(() => SalesRuntime.current.setData({description: '其他字段更新不关闭日历'}));
  await page.waitForFunction(() => document.querySelector('button[data-date-mode="date"]').getAttribute('aria-expanded') === 'true');
  assert.equal(await dialog(page).isVisible(), true);
  await input(page).fill(initial.min); await input(page).press('Enter');
  await dialog(page).waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueDate), initial.min);
  await dateTrigger(page).click();
  await route(page, 'tasks'); await dialog(page).waitFor({state: 'detached'});
}));

test('time keeps HH:mm, preserves cancellation and supports exact minute keyboard entry', {timeout: 45000}, () => isolated(async page => {
  await route(page, 'management-task-create'); await timeTrigger(page).waitFor();
  const initial = await page.evaluate(() => SalesRuntime.current.data.customDueTime);
  await timeTrigger(page).press('Enter'); await dialog(page).waitFor();
  await input(page).fill('18:37'); await dialog(page).getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), initial);
  await timeTrigger(page).click(); await input(page).fill('25:90'); await input(page).press('Enter');
  assert.equal(await page.locator('.wx-date-error').isVisible(), true);
  await input(page).fill('18:37'); await input(page).press('Enter'); await dialog(page).waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), '18:37');
  await timeTrigger(page).click(); await dialog(page).getByRole('button', {name: '09:00', exact: true}).click();
  await dialog(page).getByRole('button', {name: '确定时间', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), '09:00');
  await timeTrigger(page).click();
  await page.locator('.flatpickr-hour').fill('18');
  await dialog(page).getByRole('button', {name: '确定时间', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), '18:00', 'immediate hour confirmation must use the latest field value');
  await timeTrigger(page).click(); await page.locator('.flatpickr-minute').fill('37');
  await dialog(page).getByRole('button', {name: '确定时间', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), '18:37', 'immediate minute confirmation must use the latest field value');
  await timeTrigger(page).click(); await input(page).fill('14:25');
  await input(page).press('Tab');
  assert.equal(await page.locator('.flatpickr-hour').evaluate(el => document.activeElement === el), true);
  assert.equal(await page.locator('.flatpickr-minute').inputValue(), '25');
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('.flatpickr-minute').evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Tab');
  assert.equal(await dialog(page).getByRole('button', {name: '09:00', exact: true}).evaluate(el => document.activeElement === el), true);
  await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab');
  assert.equal(await input(page).evaluate(el => document.activeElement === el), true);
  await dialog(page).getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueTime), '18:37');
}));

test('task deadline gives persistent feedback beside the fields and clears after correction', {timeout: 45000}, () => isolated(async page => {
  await route(page, 'management-task-create'); await dateTrigger(page).click();
  await dialog(page).getByRole('button', {name: '今天', exact: true}).click();
  await timeTrigger(page).click(); await input(page).fill('00:00'); await input(page).press('Enter');
  await page.locator('#web-task-due-error').waitFor();
  assert.equal(await timeTrigger(page).getAttribute('aria-invalid'), 'true');
  await dateTrigger(page).click(); await dialog(page).getByRole('button', {name: '明天', exact: true}).click();
  await page.locator('#web-task-due-error').waitFor({state: 'detached'});
  assert.equal(await timeTrigger(page).getAttribute('aria-invalid'), null);
}));

test('calendar keeps month selection keyboard accessible after changing year, and traps Tab in the dialog', {timeout: 45000}, () => isolated(async page => {
  await route(page, 'management-task-create'); await dateTrigger(page).click();
  const year = await page.evaluate(() => new Date().getFullYear() + 1);
  await page.locator('.cur-year').fill(String(year)); await page.locator('.cur-year').press('Enter');
  const month = page.locator('.flatpickr-monthDropdown-months');
  assert.equal(await month.getAttribute('tabindex'), '0');
  await month.focus(); await month.press('Home'); await month.press('ArrowDown');
  assert.equal(await month.inputValue(), '1', 'native month keyboard must select February');
  assert.equal(await month.evaluate(el => document.activeElement === el), true, 'rebuilt month select must keep keyboard focus');
  await page.locator('.flatpickr-day:not(.prevMonthDay):not(.nextMonthDay):not(.flatpickr-disabled)').filter({hasText: /^15$/}).click();
  await dialog(page).waitFor({state: 'detached'});
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customDueDate), `${year}-02-15`);
  await dateTrigger(page).click(); await dialog(page).getByRole('button', {name: '关闭日期选择'}).focus();
  for (let i=0;i<15;i++) {
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => !!document.activeElement?.closest('#web-date-dialog')), true);
  }
  await page.setViewportSize({width:1440,height:480});
  await page.waitForFunction(() => {const box=document.querySelector('#web-date-dialog').getBoundingClientRect();return box.y>=0 && box.bottom<=innerHeight;});
}));

for (const timezoneId of ['Asia/Shanghai', 'America/Los_Angeles']) {
  test(`opportunity component preserves a leap day in ${timezoneId}`, {timeout: 45000}, () => isolated(async page => {
    await route(page, 'opportunity-create');
    await page.getByPlaceholder('搜索客户名称', {exact: true}).fill('星河');
    await page.locator('.customer-picker-card [data-handler="selectCustomer"]').filter({hasText: '星河'}).click();
    await page.locator('#opportunityForm').waitFor();
    await page.locator('#opportunityForm button[data-date-mode="date"]').click();
    await input(page).fill('0000-01-01'); await input(page).press('Enter');
    assert.equal(await page.locator('.wx-date-error').isVisible(), true);
    await input(page).fill('2024-02-29'); await input(page).press('Enter');
    await dialog(page).waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => SalesRuntime.current.selectComponent('#opportunityForm').data.form.expected_close_date), '2024-02-29');
  }, {timezoneId}));
}

test('mobile calendar stays within 320px viewport and disabled/removed controls close safely', {timeout: 45000}, () => isolated(async page => {
  await route(page, 'management-task-create'); await dateTrigger(page).click();
  const box = await dialog(page).boundingBox(); assert.ok(box.x >= 0 && box.x + box.width <= 320 && box.y >= 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.keyboard.press('Escape'); await dateTrigger(page).waitFor();
  assert.equal(await dateTrigger(page).evaluate(el => document.activeElement === el), true);
  await dateTrigger(page).click();
  await page.evaluate(() => { document.querySelector('button[data-date-mode="date"]').disabled = true; SalesDatePicker.reconcile(); });
  await dialog(page).waitFor({state: 'detached'});
}, {viewport: {width: 320, height: 720}}));
