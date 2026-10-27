const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { canCancelTask } = require('../miniprogram/utils/taskCancellation');

const filename = path.resolve(__dirname, '../miniprogram/components/task-cancel-dialog/index.js');
const taskId = '11111111-1111-1111-1111-111111111111';
const otherId = '22222222-2222-2222-2222-222222222222';
const task = (extra = {}) => ({ id: taskId, title: '核对方案', status: 'pending_execution', version_no: 3, can_cancel: true, ...extra });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

function harness(overrides = {}) {
  let definition;
  const session = { userId: 'sender', workspaceId: 'company', role: 'sales', permissionVersion: 'p1', permissions: { 'task.cancel': true } };
  const reads = [], writes = [], modals = [], events = [];
  const api = {
    getTask: async id => { reads.push(id); return task({ id }); },
    coordinateTask: async (id, body) => { writes.push({ id, body }); return task({ id, status: 'cancelled', can_cancel: false, version_no: body.version_no + 1, last_event_type: 'cancel', last_event_note: body.note }); },
    ...overrides,
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Component: value => definition = value,
    getApp: () => ({ globalData: { session } }),
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    wx: { showModal: modal => modals.push(modal) },
  });
  const instance = { ...definition.methods, properties: { open: true, taskId, taskVersion: 3 },
    data: JSON.parse(JSON.stringify(definition.data)),
    setData(patch) { Object.assign(this.data, patch); },
    triggerEvent(name, detail) { events.push({ name, detail }); },
  };
  function change(patch) {
    Object.assign(instance.properties, patch);
    if ('open' in patch || 'taskId' in patch) definition.observers['open, taskId'].call(instance, instance.properties.open, instance.properties.taskId);
    if ('taskVersion' in patch) definition.observers.taskVersion.call(instance);
  }
  return { instance, definition, session, reads, writes, modals, events, change };
}
function reason(h, value = '误建，重复任务') { h.instance.inputNote({ detail: { value } }); }

test('取消权限必须由后端精确布尔与当前权限共同允许，未知或终态不可取消', () => {
  const session = { permissions: { 'task.cancel': true } };
  for (const status of ['pending_confirm', 'pending_execution', 'in_progress', 'deferred']) assert.equal(canCancelTask(task({ status }), session), true);
  for (const status of ['pending_review', 'completed', 'cancelled', 'unknown', undefined]) assert.equal(canCancelTask(task({ status }), session), false);
  for (const can_cancel of [undefined, false, 'true', 1]) assert.equal(canCancelTask(task({ can_cancel }), session), false);
  assert.equal(canCancelTask(task(), { role: 'manager' }), false);
  assert.equal(canCancelTask(task(), { permissions: { 'task.cancel': false } }), false);
});

test('每次打开读取详情，原因必填且最多500字，二次确认后仅提交取消事件与新版本', async () => {
  const h = harness({ getTask: async () => task({ version_no: 7 }) });
  await h.instance.begin(false);
  h.instance.submit(); assert.equal(h.modals.length, 0); assert.match(h.instance.data.error, /填写取消原因/);
  reason(h, '字'.repeat(600)); assert.equal(h.instance.data.note.length, 500);
  reason(h, '  录入重复，保留有效任务  ');
  h.instance.submit(); h.instance.submit(); assert.equal(h.modals.length, 1); assert.equal(h.writes.length, 0);
  await h.modals[0].success({ confirm: true });
  assert.equal(h.writes.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(h.writes[0])), { id: taskId, body: { event_type: 'cancel', note: '录入重复，保留有效任务', version_no: 7 } });
  assert.equal(h.events[0].name, 'cancelled'); assert.equal(h.events[0].detail.status, 'cancelled');
  assert.equal(h.events[0].detail.last_event_note, '录入重复，保留有效任务');
});

