/** Fixed Web home frame: synthetic receipts; no real business service requests. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const project = new URL('../', import.meta.url);
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
async function paint(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
async function fixture(run, size, role = 'sales') {
  const context = await browser.newContext({viewport: {width: size[0], height: size[1]}, serviceWorkers: 'block'});
  const blocked = [], errors = [];
  await context.addInitScript(role => sessionStorage.setItem('sales-web:preview-role', role), role);
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)) {blocked.push(url.pathname); return route.abort();}
    // A pre-build CSS check must not mutate dist while another task is testing it.
    if (process.env.SALES_WEB_SOURCE_CSS === '1' && url.pathname.endsWith('.css')) {
      return route.fulfill({contentType: 'text/css', body: await readFile(new URL('.' + decodeURIComponent(url.pathname), project), 'utf8')});
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => SalesRuntime?.current?.route === 'pages/index/index' && !SalesRuntime.app._capabilityFlight && !SalesRuntime.current.notificationLoading);
    await page.evaluate(() => {
      SalesRuntime.current.stopNotificationPolling();
      const messages = Array.from({length: 24}, (_, index) => ({
        id: `home-fit-${index}`, kind: 'data-card', from: 'agent', time: '9月20日 10:00', sortAt: Date.UTC(2026, 8, 20, 2, 0) + index,
        card: {title: `合成任务 ${index}`, subtitle: '确认交付标准', tone: 'yellow',
          metrics: [{label: '任务状态', value: '待接受', action: 'pending'}],
          rows: Array.from({length: index ? 1 : 12}, (_, item) => ({title: `原始明细 ${index}-${item}`, meta: '完整保留的动态内容与业务上下文', taskId: `task-${index}-${item}`})),
          footer: '合成动态口径说明', action: {code: 'open_task_detail', label: '查看任务详情', taskId: `task-${index}`}},
      }));
      window.__homeFitMessages = structuredClone(messages);
      window.__homeFitMetrics = structuredClone(SalesRuntime.current.data.overviewMetrics);
      SalesRuntime.current.setData({messages});
    });
    await page.locator('#chat-message-home-fit-0').waitFor(); await paint(page);
    await run(page);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages), await page.evaluate(() => __homeFitMessages));
    assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.overviewMetrics), await page.evaluate(() => __homeFitMetrics));
    assert.deepEqual(blocked, []); assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await context.close();}
}
async function frame(page) {
  return page.evaluate(() => {
    const rect = selector => {const el = document.querySelector(selector), box = el.getBoundingClientRect(); return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, height: box.height, client: el.clientHeight, scroll: el.scrollHeight, scrollTop: el.scrollTop, overflow: getComputedStyle(el).overflowY};};
    return {width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth, documentHeight: document.documentElement.scrollHeight, documentTop: document.scrollingElement.scrollTop,
      root: rect('#page-root'), workspace: rect('.web-home-workspace'), overview: rect('.overview-fixed'), feed: rect('.web-home-feed-panel'), stream: rect('.chat-scroll'),
      controls: [...document.querySelectorAll('.web-home-metric,.web-home-actions .quick-action-button')].map(el => {const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, height: r.height};})};
  });
}
function fixedFrame(result) {
  assert.ok(result.documentWidth <= result.width + 1, JSON.stringify(result));
  assert.ok(result.documentHeight <= result.height + 1, JSON.stringify(result));
  assert.ok(result.root.scroll <= result.root.client + 1, JSON.stringify(result));
  assert.ok(result.workspace.scroll <= result.workspace.client + 1, JSON.stringify(result));
  assert.equal(result.documentTop, 0); assert.equal(result.root.scrollTop, 0);
  assert.ok(result.stream.client >= 160, JSON.stringify(result));
  assert.ok(result.stream.scroll > result.stream.client * 2, JSON.stringify(result));
  assert.equal(result.stream.overflow, 'auto');
  const bottom = result.height - (result.width <= 900 ? 66 : 0);
  assert.ok(result.stream.bottom <= bottom + 1, JSON.stringify(result));
  const sideBySide = result.width >= 1180;
  if (sideBySide) {
    assert.ok(Math.abs(result.overview.right - result.feed.left) <= 1 && Math.abs(result.overview.top - result.feed.top) <= 1, 'overview and feed share one frame with no background gap: ' + JSON.stringify(result));
    assert.ok(Math.abs(result.overview.bottom - result.feed.bottom) <= 1, 'the context rail fills the frame even when its contents are short');
    assert.ok(result.stream.client >= result.root.client * .65, 'desktop inbox uses most of the workspace height');
  } else assert.ok(result.overview.bottom < result.stream.top, JSON.stringify(result));
  for (const control of result.controls) assert.ok(control.height >= 32 && control.top >= result.root.top && control.bottom <= bottom && control.left >= 0 && control.right <= result.width + 1 && (sideBySide ? control.right <= result.overview.right : control.bottom < result.stream.top), JSON.stringify(result));
}
for (const size of [[1920, 1080], [1366, 768], [1366, 600], [1180, 768], [1179, 768], [1024, 600], [768, 800], [630, 800]]) {
  test(`${size.join('x')} fixed home keeps counters/actions visible while only the full receipt inbox scrolls`, () => fixture(async page => {
    const before = await frame(page); fixedFrame(before);
    await page.locator('#chat-message-home-fit-0 summary').click();
    assert.equal(await page.locator('#chat-message-home-fit-0 .web-activity-body .business-row').count(), 12);
    assert.ok((await page.locator('#chat-message-home-fit-0 .web-activity-body').innerText()).includes('原始明细 0-11'));
    await paint(page); fixedFrame(await frame(page));
    await page.evaluate(() => {
      window.__homeFitActions = [];
      SalesRuntime.current.handleCardRow = event => __homeFitActions.push(event.currentTarget.dataset);
    });
    await page.locator('#chat-message-home-fit-0 .business-row[data-task-id="task-0-11"]').click();
    assert.equal(await page.evaluate(() => __homeFitActions[0].taskId), 'task-0-11');
    await page.locator('.chat-scroll').evaluate(el => {el.scrollTop = el.scrollHeight;}); await paint(page);
    const after = await frame(page); fixedFrame(after); assert.ok(after.stream.scrollTop > 0);
    assert.equal(after.overview.top, before.overview.top);
    const last = await page.locator('#chat-message-home-fit-23').boundingBox();
    assert.ok(last && last.y >= after.stream.top && last.y + last.height <= after.stream.bottom + 1, JSON.stringify({last, stream: after.stream}));
    const actionClear = await page.locator('#chat-message-home-fit-23 .business-action').evaluate(el => {const r = el.getBoundingClientRect(); return [r.left + 4, r.left + r.width / 2, r.right - 4].every(x => el.contains(document.elementFromPoint(x, r.top + r.height / 2)));});
    assert.equal(actionClear, true, 'the last action remains clickable across its width, above the floating create button');
  }, size));
}
for (const size of [[1366, 768], [1366, 600], [630, 800]]) test(`${size.join('x')} FDE leader keeps team work visible without exposing customer claim`, () => fixture(async page => {
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.isFdeLead), true);
  assert.equal(await page.locator('.web-home-actions [data-action="客户认领"]').count(), 0);
  await page.evaluate(() => SalesRuntime.current.setData({fdeTeamSummary: {overdue: 4, claim: 6, handover: 2}}));
  await page.locator('.fde-team-summary').waitFor(); await paint(page); fixedFrame(await frame(page));
  const summary = await page.locator('.fde-team-summary').innerText();
  for (const value of ['4 逾期', '6 待领取', '2 待交接']) assert.ok(summary.includes(value));
  await page.evaluate(() => {window.__teamClicks = 0; SalesRuntime.current.openFdeTeamTasks = () => __teamClicks++;});
  await page.locator('.fde-team-summary').click();
  assert.equal(await page.evaluate(() => __teamClicks), 1);
}, size, 'fde_lead'));
for (const width of [390, 320]) test(`${width}px phone keeps readable stacked content with no horizontal overflow`, () => fixture(async page => {
  const layout = await frame(page);
  assert.ok(layout.documentWidth <= width + 1);
  assert.equal(await page.locator('.web-activity-detail').count(), 0, 'phone still presents complete native receipts');
  assert.equal(await page.locator('#chat-message-home-fit-0 .business-row').count(), 12);
  assert.equal(await page.locator('.web-home-metric').count(), 3);
  const buttons = await page.locator('.web-home-actions .quick-action-button').evaluateAll(nodes => nodes.map(el => ({height: el.getBoundingClientRect().height, font: parseFloat(getComputedStyle(el.querySelector('.quick-action-label')).fontSize)})));
  assert.ok(buttons.every(button => button.height >= 32 && button.font >= 12));
}, [width, 844]));
