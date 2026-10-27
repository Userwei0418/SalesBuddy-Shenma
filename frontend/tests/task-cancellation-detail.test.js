const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = path.resolve(__dirname, '../miniprogram/pages/task-detail/index.js');
const id = '11111111-1111-1111-1111-111111111111';

async function harness(taskPatch = {}, permissions = { 'task.cancel': true, 'task.coordinate': true }) {
  let page; const reads = [], toasts = [];
  const session = { workspaceId: 'company', userId: 'sender', permissionVersion: 'p1', permissions };
  const task = { id, title: '误建任务', description: '核对方案', version_no: 3, status: 'pending_execution', can_cancel: true, can_coordinate: false, events: [], ...taskPatch };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => page = value, getApp: () => ({ globalData: { session } }),
    require: name => name.endsWith('apiClient') ? { getTask: async () => task, getTaskReassignmentOptions: async () => { reads.push('directory'); throw Error('目录暂不可用'); } } : require(path.resolve(path.dirname(filename), name)),
    wx: { showToast: value => toasts.push(value) }, setTimeout() {},
  });
  page.data = JSON.parse(JSON.stringify(page.data)); page.setData = patch => Object.assign(page.data, patch);
  page.setData({ taskId: id }); await page.loadTask();
  return { page, session, task, reads, toasts };
}

test('详情直接打开取消弹层，不依赖转交权限、选人目录或可接手人员', async () => {
  const h = await harness(); assert.equal(h.page.data.task.can_coordinate, false); assert.equal(h.page.data.task.canCancel, true);
  h.page.openCancellation(); assert.equal(h.page.data.cancelOpen, true); assert.deepEqual(h.reads, []);
  assert.equal(h.page.data.coordinateOpen, false); assert.equal(h.page.data.coordinatePickerOpen, false);
});

test('详情取消入口遵循后端精确布尔和当前权限，终态、旧契约及未知状态隐藏', async () => {
  for (const patch of [{ can_cancel: undefined }, { can_cancel: 'true' }, { can_cancel: false }, { status: 'completed' }, { status: 'cancelled' }, { status: 'pending_review' }, { status: 'unknown' }]) {
    const h = await harness(patch); assert.equal(h.page.data.task.canCancel, false); h.page.openCancellation(); assert.equal(h.page.data.cancelOpen, false);
  }
  const denied = await harness({}, { 'task.cancel': false }); assert.equal(denied.page.data.task.canCancel, false);
});

test('取消后和其他不可协调状态即使后端保留协调权限也不显示转交入口', async () => {
  for (const status of ['cancelled', 'completed', 'pending_review', 'unknown']) {
    const h = await harness({ status, can_coordinate: true, action_permissions: { 'task.coordinate': true } });
    assert.equal(h.page.data.task.can_coordinate, false); h.page.openCoordination(); assert.equal(h.page.data.coordinateOpen, false);
    assert.equal(h.reads.length, 0);
  }
});

test('取消完成更新详情、原因和事件历史，不显示重新发起，已有完成记录保留', async () => {
  const h = await harness(); h.page.openCancellation();
  const events = [{ id: 'old', event_type: 'submit_completion', note: '交付说明', occurred_at: '2026-09-26T00:00:00Z' },
    { id: 'reassign', event_type: 'reassign', note: '临时交接', occurred_at: '2026-09-26T01:00:00Z' },
    { id: 'cancel', event_type: 'cancel', actor_name: '发起人', note: '重复任务', occurred_at: '2026-09-27T00:00:00Z' }];
  h.page.taskCancelled({ detail: { ...h.task, version_no: 4, status: 'cancelled', can_cancel: false, can_coordinate: false, last_event_type: 'cancel', last_event_note: '重复任务', events } });
  assert.equal(h.page.data.task.statusLabel, '历史取消'); assert.equal(h.page.data.task.cancellationNote, '重复任务');
  assert.equal(h.page.data.task.canRetry, false); assert.equal(h.page.data.task.canCancel, false); assert.equal(h.page.data.cancelOpen, false);
  assert.deepEqual(Array.from(h.page.data.task.history, row => row.label), ['提交完成', '转交任务', '取消任务']);
});

