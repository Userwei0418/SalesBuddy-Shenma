/** Review C1–C3: real shared handlers; isolated synthetic transport only. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {server = createSalesWebServer({target: ''}); await new Promise(r => server.listen(0, '127.0.0.1', r)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); await new Promise(r => server?.close(r));});
async function fixture(run, size = [1440, 1000]) {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'}), errors = [], blocked = [];
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'manager'));
  await context.route('**/*', route => {const u = new URL(route.request().url()); if (u.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)) {blocked.push(u.pathname); return route.abort();} return route.continue();});
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/?mode=preview'); await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {window.reviewFormWrites = []; const preview = SalesPreview; window.SalesPreview = {...preview, request(options = {}) {if (/POST|PATCH|PUT|DELETE/.test(options.method || 'GET')) reviewFormWrites.push(options.url); return preview.request(options);}};});
    await run(page); assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function go(page, route) {await page.evaluate(route => SalesRuntime.wx.navigateTo({url: `/pages/${route}/index`}), route); await page.waitForFunction(route => SalesRuntime.current.route === `pages/${route}/index`, route);}
async function readyCustomer(page) {await go(page, 'customer-create'); await page.waitForFunction(() => SalesRuntime.current.data.fields.length === 10 && SalesRuntime.current.directoryTeams?.length); await page.locator('.review-customer-input').first().waitFor();}
async function choose(page, key) {
  await page.locator(`.field-row[data-field-key="${key}"] .review-customer-choice`).click();
  await page.locator('#web-select-dialog .option[data-selectable]').first().click();
  await page.locator('#web-select-dialog').waitFor({state: 'detached'});
}
async function shot(page, name) {if (!process.env.REVIEW_FORM_SHOTS) return; await mkdir(process.env.REVIEW_FORM_SHOTS, {recursive: true}); await page.screenshot({path: `${process.env.REVIEW_FORM_SHOTS}/${name}.png`});}

test('customer inline text retains native trim, progress, required validation and unsaved invalid input', () => fixture(async page => {
  await readyCustomer(page);
  const input = page.locator('input[data-field-key="customer_name"]');
  await input.fill('  合成桌面建档客户  '); await input.press('Tab');
  await page.waitForFunction(() => SalesRuntime.current.data.fields.find(f => f.key === 'customer_name').value === '合成桌面建档客户');
  assert.equal(await page.locator('.editor-layer').count(), 0);
  await page.waitForFunction(() => document.querySelector('input[data-field-key=customer_name]').value === '合成桌面建档客户');
  await input.fill(''); await input.press('Tab');
  await page.locator('#review-error-customer_name').waitFor();
  assert.equal(await input.inputValue(), '');
  await page.evaluate(() => SalesRuntime.current.setData({}));
  assert.equal(await input.inputValue(), '');
  await page.locator('[data-handler=submitCustomer]').click();
  assert.equal(await page.locator('.wx-modal-mask').count(), 0);
  assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
  assert.equal(await input.inputValue(), '');
  await input.fill('修复后的合成客户'); await input.press('Tab');
  await page.waitForFunction(() => SalesRuntime.current.data.fields.find(f => f.key === 'customer_name').value === '修复后的合成客户');
  await page.locator('#review-error-customer_name').waitFor({state: 'detached'});
  await page.waitForFunction(() => !document.querySelector('.wx-toast'));
  await shot(page, '客户建档-直接填写');
}));

test('active recording can always be stopped despite an invalid inline draft', () => fixture(async page => {
  await readyCustomer(page);
  await page.locator('input[data-field-key="customer_name"]').fill('  ');
  await page.evaluate(() => {
    const page = SalesRuntime.current; window.reviewRecorderStops = 0;
    page.recorderManager = {stop() {reviewRecorderStops++;}};
    page.setData({isRecording: true}); page.toggleVoice();
  });
  assert.equal(await page.evaluate(() => reviewRecorderStops), 1);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.isStopping), true);
  assert.equal(await page.locator('input[data-field-key="customer_name"]').inputValue(), '  ');
}));

