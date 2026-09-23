const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const taskId = index => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
const summary = n => ({ today_completed: n, today_pending: 0, all_pending: n });
const snapshot = (n, ids = [], status = 'completed') => ({
  metrics: summary(n), items: ids.map(id => ({ id, status, attributes: {}, handover_required: false })),
});
const businessNote = id => ({
  id, template_code: 'business_changed', object_type: 'opportunity', object_id: taskId(999),
  status: 'pending', title: '合成项目有进展', created_at: '2026-09-14T01:00:00Z',
  payload: { tone: 'green', customer_id: 'customer', opportunity_id: 'opportunity', changes: [] },
});

function loadHome(overrides = {}, { deferredRender = false } = {}) {
  const filename = path.resolve(__dirname, '../miniprogram/pages/index/index.js');
  const app = {
    ensureLogin: () => true,
    globalData: {
      role: 'sales',
      session: { remote: true, workspaceId: 'w', userId: 'u', userName: '合成销售',
        role: 'sales', scope: '仅本人', teamIds: [], loginAt: 1, permissionVersion: 'v1' },
      roles: { sales: { name: '销售', scope: '仅本人' } },
    },
  };
  const storage = new Map(), errors = [], marked = [], writes = [], renderCallbacks = [];
  const api = {
    isEnabled: () => true,
    getAssistantHome: async () => ({ archived_visits: [] }),
    listCustomers: async () => ({ items: [] }),
    getTaskOverview: async () => snapshot(0),
    listNotifications: async () => ({ items: [] }),
    markNotificationRead: async id => { marked.push(id); },
    ...overrides,
  };
  let page;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => { page = definition; }, getApp: () => app,
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    console: { error: (...args) => errors.push(args) },
    wx: { vibrateShort() {}, showToast() {}, getStorageSync: key => storage.get(key),
      setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key) },
    setTimeout, clearTimeout, setInterval, clearInterval,
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = (values, callback) => {
    writes.push(values); Object.assign(page.data, values);
    if (callback) { if (deferredRender) renderCallbacks.push(callback); else callback(); }
  };
  return { page, app, api, marked, errors, writes, renderCallbacks };
}
function taskCards(page, count) {
  return Array.from({ length: count }, (_, index) => page.buildRemoteNotificationMessage({
    id: 'task-note-' + index, object_type: 'task', object_id: taskId(index + 1),
    template_code: 'task_assigned', body: '合成任务', payload: {},
  }, { scope: '仅本人' }));
}
const pendingValue = page => page.data.overviewMetrics.find(item => item.key === 'all_pending')?.value;
const taskValues = page => page.data.messages.filter(message => message.id.startsWith('remote_task-note-'))
  .map(message => message.card.metrics.find(metric => metric.label === '任务状态').value);

test('认领通过和驳回进入专用通知卡，显示后已读、重复刷新去重且不当作任务查详情', async () => {
  const items = ['客户认领已通过', '客户认领未通过'].map((title, index) => ({
    id: 'claim-' + index, template_code: 'customer_claim', title, status: 'pending',
    object_type: 'customer', object_id: 'customer-' + index, created_at: '2026-09-15T01:00:00Z',
    payload: { customer_name: '合成认领客户' + index }, body: index ? '公司编号需重新核实' : '运营已确认认领申请',
  }));
  const taskRequests = [];
  const { page, marked, renderCallbacks } = loadHome({
    listNotifications: async () => ({ items }),
    getTaskOverview: async ids => { taskRequests.push(ids); return snapshot(0); },
  }, { deferredRender: true });
  await page.syncRole(); renderCallbacks.splice(0).forEach(callback => callback());
  await page.consumeRemoteTaskNotification();
  const approved = page.data.messages.find(item => item.id === 'remote_claim-0').card;
  const rejected = page.data.messages.find(item => item.id === 'remote_claim-1').card;
  assert.equal(approved.eyebrow, '客户认领'); assert.equal(approved.subtitle, '合成认领客户0');
  assert.equal(approved.metrics[0].value, '已通过'); assert.equal(approved.tone, 'green');
  assert.equal(approved.action.code, 'open_assigned_customer'); assert.equal(approved.action.customerId, 'customer-0');
  assert.equal(rejected.rows[0].title, '驳回原因'); assert.equal(rejected.rows[0].meta, '公司编号需重新核实');
  assert.equal(rejected.action.code, 'open_customer_claim'); assert.equal(rejected.action.customerId, undefined);
  assert.ok(taskRequests.every(ids => !ids || !ids.length));
  assert.deepEqual(marked, []);
  renderCallbacks.splice(0).forEach(callback => callback()); await tick();
  assert.deepEqual(marked.slice().sort(), ['claim-0', 'claim-1']);
  await page.consumeRemoteTaskNotification();
  renderCallbacks.splice(0).forEach(callback => callback()); await tick();
  assert.equal(page.data.messages.filter(item => item.id.startsWith('remote_claim-')).length, 2);
  assert.equal(marked.length, 2);
});

