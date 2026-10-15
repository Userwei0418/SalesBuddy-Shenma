import test, {before, after} from 'node:test';
import assert from 'node:assert/strict';
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
async function fixture(run, {width = 1366, role = 'sales'} = {}) {
  const context = await browser.newContext({viewport: {width, height: 768}, serviceWorkers: 'block'});
  const errors = [], blocked = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/index/index' && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      window.taskRequests = []; const original = SalesPreview.request;
      window.SalesPreview = {...SalesPreview, request(options) {
        const u = new URL(options.url, location.href);
        if (u.pathname === '/api/v1/tasks') taskRequests.push(Object.fromEntries(u.searchParams));
        return original(options);
      }};
    });
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function ready(page) {
  await page.waitForFunction(() => SalesRuntime.current?.route === 'pages/tasks/index' && !SalesRuntime.current.data.loading);
  await page.locator('.web-task-tabs').waitFor();
}
for (const overview of ['all_pending', 'today_pending', 'today_completed']) {
  test(`${overview}: home drill-down keeps initial scope and exposes all status filters`, () => fixture(async page => {
    await page.locator(`.web-home-metric[data-key="${overview}"]`).click(); await ready(page);
    const expected = overview === 'today_completed' ? 'completed' : 'pending';
    assert.equal(await page.locator('.web-task-tab').count(), 4);
    const request = await page.evaluate(() => taskRequests.at(-1));
    assert.equal(request.overview, overview); assert.equal(request.tab, expected);
    assert.equal(request.view, 'self'); assert.equal(request.offset, '0');
    await page.locator(`.web-task-tab[data-key="${expected}"][aria-pressed="true"]`).waitFor();
    if (overview === 'today_completed') {
      assert.ok(await page.locator('.web-task-row').count() > 0, 'known completed fixtures cannot be filtered out by pending');
      assert.ok((await page.locator('.web-task-action').allTextContents()).every(text => text.includes('查看结果')));
    }
    await page.locator('.web-task-controls select').click();
    await page.locator('#web-select-dialog .option[data-value="1"]').click();
    await page.waitForFunction(() => !SalesRuntime.current.data.loading && taskRequests.at(-1).order === 'due_desc');
    const historyLength = await page.evaluate(() => history.length);
    for (const tab of ['completed', 'rejected', 'all', 'pending']) {
      await page.locator(`.web-task-tab[data-key="${tab}"]`).click();
      await page.waitForFunction(tab => !SalesRuntime.current.data.loading && SalesRuntime.current.data.activeTab === tab, tab);
      const state = await page.evaluate(() => ({request: taskRequests.at(-1), data: SalesRuntime.current.data, options: SalesRuntime.current.options, url: SalesRuntime.current._url, hash: location.hash}));
      assert.equal(state.request.overview, undefined); assert.equal(state.data.overviewFilter, '');
      assert.equal(state.request.tab, tab); assert.equal(state.request.order, 'due_desc');
      assert.equal(state.options.overview, undefined); assert.equal(state.options.tab, tab);
      assert.equal(state.hash, '#' + state.url); assert.ok(!state.hash.includes('overview='));
      assert.equal(await page.locator('.web-task-overview-filter').count(), 0);
      if (tab === 'completed') assert.ok(await page.locator('.web-task-row').count() > 0);
    }
    assert.equal(await page.evaluate(() => history.length), historyLength, 'status selections replace the current entry instead of adding navigation levels');
    await page.locator('.web-task-action').first().click();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/task-detail/index');
    await page.goBack(); await ready(page);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.overviewFilter), '');
    assert.equal(await page.evaluate(() => SalesRuntime.current.taskParams().order), 'due_desc');
    await page.reload(); await ready(page);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.overviewFilter), '');
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.activeTab), 'pending');
    await page.goBack();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/index/index');
  }));
}
for (const width of [1366, 1024, 390]) {
  test(`${width}px: clear overview returns to regular status and all controls remain reachable`, () => fixture(async page => {
    await page.locator('.web-home-metric[data-key="all_pending"]').click(); await ready(page);
    const before = await page.evaluate(() => ({n: SalesRuntime.current.data.filteredTotal, pending: SalesRuntime.current.data.pendingCount}));
    assert.equal(before.n, before.pending);
    const clear = page.getByRole('button', {name: '清除总览筛选'}); await clear.click();
    await page.waitForFunction(() => !SalesRuntime.current.data.overviewFilter && !SalesRuntime.current.data.loading);
    assert.equal(await page.locator('.web-task-tab').count(), 4);
    await page.locator('.web-task-tab[data-key="all"]').click();
    await page.waitForFunction(() => SalesRuntime.current.data.activeTab === 'all' && !SalesRuntime.current.data.loading);
    await page.locator(width >= 1180 ? '.web-task-content' : width >= 601 ? '.web-task-results-scroll' : '#page-root').evaluate(e => {e.scrollTop = e.scrollHeight;});
    if (width < 601) await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForFunction(() => !SalesRuntime.current.data.hasMore && !SalesRuntime.current.data.loadingMore);
    assert.equal(await page.locator('.web-task-row').count(), 22);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  }, {width}));
}
test('FDE team and opportunity source constraints survive exiting the overview', () => fixture(async page => {
  await page.evaluate(() => SalesRuntime.route('/pages/tasks/index?overview=all_pending&scope=team&opportunity=1&year=2026&quarters=3', {replace: true}));
  await ready(page);
  await page.locator('.web-task-tab[data-key="completed"]').click();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading && !SalesRuntime.current.data.overviewFilter);
  const state = await page.evaluate(() => ({request: taskRequests.at(-1), url: SalesRuntime.current._url}));
  assert.equal(state.request.view, 'team'); assert.equal(state.request.opportunity_only, 'true');
  assert.equal(state.request.completed_year, '2026'); assert.equal(state.request.completed_quarters, '3');
  assert.ok(state.url.includes('scope=team') && state.url.includes('opportunity=1') && state.url.includes('year=2026') && state.url.includes('quarters=3'));
  await page.reload(); await ready(page);
  assert.equal(await page.evaluate(() => SalesRuntime.current.taskParams().view), 'team');
}, {role: 'fde_lead'}));