test('详情离页关闭弹层，切账号权限或任务版本后忽略迟到取消回执', async () => {
  for (const change of [h => h.page.onHide(), h => h.page.onUnload(), h => h.session.userId = 'other', h => h.session.permissions['task.cancel'] = false, h => h.page.data.task.version_no = 8]) {
    const h = await harness(); h.page.openCancellation(); change(h);
    h.page.taskCancelled({ detail: { ...h.task, version_no: 4, status: 'cancelled' } }); assert.equal(h.page.data.task.status, 'pending_execution');
    assert.equal(h.toasts.length, 0);
  }
});

test('重新查询到任务已取消时更新详情与记录，提示查询结果而非本次取消成功', async () => {
  const h = await harness(); h.page.openCancellation();
  h.page.taskCancellationRefreshed({ detail: { ...h.task, status: 'cancelled', can_cancel: false, version_no: 4,
    last_event_type: 'cancel', last_event_note: '重复任务', events: [{ id: 'cancel', event_type: 'cancel', note: '重复任务' }] } });
  assert.equal(h.page.data.task.status, 'cancelled'); assert.equal(h.page.data.task.cancellationNote, '重复任务');
  assert.equal(h.page.data.task.history[0].label, '取消任务'); assert.equal(h.page.data.cancelOpen, false);
  assert.equal(h.toasts[0].title, '该任务已取消，记录已保留'); assert.equal(h.toasts[0].icon, 'none');
});

test('重新查询到完成、待验收或对象取消权限收回时按最新任务更新详情', async () => {
  for (const status of ['completed', 'pending_review', 'pending_execution']) {
    const h = await harness(); h.page.openCancellation();
    h.page.taskCancellationRefreshed({ detail: { ...h.task, status, can_cancel: false, version_no: 4 } });
    assert.equal(h.page.data.task.status, status); assert.equal(h.page.data.task.canCancel, false); assert.equal(h.page.data.cancelOpen, false);
    assert.equal(h.toasts[0].title, '任务状态已更新');
  }
});

test('刷新回执保留关闭、身份及宿主版本检查，不能覆盖新的详情', async () => {
  for (const change of [h => h.page.onHide(), h => h.session.userId = 'other', h => h.session.permissions['task.cancel'] = false,
    h => h.page.data.task.version_no = 8, h => h.page.data.task.id = 'other']) {
    const h = await harness(); h.page.openCancellation(); change(h);
    h.page.taskCancellationRefreshed({ detail: { ...h.task, status: 'cancelled', can_cancel: false, version_no: 4 } });
    assert.equal(h.page.data.task.status, 'pending_execution'); assert.equal(h.toasts.length, 0);
  }
});

test('已打开转交目录失败仍可直接取消，原转交选人面板被安全关闭', async () => {
  const h = await harness({ can_coordinate: true, action_permissions: { 'task.coordinate': true } });
  await h.page.openCoordination(); assert.equal(h.reads.length, 1); assert.ok(h.page.data.coordinateDirectoryError);
  h.page.openCancellation(); assert.equal(h.page.data.cancelOpen, true); assert.equal(h.page.data.coordinateOpen, false); assert.equal(h.reads.length, 1);
});

test('详情模板的取消入口直接可见，组件携带任务版本且旧转交操作不重复提供取消', () => {
  const markup = fs.readFileSync(filename.replace('.js', '.wxml'), 'utf8');
  const config = JSON.parse(fs.readFileSync(filename.replace('.js', '.json'), 'utf8'));
  assert.match(markup, /wx:if="\{\{!executionError && !executionLoading && task.canCancel && \(!execution \|\| execution.raw.protocol_version !== 2\)\}\}"[^>]*class="coordination-entry"[^>]*>[\s\S]*bind:click="openCancellation"/);
  assert.match(markup, /task-cancel-dialog[^>]*task-version=/);
  assert.match(markup, /bind:refreshed="taskCancellationRefreshed"/);
  assert.match(markup, /任务记录/); assert.doesNotMatch(markup, /data-event="cancel"/);
  assert.equal(config.usingComponents['task-cancel-dialog'], '/components/task-cancel-dialog/index');
});
