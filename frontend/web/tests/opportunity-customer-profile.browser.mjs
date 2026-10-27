// Built router, shared headers and actual React detail. Synthetic preview data only.
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const staticRoot = fileURLToPath(new URL('../dist/', import.meta.url));
const base = 'https://opportunity-profile.test';
const pairs = [
  ['00000010-0000-4000-8000-000000000001', '00000011-0000-4000-8000-000000000001'],
  ['00000010-0000-4000-8000-000000000002', '00000011-0000-4000-8000-000000000005'],
];
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function open(page, [customer, opportunity] = pairs[0]) {
  await page.evaluate(({customer, opportunity}) => SalesRuntime.wx.navigateTo({url: `/pages/customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}&period=all&readonly=1`}), {customer, opportunity});
  await page.waitForFunction(id => SalesRuntime.current.route === 'pages/customer-assets/index' && SalesRuntime.current.data.opportunity?.id === id && !SalesRuntime.current.data.opportunityLoading, opportunity);
  await page.getByRole('region', {name: '商机详情', exact: true}).waitFor(); await paint(page);
}
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1366, height: 768}, serviceWorkers: 'block'});
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
  const page = await context.newPage(); page.setDefaultTimeout(7000); page.on('pageerror', e => errors.push(e.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      const original = SalesPreview;
      window.profileProbe = {calls: [], missingId: false};
      window.SalesPreview = {...original, request(options) {
        const pathname = new URL(options.url, location.href).pathname;
        profileProbe.calls.push(pathname);
        if (/\/opportunities\/[^/]+\/header$/.test(pathname) && profileProbe.missingId) {
          return original.request({...options, success(response) {
            for (const row of response.data.opportunities || []) delete row.customer_id;
            options.success?.(response);
          }});
        }
        return original.request(options);
      }};
    });
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } catch (error) {
    console.error('PROFILE_DIAGNOSTIC', await page.evaluate(() => ({route: SalesRuntime.current?.route, error: SalesRuntime.current?.data?.opportunityError, text: document.body.innerText.slice(-900)})));
    throw error;
  } finally {await context.close();}
}

test('customer profile opens the header-linked customer directly and returns to the original opportunity for two customers', () => fixture(async page => {
  for (const [customer, opportunity] of pairs) {
    await open(page, [customer, opportunity]);
    const original = await page.evaluate(() => ({id: SalesRuntime.current._id, name: SalesRuntime.current.data.opportunity.customerName}));
    await page.evaluate(() => {profileProbe.calls = [];});
    await page.getByRole('button', {name: '客户档案', exact: true}).click();
    await page.waitForFunction(id => SalesRuntime.current.route === 'pages/customer-detail/index' && SalesRuntime.current.data.customer?.id === id, customer);
    const detail = page.getByRole('region', {name: '客户详情', exact: true});
    await detail.getByRole('heading', {name: original.name, exact: true}).waitFor();
    assert.equal(await page.evaluate(() => SalesRuntime.current.options.id), customer);
    assert.match(page.url(), new RegExp('/pages/customer-detail/index\\?id=' + customer));
    const calls = await page.evaluate(() => profileProbe.calls);
    assert.ok(calls.includes(`/api/v1/customers/${customer}/header`));
    assert.equal(calls.includes('/api/v1/customers'), false, 'no customer-name/list lookup');
    await page.getByRole('button', {name: '返回上一页', exact: true}).click();
    await page.waitForFunction(id => SalesRuntime.current.data.opportunity?.id === id && !SalesRuntime.current.data.opportunityLoading, opportunity);
    assert.equal(await page.evaluate(() => SalesRuntime.current._id), original.id);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), customer);
    await page.getByRole('region', {name: '商机详情', exact: true}).waitFor();
  }
}));

test('missing header customer ID explicitly disables profile instead of falling back to a list or route customer', () => fixture(async page => {
  await page.evaluate(() => {profileProbe.missingId = true;});
  await open(page);
  assert.equal(await page.getByRole('button', {name: '客户档案', exact: true}).isDisabled(), true);
  assert.equal(await page.getByText('关联客户信息缺失，暂无法打开档案。', {exact: true}).count(), 1);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), pairs[0][0]);
}));

test('customer all-actuals retains its existing same-customer behavior', () => fixture(async page => {
  await open(page);
  await page.getByRole('button', {name: '客户全部实绩', exact: true}).click();
  await page.waitForFunction(() => SalesRuntime.current.data.opportunityId === '' && !SalesRuntime.current.data.loading);
  assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-assets/index');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), pairs[0][0]);
  await page.getByRole('region', {name: '商机详情', exact: true}).waitFor({state: 'detached'});
}));
