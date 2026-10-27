const test = require('node:test');
const assert = require('node:assert/strict');
const form = require('../miniprogram/utils/opportunityCollectionActualForm');

const period = { year: 2026, quarter: 4, label: '2026 Q4' };
const response = (patch = {}) => ({ opportunity_id: 'opportunity', period: { year: 2026, quarter: 4 }, can_edit: true,
  actual: { amount: '100000.00', status: 'confirmed', source: 'operations', revision: 'a'.repeat(64), requires_source_replacement: false, source_ref: '原依据', note: '' }, ...patch });
const draft = (patch = {}) => ({ amount: '15', evidence: '银行到账凭据', note: '', replaceSource: false, ...patch });
const send = (value, baseline = response(), tradeRelation = 'direct') => form.payload({ '2026-Q4': value }, { '2026-Q4': baseline }, tradeRelation);

test('季度累计回款10改15只提交15，未改金额普通保存不重写实绩也不要求依据', () => {
  const baseline = response();
  assert.deepEqual(send(form.draftFor(baseline), baseline), []);
  assert.deepEqual(send({ amount: '10.000000', evidence: '', note: '其他备注' }, baseline), []);
  const result = send(draft(), baseline)[0];
  assert.equal(result.amount, '150000');
  assert.equal(result.expected_actual_revision, 'a'.repeat(64));
  assert.equal('replace_source' in result, false);
  assert.equal('expected_version' in result, false);
  assert.equal(baseline.actual.amount, '100000.00');
});

test('未登记与明确零严格区分，万元精确转元；修改金额才校验依据和备注', () => {
  const baseline = response({ actual: { ...response().actual, amount: null, status: 'missing' } });
  assert.equal(form.draftFor(baseline).amount, '');
  assert.deepEqual(form.payload({}, {}), []);
  assert.equal(send(draft({ amount: '0' }), baseline)[0].amount, '0');
  assert.equal(send(draft({ amount: '0.000001' }), baseline)[0].amount, '0.01');
  assert.throws(() => send(draft({ amount: '' })), /金额为零请填 0/);
  assert.throws(() => send(draft({ evidence: '' }), baseline), /回款依据/);
  assert.throws(() => send(draft({ amount: '0.0000001' }), baseline), /六位小数/);
  assert.throws(() => send(draft({ note: '字'.repeat(1001) }), baseline), /1000/);
  assert.throws(() => send(draft(), response({ can_edit: false })), /无权修改/);
});

test('替换现有来源仅采用服务端标记，需明确勾选；同来源不加确认', () => {
  const baseline = response({ actual: { ...response().actual, source: null, requires_source_replacement: true } });
  assert.throws(() => send(draft(), baseline), /确认替换/);
  assert.equal(send(draft({ replaceSource: true }), baseline)[0].replace_source, true);
  assert.deepEqual(send(form.draftFor(baseline), baseline), []);
  assert.equal('replace_source' in send(draft({ replaceSource: true }))[0], false);
});

test('拒绝对象、季度、金额或并发版本不完整响应，不伪造正式金额', () => {
  for (const patch of [{ opportunity_id: 'other' }, { period: { year: 2026, quarter: 3 } },
    { actual: { ...response().actual, revision: '' } }, { actual: { ...response().actual, amount: 0 } },
    { actual: { ...response().actual, requires_source_replacement: undefined } }, { can_edit: undefined }]) {
    assert.throws(() => form.read(response(patch), 'opportunity', period), /不一致/);
  }
  assert.equal(form.read(response(), 'opportunity', period).actual.amount, '100000.00');
});

function harness(get = async (_, requested) => response({ period: { year: requested.year, quarter: requested.quarter } })) {
  const session = { workspaceId: 'company', userId: 'sales', permissionVersion: 1 };
  const component = { properties: { existing: { id: 'opportunity' }, visitContext: false, disabled: false }, sourceKey: 'source',
    data: { ...structuredClone(form.data), form: { trade_relation: 'direct' }, quarterOptions: [period, { year: 2027, quarter: 1, label: '2027 Q1' }] },
    emit() {}, setData(changes) { for (const [path, value] of Object.entries(changes)) {
      const parts = path.split('.'); let target = this.data;
      for (const key of parts.slice(0, -1)) target = target[key] || (target[key] = {});
      target[parts.at(-1)] = value;
    } }, ...form.create({ getOpportunityCollectionActuals: get }, () => session) };
  const input = (key, value) => component.collectionActualInput({ currentTarget: { dataset: { key } }, detail: { value } });
  return { component, session, input };
}

test('切换季度保留草稿并在原保存一起提交；新来源版本需要重新确认替换', async () => {
  let revision = 'a'.repeat(64);
  const h = harness(async (_, requested) => response({ period: requested, actual: { ...response().actual, revision, requires_source_replacement: true } })), c = h.component;
  await c.resetCollectionActuals();
  assert.equal(c.data.collectionActualAmount, '10');
  assert.equal(c.data.collectionActualCurrentText, '10 万元');
  h.input('amount', '15'); h.input('evidence', '收款流水'); c.collectionActualReplace({ detail: { value: ['replace'] } });
  assert.equal(c.prepareCollectionActuals()[0].replace_source, true);
  await c.collectionActualQuarter({ detail: { value: 1 } });
  h.input('amount', '0'); h.input('evidence', '本季度未到账'); c.collectionActualReplace({ detail: { value: true } });
  await c.collectionActualQuarter({ detail: { value: 0 } });
  assert.equal(c.data.collectionActualAmount, '15');
  assert.deepEqual(c.prepareCollectionActuals().map(row => row.amount), ['150000', '0']);
  revision = 'b'.repeat(64); await c.retryCollectionActual();
  assert.equal(c.data.collectionActualAmount, '15');
  assert.equal(c.data.collectionActualReplaceSource, false);
  assert.throws(() => c.prepareCollectionActuals(), /确认替换/);
});

