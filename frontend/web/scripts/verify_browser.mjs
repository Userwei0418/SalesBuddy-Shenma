import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { createSalesWebServer } from '../server.mjs';

const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = fileURLToPath(new URL('..', import.meta.url));
const out = join(root, '.runtime', 'browser-verification');
await mkdir(out, {recursive: true});
const browser = await chromium.launch({headless: true, channel: 'chrome'});
const checks = [], errors = [];
const base = process.env.APP_URL || 'http://127.0.0.1:5186';
let offlineServer;
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(base + '/?mode=preview');
  await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && SalesRuntime.current.data.messages?.length > 0);
  await page.screenshot({path: join(out, 'desktop-overview.png')});
  for (const [title, route] of [['作战地图', 'customers'], ['商机', 'workbench'], ['看板', 'bi'], ['我的', 'profile'], ['总览', 'index']]) {
    await page.locator(`#desktop-nav [data-path="pages/${route}/index"]`).click();
    await page.waitForFunction(id => SalesRuntime.current.route === `pages/${id}/index`, route);
    await page.waitForTimeout(500);
    const state = await page.evaluate(() => ({runtime: SalesRuntime.errors, overflow: document.documentElement.scrollWidth > innerWidth, error: SalesRuntime.current.data.loadError || SalesRuntime.current.data.error || ''}));
    assert.deepEqual(state.runtime, [], title); assert.equal(state.overflow, false, title); assert.equal(state.error, '', title);
    checks.push('desktop ' + title);
    await page.screenshot({path: join(out, `desktop-${route}.png`), fullPage: true});
  }
  assert.ok(await page.evaluate(() => SalesRuntime.current.data.messages.length > 0), 'tab return retains home messages');
  checks.push('home message state survives tab navigation');
  await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
  await page.locator('#page-root [data-handler="createOpportunity"]').click();
  await page.getByPlaceholder('搜索客户名称').fill('星河');
  await page.locator('[data-handler="selectCustomer"]').first().click();
  await page.getByPlaceholder('输入商机名称').fill('【示例】Web表单验收');
  await page.getByPlaceholder('请输入金额').fill('25');
  await page.locator('opportunity-form select').first().selectOption('1');
  await page.locator('opportunity-form input[type=date]').fill('2026-12-20');
  await page.getByText('确认保存商机', {exact: true}).click();
  await page.waitForTimeout(250);
  assert.match(await page.locator('#page-root').innerText(), /至少|季度.*(?:必填|填写)/);
  await page.locator('opportunity-form input[data-key=collection]').fill('5');
  await page.locator('opportunity-form input[data-key=recognized]').fill('10');
  await page.getByText('确认保存商机', {exact: true}).click();
  await page.waitForFunction(() => {
    const data = JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1') || '{}');
    return data.opportunities?.some(row => row.name === '【示例】Web表单验收');
  });
  const record = await page.evaluate(() => JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).opportunities.find(row => row.name === '【示例】Web表单验收'));
  assert.equal(record.amount, 250000); assert.equal(record.probability, 30);
  checks.push('native component form validates 30% quarterly plans, saves 25万元 as 250000元');
  await page.waitForTimeout(900);
  await page.evaluate(row => SalesRuntime.route(`/pages/customer-assets/index?customer_id=${row.customer_id}&opportunity_id=${row.id}&period=all&readonly=1`), record);
  await page.waitForTimeout(600);
  assert.ok((await page.locator('#page-root').innerText()).includes('【示例】Web表单验收'));
  checks.push('saved opportunity read back through original detail page');
  await page.screenshot({path: join(out, 'saved-opportunity.png'), fullPage: true});
  const mobile = await browser.newPage({viewport: {width: 390, height: 844}});
  await mobile.goto(base + '/?mode=preview');
  await mobile.waitForFunction(() => window.SalesRuntime?.current?.data.messages?.length > 0);
  for (const title of ['总览', '商机', '看板', '作战地图']) {
    await mobile.locator('#mobile-nav').getByText(title, {exact: true}).click();
    await mobile.waitForTimeout(450);
    assert.equal(await mobile.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'mobile overflow ' + title);
    assert.deepEqual(await mobile.evaluate(() => SalesRuntime.errors), []);
    checks.push('390px ' + title);
  }
  await mobile.screenshot({path: join(out, 'mobile-customers.png'), fullPage: true});
  // Login failure checks use an explicitly unconfigured local server, even when
  // the user's main workspace is connected to its real business backend.
  offlineServer = createSalesWebServer({target: ''});
  await new Promise(resolve => offlineServer.listen(0, '127.0.0.1', resolve));
  const offlineBase = 'http://127.0.0.1:' + offlineServer.address().port;
  const live = await browser.newPage({viewport: {width: 1440, height: 1000}});
  await live.goto(offlineBase + '/?mode=live');
  await live.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/login/index');
  assert.equal(await live.evaluate(() => SalesRuntime.app.globalData.session), null);
  assert.equal(await live.locator('#preview-notice').isVisible(), false);
  await live.getByPlaceholder('请输入工号或手机号').fill('CHECK_CONNECTION');
  await live.getByPlaceholder('请输入密码', {exact: true}).fill('connection-check');
  if (!await live.evaluate(() => SalesRuntime.current.data.agreed)) await live.locator('[data-handler=toggleAgreement]').click();
  await live.locator('[data-handler=submitLogin]').click();
  await live.waitForTimeout(350);
  assert.ok((await live.locator('body').innerText()).includes('尚未配置后端连接'));
  checks.push('live login isolated; unconfigured API reports an error and never enters preview');
  await live.screenshot({path: join(out, 'live-login.png')});
  await live.locator('#login-preview').click();
  await live.waitForFunction(() => window.SALES_MODE === 'preview' && window.SalesRuntime?.app?.globalData?.session?.role === 'sales');
  await live.locator('#enter-live').click();
  await live.waitForFunction(() => window.SALES_MODE === 'live' && window.SalesRuntime?.current?.route === 'pages/login/index');
  assert.equal(await live.evaluate(() => SalesRuntime.app.globalData.session), null);
  checks.push('workspace mode switches both directions despite an initial mode URL parameter');
  assert.deepEqual(errors, []);
  await writeFile(join(out, 'result.json'), JSON.stringify({verified_at: new Date().toISOString(), level: '代表性路径已验证', checks, errors, live_backend_verified: false}, null, 2));
  console.log(JSON.stringify({passed: checks.length, checks, errors}, null, 2));
} finally { await browser.close(); if (offlineServer) await new Promise(resolve => {offlineServer.close(resolve); offlineServer.closeAllConnections();}); }
