const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const display = require('../miniprogram/utils/demoDisplay');
const { normalizeCustomerDetail } = require('../miniprogram/utils/customerDetail');
const { adviceResult } = require('../miniprogram/utils/customerAdvice');
const { buildVisitReceipt } = require('../miniprogram/utils/visitCards');
const DEMO = display.DEMO_WORKSPACE_ID;
const FORMAL = '00000000-0000-0000-0000-000000000002';

function freeze(value) {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

function inWorkspace(t, workspaceId) {
  const previous = global.getApp;
  global.getApp = () => ({ globalData: { session: { workspaceId } } });
  t.after(() => {
    if (previous) global.getApp = previous;
    else delete global.getApp;
  });
}

test('only the exact demo workspace and three literal labels are projected, preserving whitespace and ordinary names', () => {
  const value = ' 【演示】澄星【演示】\n【演示数据，全部业务情节为虚构】正文【验收演示数据，非真实经营事实】 ';
  assert.equal(display.displayText(value, DEMO), ' 澄星\n正文 ');
  for (const workspace of [FORMAL, undefined, null, 'demo', ` ${DEMO}`, { id: DEMO }]) {
    assert.equal(display.displayText(value, workspace), value);
  }
  const normal = '演示公司 · Demo 场景 · 演示数据脱敏 · [演示] · 【演示验收】';
  assert.equal(display.displayText(normal, DEMO), normal);
  for (const value of [null, undefined, 0, false, { title: '【演示】原始对象' }]) {
    assert.equal(display.displayText(value, DEMO), value);
  }
});

test('saved visit display removes labels while draft values, immutable evidence, identities and raw input remain intact', t => {
  inWorkspace(t, DEMO);
  const raw = freeze({ id: 'c1', name: '【演示】澄星', attributes: { synthetic: true, source: '【演示】seed' }, visits: [{
    id: 'v1', status: 'archived', customer_id: 'c1', opportunity_id: 'o1', opportunity_name: '【演示】自动化',
    partner_name_snapshot: '【演示】伙伴', visit_goal: '【演示】确认需求', next_action: '【演示】发送方案',
    follow_up_record: '【演示数据，全部业务情节为虚构】已讨论预算。',
    source_follow_up_record: '【演示】原始录入', archived_fields: { follow_up_record: '【演示】归档快照' },
    fields: { customer_needs: '【验收演示数据，非真实经营事实】知识检索' },
  }, { id: 'draft', status: 'draft', follow_up_record: '【演示】正在编辑', next_action: '【演示】草稿行动' }] });
  const before = JSON.stringify(raw);
  const result = normalizeCustomerDetail(raw);
  const visit = result.visits.find(item => item.id === 'v1');
  assert.equal(visit.customerName, '澄星');
  assert.equal(visit.opportunityName, '自动化');
  assert.equal(visit.partnerName, '伙伴');
  assert.equal(visit.title, '自动化');
  assert.equal(visit.visitGoal, '确认需求');
  assert.equal(visit.followUpRecord, '已讨论预算。');
  assert.equal(visit.nextAction, '发送方案');
  assert.equal(visit.customerNeeds, '知识检索');
  assert.equal(visit.opportunityId, 'o1');
  assert.equal(result.visits.find(item => item.id === 'draft').followUpRecord, '【演示】正在编辑');
  assert.equal(result.visits.find(item => item.id === 'draft').nextAction, '【演示】草稿行动');
  assert.equal(JSON.stringify(raw), before);
  global.getApp = () => ({ globalData: { session: { workspaceId: FORMAL } } });
  assert.equal(normalizeCustomerDetail(raw).visits.find(item => item.id === 'v1').followUpRecord, raw.visits[0].follow_up_record);
});

test('advice display and archived receipts remove labels without rewriting suggestion actions or stored receipt fields', t => {
  inWorkspace(t, DEMO);
  const raw = freeze({ id: 'a1', status: 'succeeded', summary: '【演示】跟进澄星', suggestions: [{
    id: 's1', version_no: 4, title: '【演示】推进试点', evidence: '【演示】客户已认可', action: '【演示】发送报价',
    source: { synthetic: true, title: '【演示】来源' },
  }] });
  const result = adviceResult(raw);
  assert.equal(result.summary, '跟进澄星');
  assert.equal(result.rows[0].title, '推进试点');
  assert.equal(result.rows[0].detail, '客户已认可\n建议行动：发送报价');
  assert.equal(result.rows[0].action, raw.suggestions[0].action);
  assert.equal(result.rows[0].evidence, raw.suggestions[0].evidence);
  assert.equal(result.rows[0].source, raw.suggestions[0].source);
  assert.equal(result.rows[0].version_no, 4);
  const receipt = freeze({ id: 'v1', customer_id: 'c1', customer_name: '【演示】澄星', opportunity_name: '【演示】自动化', next_action: '【演示】发报价' });
  const card = buildVisitReceipt(receipt).card;
  assert.equal(card.subtitle, '澄星');
  assert.equal(card.rows[0].meta, '自动化');
  assert.equal(card.rows[1].meta, '发报价');
  assert.equal(card.action.customerId, receipt.customer_id);
  assert.equal(receipt.next_action, '【演示】发报价');
  global.getApp = () => ({ globalData: { session: { workspaceId: FORMAL } } });
  assert.equal(adviceResult(raw).summary, raw.summary);
  assert.equal(buildVisitReceipt(receipt).card.subtitle, receipt.customer_name);
});

const notification = freeze({ items: [{ id: 'n1', title: '【演示】商机更新', body: '【演示】澄星 · 【演示】自动化',
  payload: { customer_id: 'c1', opportunity_id: 'o1', customer_name: '【演示】澄星', opportunity_name: '【演示】自动化',
    synthetic: true, source: '【演示】seed', archived_fields: { next_action: '【演示】原文' },
    changes: [{ before: '【演示】原值', after: '【演示】新值' }], audit: { title: '【演示】审计' } },
}], has_more: false, cursor: 'opaque' });

test('notification and timeline projections whitelist display fields and preserve audit/source references', () => {
  const result = display.notificationDisplayResponse(notification, DEMO);
  assert.equal(result.items[0].title, '商机更新');
  assert.equal(result.items[0].body, '澄星 · 自动化');
  assert.equal(result.items[0].payload.customer_name, '澄星');
  assert.equal(result.items[0].payload.opportunity_name, '自动化');
  for (const key of ['customer_id', 'opportunity_id', 'synthetic', 'source', 'archived_fields', 'changes', 'audit']) {
    assert.equal(result.items[0].payload[key], notification.items[0].payload[key]);
  }
  assert.equal(result.cursor, notification.cursor);
  assert.equal(notification.items[0].title, '【演示】商机更新');
  assert.equal(display.notificationDisplayResponse(notification, FORMAL), notification);
  const timeline = freeze({ items: [{ id: 'e1', title: '【演示】自动化', detail: '【演示】进入方案阶段',
    source: '【演示】seed', snapshot: { title: '【演示】历史原文' }, object_id: 'o1' }] });
  const events = display.timelineDisplayResponse(timeline, DEMO);
  assert.equal(events.items[0].title, '自动化');
  assert.equal(events.items[0].detail, '进入方案阶段');
  assert.equal(events.items[0].snapshot, timeline.items[0].snapshot);
  assert.equal(events.items[0].source, timeline.items[0].source);
  assert.equal(events.items[0].object_id, 'o1');
  assert.equal(display.timelineDisplayResponse(timeline, FORMAL), timeline);
});

function client(workspaceId, respond) {
  const storage = new Map(), requests = [];
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  const module = { exports: {} };
  const wx = { getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), request: request => { requests.push(request); respond(request); } };
  const identityModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(path.dirname(filename), 'requestIdentity.js'), 'utf8'), { module: identityModule, wx });
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { module, Map, Set, Date,
    require: name => name === './requestIdentity' ? identityModule.exports : require(path.resolve(path.dirname(filename), name)), wx,
  });
  const api = module.exports;
  api.saveAuth({ access_token: 'test-only', actor: { workspace_id: workspaceId, user_id: 'u1' } });
  return { api, storage, requests };
}