test('释放回执返回名单，待审批和未知认领事件不冒充审批通过或跳转任务', () => {
  const { page } = loadHome();
  for (const title of ['客户已释放', '客户认领待审批', '后续新增事件']) {
    const card = page.buildRemoteNotificationMessage({ template_code: 'customer_claim', title,
      object_id: 'customer', payload: null }, {}).card;
    assert.notEqual(card.metrics[0].value, '已通过');
    assert.equal(card.subtitle, '客户信息暂不可用');
    if (title === '客户已释放') assert.equal(card.action.code, 'open_customer_claim');
    else assert.equal(card.action, null);
  }
});

test('FDE主管总览使用服务端完整计数，不自动取完团队任务', async () => {
  let fullListReads = 0;
  const { page, app } = loadHome({
    getAssistantHome: async () => ({ archived_visits: [], team_summary: { overdue: 4, claim: 23, handover: 2 } }),
    listTasks: async () => { fullListReads += 1; throw new Error('不得取全量'); },
  });
  app.globalData.role = 'fde_lead';
  app.globalData.session.role = 'fde_lead';
  app.globalData.roles.fde_lead = { name: 'FDE主管', scope: '部门' };
  await page.syncRole();
  assert.equal(fullListReads, 0);
  assert.deepEqual({ ...page.data.fdeTeamSummary }, { overdue: 4, claim: 23, handover: 2 });
  app.globalData.role = app.globalData.session.role = 'sales';
  await page.syncRole();
  assert.equal(page.data.fdeTeamSummary, null);
});

test('通知先展示，辅助任务状态超时不阻塞业务动态；重试后恢复任务状态与摘要', async () => {
  const { page, api, marked } = loadHome();
  await page.syncRole();
  page.data.messages = taskCards(page, 1);
  api.getTaskOverview = async ids => snapshot(7, ids, 'pending_execution');
  await page.refreshHomeTaskState([taskId(1)]);
  const delayed = deferred();
  api.getTaskOverview = () => delayed.promise;
  api.listNotifications = async () => ({ items: [businessNote('business-new'), {
    id: 'visit-new', template_code: 'visit_archived', object_type: 'visit', object_id: 'visit',
    title: '拜访已归档', status: 'pending', payload: {},
  }] });
  const poll = page.consumeRemoteTaskNotification();
  await tick();
  assert.ok(page.data.messages.some(message => message.id === 'remote_business-new'));
  assert.ok(page.data.messages.some(message => message.id === 'remote_visit-new'));
  assert.deepEqual(marked, ['business-new', 'visit-new']);
  assert.equal(pendingValue(page), '7');
  assert.deepEqual(Array.from(taskValues(page)), ['已接受']);
  delayed.reject(new Error('合成状态请求超时'));
  await poll;
  assert.equal(page.notificationLoading, false);
  assert.equal(pendingValue(page), '7');
  assert.deepEqual(Array.from(taskValues(page)), ['已接受']);
  api.getTaskOverview = async ids => snapshot(8, ids);
  await page.consumeRemoteTaskNotification();
  assert.equal(pendingValue(page), '8');
  assert.deepEqual(Array.from(taskValues(page)), ['已完成']);
  assert.equal(page.data.messages.filter(message => message.id === 'remote_business-new').length, 1);
  assert.deepEqual(marked, ['business-new', 'visit-new']);
});

