/**
 * Real browser checks for the Mini Program compatibility host.
 * Run: PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test tests/runtime.browser.mjs
 * Start the local Web server first. Every account and response used here is synthetic.
 */
import assert from 'node:assert/strict';
import {test} from 'node:test';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const APP_URL = process.env.APP_URL || 'http://127.0.0.1:5186';
const address = new URL(APP_URL);
if (!['localhost', '127.0.0.1', '[::1]'].includes(address.hostname)) throw new Error('Runtime browser checks only run against a local server.');

async function previewBrowser(run) {
  const browser = await chromium.launch({channel: process.env.CHROME_CHANNEL || 'chrome', headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const pageErrors = [], forbiddenRequests = [];
  try {
    await context.addInitScript(() => {
      sessionStorage.setItem('sales-web:preview-role', 'sales');
    });
    await context.route('**/*', route => {
      const target = new URL(route.request().url());
      if (target.origin !== address.origin || target.pathname.startsWith('/api/') || target.pathname === '/local-login') { forbiddenRequests.push(target.pathname); return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(APP_URL + '/?mode=preview', {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && window.SalesRuntime.current.data.messages?.length > 0);
    await run(page);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.deepEqual(forbiddenRequests, []);
  } finally { await context.close(); await browser.close(); }
}

test('source logout confirmation keeps keyboard focus inside the modal and restores its trigger', {timeout: 45000}, async () => previewBrowser(async page => {
  await page.locator('#account-button').click();
  await page.locator('#account-profile').click();
  const trigger = page.locator('button[data-handler="logout"]');
  await trigger.click();
  await page.locator('.wx-modal-mask').waitFor();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(() => !!document.activeElement?.closest('.wx-modal-mask')), true, 'Tab must not reach background navigation while confirmation is open');
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.evaluate(() => !!document.activeElement?.closest('.wx-modal-mask')), true);
  await page.keyboard.press('Escape');
  await page.locator('.wx-modal-mask').waitFor({state: 'detached'});
  await page.waitForFunction(() => document.activeElement?.dataset.handler === 'logout');
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.handler), 'logout');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.logoutBusy), false);
}));

test('saving customer edits then changing tabs cannot let the old completion navigate the new page', {timeout: 45000}, async () => previewBrowser(async page => {
  await page.locator('#desktop-nav [data-path="pages/customers/index"]').click();
  await page.locator('.customer-card[data-handler="openCustomer"]').first().click();
  await page.locator('[data-handler="editCustomer"]').click();
  const title = page.locator('input[data-key="contact_title"]');
  await title.waitFor();
  await title.fill('运行时回归测试岗位');
  await page.locator('button[data-handler="submit"]').click();
  await page.getByText('客户信息已更新', {exact: true}).waitFor();
  await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
  // The original source schedules navigateBack 700 ms after a successful save.
  await page.waitForTimeout(950);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/workbench/index');
  await page.locator('#desktop-nav [data-path="pages/customers/index"]').click();
  await page.locator('[data-handler="editCustomer"]').click();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading);
  assert.equal(await page.locator('input[data-key="contact_title"]').inputValue(), '运行时回归测试岗位');
}));

test('source customer field editor honors focus and retains Chinese text until saved to the draft', {timeout: 45000}, async () => previewBrowser(async page => {
  await page.goto(APP_URL + '/?mode=preview#/pages/customer-create/index', {waitUntil: 'domcontentloaded'});
  await page.locator('[data-handler="openEditor"]').filter({hasText: '客户名称'}).click();
  await page.locator('.editor-control input').waitFor();
  await page.waitForTimeout(50);
  assert.equal(await page.evaluate(() => document.activeElement?.matches('.editor-control input')), true, 'The source focus attribute should focus the opened editor');
  await page.keyboard.insertText('星河测试研发中心');
  await page.locator('[data-handler="saveEditor"]').click();
  await page.locator('.editor-layer').waitFor({state: 'detached'});
  assert.match(await page.locator('[data-handler="openEditor"]').filter({hasText: '客户名称'}).innerText(), /星河测试研发中心/);
}));

