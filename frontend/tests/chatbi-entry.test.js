const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Existing conversation cases exercise the retained phase-two opt-in path.
function loadPage(api = {}, config = { HOME_CHATBI_ENABLED: true, HOME_MESSAGE_ORDER: 'asc' }) {
  const file = path.resolve(__dirname, '../miniprogram/pages/index/index.js');
  const app = { globalData: {
    role: 'sales',
    session: { workspaceId: 'w1', userId: 'u1', userName: '合成销售', role: 'sales', scope: '仅本人', loginAt: 1, teamIds: [] },
    roles: { sales: { name: '销售', scope: '仅本人' }, manager: { name: '经理', scope: '全部团队' } },
  } };
  const toasts = [], navigations = [], storage = new Map();
  const client = {
    getAssistantHome: async () => ({ archived_visits: [] }),
    getTaskOverview: async () => ({items:[], metrics:{today_completed:0,today_pending:0,all_pending:0}}),
    listTasks: async () => ({ items: [] }), listCustomers: async () => ({ items: [] }),
    ...api,
  };
  let page;
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => { page = value; }, getApp: () => app,
    require: name => name.endsWith('apiClient') ? client : name === '../../config' ? config : require(path.resolve(path.dirname(file), name)),
    setInterval, clearInterval, setTimeout, clearTimeout,
    wx: { vibrateShort() {}, showToast: value => toasts.push(value.title), navigateTo: value => navigations.push(value.url),
      setStorageSync: (key, value) => storage.set(key, value), getStorageSync: key => storage.get(key) },
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = (data, callback) => { Object.assign(page.data, data); if (callback) callback(); };
  return { page, app, toasts, navigations };
}

test('三类销售按服务端能力保留三个可用入口，FDE两类只保留拜访和任务', async t => {
  const access = require('../miniprogram/utils/access');
  const previousWx = global.wx;
  global.wx = { showToast() {} };
  t.after(() => { global.wx = previousWx; });
  for (const role of ['sales', 'supervisor', 'manager', 'fde', 'fde_lead']) {
    const { page, app, navigations } = loadPage();
    const fde = access.isFde(role);
    app.globalData.role = role;
    app.globalData.roles[role] = { name: role, scope: '授权范围' };
    app.globalData.session = { ...app.globalData.session, role, capabilities: {
      'customer.claim': !fde, 'visit.create': true, 'task.create': true,
    } };
    app.can = key => access.can(app.globalData.session, key);
    access.protectActions(app, page, 'index');
    await page.syncRole();
    assert.deepEqual(Array.from(page.data.quickActions, item => item.action),
      fde ? ['记录客户拜访', '创建任务'] : ['客户认领', '记录客户拜访', '创建任务']);
    for (const item of page.data.quickActions) page.tapQuickAction({ currentTarget: { dataset: { action: item.action } } });
    assert.deepEqual(navigations, [
      ...(!fde ? ['/pages/customer-claim/index'] : []),
      '/pages/visit-entry/index', '/pages/management-task-create/index',
    ]);
    navigations.length = 0;
    app.globalData.session.capabilities = {};
    await page.syncRole();
    assert.equal(page.data.quickActions.length, 0, '服务端收回能力后不按角色强行放行');
    for (const action of ['客户认领', '记录客户拜访', '创建任务']) page.tapQuickAction({ currentTarget: { dataset: { action } } });
    assert.equal(navigations.length, 0, '迟到的旧按钮事件也不能跳转');
  }
});

const result = { result: { title: '合成问数结果', summary: '本人有1个商机',
  metrics: [{ label: '商机', value: '1个' }], rows: [{ title: '合成客户', detail: '待跟进' }] } };

