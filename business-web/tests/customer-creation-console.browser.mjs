/** Isolated console interaction only. Real SQL/RLS is verified by the Python S2 suite. */
import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {readFile, mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = new URL('../../backend/src/sales_backend/web/', import.meta.url);
let server, browser, base;
before(async () => {
  server = createServer(async (req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname;
    const relative = path === '/admin' ? 'admin.html' : path.startsWith('/admin/assets/') ? path.slice(7) : null;
    if (!relative || relative.includes('..')) { res.writeHead(404).end(); return; }
    try {
      const content = await readFile(new URL(relative, root));
      res.setHeader('Content-Type', relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : relative.endsWith('.svg') ? 'image/svg+xml' : relative.endsWith('.png') ? 'image/png' : 'text/html');
      res.end(content);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => { await browser?.close(); server?.closeAllConnections(); await new Promise(r => server.close(r)); });

async function fixture(run, {width = 1440, review = true} = {}) {
  const context = await browser.newContext({viewport: {width, height: 900}, serviceWorkers: 'block'});
  const page = await context.newPage();
  const requests = [], errors = [], unexpected = [];
  let failNext = false;
  const record = {id: '11111111-1111-4111-8111-111111111111', status: 'pending', applicant_name: '测试销售',
    requested_at: '2026-10-15T02:00:00Z', decision_reason: '', payload: {
      name: '<img src=x onerror=alert(1)>隔离验收客户', company_reference: 'S2-UI-001', industry: '软件',
      target_team: '北区', customer_type: '潜在客户', level_code: 'Tier-2', source: '销售自拓',
      contact_name: '测试联系人', contact_title: '采购经理', contact_role: '决策者',
    }};
  const actor = {workspace_id: 'isolated-company', user_id: 'isolated-operator', role: 'operations', display_name: '隔离运营'};
  page.setDefaultTimeout(8000);
  page.on('pageerror', e => errors.push(e.message));
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base) { unexpected.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    if (url.pathname.endsWith('/auth/refresh')) return route.fulfill({json: {access_token: 'isolated-token', actor}});
    if (url.pathname.endsWith('/auth/me')) return route.fulfill({json: {actor}});
    if (url.pathname.endsWith('/permissions/me')) return route.fulfill({json: {permissions: {
      'access.console': true, 'customer.read': true, 'customer.create_review': review,
    }}});
    if (url.pathname.endsWith('/companies')) return route.fulfill({json: {home_company_id: actor.workspace_id, items: [{id: actor.workspace_id, name: '隔离验收公司'}]}});
    if (url.pathname.endsWith('/customer-creation-requests')) {
      requests.push({method: 'GET', status: url.searchParams.get('status')});
      const status = url.searchParams.get('status');
      const items = status === 'all' || status === record.status ? [record] : [];
      return route.fulfill({json: {items, total: items.length, offset: 0, limit: 50, has_more: false}});
    }
    if (url.pathname.endsWith('/decision')) {
      requests.push({method: request.method(), body: request.postDataJSON(), key: request.headers()['idempotency-key']});
      if (failNext) { failNext = false; return route.fulfill({status: 503, json: {detail: '隔离故障，请重试'}}); }
      const data = request.postDataJSON(); record.status = data.decision; record.decision_reason = data.reason;
      return route.fulfill({json: record});
    }
    if (url.pathname.endsWith('/customers')) return route.fulfill({json: {items: [], total: 0}});
    if (url.pathname.endsWith('/summary')) return route.fulfill({json: {}});
    unexpected.push(url.pathname); return route.fulfill({status: 403, json: {detail: '隔离测试未开放'}});
  });
  try {
    await page.goto(base + '/admin#creation_requests');
    if (review) await page.getByRole('heading', {name: '客户建档审批', exact: true}).waitFor();
    else await page.locator('.shell').waitFor();
    await run({page, requests, record, fail: () => { failNext = true; }});
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpected, []);
  } finally { await context.close(); }
}

test('approval creates pending-pool feedback, safe details and all-status history', {timeout: 30000}, () => fixture(async ({page, record, requests}) => {
  assert.equal(await page.locator('#content img').count(), 0);
  await page.getByRole('button', {name: '查看申请', exact: true}).click();
  await page.getByRole('heading', {name: '客户建档申请详情'}).waitFor();
  assert((await page.locator('#dialog').innerText()).includes('S2-UI-001'));
  await page.getByRole('button', {name: '关闭', exact: true}).last().click();
  await page.getByRole('button', {name: '审核', exact: true}).click();
  await page.getByRole('button', {name: '确认处理'}).click();
  await page.getByText('已通过，客户已进入待认领客户池', {exact: true}).waitFor();
  assert.equal(record.status, 'approved');
  await page.locator('select[name=status]').selectOption('all');
  await page.getByRole('button', {name: '查询', exact: true}).click();
  await page.locator('tbody .badge').filter({hasText: '已通过'}).waitFor();
  assert.equal(await page.getByRole('button', {name: '审核', exact: true}).count(), 0);
  assert.equal(requests.filter(r => r.method === 'POST').length, 1);
  assert(requests.some(r => r.status === 'all'));
}));

test('rejection validates reason and failed retry keeps content and idempotency key', {timeout: 30000}, () => fixture(async ({page, requests, fail, record}) => {
  await page.getByRole('button', {name: '审核', exact: true}).click();
  await page.locator('#dialog select[name=decision]').selectOption('rejected');
  await page.getByRole('button', {name: '确认处理'}).click();
  await page.getByText('请填写驳回原因', {exact: true}).waitFor();
  assert.equal(requests.filter(r => r.method === 'POST').length, 0);
  await page.locator('#dialog textarea[name=reason]').fill('公司编号需要重新核对');
  fail(); await page.getByRole('button', {name: '确认处理'}).click();
  await page.getByText('隔离故障，请重试', {exact: true}).waitFor();
  assert.equal(await page.locator('#dialog textarea[name=reason]').inputValue(), '公司编号需要重新核对');
  await page.getByRole('button', {name: '确认处理'}).click();
  await page.getByText('已驳回客户建档申请', {exact: true}).waitFor();
  const posts = requests.filter(r => r.method === 'POST');
  assert.equal(posts.length, 2); assert(posts[0].key); assert.equal(posts[0].key, posts[1].key);
  assert.equal(record.status, 'rejected');
}));

for (const width of [1440, 390]) test(`console queue and decision fit ${width}px`, {timeout: 30000}, () => fixture(async ({page}) => {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.getByRole('button', {name: '审核', exact: true}).click();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await mkdir('/tmp/shenma-s2-ui', {recursive: true});
  await page.screenshot({path: `/tmp/shenma-s2-ui/console-${width}.png`, fullPage: true});
}, {width}));

test('console without review grant hides navigation and ignores forged hash', {timeout: 30000}, () => fixture(async ({page, requests}) => {
  assert.equal(await page.locator('[data-nav=creation_requests]').count(), 0);
  assert.equal(requests.length, 0);
  assert.equal(await page.getByRole('heading', {name: '客户建档审批', exact: true}).count(), 0);
}, {review: false}));
