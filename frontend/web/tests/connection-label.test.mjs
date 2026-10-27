// Exercise the actual shell and connection-status functions in memory.
// No listener, real HTTP, account, model request or fixed build is used.
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const shell = fs.readFileSync(new URL('../shell.js', import.meta.url), 'utf8');
const update = shell.slice(shell.indexOf('  function updateConnection() {'), shell.indexOf('  // Production Web is served'));
assert.ok(update.includes('function updateConnection()'));

function label(connection, {session = null, preview = false} = {}) {
  const node = {dataset: {}}, frame = {}, calls = [];
  vm.runInNewContext(update + '\nupdateConnection();', {
    connection, preview, SalesRuntime: {app: {globalData: {session}}}, frame,
    $: () => node, paintFrame: () => calls.push('paint'), updateLocalLogin: () => calls.push('login'),
  });
  assert.equal(node.textContent, frame.statusText);
  assert.equal(node.dataset.state, frame.status);
  assert.deepEqual(calls, ['paint', 'login']);
  return {text: frame.statusText, state: frame.status};
}

for (const session of [null, {workspaceId: 'synthetic-company'}]) {
  test(`global gateway missing does not label company AI unavailable (${session ? 'signed in' : 'login'})`, () => {
    const connection = {configured: true, reachable: true, degraded: true, checks: {database: true, auth_configured: true, model_gateway_configured: false}};
    assert.deepEqual(label(connection, {session}), {text: session ? '服务已连接' : '服务已连接 · 请登录', state: 'ready'});
  });
}

test('connection and preview failures retain their own distinct states', () => {
  for (const [connection, options, expected] of [
    [null, {}, {text: '正在检查服务', state: 'checking'}],
    [{configured: false, reachable: false}, {}, {text: '服务尚未配置', state: 'unavailable'}],
    [{configured: true, reachable: false}, {session: {}}, {text: '服务暂不可用', state: 'unavailable'}],
    [{configured: false, reachable: false}, {preview: true}, {text: '合成数据演示', state: 'preview'}],
    [{configured: true, reachable: true}, {session: {}}, {text: '服务已连接', state: 'ready'}],
  ]) assert.deepEqual(label(connection, options), expected);
});

const server = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');
const check = server.slice(server.indexOf('  async function connectionStatus() {'), server.indexOf('  return http.createServer'));
assert.ok(check.includes('async function connectionStatus()'));

async function connectionStatus(checks, statusCode = 503) {
  let requested;
  const transport = {get(url, options, ready) {
    requested = {url: String(url), options};
    const request = new EventEmitter();
    request.destroy = () => {throw Error('Unexpected timeout in synthetic response');};
    queueMicrotask(() => {
      const response = new EventEmitter(); response.statusCode = statusCode; response.setEncoding = () => {};
      ready(response);
      response.emit('data', JSON.stringify({status: statusCode === 200 ? 'ok' : 'degraded', checks}));
      response.emit('end');
    });
    return request;
  }};
  const context = {upstream: new URL('http://synthetic.invalid/api/v1'), URL, http: transport, https: transport,
    previewOnly: false, environment: 'Synthetic service', connectionSnapshot: null, connectionFlight: null,
    setTimeout, clearTimeout, healthTimeout: 1000};
  const result = await vm.runInNewContext(check + '\nconnectionStatus();', context);
  assert.equal(requested.url, 'http://synthetic.invalid/api/v1/health/ready');
  assert.equal(requested.options.headers.Authorization, undefined);
  return result;
}

test('public connection metadata does not infer tenant AI status from the global gateway', async () => {
  const result = await connectionStatus({database: true, auth_configured: true, model_gateway_configured: false});
  assert.equal(result.reachable, true);
  assert.equal(result.degraded, true);
  assert.equal(result.checks.model_gateway_configured, false);
  assert.equal(result.detail, '业务服务已响应，登录后读取授权数据');
  assert.equal(label(result, {session: {}}).text, '服务已连接');
});

test('database or auth failure remains unavailable even with a configured model', async () => {
  for (const checks of [{database: false, auth_configured: true, model_gateway_configured: true}, {database: true, auth_configured: false, model_gateway_configured: true}]) {
    const result = await connectionStatus(checks);
    assert.equal(result.reachable, false);
    assert.equal(result.detail, '业务服务暂时不可用');
    assert.equal(label(result, {session: {}}).state, 'unavailable');
  }
});