test('250 张任务卡按 100/100/50 原子更新，中间批次失败保留全部旧状态并可恢复', async () => {
  const { page, api } = loadHome();
  await page.syncRole();
  page.data.messages = taskCards(page, 250);
  const ids = Array.from({ length: 250 }, (_, index) => taskId(index + 1));
  api.getTaskOverview = async batch => snapshot(7, batch, 'pending_execution');
  await page.refreshHomeTaskState(ids);
  const calls = [];
  api.listNotifications = async () => ({ items: [businessNote('during-failure')] });
  api.getTaskOverview = async batch => {
    calls.push(batch.length);
    if (calls.length === 2) throw new Error('第二批失败');
    return snapshot(250, batch);
  };
  await page.consumeRemoteTaskNotification();
  assert.deepEqual(calls, [100, 100]);
  assert.equal(page.notificationLoading, false);
  assert.equal(pendingValue(page), '7');
  assert.equal(taskValues(page).filter(value => value === '已接受').length, 250);
  assert.equal(Array.from(page.currentTaskMap.values()).filter(item => item.status === 'pending_execution').length, 250);
  assert.ok(page.data.messages.some(message => message.id === 'remote_during-failure'));
  calls.length = 0;
  api.getTaskOverview = async batch => { calls.push(batch.length); return snapshot(250, batch); };
  await page.consumeRemoteTaskNotification();
  assert.deepEqual(calls, [100, 100, 50]);
  assert.equal(pendingValue(page), '250');
  assert.equal(taskValues(page).filter(value => value === '已完成').length, 250);
  assert.equal(page.currentTaskMap.size, 250);
  // The initial summary endpoint returns no card IDs and must not erase them.
  await page.syncRole();
  assert.equal(page.currentTaskMap.size, 250);
  assert.equal(taskValues(page).filter(value => value === '已完成').length, 250);
});

for (const olderSource of ['poll', 'sync']) {
  test(`同账号 ${olderSource === 'poll' ? '旧轮询' : '旧首页初始化'} 晚返回不能覆盖较新的摘要`, async () => {
    const { page, api } = loadHome();
    await page.syncRole();
    page.data.messages = taskCards(page, 1);
    const olderResponse = deferred();
    let calls = 0;
    api.getTaskOverview = ids => ++calls === 1 ? olderResponse.promise : Promise.resolve(snapshot(9, ids));
    const older = olderSource === 'poll' ? page.consumeRemoteTaskNotification() : page.syncRole();
    await tick();
    await (olderSource === 'poll' ? page.syncRole() : page.consumeRemoteTaskNotification());
    assert.equal(pendingValue(page), '9');
    olderResponse.resolve(snapshot(4, [taskId(1)], 'pending_confirm'));
    await older;
    assert.equal(pendingValue(page), '9');
  });
}

for (const change of ['account', 'login', 'permissions']) {
  test(`${change} 变化后旧通知不落入新身份，旧请求结束不能释放新轮询`, async () => {
    const { page, api, app, marked } = loadHome();
    await page.syncRole();
    const oldNotes = deferred(), newNotes = deferred();
    let calls = 0;
    api.listNotifications = () => ++calls === 1 ? oldNotes.promise : newNotes.promise;
    const older = page.consumeRemoteTaskNotification();
    if (change === 'account') app.globalData.session.userId = 'other-user';
    if (change === 'login') app.globalData.session.loginAt = 2;
    if (change === 'permissions') app.globalData.session.permissionVersion = 'v2';
    api.getTaskOverview = async ids => snapshot(9, ids);
    await page.syncRole();
    const newer = page.consumeRemoteTaskNotification();
    oldNotes.resolve({ items: [businessNote('old-private')] });
    await older;
    assert.equal(page.notificationLoading, true);
    assert.equal(pendingValue(page), '9');
    assert.ok(!page.data.messages.some(message => message.id === 'remote_old-private'));
    assert.deepEqual(marked, []);
    newNotes.resolve({ items: [businessNote('new-visible')] });
    await newer;
    assert.equal(page.notificationLoading, false);
    assert.ok(page.data.messages.some(message => message.id === 'remote_new-visible'));
    assert.deepEqual(marked, ['new-visible']);
  });

  test(`${change} 变化后旧任务摘要与状态晚返回不会污染新身份`, async () => {
    const { page, api, app } = loadHome();
    await page.syncRole();
    page.data.messages = taskCards(page, 1);
    const oldState = deferred();
    api.getTaskOverview = () => oldState.promise;
    const older = page.consumeRemoteTaskNotification();
    await tick();
    if (change === 'account') app.globalData.session.userId = 'other-user';
    if (change === 'login') app.globalData.session.loginAt = 2;
    if (change === 'permissions') app.globalData.session.permissionVersion = 'v2';
    api.getTaskOverview = async ids => snapshot(9, ids);
    await page.syncRole();
    oldState.resolve(snapshot(4, [taskId(1)]));
    await older;
    assert.equal(pendingValue(page), '9');
    assert.equal(page.currentTaskMap.size, 0);
    assert.equal(taskValues(page).length, 0);
  });
}

