/* Actual visit input handlers and Web renderer; isolated synthetic preview only. */
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
async function fixture(run, firstVisit = false) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  const errors = [], blocked = [];
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'sales'));
  await context.route('**/*', route => {const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/') || url.pathname === '/local-login') {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/visit-entry/index');
    await page.locator('.customer-result[data-handler="chooseCustomer"]').filter({hasText: '星河制造'}).click();
    if (firstVisit) await page.locator('input[type="checkbox"][value="first"]').check();
    await page.locator('textarea.note-input').fill('沟通内容：合成测试核对验收清单。\n下一步计划：明天由我整理清单并发送给客户。\n跟进日期：2026-09-16\n对接人：合成测试联系人');
    await page.locator('[data-handler="submitTranscript"]').click();
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/visit-confirm/index' && SalesRuntime.current.data.values.contact_name === '合成测试联系人');
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function paint(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
test('next action accepts sequential typing and keeps the visible text equal to its saved draft', () => fixture(async page => {
  const input = page.locator('textarea[data-key="next_action"]');
  await input.fill(''); await paint(page);
  await input.evaluate(el => {window.visitInputNode = el;});
  await input.pressSequentially('123', {delay: 60}); await paint(page);
  const actual = await page.evaluate(() => ({visible: document.querySelector('textarea[data-key="next_action"]').value,
    value: SalesRuntime.current.data.values.next_action,
    draft: SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey).values.next_action,
    focusedSameNode: document.activeElement === window.visitInputNode, busy: SalesRuntime.current.data.busy}));
  assert.deepEqual(actual, {visible: '123', value: '123', draft: '123', focusedSameNode: true, busy: false});
}));

test('all review text fields retain typing, Chinese composition, selection and restored drafts', () => fixture(async page => {
  const expected = {
    follow_up_record: '核对需求并确认交付范围', next_action: '2026年9月20日由我发送方案',
    customer_main_business: '企业软件服务', customer_needs: '减少人工整理工作', customer_budget: '已确认预算10万元',
  };
  for (const [key, text] of Object.entries(expected)) {
    const input = page.locator(`input[data-key="${key}"],textarea[data-key="${key}"]`);
    await input.fill(text); await paint(page); await input.pressSequentially('123', {delay: 35}); await paint(page);
    expected[key] = text + '123'; assert.equal(await input.inputValue(), expected[key], key);
  }
  const next = page.locator('textarea[data-key="next_action"]'); await next.fill(''); await next.focus();
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Input.imeSetComposition', {text: 'mingtian', selectionStart: 8, selectionEnd: 8}); await paint(page);
  assert.equal(await next.inputValue(), 'mingtian');
  await page.evaluate(() => SalesRuntime.current.setData({analysisPhrase: '无关状态更新'})); await paint(page);
  assert.equal(await next.inputValue(), 'mingtian', 'a render must not reset an in-progress composition');
  await cdp.send('Input.insertText', {text: '明天'}); await paint(page); assert.equal(await next.inputValue(), '明天');
  await page.keyboard.insertText('发送方案'); await paint(page); expected.next_action = '明天发送方案';
  await next.evaluate(el => el.setSelectionRange(2, 2)); await page.keyboard.insertText('上午'); await paint(page);
  expected.next_action = '明天上午发送方案';
  assert.equal(await next.inputValue(), expected.next_action);
  assert.equal(await next.evaluate(el => el.selectionStart), 4, 'cursor stays after inserted text');
  await next.press('Backspace'); await paint(page); expected.next_action = '明天上发送方案';
  assert.equal(await next.inputValue(), expected.next_action);
  for (const [key, value] of Object.entries(expected)) assert.equal(await page.evaluate(key => SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey).values[key], key), value);
  await page.reload();
  await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/visit-confirm/index' && !!SalesRuntime.current.data.core.length);
  for (const [key, value] of Object.entries(expected)) {
    const input = page.locator(`input[data-key="${key}"],textarea[data-key="${key}"]`); await input.waitFor();
    assert.equal(await input.inputValue(), value, 'reload restores ' + key);
  }
}, true));

test('quality result is invalidated after editing a narrative field; re-review uses the new text', () => fixture(async page => {
  await page.locator('[data-handler="review"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'result' && SalesRuntime.current.data.canSubmit);
  const prior = await page.evaluate(() => SalesRuntime.current.data.reviewRunId);
  await page.locator('[data-handler="backToEdit"]').click();
  const value = '2026年9月20日由我整理验收方案并发送客户确认';
  await page.locator('textarea[data-key="next_action"]').fill(value); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.reviewStale), true);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.canSubmit), false);
  await page.locator('[data-handler="review"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'result' && SalesRuntime.current.data.canSubmit);
  const result = await page.evaluate(() => ({id: SalesRuntime.current.data.reviewRunId, value: SalesRuntime.current.data.reviewPayload.fields.next_action}));
  assert.notEqual(result.id, prior); assert.equal(result.value, value);
}));

test('quality waiting stays read-only, times out into editable draft and reuses the existing run for unchanged retry', () => fixture(async page => {
  const start = Date.now(); await page.clock.setFixedTime(new Date(start));
  await page.evaluate(() => {
    const original = SalesPreview.request; window.holdQuality = true; window.qualityReads = 0; window.qualitySubmits = 0;
    window.SalesPreview = {...SalesPreview, request(options) {
      const url = new URL(options.url, location.href);
      if (url.pathname === '/api/v1/visit-flow/quality') window.qualitySubmits++;
      if (window.holdQuality && url.pathname.startsWith('/api/v1/agent/runs/')) {
        const timer = setTimeout(() => {window.qualityReads++; options.success({statusCode: 200, data: {id: url.pathname.split('/').at(-1), status: 'running'}});}, 20);
        return {abort() {clearTimeout(timer);}};
      }
      return original(options);
    }};
  });
  const value = await page.locator('textarea[data-key="next_action"]').inputValue();
  await page.locator('[data-handler="review"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'analyzing' && window.qualityReads > 0);
  await page.locator('.analyzing-card').waitFor();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.busy), true);
  assert.equal(await page.locator('textarea[data-key="next_action"]').isVisible(), false);
  assert.equal(await page.locator('textarea[data-key="next_action"]').isDisabled(), true);
  assert.match(await page.locator('.analyzing-card').innerText(), /质检期间暂不可编辑/);
  assert.match(await page.locator('.web-analysis-preview').innerText(), /本次送检内容/);
  assert.equal(await page.locator('.web-analysis-field p').nth(1).innerText(), value, 'read-only context retains the submitted next action');
  assert.equal(await page.locator('.web-analysis-preview :is(input,textarea,button)').count(), 0, 'analysis context cannot mutate the draft');
  const run = await page.evaluate(() => SalesRuntime.current.data.pendingReviewId);
  await page.clock.setFixedTime(new Date(start + 121000));
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'edit' && !SalesRuntime.current.data.busy);
  const next = page.locator('textarea[data-key="next_action"]'); await next.waitFor();
  assert.equal(await next.isEnabled(), true); assert.equal(await next.inputValue(), value);
  assert.match(await page.evaluate(() => SalesRuntime.current.data.errorText), /超时/);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.pendingReviewId), run);
  assert.equal(await page.evaluate(() => SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey).values.next_action), value);
  await page.evaluate(() => {window.holdQuality = false;}); await page.locator('[data-handler="review"]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.flowStep === 'result' && SalesRuntime.current.data.canSubmit);
  assert.equal(await page.evaluate(() => window.qualitySubmits), 1, 'retry must not create a second quality run');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.reviewRunId), run);
}));
