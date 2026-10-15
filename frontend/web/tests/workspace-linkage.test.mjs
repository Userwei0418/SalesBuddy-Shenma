import test from 'node:test';
import assert from 'node:assert/strict';
import {workspacePath, recordSelection, readScope} from '../department-ui/pc/workspace-selection.js';
import {loadWorkspaceTasks, loadWorkspaceRisks, taskScopeParams, riskScopeParams} from '../department-ui/pc/workspace-data.js';
import {loadActiveOpportunities, normalizeVisit, groupBy} from '../department-ui/pc/data.js';
import {periodRange} from '../department-ui/pc/period.js';

test('every workbench period and scope survives customer/opportunity/report navigation', () => {
  for (const period of ['day', 'week', 'biweek', 'month', 'quarter']) {
    for (const scope of [{kind: 'self'}, {kind: 'team', teamId: 't1'}, {kind: 'member', memberId: 'm2'}]) {
      const range = periodRange(period);
      for (const view of ['customer', 'opportunity']) {
        const query = Object.fromEntries(new URLSearchParams(workspacePath('visit-records', range, scope, {view, linked: '1'}).split('?')[1]));
        assert.deepEqual(recordSelection(query), {period, scope, view, linkedOnly: true});
      }
      const query = Object.fromEntries(new URLSearchParams(workspacePath('work-report', range, scope).split('?')[1]));
      assert.equal(query.period, period);
      assert.deepEqual(readScope(query), scope);
      assert.ok(range.name, 'every period has a display name');
    }
  }
});

test('personal tasks explicitly constrain owner, and teams use stable IDs', () => {
  assert.deepEqual(taskScopeParams({kind: 'self'}, {userId: 'me'}), {view: 'self', member_id: 'me'});
  assert.deepEqual(taskScopeParams({kind: 'team', teamId: 't1'}), {view: 'team', team_id: 't1'});
  assert.deepEqual(taskScopeParams({kind: 'member', memberId: 'm2'}), {view: 'team', member_id: 'm2'});
  assert.deepEqual(riskScopeParams({kind: 'member', memberId: 'm2'}), {scope: 'person', member_id: 'm2'});
  assert.deepEqual(riskScopeParams({kind: 'team', teamId: 'all'}), {scope: 'all'});
  assert.deepEqual(riskScopeParams({kind: 'team', teamId: 't1'}), {scope: 'team', team_id: 't1'});
  assert.throws(() => riskScopeParams({kind: 'member'}), /选择成员/);
});

test('task and risk totals come from the full server scope, not the current page', async () => {
  const previous = globalThis.SalesRuntime, calls = [];
  globalThis.SalesRuntime = {app: {globalData: {session: {userId: 'me'}}}, require: () => ({
    listTaskPage: async params => { calls.push(params); return {items: [{id: 'task1', description: '跟进客户'}], has_more: true,
      summary: {filtered_total: 131, pending_count: 131, overdue_count: 70, today_pending_count: 12, today_completed_count: 8}}; },
    request: async request => { calls.push(request); return {items: [{id: 'risk1', title: '延期'}], total: 125, has_more: true}; },
  })};
  try {
    const tasks = await loadWorkspaceTasks({kind: 'self'}, {offset: 50});
    assert.equal(tasks.total, 131);
    assert.equal(tasks.counts.overdue, 70);
    assert.equal(tasks.counts.todayCompleted, 8);
    assert.equal(calls[0].member_id, 'me');
    assert.equal(calls[0].offset, 50);
    const risks = await loadWorkspaceRisks({kind: 'team', teamId: 't1'}, {offset: 100});
    assert.equal(risks.total, 125);
    const query = new URLSearchParams(calls[1].path.split('?')[1]);
    assert.equal(query.get('team_id'), 't1');
    assert.equal(query.get('status'), 'open');
    assert.equal(query.get('offset'), '100');
  } finally { globalThis.SalesRuntime = previous; }
});

test('missing summary and forbidden requests fail visibly instead of producing zero', async () => {
  const previous = globalThis.SalesRuntime;
  globalThis.SalesRuntime = {app: {globalData: {session: {userId: 'me'}}}, require: () => ({
    listTaskPage: async () => ({items: []}), request: async () => { throw Error('无权读取'); },
  })};
  try {
    await assert.rejects(loadWorkspaceTasks({kind: 'self'}), /接口尚未就绪/);
    await assert.rejects(loadWorkspaceRisks({kind: 'self'}), /无权读取/);
  } finally { globalThis.SalesRuntime = previous; }
});

test('active opportunity detail includes later pages and does not filter by recorder ownership', async () => {
  const previous = globalThis.SalesRuntime, calls = [];
  const visit = normalizeVisit({id: 'v1', customer_id: 'c1', recorder_id: 'me', linked_opportunities: [{id: 'o2', name: '联合拜访商机'}, {id: 'o3', name: '另一商机'}]});
  assert.equal(groupBy([visit], 'opportunity').find(g => g.key === 'o3').title, '另一商机');
  globalThis.SalesRuntime = {app: {globalData: {session: {userId: 'me'}}}, require: () => ({
    listOpportunities: async params => { calls.push(params); return params.offset ? {items: [{id: 'o2', customer_id: 'c1', name: '联合拜访商机', owner_id: 'other', amount: 1}], has_more: false}
      : {items: [{id: 'o1'}], has_more: true, next_offset: 100}; },
  })};
  try {
    const result = await loadActiveOpportunities(periodRange('biweek'), {kind: 'self'}, [visit]);
    assert.equal(result.total, 2);
    assert.equal(result.items.length, 2);
    assert.equal(result.items[0].name, '联合拜访商机');
    assert.equal(result.items[1].amount, null, 'missing monetary data is never manufactured');
    assert.equal(calls.length, 2);
    assert.equal(calls[0].scope, 'team');
    assert.equal('ownerId' in calls[0], false);
  } finally { globalThis.SalesRuntime = previous; }
});