test('放弃二次确认不写入且保留原因，继续确认只提交一次', async () => {
  const pending = deferred(); const h = harness({ coordinateTask: (id, body) => { h.writes.push({ id, body }); return pending.promise; } });
  await h.instance.begin(false); reason(h); h.instance.submit(); await h.modals[0].success({ confirm: false });
  assert.equal(h.writes.length, 0); assert.equal(h.instance.data.note, '误建，重复任务'); assert.equal(h.instance.data.confirming, false);
  h.instance.submit(); const first = h.modals[1].success({ confirm: true });
  h.instance.submit(); await h.modals[1].success({ confirm: true }); assert.equal(h.writes.length, 1);
  pending.resolve(task({ status: 'cancelled', can_cancel: false, last_event_type: 'cancel', version_no: 4 })); await first;
  assert.equal(h.events.length, 1);
});

test('详情不允许取消、版本缺失或响应任务ID不匹配时禁止提交', async () => {
  for (const response of [task({ can_cancel: false }), task({ can_cancel: 'true' }), task({ version_no: undefined }), task({ id: otherId }), task({ status: 'pending_review' })]) {
    const h = harness({ getTask: async () => response }); await h.instance.begin(false); reason(h); h.instance.submit();
    assert.equal(h.instance.data.canSubmit, false); assert.equal(h.modals.length, 0); assert.ok(h.instance.data.error);
  }
});

test('读取到同身份最新任务已不可取消时回传refreshed，仍可取消或无效响应不回传', async () => {
  for (const patch of [{ status: 'cancelled', last_event_type: 'cancel', can_cancel: false },
    { status: 'completed', can_cancel: false }, { status: 'pending_review', can_cancel: false }, { can_cancel: false }]) {
    const latest = task({ ...patch, version_no: 4 });
    const h = harness({ getTask: async () => latest }); await h.instance.begin(false);
    assert.equal(h.events.length, 1); assert.equal(h.events[0].name, 'refreshed'); assert.equal(h.events[0].detail, latest);
    assert.equal(h.instance.data.loading, false); assert.equal(h.instance.data.canSubmit, false);
  }
  for (const latest of [task(), task({ version_no: undefined }), task({ id: otherId })]) {
    const h = harness({ getTask: async () => latest }); await h.instance.begin(false); assert.equal(h.events.length, 0);
  }
});

test('取消响应丢失后主动刷新，或写入途中关闭再打开，读取结果能同步宿主且不重复写入', async () => {
  for (const closedDuringWrite of [false, true]) {
    const pending = deferred(); let latest = task();
    const h = harness({ getTask: async () => latest,
      coordinateTask: (id, body) => { h.writes.push({ id, body }); return pending.promise; } });
    await h.instance.begin(false); reason(h); h.instance.submit(); const write = h.modals[0].success({ confirm: true });
    if (closedDuringWrite) { h.instance.close(); h.change({ open: false }); }
    latest = task({ status: 'cancelled', can_cancel: false, last_event_type: 'cancel', last_event_note: '误建，重复任务', version_no: 4 });
    if (closedDuringWrite) pending.resolve(latest); else pending.reject(Error('网络连接中断'));
    await write;
    if (closedDuringWrite) { h.change({ open: true }); await tick(); } else await h.instance.refresh();
    assert.equal(h.writes.length, 1);
    assert.equal(h.events.filter(event => event.name === 'cancelled').length, 0);
    const refreshed = h.events.filter(event => event.name === 'refreshed');
    assert.equal(refreshed.length, 1); assert.equal(refreshed[0].detail, latest);
  }
});

test('不可取消的旧详情在切账号、关闭或宿主版本变化后不回传refreshed', async () => {
  for (const change of [h => h.session.userId = 'other', h => h.instance.close(), h => h.change({ taskVersion: 4 })]) {
    const pending = deferred(); const h = harness({ getTask: () => pending.promise }); const read = h.instance.begin(false);
    change(h); pending.resolve(task({ status: 'cancelled', can_cancel: false, version_no: 4 })); await read;
    assert.equal(h.events.filter(event => event.name === 'refreshed').length, 0);
  }
});