test('显式启用二期文字问数后提交一次，等待期间不重复发送，结果沿用原卡片', async () => {
  const pending = deferred(), calls = [];
  const { page } = loadPage({ queryChatBI: question => { calls.push(question); return pending.promise; } });
  page.inputText({ detail: { value: '  我有多少商机？  ' } });
  const completion = page.sendText();
  assert.equal(page.data.textInput, '');
  assert.equal(page.data.isProcessing, true);
  assert.equal(page.data.managementTaskMode, false);
  assert.equal(page.data.visitRecordingMode, false);
  page.inputText({ detail: { value: '重复点击' } });
  page.sendText();
  assert.deepEqual(calls, ['我有多少商机？']);
  pending.resolve(result);
  await completion;
  assert.equal(page.data.isProcessing, false);
  assert.equal(page.data.messages.filter(message => message.from === 'user').length, 1);
  const card = page.data.messages.find(message => message.kind === 'data-card').card;
  assert.equal(card.title, result.result.title);
  assert.deepEqual(JSON.parse(JSON.stringify(card.metrics)), result.result.metrics);
  assert.equal(card.rows[0].meta, '待跟进');
  assert.equal(card.action.code, 'open_workbench');
});

test('服务失败后释放输入，用户可重新提问，没有自动重发', async () => {
  let count = 0;
  const { page } = loadPage({ queryChatBI: async () => {
    count += 1;
    if (count === 1) throw new Error('问数服务暂时不可用');
    return result;
  } });
  page.inputText({ detail: { value: '第一次提问' } });
  await page.sendText();
  assert.equal(count, 1);
  assert.equal(page.data.isProcessing, false);
  assert.ok(page.data.messages.some(message => message.text === '问数服务暂时不可用'));
  page.inputText({ detail: { value: '重新提问' } });
  await page.sendText();
  assert.equal(count, 2);
  assert.equal(page.data.messages.filter(message => message.kind === 'data-card').length, 1);
});

test('已有经营分析执行时，文字和录音均不并发发起', () => {
  const { page, toasts } = loadPage({ queryChatBI: () => { throw new Error('不应发送'); } });
  page.data.isThinking = true;
  page.data.textInput = '稍后发送';
  page.requestRecordPermission = () => { throw new Error('不应申请录音'); };
  page.sendText();
  page.startRecord();
  assert.equal(page.data.textInput, '稍后发送');
  assert.equal(page.data.messages.length, 0);
  assert.equal(toasts.length, 1);
});

for (const [name, change] of [
  ['换销售账号', app => { app.globalData.session.userId = 'u2'; }],
  ['换工作空间', app => { app.globalData.session.workspaceId = 'w2'; }],
  ['换角色', app => { app.globalData.role = 'manager'; }],
  ['调整团队范围', app => { app.globalData.session.teamIds = ['t2']; }],
  ['同账号重新登录', app => { app.globalData.session.loginAt = 2; }],
]) {
  test(`${name}后迟到结果不能进入当前页面或结束新的请求`, async () => {
    const first = deferred(), second = deferred();
    let calls = 0;
    const { page, app } = loadPage({ queryChatBI: () => (++calls === 1 ? first : second).promise });
    const oldRequest = page.runChatBIQuestion('旧问题', false, 0);
    change(app);
    await page.syncRole();
    assert.equal(page.data.isProcessing, false);
    const newRequest = page.runChatBIQuestion('新问题', false, 0);
    first.resolve({ result: { title: '不应显示的旧账号结果' } });
    await oldRequest;
    assert.equal(page.data.isProcessing, true);
    assert.ok(!JSON.stringify(page.data.messages).includes('不应显示'));
    second.resolve(result);
    await newRequest;
    assert.equal(page.data.isProcessing, false);
    assert.equal(page.data.messages.filter(message => message.kind === 'data-card').length, 1);
  });
}

test('语音转写后沿用相同问数调用和结果卡，不调用拜访结构化', async () => {
  const calls = [];
  const { page } = loadPage({
    transcribeAudio: async (file, purpose) => { calls.push([file, purpose]); return { text: ' 我有多少商机？ ' }; },
    queryChatBI: async question => { calls.push(question); return result; },
  });
  await page.processChatBIRecording({ tempFilePath: '/synthetic/question.mp3', duration: 2000 }, '00:02');
  assert.deepEqual(calls, [['/synthetic/question.mp3', 'chatbi'], '我有多少商机？']);
  assert.equal(page.data.isProcessing, false);
  assert.equal(page.data.messages.filter(message => message.kind === 'data-card').length, 1);
  assert.ok(page.data.messages.some(message => (message.text || '').includes('（语音 00:02）')));
});

