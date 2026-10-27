/** Desktop scroll ownership and Mini Program lifecycle adapters, synthetic preview only.
 * Run: PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node --test tests/desktop-scroll.browser.mjs
 * The server has target:''; the browser additionally rejects all /api, /local-login and external requests.
 */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
const desktopSizes = [[1366, 768], [1024, 600], [630, 800]];
const oldVisibleChat = {1366: 241.328125, 1024: 89.328125, 630: 0}; // Measured before this change on the isolated 0.5 baseline.
async function settle(page) {await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));}
async function fixture(run, [width, height] = desktopSizes[0], role = 'sales') {
  const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block'});
  const forbidden = [], pageErrors = [];
  await context.addInitScript(role => {
    sessionStorage.setItem('sales-web:preview-role', role);
    // Assert the public layout contract through each page's known main scroll surface.
    // This is independent of the runtime's visible data-web-page-scroll discovery.
    window.__scrollOwnerSelector = () => {
      const page = window.SalesRuntime?.current, name = page?.route?.split('/')[1];
      if (page?.data.isFde && ['workbench','bi'].includes(name)) return name==='workbench'&&innerWidth>=1180?'.web-collection-main':'.web-insights-scroll';
      return ({tasks:innerWidth>=1180?'.web-task-content':'.web-task-results-scroll', customers:innerWidth>=1000?'.web-customer-list-scroll':'.web-customer-results', workbench:innerWidth>=1180?'.web-opportunity-results':'.web-opportunity-results-scroll',
        'visit-entry':innerWidth>=901?'#page-root':'.web-visit-content', 'management-task-create':'.web-workflow-scroll', bi:'.web-insights-scroll'})[name] || '#page-root';
    };
    window.__scrollOwner = () => document.querySelector(window.__scrollOwnerSelector());
  }, role);
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== base || /^\/api(?:\/|$)/.test(url.pathname) || url.pathname === '/local-login') {
      forbidden.push({path: url.pathname, method: route.request().method()}); return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => pageErrors.push(error.message));
  try {
    await page.goto(base + '/?mode=preview');
    await page.waitForFunction(() => window.SalesRuntime?.current?.route === 'pages/index/index' && window.SalesRuntime.app.globalData.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(() => {
      window.__scrollApiCalls = [];
      const original = window.SalesPreview;
      window.SalesPreview = {...original, request(options) {
        const url = new URL(options.url, location.href);
        window.__scrollApiCalls.push({path: url.pathname, query: url.search, method: options.method || 'GET'});
        return original.request(options);
      }};
    });
    await settle(page); await run(page);
    assert.deepEqual(forbidden, [], 'No remote API, local quick login, or outside origin may be contacted');
    assert.deepEqual(pageErrors, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } catch (error) {
    const diagnostic = await page.evaluate(() => {
      const root = document.querySelector('#page-root'), current = window.SalesRuntime?.current;
      const dimensions = el => el && {client: [el.clientWidth, el.clientHeight], scroll: [el.scrollWidth, el.scrollHeight], scrollTop: el.scrollTop, rect: {x: el.getBoundingClientRect().x, y: el.getBoundingClientRect().y, width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height}, overflowY: getComputedStyle(el).overflowY};
      return {viewport: [innerWidth, innerHeight], windowY: scrollY, document: dimensions(document.documentElement), root: dimensions(root), chat: dimensions(document.querySelector('.chat-scroll')), route: current?.route,
        data: {loading: current?.data.loading, hasMore: current?.data.hasMore, rows: current?.data.filteredTasks?.length, error: current?.data.loadError || current?.data.errorText, scrollCalls: current?.__scrollReachCalls}, calls: window.__scrollApiCalls, runtimeErrors: window.SalesRuntime?.errors};
    }).catch(() => ({}));
    console.error('DESKTOP_SCROLL_DIAGNOSTIC ' + JSON.stringify({diagnostic, forbidden, pageErrors})); throw error;
  } finally {await context.close();}
}
async function go(page, name, query = '', method = 'reLaunch') {
  await page.evaluate(({name, query, method}) => SalesRuntime.wx[method]({url: '/pages/' + name + '/index' + query}), {name, query, method});
  await page.waitForFunction(name => {
    const current = SalesRuntime.current, data = current?.data || {};
    const fdeComponent = data.isFde && ['workbench', 'bi'].includes(name) ? current.selectComponent('#fdeContent') : null;
    const loading = data.isFde && ['workbench', 'bi'].includes(name) ? !fdeComponent || fdeComponent.data.loading : data.loading;
    return current?.route === 'pages/' + name + '/index' && !loading && !data.searching && !data.recipientLoading && (data.isFde || (!data.opportunityListLoading && !data.opportunityOverviewLoading));
  }, name);
  await settle(page);
}
async function scrollSurface(page) {return page.locator(await page.evaluate(() => __scrollOwnerSelector()));}
async function geometry(page) {return page.evaluate(() => {
  const root = document.querySelector('#page-root'), rect = root.getBoundingClientRect(), owner=__scrollOwner();
  return {viewport: [innerWidth, innerHeight], documentHeight: Math.max(document.documentElement.scrollHeight, document.body.scrollHeight), documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth), windowY: scrollY,
    ownerSelector:__scrollOwnerSelector(), ownerHeight:owner.clientHeight, ownerScrollHeight:owner.scrollHeight, ownerOverflow:getComputedStyle(owner).overflowY, ownerBottom:owner.getBoundingClientRect().bottom, rootHeight: root.clientHeight, rootWidth: root.clientWidth, rootScrollHeight: root.scrollHeight, rootScrollWidth: root.scrollWidth, rootY: rect.top, rootBottom: rect.bottom, overflowY: getComputedStyle(root).overflowY};
});}
function assertDesktopGeometry(result, label) {
  assert.ok(result.documentHeight <= result.viewport[1] + 2, label + ': document must not be a second vertical scroller: ' + JSON.stringify(result));
  assert.ok(result.documentWidth <= result.viewport[0] + 2, label + ': document must not overflow horizontally');
  assert.equal(result.windowY, 0, label + ': window must stay at top');
  if (label === 'index' || label === 'home') {
    assert.equal(result.overflowY, 'hidden', label + ': homepage leaves scrolling to the dynamic feed');
    assert.ok(result.rootScrollHeight <= result.rootHeight + 2, label + ': homepage root must not have extra vertical content');
  } else {
    assert.ok(['auto','scroll'].includes(result.ownerOverflow), label + ': designated content owns desktop scrolling: '+JSON.stringify(result));
    assert.ok(result.ownerHeight>=128 && result.ownerBottom<=result.rootBottom+2, label + ': content surface fits within frame: '+JSON.stringify(result));
    assert.ok(result.rootScrollHeight<=result.rootHeight+2, label + ': root must not duplicate the content scrollbar');
  }
  assert.ok(result.rootHeight > 250 && result.rootBottom <= result.viewport[1] + 2, label + ': usable root fits below the header');
  assert.ok(result.rootScrollWidth <= result.rootWidth + 2, label + ': content must not widen root');
}
for (const size of desktopSizes) test(`${size.join('x')} seven work pages have one bounded desktop main scroller`, {timeout: 45000}, () => fixture(async page => {
  for (const name of ['index', 'tasks', 'customers', 'workbench', 'visit-entry', 'management-task-create', 'bi']) {
    await go(page, name, name === 'tasks' ? '?tab=all' : '');
    assertDesktopGeometry(await geometry(page), name);
    const heights = await page.evaluate(async () => ({root: document.querySelector('#page-root').clientHeight,
      sync: SalesRuntime.wx.getSystemInfoSync().windowHeight, window: SalesRuntime.wx.getWindowInfo().windowHeight,
      async: (await new Promise(resolve => SalesRuntime.wx.getSystemInfo({success: resolve}))).windowHeight}));
    assert.equal(heights.sync, heights.root, name + ': getSystemInfoSync uses the usable root height');
    assert.equal(heights.window, heights.root, name + ': getWindowInfo uses the usable root height');
    assert.equal(heights.async, heights.root, name + ': getSystemInfo callback uses the usable root height');
    if (name !== 'index') {
      const before = await (await scrollSurface(page)).evaluate(el => ({max: el.scrollHeight - el.clientHeight, y: el.scrollTop}));
      if (before.max > 20) {
        const edge=await (await scrollSurface(page)).boundingBox();await page.mouse.move(edge.x+edge.width-5,edge.y+edge.height-5);await page.mouse.wheel(0,300);
        await page.waitForFunction(() => __scrollOwner().scrollTop > 10);
        assert.equal(await page.evaluate(() => scrollY), 0, name + ': real wheel scroll stays inside root');
      }
    }
  }
}, size));

