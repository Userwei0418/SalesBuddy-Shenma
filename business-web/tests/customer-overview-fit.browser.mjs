/** Customer layout only, exercised through shared Page handlers in isolated preview. */
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {before, after, test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const screenshots = new URL('../docs/desktop-forms-20260920/截图/', import.meta.url);
const customer = '00000010-0000-4000-8000-000000000001';
let server, browser, base;
before(async () => {
  await mkdir(screenshots, {recursive: true});
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function paint(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
async function fixture(run, size = [1366, 768], role = 'sales') {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'});
  const errors = [], blocked = [], page = await context.newPage();
  page.setDefaultTimeout(10000);
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function go(page, path) {await page.evaluate(path => SalesRuntime.wx.navigateTo({url: '/pages/' + path}), path); await paint(page);}
async function list(page) {
  await go(page, 'customers/index');
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customers/index' && !SalesRuntime.current.data.loading && !SalesRuntime.current.data.acvLoading && !SalesRuntime.current.data.assetLoading && SalesRuntime.current.data.customers.length > 0);
  await paint(page);
}
async function overlay(page) {
  await page.evaluate(id => SalesRuntime.current.showCustomerDetail(id), customer);
  await page.waitForFunction(() => SalesRuntime.current.data.selectedCustomer && SalesRuntime.current.data.detailSummary.loaded);
  await page.locator('.web-customer-workspace .detail-tabs [data-tab=overview]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.detailTab === 'overview' && SalesRuntime.current.data.detailPages.contacts?.loaded && SalesRuntime.current.data.detailPages.opportunities?.loaded);
  await paint(page);
}
async function standalone(page) {
  await go(page, 'customer-detail/index?id=' + customer);
  await page.waitForFunction(() => SalesRuntime.current.data.customer && SalesRuntime.current.data.detailSummary.loaded && SalesRuntime.current.data.detailPages.contacts?.loaded);
  await paint(page);
}
async function boxes(page, selector) {
  return page.locator(selector).evaluateAll(nodes => nodes.map(el => {
    const r = el.getBoundingClientRect();
    return {selector: el.className, x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom, client: el.clientHeight, scroll: el.scrollHeight, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, overflow: getComputedStyle(el).overflowY};
  }));
}
async function shown(page, selector) {
  const bounds = await boxes(page, selector), size = page.viewportSize();
  assert.ok(bounds.length, selector);
  for (const r of bounds) assert.ok(r.height > 0 && r.y >= 0 && r.bottom <= size.height - (size.width <= 900 ? 66 : 0) + 2 && r.x >= 0 && r.right <= size.width + 1, selector + JSON.stringify(r));
}
function detailHost(page) {
  const width = page.viewportSize().width;
  return page.locator(width >= 1180 ? '.web-customer-workspace .web-customer-primary' : width >= 768 ? '.web-customer-workspace .web-detail-main' : width > 600 ? '.web-customer-workspace .web-detail-columns' : '.web-customer-workspace .detail-scroll');
}
async function frame(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  const root = (await boxes(page, '#page-root'))[0];
  assert.ok(root.scroll <= root.client + 2, JSON.stringify(root));
}
async function shot(page, name) {await page.screenshot({path: fileURLToPath(new URL('客户图表联动-' + name + '.png', screenshots))});}

for (const size of [[1366, 768], [1024, 600], [768, 800], [630, 780]]) test(`${size.join('x')} customer list and both overview routes retain visible identity, totals and tabs`, () => fixture(async page => {
  await list(page); await frame(page);
  await shown(page, '.asset-page-heading,.asset-overview,.web-customer-list-controls');
  assert.equal(await page.locator('#customer-view-switch,[data-customer-view]').count(), 0, 'map/list are directly visible without obsolete view state');
  assert.equal(await page.locator('.battle-section').isVisible(), true);
  if (size[0] >= 1000) {
    await shown(page, '.battle-map,.quadrant-note,.web-customer-list-pane,.asset-list-heading');
    const [map, pane] = await boxes(page, '.battle-section,.web-customer-list-pane');
    assert.ok(pane.x >= map.right + 6 && Math.abs(pane.y - map.y) <= 2, JSON.stringify({map, pane}));
  } else {
    const [map, pane] = await boxes(page, '.battle-section,.web-customer-list-pane');
    assert.ok(pane.y >= map.bottom, 'mid-size layout presents map followed by the full list');
    assert.equal(await page.locator('.web-customer-results').evaluate(el => getComputedStyle(el).overflowY), 'auto');
  }
  if (size[0] === 1366) {await shot(page, '概览优化-客户列表-1366x768'); console.log('CUSTOMER_LIST_GEOMETRY', JSON.stringify(await boxes(page, '#page-root,.web-customer-list-scroll')));}
  await overlay(page); await frame(page);
  await shown(page, '.web-customer-workspace .web-detail-header,.web-customer-workspace .web-customer-kpis,.web-customer-workspace .detail-tabs');
  assert.equal(await page.locator('.web-customer-kpis>view').count(), 3);
  assert.equal(await page.locator('.web-customer-overview-panels>section').count(), 2);
  if (size[0] >= 1180) {
    for (const heading of await page.locator('.web-customer-overview-section>.detail-title').all()) {
      await heading.scrollIntoViewIfNeeded();
      const r = await heading.boundingBox();
      assert.ok(r && r.y >= 0 && r.y + r.height <= size[1], 'both original section headings remain reachable');
      await shown(page, '.web-customer-workspace .detail-tabs');
    }
    await detailHost(page).evaluate(el => el.scrollTop = 0);
  } else if (size[0] >= 768) await shown(page, '.web-customer-overview-section>.detail-title');
  const value = await page.evaluate(() => SalesRuntime.current.data.selectedCustomer.annualValue);
  assert.ok((await page.locator('.web-customer-kpis').innerText()).includes(value));
  if (size[0] === 1366) {await shot(page, '概览优化-客户详情-1366x768'); console.log('CUSTOMER_DETAIL_GEOMETRY', JSON.stringify(await boxes(page, '.detail-scroll,.web-detail-main,.web-detail-aside')));}
  for (const tab of ['visits', 'opportunity', 'tasks', 'overview']) {
    await detailHost(page).evaluate(el => el.scrollTop = el.scrollHeight);
    await page.locator(`.web-customer-workspace .detail-tabs [data-tab=${tab}]`).click(); await paint(page);
    assert.equal(await detailHost(page).evaluate(el => el.scrollTop), 0);
  }
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedCustomer.annualValue), value);
  await standalone(page); await frame(page);
  await shown(page, '.web-standalone-customer .web-detail-header,.web-standalone-customer .web-customer-kpis,.web-standalone-customer .detail-tabs');
  assert.equal(await page.locator('.web-customer-overview-panels>section').count(), 2);
  if (size[0] === 1366) await shot(page, '概览优化-独立客户详情-1366x768');
}, size));

test('long customer lists and overview evidence remain fully reachable inside their own panes', () => fixture(async page => {
  await list(page);
  await page.evaluate(() => {
    const p = SalesRuntime.current, original = p.data.customers;
    p.setData({customers: Array.from({length: 100}, (_, i) => ({...original[i % original.length], id: i ? 'synthetic-list-' + i : original[0].id, name: '【布局合成】客户 ' + (i + 1)}))});
  });
  await paint(page); await frame(page);
  assert.equal(await page.locator('.customer-list>.customer-card').count(), 100);
  const listBounds = (await boxes(page, '.web-customer-list-scroll'))[0];
  assert.ok(listBounds.scroll > listBounds.client * 3, JSON.stringify(listBounds));
  const beforeControls = (await boxes(page, '.web-customer-list-controls'))[0];
  const beforeMap = (await boxes(page, '.battle-map'))[0];
  await page.locator('.web-customer-list-scroll').evaluate(el => el.scrollTop = el.scrollHeight);
  await shown(page, '.customer-list>.customer-card:last-child');
  const afterControls = (await boxes(page, '.web-customer-list-controls'))[0];
  assert.ok(Math.abs(beforeControls.y - afterControls.y) <= 1, 'search and filters stay on the same content surface');
  const afterMap = (await boxes(page, '.battle-map'))[0];
  assert.deepEqual({x: afterMap.x, y: afterMap.y, width: afterMap.width, height: afterMap.height}, {x: beforeMap.x, y: beforeMap.y, width: beforeMap.width, height: beforeMap.height}, 'browsing a long list never scrolls the quadrant map away');
  await shown(page, '.battle-map');
  assert.equal(await page.locator('.web-customer-list-scroll').evaluate(el => getComputedStyle(el).overflowY), 'auto');
  assert.equal(await page.locator('.web-customer-list-scroll').getAttribute('data-web-page-scroll'), 'customers');
  assert.ok(await page.locator('.web-customer-content').evaluate(el => el.scrollHeight <= el.clientHeight + 2));
  assert.ok(['visible', 'hidden'].includes(await page.locator('.web-customer-results').evaluate(el => getComputedStyle(el).overflowY)));
  await overlay(page);
  await page.evaluate(() => {
    const p = SalesRuntime.current, c = p.data.selectedCustomer;
    p.setData({selectedCustomer: {...c, potentialEvidence: Array.from({length: 45}, (_, i) => '合成依据 ' + (i + 1)), nextAction: '合成行动完整保留。'.repeat(80)}});
  });
  await paint(page); await frame(page);
  assert.equal(await page.locator('.detail-evidence').first().locator('text').count(), 45);
  const content = await detailHost(page).evaluate(el => ({client: el.clientHeight, scroll: el.scrollHeight}));
  assert.ok(content.scroll > content.client * 1.5, JSON.stringify(content));
  assert.equal(await page.locator('.web-detail-aside').evaluate(el => getComputedStyle(el).overflowY), 'visible');
  assert.ok((await page.locator('.web-customer-next-action').innerText()).includes('合成行动完整保留。'.repeat(80)));
  const lastEvidence = page.locator('.detail-evidence').first().locator('text').last();
  await lastEvidence.scrollIntoViewIfNeeded();
  const evidenceBounds = await lastEvidence.boundingBox();
  assert.ok(evidenceBounds && evidenceBounds.y >= 0 && evidenceBounds.y + evidenceBounds.height <= page.viewportSize().height, 'last original evidence remains reachable');
  await shown(page, '.web-customer-workspace .detail-tabs');
  assert.ok(await detailHost(page).evaluate(el => el.scrollTop > 0));
  assert.equal(await page.locator('.web-detail-main').evaluate(el => el.scrollTop), 0);
}));

for (const role of ['sales', 'manager', 'fde_lead']) test(`${role} desktop customer metrics sit above simultaneous map/list without changing period or filter actions`, () => fixture(async page => {
  await list(page);
  const [summary, content] = await boxes(page, '.web-customer-summarybar,.web-customer-content');
  assert.ok(Math.abs(content.x - summary.x) <= 2 && content.y >= summary.bottom - 1, JSON.stringify({summary, content}));
  assert.ok(summary.width >= content.width - 2 && summary.height < 170, 'summary remains compact above the shared result workspace');
  const metrics = await boxes(page, '.asset-metric');
  assert.equal(metrics.length, 3);
  assert.ok(metrics.every(metric => Math.abs(metric.y - metrics[0].y) <= 2), 'three metrics share a horizontal row');
  await shown(page, '.battle-map,.web-customer-list-pane');
  await shown(page, '.asset-overview,.map-search,.operating-filters');
  const before = await page.evaluate(() => ({id: SalesRuntime.current._id, count: SalesRuntime.current.data.customers.length, total: SalesRuntime.current.data.acvText}));
  await page.locator('.asset-period [data-period=all]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.assetPeriod === 'all' && !SalesRuntime.current.data.assetLoading);
  assert.deepEqual(await page.evaluate(() => ({id: SalesRuntime.current._id, count: SalesRuntime.current.data.customers.length, total: SalesRuntime.current.data.acvText})), before);
  await page.locator('.asset-period [data-period=year]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.assetPeriod === 'year' && !SalesRuntime.current.data.assetLoading);
  assert.equal(await page.locator('[data-handler=openCustomerClaim]').count(), role === 'fde_lead' ? 0 : 1);
  if (role === 'manager') await shown(page, '.filter-team,.filter-member');
  if (role === 'fde_lead') await shown(page, '.fde-member-filter');
  await page.locator('.map-search input').fill('【无匹配布局检查】');
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length === 0);
  await page.locator('.map-search input').fill('');
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length > 0);
  await page.locator('.map-search input').blur();
  await paint(page); await frame(page);
  if (role === 'sales') await shot(page, '客户两栏-1366x768');
}, [1366, 768], role));

test('1180px short customer workspace keeps long asset values and the full map/list reachable', () => fixture(async page => {
  await list(page);
  await page.evaluate(() => SalesRuntime.current.setData({acvText:'9,876,543,210.88', recognizedText:'8,765,432,109.77', collectionText:'7,654,321,098.66'}));
  await paint(page); await frame(page);
  const summary = (await boxes(page, '.web-customer-summarybar'))[0];
  for (const metric of await boxes(page, '.asset-metric')) {
    assert.ok(metric.scrollWidth <= metric.clientWidth + 1 && metric.right <= summary.right, JSON.stringify(metric));
  }
  await shown(page, '.asset-acv-note,.battle-map,.quadrant-note');
  assert.equal(await page.locator('.battle-section').isVisible(), true);
  const count = await page.evaluate(() => SalesRuntime.current.data.customers.length);
  await page.locator('.map-zone.zone-attack').click({position:{x:10,y:10}});
  await page.waitForFunction(() => SalesRuntime.current.data.quadrantIndex > 0);
  await paint(page);
  assert.equal(await page.locator('.customer-list>.customer-card').count(), await page.evaluate(() => SalesRuntime.current.data.customers.length));
  await page.locator('.map-zone.zone-expanded').click({position:{x:10,y:10}});
  await page.waitForFunction(() => SalesRuntime.current.data.quadrantIndex === 0);
  await paint(page);
  assert.equal(await page.locator('.customer-list>.customer-card').count(), count);
  await page.locator('.web-customer-list-scroll').evaluate(el => el.scrollTop = el.scrollHeight);
  await shown(page, '.customer-list>.customer-card:last-child,.web-customer-list-controls');
  assert.ok(['visible', 'hidden'].includes(await page.locator('.battle-section').evaluate(el => getComputedStyle(el).overflowY)));
  await frame(page);
}, [1180, 650], 'manager'));

for (const width of [390, 320]) test(`${width}px customer routes keep readable one-column content and all tab actions`, () => fixture(async page => {
  await list(page); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('.battle-section').isVisible(), true);
  assert.equal(await page.locator('#customer-view-switch,[data-customer-view]').count(), 0);
  await overlay(page);
  await shown(page, '.web-customer-workspace .detail-tabs');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.locator('.web-customer-workspace .detail-tabs [data-tab=visits]').click();
  await page.waitForFunction(() => SalesRuntime.current.data.detailPages.visits?.loaded);
  await standalone(page);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('.web-customer-overview-panels>section').count(), 2);
  await page.locator('[data-handler=recordVisit]').click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/visit-entry/index' && SalesRuntime.current.data.customerConfirmed);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customerId), customer);
}, [width, 844]));

for (const width of [1366, 630]) test(`${width}px native related-opportunity focus scrolls the actual customer content pane`, () => fixture(async page => {
  await list(page); await overlay(page);
  await page.evaluate(() => {
    const p = SalesRuntime.current, seed = p._detailReader.state('opportunities').items[0], header = p._detailRaw;
    const rows = Array.from({length: 20}, (_, i) => ({...seed, id: 'synthetic-opportunity-' + (i + 1), name: '【合成定位】商机 ' + (i + 1)}));
    const original = SalesRuntime.wx.request;
    SalesRuntime.wx.request = options => {
      const path = new URL(options.url, location.origin).pathname;
      let data, delay;
      if (path.endsWith('/customers/' + header.id + '/opportunities')) {
        data = {items: rows, has_more: false, next_offset: null, total: 20}; delay = 10;
      } else if (path.endsWith('/opportunities/synthetic-opportunity-20/header')) {
        data = {...header, opportunities: [rows[19]]}; delay = 100;
      } else return original(options);
      const timer = setTimeout(() => {const result = {statusCode: 200, data: structuredClone(data), header: {}}; options.success?.(result); options.complete?.(result);}, delay);
      return {abort() {clearTimeout(timer);}};
    };
    p.openTaskOpportunity({currentTarget: {dataset: {id: 'synthetic-opportunity-20'}}});
  });
  await page.waitForFunction(() => SalesRuntime.current.data.detailScrollTarget === 'opportunity-card-19');
  await paint(page);
  await shown(page, '#opportunity-card-19 .detail-stage-name');
  assert.equal(await page.locator('#page-root').evaluate(el => el.scrollTop), 0);
  assert.equal(await page.locator('.detail-scroll').evaluate(el => el.scrollTop), 0);
  assert.ok(await detailHost(page).evaluate(el => el.scrollTop > 0));
}, [width, 780]));

async function linkedCustomerIds(page) {
  await paint(page);
  const state = await page.evaluate(() => ({list: SalesRuntime.current.data.customers.map(row => row.id), dots: SalesRuntime.current.data.plotCustomers.map(row => row.id)}));
  assert.deepEqual(state.dots, state.list, 'shared source filtering keeps the map and list in sync');
  assert.equal(await page.locator('.plot-hit-area').count(), state.list.length);
  assert.equal(await page.locator('.customer-list>.customer-card').count(), state.list.length);
  assert.equal((await page.locator('.map-result-count').innerText()).trim(), state.list.length + ' 家');
  return state.list;
}

test('shared search and quadrant interactions keep map/list linked without changing scope, ACV or asset period', () => fixture(async page => {
  await list(page);
  const before = await page.evaluate(() => {
    const p = SalesRuntime.current, d = p.data;
    return {pageId: p._id, scope: d.scope, team: d.selectedTeam, member: d.selectedMember, count: d.scopeCustomerCount, acv: d.acvText, period: d.assetPeriod, recognized: d.recognizedText, collection: d.collectionText, firstName: d.customers[0].name};
  });
  const totalIds = await linkedCustomerIds(page);
  await page.locator('.map-search input').fill(before.firstName);
  await page.waitForFunction(name => SalesRuntime.current.data.keyword === name, before.firstName);
  assert.ok((await linkedCustomerIds(page)).length > 0);
  assert.ok((await linkedCustomerIds(page)).length < totalIds.length, 'search narrows both surfaces');
  await page.locator('.map-search input').fill('【没有匹配的客户】');
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length === 0);
  await linkedCustomerIds(page);
  assert.equal(await page.locator('.map-empty').isVisible(), true);
  assert.equal(await page.locator('.customer-list>.empty').isVisible(), true);
  await page.locator('.map-search input').fill('');
  await page.waitForFunction(total => SalesRuntime.current.data.customers.length === total, totalIds.length);
  await page.locator('.map-zone.zone-attack').click({position: {x: 10, y: 10}});
  await page.waitForFunction(() => SalesRuntime.current.data.quadrantIndex === 1);
  const zoomed = await linkedCustomerIds(page);
  assert.ok(zoomed.length > 0 && zoomed.length < totalIds.length);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customers.every(row => row.quadrant === '主攻区')), true);
  await page.locator('.map-zone.zone-expanded').click({position: {x: 10, y: 10}});
  await page.waitForFunction(() => SalesRuntime.current.data.quadrantIndex === 0);
  assert.deepEqual(await linkedCustomerIds(page), totalIds);
  assert.deepEqual(await page.evaluate(() => {
    const p = SalesRuntime.current, d = p.data;
    return {pageId: p._id, scope: d.scope, team: d.selectedTeam, member: d.selectedMember, count: d.scopeCustomerCount, acv: d.acvText, period: d.assetPeriod, recognized: d.recognizedText, collection: d.collectionText, firstName: d.customers[0].name};
  }), before, 'display filtering does not alter asset scope or period');
}, [1366, 768], 'manager'));