test('语音转写中退出登录，不继续用旧音频内容发起问数', async () => {
  const transcript = deferred();
  let queries = 0;
  const { page, app, toasts } = loadPage({
    transcribeAudio: () => transcript.promise,
    queryChatBI: async () => { queries += 1; return result; },
  });
  const completion = page.processChatBIRecording({ tempFilePath: '/synthetic/question.mp3' }, '00:02');
  app.globalData.session = null;
  transcript.resolve({ text: '旧账号语音' });
  await completion;
  assert.equal(queries, 0);
  assert.equal(page.data.messages.length, 0);
  assert.equal(toasts.length, 0);
});

test('空语音可恢复；页面销毁后旧问数错误不显示', async () => {
  const failed = deferred();
  const { page, toasts } = loadPage({
    transcribeAudio: async () => ({ text: '' }), queryChatBI: () => failed.promise,
  });
  await page.processChatBIRecording({ tempFilePath: '/synthetic/question.mp3' }, '00:02');
  assert.equal(page.data.isProcessing, false);
  assert.deepEqual(toasts, ['未识别到有效语音内容']);
  const completion = page.runChatBIQuestion('待返回', false, 0);
  page.onUnload();
  failed.reject(new Error('不应在销毁后显示'));
  await completion;
  assert.equal(page.data.messages.length, 0);
});

test('退出页面触发录音停止回调时，不再启动新的语音转写', () => {
  const { page } = loadPage({ transcribeAudio: () => { throw new Error('页面退出后不应再上传录音'); } });
  page.data.isRecording = true;
  page.recorderManager = {
    stop: () => page.processChatBIRecording({ tempFilePath: '/synthetic/question.mp3' }, '00:02'),
  };
  assert.doesNotThrow(() => page.onUnload());
  assert.equal(page.data.messages.length, 0);
  assert.equal(page.data.isProcessing, false);
});

test('停留在旧消息时提问，新问题和回答按顺序排在底部并定位，保留原消息', async () => {
  const pending = deferred();
  const { page } = loadPage({ queryChatBI: () => pending.promise });
  const history = Array.from({ length: 50 }, (_, index) => ({
    id: `old-${index}`, from: 'agent', kind: 'text', text: `历史消息${index}`, sortAt: 1000 + index,
  }));
  page.data.messages = history.slice();
  page.data.chatScrollTarget = 'chat-message-old-0';
  page.inputText({ detail: { value: '查看我的待办' } });
  const completion = page.sendText();
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
  assert.equal(page.data.isProcessing, true);
  pending.resolve(result);
  await completion;
  const answer = page.data.messages.find(message => message.kind === 'data-card');
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
  assert.equal(page.data.messages.at(-1).id, answer.id);
  assert.equal(page.data.messages.at(-2).from, 'user');
  assert.equal(page.data.messages.filter(message => message.id.startsWith('old-')).length, 50);
  assert.equal(page.data.isProcessing, false);
});

test('快捷问数定位到底部等待提示，失败回复也保留在问题下方', async () => {
  const pending = deferred();
  const { page } = loadPage({ queryChatBI: () => pending.promise });
  const completion = page.runChatBIQuestion('我的客户情况', true, 0);
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
  assert.ok(page.data.messages.some(message => message.text === '我的客户情况'));
  pending.reject(new Error('连接暂时不可用'));
  await completion;
  const errorMessage = page.data.messages.find(message => message.text === '连接暂时不可用');
  assert.ok(errorMessage);
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
  assert.equal(page.data.isProcessing, false);
});