for (const size of desktopSizes) test(`${size.join('x')} homepage gives dynamic records more visible room without root/chat double scrolling`, {timeout: 30000}, () => fixture(async page => {
  await page.evaluate(() => {
    const owner = SalesRuntime.current; owner.stopNotificationPolling?.();
    owner.setData({messages: Array.from({length: 25}, (_, index) => ({id: 'scroll-fixture-' + index, from: 'agent', kind: 'data-card', time: '合成时间', sortAt: index,
      card: {tone: 'blue', title: '合成动态 ' + index, subtitle: '用于检验独立动态列表滚动，不生成业务待办', rows: [], metrics: []}}))});
  });
  await settle(page); const result = await geometry(page); assertDesktopGeometry(result, 'home');
  const chat = await page.locator('.chat-scroll').evaluate(el => {
    const rect = el.getBoundingClientRect(); return {height: el.clientHeight, max: el.scrollHeight - el.clientHeight, visible: Math.max(0, Math.min(innerHeight, rect.bottom) - Math.max(0, rect.top))};
  });
  assert.ok(chat.visible >= Math.max(oldVisibleChat[size[0]] + 20, result.rootHeight * 0.35), 'Visible feed area must improve over the measured baseline: ' + JSON.stringify({chat, baseline: oldVisibleChat[size[0]], result}));
  assert.ok(result.rootScrollHeight <= result.rootHeight + 2, 'Home must not add a root scrollbar around its feed');
  assert.ok(chat.max > 100); await page.locator('.chat-scroll').hover(); await page.mouse.wheel(0, 600);
  await page.waitForFunction(() => document.querySelector('.chat-scroll').scrollTop > 10);
  assert.equal(await (await scrollSurface(page)).evaluate(el => el.scrollTop), 0); assert.equal(await page.evaluate(() => scrollY), 0);
}, size));

