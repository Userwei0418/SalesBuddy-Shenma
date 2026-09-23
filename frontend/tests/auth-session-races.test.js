const test = require('node:test');
const assert = require('node:assert/strict');

const tick = () => new Promise(resolve => setImmediate(resolve));
const changedSession = { code: 'SESSION_CHANGED' };
function auth(user, token = user) {
  return { access_token: `access-${token}`, refresh_token: `refresh-${token}`,
    actor: { workspace_id: 'workspace', user_id: user, role: 'sales' } };
}
function harness() {
  const storage = new Map(), requests = [], uploads = [];
  global.wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    request: options => { requests.push(options); },
    uploadFile: options => {
      uploads.push(options);
      return { onProgressUpdate: callback => { options.progress = callback; } };
    },
  };
  delete require.cache[require.resolve('../miniprogram/utils/apiClient')];
  const api = require('../miniprogram/utils/apiClient');
  return { api, storage, requests, uploads,
    reply: (request, statusCode, data = {}) => request.success({ statusCode, data }),
    uploadReply: (request, statusCode, data = {}) => request.success({ statusCode, data: JSON.stringify(data) }),
    refreshes: () => requests.filter(request => request.url.endsWith('/auth/refresh')),
  };
}

test('a late refresh cannot replace a newly logged-in account or retry its old request', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const original = h.api.request({ path: '/tasks' });
  const rejected = assert.rejects(original, changedSession);
  h.reply(h.requests[0], 401); await tick();
  const oldRefresh = h.refreshes()[0];
  const login = h.api.loginWithAccount('b', 'test-password', 'sales');
  h.reply(h.requests.at(-1), 200, auth('b')); await login;
  h.reply(oldRefresh, 200, auth('a', 'renewed-a'));
  await rejected;
  assert.equal(h.api.getAuth().actor.user_id, 'b');
  assert.equal(h.requests.length, 3, 'the old business request was never retried');
});

test('logout invalidates an outstanding refresh without resurrecting its session', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const rejected = assert.rejects(h.api.request({ path: '/tasks' }), changedSession);
  h.reply(h.requests[0], 401); await tick();
  const oldRefresh = h.refreshes()[0], logout = h.api.logout();
  h.reply(h.requests.at(-1), 200);
  h.reply(oldRefresh, 200, auth('a', 'renewed-a'));
  await Promise.all([logout, rejected]);
  assert.equal(h.api.getAuth(), null);
  assert.equal(h.requests.filter(request => request.url.endsWith('/tasks')).length, 1);
});

test('logging back into the same account still discards the previous session refresh', async () => {
  const h = harness(); h.api.saveAuth(auth('a', 'old-a'));
  const rejected = assert.rejects(h.api.request({ path: '/tasks' }), changedSession);
  h.reply(h.requests[0], 401); await tick();
  const oldRefresh = h.refreshes()[0], logout = h.api.logout();
  h.reply(h.requests.at(-1), 200); await logout;
  const login = h.api.loginWithAccount('a', 'test-password', 'sales');
  h.reply(h.requests.at(-1), 200, auth('a', 'new-session')); await login;
  h.reply(oldRefresh, 200, auth('a', 'old-session-rotated')); await rejected;
  assert.equal(h.api.getAuth().access_token, 'access-new-session');
});

test('a delayed 401 after a direct session replacement does not refresh the new account', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const rejected = assert.rejects(h.api.request({ path: '/customers' }), changedSession);
  h.api.saveAuth(auth('b'));
  h.reply(h.requests[0], 401); await rejected;
  assert.equal(h.refreshes().length, 0);
  assert.equal(h.api.getAuth().actor.user_id, 'b');
});

test('old successful data and mutation responses cannot be delivered into a new session', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const read = assert.rejects(h.api.request({ path: '/customers' }), changedSession);
  const mutation = assert.rejects(h.api.request({ path: '/tasks', method: 'POST', data: { description: 'old task' } }), changedSession);
  h.api.saveAuth(auth('b'));
  h.reply(h.requests[0], 200, { items: [{ id: 'a-private-customer' }] });
  h.reply(h.requests[1], 201, { id: 'a-created-task' });
  await Promise.all([read, mutation]);
});

test('same-account relogin gets a new in-flight request while retaining the uncertain idempotency key', async () => {
  const h = harness(); h.api.saveAuth(auth('a', 'old'));
  const options = { path: '/tasks', method: 'POST', data: { description: 'one intended task' } };
  const rejected = assert.rejects(h.api.request(options), changedSession);
  const first = h.requests[0];
  h.api.saveAuth(auth('a', 'new'));
  const next = h.api.request(options), second = h.requests[1];
  assert.equal(h.requests.length, 2);
  assert.equal(second.header.Authorization, 'Bearer access-new');
  assert.equal(first.header['Idempotency-Key'], second.header['Idempotency-Key']);
  h.reply(first, 201, { id: 'same-server-task' }); await rejected;
  const doubleTap = h.api.request(options);
  assert.equal(h.requests.length, 2, 'settling the old flight must not remove the new flight');
  h.reply(second, 201, { id: 'same-server-task' });
  assert.deepEqual(await Promise.all([next, doubleTap]), [{ id: 'same-server-task' }, { id: 'same-server-task' }]);
});

