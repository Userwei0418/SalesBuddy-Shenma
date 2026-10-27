import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../..');
const miniPath = path.join(repo, 'frontend/miniprogram/utils/taskHistory.js');
const webPath = path.join(repo, 'frontend/web/source/miniprogram/utils/taskHistory.js');
const requireMini = createRequire(miniPath);

test('Mini Program and Web task-history projections stay byte-for-byte aligned', () => {
  assert.equal(fs.readFileSync(miniPath, 'utf8'), fs.readFileSync(webPath, 'utf8'));
  const miniPage = fs.readFileSync(path.join(repo, 'frontend/miniprogram/pages/task-detail/index.js'), 'utf8');
  const webPage = fs.readFileSync(path.join(repo, 'frontend/web/source/miniprogram/pages/task-detail/index.js'), 'utf8');
  assert.match(miniPage, /require\(['"]\.\.\/\.\.\/utils\/taskHistory['"]\)/);
  assert.match(webPage, /require\(['"]\.\.\/\.\.\/utils\/taskHistory['"]\)/);
  assert.match(miniPage, /taskHistory\.fromEvents\(item\.events\)/);
  assert.match(webPage, /taskHistory\.fromEvents\(item\.events\)/);
});

test('history includes execution, dual-role handover, rejection and requirement changes', () => {
  const events = [
    { id: 'legacy', event_type: 'submit_completion', actor_name: '执行人', note: '已提交' },
    { id: 'reject', event_type: 'reject_completion', actor_name: '审核人', note: '请补充验收证据' },
    { id: 'executor-init', event_type: 'initiate_executor_handover', actor_name: '执行人', note: '换岗交接' },
    { id: 'executor-reject', event_type: 'respond_executor_handover', actor_name: '候选人', payload: { command: { decision: 'reject', reason: '当前无容量' } } },
    { id: 'reviewer-init', event_type: 'initiate_reviewer_handover', actor_name: '发起人', note: '申请更换验收人' },
    { id: 'reviewer-accept', event_type: 'respond_reviewer_handover', actor_name: '新验收人', payload: { command: { decision: 'accept' } } },
    { id: 'requirements', event_type: 'respond_requirements', actor_name: '新验收人', note: '确认变更' },
    { id: 'unknown', event_type: 'notification_only', actor_name: '系统', note: '不应进入任务记录' },
  ];
  const history = requireMini('./taskHistory').fromEvents(events);
  assert.deepEqual(history.map(item => [item.event_type, item.label]), [
    ['submit_completion', '提交完成'],
    ['reject_completion', '驳回'],
    ['initiate_executor_handover', '发起执行责任交接'],
    ['respond_executor_handover', '处理执行责任交接'],
    ['initiate_reviewer_handover', '发起验收责任交接'],
    ['respond_reviewer_handover', '处理验收责任交接'],
    ['respond_requirements', '确认任务要求变更'],
  ]);
  assert.equal(history[3].note, '当前无容量');
  assert.equal(history[5].note, '已确认接手');
  assert.equal(history[0].actor_name, '执行人');
  assert.equal(history.at(-1).event_type, 'respond_requirements');
});

test('execution read events merge into task history without duplicating task_event rows', () => {
  const taskEvent = { id: 'same', event_type: 'initiate_executor_handover', actor_name: '执行人', occurred_at: '2026-10-18T10:00:00Z', note: '交接' };
  const reviewTaskEvent = { id: 'review', event_type: 'reject_completion', actor_name: '审核人', occurred_at: '2026-10-18T11:00:00Z', note: '' };
  const executionEvents = [
    { id: 'same', kind: 'initiate_executor_handover', occurred_at: '2026-10-18T10:00:00Z', body: { command: { reason: '交接' }, actor_name: '执行人' } },
    { id: 'review', kind: 'review_completion', occurred_at: '2026-10-18T11:00:00Z', body: { command: { decision: 'reject', note: '请补充' }, actor_name: '审核人' } },
  ];
  const taskHistory = requireMini('./taskHistory');
  const merged = taskHistory.mergeExecutionEvents([taskEvent, reviewTaskEvent], executionEvents);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].id, 'same');
  const history = taskHistory.fromEvents(merged);
  assert.deepEqual(history.map(item => [item.event_type, item.label]), [
    ['initiate_executor_handover', '发起执行责任交接'],
    ['reject_completion', '驳回'],
  ]);
  assert.equal(history[1].note, '请补充');
  assert.equal(history[1].actor_name, '审核人');
});
