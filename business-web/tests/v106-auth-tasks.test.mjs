/** Source page + Web storage contracts. Every identity/password below is synthetic. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const read = file => fs.readFileSync(new URL('../' + file, import.meta.url), 'utf8');
const KEY = 'salesRememberedLoginV1';
function storage() {
  const values = {};
  return new Proxy(values, {get(target, key) {
    if (key === 'getItem') return name => target[name] ?? null;
    if (key === 'setItem') return (name, value) => {target[name] = String(value);};
    if (key === 'removeItem') return name => {delete target[name];};
    return target[key];
  }});
}
function fixture({local = storage(), session = storage(), mode = 'live', origin = 'http://localhost:5186', apiBase = '/api/v1', login = async () => ({role: 'sales'})} = {}) {
  const window = {SALES_MODE: mode, location: {href: origin + '/', origin}}, wx = {}, notices = [], calls = [];
  vm.runInNewContext(read('browser-platform.js'), {window, localStorage: local, sessionStorage: session, URL, Blob, navigator: {}, screen: {}, requestAnimationFrame: fn => fn()});
  window.SalesPlatform.install(wx);
  Object.assign(wx, {showToast: value => notices.push(value.title), switchTab() {}});
  const app = {globalData: {}, async loginWithApi(...args) {calls.push(args); const result = await login(...args); app.globalData.session = result; wx.setStorageSync('salesApiAuth', {access_token: 'synthetic-session-token'}); wx.setStorageSync('salesSession', result); return result;}, logout() {app.globalData.session = null; wx.removeStorageSync('salesApiAuth'); wx.removeStorageSync('salesSession');}};
  const module = {exports: {}}; vm.runInNewContext(read('source/miniprogram/utils/rememberedLogin.js'), {module, wx});
  let definition; vm.runInNewContext(read('source/miniprogram/pages/login/index.js'), {Page: value => {definition = value;}, wx, getApp: () => app, require: name => name.includes('rememberedLogin') ? module.exports : {getBaseUrl: () => apiBase, changePassword: async () => {}}});
  const page = {...definition, data: {...definition.data}, setData(value) {Object.assign(this.data, value);}};
  return {page, app, wx, local, session, notices, calls, remembered: module.exports};
}
async function submit(x, account = 'SYNTHETIC_A', password = 'Synthetic-Only-A', remember = true) {
  x.page.inputAccount({detail: {value: account}}); x.page.inputPassword({detail: {value: password}});
  if (x.page.data.rememberPassword !== remember) x.page.toggleRememberPassword();
  x.page.setData({agreed: true}); await x.page.submitLogin();
}
test('remembered passwords require opt-in and success; tokens remain tab-scoped', async () => {
  const x = fixture(); await submit(x, 'synthetic_a', 'Synthetic-Only-A', false);
  assert.equal(Object.keys(x.local).length, 0); assert.ok(x.wx.getStorageSync('salesApiAuth'));
  x.app.logout(); await submit(x); assert.equal(Object.keys(x.local).length, 1);
  assert.equal(Object.keys(x.local).some(key => /salesApiAuth|salesSession/.test(key)), false);
  assert.match(Object.keys(x.local)[0], /live.*http%3A%2F%2Flocalhost%3A5186%2Fapi%2Fv1/);
  x.app.logout(); assert.ok(x.wx.getStorageSync(KEY));
  const fresh = fixture({local: x.local}); fresh.page.onLoad();
  assert.equal(fresh.page.data.account, 'SYNTHETIC_A'); assert.equal(fresh.page.data.password, 'Synthetic-Only-A');
  assert.equal(fresh.page.data.rememberPassword, true); assert.equal(fresh.page.data.agreed, false);
  assert.equal(fresh.wx.getStorageSync('salesApiAuth'), ''); assert.equal(fresh.wx.getStorageSync('salesSession'), '');
});
test('prefix suggestions and account switching preserve source username and phone semantics', async () => {
  const x = fixture(); await submit(x, 'synthetic_a'); x.page.switchAccount();
  assert.equal(x.page.data.password, ''); assert.equal(x.page.data.rememberPassword, false);
  await submit(x, 'synthetic_b', 'Synthetic-Only-B'); x.page.switchAccount();
  x.page.inputAccount({detail: {value: 'synthetic_'}});
  assert.deepEqual(Array.from(x.page.data.accountSuggestions), ['synthetic_b', 'synthetic_a']); assert.equal(x.page.data.password, '');
  x.page.selectAccountSuggestion({currentTarget: {dataset: {account: 'synthetic_a'}}});
  assert.equal(x.page.data.password, 'Synthetic-Only-A'); assert.equal(x.page.data.passwordVisible, false);
  x.page.toggleRememberPassword(); assert.equal(x.remembered.read('/api/v1', 'SYNTHETIC_A'), null);
  assert.equal(x.remembered.read('/api/v1', 'synthetic_b').password, 'Synthetic-Only-B');
  await submit(x, '13900000000', 'Synthetic-Only-Phone', false);
  assert.equal(x.calls.at(-1)[1], '13900000000'); assert.equal(x.calls[0][1], 'SYNTHETIC_A');
});
test('saved entries are isolated by Web mode, origin and API base', async () => {
  const x = fixture(); await submit(x);
  for (const config of [{mode: 'preview'}, {origin: 'http://localhost:5187'}, {apiBase: '/other/api/v1'}]) {
    const other = fixture({local: x.local, ...config}); other.page.onLoad(); assert.equal(other.page.data.password, '');
  }
  const preview = fixture({local: x.local, mode: 'preview'}); await submit(preview, 'PREVIEW_ACCOUNT', 'Synthetic-Preview-Only');
  const original = fixture({local: x.local}); original.page.onLoad(); assert.equal(original.page.data.account, 'SYNTHETIC_A');
});
test('401 clears only the failed saved account; a network failure preserves saved entries', async () => {
  const seed = fixture(); await submit(seed); await submit(seed, 'SYNTHETIC_B', 'Synthetic-Only-B');
  const failed = fixture({local: seed.local, login: async () => {throw Object.assign(new Error('synthetic denied'), {statusCode: 401});}});
  failed.page.inputAccount({detail: {value: 'synthetic_a'}}); failed.page.data.agreed = true; await failed.page.submitLogin();
  assert.equal(failed.remembered.read('/api/v1', 'synthetic_a'), null); assert.ok(failed.remembered.read('/api/v1', 'SYNTHETIC_B'));
  const network = fixture({local: seed.local, login: async () => {throw new Error('synthetic network');}});
  network.page.onLoad(); network.page.data.agreed = true; await network.page.submitLogin(); assert.ok(network.remembered.read('/api/v1', 'SYNTHETIC_B'));
});
test('forced password change remembers only the successfully changed password', async () => {
  const x = fixture({login: async () => ({role: 'sales', account: 'SYNTHETIC_A', mustChangePassword: true})});
  await submit(x); assert.equal(x.wx.getStorageSync(KEY), ''); assert.equal(x.page.data.mustChangePassword, true);
  x.page.setData({newPassword: 'Synthetic-New-Only'}); await x.page.submitPasswordChange();
  assert.equal(x.remembered.read('/api/v1', 'SYNTHETIC_A').password, 'Synthetic-New-Only'); assert.equal(x.page.data.password, '');
});
test('unavailable persistent storage reports opt-in failure without breaking successful login', async () => {
  const local = {getItem: () => null, setItem() {throw Error('synthetic quota');}, removeItem() {throw Error('synthetic denied');}};
  const x = fixture({local}); await submit(x); assert.ok(x.app.globalData.session); assert.equal(x.page.data.rememberPassword, false); assert.match(x.notices[0], /未能记住/);
});
function taskFixture(self = false) {
  let user = 'owner', task = {id: '11111111-1111-4111-8111-111111111111', status: 'pending_execution', creator_user_ref_id: self ? 'owner' : 'creator', creator_name: '合成发起人', assignees: [{user_id: 'owner', name: '合成接收人', responsibility: 'owner'}], version_no: 3, events: []};
  const calls = [], stored = [], modals = [];
  const api = {getTask: async () => structuredClone(task), completeTask: async (id, note, version) => {
    calls.push({event: 'complete', note, version}); assert.equal(version, task.version_no);
    task = {...task, status: self ? 'completed' : 'pending_review', completion_note: note, version_no: version + 1};
    task.events.push({id: String(version), event_type: self ? 'complete' : 'submit_completion', note}); return structuredClone(task);
  }, respondTask: async (id, event, note, version) => {
    calls.push({event, note, version}); assert.equal(version, task.version_no);
    task = {...task, status: event === 'approve_completion' ? 'completed' : 'in_progress', last_event_type: event, completion_review_note: note, version_no: version + 1};
    task.events.push({id: String(version), event_type: event, note}); return structuredClone(task);
  }};
  let definition;
  vm.runInNewContext(read('source/miniprogram/pages/task-detail/index.js'), {Page: value => {definition = value;}, wx: {showToast() {}, showModal: value => modals.push(value), setStorageSync: (...args) => stored.push(args), vibrateShort() {}}, getApp: () => ({globalData: {session: {userId: user, role: 'sales'}}}), setTimeout() {}, require: name => name.includes('apiClient') ? api : name.includes('/access') ? {can: () => true, identity: session => session.userId} : name.includes('statusLight') ? {taskLight: () => ({tone: 'yellow'})} : {}});
  const page = {...definition, data: {...definition.data}, setData(value) {Object.assign(this.data, value);}}; page.data.taskId = task.id;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  return {page, calls, stored, modals, async as(next) {user = next; page.loadTask(); await tick();}, async confirm() {modals.at(-1).success({confirm: true}); await tick();}};
}
test('completion requires creator review, permits rejection/resubmission and preserves versions/history', async () => {
  const x = taskFixture(); await x.as('owner'); x.page.markCompleted(); assert.equal(x.modals.length, 0);
  x.page.setData({completionNote: '合成交付第一版'}); x.page.markCompleted(); await x.confirm();
  assert.equal(x.page.data.task.status, 'pending_review'); assert.equal(x.page.data.task.canComplete, false); assert.equal(x.stored.length, 0);
  await x.as('observer'); assert.equal(x.page.data.task.canReview, false); x.page.reviewCompletion({currentTarget: {dataset: {event: 'approve_completion'}}}); assert.equal(x.calls.length, 1);
  await x.as('creator'); assert.equal(x.page.data.task.canReview, true);
  x.page.reviewCompletion({currentTarget: {dataset: {event: 'reject_completion'}}}); assert.equal(x.modals.length, 1);
  x.page.setData({reviewNote: '补齐合成验证'}); x.page.reviewCompletion({currentTarget: {dataset: {event: 'reject_completion'}}}); await x.confirm();
  await x.as('owner'); assert.equal(x.page.data.task.reviewRejected, true); assert.equal(x.page.data.task.reviewNote, '补齐合成验证');
  x.page.setData({completionNote: '合成交付第二版'}); x.page.markCompleted(); await x.confirm();
  await x.as('creator'); x.page.reviewCompletion({currentTarget: {dataset: {event: 'approve_completion'}}}); await x.confirm();
  assert.equal(x.page.data.task.status, 'completed'); assert.equal(x.page.data.task.history.length, 4);
  assert.deepEqual(x.calls.map(call => call.version), [3, 4, 5, 6]); assert.deepEqual(x.calls.map(call => call.event), ['complete', 'reject_completion', 'complete', 'approve_completion']);
});
test('self-created/self-owned tasks complete directly after mandatory explanation', async () => {
  const x = taskFixture(true); await x.as('owner'); assert.equal(x.page.data.task.selfAssigned, true);
  x.page.markCompleted(); assert.equal(x.calls.length, 0);
  x.page.setData({completionNote: '合成自建任务已交付'}); x.page.markCompleted(); await x.confirm();
  assert.equal(x.page.data.task.status, 'completed'); assert.equal(x.stored.length, 1); assert.equal(x.stored[0][0], 'lastCompletedTaskId');
});
