const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('task page sends one bounded request with repeated quarter keys and no automatic continuation', async () => {
  const requests = [], storage = new Map(), module = { exports: {} };
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, Map, Set, Date, require: name => require(path.resolve(path.dirname(filename), name)),
    wx: { getStorageSync: k => storage.get(k), setStorageSync: (k,v) => storage.set(k,v), removeStorageSync: k => storage.delete(k),
      request: request => { requests.push(request); request.success({ statusCode: 200, data: { items: [], has_more: true, next_offset: 20 } }); } }
  });
  const api = module.exports;
  api.saveAuth({ access_token: 'test-only', actor: { workspace_id: 'w', user_id: 'u' } });
  await api.listTaskPage({ tab: 'completed', completed_year: 2026, completed_quarters: [1,3], member: '甲 & 乙' });
  assert.equal(requests.length, 1);
  const url = new URL(requests[0].url);
  assert.equal(url.pathname, '/api/v1/tasks');
  assert.equal(url.searchParams.get('page_size'), '20');
  assert.deepEqual(url.searchParams.getAll('completed_quarters'), ['1','3']);
  assert.equal(url.searchParams.get('member'), '甲 & 乙');
  assert.equal(typeof api.listTasks, 'function');
});