test('real primary content bottom scrolling calls original onReachBottom and loads one next task page', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'tasks', '?tab=all'); await page.waitForFunction(() => SalesRuntime.current.data.filteredTasks.length === 20 && SalesRuntime.current.data.hasMore);
  await page.evaluate(() => {
    const owner = SalesRuntime.current, original = owner.onReachBottom; owner.__scrollReachCalls = 0;
    owner.onReachBottom = function (...args) {this.__scrollReachCalls++; return original.apply(this, args);};
  });
  await (await scrollSurface(page)).evaluate(el => el.scrollTo({top: el.scrollHeight, behavior: 'instant'}));
  await page.waitForFunction(() => SalesRuntime.current.data.filteredTasks.length === 22 && !SalesRuntime.current.data.loadingMore);
  const first = await page.evaluate(() => ({count: SalesRuntime.current.__scrollReachCalls, calls: __scrollApiCalls.filter(call => call.path.endsWith('/tasks') && new URLSearchParams(call.query).get('offset') === '20')}));
  assert.equal(first.calls.length, 1); assert.ok(first.count >= 1); assert.equal(await page.evaluate(() => scrollY), 0);
  await page.getByText('已显示全部 22 项', {exact: true}).waitFor();
  await (await scrollSurface(page)).evaluate(el => el.scrollTo({top: 0, behavior: 'instant'})); await settle(page);
  await (await scrollSurface(page)).evaluate(el => el.scrollTo({top: el.scrollHeight, behavior: 'instant'})); await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => __scrollApiCalls.filter(call => call.path.endsWith('/tasks') && new URLSearchParams(call.query).get('offset') === '20').length), 1);
}));