test('source visit entry preserves checkbox values, catches help taps, and respects Chinese composition', {timeout: 45000}, async () => previewBrowser(async page => {
  await page.goto(APP_URL + '/?mode=preview#/pages/visit-entry/index', {waitUntil: 'domcontentloaded'});
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/visit-entry/index' && !SalesRuntime.current.data.searching);
  const query = page.getByPlaceholder('输入客户名称关键词', {exact: true});
  await query.waitFor();
  // Observe the existing handler without replacing its business behavior.
  await page.evaluate(() => {
    const owner = SalesRuntime.current, search = owner.searchDepartmentCustomers;
    owner.__confirmCalls = 0;
    owner.searchDepartmentCustomers = function (...args) { this.__confirmCalls += 1; return search.apply(this, args); };
  });
  await query.dispatchEvent('keydown', {key: 'Enter', code: 'Enter', isComposing: true});
  assert.equal(await page.evaluate(() => SalesRuntime.current.__confirmCalls), 0, 'Confirming an IME candidate must not submit the customer search');
  await query.press('Enter');
  await page.waitForFunction(() => SalesRuntime.current.__confirmCalls === 1 && !SalesRuntime.current.data.searching);
  await page.locator('.customer-result[data-handler="chooseCustomer"]').first().click();
  const firstVisit = page.locator('input[type="checkbox"][value="first"]');
  await firstVisit.check();
  await page.waitForFunction(() => SalesRuntime.current.data.isFirstVisit === true);
  const transcript = page.locator('textarea.note-input');
  await transcript.fill('首次拜访已确认客户目标，下一步约产品演示。');
  await page.waitForFunction(() => !document.querySelector('button[data-handler="submitTranscript"]').disabled);
  assert.equal(await firstVisit.isChecked(), true, 'Unrelated input rendering must preserve the selected checkbox');
  assert.equal(await transcript.inputValue(), '首次拜访已确认客户目标，下一步约产品演示。');
  assert.equal(await page.locator('button[data-handler="submitTranscript"]').isEnabled(), true);
  await page.getByRole('button', {name: '查看语音录入说明', exact: true}).click();
  await page.locator('.input-help-popover').waitFor();
  await page.locator('.input-help-popover .help-row').click();
  assert.equal(await page.locator('.input-help-popover').isVisible(), true, 'catchtap must prevent the page from immediately dismissing its own help');
  await page.locator('.entry-hero').click();
  await page.locator('.input-help-popover').waitFor({state: 'detached'});
  await page.locator('button[data-handler="switchInputMode"][data-mode="file"]').click();
  await page.locator('button[data-handler="switchInputMode"][data-mode="file"][aria-selected="true"]').waitFor();
  assert.equal(await page.locator('button[data-handler="switchInputMode"][data-mode="file"]').getAttribute('aria-selected'), 'true');
  await page.getByRole('button', {name: '查看文件录入说明', exact: true}).click();
  await page.getByText('文档文件', {exact: true}).waitFor();
  await page.getByRole('button', {name: '关闭说明', exact: true}).click();
  await page.locator('.input-help-popover').waitFor({state: 'detached'});
  await firstVisit.uncheck();
  await page.waitForFunction(() => SalesRuntime.current.data.isFirstVisit === false);
}));

