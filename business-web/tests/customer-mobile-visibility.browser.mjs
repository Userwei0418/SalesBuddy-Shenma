/** Synthetic layout regression: customer content must be reachable, not just overflow-free. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({previewOnly:true});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel:'chrome', headless:true});
});
after(async () => {
  await browser?.close();
  if (server) await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});
});
for (const width of [1440, 390]) test(`${width}px customer map and list stay reachable through resize and detail navigation`, {timeout:40000}, async () => {
  const context = await browser.newContext({viewport:{width,height:900}, serviceWorkers:'block'});
  const page = await context.newPage(), errors = [], blocked = [];
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || url.pathname.startsWith('/api/') || url.pathname === '/local-login') {
      blocked.push(url.pathname); return route.abort();
    }
    return route.continue();
  });
  try {
    await page.goto(`${base}/?mode=preview#/pages/customers/index`);
    await page.waitForFunction(() => window.SalesRuntime?.current?.data?.plotCustomers?.length > 0 && !SalesRuntime.current.data.assetLoading);
    await page.locator('.ds-customer-name').first().waitFor();
    const firstId = await page.evaluate(() => SalesRuntime.current.data.customers[0].id);
    for (const nextWidth of [width, width === 390 ? 1440 : 390, width]) {
      await page.setViewportSize({width:nextWidth,height:nextWidth === 390 ? 844 : 900});
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const geometry = await page.evaluate(() => {
        const bounds = selector => document.querySelector(selector).getBoundingClientRect().toJSON();
        const root = document.querySelector('#page-root');
        return {wrap:bounds('.ds-customer-workspace-wrap'), workbench:bounds('.ds-customer-workbench'), map:bounds('.sb-bmap'), list:bounds('.ds-customer-list'),
          rootOverflow:root.scrollHeight - root.clientHeight, documentOverflow:document.documentElement.scrollWidth - innerWidth};
      });
      assert.ok(geometry.wrap.height > 200, `map/list wrapper must contribute height: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.map.height > 200, 'the map must have a usable size');
      assert.ok(geometry.list.bottom <= geometry.workbench.bottom + 2, 'the list cannot be clipped by the workbench');
      assert.ok(geometry.documentOverflow <= 1, 'no horizontal document overflow');
      if (nextWidth > 900) assert.ok(geometry.rootOverflow <= 2, 'desktop workspace still fits its pane');
    }
    await page.locator('.ds-customer-name').first().click();
    await page.waitForFunction(id => window.SalesRuntime?.current?.route === 'pages/customer-detail/index' && SalesRuntime.current.data.customer?.id === id, firstId);
    await page.locator('[aria-label="客户详情"]').waitFor({state:'visible'});
    for (const nextWidth of [1440, 390, 1440]) {
      await page.setViewportSize({width:nextWidth,height:844});
      const bounds = await page.evaluate(() => ({header:document.querySelector('#frame-topbar').getBoundingClientRect().toJSON(), title:document.querySelector('#frame-topbar .ant-breadcrumb').getBoundingClientRect().toJSON()}));
      assert.ok(bounds.title.top >= bounds.header.top && bounds.title.bottom <= bounds.header.bottom, 'detail breadcrumb remains inside the header');
    }
    await page.goto(`${base}/?mode=preview#/pages/risks/index`);
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/risks/index' && SalesRuntime.current.data.filteredRisks?.length > 0 && !SalesRuntime.current.data.loading);
    for (const nextWidth of [1440, 390, 1440, 390]) {
      await page.setViewportSize({width:nextWidth,height:844});
      const table = page.locator('.ant-table-content');
      const geometry = await table.evaluate(el => ({client:el.clientWidth,scroll:el.scrollWidth,titleWidth:el.querySelector('tbody td').getBoundingClientRect().width,overflow:document.documentElement.scrollWidth-innerWidth}));
      assert.ok(geometry.titleWidth >= 230, 'risk titles retain a readable column width');
      assert.ok(geometry.overflow <= 1, 'table scrolling stays inside the page');
      if (nextWidth === 390) assert.ok(geometry.scroll > geometry.client, 'all columns are available through internal scrolling');
    }
    const related = page.locator('.ant-table-tbody tr.ant-table-row').first().locator('td').last();
    await related.scrollIntoViewIfNeeded();
    const lastCell = await related.boundingBox();
    assert.ok(lastCell && lastCell.x >= 0 && lastCell.x + lastCell.width <= 390, 'last risk column is reachable');
    await page.locator('.ant-table-content').evaluate(el => {el.scrollLeft = 0;});
    await page.locator('.ant-table-tbody tr.ant-table-row').first().locator('td').first().click();
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/risk-detail/index' && !!SalesRuntime.current.data.risk);

    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
});
