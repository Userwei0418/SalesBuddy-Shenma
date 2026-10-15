import test from 'node:test';
import assert from 'node:assert/strict';
import {historyParams, initialHistoryFilters, normalizeHistory, normalizeExistingCustomers, existingCustomerQuery, creationSearchBlock, loadRequestHistory, loadExistingCustomers} from '../department-ui/pc/customer-flow.js';

const summary = {pending: 21, approved: 2, rejected: 1, cancelled: 0};
const row = {id: 'r1', kind: 'claim', status: 'pending', can_read_customer: false};
const defaults = {enabled: true, name: '客户甲', loading: false, error: '', value: {items: [], total: 0}};

test('history uses server authorization and expresses only explicit self/department/member filters', () => {
  const initial = initialHistoryFilters();
  assert.deepEqual(historyParams(initial), {scope: 'self', limit: 20, offset: 0});
  assert.deepEqual(historyParams({...initial, scope: {kind: 'team', teamId: 'all'}}), {scope: 'team', limit: 20, offset: 0});
  assert.deepEqual(historyParams({...initial, scope: {kind: 'team', teamId: 'south'}, status: 'pending', q: ' 客户 ', offset: 20}), {scope: 'team', team_id: 'south', status: 'pending', q: '客户', limit: 20, offset: 20});
  assert.deepEqual(historyParams({...initial, scope: {kind: 'member', memberId: 'm2', teamId: 'stale'}}), {scope: 'team', member_id: 'm2', limit: 20, offset: 0});
});

test('history retains server totals for empty trailing pages and never derives summary from displayed rows', () => {
  assert.deepEqual(normalizeHistory({items: [], total: 21, summary}), {items: [], total: 21, summary});
  assert.equal(normalizeHistory({items: [row], total: 21, summary}).summary.approved, 2);
  for (const value of [{items: [], total: 0}, {items: [], total: 0, summary: {}}, {items: [{...row, can_read_customer: undefined}], total: 1, summary}, {items: [row], total: 0, summary}]) assert.throws(() => normalizeHistory(value));
});

test('creation prevents failed, stale and exact-name searches without blocking merely similar candidates', () => {
  assert.equal(creationSearchBlock(defaults), '');
  assert.ok(creationSearchBlock({...defaults, name: ''}));
  assert.ok(creationSearchBlock({...defaults, loading: true}));
  assert.ok(creationSearchBlock({...defaults, error: '读取失败'}));
  assert.ok(creationSearchBlock({...defaults, value: null}));
  const matched = {items: [{id: 'c1', name: '客户甲分公司'}], total: 6};
  assert.equal(creationSearchBlock({...defaults, value: matched}), '');
  assert.ok(creationSearchBlock({...defaults, value: {items: [{id: 'c1', name: ' 客户甲 '}], total: 1}}));
  assert.equal(creationSearchBlock({...defaults, enabled: false, error: '无目录权限'}), '');
});

test('candidate retrieval is bounded to 100 Unicode characters while duplicate comparison retains the complete name', () => {
  const name = '𠮷'.repeat(120);
  assert.equal([...existingCustomerQuery(name)].length, 100);
  assert.equal(creationSearchBlock({...defaults, name, value: {items: [{id: 'c', name}], total: 1}}), '已有同名客户，请使用已有客户');
  assert.throws(() => normalizeExistingCustomers({items: [], total: null}));
  assert.throws(() => normalizeExistingCustomers({items: [{}], total: 1}));
});

test('history and candidate reads reject responses arriving after identity changes', async () => {
  let current = 'u1', request, complete;
  globalThis.SalesRuntime = {app: {globalData: {session: {}}}, require: name => name === 'utils/access' ? {identity: () => current} : {request: options => {request = options; return new Promise(resolve => {complete = resolve;});}}};
  const history = loadRequestHistory({...initialHistoryFilters(), scope: {kind: 'member', memberId: 'u2'}});
  assert.equal(new URL(request.path, 'http://local').searchParams.get('member_id'), 'u2');
  assert.equal(request.fresh, true);
  current = 'u3'; complete({items: [], total: 0, summary});
  await assert.rejects(history, /身份或权限/);
  const existing = loadExistingCustomers('甲'.repeat(120));
  assert.equal(new URL(request.path, 'http://local').searchParams.get('q').length, 100);
  current = 'u4'; complete({items: [], total: 0});
  await assert.rejects(existing, /身份或权限/);
  delete globalThis.SalesRuntime;
});