test('source pages preserve tab state, component events, input, navigation, and login boundaries', {timeout: 90000}, async () => {
  const browser = await chromium.launch({channel: process.env.CHROME_CHANNEL || 'chrome', headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
  const externalRequests = [], networkApiRequests = [], pageErrors = [];
  try {
    await context.addInitScript(() => {
      sessionStorage.setItem('sales-web:preview-role', 'sales');
    });
    // The preview adapter must satisfy requests in memory. Never contact a real backend.
    await context.route('**/*', async route => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.origin !== address.origin) { externalRequests.push(requestUrl.origin); return route.abort(); }
      if (requestUrl.pathname.startsWith('/api/') || requestUrl.pathname === '/local-login') { networkApiRequests.push(requestUrl.pathname); return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(APP_URL + '/?mode=preview', {waitUntil: 'domcontentloaded'});
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && window.SalesRuntime.current.data.messages?.length > 0);
    const initial = await page.evaluate(() => {
      const home = SalesRuntime.current;
      // Observe the original onShow read instead of racing its display/task promises.
      const syncRole = home.syncRole;
      home.syncRole = function (...args) {
        const pending = syncRole.apply(this, args);
        window.__runtimeTestHomeReady = Promise.resolve(pending);
        return pending;
      };
      return {id: home._id, messages: home.data.messages.map(item => item.id), businessMessages: home.data.messages.filter(item => item.kind !== 'greeting').map(item => item.id).sort()};
    });
    assert.ok(initial.messages.some(id => id.includes('greeting')));
    await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/workbench/index');
    const workbenchId = await page.evaluate(() => SalesRuntime.current._id);
    await page.locator('#desktop-nav [data-path="pages/index/index"]').click();
    await page.waitForFunction(id => SalesRuntime.current._id === id, initial.id);
    await page.evaluate(() => window.__runtimeTestHomeReady);
    assert.equal(await page.evaluate(() => SalesRuntime.current._id), initial.id);
    // The source treats greeting as once per login; cached business records must remain.
    assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages.filter(item => item.kind !== 'greeting').map(item => item.id).sort()), initial.businessMessages);
    assert.ok(await page.evaluate(() => SalesRuntime.current.data.messages.filter(item => item.kind === 'greeting').length <= 1), 'returning to a cached tab must not duplicate its welcome card');

    await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
    await page.waitForFunction(id => SalesRuntime.current._id === id, workbenchId);
    await page.locator('[data-handler="createOpportunity"]').click();
    await page.getByPlaceholder('搜索客户名称', {exact: true}).fill('星河');
    await page.locator('.customer-picker-card [data-handler="selectCustomer"]').filter({hasText: '星河'}).click();
    await page.locator('#opportunityForm').waitFor();
    const name = page.locator('#opportunityForm input[data-key="name"]');
    await name.fill('运行时组件校验草案');
    await name.press('End');
    await name.pressSequentially('A');
    await page.locator('#opportunityForm input[data-key="amount"]').fill('30');
    await page.locator('#opportunityForm picker select').first().selectOption({label: '意向沟通－10%'});
    await page.waitForFunction(() => SalesRuntime.current.selectComponent('#opportunityForm').data.form.stageIndex === 0);
    await page.locator('#opportunityForm picker select').first().selectOption({label: '商机确认－30%'});
    await page.waitForFunction(() => SalesRuntime.current.selectComponent('#opportunityForm').data.forecastRequired === true);
    assert.equal(await name.inputValue(), '运行时组件校验草案A');
    assert.match(await page.locator('#opportunityForm .forecast').innerText(), /30%及以上阶段/);
    await page.locator('#opportunityForm input[data-key="collection"]').fill('10');
    await page.waitForFunction(() => SalesRuntime.current.selectComponent('#opportunityForm').data.predictedCollection === '3');

    await page.locator('#opportunityForm fde-picker [data-handler="open"]').click();
    // The Web adapter renders the shared picker through its Tom Select portal.
    // Confirm the rendered member and verify its source component callback below.
    const fdeOption = page.locator('#web-select-dialog .option[data-selectable]').filter({has: page.getByText('示例FDE', {exact: true})});
    const fdeId = await fdeOption.getAttribute('data-value');
    assert.ok(fdeId, 'The rendered FDE option must retain its stable member ID');
    await fdeOption.click();
    await page.locator('#web-select-apply').click();
    await page.waitForFunction(() => SalesRuntime.current.selectComponent('#opportunityForm').data.form.fde_member_ids?.length === 1);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.current.selectComponent('#opportunityForm').data.form.fde_member_ids), [fdeId]);
    await page.locator('#opportunityForm fde-picker .chosen').getByText('示例FDE', {exact: true}).waitFor();
    assert.match(await page.locator('#opportunityForm fde-picker .chosen').innerText(), /示例FDE/);
    assert.equal(await name.inputValue(), '运行时组件校验草案A');

    await page.goBack();
    await page.waitForFunction(id => SalesRuntime.current.route === 'pages/workbench/index' && SalesRuntime.current._id === id, workbenchId);
    assert.equal(await page.locator('#opportunityForm').count(), 0);

    // After explicit logout, an old private route must not become the next account's deep link.
    await page.evaluate(() => { SalesRuntime.app.logout(); SalesRuntime.route('/pages/tasks/index?tab=pending'); });
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/login/index' && !SalesRuntime.app.globalData.session);
    await page.evaluate(async () => {
      await SalesRuntime.app.loginWithApi('sales', 'PREVIEW_SALES', 'preview');
      SalesRuntime.wx.switchTab({url: '/pages/index/index'});
    });
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/index/index');
    assert.equal(await page.evaluate(() => sessionStorage.getItem('sales-web:pending-path:preview')), null);

    // A fresh unauthenticated entry can still resume its intended deep link after login.
    await page.evaluate(() => {
      SalesRuntime.app.logout();
      sessionStorage.removeItem('sales-web:signed-out:preview');
      SalesRuntime.route('/pages/tasks/index?tab=pending');
    });
    await page.evaluate(async () => {
      await SalesRuntime.app.loginWithApi('sales', 'PREVIEW_SALES', 'preview');
      SalesRuntime.wx.switchTab({url: '/pages/index/index'});
    });
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && SalesRuntime.current.options.tab === 'pending');

    // A rejected actor refresh must clear the session and return to the original login page.
    await page.waitForFunction(() => !SalesRuntime.app._capabilityFlight);
    await page.evaluate(async () => {
      const original = SalesRuntime.wx.request;
      SalesRuntime.wx.request = options => {
        if (String(options.url).split('?')[0].endsWith('/auth/me')) {
          queueMicrotask(() => options.success?.({statusCode: 401, data: {message: 'Synthetic expired session'}}));
          return {abort() {}};
        }
        return original(options);
      };
      try { await SalesRuntime.app.refreshCapabilities(true); } catch (_) {}
      finally { SalesRuntime.wx.request = original; }
    });
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/login/index' && !SalesRuntime.app.globalData.session);
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.deepEqual(networkApiRequests, []);
    assert.deepEqual(externalRequests, []);
  } finally { await context.close(); await browser.close(); }
});