test('onShow 的通知请求无需等待首页任务摘要，页面卸载后不再提交迟到状态', async () => {
  const taskResponse = deferred();
  const { page } = loadHome({
    getTaskOverview: () => taskResponse.promise,
    listNotifications: async () => ({ items: [businessNote('immediate')] }),
  });
  page.startNotificationPolling = () => {};
  page.onShow();
  await tick();
  assert.ok(page.data.messages.some(message => message.id === 'remote_immediate'));
  page.onUnload();
  taskResponse.resolve(snapshot(99));
  await tick();
  assert.equal(pendingValue(page), undefined);
});

for (const newestFirst of [false, true]) {
  test(`原生渲染回调晚于下一轮轮询，${newestFirst ? '新' : '旧'}回调先完成仍只确认一次已展示通知`, async () => {
    const { page, marked, renderCallbacks } = loadHome({
      listNotifications: async () => ({ items: [businessNote('late-render')] }),
    }, { deferredRender: true });
    await page.consumeRemoteTaskNotification();
    await page.consumeRemoteTaskNotification();
    assert.equal(page.data.messages.length, 1);
    assert.deepEqual(marked, []);
    assert.equal(renderCallbacks.length, 2);
    const callbacks = renderCallbacks.splice(0);
    if (newestFirst) callbacks.reverse();
    callbacks.forEach(callback => callback());
    await tick();
    assert.deepEqual(marked, ['late-render']);
    await page.consumeRemoteTaskNotification();
    renderCallbacks.splice(0).forEach(callback => callback());
    await tick();
    assert.deepEqual(marked, ['late-render']);
  });
}

test('已读请求失败保留卡片，下一次正常轮询补发，成功后陈旧未读响应不会再发送', async () => {
  const attempts = [];
  const { page } = loadHome({
    listNotifications: async () => ({ items: [businessNote('retry-read')] }),
    markNotificationRead: async id => {
      attempts.push(id);
      if (attempts.length === 1) throw new Error('合成请求超时');
    },
  });
  await page.consumeRemoteTaskNotification();
  await tick();
  assert.deepEqual(attempts, ['retry-read']);
  assert.equal(page.data.messages.length, 1);
  await tick();
  assert.equal(attempts.length, 1, '不能在失败后立即无限重试');
  await page.consumeRemoteTaskNotification();
  await tick();
  await page.consumeRemoteTaskNotification();
  await tick();
  assert.deepEqual(attempts, ['retry-read', 'retry-read']);
  assert.equal(page.data.messages.length, 1);
});