test('读取失败不锁普通商机保存；改动金额的读取失败保留草稿并避免错误提交', async () => {
  let fail = true;
  const h = harness(async (_, requested) => { if (fail) throw Error('服务暂不可用'); return response({ period: requested }); });
  const c = h.component;
  await c.resetCollectionActuals();
  assert.equal(c.data.collectionActualLoaded, false);
  assert.equal(c.data.collectionActualCurrentText, '—');
  assert.deepEqual(c.prepareCollectionActuals(), []);
  fail = false; await c.retryCollectionActual();
  h.input('amount', '15'); h.input('evidence', '流水凭据');
  fail = true; await c.retryCollectionActual();
  assert.equal(c.data.form.collection_actual_drafts['2026-Q4'].amount, '15');
  assert.throws(() => c.prepareCollectionActuals(), /服务暂不可用/);
  fail = false; await c.retryCollectionActual();
  assert.equal(c.data.collectionActualAmount, '15');
  assert.equal(c.prepareCollectionActuals()[0].amount, '150000');
});

test('拜访内嵌/新增不修改实际回款，disabled与切换身份后的迟到响应不能写草稿', async () => {
  const h = harness(), c = h.component;
  c.properties.visitContext = true; await c.resetCollectionActuals();
  assert.equal(c.data.collectionActualVisible, false);
  assert.deepEqual(c.prepareCollectionActuals(), []);
  c.properties.visitContext = false; c.properties.existing = null; await c.resetCollectionActuals();
  assert.equal(c.data.collectionActualVisible, false);
  c.properties.existing = { id: 'opportunity' }; await c.resetCollectionActuals();
  c.properties.disabled = true; h.input('amount', '15');
  assert.deepEqual(c.prepareCollectionActuals(), []);
  let resolve;
  const late = harness(() => new Promise(done => { resolve = done; }));
  const loading = late.component.resetCollectionActuals();
  late.session.userId = 'someone-else'; resolve(response()); await loading;
  assert.equal(late.component.data.collectionActualLoaded, false);
  assert.throws(() => late.component.prepareCollectionActuals(), /账号已变化/);
});


test('历史多位小数可读且原样保留：尾零及分以下精度均不触发未修改金额的重写', () => {
  for (const amount of ['100000.000000', '100000.000001']) {
    const baseline = response({ actual: { ...response().actual, amount, source: 'historical', requires_source_replacement: true } });
    assert.equal(form.read(baseline, 'opportunity', period).actual.amount, amount);
    const original = form.draftFor(baseline);
    assert.deepEqual(send(original, baseline), []);
    assert.deepEqual(send({ ...original, evidence: '', note: '只浏览原金额' }, baseline), []);
    assert.equal(send(draft({ replaceSource: true }), baseline)[0].amount, '150000');
  }
  assert.equal(form.draftFor(response({ actual: { ...response().actual, amount: '100000.000001' } })).amount, '10.0000000001');
  assert.throws(() => send(draft({ amount: '15.0000000001' })), /六位小数/);
});

test('只在修改实际回款时要求明确交易关系，下游非零回款有独立提示且零仍可登记', () => {
  const baseline = response();
  assert.deepEqual(send(form.draftFor(baseline), baseline, null), []);
  assert.throws(() => send(draft(), baseline, null), error =>
    error.code === 'OPPORTUNITY_TRADE_RELATION_REQUIRED' && /交易物料中选择交易关系/.test(error.message));
  assert.throws(() => send(draft(), baseline, 'downstream'), error =>
    error.code === 'OPPORTUNITY_COLLECTION_DOWNSTREAM_FORBIDDEN' && /收款归属/.test(error.message));
  assert.equal(send(draft({ amount: '0' }), baseline, 'downstream')[0].amount, '0');
  assert.equal(send(draft(), baseline, 'distributor')[0].amount, '150000');
});

test('跨季度草稿仍标记交易关系必填并校验全部改动，校验失败保留输入与万元换算预览', async () => {
  const h = harness(), c = h.component;
  await c.resetCollectionActuals();
  c.data.form.trade_relation = null;
  h.input('amount', '10000'); h.input('evidence', '银行到账凭据');
  assert.equal(c.data.collectionActualAmountPreview, '折合 100,000,000 元（1 亿元）');
  assert.equal(c.data.collectionActualAnyDirty, true);
  await c.collectionActualQuarter({ detail: { value: 1 } });
  assert.equal(c.data.collectionActualDirty, false);
  assert.equal(c.data.collectionActualAnyDirty, true);
  assert.throws(() => c.prepareCollectionActuals(), /2026 Q4：请先在交易物料中选择交易关系/);
  assert.equal(c.data.form.collection_actual_drafts['2026-Q4'].amount, '10000');
  c.data.form.trade_relation = 'direct';
  assert.equal(c.prepareCollectionActuals()[0].amount, '100000000');
  await c.collectionActualQuarter({ detail: { value: 0 } });
  h.input('amount', '10');
  assert.equal(c.data.collectionActualAnyDirty, false);
  assert.deepEqual(c.prepareCollectionActuals(), []);
});
