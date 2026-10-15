import {test, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {creationHistoryParams, creationTimeLabel, creationScopeLabel, creationScopeOption, initialCreationFilters, loadCreationDirectory, loadCreationHistory, normalizeCreationHistory, validCreationOffset} from '../department-ui/pc/opportunity-history.js';

const row = {id: 'o1', customer_id: 'c1', name: '测试商机', customer_name: '测试客户', creator_id: 'creator', creator_name: '创建人', business_created_on: '2026-10-09', created_at: '2026-10-08T16:15:00Z', owner_name: '接手人', can_read_customer: false, status: 'open', stage: 'qualified'};
const page = (items = [row], extra = {}) => ({items, total: items.length, has_more: false, next_offset: null, ...extra});
const options = (members, extra = {}) => ({teams: [{id: 'south', name: '南区'}], members, total: members.length, has_more: false, next_offset: null, default_scope: 'self', team_basis: 'current_creator_membership', ...extra});
function mock(request, current = () => 'actor:v1') {
  globalThis.SalesRuntime = {app: {globalData: {session: {}}}, require: name => name === 'utils/access' ? {identity: current} : {request}};
}
afterEach(() => {delete globalThis.SalesRuntime;});

test('creation scopes never reuse owner scope or silently drop an empty member selection', () => {
  const base = initialCreationFilters();
  assert.deepEqual(creationHistoryParams(base), {scope: 'self', limit: 20, offset: 0});
  assert.deepEqual(creationHistoryParams({...base, scope: {kind: 'member', memberId: 'creator', teamId: 'stale'}, q: ' 测试 ', created_from: '2026-09-01', created_to: '2026-10-09', offset: 20}), {scope: 'team', member_id: 'creator', q: '测试', created_from: '2026-09-01', created_to: '2026-10-09', limit: 20, offset: 20});
  assert.deepEqual(creationHistoryParams({...base, scope: {kind: 'team', teamId: 'south'}}), {scope: 'team', team_id: 'south', limit: 20, offset: 0});
  assert.deepEqual(creationHistoryParams({...base, scope: {kind: 'team', teamId: 'all'}}), {scope: 'team', limit: 20, offset: 0});
  for (const patch of [{scope: {kind: 'member'}}, {created_from: '2026-02-30'}, {created_from: '2026-10-10', created_to: '2026-10-09'}]) assert.throws(() => creationHistoryParams({...base, ...patch}));
});

test('unknown provenance remains unknown, and imported date-only facts do not acquire fake midnight', () => {
  const unknown = {...row, creator_id: null, creator_name: null, business_created_on: null, created_at: null};
  assert.equal(normalizeCreationHistory(page([unknown])).items[0].creator_name, null);
  assert.equal(creationTimeLabel(unknown), '日期未记录');
  assert.equal(creationTimeLabel({...unknown, created_at: '2026-10-09T00:00:00Z'}), '日期未记录');
  assert.equal(creationTimeLabel({...unknown, business_created_on: '2021-05-12'}), '2021-05-12');
  assert.equal(creationTimeLabel({...row, business_created_on: '2021-05-12'}), '2021-05-12');
  assert.equal(creationTimeLabel(row), '2026-10-09 00:15');
});

test('history requires explicit authority and pagination metadata without inventing totals', () => {
  assert.equal(normalizeCreationHistory(page([], {total: 19}), 20).total, 19);
  assert.equal(normalizeCreationHistory(page([row], {total: 41, has_more: true, next_offset: 21}), 20).total, 41);
  for (const bad of [page([row], {total: 41}), page([row], {has_more: true, next_offset: 0}), page([row, row]), page([{...row, can_read_customer: undefined}]), page([{...row, creator_id: null}]), page([{...row, business_created_on: '2026-02-30'}]), page([{...row, created_at: '2026-10-09'}])]) assert.throws(() => normalizeCreationHistory(bad));
});

test('directory follows all server pages and retains stopped or unassigned creators', async () => {
  const members = Array.from({length: 101}, (_, i) => ({id: `member${i}`, name: `创建人${i}`, is_active: i !== 100, team_ids: i === 100 ? [] : ['south']})), calls = [];
  mock(async request => {calls.push(request); const offset = Number(new URL(request.path, 'http://local').searchParams.get('offset')); return options(members.slice(offset, offset + 100), {total: 101, has_more: offset === 0, next_offset: offset === 0 ? 100 : null});});
  const result = await loadCreationDirectory();
  assert.equal(calls.length, 2); assert.ok(calls.every(call => call.fresh));
  assert.equal(result.members.length, 101); assert.equal(result.people.get('member100').is_active, false);
  assert.equal(creationScopeLabel({kind: 'member', memberId: 'member100'}, result), '创建人100');
  assert.equal(creationScopeLabel({kind: 'team', teamId: 'all'}, result), '全部可见创建记录');
});

test('directory aborts or fails visibly on repeated cursors instead of showing a partial selector', async () => {
  let calls = 0;
  mock(async () => {calls++; return options([{id: 'one', name: '甲', is_active: true, team_ids: []}], {total: 3, has_more: true, next_offset: 1});});
  await assert.rejects(loadCreationDirectory(), /目录/);
  assert.equal(calls, 2);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(loadCreationDirectory(controller.signal), /取消/);
  assert.equal(calls, 2);
});

test('history rejects stale identity results and API failures without falling back to owner lists', async () => {
  let actor = 'a:v1', finish, calls = [];
  mock(request => {calls.push(request); return new Promise(resolve => {finish = resolve;});}, () => actor);
  const pending = loadCreationHistory(initialCreationFilters());
  actor = 'a:v2'; finish(page());
  await assert.rejects(pending, /身份或权限/);
  assert.equal(calls.length, 1); assert.match(calls[0].path, /^\/opportunities\/creation-history\?/);
  mock(async () => {throw Error('没有权限');});
  await assert.rejects(loadCreationHistory(initialCreationFilters()), /没有权限/);
});

test('directory identity changes stop traversal before the next page', async () => {
  let actor = 'a', count = 0;
  mock(async () => {count++; actor = 'b'; return options([{id: 'one', name: '甲', is_active: true, team_ids: []}], {total: 2, has_more: true, next_offset: 1});}, () => actor);
  await assert.rejects(loadCreationDirectory(), /身份或权限/); assert.equal(count, 1);
});

test('shrunk pages select the last valid server offset without widening filters', () => {
  for (const [offset, total, expected] of [[20, 19, 0], [40, 25, 20], [20, 0, 0], [20, 21, 20], [0, 150, 0]]) assert.equal(validCreationOffset(offset, total), expected);
});

test('unavailable selected creators or departments keep identity but display a meaningful label', () => {
  const empty = {teams: [], members: [], people: new Map()};
  assert.deepEqual(creationScopeOption({kind: 'member', memberId: 'removed-id'}, empty), {value: 'member:removed-id', label: '所选创建人已不可用，请重新选择'});
  assert.deepEqual(creationScopeOption({kind: 'team', teamId: 'gone-team'}, empty), {value: 'team:gone-team', label: '所选部门已不可用，请重新选择'});
  assert.deepEqual(creationScopeOption({kind: 'team', teamId: 'all'}, empty), {value: 'team:all', label: '全部可见创建记录'});
  const stopped = {...empty, people: new Map([['stopped', {name: '离职同事', is_active: false}]])};
  assert.equal(creationScopeOption({kind: 'member', memberId: 'stopped'}, stopped).label, '离职同事（已停用）');
});
