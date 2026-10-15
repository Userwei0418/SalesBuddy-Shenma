import test from 'node:test';
import assert from 'node:assert/strict';
import {customerRange, initialCustomerFilters, loadCustomerPortfolio, normalizePortfolio, portfolioParams, selectionCount} from '../department-ui/pc/customer-portfolio.js';

const now = Date.parse('2026-10-08T10:00:00+08:00');
const filters = initialCustomerFilters({kind: 'team', teamId: 'south'});
const fixture = () => ({
  total: 103, offset: 0, limit: 10, filters_applied: {},
  summary: {claimed: 125, active: 3, pipeline_ge_10: 103, pipeline_ge_30: 83, pipeline_ge_50: 61, pipeline_ge_70: 40, pipeline_ge_90: 20, won: 27, pool_total: null, activity_unavailable_count: 0, opportunity_unavailable_count: 0},
  availability: {activity: true, opportunity: true, map: true, pool: false},
  items: [{id: 'customer-0', name: '本期未跟进的在推客户', is_active: false, max_open_probability: 50, has_won: true, can_open: true, activity_readable: true, opportunity_readable: true, map_readable: true}],
  map_points: Array.from({length: 100}, (_, n) => ({id: `customer-${n}`})), unscored_count: 3, map_unavailable_count: 0,
});

test('customer activity defaults to current Beijing year; natural periods stop at today', () => {
  assert.equal(filters.period, 'year');
  assert.deepEqual([customerRange('year', now).start, customerRange('year', now).end], ['2026-01-01', '2026-12-31']);
  assert.deepEqual([portfolioParams(filters, {now}).activity_start, portfolioParams(filters, {now}).activity_end], ['2026-01-01', '2026-10-08']);
  const biweek = portfolioParams({...filters, period: 'biweek'}, {now});
  assert.deepEqual([biweek.activity_start, biweek.activity_end], ['2026-09-28', '2026-10-08']);
  const boundary = Date.parse('2026-12-31T16:00:00Z');
  assert.equal(portfolioParams(filters, {now: boundary}).activity_start, '2027-01-01');
});

test('pipeline requests are independent from activity and scoped by requested owner/department', () => {
  const pipeline = portfolioParams({...filters, selection: 'pipeline:50', period: 'week', q: '  示例客户  ', offset: 30}, {now, limit: 15});
  assert.equal(pipeline.segment, 'pipeline'); assert.equal(pipeline.min_probability, 50);
  assert.equal(pipeline.team_id, 'south'); assert.equal(pipeline.scope, 'team');
  assert.equal(pipeline.q, '示例客户'); assert.equal(pipeline.offset, 30); assert.equal(pipeline.limit, 15);
  assert.equal(pipeline.is_active, undefined); assert.equal(pipeline.active, undefined);
  const person = portfolioParams({...filters, scope: {kind: 'member', memberId: 'colleague'}, selection: 'won'}, {now});
  assert.equal(person.member_id, 'colleague'); assert.equal(person.team_id, undefined); assert.equal(person.min_probability, undefined);
  assert.equal(portfolioParams({...filters, scope: {kind: 'self'}}, {now}).scope, 'self');
});

test('complete server totals and map scope are preserved across pagination; no active-only client filtering', () => {
  const data = normalizePortfolio(fixture());
  assert.equal(data.items.length, 1); assert.equal(data.total, 103);
  assert.equal(data.items[0].is_active, false); assert.equal(data.items[0].has_won, true);
  assert.equal(selectionCount(data.summary, 'pipeline:50'), 61);
  assert.equal(selectionCount(data.summary, 'active'), 3);
  assert.equal(data.map_points.length + data.unscored_count, data.total);
});

test('resource denial remains unavailable, never a fabricated zero or stale map', () => {
  const data = fixture();
  data.availability = {activity: false, opportunity: false, map: false, pool: false};
  for (const key of ['active', 'pipeline_ge_10', 'pipeline_ge_30', 'pipeline_ge_50', 'pipeline_ge_70', 'pipeline_ge_90', 'won']) data.summary[key] = null;
  data.map_points = []; data.unscored_count = null; data.map_unavailable_count = data.total;
  assert.equal(selectionCount(normalizePortfolio(data).summary, 'active'), null);
  assert.equal(data.summary.pool_total, null);
});

test('partially authorized maps keep unreadable customers distinct from genuinely unscored customers', () => {
  const data = fixture();
  data.map_points = data.map_points.slice(0, 70); data.map_unavailable_count = 30;
  assert.equal(normalizePortfolio(data).unscored_count, 3);
  assert.equal(data.map_points.length + data.unscored_count + data.map_unavailable_count, data.total);
  data.map_unavailable_count = 29;
  assert.throws(() => normalizePortfolio(data), /范围不一致/);
});

test('truncated, duplicate or inconsistent portfolio responses are rejected', () => {
  const truncated = fixture(); delete truncated.summary.pipeline_ge_30;
  assert.throws(() => normalizePortfolio(truncated), /响应不完整/);
  const duplicate = fixture(); duplicate.items.push(duplicate.items[0]);
  assert.throws(() => normalizePortfolio(duplicate), /分页数据不完整/);
  const wrongMap = fixture(); wrongMap.unscored_count = 0;
  assert.throws(() => normalizePortfolio(wrongMap), /范围不一致/);
  const missingAvailability = fixture(); delete missingAvailability.availability;
  assert.throws(() => normalizePortfolio(missingAvailability), /响应不完整/);
  const missingRowAccess = fixture(); delete missingRowAccess.items[0].opportunity_readable;
  assert.throws(() => normalizePortfolio(missingRowAccess), /分页数据不完整/);
});

test('account changes discard in-flight portfolio responses and forbidden errors do not fall back', async () => {
  const previous = globalThis.SalesRuntime;
  let actor = 'a', release, calls = [];
  const request = options => { calls.push(options); return new Promise(resolve => { release = resolve; }); };
  globalThis.SalesRuntime = {app: {globalData: {session: {userId: 'a'}}}, require: name => name === 'utils/access' ? {identity: () => actor} : {request}};
  try {
    const pending = loadCustomerPortfolio({scope: 'team', segment: 'claimed'});
    actor = 'b'; release(fixture());
    await assert.rejects(pending, /身份或权限已变化/);
    assert.match(calls[0].path, /^\/customers\/portfolio\?/); assert.equal(calls[0].fresh, true);
    globalThis.SalesRuntime.require = name => name === 'utils/access' ? {identity: () => actor} : {request: async () => { throw Object.assign(Error('禁止读取'), {statusCode: 403}); }};
    await assert.rejects(loadCustomerPortfolio({segment: 'active'}), /禁止读取/);
    assert.equal(calls.length, 1);
  } finally { globalThis.SalesRuntime = previous; }
});
