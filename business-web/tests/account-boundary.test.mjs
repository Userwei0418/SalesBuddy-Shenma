import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

// Exercise the generated Web module, including its build-time session guard.
// There is no browser, HTTP client, preview backend or real account in this VM.
const webRoot = fileURLToPath(new URL('../', import.meta.url));
const bundleScript = fs.readFileSync(path.join(webRoot, 'dist/bundle.js'), 'utf8');
const flush = async () => { for (let i = 0; i < 24; i++) await Promise.resolve(); };

function syntheticAuth(id, suffix = '') {
  return {
    access_token: `SYNTHETIC-${id}${suffix}`,
    refresh_token: `SYNTHETIC-REFRESH-${id}${suffix}`,
    auth_method: 'password',
    actor: {workspace_id: 'synthetic-workspace', user_id: id, account_code: id,
      display_name: id, role: 'sales', role_name: '一线销售', scope_name: '仅本人',
      team_ids: ['synthetic-team'], team_names: ['合成团队'],
      capabilities: {'visit.create': true}, permission_version: 'synthetic-v1'},
  };
}

function harness() {
  const storage = new Map(), timers = [], pending = [], requests = [], cache = new Map();
  let app;
  const context = vm.createContext({window: {}, console,
    wx: {
      getStorageSync: key => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key),
      reLaunch() {},
      request(options) {
        assert.match(options.url, /^\/api\/v1\//, 'all requests remain synthetic and same-origin');
        pending.push(options);
        requests.push({url: options.url, method: options.method, token: options.header.Authorization || null});
      },
    },
    App: value => { app = value; },
    getCurrentPages: () => [],
    setTimeout: (callback, delay) => { timers.push({callback, delay}); return timers.length; },
    clearTimeout() {},
  });
  vm.runInContext(bundleScript, context, {filename: 'dist/bundle.js'});
  const modules = context.window.SALES_BUNDLE.modules;
  function load(id, from = '') {
    id = path.posix.normalize(id.startsWith('.') ? path.posix.join(path.posix.dirname(from), id) : id).replace(/\.js$/, '');
    if (cache.has(id)) return cache.get(id).exports;
    assert.equal(typeof modules[id], 'string', `generated module exists: ${id}`);
    const module = {exports: {}}; cache.set(id, module);
    const factory = vm.runInContext(`(function(require,module,exports){${modules[id]}\n})`, context, {filename: `generated/${id}.js`});
    factory(specifier => load(specifier, id), module, module.exports);
    return module.exports;
  }
  const api = load('utils/apiClient'); load('app');
  function respond(endpoint, statusCode, data) {
    const index = pending.findIndex(options => options.url === '/api/v1' + endpoint);
    assert.notEqual(index, -1, `expected pending synthetic request: ${endpoint}`);
    pending.splice(index, 1)[0].success({statusCode, data});
  }
  async function login(id, suffix = '') {
    const flight = app.loginWithApi('sales', id, 'SYNTHETIC-PASSWORD');
    respond('/auth/password/login', 200, syntheticAuth(id, suffix));
    await flight;
  }
  async function logout() {
    app.logout();
    respond('/auth/logout', 200, {});
    await flush();
  }
  async function beginRun(status = 'running') {
    const outcome = api.runAgent('visit_entry', '合成拜访内容', 'synthetic-customer')
      .then(value => ({value}), error => ({error}));
    respond('/conversations', 200, {id: 'synthetic-conversation-A'}); await flush();
    respond('/conversations/synthetic-conversation-A/messages', 200, {run_id: 'synthetic-run-A'}); await flush();
    respond('/agent/runs/synthetic-run-A', 200, {id: 'synthetic-run-A', status}); await flush();
    return {outcome};
  }
  async function poll(delay = 700) {
    assert.equal(timers.length, 1, 'one deferred Agent poll');
    const timer = timers.shift(); assert.equal(timer.delay, delay);
    timer.callback(); await flush();
  }
  const runRequests = () => requests.filter(request => request.url === '/api/v1/agent/runs/synthetic-run-A');
  return {api, app, pending, requests, timers, login, logout, beginRun, poll, respond, runRequests};
}