test('normal concurrent 401s share one refresh, including a late 401 after token rotation', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const first = h.api.request({ path: '/first' });
  const second = h.api.request({ path: '/second' });
  const late = h.api.request({ path: '/late' });
  h.reply(h.requests[0], 401); h.reply(h.requests[1], 401); await tick();
  assert.equal(h.refreshes().length, 1);
  h.reply(h.refreshes()[0], 200, auth('a', 'rotated')); await tick();
  h.reply(h.requests[2], 401); await tick();
  assert.equal(h.refreshes().length, 1, 'late 401 can retry with the already rotated token');
  const retries = h.requests.slice(4);
  assert.equal(retries.length, 3);
  for (const request of retries) {
    assert.equal(request.header.Authorization, 'Bearer access-rotated');
    h.reply(request, 200, { ok: true });
  }
  assert.deepEqual(await Promise.all([first, second, late]), [{ ok: true }, { ok: true }, { ok: true }]);
});

test('settling an old refresh cannot clear the new account shared refresh', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const oldRejected = assert.rejects(h.api.request({ path: '/old' }), changedSession);
  h.reply(h.requests[0], 401); await tick();
  const oldRefresh = h.refreshes()[0];
  h.api.saveAuth(auth('b'));
  const first = h.api.request({ path: '/new-first' });
  h.reply(h.requests.at(-1), 401); await tick();
  const currentRefresh = h.refreshes()[1];
  h.reply(oldRefresh, 200, auth('a', 'late')); await oldRejected;
  const second = h.api.request({ path: '/new-second' });
  h.reply(h.requests.at(-1), 401); await tick();
  assert.equal(h.refreshes().length, 2);
  h.reply(currentRefresh, 200, auth('b', 'rotated')); await tick();
  for (const request of h.requests.slice(-2)) {
    assert.equal(request.header.Authorization, 'Bearer access-rotated');
    h.reply(request, 200, { ok: true });
  }
  await Promise.all([first, second]);
  assert.equal(h.api.getAuth().actor.user_id, 'b');
});

test('only the latest login may establish a session, and logout invalidates a pending login', async () => {
  const h = harness();
  const first = assert.rejects(h.api.loginWithAccount('a', 'test-password', 'sales'), changedSession);
  const second = h.api.loginWithAccount('b', 'test-password', 'sales');
  h.reply(h.requests[1], 200, auth('b')); await second;
  h.reply(h.requests[0], 200, auth('a')); await first;
  assert.equal(h.api.getAuth().actor.user_id, 'b');
  const pending = assert.rejects(h.api.loginWithAccount('c', 'test-password', 'sales'), changedSession);
  const pendingRequest = h.requests.at(-1), logout = h.api.logout();
  h.reply(h.requests.at(-1), 200); await logout;
  h.reply(pendingRequest, 200, auth('c')); await pending;
  assert.equal(h.api.getAuth(), null);
});

for (const kind of ['audio', 'visit-file']) {
  test(`${kind} upload cannot refresh or return a previous user's result after switching accounts`, async () => {
    const h = harness(); h.api.saveAuth(auth('a'));
    const send = () => kind === 'audio'
      ? h.api.transcribeAudio('/tmp/test-only.wav', 'visit')
      : h.api.uploadVisitFile('/tmp/test-only.pdf', 'test-only.pdf');
    const rejected = assert.rejects(send(), changedSession);
    h.uploadReply(h.uploads[0], 401); await tick();
    const oldRefresh = h.refreshes()[0];
    h.api.saveAuth(auth('b'));
    h.reply(oldRefresh, 200, auth('a', 'late')); await rejected;
    assert.equal(h.uploads.length, 1);
    assert.equal(h.api.getAuth().actor.user_id, 'b');
    const staleResult = assert.rejects(send(), changedSession);
    h.api.saveAuth(auth('c'));
    h.uploadReply(h.uploads[1], 200, { text: 'private b content', id: 'b-file' });
    await staleResult;
  });
}

test('upload progress stops at a session boundary and normal uploads can refresh once', async () => {
  const h = harness(); h.api.saveAuth(auth('a'));
  const progress = [];
  const upload = h.api.uploadVisitFile('/tmp/test-only.pdf', 'test-only.pdf', event => progress.push(event.progress));
  h.uploads[0].progress({ progress: 10 });
  h.uploadReply(h.uploads[0], 401); await tick();
  h.reply(h.refreshes()[0], 200, auth('a', 'rotated')); await tick();
  assert.equal(h.uploads[1].header.Authorization, 'Bearer access-rotated');
  h.uploads[1].progress({ progress: 80 });
  h.uploadReply(h.uploads[1], 200, { id: 'real-upload-contract' });
  assert.deepEqual(await upload, { id: 'real-upload-contract' });
  h.api.saveAuth(auth('b'));
  h.uploads[1].progress({ progress: 100 });
  assert.deepEqual(progress, [10, 80]);
});