test('nested customer list scrolling neither moves the main page nor invokes page onReachBottom', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'visit-entry'); const list = page.locator('.customer-results'); await list.waitFor();
  assert.ok(await list.evaluate(el => el.scrollHeight > el.clientHeight));
  await page.evaluate(() => {SalesRuntime.current.__scrollReachCalls = 0; SalesRuntime.current.onReachBottom = function () {this.__scrollReachCalls++;};});
  const top = await (await scrollSurface(page)).evaluate(el => el.scrollTop);
  await list.hover(); await page.mouse.wheel(0, 120); await page.waitForFunction(() => document.querySelector('.customer-results').scrollTop > 10);
  assert.equal(await (await scrollSurface(page)).evaluate(el => el.scrollTop), top);
  await list.evaluate(el => el.scrollTo({top: el.scrollHeight, behavior: 'instant'})); await page.waitForTimeout(400);
  assert.equal(await page.evaluate(() => SalesRuntime.current.__scrollReachCalls), 0, 'Captured nested scroll events must not masquerade as page bottom');
  const selected = await page.locator('.customer-result').last().getAttribute('data-id'); await page.locator('.customer-result').last().click();
  await page.waitForFunction(id => SalesRuntime.current.data.customerConfirmed && SalesRuntime.current.data.customerId === id, selected);
  await page.locator('.customer-results').waitFor({state: 'detached'});
  assert.equal(await page.locator('.customer-results').count(), 0);
}));

test('wx.pageScrollTo numeric and selector positions address the designated desktop content', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'management-task-create');
  const numericTarget=await page.evaluate(()=>Math.min(230,__scrollOwner().scrollHeight-__scrollOwner().clientHeight));assert.ok(numericTarget>20,'fixture must contain scrollable form content');
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 230, duration: 0}));
  await page.waitForFunction(y => Math.abs(__scrollOwner().scrollTop - y) < 2,numericTarget);
  assert.equal(await page.evaluate(() => scrollY), 0);
  const expected = await page.evaluate(() => {
    const root = __scrollOwner(), target = document.querySelector('.custom-due-fields');
    return Math.min(root.scrollHeight - root.clientHeight, Math.max(0, root.scrollTop + target.getBoundingClientRect().top - root.getBoundingClientRect().top - (parseFloat(getComputedStyle(root).scrollPaddingTop)||0) - (parseFloat(getComputedStyle(target).scrollMarginTop)||0)));
  });
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({selector: '.custom-due-fields', duration: 0}));
  await page.waitForFunction(expected => Math.abs(__scrollOwner().scrollTop - expected) < 3, expected);
  assert.equal(await page.evaluate(() => scrollY), 0);
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 0, duration: 0}));
  await page.waitForFunction(() => __scrollOwner().scrollTop === 0);
}));

test('navigateTo/back and switching cached tabs restore each page content position after source reloads', {timeout: 45000}, () => fixture(async page => {
  await go(page, 'tasks', '?tab=all'); await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 260, duration: 0}));
  await page.waitForFunction(() => __scrollOwner().scrollTop >= 250);
  const parentY = await (await scrollSurface(page)).evaluate(el => el.scrollTop);
  await go(page, 'visit-entry', '', 'navigateTo'); assert.equal(await (await scrollSurface(page)).evaluate(el => el.scrollTop), 0);
  await page.evaluate(() => SalesRuntime.wx.navigateBack()); await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && !SalesRuntime.current.data.loading);
  await page.waitForFunction(y => Math.abs(__scrollOwner().scrollTop - y) < 3, parentY);
  await go(page, 'workbench', '', 'switchTab'); await page.waitForFunction(() => __scrollOwner().scrollHeight - __scrollOwner().clientHeight > 450);
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 420, duration: 0})); await page.waitForFunction(() => __scrollOwner().scrollTop >= 410);
  const tabY = await (await scrollSurface(page)).evaluate(el => el.scrollTop);
  await go(page, 'index', '', 'switchTab'); assert.equal(await (await scrollSurface(page)).evaluate(el => el.scrollTop), 0);
  await go(page, 'workbench', '', 'switchTab');
  await page.waitForFunction(y => Math.abs(__scrollOwner().scrollTop - y) < 3, tabY);
  assert.equal(await page.evaluate(() => scrollY), 0);
}));