for (const status of ['pending', 'running']) {
  test(`generated ${status} Agent poll rejects after A logs out and B logs in`, async () => {
    const h = harness(); await h.login('A');
    const {outcome} = await h.beginRun(status);
    assert.equal(h.runRequests()[0].token, 'Bearer SYNTHETIC-A');
    await h.logout(); await h.login('B');
    await h.poll();
    assert.equal((await outcome).error?.code, 'SESSION_CHANGED');
    assert.equal(h.runRequests().length, 1, 'no old run request may use the new account token');
    assert.equal(h.timers.length, 0, 'old poll does not reschedule');
    assert.equal(h.api.getAuth().actor.user_id, 'B', 'old rejection keeps the new session');

    const home = h.api.getAssistantHome();
    assert.equal(h.requests.at(-1).token, 'Bearer SYNTHETIC-B');
    h.respond('/assistant/home', 200, {synthetic_owner: 'B'});
    assert.equal((await home).synthetic_owner, 'B', 'the new account can still issue its own request');
  });
}

test('generated Agent poll rejects after signing out without a replacement login', async () => {
  const h = harness(); await h.login('A'); const {outcome} = await h.beginRun();
  await h.logout(); await h.poll();
  assert.equal((await outcome).error?.code, 'SESSION_CHANGED');
  assert.equal(h.runRequests().length, 1, 'no anonymous poll follows logout');
  assert.equal(h.api.getAuth(), null);
});

test('generated Agent poll rejects a new session even when the account is the same', async () => {
  const h = harness(); await h.login('A'); const {outcome} = await h.beginRun();
  await h.logout(); await h.login('A', '-RELOGIN'); await h.poll();
  assert.equal((await outcome).error?.code, 'SESSION_CHANGED');
  assert.equal(h.runRequests().length, 1, 'identity equality must not revive the previous session');
  assert.equal(h.api.getAuth().access_token, 'SYNTHETIC-A-RELOGIN');
});

test('token refresh within the same session keeps the generated Agent poll working', async () => {
  const h = harness(); await h.login('A'); const {outcome} = await h.beginRun();
  const actorFlight = h.api.getCurrentActor();
  h.respond('/auth/me', 401, {detail: 'synthetic expired access token'}); await flush();
  h.respond('/auth/refresh', 200, syntheticAuth('A', '-ROTATED')); await flush();
  h.respond('/auth/me', 200, {actor: syntheticAuth('A').actor}); await actorFlight;
  await h.poll();
  assert.equal(h.runRequests().length, 2);
  assert.equal(h.runRequests()[1].token, 'Bearer SYNTHETIC-A-ROTATED');
  h.respond('/agent/runs/synthetic-run-A', 200, {id: 'synthetic-run-A', status: 'waiting_human', result: {synthetic: true}});
  const result = await outcome;
  assert.equal(result.error, undefined); assert.equal(result.value.status, 'waiting_human');
  assert.equal(result.value.result.synthetic, true); assert.equal(h.timers.length, 0);
});

test('generated advice poll rejects a new session for the same account', async () => {
  const h = harness(); await h.login('A');
  const outcome = h.api.queryBusinessAdvice('customer', 'synthetic-customer')
    .then(value => ({value}), error => ({error}));
  h.respond('/advice', 200, {id: 'synthetic-advice-A', status: 'running'}); await flush();
  await h.logout(); await h.login('A', '-RELOGIN'); await h.poll(1200);
  assert.equal((await outcome).error?.code, 'SESSION_CHANGED');
  assert.equal(h.requests.filter(request => request.url === '/api/v1/advice/synthetic-advice-A').length, 0);
  assert.equal(h.api.getAuth().access_token, 'SYNTHETIC-A-RELOGIN');
  assert.equal(h.timers.length, 0);
});

test('generated advice poll completes queued and running work in its original session', async () => {
  const h = harness(); await h.login('A');
  const outcome = h.api.queryBusinessAdvice('customer', 'synthetic-customer');
  h.respond('/advice', 200, {id: 'synthetic-advice-A', status: 'queued'}); await flush();
  await h.poll(1200);
  h.respond('/advice/synthetic-advice-A', 200, {id: 'synthetic-advice-A', status: 'running'}); await flush();
  await h.poll(2000);
  h.respond('/advice/synthetic-advice-A', 200, {id: 'synthetic-advice-A', status: 'succeeded', synthetic: true});
  assert.equal((await outcome).synthetic, true);
  const polls = h.requests.filter(request => request.url === '/api/v1/advice/synthetic-advice-A');
  assert.equal(polls.length, 2); assert.ok(polls.every(request => request.token === 'Bearer SYNTHETIC-A'));
  assert.equal(h.timers.length, 0);
});
