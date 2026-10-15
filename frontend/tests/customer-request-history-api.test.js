const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function harness() {
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  const module = { exports: {} }, requests = [], storage = new Map();
  const result = { items: [], total: 3, summary: { total: 12, pending: 3, approved: 9 } };
  const wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), request: request => {
      requests.push(request); request.success({ statusCode: 200, data: result });
    } };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, wx, Map, Set, Date, require: name => require(path.resolve(path.dirname(filename), name)),
  });
  return { api: module.exports, requests, result };
}

test('本人历史保持旧默认且不携带部门或成员筛选', async () => {
  const { api, requests } = harness();
  for (const options of [{}, { team_id: 'old-team', member_id: 'old-member' },
    { scope: 'self', team_id: 'old-team', member_id: 'old-member' }]) {
    await api.listCustomerRequestHistory(options);
    const url = new URL(requests.at(-1).url);
    assert.equal(url.pathname, '/api/v1/customers/requests/history');
    assert.equal(url.searchParams.get('scope'), 'self');
    assert.equal(url.searchParams.has('team_id'), false);
    assert.equal(url.searchParams.has('member_id'), false);
  }
});

test('授权历史支持全范围、部门、成员及交集，保留服务端summary和分页', async () => {
  const { api, requests, result } = harness();
  const team = '10000000-0000-0000-0000-000000000001';
  const member = '20000000-0000-0000-0000-000000000001';
  for (const filters of [{}, { team_id: team }, { member_id: member }, { team_id: team, member_id: member }]) {
    const response = await api.listCustomerRequestHistory({ scope: 'team', ...filters,
      kind: 'claim', status: 'pending', q: '客户 & %26', limit: 10, offset: 20 });
    const url = new URL(requests.at(-1).url);
    assert.equal(url.searchParams.get('scope'), 'team');
    assert.equal(url.searchParams.get('team_id'), filters.team_id || null);
    assert.equal(url.searchParams.get('member_id'), filters.member_id || null);
    assert.equal(url.searchParams.get('q'), '客户 & %26');
    assert.equal(url.searchParams.get('status'), 'pending');
    assert.equal(url.searchParams.get('kind'), 'claim');
    assert.equal(url.searchParams.get('limit'), '10'); assert.equal(url.searchParams.get('offset'), '20');
    assert.deepEqual(JSON.parse(JSON.stringify(response)), result);
    assert.equal(response.total, 3); assert.equal(response.summary.total, 12);
  }
});