test('only explicit notification/timeline GETs use display projection; form GET, visit GET and write payloads retain original text', async () => {
  const original = freeze({ id: 'raw', title: '【演示】原文', action: '【演示】填写任务', source: { synthetic: true } });
  const { api, requests } = client(DEMO, request => request.success({ statusCode: 200, data:
    request.url.includes('/notifications?') ? notification : request.url.includes('/timeline?') ? { items: [{ title: '【演示】进展', detail: '【演示】历史' }] } : original,
  }));
  assert.equal((await api.listNotifications()).items[0].payload.customer_name, '澄星');
  assert.equal((await api.getOpportunityTimeline('o1')).items[0].detail, '历史');
  assert.equal((await api.getBusinessAdvice('a1')).action, original.action);
  assert.equal((await api.getVisit('v1')).title, original.title);
  const fields = freeze({ follow_up_record: '【演示】人工输入', next_action: '【演示】待提交', source: '【演示】source' });
  assert.equal((await api.createVisit('c1', fields)).title, original.title);
  assert.equal(requests.at(-1).method, 'POST');
  assert.equal(requests.at(-1).data.fields, fields);
  assert.equal(requests.at(-1).data.customer_id, 'c1');
});

test('single visit detail renders its scoped display customer name and archived body from the untouched GET record', async t => {
  inWorkspace(t, DEMO);
  const raw = freeze({ id: 'v1', customer_id: 'c1', customer_name: '【演示】澄星', status: 'archived', follow_up_record: '【演示】沟通内容' });
  const filename = path.resolve(__dirname, '../miniprogram/pages/visit-detail/index.js');
  let page;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), { Page: definition => { page = definition; },
    require: name => name.endsWith('apiClient') ? { getVisit: async () => raw } : require(path.resolve(path.dirname(filename), name)),
    getApp: () => ({ globalData: { session: { workspaceId: DEMO, userId: 'u1' } } }),
  });
  page.data = { customerId: 'c1', visitId: 'v1' };
  page.setData = value => Object.assign(page.data, value);
  page.loadVisitAdvice = () => {};
  page.loadVisit();
  await new Promise(setImmediate);
  assert.equal(page.data.customerName, '澄星');
  assert.equal(page.data.visit.followUpRecord, '沟通内容');
  assert.equal(raw.follow_up_record, '【演示】沟通内容');
});

test('GET projections capture the request workspace instead of using a later display session', async () => {
  for (const [start, finish, expected] of [[FORMAL, DEMO, '【演示】商机更新'], [DEMO, FORMAL, '商机更新']]) {
    let complete;
    const { api, storage } = client(start, request => { complete = request.success; });
    const pending = api.listNotifications();
    storage.set('salesApiAuth', { actor: { workspace_id: finish, user_id: 'u1' } });
    complete({ statusCode: 200, data: notification });
    assert.equal((await pending).items[0].title, expected);
  }
});
