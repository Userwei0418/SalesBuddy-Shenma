const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { draftScope } = require('../miniprogram/utils/draftScope');

const root = path.resolve(__dirname, '../miniprogram');
const filename = path.join(root, 'pages/visit-confirm/index.js');
const session = { workspaceId: 'test-workspace', userId: 'sales-test', role: 'sales', userName: '测试销售' };
const scope = draftScope(session);
const draftKey = `visitConfirmV2:${scope}`;
const sourceKey = `visitStructuredV2:${scope}`;
const fields = { follow_up_record: '客户确认试点范围', next_action: '9月25日销售发送方案', contact_name: '客户经理' };
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup({ values = fields, draft, history, memory = new Map(), clock = { now: '2026-09-23T16:05:00Z' } } = {}) {
  if (!memory.has(sourceKey)) memory.set(sourceKey, {
    draftId: 'entry-test', runId: 'structure-test', customerHintId: 'customer-test',
    customerHint: '隔离测试客户', result: { fields: values },
  });
  if (draft) memory.set(draftKey, { draftId: 'entry-test', ...draft });
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock.now])); }
    static now() { return Date.parse(clock.now); }
  }
  const dateModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'utils/visitDates.js'), 'utf8'), { module: dateModule, Date: ClockDate });
  const requests = [], errors = [];
  const api = {
    request: async ({ path: url }) => url.startsWith('/visits/') ? history : { items: [] },
    submitVisitStage: async (stage, payload) => { requests.push({ stage, payload }); return { run_id: 'quality-test' }; },
    waitVisitRun: async () => ({ result: { visit_stage: 'quality', quality_review: { follow_up_score: 85, next_action: { passed: true } } } }),
  };
  let page;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('/apiClient') ? api : name.endsWith('/visitDates') ? dateModule.exports : require(path.resolve(path.dirname(filename), name)),
    getApp: () => ({ ensureLogin: () => true, guardPage: () => true, globalData: { session } }),
    wx: {
      getStorageSync: key => structuredClone(memory.get(key)),
      setStorageSync: (key, value) => memory.set(key, structuredClone(value)),
      pageScrollTo() {}, showToast() {},
    },
    setInterval, clearInterval, setTimeout, clearTimeout,
  });
  page.data = structuredClone(page.data);
  page.setData = values => {
    for (const [key, value] of Object.entries(values)) {
      const parts = key.split('.');
      let target = page.data;
      for (const part of parts.slice(0, -1)) target = target[part];
      target[parts.at(-1)] = value;
    }
  };
  // Keep the actual date, draft, validation and quality logic; unrelated directories are not exercised here.
  page.loadBusinessOptions = () => {};
  page.loadOpportunities = () => {};
  page.searchCustomers = () => {};
  page.fail = error => { errors.push(String(error.message || error)); };
  page.onLoad(history ? { visitId: 'history-test' } : {});
  return { page, memory, clock, requests, errors };
}

test('首次新建默认北京时间日期，未操作选择器也传入质检并保存草稿', async () => {
  const h = setup();
  assert.equal(h.page.data.values.interaction_at, '2026-09-24');
  assert.equal(h.page.data.values.created_date, '2026-09-24');
  assert.equal(h.page.data.visitDateLabel, '2026年9月24日');
  assert.equal(h.page.data.createdDateLabel, '2026年9月24日');
  assert.equal(h.memory.get(draftKey).values.interaction_at, '2026-09-24');
  await h.page.review();
  assert.deepEqual(h.errors, []);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].payload.fields.interaction_at, '2026-09-24');
  assert.equal(h.requests[0].payload.fields.created_date, '2026-09-24');
  assert.equal(h.page.data.canSubmit, true);
});

test('识别出的日期和时区原值保留，只补空日期，不修改结构化原文', () => {
  const values = { ...fields, interaction_at: '2026-09-20T17:00:00Z', created_date: '  ' };
  const h = setup({ values });
  assert.equal(h.page.data.values.interaction_at, values.interaction_at);
  assert.equal(h.page.data.visitDateLabel, '2026年9月21日');
  assert.equal(h.page.data.values.created_date, '2026-09-24');
  assert.equal(values.created_date, '  ');
  assert.equal(h.memory.get(sourceKey).result.fields.created_date, '  ');
});

