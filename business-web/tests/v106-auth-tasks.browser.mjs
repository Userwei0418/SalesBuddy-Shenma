/** Isolated Web acceptance of imported v1.0.6 auth/task pages and saved media. */
import test, {before, after} from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => {server = createSalesWebServer({target: ''}); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); base = 'http://127.0.0.1:' + server.address().port; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); await new Promise(resolve => server?.close(resolve));});
const actor = user => ({workspace_id: 'synthetic-v106', user_id: user, account_code: user.toUpperCase(), display_name: '合成' + user, role: 'sales', role_name: '一线销售', scope_name: '仅本人', team_ids: [], team_names: [], capabilities: {'task.read': true, 'task.respond': true, 'customer.read': true}, permission_version: 'v106'});
const taskId = '11111111-1111-4111-8111-111111111111';
async function fixture(run) {
 const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'}), errors = [], unexpected = [], eventCalls = [], logins = [];
 let deny = false, task = {id: taskId, title: '合成验收流程', description: '合成验收流程', status: 'pending_execution', creator_user_ref_id: 'creator', creator_name: '合成发起人', assignees: [{user_id: 'owner', name: '合成接收人', responsibility: 'owner'}], version_no: 3, events: []};
 await context.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url());
  if (url.origin !== base || url.pathname === '/local-login') {unexpected.push(url.pathname); return route.abort();}
  if (url.pathname === '/connection-status') return route.fulfill({json: {configured: true, reachable: true, localQuickLogin: {available: false}}});
  if (!url.pathname.startsWith('/api/')) return route.continue();
  if (url.pathname === '/api/v1/auth/password/login') {
   const data = request.postDataJSON(), user = data.account_code.toLowerCase(); logins.push(data.account_code);
   if (deny) return route.fulfill({status: 401, json: {detail: '合成账号验证失败'}});
   return route.fulfill({json: {access_token: 'synthetic-' + user, refresh_token: 'synthetic-refresh', actor: actor(user), auth_method: 'password', must_change_password: false}});
  }
  const user = String(request.headers().authorization || '').replace('Bearer synthetic-', '');
  if (url.pathname === '/api/v1/auth/me') return route.fulfill({json: {actor: actor(user)}});
  if (url.pathname === '/api/v1/auth/logout') return route.fulfill({json: {ok: true}});
  if (url.pathname === '/api/v1/assistant/home') return route.fulfill({json: {archived_visits: [], display_policy: {definition: {message_order: 'desc'}}, team_summary: {overdue: 0, claim: 0, handover: 0}}});
  if (url.pathname === '/api/v1/tasks/overview') return route.fulfill({json: {items: [], metrics: {today_completed: 0, today_pending: 0, all_pending: 0}}});
  if (url.pathname === '/api/v1/tasks/' + taskId) return route.fulfill({json: task});
  if (url.pathname === '/api/v1/tasks/' + taskId + '/events') {
   const data = request.postDataJSON(); eventCalls.push(data); assert.equal(data.version_no, task.version_no);
   task = {...task, status: data.event_type === 'approve_completion' ? 'completed' : data.event_type === 'reject_completion' ? 'in_progress' : task.creator_user_ref_id === 'owner' ? 'completed' : 'pending_review', version_no: task.version_no + 1, last_event_type: data.event_type, ...(data.event_type === 'complete' ? {completion_note: data.note} : {completion_review_note: data.note}), events: [...task.events, {id: String(task.version_no), event_type: data.event_type === 'complete' ? 'submit_completion' : data.event_type, actor_name: '合成' + user, note: data.note, occurred_at: new Date().toISOString()}]};
   return route.fulfill({json: task});
  }
  if (['/api/v1/customers', '/api/v1/notifications'].includes(url.pathname)) return route.fulfill({json: {items: [], total: 0, has_more: false}});
  unexpected.push(url.pathname); return route.fulfill({status: 503, json: {detail: 'Unconfigured synthetic endpoint'}});
 });
 const page = await context.newPage(); page.setDefaultTimeout(8000); page.on('pageerror', error => errors.push(error.message));
 try {await page.goto(base + '/?mode=live#/pages/login/index'); await page.getByPlaceholder('请输入账号名或手机号').waitFor(); await run({page, context, eventCalls, logins, deny: value => {deny = value;}}); assert.deepEqual(unexpected, []); assert.deepEqual(errors, []); assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);} finally {await context.close();}
}
async function login(page, user, remember = false) {
 await page.getByPlaceholder('请输入账号名或手机号').fill(user); await page.getByPlaceholder('请输入密码', {exact: true}).fill('Synthetic-Only-' + user);
 await page.waitForFunction(() => document.querySelector('[data-handler="toggleRememberPassword"]').getAttribute('aria-checked') === String(SalesRuntime.current.data.rememberPassword));
 if ((await page.locator('[data-handler="toggleRememberPassword"]').getAttribute('aria-checked') === 'true') !== remember) await page.locator('[data-handler="toggleRememberPassword"]').click();
 // Click the checkbox, not the row centre which can be the separate privacy link.
 await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
 await page.waitForFunction(() => SalesRuntime.current.data.agreed === true);
 await page.locator('[data-handler="submitLogin"]').click();
 await page.waitForFunction(() => SalesRuntime.current.route === 'pages/index/index' && !SalesRuntime.app._capabilityFlight);
}
async function out(page) {await page.evaluate(() => SalesRuntime.signOut()); await page.getByPlaceholder('请输入账号名或手机号').waitFor();}
async function detail(page) {await page.evaluate(id => SalesRuntime.route('/pages/task-detail/index?id=' + id), taskId); await page.waitForFunction(() => SalesRuntime.current.route === 'pages/task-detail/index' && !!SalesRuntime.current.data.task && !SalesRuntime.current.data.loading);}
async function confirm(page) {await page.locator('.wx-modal-buttons button').last().click();}
test('remembered accounts survive a fresh tab, suggest prefixes, isolate preview, and clear only failed credentials', () => fixture(async ({page, context, logins, deny}) => {
 await login(page, 'synthetic_a', true); await out(page); await login(page, 'synthetic_b', true); await out(page);
 await page.locator('[data-handler="switchAccount"]').click(); await page.getByPlaceholder('请输入账号名或手机号').fill('synthetic_');
 await page.waitForFunction(() => document.querySelectorAll('.account-suggestion').length === 2); assert.equal(await page.locator('.account-suggestion').count(), 2); await page.locator('.account-suggestion[data-account="synthetic_a"]').click();
 await page.waitForFunction(() => document.querySelector('input[placeholder="请输入密码"]').value === 'Synthetic-Only-synthetic_a');
 assert.equal(await page.getByPlaceholder('请输入密码', {exact: true}).inputValue(), 'Synthetic-Only-synthetic_a');
 assert.equal(await page.locator('[data-handler="toggleAgreement"]').getAttribute('aria-checked'), 'false');
 deny(true); await page.locator('[data-handler="toggleAgreement"] .checkbox').click(); await page.locator('[data-handler="submitLogin"]').click(); await page.locator('#login-error').filter({hasText: '合成账号验证失败'}).waitFor();
 await page.reload(); await page.getByPlaceholder('请输入账号名或手机号').waitFor();
 assert.equal(await page.getByPlaceholder('请输入账号名或手机号').inputValue(), 'synthetic_b');
 const fresh = await context.newPage(); await fresh.goto(base + '/?mode=live#/pages/login/index'); await fresh.getByPlaceholder('请输入账号名或手机号').waitFor();
 assert.equal(await fresh.getByPlaceholder('请输入密码', {exact: true}).inputValue(), 'Synthetic-Only-synthetic_b');
 assert.equal(await fresh.evaluate(() => !!SalesRuntime.wx.getStorageSync('salesApiAuth')), false);
 await fresh.goto(base + '/?mode=preview#/pages/index/index'); await fresh.waitForFunction(() => !!window.SalesRuntime?.app?.globalData.session);
 assert.equal(await fresh.evaluate(() => SalesRuntime.wx.getStorageSync('salesRememberedLoginV1')), '');
 await fresh.close(); assert.deepEqual(logins, ['SYNTHETIC_A', 'SYNTHETIC_B', 'SYNTHETIC_A']);
}));
test('task UI submits evidence, limits review to creator, rejects, resubmits, and shows confirmed history', () => fixture(async ({page, eventCalls}) => {
 await login(page, 'owner'); await detail(page);
 await page.locator('[data-handler="markCompleted"]').click(); assert.equal(eventCalls.length, 0);
 await page.getByPlaceholder('必填：说明交付结果、附件位置或后续安排').fill('合成结果第一版'); await page.locator('[data-handler="markCompleted"]').click(); await confirm(page);
 await page.waitForFunction(() => SalesRuntime.current.data.task?.status === 'pending_review'); assert.equal(await page.locator('[data-handler="reviewCompletion"]').count(), 0);
 await out(page); await login(page, 'creator'); await detail(page);
 await page.locator('[data-event="reject_completion"]').click(); assert.equal(eventCalls.length, 1);
 await page.getByPlaceholder('验收意见（驳回时必填）').fill('请补齐合成验证'); await page.locator('[data-event="reject_completion"]').click(); await confirm(page);
 await page.waitForFunction(() => SalesRuntime.current.data.task?.status === 'in_progress');
 await out(page); await login(page, 'owner'); await detail(page); assert.match(await page.locator('.review-rejected').innerText(), /请补齐合成验证/);
 await page.getByPlaceholder('必填：说明交付结果、附件位置或后续安排').fill('合成结果第二版'); await page.locator('[data-handler="markCompleted"]').click(); await confirm(page);
 await page.waitForFunction(() => SalesRuntime.current.data.task?.status === 'pending_review');
 await out(page); await login(page, 'creator'); await detail(page); await page.locator('[data-event="approve_completion"]').click(); await confirm(page);
 await page.waitForFunction(() => SalesRuntime.current.data.task?.status === 'completed' && document.querySelectorAll('.review-history-row').length === 4); assert.equal(await page.locator('.review-history-row').count(), 4);
 assert.deepEqual(eventCalls.map(item => item.event_type), ['complete', 'reject_completion', 'complete', 'approve_completion']);
 assert.deepEqual(eventCalls.map(item => item.version_no), [3, 4, 5, 6]);
}));
test('saved media survives page reload, keeps MIME on upload and rejects another account or mode', async () => {
 const context = await browser.newContext({serviceWorkers: 'block'}), page = await context.newPage(), uploads = [];
 await context.route('**/*', route => {const url = new URL(route.request().url());
  if (url.origin !== base) return route.abort();
  if (url.pathname === '/sandbox') return route.fulfill({contentType: 'text/html', body: '<html><body>Isolated saved-file test</body></html>'});
  if (url.pathname === '/api/v1/visit-imports') {uploads.push(route.request().postDataBuffer().toString()); return route.fulfill({json: {id: 'synthetic-import'}});}
  if (url.pathname === '/browser-platform.js') return route.continue(); return route.abort();
 });
 async function init() {await page.goto(base + '/sandbox'); await page.addScriptTag({url: base + '/browser-platform.js'}); await page.evaluate(() => {window.wx = {}; SalesPlatform.install(wx); wx.setStorageSync('salesSession', {workspaceId: 'synthetic-workspace', userId: 'synthetic-owner'});});}
 try {
  await init(); const path = await page.evaluate(() => new Promise((resolve, reject) => {const tempFilePath = SalesPlatform.registerLocalFile(new File(['synthetic audio bytes'], 'recording.webm', {type: 'audio/webm'}), {recorded: true}); wx.getFileSystemManager().saveFile({tempFilePath, success: value => resolve(value.savedFilePath), fail: reject});}));
  await init(); assert.equal(await page.evaluate(path => new Promise((resolve, reject) => wx.getFileSystemManager().readFile({filePath: path, encoding: 'utf8', success: value => resolve(value.data), fail: reject})), path), 'synthetic audio bytes');
  await page.evaluate(path => new Promise((resolve, reject) => wx.uploadFile({url: '/api/v1/visit-imports', filePath: path, name: 'file', formData: {original_filename: 'wrong.mp3'}, success: resolve, fail: reject})), path);
  assert.match(uploads[0], /filename="recording.webm"/); assert.match(uploads[0], /Content-Type: audio\/webm/); assert.doesNotMatch(uploads[0], /wrong.mp3/);
  await page.evaluate(() => wx.setStorageSync('salesSession', {workspaceId: 'synthetic-workspace', userId: 'synthetic-other'}));
  assert.match(await page.evaluate(path => new Promise(resolve => wx.getFileSystemManager().readFile({filePath: path, success: () => resolve('unexpected success'), fail: value => resolve(value.errMsg)})), path), /不属于当前账号/);
  await page.evaluate(() => {window.SALES_MODE = 'preview'; window.previewWx = {}; SalesPlatform.install(previewWx); previewWx.setStorageSync('salesSession', {workspaceId: 'synthetic-workspace', userId: 'synthetic-owner'});});
  assert.match(await page.evaluate(path => new Promise(resolve => previewWx.getFileSystemManager().readFile({filePath: path, success: () => resolve('unexpected success'), fail: value => resolve(value.errMsg)})), path), /不属于当前账号/);
  await page.evaluate(() => wx.setStorageSync('salesSession', {workspaceId: 'synthetic-workspace', userId: 'synthetic-owner'}));
  await page.evaluate(path => new Promise((resolve, reject) => wx.getFileSystemManager().removeSavedFile({filePath: path, success: resolve, fail: reject})), path);
  await init(); assert.match(await page.evaluate(path => new Promise(resolve => wx.getFileSystemManager().readFile({filePath: path, success: () => resolve('unexpected success'), fail: value => resolve(value.errMsg)})), path), /文件不存在/);
 } finally {await context.close();}
});
