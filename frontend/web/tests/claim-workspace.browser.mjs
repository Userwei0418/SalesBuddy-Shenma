/** Real claim handlers against isolated synthetic responses; business network is blocked. */
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
async function fixture(run, size = [1366, 768]) {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'});
  const blocked = [], errors = [], page = await context.newPage();
  page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    return route.continue();
  });
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      const original = SalesRuntime.wx.request;
      const rows = Array.from({length: 120}, (_, index) => ({
        id: `claim-fixture-${index + 1}`, name: `合成客户 ${index + 1}`,
        level_code: 'Tier1', industry: '合成行业', team_name: '合成团队', owner_name: '未分配',
        claimed: false, can_claim: true, ownership_state: 'unassigned', claim_status: 'unclaimed',
      }));
      Object.assign(rows[0], {claimed: true, can_claim: false, ownership_state: 'owned', claim_status: 'claimed', owner_name: '本人'});
      Object.assign(rows[1], {can_claim: false, ownership_state: 'owned', claim_status: 'claimed', owner_name: '其他负责人'});
      Object.assign(rows[2], {can_claim: false, ownership_state: 'legacy_review'});
      Object.assign(rows[3], {can_claim: false, claim_status: 'pending'});
      Object.assign(rows[4], {claim_status: 'rejected'});
      rows[5].name = '合成客户：名称较长的跨区域协同服务有限公司';
      window.__claimFixture = {rows, reads: [], writes: []};
      SalesRuntime.wx.request = options => {
        const url = new URL(options.url, location.origin), path = url.pathname;
        if (!path.endsWith('/customers/claim-pool') && !/\/customers\/claim-fixture-\d+\/claims$/.test(path)) return original(options);
        let data;
        if (path.endsWith('/claim-pool')) {
          const offset = Number(url.searchParams.get('offset') || 0), size = Number(url.searchParams.get('page_size') || 50), query = url.searchParams.get('q') || '';
          __claimFixture.reads.push({offset, query});
          const filtered = rows.filter(row => row.name.includes(query)), end = Math.min(offset + size, filtered.length);
          data = {items: filtered.slice(offset, end), total: filtered.length, has_more: end < filtered.length, next_offset: end < filtered.length ? end : null};
        } else {
          const id = path.split('/').at(-2), row = rows.find(row => row.id === id);
          __claimFixture.writes.push(id);
          row.can_claim = false; row.claim_status = 'pending';
          data = {id: 'claim-application-synthetic', customer_id: id, status: 'pending'};
        }
        const timer = setTimeout(() => {const response = {statusCode: 200, data: structuredClone(data), header: {}}; options.success?.(response); options.complete?.(response);}, 10);
        return {abort() {clearTimeout(timer);}};
      };
      SalesRuntime.wx.navigateTo({url: '/pages/customer-claim/index'});
    });
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-claim/index' && !SalesRuntime.current.data.loading && SalesRuntime.current.data.customers.length === 50);
    await paint(page);
    await run(page);
    assert.deepEqual(errors, []);
    assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function visibleFooter(page) {
  const result = await page.locator('.claim-confirm').evaluate(el => {
    const rect = el.getBoundingClientRect();
    return {height: rect.height, bottom: rect.bottom, left: rect.left, right: rect.right, width: innerWidth, heightLimit: innerHeight - (innerWidth <= 900 ? 66 : 0), reachable: el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2))};
  });
  assert.equal(result.height, 36);
  assert.ok(result.left >= 0 && result.right <= result.width && result.bottom <= result.heightLimit + 1 && result.reachable, JSON.stringify(result));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('#page-root').evaluate(el => el.scrollHeight <= el.clientHeight + 2), true);
}

for (const size of [[1366, 768], [1024, 600], [320, 740]]) {
  test(`${size.join('x')} claim list fits the workspace with an internal scroller and a visible 36px submit`, () => fixture(async page => {
    await visibleFooter(page);
    const bounds = await page.locator('.web-claim-scroll').evaluate(el => ({height: el.clientHeight, content: el.scrollHeight, width: el.clientWidth, contentWidth: el.scrollWidth}));
    assert.ok(bounds.height > 90 && bounds.content > bounds.height * 2, JSON.stringify(bounds));
    assert.ok(bounds.contentWidth <= bounds.width + 1, JSON.stringify(bounds));
    await page.locator('.claim-card[data-id="claim-fixture-6"]').click();
    await paint(page);
    assert.match(await page.locator('.web-claim-selection').innerText(), /名称较长/);
    await visibleFooter(page);
    if (size[0] === 1366 || size[0] === 320) await page.screenshot({path: size[0] === 1366 ? '/tmp/claim-workspace-desktop.png' : '/tmp/claim-workspace-mobile.png'});
    if (size[0] > 760) assert.deepEqual(await page.locator('.web-claim-columns text').allTextContents(), ['', '客户名称', '等级', '行业 / 团队', '负责人', '认领状态']);
  }, size));
}