test('提前保存日期后，商机目录尚未加载时重进仍保留待应用的 AI 建议', () => {
  const h = setup({ values: { ...fields, opportunity_name: '隔离测试方案', amount_wan: 30 } });
  assert.equal(h.page.aiOpportunitySuggestion.name, '隔离测试方案');
  const restored = setup({ memory: h.memory });
  assert.equal(restored.page.aiOpportunitySuggestion.name, '隔离测试方案');
  assert.equal(restored.page.aiOpportunitySuggestion.amount, '30');
  assert.equal(restored.page.data.values.interaction_at, '2026-09-24');
});

test('AI 建议被应用或主动取消后不从原文复活，旧草稿行为不变', () => {
  const values = { ...fields, opportunity_name: '隔离测试方案' };
  const h = setup({ values });
  h.page.aiOpportunitySuggestion = null;
  h.page.persist();
  assert.equal(setup({ memory: h.memory }).page.aiOpportunitySuggestion, null);
  assert.equal(setup({ values, draft: { values: fields } }).page.aiOpportunitySuggestion, null);
});

test('手动修改两种日期，重新打开草稿仍保留修改值', async () => {
  const h = setup();
  h.page.selectVisitDate({ detail: { value: '2026-09-18' } });
  h.page.selectCreatedDate({ detail: { value: '2026-09-19' } });
  const restored = setup({ memory: h.memory });
  assert.equal(restored.page.data.values.interaction_at, '2026-09-18');
  assert.equal(restored.page.data.values.created_date, '2026-09-19');
  await restored.page.review();
  assert.equal(restored.requests[0].payload.fields.interaction_at, '2026-09-18');
  assert.equal(restored.requests[0].payload.fields.created_date, '2026-09-19');
});

for (const [key, clear, label] of [
  ['interaction_at', 'clearVisitDate', '跟进日期'], ['created_date', 'clearCreatedDate', '创建时间'],
]) test(`主动清空${label}后刷新、恢复草稿均不回填，并阻止质检请求`, async () => {
  const h = setup();
  h.page[clear]();
  h.page.refresh();
  assert.equal(h.page.data.values[key], '');
  const restored = setup({ memory: h.memory });
  assert.equal(restored.page.data.values[key], '');
  await restored.page.review();
  assert.equal(restored.requests.length, 0);
  assert.match(restored.errors[0], new RegExp(label));
});

test('北京跨日后恢复草稿保留首次默认日期，新记录取进入页面当天', () => {
  const clock = { now: '2026-09-24T15:59:00Z' };
  const h = setup({ clock });
  clock.now = '2026-09-24T16:01:00Z';
  const restored = setup({ memory: h.memory, clock });
  assert.equal(restored.page.data.values.interaction_at, '2026-09-24');
  assert.equal(restored.page.data.values.created_date, '2026-09-24');
  const fresh = setup({ clock });
  assert.equal(fresh.page.data.values.interaction_at, '2026-09-25');
  assert.equal(fresh.page.data.values.created_date, '2026-09-25');
});

test('另一条记录的旧草稿不覆盖新记录；同记录旧草稿的空值不被改写', () => {
  const fresh = setup({ draft: { draftId: 'another-entry', values: { ...fields, interaction_at: '2026-08-01' } } });
  assert.equal(fresh.page.data.values.interaction_at, '2026-09-24');
  const restored = setup({ draft: { values: { ...fields, interaction_at: '', created_date: '' } } });
  assert.equal(restored.page.data.values.interaction_at, '');
  assert.equal(restored.page.data.values.created_date, '');
});

test('非空异常日期保留给现有校验，不静默替换为今天', () => {
  const h = setup({ values: { ...fields, interaction_at: '日期待核对', created_date: '2026-09-12' } });
  assert.equal(h.page.data.values.interaction_at, '日期待核对');
  assert.equal(h.page.data.values.created_date, '2026-09-12');
});

test('编辑历史记录不补日期、不创建确认草稿', async () => {
  const h = setup({ history: { ...fields, interaction_at: '', created_date: null, customer_id: 'customer-test', customer_name: '隔离测试客户', version_no: 1 } });
  await flush();
  assert.equal(h.page.data.editing, true);
  assert.equal(h.page.data.values.interaction_at, '');
  assert.equal(h.page.data.values.created_date, null);
  assert.equal(h.memory.has(draftKey), false);
  assert.equal(h.requests.length, 0);
});
