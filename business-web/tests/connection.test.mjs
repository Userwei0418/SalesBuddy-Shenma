import {test} from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createSalesWebServer} from '../server.mjs';

const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', () => resolve('http://127.0.0.1:' + server.address().port)));
const close = server => new Promise(resolve => {server.close(resolve); server.closeAllConnections();});

test('connection status verifies upstream readiness, coalesces reads, and exposes only public flags', async () => {
  let requests = 0;
  const upstream = http.createServer((req, res) => {
    requests++; assert.equal(req.url, '/api/v1/health/ready'); assert.equal(req.headers.authorization, undefined);
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({status: 'ok', checks: {database: true, model_gateway_configured: true, auth_configured: true, private_config: 'not-for-browser'}, extra: 'not-for-browser'}));
  });
  const target = await listen(upstream);
  const server = createSalesWebServer({target: target + '/api/v1', environment: '测试业务服务'}), address = await listen(server);
  try {
    const results = await Promise.all([1, 2, 3].map(() => fetch(address + '/connection-status').then(r => r.json())));
    assert.equal(requests, 1);
    for (const status of results) {
      assert.equal(status.configured, true); assert.equal(status.reachable, true); assert.equal(status.environment, '测试业务服务');
      assert.deepEqual(status.checks, {database: true, model_gateway_configured: true, auth_configured: true});
      assert.ok(Number.isFinite(Date.parse(status.checkedAt))); assert.equal(JSON.stringify(status).includes('not-for-browser'), false);
    }
    assert.equal((await fetch(address + '/connection-status', {method: 'POST'})).status, 405);
  } finally {await close(server); await close(upstream);}
});

test('unconfigured, unavailable and timed-out connections never report a ready backend', async () => {
  const empty = createSalesWebServer({target: ''}), emptyAddress = await listen(empty);
  try {const status = await (await fetch(emptyAddress + '/connection-status')).json(); assert.equal(status.configured, false); assert.equal(status.reachable, false);} finally {await close(empty);}
  const upstream = http.createServer((req, res) => {if (req.url.startsWith('/bad/')) {res.writeHead(503); res.end('{}');}}), target = await listen(upstream);
  try {
    for (const path of ['/bad', '/silent']) {
      const server = createSalesWebServer({target: target + path, healthTimeout: 25}), address = await listen(server);
      try {const status = await (await fetch(address + '/connection-status')).json(); assert.equal(status.configured, true); assert.equal(status.reachable, false);} finally {await close(server);}
    }
  } finally {await close(upstream);}
});
