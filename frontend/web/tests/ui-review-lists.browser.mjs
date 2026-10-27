/** Review A1/A2/E2: unchanged native actions on an isolated synthetic backend. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function paint(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
async function fixture(route, size, run) {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'});
  const blocked = [], errors = [];
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'sales'));
  await context.route('**/*', request => {
    const url = new URL(request.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return request.abort();}
    return request.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`${base}/?mode=preview#/pages/${route}/index`);
    await page.waitForFunction(() => SalesRuntime.current && !SalesRuntime.app._capabilityFlight && !SalesRuntime.current.data.loading && !SalesRuntime.current.data.opportunityListLoading);
    await paint(page); await run(page);
    assert.deepEqual(blocked, []); assert.deepEqual(errors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}

for (const route of ['workbench', 'opportunities']) for (const size of [[1440, 1000], [1366, 600], [1180, 650], [1024, 600], [390, 844]]) {
  test(`${route} ${size.join('x')}: shared columns keep every business fact and no page overflow`, () => fixture(route, size, async page => {
    const rows = page.locator('.web-review-opportunity-row');
    await rows.first().waitFor(); await paint(page);
    const facts = await rows.evaluateAll(elements => elements.map(row => ({
      id: row.dataset.opportunityId,
      cells: [...row.children].filter(child => child.getAttribute('role') === 'cell').map(cell => cell.textContent.trim()),
      height: row.getBoundingClientRect().height,
      amountAlignment: getComputedStyle(row.querySelector('.web-review-op-amount')).textAlign,
      numeric: getComputedStyle(row.querySelector('.web-review-op-amount')).fontVariantNumeric,
    })));
    const source = await page.evaluate(route => (route === 'workbench' ? SalesRuntime.current.data.opportunityGroups : SalesRuntime.current.data.opportunityGroups).flatMap(group => group.items).map(item => ({id: item.id, name: item.name, customer: item.customer_name, team: item.team, owner: item.owner, recognized: item.recognizedLabel, collection: item.collectionLabel, close: item.closeLabel, product: item.productLineLabel, stage: item.stageName, status: item.signal.detail})), route);
    assert.equal(facts.length, source.length);
    facts.forEach((row, index) => {
      const item = source[index];
      assert.equal(row.id, item.id);
      assert.equal(row.cells.length, 7);
      for (const value of [item.name, item.customer, item.team, item.owner]) assert.ok(row.cells[0].includes(value));
      assert.ok(row.cells[1].includes(item.stage) && row.cells[1].includes(item.status));
      assert.deepEqual(row.cells.slice(2, 6), [item.recognized, item.collection, item.close, item.product]);
      if (size[0] >= 1366) assert.equal(row.amountAlignment, 'right');
      assert.equal(row.numeric, 'tabular-nums');
    });
    if (size[0] >= 1366) assert.ok(facts[0].height <= 108, `First row should be compact without suppressing facts: ${facts[0].height}`);
    const overflow = await page.evaluate(() => ({document: document.documentElement.scrollWidth > innerWidth + 1, root: document.querySelector('#page-root').scrollWidth > document.querySelector('#page-root').clientWidth + 1}));
    assert.deepEqual(overflow, {document: false, root: false});
    const cellOverflow = await rows.first().evaluate(row => [...row.children].some(cell => cell.getBoundingClientRect().right > row.getBoundingClientRect().right + 1));
    assert.equal(cellOverflow, false, 'No field or action may be clipped at the row edge');
    assert.equal(await page.locator('.web-review-opportunity-columns [role=columnheader]').count(), 7);
    if (size[0] === 1440) await page.screenshot({path: `/tmp/ui-review-${route}-1440.png`});
  }));
}

test('workbench keeps permission-guarded edit separate from row navigation; Enter opens the correct object', () => fixture('workbench', [1440, 1000], async page => {
  const edit = page.locator('.web-review-row-edit').first(); await edit.waitFor();
  const id = await edit.getAttribute('data-opportunity-id');
  const customer = await edit.getAttribute('data-customer-id');
  await edit.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/opportunity-create/index');
  assert.ok(page.url().includes(`opportunityId=${encodeURIComponent(id)}`));
  assert.ok(page.url().includes(`customerId=${encodeURIComponent(customer)}`));
  await page.goto(`${base}/?mode=preview#/pages/workbench/index`);
  const row = page.locator('.web-review-opportunity-row').first(); await row.waitFor();
  const rowId = await row.getAttribute('data-opportunity-id');
  await row.focus(); await page.keyboard.press('Enter');
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-assets/index');
  assert.ok(page.url().includes(`opportunity_id=${encodeURIComponent(rowId)}`));
}));

for (const route of ['workbench', 'opportunities']) test(`${route}: shared headings stay readable below variable-height sticky filters`, () => fixture(route, [1440, 1000], async page => {
  await page.locator('.web-review-opportunity-row').first().waitFor();
  const host = page.locator(route === 'workbench' ? '.web-opportunity-results' : '.web-collection-main');
  await host.evaluate(element => element.scrollTop = 500);
  await paint(page);
  const heading = await page.locator('.web-review-opportunity-columns').boundingBox();
  const tools = await page.locator(route === 'workbench' ? '.workbench-opportunity-tools' : '.filter-card').boundingBox();
  const bounds = await host.boundingBox();
  assert.ok(heading.y >= tools.y + tools.height - 1, JSON.stringify({heading, tools}));
  assert.ok(heading.y + heading.height < bounds.y + bounds.height, JSON.stringify({heading, bounds}));
  assert.equal(await page.locator('.web-review-opportunity-columns').evaluate(element => element.contains(document.elementFromPoint(element.getBoundingClientRect().x + 12, element.getBoundingClientRect().y + 12))), true);
}));

test('unavailable claim rows omit selection circles while retaining native explanatory action', () => fixture('customer-claim', [1440, 1000], async page => {
  await page.locator('.claim-card').first().waitFor();
  await page.evaluate(() => {
    const page = SalesRuntime.current;
    const customers = page.data.customers.map((row, index) => index < 2 ? {...row, claimEligible: index === 1, claimLabel: index === 1 ? '可申请认领' : '申请待审批', claim_status: index === 1 ? 'unclaimed' : 'pending'} : row);
    page.setData({customers});
  });
  await paint(page);
  const unavailable = page.locator('.claim-card').nth(0), available = page.locator('.claim-card').nth(1);
  assert.equal(await unavailable.locator('.claim-radio').count(), 0);
  assert.equal(await available.locator('.claim-radio').count(), 1);
  assert.match(await unavailable.getAttribute('aria-label'), /不可选择/);
  await available.click(); await paint(page);
  assert.notEqual(await page.evaluate(() => SalesRuntime.current.data.selectedCustomerId), '');
  await unavailable.click(); await paint(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedCustomerId), '');
  assert.equal(await page.locator('.claim-confirm').isDisabled(), true);
}));
