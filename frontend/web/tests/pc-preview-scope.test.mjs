import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// 合成数据：一线销售只读本人团队（需求清单第 23 条），写操作与跨团队仍按原权限；商机筛选统计与活跃窗口与小程序 1.1.1 一致。
function workspace() {
  const storage = new Map();
  const window = {SALES_MODE: 'preview', setTimeout, clearTimeout, localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)}};
  vm.runInNewContext(fs.readFileSync(new URL('../preview-api.js', import.meta.url), 'utf8'), {window, URL, URLSearchParams, setTimeout, clearTimeout});
  return (role, path, method = 'GET', data) => new Promise(resolve => window.SalesPreview.request({url: 'https://offline.invalid/api/v1' + path, method, data, header: {Authorization: 'Bearer preview-access-' + role}, success: resolve}));
}
const ok = async promise => { const response = await promise; assert.equal(response.statusCode, 200, response.data.message); return response.data; };

test('frontline sales read their own team, not other teams, and cannot write teammates records', async () => {
  const read = workspace();
  const me = (await ok(read('sales', '/auth/me'))).actor;
  const team = me.team_ids[0];
  const own = await ok(read('sales', '/customer-assets/map?scope=self'));
  const teamView = await ok(read('sales', `/customer-assets/map?scope=team&team_id=${team}`));
  assert.ok(teamView.items.length > own.items.length, 'team view adds teammates customers');
  assert.ok(teamView.items.some(row => row.owner_id !== me.user_id));
  const peer = teamView.items.find(row => row.owner_id !== me.user_id);
  const visits = await ok(read('sales', `/visits?customer_id=${peer.id}`));
  assert.ok(visits.items.length > 0, 'teammate customer timeline is readable');
  await ok(read('sales', `/customers/${peer.id}/overview`));
  const directory = await ok(read('sales', '/directory/members'));
  assert.ok(directory.items.some(person => person.id !== me.user_id));
  const teams = await ok(read('manager', '/directory/teams'));
  const other = teams.teams.find(row => row.id !== team);
  assert.equal((await read('sales', `/customer-assets/map?scope=team&team_id=${other.id}`)).statusCode, 403);
  const write = await read('sales', `/customers/${peer.id}/opportunities`, 'POST', {name: '不应成功', amount: 1});
  assert.notEqual(write.statusCode, 200);
  const opportunities = await ok(read('sales', `/opportunities?scope=team&team_id=${team}&include_closed=true&page_size=100`));
  assert.ok(opportunities.items.some(row => row.owner_id !== me.user_id));
});

test('opportunity list returns the filtered overview and honours the activity window', async () => {
  const read = workspace();
  const year = new Date(Date.now() + 28800000).getUTCFullYear();
  const data = await ok(read('supervisor', `/opportunities?include_closed=true&include_overview=true&overview_year=${year}&page_size=20`));
  assert.equal(data.overview.definition_version, 'opportunity_filtered_overview_v1');
  assert.equal(data.overview.metrics.total, data.summary.total);
  const today = await ok(read('supervisor', '/opportunities?include_closed=true&activity_window=today&page_size=100'));
  const all = await ok(read('supervisor', '/opportunities?include_closed=true&page_size=100'));
  assert.ok(today.summary.total > 0 && today.summary.total < all.summary.total);
  const map = await ok(read('supervisor', '/customer-assets/map?activity_window=today'));
  const year2 = await ok(read('supervisor', '/customer-assets/map'));
  assert.ok(map.items.length > 0 && map.items.length <= year2.items.length);
});

test('workbench fixtures apply owner scope before task totals and risk pagination', async () => {
  const read = workspace();
  const me = (await ok(read('sales', '/auth/me'))).actor;
  const personal = await ok(read('manager', `/tasks?tab=pending&member_id=${me.user_id}&page_size=1`));
  assert.equal(personal.items.length, 1);
  assert.ok(personal.summary.filtered_total > personal.items.length);
  assert.equal(personal.summary.pending_count, personal.summary.filtered_total);
  assert.equal(Number.isInteger(personal.summary.today_pending_count), true);
  assert.equal(Number.isInteger(personal.summary.today_completed_count), true);
  assert.equal(Number.isInteger(personal.summary.overdue_count), true);
  const riskPage = await ok(read('manager', `/risks?status=open&scope=person&member_id=${me.user_id}&page_size=1`));
  assert.equal(riskPage.items.length, 1);
  assert.ok(riskPage.total > 1);
  assert.equal(riskPage.total, riskPage.summary.open_count);
  const second = await ok(read('manager', `/risks?status=open&scope=person&member_id=${me.user_id}&page_size=1&offset=${riskPage.next_offset}`));
  assert.notEqual(second.items[0].id, riskPage.items[0].id);
  const managerRisks = await ok(read('manager', '/risks?status=open&scope=self'));
  assert.equal(managerRisks.total, 0);
  assert.equal((await read('manager', '/risks?scope=person')).statusCode, 422);
  assert.equal((await read('manager', '/risks?scope=team')).statusCode, 422);
});


test('normalized preview policy gives both frontline roles department reads and leaders company reads without export rights', async () => {
  const read = workspace();
  for (const role of ['sales', 'fde', 'supervisor', 'fde_lead', 'manager']) {
    const actor = (await ok(read(role, '/auth/me'))).actor;
    const grant = actor.permission_grants.find(g => g.permission_code === 'visit.read');
    assert.equal(grant.scope_code, ['sales', 'fde'].includes(role) ? 'teams' : 'workspace');
    assert.equal(actor.client_export_permissions['visit.export'], false);
    const directory = await ok(read(role, '/directory/members?permission=visit.read&include_stats=false'));
    assert.ok(directory.items.some(person => person.role === 'sales'));
    assert.ok(directory.items.some(person => person.role === 'fde'));
    assert.equal((await read(role, '/directory/members?permission=customer.update')).statusCode, 422);
    const range = await ok(read(role, '/visits/range?scope=all&page_size=100'));
    assert.ok(range.items.some(row => row.recorder_id !== actor.user_id));
    if (role === 'sales') {
      const peer = (await ok(read(role, '/opportunities?scope=team&include_closed=true&page_size=100'))).items.find(row => row.owner_id !== actor.user_id);
      assert.equal(peer.can_edit, false);
    }
  }
});

test('claim request history uses authorized applicants and keeps status summary outside pagination', async () => {
  const read = workspace();
  const pool = await ok(read('sales', '/customers/claim-pool?page_size=100'));
  const available = pool.items.find(row => row.can_claim);
  assert.ok(available);
  const claim = await ok(read('sales', `/customers/${available.id}/claims`, 'POST', {}));
  const me = (await ok(read('sales', '/auth/me'))).actor;
  const history = await ok(read('sales', '/customers/requests/history?scope=self'));
  assert.ok(history.items.some(row => row.id === claim.id));
  assert.equal(history.summary.pending, 1);
  assert.equal(history.items[0].can_read_customer, (await read('sales', `/customers/${available.id}/overview`)).statusCode === 200);
  const hidden = await ok(read('sales', '/customers/requests/history?scope=self&status=approved'));
  assert.equal(hidden.total, 0);
  assert.equal(hidden.summary.pending, 1);
  const trailing = await ok(read('supervisor', `/customers/requests/history?scope=team&member_id=${me.user_id}&offset=20`));
  assert.equal(trailing.total, 1);
  assert.equal(trailing.items.length, 0);
  assert.equal((await read('sales', '/customers/requests/history?scope=team&member_id=00000001-0000-4000-8000-999999999999')).statusCode, 403);
});
