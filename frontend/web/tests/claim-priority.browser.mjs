/** Built React claim page and real shared handlers; synthetic transport, no business network. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({previewOnly: true, staticDir: process.env.SALES_WEB_TEST_DIST});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {
  await browser?.close(); server?.closeAllConnections();
  await new Promise(resolve => server?.close(resolve));
});
async function ready(page) {
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/customer-claim/index'
    && !SalesRuntime.current.data.loading && !SalesRuntime.current.data.optionsLoading);
  await page.locator('.ds-claim-toolbar').waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}
async function chooseState(page, label) {
  await page.locator('.ds-claim-toolbar').getByRole('combobox').last().click();
  await page.locator('.ant-select-dropdown:visible').getByText(label, {exact: true}).click();
  await ready(page);
}
const search = page => page.getByPlaceholder('客户名称 / 拼音 / 首字母', {exact: true});
const read = page => page.evaluate(() => claimProbe.reads.at(-1));
async function query(page, value) {
  await search(page).fill(value);
  await page.waitForFunction(value => claimProbe.reads.at(-1)?.q === value, value);
  await ready(page);
}
async function fixture(run, {q = '', empty = false} = {}) {
  const context = await browser.newContext({viewport: {width: 1366, height: 768}, serviceWorkers: 'block'});
  const page = await context.newPage(), errors = [], blocked = [];
  page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  await context.addInitScript(() => sessionStorage.setItem('sales-web:preview-role', 'sales'));
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {
      blocked.push(url.pathname); return route.abort();
    }
    return route.continue();
  });
  try {
    await page.goto(base + '/?mode=preview#/pages/index/index');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(({q, empty}) => {
      const rows = Array.from({length: 130}, (_, i) => ({
        id: `10000000-0000-4000-8000-${String(i + 1).padStart(12, '0')}`,
        name: `${i < 65 ? 'A已认领' : 'B未认领'}合成客户${String(i % 65 + 1).padStart(3, '0')}`,
        industry_code: '软件', level_code: 'Tier1', team_name: '合成团队',
        business_identities: ['customer'], owner_name: i < 65 ? '合成负责人' : null,
        ownership_state: i < 65 ? 'claimed' : 'unclaimed', claimed: i === 0,
        claim_status: i === 65 ? 'pending' : i === 66 ? 'rejected' : null,
        can_claim: i >= 65 && i !== 67, can_read_customer: i < 65,
      }));
      window.claimProbe = {rows, reads: [], writes: [], held: [], holdReads: false, holdWrite: false, empty};
      const preview = SalesPreview;
      const respond = (options, data, statusCode = 200, hold = false) => {
        const response = {statusCode, data: structuredClone(data), header: {}};
        const finish = () => {options.success?.(response); options.complete?.(response);};
        if (hold) claimProbe.held.push(finish); else queueMicrotask(finish);
        return {abort() {}};
      };
      window.SalesPreview = {...preview, request(options = {}) {
        const url = new URL(options.url, location.href), path = url.pathname;
        if (path.endsWith('/customers/claim-pool/options')) return respond(options, {
          industries: [{value: '', label: '全部行业'}, {value: '软件', label: '软件'}],
          // Deliberately different order: the selected label must follow value, not index 1.
          claim_statuses: [{value: '', label: '全部认领状态'}, {value: 'claimed', label: '已认领'},
            {value: 'pending', label: '我的申请待审批'}, {value: 'unclaimed', label: '未认领'},
            {value: 'mine', label: '本人已认领'}, {value: 'legacy_review', label: '待运营核对'}],
        });
        if (path.endsWith('/customers/claim-pool')) {
          const p = url.searchParams, status = p.get('claim_status') || '', q = p.get('q') || '';
          const offset = Number(p.get('offset')), size = Number(p.get('page_size'));
          claimProbe.reads.push({status, q, offset, size});
          if (claimProbe.denyNext) {claimProbe.denyNext = false; return respond(options, {detail: '当前没有客户目录权限'}, 403);}
          const filtered = (claimProbe.empty ? [] : rows).filter(row => row.name.includes(q)
            && (!status || (['unclaimed', 'claimed', 'legacy_review'].includes(status) && row.ownership_state === status)
              || (status === 'mine' && row.claimed) || (status === 'pending' && row.claim_status === 'pending')));
          const end = Math.min(offset + size, filtered.length);
          return respond(options, {items: filtered.slice(offset, end), total: filtered.length,
            has_more: end < filtered.length, next_offset: end < filtered.length ? end : null}, 200, claimProbe.holdReads);
        }
        if (/\/customers\/[^/]+\/claims$/.test(path)) {
          const id = path.split('/').at(-2), row = rows.find(row => row.id === id);
          claimProbe.writes.push(id);
          row.claim_status = 'pending';
          return respond(options, {id: 'synthetic-application', customer_id: id, status: 'pending'}, 201, claimProbe.holdWrite);
        }
        return preview.request(options);
      }};
      SalesRuntime.wx.navigateTo({url: '/pages/customer-claim/index' + (q ? '?q=' + encodeURIComponent(q) : '')});
    }, {q, empty});
    await ready(page);
    await run(page);
    assert.deepEqual(errors, []); assert.deepEqual(blocked, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } catch (error) {
    console.error('CLAIM_UI_DIAGNOSTIC', await page.evaluate(() => ({
      text: document.querySelector('#page-root')?.innerText.slice(0, 600),
      query: SalesRuntime.current.data.query, selected: SalesRuntime.current.data.selectedCustomerId,
      first: SalesRuntime.current.data.customers.slice(0, 3).map(row => ({name: row.name, label: row.claimLabel})), reads: claimProbe.reads,
    })));
    throw error;
  } finally {await context.close();}
}

test('first request filters the whole directory, keeps server pages and pending/permission guards', () => fixture(async page => {
  assert.deepEqual(await page.evaluate(() => claimProbe.reads), [{status: 'unclaimed', q: '', offset: 0, size: 50}]);
  assert.equal(await page.getByText('状态：未认领', {exact: true}).count(), 1);
  const selectedLabel = await page.getByText('未认领', {exact: true}).evaluate(el => ({width: el.clientWidth, content: el.scrollWidth}));
  assert.ok(selectedLabel.width > 0 && selectedLabel.content <= selectedLabel.width, JSON.stringify(selectedLabel));
  assert.equal(await page.getByText('已加载 50 / 共 65 家', {exact: true}).count(), 1);
  assert.equal(await page.getByRole('radio', {name: '选择 B未认领合成客户001', exact: true}).isDisabled(), true);
  assert.equal(await page.getByRole('radio', {name: '选择 B未认领合成客户003', exact: true}).isDisabled(), true);
  assert.equal(await page.getByText('申请待审批', {exact: false}).count(), 1);
  assert.equal(await page.getByRole('button', {name: '查看档案', exact: true}).count(), 0);
  await mkdir('.runtime/claim-priority', {recursive: true});
  await page.screenshot({path: '.runtime/claim-priority/default-unclaimed.png', fullPage: true});
  await page.getByRole('button', {name: '加载更多客户', exact: true}).click();
  await page.getByText('已加载 65 / 共 65 家', {exact: true}).waitFor();
  assert.deepEqual(await read(page), {status: 'unclaimed', q: '', offset: 50, size: 50});
  assert.equal(await page.evaluate(() => new Set(SalesRuntime.current.data.customers.map(row => row.id)).size), 65);
}));

test('all/other states persist through search and refresh; reset restores unclaimed and its real label', () => fixture(async page => {
  await chooseState(page, '全部认领状态');
  assert.equal((await read(page)).status, '');
  await page.getByRole('button', {name: '刷新名单', exact: true}).click(); await ready(page);
  assert.equal((await read(page)).status, '');
  await page.getByRole('button', {name: '加载更多客户', exact: true}).click();
  await page.getByText('已加载 100 / 共 130 家', {exact: true}).waitFor();
  assert.deepEqual(await read(page), {status: '', q: '', offset: 50, size: 50});
  await query(page, 'A已认领合成客户065');
  assert.equal(await page.getByText('A已认领合成客户065', {exact: true}).count(), 1);
  assert.equal((await read(page)).status, '');
  await chooseState(page, '已认领'); assert.equal((await read(page)).status, 'claimed');
  await page.getByRole('button', {name: '重置为未认领', exact: true}).click(); await ready(page);
  assert.deepEqual(await read(page), {status: 'unclaimed', q: '', offset: 0, size: 50});
  assert.equal(await search(page).inputValue(), '');
  assert.equal(await page.getByText('状态：未认领', {exact: true}).count(), 1);
}));

for (const empty of [false, true]) test(`empty unclaimed view explains scope and preserves q (empty directory: ${empty})`, () => fixture(async page => {
  assert.deepEqual(await page.evaluate(() => claimProbe.reads), [{status: 'unclaimed', q: 'A已认领合成客户065', offset: 0, size: 50}]);
  await page.getByText('未认领范围内没有匹配客户，可切换“全部认领状态”查看其他客户', {exact: true}).waitFor();
  assert.equal(await search(page).inputValue(), 'A已认领合成客户065');
  if (empty) await page.evaluate(() => claimProbe.empty = false);
  await chooseState(page, '全部认领状态');
  await page.getByText('A已认领合成客户065', {exact: true}).waitFor();
  assert.equal((await read(page)).q, 'A已认领合成客户065');
}, {q: 'A已认领合成客户065', empty}));

test('explicit application submits once and refreshes authoritative pending state and total', () => fixture(async page => {
  await page.getByRole('radio', {name: '选择 B未认领合成客户002', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('input[aria-label="选择 B未认领合成客户002"]')?.checked);
  await page.evaluate(() => claimProbe.holdWrite = true);
  await page.getByRole('button', {name: '提交认领申请', exact: true}).click();
  await page.getByRole('button', {name: '提交申请', exact: true}).click();
  await page.waitForFunction(() => claimProbe.writes.length === 1 && SalesRuntime.current.data.submitting);
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByRole('button', {name: '重置为未认领', exact: true}).isDisabled(), true);
  await page.evaluate(() => SalesRuntime.current.confirmClaim());
  assert.equal(await page.evaluate(() => claimProbe.writes.length), 1);
  await page.evaluate(() => claimProbe.held.shift()());
  await page.waitForFunction(() => claimProbe.reads.length === 2 && !SalesRuntime.current.data.loading);
  await page.getByText('“B未认领合成客户002”的认领申请已提交，等待运营审批。', {exact: true}).waitFor();
  assert.equal(await page.getByRole('radio', {name: '选择 B未认领合成客户002', exact: true}).isDisabled(), true);
  assert.equal(await page.getByText('已加载 50 / 共 65 家', {exact: true}).count(), 1);
  assert.equal((await read(page)).status, 'unclaimed');
}));

test('late searches cannot overwrite reset; identity change restores default and rejects the old account response', () => fixture(async page => {
  await chooseState(page, '全部认领状态');
  await page.evaluate(() => claimProbe.holdReads = true);
  await search(page).fill('A已认领合成客户065');
  await page.waitForFunction(() => claimProbe.held.length === 1);
  await page.evaluate(() => claimProbe.holdReads = false);
  await page.getByRole('button', {name: '重置为未认领', exact: true}).click(); await ready(page);
  await page.evaluate(() => claimProbe.held.shift()());
  await page.waitForFunction(() => SalesRuntime.current.data.query === '' && SalesRuntime.current.data.customers.length === 50);
  assert.equal(await page.getByText('A已认领合成客户065', {exact: true}).count(), 0);
  await chooseState(page, '全部认领状态');
  await page.evaluate(() => claimProbe.holdReads = true);
  await search(page).fill('A已认领合成客户064');
  await page.waitForFunction(() => claimProbe.held.length === 1);
  await page.evaluate(() => {
    claimProbe.holdReads = false;
    SalesRuntime.app.globalData.session.userId = 'synthetic-new-user';
    SalesRuntime.current.onShow();
  });
  await ready(page);
  await page.evaluate(() => claimProbe.held.shift()());
  assert.deepEqual(await read(page), {status: 'unclaimed', q: '', offset: 0, size: 50});
  assert.equal(await page.getByText('状态：未认领', {exact: true}).count(), 1);
  assert.equal(await page.getByText('A已认领合成客户064', {exact: true}).count(), 0);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.customers.length), 50);
}));

test('directory denial stays an error and retry retains the selected server filter', () => fixture(async page => {
  await page.evaluate(() => claimProbe.denyNext = true);
  await page.getByRole('button', {name: '刷新名单', exact: true}).click(); await ready(page);
  await page.getByText('当前没有客户目录权限', {exact: true}).waitFor();
  assert.equal(await page.getByText('未认领范围内没有匹配客户，可切换“全部认领状态”查看其他客户', {exact: true}).count(), 0);
  await page.getByRole('button', {name: '刷新名单', exact: true}).click(); await ready(page);
  assert.equal((await read(page)).status, 'unclaimed');
  await page.getByText('已加载 50 / 共 65 家', {exact: true}).waitFor();
}));