test('已读请求有界并发且进行中不重复，辅助摘要不必等待回执', async () => {
  const waiting = [], attempts = [];
  let active = 0, peak = 0;
  const { page } = loadHome({
    listNotifications: async () => ({ items: Array.from({ length: 8 }, (_, i) => businessNote('bounded-' + i)) }),
    markNotificationRead: id => {
      const response = deferred(); waiting.push(response); attempts.push(id);
      peak = Math.max(peak, ++active);
      return response.promise.finally(() => { active--; });
    },
  });
  await page.consumeRemoteTaskNotification();
  assert.equal(pendingValue(page), '0');
  assert.equal(page.notificationLoading, false);
  assert.equal(attempts.length, 3);
  await page.consumeRemoteTaskNotification();
  assert.equal(attempts.length, 3);
  while (active) {
    waiting.splice(0).forEach(response => response.resolve());
    await tick();
  }
  assert.equal(peak, 3);
  assert.equal(attempts.length, 8);
  assert.equal(new Set(attempts).size, 8);
  await page.consumeRemoteTaskNotification();
  await tick();
  assert.equal(attempts.length, 8);
});

for (const change of ['account', 'login', 'permissions']) {
  test(`${change} 变化后旧原生渲染回调不确认新身份通知`, async () => {
    const { page, api, app, marked, renderCallbacks } = loadHome({
      listNotifications: async () => ({ items: [businessNote('old-render')] }),
    }, { deferredRender: true });
    await page.consumeRemoteTaskNotification();
    const oldCallback = renderCallbacks.shift();
    if (change === 'account') app.globalData.session.userId = 'other-user';
    if (change === 'login') app.globalData.session.loginAt = 2;
    if (change === 'permissions') app.globalData.session.permissionVersion = 'v2';
    api.listNotifications = async () => ({ items: [businessNote('current-render')] });
    await page.consumeRemoteTaskNotification();
    oldCallback();
    assert.deepEqual(marked, []);
    renderCallbacks.splice(0).forEach(callback => callback());
    await tick();
    assert.deepEqual(marked, ['current-render']);
  });

  test(`${change} 变化后旧回执结束不能清除或确认新身份待重试队列`, async () => {
    const oldResponse = deferred(); let attempts = 0;
    const { page, app } = loadHome({
      listNotifications: async () => ({ items: [businessNote('same-visible-id')] }),
      markNotificationRead: () => {
        attempts++;
        if (attempts === 1) return oldResponse.promise;
        if (attempts === 2) return Promise.reject(new Error('新会话首次失败'));
        return Promise.resolve();
      },
    });
    await page.consumeRemoteTaskNotification();
    if (change === 'account') app.globalData.session.userId = 'other-user';
    if (change === 'login') app.globalData.session.loginAt = 2;
    if (change === 'permissions') app.globalData.session.permissionVersion = 'v2';
    await page.consumeRemoteTaskNotification();
    await tick();
    assert.equal(attempts, 2);
    oldResponse.resolve();
    await tick();
    await page.consumeRemoteTaskNotification();
    await tick();
    assert.equal(attempts, 3);
  });
}

test('隐藏后首次到达的卡片等返回显示才确认，已读和不支持的通知不发回执', async () => {
  const { page, marked, renderCallbacks } = loadHome({
    listNotifications: async () => ({ items: [businessNote('hidden'),
      { ...businessNote('already-read'), status: 'read' },
      { ...businessNote('unsupported'), template_code: 'unrendered-template' }] }),
  }, { deferredRender: true });
  await page.consumeRemoteTaskNotification();
  page.onHide();
  renderCallbacks.splice(0).forEach(callback => callback());
  assert.deepEqual(marked, []);
  page.homeVisible = true;
  await page.consumeRemoteTaskNotification();
  renderCallbacks.splice(0).forEach(callback => callback());
  await tick();
  assert.deepEqual(marked, ['hidden']);
  assert.equal(page.data.messages.length, 2);
});

test('卸载后原生渲染回调不写已读，进行中回执返回不恢复队列', async () => {
  const response = deferred();
  const { page, marked, renderCallbacks } = loadHome({
    listNotifications: async () => ({ items: [businessNote('unloaded')] }),
    markNotificationRead: id => { marked.push(id); return response.promise; },
  }, { deferredRender: true });
  await page.consumeRemoteTaskNotification();
  renderCallbacks.shift()();
  await page.consumeRemoteTaskNotification();
  page.onUnload();
  renderCallbacks.splice(0).forEach(callback => callback());
  response.resolve();
  await tick();
  assert.deepEqual(marked, ['unloaded']);
  assert.equal(page.notificationReceipts, null);
});