test('claim keeps native ineligible reasons, single selection, cancel and pending approval without ownership', () => fixture(async page => {
  assert.equal(await page.locator('.claim-confirm').isDisabled(), true);
  const labels = ['本人已认领', '已被认领', '待运营核对', '申请待审批'];
  for (let i = 0; i < labels.length; i++) {
    const row = page.locator(`.claim-card[data-id="claim-fixture-${i + 1}"]`);
    assert.equal(await row.locator('.web-claim-state').innerText(), labels[i]);
    await row.click();
    await paint(page);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedCustomerId), '');
    assert.equal(await page.locator('.claim-confirm').isDisabled(), true);
  }
  await page.locator('.claim-card[data-id="claim-fixture-5"]').click();
  await paint(page);
  assert.equal(await page.locator('.claim-card.selected').count(), 1);
  assert.match(await page.locator('.claim-card.selected').innerText(), /可重新申请/);
  await page.locator('.claim-card[data-id="claim-fixture-6"]').click();
  await paint(page);
  assert.equal(await page.locator('.claim-card.selected').count(), 1);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedCustomerId), 'claim-fixture-6');
  await page.locator('.claim-confirm').click();
  await page.locator('.wx-modal').waitFor();
  assert.match(await page.locator('.wx-modal-content').innerText(), /运营审批通过.*记录拜访无需先认领/);
  await page.locator('.wx-modal-buttons button').filter({hasText: '取消'}).click();
  assert.deepEqual(await page.evaluate(() => __claimFixture.writes), []);
  assert.equal(await page.locator('.claim-card.selected').count(), 1);
  await page.locator('.claim-confirm').click();
  await page.locator('.wx-modal-buttons button').filter({hasText: '提交申请'}).click();
  await page.waitForFunction(() => !SalesRuntime.current.data.loading && SalesRuntime.current.data.resultMessage && !SalesRuntime.current.data.selectedCustomerId);
  await paint(page);
  assert.deepEqual(await page.evaluate(() => __claimFixture.writes), ['claim-fixture-6']);
  assert.equal(await page.locator('.claim-card[data-id="claim-fixture-6"] .web-claim-state').innerText(), '申请待审批');
  assert.equal(await page.evaluate(() => __claimFixture.rows[5].claimed), false);
  assert.match(await page.locator('.claim-result').innerText(), /等待运营审批/);
  await visibleFooter(page);
}));

test('claim internal scroll keeps native pagination and search clears selection without changing the query contract', () => fixture(async page => {
  await page.locator('.web-claim-scroll').evaluate(el => el.scrollTop = el.scrollHeight);
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length === 100);
  await page.locator('.web-claim-scroll').evaluate(el => el.scrollTop = el.scrollHeight);
  await page.waitForFunction(() => SalesRuntime.current.data.customers.length === 120);
  assert.deepEqual(await page.evaluate(() => __claimFixture.reads.map(read => read.offset)), [0, 50, 100]);
  assert.equal(await page.locator('.claim-card').count(), 120);
  assert.equal(await page.locator('.claim-list-end').innerText(), '已显示全部匹配客户');
  await page.locator('.claim-card[data-id="claim-fixture-120"]').click();
  await page.getByPlaceholder('搜索客户名称').fill('合成客户 120');
  await page.waitForFunction(() => !SalesRuntime.current.data.loading && SalesRuntime.current.data.total === 1);
  await paint(page);
  assert.equal(await page.locator('.claim-card').count(), 1);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.selectedCustomerId), '');
  assert.equal(await page.locator('.claim-confirm').isDisabled(), true);
  assert.deepEqual(await page.evaluate(() => __claimFixture.reads.at(-1)), {offset: 0, query: '合成客户 120'});
  await visibleFooter(page);
}));
