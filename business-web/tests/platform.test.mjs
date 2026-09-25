import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import http from 'node:http';
import { readFile, mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createSalesWebServer } from '../server.mjs';

const script = await readFile(new URL('../browser-platform.js', import.meta.url), 'utf8');
function platform({ mode = 'live', fetchImpl = fetch, store = new Map(), preview } = {}) {
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) };
  const window = { SALES_MODE: mode, SalesPreview: preview, location: { href: 'http://localhost:5186/', origin: 'http://localhost:5186' } };
  const context = { window, sessionStorage: storage, Blob, File, FormData, URL, Headers, AbortController, queueMicrotask, setTimeout, clearTimeout, fetch: fetchImpl, navigator: {}, requestAnimationFrame: callback => callback(), screen: {} };
  vm.runInNewContext(script, context);
  const wx = {};
  window.SalesPlatform.install(wx);
  return { wx, register: window.SalesPlatform.registerLocalFile };
}
function request(wx, options, upload = false) {
  return new Promise((resolve, reject) => wx[upload ? 'uploadFile' : 'request']({ ...options, success: resolve, fail: reject }));
}
async function start(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${server.address().port}`;
}
async function close(server) { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }

test('live network preserves wx HTTP success semantics, auth and idempotency headers', async () => {
  let seen;
  const { wx } = platform({ fetchImpl: async (url, options) => { seen = { url, options }; return new Response(JSON.stringify({ detail: '版本已变化' }), { status: 409 }); } });
  const response = await request(wx, { url: '/api/v1/tasks/task/events', method: 'POST', data: { version_no: 3 }, header: { Authorization: 'Bearer local-test-token', 'Idempotency-Key': 'test-key' } });
  assert.equal(response.statusCode, 409);
  assert.equal(response.data.detail, '版本已变化');
  assert.equal(seen.options.headers.get('Authorization'), 'Bearer local-test-token');
  assert.equal(seen.options.headers.get('Idempotency-Key'), 'test-key');
  assert.equal(seen.options.body, '{"version_no":3}');
  assert.equal(seen.options.redirect, 'error');
});

test('GET appends filters and refuses upstream or non-API URLs before fetch', async () => {
  const calls = [];
  const { wx } = platform({ fetchImpl: async (url, options) => { calls.push({ url, options }); return new Response('{"items":[]}'); } });
  await request(wx, { url: '/api/v1/tasks?tab=pending', data: { completed_quarters: [1, 3], q: '客户' } });
  assert.deepEqual(calls[0].url.searchParams.getAll('completed_quarters'), ['1', '3']);
  assert.equal(calls[0].url.searchParams.get('q'), '客户');
  assert.equal(calls[0].options.body, undefined);
  await assert.rejects(request(wx, { url: 'https://elsewhere.invalid/api/v1/tasks' }), error => /本站/.test(error.errMsg));
  await assert.rejects(request(wx, { url: '/admin' }), error => /本站/.test(error.errMsg));
  assert.equal(calls.length, 1);
});

test('live and preview storage never share a session and cannot override API base', () => {
  const store = new Map();
  const live = platform({ store }).wx;
  const preview = platform({ store, mode: 'preview' }).wx;
  live.setStorageSync('salesApiAuth', { actor: { user_id: 'real-test' } });
  assert.equal(preview.getStorageSync('salesApiAuth'), '');
  preview.setStorageSync('salesApiAuth', { actor: { user_id: 'preview-test' } });
  assert.equal(live.getStorageSync('salesApiAuth').actor.user_id, 'real-test');
  live.setStorageSync('salesApiBaseUrl', 'https://elsewhere.invalid');
  assert.equal(live.getStorageSync('salesApiBaseUrl'), '/api/v1');
  assert.equal(preview.getStorageSync('salesApiBaseUrl'), '/api/v1');
});

test('preview dispatch is explicit and missing preview operations never hit live network', async () => {
  let fetches = 0;
  const configured = platform({ mode: 'preview', fetchImpl: () => { fetches++; }, preview: { request: options => options.success({ statusCode: 200, data: { source: 'preview' } }) } });
  assert.equal((await request(configured.wx, { url: '/api/v1/tasks' })).data.source, 'preview');
  const missing = platform({ mode: 'preview', fetchImpl: () => { fetches++; } });
  await assert.rejects(request(missing.wx, { url: '/api/v1/tasks' }), error => /未发送真实/.test(error.errMsg));
  assert.equal(fetches, 0);
});

test('upload sends a real multipart file, returns raw JSON text, and uses recorded MIME filename', async () => {
  let sent;
  const { wx, register } = platform({ fetchImpl: async (url, options) => { sent = options; return new Response('{"id":"import-local-test"}', { status: 201 }); } });
  const filePath = register(new File(['test audio bytes'], '拜访录音.webm', { type: 'audio/webm' }), { recorded: true });
  const response = await request(wx, { url: '/api/v1/visit-imports', filePath, name: 'file', header: { 'Content-Type': 'application/json' }, formData: { original_filename: '拜访录音.mp3' } }, true);
  assert.equal(typeof response.data, 'string');
  assert.equal(response.statusCode, 201);
  assert.equal(sent.headers.has('Content-Type'), false);
  assert.equal(sent.body.get('file').type, 'audio/webm');
  assert.equal(sent.body.get('file').name, '拜访录音.webm');
  assert.equal(sent.body.get('original_filename'), '拜访录音.webm');
  await assert.rejects(request(wx, { url: '/api/v1/visit-imports', filePath: 'missing' }, true), error => /文件已失效/.test(error.errMsg));
});

test('request and multipart upload abort/timeout call fail/complete once without false progress', async () => {
  const { wx, register } = platform({ fetchImpl: (url, options) => new Promise((resolve, reject) => {
    if (options.signal.aborted) return reject(new Error('aborted'));
    options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }) });
  const filePath = register(new File(['pending upload'], 'pending.txt'));
  for (const { timeout, upload } of [{ timeout: true }, { timeout: false }, { timeout: true, upload: true }, { timeout: false, upload: true }]) {
    let successes = 0, failures = 0, completions = 0, progress = 0;
    const outcome = await new Promise(resolve => {
      const task = wx[upload ? 'uploadFile' : 'request']({ url: upload ? '/api/v1/visit-imports' : '/api/v1/tasks', filePath, timeout: timeout ? 5 : 1000, success: () => successes++, fail: () => failures++, complete: value => { completions++; resolve(value); } });
      task.onProgressUpdate(() => progress++);
      if (!timeout) task.abort();
    });
    assert.match(outcome.errMsg, timeout ? /超时/ : /取消/);
    assert.equal(successes, 0); assert.equal(failures, 1); assert.equal(completions, 1);
    assert.equal(progress, 0);
  }
});

test('server has a clear unconfigured error and serves only the built static directory', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'sales-web-static-'));
  await writeFile(join(dir, 'index.html'), '<title>local test</title>');
  const server = createSalesWebServer({ target: '', staticDir: dir });
  const base = await start(server);
  try {
    assert.deepEqual(await (await fetch(base + '/health')).json(), { configured: false });
    const response = await fetch(base + '/api/v1/auth/password/login', { method: 'POST', body: '{}' });
    assert.equal(response.status, 503); assert.match((await response.json()).detail, /SALES_WEB_API_TARGET/);
    assert.match(await (await fetch(base)).text(), /local test/);
    assert.equal((await fetch(base + '/server.mjs')).status, 404);
  } finally { await close(server); await rm(dir, { recursive: true, force: true }); }
});

test('proxy preserves API path, status, JSON and multipart bytes without accepting another upstream', async () => {
  const received = [];
  const upstream = http.createServer(async (req, res) => {
    let body = ''; for await (const chunk of req) body += chunk;
    received.push({ url: req.url, method: req.method, headers: req.headers, body });
    res.writeHead(409, { 'Content-Type': 'application/json' }); res.end('{"detail":"版本冲突"}');
  });
  const target = await start(upstream);
  const proxy = createSalesWebServer({ target: target + '/api/v1' });
  const base = await start(proxy);
  try {
    assert.deepEqual(await (await fetch(base + '/healthz')).json(), { configured: true });
    const response = await fetch(base + '/api/v1/tasks/x/events?version=3', { method: 'POST', headers: { Authorization: 'Bearer local-test', 'Idempotency-Key': 'same-key', 'Content-Type': 'application/json' }, body: '{"event_type":"complete"}' });
    assert.equal(response.status, 409); assert.equal((await response.json()).detail, '版本冲突');
    assert.equal(received[0].url, '/api/v1/tasks/x/events?version=3');
    assert.equal(received[0].headers.authorization, 'Bearer local-test');
    assert.equal(received[0].headers['idempotency-key'], 'same-key');
    const form = new FormData(); form.append('file', new File(['test body'], 'test.txt')); form.append('original_filename', 'test.txt');
    await fetch(base + '/api/v1/visit-imports', { method: 'POST', body: form });
    assert.match(received[1].headers['content-type'], /multipart\/form-data; boundary=/);
    assert.match(received[1].body, /test body/);
    const blocked = await fetch(base + '/api/v1/tasks', { method: 'POST', headers: { Origin: 'https://elsewhere.invalid' }, body: '{}' });
    assert.equal(blocked.status, 403); assert.equal(received.length, 2);
  } finally { await close(proxy); await close(upstream); }
});

test('proxy rejects redirect responses and terminates a timed-out upstream', async () => {
  const upstream = http.createServer((req, res) => {
    if (req.url.endsWith('/redirect')) { res.writeHead(302, { Location: 'https://elsewhere.invalid' }); res.end(); }
  });
  const target = await start(upstream);
  const proxy = createSalesWebServer({ target, timeout: 20 });
  const base = await start(proxy);
  try {
    const redirect = await fetch(base + '/api/v1/redirect');
    assert.equal(redirect.status, 502); assert.match((await redirect.json()).detail, /重定向/);
    const timeout = await fetch(base + '/api/v1/slow');
    assert.equal(timeout.status, 504); assert.match((await timeout.json()).detail, /超时/);
  } finally { await close(proxy); await close(upstream); }
});

test('configured target must not embed credentials or query parameters', () => {
  assert.throws(() => createSalesWebServer({ target: 'http://user:pass@example.invalid/api/v1' }), /不含认证/);
  assert.throws(() => createSalesWebServer({ target: 'http://example.invalid/api/v1?target=x' }), /查询参数/);
});
