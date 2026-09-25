import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
const shared = vm.createContext({module: {exports: {}}});
vm.runInContext(fs.readFileSync(new URL('../source/miniprogram/utils/taskOverview.js', import.meta.url), 'utf8'), shared);
const TASK_SORT_OPTIONS = JSON.parse(JSON.stringify(shared.module.exports.TASK_SORT_OPTIONS));
const context = vm.createContext({Intl});
vm.runInContext(fs.readFileSync(new URL('../task-workspace.js', import.meta.url), 'utf8'), context);
const {configure, presentation} = context.SalesTasks;
const page = {route: 'pages/tasks/index'};
const state = rows => ({filteredTasks: rows, sortOptions: TASK_SORT_OPTIONS, sortIndex: 2, activeTab: 'pending', tabs: [], pendingCount: rows.length});
const task = (id, due_at, fields = {}) => ({id, due_at, status: 'pending', statusLabel: '已接受', title: '任务', description: '任务', ...fields});
const show = (data, now) => JSON.parse(JSON.stringify(presentation(page, data, Date.parse(now))));

test('task deadlines use Beijing date boundaries and exact timestamps, including missing and completed dates', () => {
  const data = state([
    task('yesterday', '2026-09-15T23:59:00+08:00'),
    task('today', '2026-09-15T16:00:00Z'),
    task('tomorrow', '2026-09-17T09:00:00+08:00'),
    task('none', null), task('bad', 'not-a-date'),
    task('done', '2025-01-01T08:00:00Z', {status: 'completed', completed_at: '2026-09-15T16:00:00Z'}),
    task('closed', '2025-01-01T08:00:00Z', {status: 'rejected'}),
  ]);
  const rows = show(data, '2026-09-16T00:00:00+08:00').webTaskRows;
  assert.deepEqual(rows.map(row => row.webTime.key), ['overdue', 'today', 'upcoming', 'undated', 'undated', 'closed', 'closed']);
  assert.equal(rows[0].webTime.label, '已逾期 不足 1 小时');
  assert.equal(rows[1].webTime.label, '今天 00:00');
  assert.equal(rows[2].webTime.label, '明天 09:00');
  assert.equal(rows[5].webTime.detail, '9月16日 00:00');
  assert.equal(rows[6].webTime.detail, '任务已结束');
});

test('presentation preserves server order and facts; only pending due_asc is divided into time groups', () => {
  const data = state([task('b', '2026-09-17T10:00:00+08:00', {requires_action: true, priorityClass: 'high'}), task('a', '2026-09-16T09:00:00+08:00')]);
  const original = JSON.stringify(data), now = '2026-09-16T10:00:00+08:00';
  const rows = show(data, now).webTaskRows;
  assert.deepEqual(rows.map(row => row.id), ['b', 'a'], 'never sort the loaded page');
  assert.equal(rows[0].webAction, '去确认'); assert.equal(rows[0].webPriority, '高优先级');
  assert.equal(rows[0].webDescription, ''); assert.equal(JSON.stringify(data), original);
  for (const changes of [{sortIndex: 0}, {sortIndex: 1}, {sortIndex: 3}, {activeTab: 'all'}, {activeTab: 'completed'}]) {
    const result = show({...data, ...changes}, now);
    assert.equal(result.webTaskGrouped, false); assert.ok(result.webTaskRows.every(row => !row.webGroup));
  }
});

test('Web initialization chooses the existing due_asc contract without changing option indices or other pages', () => {
  const instance = {...page, data: state([])}; instance.data.sortIndex = 0;
  configure(instance);
  assert.equal(instance.data.sortOptions[instance.data.sortIndex].key, 'due_asc');
  assert.deepEqual(Array.from(instance.data.sortOptions, row => row.key), TASK_SORT_OPTIONS.map(row => row.key));
  const other = {route: 'pages/bi/index', data: {sortIndex: 0}}; configure(other);
  assert.equal(other.data.sortIndex, 0);
});

test('pending creator review stays unfinished and uses a read-only review entry without granting actions', () => {
  const data = state([task('review', '2026-09-15T09:00:00+08:00', {statusLabel: '待发起人确认', requires_action: true}), task('rework', '2026-09-17T09:00:00+08:00', {last_event_type: 'reject_completion', statusLabel: '执行中'})]);
  const rows = show(data, '2026-09-16T10:00:00+08:00').webTaskRows;
  assert.equal(rows[0].webState, 'review'); assert.equal(rows[0].webAction, '查看验收');
  assert.equal(rows[0].status, 'pending'); assert.equal(rows[0].webTime.key, 'overdue');
  assert.equal(rows[0].canReview, undefined); assert.equal(rows[1].webState, 'active');
});