test('explicit pageScrollTo during a pending return load cancels the previous saved position', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'tasks', '?tab=all');
  await page.evaluate(() => {
    SalesRuntime.wx.pageScrollTo({scrollTop: 420, duration: 0});
    const owner = SalesRuntime.current, original = owner.onShow;
    owner.onShow = async function (...args) {
      this.__scrollDelayedShow = true;
      await new Promise(resolve => {window.__resumeTaskShow = resolve;});
      try {return await original.apply(this, args);}
      finally {this.__scrollDelayedShow = false;}
    };
  });
  await page.waitForFunction(() => __scrollOwner().scrollTop >= 410);
  await go(page, 'visit-entry', '', 'navigateTo');
  await page.evaluate(() => SalesRuntime.wx.navigateBack());
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && SalesRuntime.current.__scrollDelayedShow && typeof __resumeTaskShow === 'function');
  // A deliberate "back to top" command must cancel restoration even when the root is already at 0.
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 0, duration: 0}));
  await page.evaluate(() => {window.__resumeTaskShow(); delete window.__resumeTaskShow;});
  await page.waitForFunction(() => !SalesRuntime.current.__scrollDelayedShow && !SalesRuntime.current.data.loading && SalesRuntime.current.data.filteredTasks.length === 20);
  await settle(page);
  assert.equal(await (await scrollSurface(page)).evaluate(el => el.scrollTop), 0, 'Late onShow completion must not restore the previous 420px position');
  assert.equal(await page.evaluate(() => scrollY), 0);
}));

test('FDE component pages restore cached tab position despite unused parent loading flags', {timeout: 30000}, () => fixture(async page => {
  for (const name of ['workbench', 'bi']) {
    await go(page, name);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.isFde), true);
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.loading), true, name + ': the source parent loading flag belongs to the hidden sales branch');
    assert.equal(await page.evaluate(() => SalesRuntime.current.selectComponent('#fdeContent').data.error), '');
    await page.waitForFunction(() => __scrollOwner().scrollHeight - __scrollOwner().clientHeight > 200);
    await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 180, duration: 0}));
    await page.waitForFunction(() => __scrollOwner().scrollTop === 180);
    await go(page, 'index', '', 'switchTab');
    await go(page, name, '', 'switchTab');
    await page.waitForFunction(() => Math.abs(__scrollOwner().scrollTop - 180) < 3);
    assert.equal(await page.evaluate(() => scrollY), 0);
  }
}, desktopSizes[0], 'fde'));

test('390px phone keeps natural document scrolling and wx.pageScrollTo addresses window', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'management-task-create');
  assert.ok((await geometry(page)).documentHeight > 844 + 100);
  await page.locator('.description-card').hover(); await page.mouse.wheel(0, 450);
  await page.waitForFunction(() => scrollY > 50); assert.equal(await page.locator('#page-root').evaluate(el => el.scrollTop), 0);
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({scrollTop: 120, duration: 0})); await page.waitForFunction(() => Math.abs(scrollY - 120) < 2);
  await page.evaluate(() => SalesRuntime.wx.pageScrollTo({selector: '.custom-due-fields', duration: 0})); await page.waitForFunction(() => scrollY > 300);
  assert.equal(await page.locator('#page-root').evaluate(el => el.scrollTop), 0);
  assert.ok((await geometry(page)).documentWidth <= 392);
}, [390, 844]));

test('phone header pointer input cancels a pending document scroll restoration', {timeout: 30000}, () => fixture(async page => {
  await go(page, 'tasks', '?tab=all');
  await page.evaluate(() => {
    SalesRuntime.wx.pageScrollTo({scrollTop: 420, duration: 0});
    const owner = SalesRuntime.current, original = owner.onShow;
    owner.onShow = async function (...args) {
      this.__scrollDelayedShow = true;
      await new Promise(resolve => {window.__resumePhoneShow = resolve;});
      try {return await original.apply(this, args);}
      finally {this.__scrollDelayedShow = false;}
    };
  });
  await page.waitForFunction(() => scrollY >= 410);
  await go(page, 'visit-entry', '', 'navigateTo');
  await page.evaluate(() => SalesRuntime.wx.navigateBack());
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/tasks/index' && SalesRuntime.current.__scrollDelayedShow && typeof __resumePhoneShow === 'function');
  assert.equal(await page.locator('#page-title').evaluate(el => document.querySelector('#page-root').contains(el)), false, 'The pointer target must be outside the page root');
  await page.locator('#page-title').click();
  await page.evaluate(() => {window.__resumePhoneShow(); delete window.__resumePhoneShow;});
  await page.waitForFunction(() => !SalesRuntime.current.__scrollDelayedShow && !SalesRuntime.current.data.loading && SalesRuntime.current.data.filteredTasks.length === 20);
  await settle(page);
  assert.equal(await page.evaluate(() => scrollY), 0, 'A header interaction must cancel the saved 420px document position');
  assert.equal(await page.locator('#page-root').evaluate(el => el.scrollTop), 0);
}, [390, 844]));