test('用户浏览旧消息后可再次定位列表底部，后台首页刷新不抢走结果位置', async () => {
  const { page } = loadPage();
  await page.syncRole();
  const targets = [];
  const update = page.setData;
  page.setData = (data, callback) => {
    if ('chatScrollTarget' in data) targets.push(data.chatScrollTarget);
    update(data, callback);
  };
  page.data.chatScrollTarget = 'chat-latest';
  page.beginChatBIRequest();
  assert.deepEqual(targets.slice(-2), ['', 'chat-latest']);
  page.appendMessage({ id: 'latest-answer', from: 'agent', kind: 'text', text: '已完成查询' });
  const target = page.data.chatScrollTarget;
  await page.syncRole();
  assert.equal(page.data.chatScrollTarget, target);
  assert.ok(page.data.messages.some(message => message.id === 'latest-answer'));
});

test('同一毫秒产生的问题与回答仍保持先问后答，不因排序颠倒', () => {
  const { page } = loadPage();
  page.appendMessage({ id: 'question', sortAt: 1000, from: 'user', kind: 'text', text: '合成问题' });
  page.appendMessage({ id: 'answer', sortAt: 1000, from: 'agent', kind: 'text', text: '合成回答' });
  assert.deepEqual(Array.from(page.data.messages, message => message.id), ['question', 'answer']);
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
});

test('一期默认隐藏问数，文字、录音和直接回调都不发起查询，三个业务入口保留', async () => {
  const config = require('../miniprogram/config');
  let queries = 0, transcriptions = 0, permissions = 0;
  const { page } = loadPage({
    queryChatBI: () => { queries += 1; return Promise.resolve(result); },
    transcribeAudio: () => { transcriptions += 1; return Promise.resolve({ text: '旧语音' }); },
  }, config);
  page.requestRecordPermission = () => { permissions += 1; };
  assert.equal(page.data.homeChatBIEnabled, false);
  page.inputText({ detail: { value: '未开放的问数' } });
  await page.sendText();
  page.startRecord();
  await page.runChatBIQuestion('旧回调', true, 0);
  await page.processChatBIRecording({ tempFilePath: '/synthetic/old.mp3' }, '00:02');
  assert.deepEqual([queries, transcriptions, permissions], [0, 0, 0]);
  assert.equal(page.data.isProcessing, false);
  assert.equal(page.data.messages.length, 0);
  await page.syncRole();
  assert.deepEqual(Array.from(page.data.quickActions, item => item.action), ['客户认领', '记录客户拜访', '创建任务']);
  const template = fs.readFileSync(path.resolve(__dirname, '../miniprogram/pages/index/index.wxml'), 'utf8');
  assert.match(template, /wx:if="\{\{homeChatBIEnabled \|\| managementTaskMode \|\| visitRecordingMode\}\}" class="voice-composer/);
});

test('一期默认最新动态在上，追加同毫秒动态保持最新优先并定位顶部', () => {
  const { page } = loadPage({}, require('../miniprogram/config'));
  page.appendMessage({ id: 'older', sortAt: 1000 });
  page.appendMessage({ id: 'newer', sortAt: 2000 });
  page.appendMessage({ id: 'latest', sortAt: 2000 });
  assert.deepEqual(Array.from(page.data.messages, item => item.id), ['latest', 'newer', 'older']);
  assert.equal(page.data.chatScrollTarget, 'chat-newest');
});

test('关闭问数不影响已有拜访与管理任务录音的启动权限流程', () => {
  const { page } = loadPage({}, require('../miniprogram/config'));
  let permissions = 0;
  page.requestRecordPermission = () => { permissions += 1; };
  page.data.visitRecordingMode = true;
  page.startRecord();
  assert.equal(permissions, 1);
  page.data.isStarting = false;
  page.data.visitRecordingMode = false;
  page.data.managementTaskMode = true;
  page.startRecord();
  assert.equal(permissions, 2);
});

test('排序与入口配置互相独立，切换顺序不隐式开放问数', async () => {
  for (const [enabled, order, expected] of [[false, 'asc', ['old', 'new']], [true, 'desc', ['new', 'old']]]) {
    let queries = 0;
    const { page } = loadPage({ queryChatBI: async () => { queries += 1; return result; } },
      { HOME_CHATBI_ENABLED: enabled, HOME_MESSAGE_ORDER: order });
    page.appendMessage({ id: 'old', sortAt: 1000 });
    page.appendMessage({ id: 'new', sortAt: 2000 });
    assert.deepEqual(Array.from(page.data.messages, item => item.id), expected);
    assert.equal(page.data.homeChatBIEnabled, enabled);
    await page.runChatBIQuestion('合成问题', true, 0);
    assert.equal(queries, enabled ? 1 : 0);
  }
});

test('公司顺序随首页载入生效，后续刷新不翻转也不开放问数', async () => {
  let order = 'asc';
  const { page } = loadPage({ getAssistantHome: async () => ({ archived_visits: [], display_policy: {
    id: 'policy-1', version: 1, definition: { message_order: order, chatbi_enabled: true },
  } }) }, { HOME_CHATBI_ENABLED: false, HOME_MESSAGE_ORDER: 'desc' });
  await page.syncRole();
  page.setData({ messages: [] });
  page.appendMessage({ id: 'old', sortAt: 1 });
  page.appendMessage({ id: 'new', sortAt: 2 });
  assert.deepEqual(Array.from(page.data.messages, x => x.id), ['old', 'new']);
  assert.equal(page.data.homeChatBIEnabled, false);
  assert.equal(page.data.chatScrollTarget, 'chat-latest');
  order = 'desc';
  page.refreshVisitReceipts();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.homeOrder, 'asc');
  await page.syncRole();
  assert.deepEqual(Array.from(page.data.messages, x => x.id), ['new', 'old']);
});

