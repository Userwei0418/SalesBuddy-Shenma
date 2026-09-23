const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function scenario(statusAtTime) {
  let now = 0;
  const requests = [];
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../miniprogram/utils/apiClient.js'), 'utf8'), {
    module,
    require: (name) => name === '../config'
      ? { API_BASE_URL: 'https://synthetic.invalid/api/v1' }
      : { supports: () => false },
    Date: { now: () => now },
    setTimeout: (callback, delay) => { now += delay; queueMicrotask(callback); },
    wx: {
      getStorageSync: () => undefined,
      request: (options) => {
        requests.push({ method: options.method, url: options.url, data: options.data });
        let data;
        if (options.url.endsWith('/conversations')) data = { id: 'conversation-1' };
        else if (options.url.endsWith('/messages')) data = { run_id: 'run-1' };
        else data = { id: 'run-1', status: statusAtTime(now), result: { summary: '同一业务结果' } };
        options.success({ statusCode: 200, data });
      },
    },
  });
  return { api: module.exports, requests, elapsed: () => now };
}

for (const mode of ['chatbi', 'customer_chatbi']) {
  test(`${mode}: a fallback finishing after 30 seconds is accepted without another submission`, async () => {
    const s = scenario((now) => now >= 42000 ? 'succeeded' : 'running');
    const run = await (mode === 'chatbi'
      ? s.api.queryChatBI('合成问数')
      : s.api.runAgent(mode, '合成问数', 'synthetic-customer'));
    assert.equal(run.id, 'run-1');
    assert.equal(run.result.summary, '同一业务结果');
    assert.ok(s.elapsed() >= 42000);
    assert.equal(s.requests.filter((r) => r.method === 'POST').length, 2, 'one conversation and one message');
    assert.ok(s.requests.filter((r) => r.method === 'GET').every((r) => r.url.endsWith('/agent/runs/run-1')));
  });
}

test('chatbi: a terminal failure is reported immediately and a still-running job remains bounded', async () => {
  const failed = scenario((now) => now >= 1400 ? 'failed' : 'running');
  await assert.rejects(failed.api.queryChatBI('合成问数'), { code: 'RUN_FAILED' });
  assert.equal(failed.elapsed(), 1400);
  const stalled = scenario(() => 'running');
  await assert.rejects(stalled.api.queryChatBI('合成问数'), { code: 'RUN_TIMEOUT' });
  assert.ok(stalled.elapsed() >= 60000 && stalled.elapsed() < 61000);
  assert.equal(stalled.requests.filter((r) => r.method === 'POST').length, 2, 'timeout never recreates the job');
});