test('cleared required text saves as an incomplete draft and restores without the previous value', () => fixture(async page => {
  await readyCustomer(page);
  const name = page.locator('input[data-field-key="customer_name"]');
  await name.fill('应被清除的合成旧名称'); await name.press('Tab');
  await page.waitForFunction(() => SalesRuntime.current.data.fields.find(f => f.key === 'customer_name').value === '应被清除的合成旧名称');
  await name.fill('  '); await name.press('Tab'); await page.locator('#review-error-customer_name').waitFor();
  await page.locator('[data-handler=saveDraft]').click();
  const saved = await page.evaluate(() => {const p = SalesRuntime.current; return {fields: SalesRuntime.wx.getStorageSync(p.draftKey).fields, missing: p.data.missingCount};});
  assert.equal(saved.fields.find(f => f.key === 'customer_name').value, ''); assert.ok(saved.missing > 0);
  await page.evaluate(() => SalesRuntime.wx.navigateBack()); await readyCustomer(page);
  assert.equal(await page.locator('input[data-field-key="customer_name"]').inputValue(), '');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.draftRestored), true);
  await page.locator('[data-handler=submitCustomer]').click();
  assert.equal(await page.locator('.wx-modal-mask').count(), 0); assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
}));

test('voice can repair a blank required draft while preserving other visible text', () => fixture(async page => {
  await readyCustomer(page);
  await page.locator('input[data-field-key="customer_name"]').fill('准备语音重填的合成旧名称');
  await page.locator('input[data-field-key="customer_name"]').press('Tab');
  await page.waitForFunction(() => SalesRuntime.current.data.fields.find(f => f.key === 'customer_name').value === '准备语音重填的合成旧名称');
  await page.locator('input[data-field-key="customer_name"]').fill('');
  await page.locator('input[data-field-key="partner_name"]').fill('  保留的合成合作伙伴  ');
  await page.evaluate(() => {
    const page = SalesRuntime.current; window.reviewRecorderStarts = 0; window.reviewRecorderStops = 0;
    page.requestRecordPermission = granted => granted();
    page.recorderManager = {start() {reviewRecorderStarts++; page.setData({isStarting: false, isRecording: true});}, stop() {reviewRecorderStops++; page.setData({isRecording: false, isStopping: false});}};
    page.toggleVoice();
  });
  assert.equal(await page.evaluate(() => reviewRecorderStarts), 1);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.fields.find(f => f.key === 'partner_name').value), '保留的合成合作伙伴');
  await page.evaluate(() => {const p = SalesRuntime.current; p.toggleVoice(); p.applyVoiceDraft({customer_name: '语音补全的合成客户'});});
  assert.equal(await page.evaluate(() => reviewRecorderStops), 1);
  await page.waitForFunction(() => document.querySelector('input[data-field-key=customer_name]').value === '语音补全的合成客户');
  assert.equal(await page.locator('input[data-field-key="partner_name"]').inputValue(), '保留的合成合作伙伴');
  assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
}));

test('customer selectors keep backend options and stable team IDs; create still asks confirmation', () => fixture(async page => {
  await readyCustomer(page);
  for (const [key, value] of Object.entries({customer_name: '合成界面评审建档', contact_name: '合成联系人', contact_title: '合成联系人职位'})) {
    await page.locator(`input[data-field-key="${key}"]`).fill(value); await page.locator(`input[data-field-key="${key}"]`).press('Tab');
  }
  for (const key of ['customer_type', 'level_code', 'lead_source', 'target_team', 'contact_role']) await choose(page, key);
  await page.waitForFunction(() => SalesRuntime.current.data.missingCount === 0);
  const team = await page.evaluate(() => {const p = SalesRuntime.current, field = p.data.fields.find(f => f.key === 'target_team'); return {field, match: p.directoryTeams.some(team => team.id === field.teamId && team.name === field.value)};});
  assert.equal(team.match, true);
  await page.locator('[data-handler=submitCustomer]').click(); await page.locator('.wx-modal-mask').getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.locator('input[data-field-key="customer_name"]').inputValue(), '合成界面评审建档');
  assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
}));

