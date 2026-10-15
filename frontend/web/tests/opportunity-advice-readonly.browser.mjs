// Actual built Web runtime, imported Mini handlers and React detail together.
// All resources/transport are fulfilled in memory; no service is started.
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = fileURLToPath(new URL('../', import.meta.url));
const staticRoot = path.resolve(root, process.env.SALES_WEB_DIST_DIR || 'dist');
const base = 'https://advice-runtime.test';
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
let browser;
before(async () => {browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close();});
const paint = page => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
async function open(page, readonly) {
  await page.evaluate(({customer, opportunity, readonly}) => SalesRuntime.wx.navigateTo({url: `/pages/customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}&period=all&readonly=${readonly ? 1 : 0}`}), {customer, opportunity, readonly});
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-assets/index' && SalesRuntime.current.data.opportunity && !SalesRuntime.current.data.opportunityLoading);
  await paint(page);
}
async function fixture(run) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'}), errors = [], unexpected = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/')) {unexpected.push(url.pathname); return route.abort();}
    if (url.pathname === '/web-capabilities') return route.fulfill({json: {previewOnly: true}});
    const file = path.resolve(staticRoot, url.pathname === '/' ? 'index.html' : decodeURIComponent(url.pathname).slice(1));
    if (!file.startsWith(staticRoot + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
    const contentType = ({'.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png'})[path.extname(file)] || 'application/octet-stream';
    return route.fulfill({body: fs.readFileSync(file), contentType});
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight && SalesRuntime.current?.data?.initializedRole);
    await page.evaluate(({customer, opportunity}) => {
      const original = SalesPreview;
      window.adviceProbe = {calls: [], denied: false, status: 'none', row: {id: 'synthetic-advice', subject_kind: 'opportunity', subject_id: opportunity, opportunity_id: opportunity, customer_id: customer, section: 'overview', status: 'queued', summary: '合成已生成建议', suggestions: [], stale: false}};
      window.SalesPreview = {...original, request(options = {}) {
        const url = new URL(options.url, location.href), pathname = url.pathname, method = (options.method || 'GET').toUpperCase();
        adviceProbe.calls.push({path: pathname, method});
        if (pathname === '/api/v1/advice' || pathname === '/api/v1/advice/latest' || pathname === '/api/v1/advice/synthetic-advice') {
          if(method === 'POST') adviceProbe.status = 'queued';
          const row = {...adviceProbe.row, section: url.searchParams.get('section') || options.data?.section || 'overview', status: adviceProbe.status};
          const data = pathname.endsWith('/latest') ? {state:adviceProbe.status,analysis:adviceProbe.status === 'none' ? null : row} : row;
          const response = adviceProbe.denied ? {statusCode: 403, data: {detail: '合成建议已无权读取'}} : {statusCode: 200, data};
          queueMicrotask(() => {options.success?.(response); options.complete?.(response);}); return {abort() {}};
        }
        return original.request(options);
      }};
    }, {customer, opportunity});
    await run(page);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []);
  } catch(error) {console.error(JSON.stringify(await page.evaluate(()=>({route:SalesRuntime.current?.route,error:SalesRuntime.current?.data?.opportunityError,body:document.body.innerText.slice(-1500),errors:SalesRuntime.errors,calls:adviceProbe.calls}))).slice(0,10000));throw error;} finally {await context.close();}
}
const posts = page => page.evaluate(() => adviceProbe.calls.filter(row => row.path === '/api/v1/advice' && row.method === 'POST').length);

test('opening readonly detail, prefetching and switching all advice tabs performs zero advice POSTs', {timeout: 30000}, () => fixture(async page => {
  await open(page, true);
  for (const tab of ['tasks', 'visits', 'opportunity', 'overview']) {
    await page.evaluate(tab => SalesRuntime.current.selectOpportunityTab({currentTarget: {dataset: {tab}}}), tab);
    await paint(page);
  }
  assert.equal(await posts(page), 0);
  assert.equal(await page.getByRole('button', {name: '生成建议', exact:true}).count(), 1);
  await page.evaluate(() => SalesRuntime.current.requestOpportunityAdvice({currentTarget: {dataset: {adviceAction: 'generate', opportunityId: SalesRuntime.current.data.opportunityId, section: 'overview'}}}));
  assert.equal(await posts(page), 1);
  assert.equal(await page.getByText('AI 生成中', {exact: true}).count(), 0);
}));

test('explicit generation creates once, GET refresh and readonly cache reentry never recreate, denial clears the result', {timeout: 30000}, () => fixture(async page => {
  await open(page, false); assert.equal(await posts(page), 0);
  await page.getByRole('button', {name: '生成建议', exact: true}).click();
  await page.waitForFunction(() => SalesRuntime.current.data.opportunityAdvice.overview?.status === 'queued');
  assert.equal(await posts(page), 1);
  await page.evaluate(() => {adviceProbe.status = 'succeeded';});
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click();
  await page.getByText('合成已生成建议', {exact: true}).waitFor();
  assert.equal(await posts(page), 1);
  await open(page, true);
  await page.getByText('合成已生成建议', {exact: true}).waitFor();
  assert.equal(await posts(page), 1);
  assert.equal(await page.getByRole('button', {name: '重新生成建议',exact:true}).count(), 1);
  await page.evaluate(() => {adviceProbe.denied = true;});
  await page.getByRole('button', {name: '刷新已有建议', exact: true}).click();
  await page.getByText('合成建议已无权读取', {exact: true}).waitFor();
  assert.equal(await page.getByText('合成已生成建议', {exact: true}).count(), 0);
  assert.equal(await posts(page), 1);
}));
