/** Home presentation checks: an isolated server, synthetic actors, intercepted API only. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let server, browser, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });

const taskId = '00000000-0000-4000-8000-000000000099';
const metrics = [{key: 'today_completed', value: 0}, {key: 'all_pending', value: 17}, {key: 'today_pending', value: 5}];
const labels = ['今日待办', '全部待办', '今日已完成'];

async function fixture(run, {width = 1440, role = 'sales', order = 'desc'} = {}) {
  const actor = {workspace_id: 'synthetic-home-workspace', user_id: `synthetic-home-${role}`,
    account_code: `HOME_${role.toUpperCase()}`, display_name: '合成首页用户', role,
    role_name: role === 'fde' ? 'FDE' : '一线销售', scope_name: '合成本人范围', team_ids: [], team_names: [],
    permission_version: 'home-layout-v1', capabilities: {'customer.read': true, 'customer.claim': role === 'sales',
      'visit.create': true, 'task.create': true, 'task.respond': true, 'team.view': false}};
  const session = {workspaceId: actor.workspace_id, userId: actor.user_id, account: actor.account_code,
    userName: actor.display_name, role, roleName: actor.role_name, scope: actor.scope_name,
    teamIds: [], capabilities: actor.capabilities, permissionVersion: actor.permission_version,
    remote: true, authMethod: 'password', mustChangePassword: false, loginAt: 123456789};
  const context = await browser.newContext({viewport: {width, height: width < 900 ? 844 : 1000}, serviceWorkers: 'block'});
  const calls = [], unexpected = [], pageErrors = [];
  await context.addInitScript(({actor, session}) => {
    sessionStorage.setItem('sales-web:live:v1:salesSession', JSON.stringify(session));
    sessionStorage.setItem('sales-web:live:v1:salesApiAuth', JSON.stringify({actor, access_token: 'synthetic-home-access',
      refresh_token: 'synthetic-home-refresh', auth_method: 'password', must_change_password: false}));
    window.__homeMicrophoneRequests = 0;
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async () => {
      window.__homeMicrophoneRequests++;
      throw new Error('This presentation test must not start recording');
    };
  }, {actor, session});
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== base) { unexpected.push({external: url.origin}); return route.abort(); }
    if (url.pathname === '/connection-status') return route.fulfill({json: {configured: true, reachable: true, localQuickLogin: {available: false}}});
    if (process.env.SALES_WEB_SOURCE_CSS === '1' && url.pathname.endsWith('.css')) return route.fulfill({contentType: 'text/css', body: await readFile(new URL('..' + decodeURIComponent(url.pathname), import.meta.url), 'utf8')});
    if (!url.pathname.startsWith('/api/') && url.pathname !== '/local-login') return route.continue();
    calls.push({path: url.pathname, query: url.search, method: request.method()});
    if (request.method() !== 'GET') {
      unexpected.push({path: url.pathname, method: request.method()});
      return route.fulfill({status: 503, json: {detail: 'Writes are disabled in the isolated presentation fixture'}});
    }
    if (url.pathname === '/api/v1/auth/me') return route.fulfill({json: {actor}});
    if (url.pathname === '/api/v1/assistant/home') return route.fulfill({json: {archived_visits: [], display_policy: {definition: {message_order: order}}}});
    if (url.pathname === '/api/v1/tasks/overview') return route.fulfill({json: {items: [], metrics: {today_completed: 0, today_pending: 5, all_pending: 17}}});
    if (url.pathname === '/api/v1/tasks') return route.fulfill({json: {items: [], has_more: false, next_offset: null,
      summary: {total: 0, pending_count: 0, completed_count: 0, filtered_total: 0}}});
    if (url.pathname === `/api/v1/tasks/${taskId}`) return route.fulfill({json: {id: taskId, title: '合成任务详情',
      description: '仅用于核对原业务入口', status: 'pending_execution', priority_code: 'normal',
      due_at: '2026-09-20T09:00:00Z', created_at: '2026-09-15T00:00:00Z', assignees: [], events: [], attributes: {}, version_no: 1}});
    if (['/api/v1/customers', '/api/v1/customers/claim-pool', '/api/v1/notifications', '/api/v1/directory/task-positions',
      '/api/v1/directory/task-assignees', '/api/v1/tasks/recipients', '/api/v1/tasks/customers', '/api/v1/fde/visit-opportunities'].includes(url.pathname)) {
      return route.fulfill({json: {items: [], total: 0, has_more: false, next_offset: null}});
    }
    unexpected.push({path: url.pathname, query: url.search});
    return route.fulfill({status: 503, json: {detail: 'Unconfigured isolated home endpoint'}});
  });
  const page = await context.newPage(); page.setDefaultTimeout(8000);
  page.on('pageerror', error => pageErrors.push(error.message));
  try {
    await page.goto(base + '/?mode=live#/pages/index/index');
    await page.waitForFunction(order => window.SalesRuntime?.current?.route === 'pages/index/index'
      && SalesRuntime.current.homeOrder === order && SalesRuntime.current.data.overviewMetrics.length === 3
      && !SalesRuntime.app._capabilityFlight && !SalesRuntime.current.notificationLoading, order);
    await run({page, calls, setCapabilities(capabilities) {
      actor.capabilities = {...capabilities}; actor.permission_version = 'home-layout-v2';
    }});
    assert.deepEqual(unexpected, [], 'No remote, write or unconfigured request is allowed');
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    assert.equal(await page.evaluate(() => window.__homeMicrophoneRequests), 0);
    assert.equal(calls.some(call => /conversations|agent\/runs|audio\/transcriptions|visit-imports/.test(call.path)), false,
      'Rearranging or navigating the homepage must not pretend to record or start an Agent');
  } catch (error) {
    const state = await page.evaluate(() => ({route: window.SalesRuntime?.current?.route,
      order: window.SalesRuntime?.current?.homeOrder, metrics: window.SalesRuntime?.current?.data.overviewMetrics,
      quickActions: window.SalesRuntime?.current?.data.quickActions,
      messageIds: window.SalesRuntime?.current?.data.messages?.map(item => item.id),
      visibleHomeButtons: Array.from(document.querySelectorAll('.web-home-workspace button')).map(button => button.textContent.trim()),
      errors: window.SalesRuntime?.errors, width: innerWidth, scrollWidth: document.documentElement.scrollWidth})).catch(() => ({}));
    error.message += '\nHOME_DIAGNOSTIC ' + JSON.stringify({state, calls, unexpected, pageErrors});
    throw error;
  } finally { await context.close(); }
}

async function returnHome(page) {
  const nav = page.viewportSize().width < 900 ? '#mobile-nav' : '#desktop-nav';
  await page.locator(`${nav} [data-path="pages/index/index"]`).click();
  await page.waitForFunction(() => SalesRuntime.current.route === 'pages/index/index' && SalesRuntime.current.data.overviewMetrics.length === 3);
}

for (const width of [1440, 390, 320]) test(`${width}px home keeps metric values, one welcome and an overflow-free work sequence`, {timeout: 30000}, () => fixture(async ({page}) => {
  await page.evaluate(metrics => {
    const home = SalesRuntime.current; home.stopNotificationPolling();
    home.setData({overviewMetrics: metrics, messages: [...home.data.messages, {
      id: 'synthetic-width-event', kind: 'data-card', from: 'agent', time: '合成时间', sortAt: 1,
      card: {tone: 'blue', title: '合成业务动态：客户拜访记录和任务反馈需要查看当前详情',
        subtitle: 'SYNTHETIC_LONG_REFERENCE_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ',
        metrics: [], rows: [], action: {label: '查看原任务详情', code: 'open_task_detail', taskId: '00000000-0000-4000-8000-000000000099'}},
    }]});
  }, metrics);
  await page.locator('#chat-message-synthetic-width-event').waitFor();
  assert.deepEqual(await page.locator('.web-home-metric .metric-label').allTextContents(), labels);
  assert.deepEqual(await page.locator('.web-home-metric .metric-num').allTextContents(), ['5', '17', '0']);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.overviewMetrics), metrics, 'render must not reorder or rewrite source counters');
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.messages.some(message => message.kind === 'greeting')), true);
  assert.equal(await page.locator('.greeting-card').count(), 0, 'source greeting remains data but is not rendered twice');
  assert.equal(await page.locator('.workspace-head .greeting').count(), 1);
  assert.equal(await page.locator('.voice-composer').count(), 0, 'disabled ChatBI must remain absent');
  assert.equal(await page.locator('.visit-entry-microphone').count(), 0, 'the navigation action no longer implies instant recording');
  const sections = await page.locator('.overview-fixed,.web-home-shortcuts,.web-home-feed-heading,.chat-scroll').evaluateAll(nodes => nodes.map(node => ({name: node.className, x: node.getBoundingClientRect().x, y: node.getBoundingClientRect().y, right: node.getBoundingClientRect().right})));
  assert.equal(sections.length, 4);
  if (width >= 1180) {
    assert.ok(sections[1].y > sections[0].y && sections[1].right <= sections[0].right, 'shortcuts stay in the left overview rail');
    assert.ok(sections[2].x >= sections[0].right - 1 && sections[3].y > sections[2].y, 'activity starts in the adjoining right panel, below its own heading');
  } else assert.ok(sections.every((section, index) => index === 0 || section.y >= sections[index - 1].y), 'narrow windows retain stacked work order');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.equal(await page.locator('.web-home-workspace').evaluate(element => element.scrollWidth <= element.clientWidth), true);
  await page.evaluate(() => SalesRuntime.current.setData({overviewMetrics: [{key: 'today_completed', value: 0}, {key: 'today_pending', value: null}]}));
  await page.waitForFunction(() => document.querySelector('.web-home-metric .metric-num')?.textContent === '—');
  assert.deepEqual(await page.locator('.web-home-metric .metric-num').allTextContents(), ['—', '—', '0'], 'unknown metrics never become zero');
}, {width}));

test('all three metric buttons preserve the original overview query and server pagination', {timeout: 30000}, () => fixture(async ({page, calls}) => {
  for (const key of ['today_pending', 'all_pending', 'today_completed']) {
    const before = calls.length;
    await page.locator(`.web-home-metric[data-key="${key}"]`).click();
    await page.waitForFunction(key => SalesRuntime.current.route === 'pages/tasks/index'
      && SalesRuntime.current.data.overviewFilter === key && !SalesRuntime.current.data.loading, key);
    const request = calls.slice(before).find(call => call.path === '/api/v1/tasks');
    assert.ok(request, `original task API requested for ${key}`);
    const query = new URLSearchParams(request.query);
    assert.equal(query.get('overview'), key); assert.equal(query.get('view'), 'self');
    assert.equal(query.get('page_size'), '20'); assert.equal(query.get('offset'), '0');
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.loadError), '');
    await returnHome(page);
  }
}));

test('home shortcut buttons preserve source routes and FDE claim permission narrowing', {timeout: 40000}, async () => {
  await fixture(async ({page}) => {
    const entries = [['客户认领', 'customer-claim'], ['记录客户拜访', 'visit-entry'], ['创建任务', 'management-task-create']];
    for (const [label, route] of entries) {
      await page.locator('.web-home-actions').getByRole('button', {name: label, exact: false}).click();
      await page.waitForFunction(route => SalesRuntime.current.route === `pages/${route}/index`, route);
      assert.equal(await page.evaluate(() => !!SalesRuntime.current.data.accessBlocked), false);
      await returnHome(page);
    }
  });
  await fixture(async ({page, setCapabilities}) => {
    assert.deepEqual(await page.locator('.web-home-actions .quick-action-label').allTextContents(), ['记录客户拜访', '创建任务']);
    assert.equal(await page.locator('.web-home-actions [data-action="客户认领"]').count(), 0);
    await page.locator('.web-home-actions [data-action="记录客户拜访"]').click();
    await page.waitForFunction(() => SalesRuntime.current.route === 'pages/visit-entry/index');
    assert.equal(await page.evaluate(() => SalesRuntime.current.data.isFde), true);
    assert.equal(await page.evaluate(() => !!SalesRuntime.current.data.isRecording), false);
    await returnHome(page);
    setCapabilities({'customer.read': true, 'customer.claim': false, 'visit.create': false, 'task.create': false, 'team.view': false});
    await page.evaluate(() => SalesRuntime.app.refreshCapabilities(true));
    await page.waitForFunction(() => SalesRuntime.current.data.quickActions.length === 0
      && !document.querySelector('.web-home-shortcuts'));
    assert.equal(await page.locator('.web-home-actions').count(), 0, 'all shortcuts disappear after current capabilities narrow');
  }, {role: 'fde'});
});

for (const order of ['asc', 'desc']) test(`${order} business feed preserves source message order and task detail actions`, {timeout: 30000}, () => fixture(async ({page, calls}) => {
  const messages = [
    {id: 'synthetic-text-old', kind: 'text', from: 'agent', sortAt: 1, time: '合成较早时间', text: '合成早期动态'},
    {id: 'synthetic-welcome', kind: 'greeting', from: 'agent', sortAt: 2, card: {title: '仅保留在源数据的欢迎卡'}},
    {id: 'synthetic-task-event', kind: 'data-card', from: 'agent', sortAt: 3, time: '合成事件时间', card: {
      tone: 'blue', title: '合成业务动态', subtitle: '这是一条任务回执，不是待办队列', metrics: [], rows: [],
      action: {label: '查看原任务详情', code: 'open_task_detail', taskId}}},
    {id: 'synthetic-text-new', kind: 'text', from: 'agent', sortAt: 4, time: '合成较新时间', text: '合成最新动态'},
  ];
  if (order === 'desc') messages.reverse();
  await page.evaluate(messages => { const home = SalesRuntime.current; home.stopNotificationPolling(); home.setData({messages}); }, messages);
  const expected = messages.filter(message => message.kind !== 'greeting').map(message => 'chat-message-' + message.id);
  await page.waitForFunction(() => !!document.querySelector('#chat-message-synthetic-task-event'));
  assert.deepEqual(await page.locator('.chat-stream > .message-row').evaluateAll(nodes => nodes.map(node => node.id)), expected);
  assert.deepEqual(await page.evaluate(() => SalesRuntime.current.data.messages), messages, 'presentation must preserve all original data and event order');
  assert.equal(await page.locator('.web-home-feed-heading').innerText().then(text => text.includes(order === 'asc' ? '按时间正序' : '最近更新在前')), true);
  await page.locator('#chat-message-synthetic-task-event summary').click();
  assert.equal(await page.locator('.database-badge').innerText(), '动态记录', 'an event receipt must not claim a fresh data sync');
  await page.locator('#chat-message-synthetic-task-event summary').click();
  await page.locator('[data-action="open_task_detail"]').getByText('查看原任务详情', {exact: true}).click();
  await page.waitForFunction(taskId => SalesRuntime.current.route === 'pages/task-detail/index'
    && SalesRuntime.current.data.task?.id === taskId, taskId);
  assert.equal(await page.evaluate(() => SalesRuntime.current.data.task.title), '合成任务详情');
  assert.ok(calls.some(call => call.path === `/api/v1/tasks/${taskId}`));
}, {order}));