test('opportunity initial permitted customers appear without typing and preserve explicit selection', () => fixture(async page => {
  await go(page, 'opportunity-create');
  await page.locator('.customer-picker-card [data-handler=selectCustomer]').first().waitFor();
  assert.equal(await page.locator('.search').inputValue(), '');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), '');
  assert.equal(await page.locator('.review-op-preview input:disabled').count(), 2);
  assert.equal(await page.locator('#opportunityForm').count(), 0);
  await shot(page, '新增商机-客户与字段预览');
  const id = await page.locator('[data-handler=selectCustomer]').first().getAttribute('data-id');
  await page.locator('[data-handler=selectCustomer]').first().click();
  await page.locator('#opportunityForm input[data-key=name]').waitFor();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), id);
  assert.equal(await page.locator('.review-op-preview').count(), 0);
  assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
}));

test('initial customer response cannot overwrite a newer search or a departed page', () => fixture(async page => {
  await page.evaluate(() => {const preview = SalesPreview; window.SalesPreview = {...preview, request(options = {}) {const u = new URL(options.url, location.href); return preview.request({...options, success(response) {if (/\/customers$/.test(u.pathname) && Number(u.searchParams.get('page_size')) === 5) setTimeout(() => options.success?.(response), 700); else options.success?.(response);}});}};});
  await go(page, 'opportunity-create');
  await page.locator('.search').fill('星河');
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length > 0 && SalesRuntime.current.data.customers.every(c => c.name.includes('星河')));
  await page.waitForTimeout(900);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customers.every(c => c.name.includes('星河'))), true);
  await go(page, 'opportunity-create'); await page.waitForFunction(() => SalesRuntime.current.data.webCustomerLoading);
  await go(page, 'management-task-create'); await page.waitForTimeout(900);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/management-task-create/index');
  assert.deepEqual(await page.evaluate(() => reviewFormWrites), []);
}));

test('initial customer load failure has retry; no customer is chosen by default', () => fixture(async page => {
  await page.evaluate(() => {
    window.reviewFailCustomers = true; const preview = SalesPreview;
    window.SalesPreview = {...preview, request(options = {}) {
      const u = new URL(options.url, location.href);
      if (reviewFailCustomers && /\/customers$/.test(u.pathname) && Number(u.searchParams.get('page_size')) === 5) {
        const response = {statusCode: 503, data: {message: '合成目录暂不可用'}};
        queueMicrotask(() => {options.success?.(response); options.complete?.(response);}); return {abort() {}};
      }
      return preview.request(options);
    }};
  });
  await go(page, 'opportunity-create'); await page.locator('.review-customer-retry').waitFor();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), '');
  await page.evaluate(() => reviewFailCustomers = false); await page.locator('.review-customer-retry').click();
  await page.locator('.customer-picker-card [data-handler=selectCustomer]').first().waitFor();
  assert.equal(await page.locator('.review-customer-retry').count(), 0);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), '');
}));

for (const size of [[1440, 1000], [1024, 600], [390, 844]]) test(`task order and all reviewed forms fit ${size.join('x')}`, () => fixture(async page => {
  await go(page, 'management-task-create'); await page.waitForFunction(() => SalesRuntime.current.data.members.length > 0);
  const layout = await page.evaluate(() => {const a = document.querySelector('.web-task-content').getBoundingClientRect(), b = document.querySelector('.web-task-settings').getBoundingClientRect(); return {ax: a.x, ay: a.y, bx: b.x, by: b.y, width: innerWidth};});
  if (size[0] > 900) assert.ok(layout.ax < layout.bx, JSON.stringify(layout)); else assert.ok(layout.ay < layout.by, JSON.stringify(layout));
  assert.equal(await page.locator('.section-head .review-required').count(), 3);
  assert.ok(await page.locator('.choice').first().evaluate(el => el.getBoundingClientRect().width) <= 88);
  await page.locator('.bottom-bar button').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (size[0] === 1440) await shot(page, '创建任务-内容优先');
  await readyCustomer(page); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.locator('.web-extended-footer button').first().scrollIntoViewIfNeeded();
  await go(page, 'opportunity-create'); await page.locator('.review-op-preview').waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}, size));
