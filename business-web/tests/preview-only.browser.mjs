/** The isolated review server never offers or starts enterprise authentication. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;

before(async () => {
  server = createSalesWebServer({target: '', localLogin: null, previewOnly: true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});

after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});
});

async function fixture(width, run) {
  const context = await browser.newContext({viewport: {width, height: width < 900 ? 844 : 1000}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], blocked = [], capabilities = [];
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (new URL(response.url()).pathname === '/web-capabilities') {
      // Read immediately; Chrome discards the old response body after navigation.
      capabilities.push(response.json().then(data => ({data}), error => ({error: error.message})));
    }
  });
  // Serve capabilities and all assets from the actual server. Any enterprise
  // request is an error, including session restoration and local quick login.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)/.test(url.pathname) || url.pathname === '/local-login') {
      blocked.push(url.pathname);
      return route.abort();
    }
    return route.continue();
  });
  try {
    await run(page);
    assert.ok(capabilities.length > 0, 'entry must read the actual server capabilities');
    for (const response of await Promise.all(capabilities)) {
      assert.equal(response.error, undefined);
      assert.equal(response.data.previewOnly, true);
    }
    assert.deepEqual(blocked, [], 'isolated preview must not start any enterprise request');
    assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => window.SalesRuntime?.errors || []), []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'viewport must not overflow horizontally');
  } finally {
    await context.close();
  }
}

async function assertEnterpriseEntriesDisabled(page) {
  for (const selector of ['#enter-live', '#choose-live', '#preview-enter-live']) {
    assert.equal(await page.locator(selector).count(), 1);
    assert.equal(await page.locator(selector).isVisible(), false, `${selector} must be hidden`);
    assert.equal(await page.locator(selector).isDisabled(), true, `${selector} must be disabled`);
  }
}

async function waitForPreview(page, role = 'sales') {
  await page.waitForFunction(expectedRole => window.SALES_MODE === 'preview' &&
    window.SalesRuntime?.current?.route === 'pages/index/index' &&
    SalesRuntime.app.globalData.session?.role === expectedRole && !SalesRuntime.app._capabilityFlight, role);
  await assertEnterpriseEntriesDisabled(page);
}

for (const width of [1440, 390]) {
  for (const path of ['/?mode=live#/pages/login/index', '/#/pages/customer-detail/index?customerId=SYNTHETIC_DEEP_LINK']) {
    test(`${width}px preview-only server stops enterprise entry before runtime boot: ${path}`, {timeout: 30000}, () => fixture(width, async page => {
      await page.goto(base + path);
      const card = page.locator('.web-entry-card');
      await card.getByRole('heading', {name: '销售工作区', exact: true}).waitFor();
      assert.equal(await page.evaluate(() => Boolean(window.SalesRuntime)), false, 'enterprise runtime must not boot');
      assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isVisible(), false);
      assert.equal(await page.getByPlaceholder('请输入密码', {exact: true}).isVisible(), false);
      const link = card.getByRole('link', {name: '进入示例体验 →', exact: true});
      const target = new URL(await link.getAttribute('href'), page.url());
      assert.equal(target.origin, base);
      assert.equal(target.searchParams.get('mode'), 'preview');
      assert.equal(target.hash, '#/pages/index/index', 'enterprise object identifiers must not carry into preview');
      const box = await link.boundingBox();
      assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= width && box.y + box.height <= page.viewportSize().height);
      await link.click();
      await waitForPreview(page);
    }));
  }

  test(`${width}px preview-only logout and role re-entry stay password-free after refresh`, {timeout: 45000}, () => fixture(width, async page => {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await waitForPreview(page);
    await page.locator(width < 900 ? '#mobile-account-button' : '#account-button').click();
    await page.locator('#account-logout').click();
    await page.locator('.wx-modal-mask').getByRole('button', {name: '退出示例', exact: true}).click();
    await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/login/index' && !SalesRuntime.app.globalData.session);
    await page.reload();
    await page.locator('#preview-entry-choice').waitFor();
    assert.equal(await page.evaluate(() => SalesRuntime.app.globalData.session), null, 'refresh must preserve explicit logout');
    await assertEnterpriseEntriesDisabled(page);
    assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isVisible(), false);
    assert.equal(await page.getByPlaceholder('请输入密码', {exact: true}).isVisible(), false);
    await page.locator('#preview-entry-role').selectOption('fde');
    await page.locator('#preview-enter').click();
    await waitForPreview(page, 'fde');
    assert.equal(await page.locator('#preview-role').inputValue(), 'fde');
    assert.equal(new URL(page.url()).searchParams.get('mode'), 'preview');
  }));
}