test('加载中关闭再打开，迟到的旧详情与失败均不能污染新弹层', async () => {
  for (const fail of [false, true]) {
    const old = deferred(); let count = 0;
    const h = harness({ getTask: () => ++count === 1 ? old.promise : Promise.resolve(task({ version_no: 8 })) });
    const first = h.instance.begin(false); h.instance.close(); h.change({ open: false }); h.change({ open: true }); await tick();
    reason(h, '本轮原因');
    if (fail) old.reject(Error('旧错误')); else old.resolve(task({ version_no: 1 }));
    await first;
    assert.equal(h.instance.data.task.version_no, 8); assert.equal(h.instance.data.note, '本轮原因'); assert.equal(h.instance.data.error, '');
  }
});

test('切账号、切租户、权限版本或实时取消权限变化后，旧详情不能启用按钮', async () => {
  for (const changeSession of [s => s.userId = 'other', s => s.workspaceId = 'other', s => s.permissionVersion = 'p2', s => s.permissions['task.cancel'] = false]) {
    const pending = deferred(); const h = harness({ getTask: () => pending.promise }); const first = h.instance.begin(false);
    changeSession(h.session); pending.resolve(task()); await first;
    assert.equal(h.instance.data.task, null); assert.equal(h.instance.data.canSubmit, false); assert.equal(h.events.length, 0);
  }
});

test('确认后账号权限变化、关闭、离页、卸载、任务ID或版本变化均不能发起旧写入', async () => {
  const changes = [
    h => h.session.userId = 'other', h => h.session.permissionVersion = 'p2', h => h.session.permissions['task.cancel'] = false,
    h => h.instance.close(), h => h.change({ open: false }), h => h.definition.pageLifetimes.hide.call(h.instance),
    h => h.definition.lifetimes.detached.call(h.instance), h => h.change({ taskId: otherId }),
    h => h.change({ taskVersion: 4 }), h => h.instance.data.task.version_no = 4,
  ];
  for (const change of changes) {
    const h = harness(); await h.instance.begin(false); reason(h); h.instance.submit(); change(h);
    await h.modals[0].success({ confirm: true }); assert.equal(h.writes.length, 0);
  }
});

test('提交后的迟到成功与失败不触发新页面状态、事件或覆盖原因', async () => {
  for (const fail of [false, true]) {
    const pending = deferred(); const h = harness({ coordinateTask: () => pending.promise });
    await h.instance.begin(false); reason(h); h.instance.submit(); const write = h.modals[0].success({ confirm: true });
    h.instance.close(); h.change({ open: false }); h.change({ taskId: otherId, open: true }); await tick(); reason(h, '新任务原因');
    if (fail) pending.reject(Error('旧写入失败')); else pending.resolve(task({ status: 'cancelled' }));
    await write;
    assert.equal(h.instance.data.task.id, otherId); assert.equal(h.instance.data.note, '新任务原因'); assert.equal(h.instance.data.error, '');
    assert.equal(h.events.filter(event => event.name === 'cancelled').length, 0);
  }
});

test('关闭重开同一任务仍有写入在途时不能重复提交，必须主动刷新状态', async () => {
  const pending = deferred(); const h = harness({ coordinateTask: (id, body) => { h.writes.push({ id, body }); return pending.promise; } });
  await h.instance.begin(false); reason(h); h.instance.submit(); const write = h.modals[0].success({ confirm: true });
  h.instance.close(); h.change({ open: false }); h.change({ open: true }); await tick();
  reason(h); h.instance.submit(); assert.equal(h.writes.length, 1); assert.equal(h.instance.data.needsRefresh, true);
  pending.resolve(task({ status: 'cancelled' })); await write; assert.equal(h.events.filter(event => event.name === 'cancelled').length, 0);
  await h.instance.refresh(); assert.equal(h.reads.length, 2);
});