test('旧首页的迟到配置不能覆盖新账号展示顺序', async () => {
  const pending = deferred(); let calls = 0;
  const { page, app } = loadPage({ getAssistantHome: () => ++calls === 1 ? pending.promise : Promise.resolve({
    archived_visits: [], display_policy: { definition: { message_order: 'desc' } },
  }) });
  const older = page.syncRole();
  app.globalData.session.userId = 'u2';
  await page.syncRole();
  pending.resolve({ archived_visits: [], display_policy: { definition: { message_order: 'asc' } } });
  await older;
  assert.equal(page.homeOrder, 'desc');
});

test('FDE与负责人同样遵循二期问数开关，保留两个真实业务入口', async () => {
  for (const role of ['fde', 'fde_lead']) {
    let queries = 0, transcriptions = 0, permissions = 0;
    const { page, app } = loadPage({
      queryChatBI: async () => { queries += 1; return result; },
      transcribeAudio: async () => { transcriptions += 1; return { text: 'synthetic' }; },
    }, require('../miniprogram/config'));
    app.globalData.role = role;
    app.globalData.session = { ...app.globalData.session, role };
    app.globalData.roles[role] = { name: role === 'fde' ? 'FDE' : 'FDE主管', scope: '协助项目' };
    app.can = permission => ['visit.create', 'task.create'].includes(permission);
    await page.syncRole();
    assert.equal(page.data.isFde, true);
    assert.equal(page.data.homeChatBIEnabled, false);
    assert.deepEqual(Array.from(page.data.quickActions, item => item.action), ['记录客户拜访', '创建任务']);
    page.requestRecordPermission = () => { permissions += 1; };
    page.inputText({ detail: { value: '问数留待二期' } });
    await page.sendText();
    await page.runChatBIQuestion('旧事件回调', false, 0);
    await page.processChatBIRecording({ tempFilePath: '/synthetic/old.mp3' }, '00:02');
    page.startRecord();
    assert.deepEqual([queries, transcriptions, permissions], [0, 0, 0]);
    page.data.visitRecordingMode = true;
    page.startRecord();
    assert.equal(permissions, 1, '保留拜访录音能力');
  }
});