for (const size of desktopSizes) test(`${size.join('x')} last form controls and submit button remain visible and unoccluded`, {timeout: 30000}, () => fixture(async page => {
  await go(page, 'management-task-create'); await (await scrollSurface(page)).evaluate(el => el.scrollTo({top: el.scrollHeight, behavior: 'instant'})); await settle(page);
  const task = await page.evaluate(() => {
    const fields = document.querySelector('.custom-due-fields').getBoundingClientRect(), button = document.querySelector('.bottom-bar button[data-handler="submitTask"]'), box = button.getBoundingClientRect(), bar = button.closest('.bottom-bar').getBoundingClientRect(), root = document.querySelector('#page-root').getBoundingClientRect();
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return {fieldsTop: fields.top, fieldsBottom: fields.bottom, rootTop: root.top, rootLeft: root.left, barTop: bar.top, button: {left: box.left, top: box.top, right: box.right, bottom: box.bottom}, hit: hit === button || button.contains(hit), viewport: [innerWidth, innerHeight]};
  });
  assert.ok(task.fieldsTop >= task.rootTop - 2 && task.fieldsBottom <= task.barTop + 2, 'Final date/time inputs must not be hidden beneath the fixed submit bar: ' + JSON.stringify(task));
  assert.ok(task.button.left >= task.rootLeft - 2 && task.button.bottom <= task.viewport[1] + 2); assert.equal(task.hit, true);
  await go(page, 'visit-entry');
  const initialSubmit = await page.locator('button[data-handler="submitTranscript"]').evaluate(button => {const box = button.getBoundingClientRect(), hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2); return {top: box.top, bottom: box.bottom, height: innerHeight, hit: hit === button || button.contains(hit)};});
  assert.ok(initialSubmit.top >= 0 && initialSubmit.bottom <= initialSubmit.height + 2, 'Original visit submit must be visible before scrolling: ' + JSON.stringify(initialSubmit));
  assert.equal(initialSubmit.hit, true);
  await page.locator('.customer-result').first().click(); await page.locator('textarea.note-input').fill('合成滚动测试记录。\n'.repeat(20));
  await settle(page);
  await (await scrollSurface(page)).evaluate(el => el.scrollTo({top: el.scrollHeight, behavior: 'instant'})); await settle(page);
  const submit = await page.locator('button[data-handler="submitTranscript"]').evaluate(button => {const box = button.getBoundingClientRect(), hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2), tip = document.querySelector('.safe-tip').getBoundingClientRect(), root = document.querySelector('#page-root').getBoundingClientRect(), footer = getComputedStyle(button.closest('.visit-entry-page'), '::after'); const range = document.createRange(); range.selectNodeContents(document.querySelector('.safe-tip')); const text = range.getBoundingClientRect(); return {top: box.top, bottom: box.bottom, left: box.left, height: innerHeight, hit: hit === button || button.contains(hit), disabled: button.disabled, tipTop: text.top, tipBottom: text.bottom, tipRight: text.right, rootTop: root.top};});
  assert.ok(submit.top >= 0 && submit.bottom <= submit.height + 2, JSON.stringify(submit)); assert.equal(submit.hit, true); assert.equal(submit.disabled, false);
  assert.ok(submit.tipTop >= submit.rootTop - 2 && submit.tipBottom <= submit.height && (submit.tipBottom <= submit.top + 2 || submit.tipRight <= submit.left - 4), 'Last explanation must remain visible and separate from the submit action: ' + JSON.stringify(submit));
}, size));