test('版本冲突不自动重试或静默换版本，保留原因并主动刷新后再次确认', async () => {
  let reads = 0, writes = 0;
  const h = harness({ getTask: async () => task({ version_no: ++reads === 1 ? 3 : 4 }),
    coordinateTask: async (id, body) => { h.writes.push({ id, body }); if (++writes === 1) throw Object.assign(Error('conflict'), { statusCode: 409 }); return task({ status: 'cancelled', last_event_type: 'cancel', version_no: 5 }); } });
  await h.instance.begin(false); reason(h); h.instance.submit(); await h.modals[0].success({ confirm: true });
  assert.match(h.instance.data.error, /刷新任务后重新确认/); assert.equal(h.instance.data.note, '误建，重复任务'); assert.equal(h.instance.data.submitting, false);
  h.instance.submit(); assert.equal(h.modals.length, 1); assert.equal(reads, 1); assert.equal(writes, 1);
  await h.instance.refresh(); assert.equal(h.instance.data.task.version_no, 4); assert.equal(h.instance.data.note, '误建，重复任务'); assert.equal(writes, 1);
  h.instance.submit(); await h.modals[1].success({ confirm: true }); assert.equal(h.writes[1].body.version_no, 4);
});

test('失败保留原因和可读错误；权限拒绝需刷新；任务加载失败可原地重试', async () => {
  for (const statusCode of [403, 500]) {
    const h = harness({ coordinateTask: async () => { throw Object.assign(Error('服务暂时不可用'), { statusCode }); } });
    await h.instance.begin(false); reason(h); h.instance.submit(); await h.modals[0].success({ confirm: true });
    assert.equal(h.instance.data.note, '误建，重复任务'); assert.match(h.instance.data.error, /服务暂时不可用/);
    assert.equal(h.instance.data.submitting, false); assert.equal(h.instance.data.needsRefresh, statusCode === 403);
  }
  let count = 0; const h = harness({ getTask: async () => { if (!count++) throw Error('加载失败'); return task(); } });
  await h.instance.begin(false); assert.equal(h.instance.data.loading, false); assert.equal(h.instance.data.needsRefresh, true);
  await h.instance.refresh(); assert.equal(h.instance.data.canSubmit, true);
});

test('异常取消回执不能显示成功或允许重复提交，保留原因并引导查询结果', async () => {
  const success = task({ status: 'cancelled', last_event_type: 'cancel', version_no: 4 });
  for (const response of [null, { ...success, id: otherId }, { ...success, status: 'completed' },
    { ...success, last_event_type: 'reject' }, { ...success, version_no: 3 }, { ...success, version_no: undefined }]) {
    const h = harness({ coordinateTask: async () => response }); await h.instance.begin(false); reason(h); h.instance.submit();
    await h.modals[0].success({ confirm: true });
    assert.equal(h.events.length, 0); assert.equal(h.instance.data.submitting, false); assert.equal(h.instance.data.needsRefresh, true);
    assert.equal(h.instance.data.canSubmit, false); assert.equal(h.instance.data.note, '误建，重复任务'); assert.match(h.instance.data.error, /刷新任务状态/);
  }
});

test('宿主版本改变保留已输入原因，取消旧确认后主动刷新', async () => {
  const h = harness(); await h.instance.begin(false); reason(h); h.instance.submit(); h.change({ taskVersion: 4 });
  assert.equal(h.instance.data.canSubmit, false); assert.equal(h.instance.data.note, '误建，重复任务'); assert.equal(h.instance.data.needsRefresh, true);
  await h.modals[0].success({ confirm: true }); assert.equal(h.writes.length, 0);
  await h.instance.refresh(); assert.equal(h.instance.data.canSubmit, true); assert.equal(h.instance.data.note, '误建，重复任务');
});

test('弹层独立于选人目录，支持原生弹层、字数限制和明确的保留记录说明', () => {
  const source = fs.readFileSync(filename, 'utf8'); const markup = fs.readFileSync(filename.replace('.js', '.wxml'), 'utf8');
  assert.doesNotMatch(source, /[Rr]ecipient|[Rr]eassignment|[Mm]ember|[Tt]eam/);
  assert.match(markup, /root-portal/); assert.match(markup, /maxlength="\{\{500\}\}"/); assert.match(markup, /记录会保留/);
});