test('overlapping map points preserve the source customer chooser and selected customer detail', () => fixture(async page => {
  await list(page);
  const ids = await page.evaluate(() => {
    const p = SalesRuntime.current, rows = p.data.plotCustomers.slice(0, 2).map(row => ({...row, plotStyle: 'left:45%;top:45%'}));
    p.setData({plotCustomers: rows});
    return rows.map(row => row.id);
  });
  await page.locator('.plot-hit-area').last().click();
  await page.waitForFunction(() => SalesRuntime.current.data.plotCandidates.length === 2);
  await page.locator('.plot-candidate').first().waitFor();
  assert.equal(await page.locator('.plot-candidate').count(), 2);
  await page.locator(`.plot-candidate[data-id="${ids[0]}"]`).click();
  await page.waitForFunction(id => SalesRuntime.current.data.selectedBattleCustomer?.id === id, ids[0]);
  await page.locator('.customer-sheet').waitFor();
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.plotCandidates.length), 0);
  assert.equal(await page.locator('.customer-sheet').isVisible(), true);
  await page.locator('.customer-sheet [data-handler=closeBattleCustomer]').click();
  await page.waitForFunction(() => !SalesRuntime.current.data.selectedBattleCustomer);
  assert.equal(await page.locator('.battle-section').isVisible(), true);
  assert.equal(await page.locator('.web-customer-list-pane').isVisible(), true);
}, [1366, 768], 'manager'));
