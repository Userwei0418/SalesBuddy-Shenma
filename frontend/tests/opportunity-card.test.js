const test = require('node:test');
const assert = require('node:assert/strict');
const { cardFields, loadCardActuals } = require('../miniprogram/utils/opportunityCard');
const rows = [{ id: 'o1', customer_id: 'c1', amount: 999999, quarterly_forecasts: [{ recognized_amount: 888888 }] }, { id: 'o2', customer_id: 'c1' }, { id: 'o3', customer_id: 'c2' }];
const entry = (kind, amount, opportunity_id = 'o1') => ({ customer_id: 'c1', opportunity_id, kind, amount });
function apiFor(entries) {
  return { getCustomerAssets: async options => options.customer_id
    ? { view: 'entries', items: entries, has_more: false }
    : { view: 'customers', items: [{ customer_id: 'c1' }], has_more: false } };
}

test('产品线取实际字段，缺失不制造示例产品，初始金额为加载中', () => {
  assert.equal(cardFields({ product_line: ' Token Plan ' }).productLineLabel, 'Token Plan');
  assert.equal(cardFields({ product_line: '如影' }).productLineLabel, '如影');
  assert.equal(cardFields({}).productLineLabel, '未填写');
  assert.equal(cardFields({}).recognizedLabel, '加载中');
});
test('累计实际金额仅计关联商机，不串客户、不分摊未关联记录、不使用预测和ACV', async () => {
  const result = await loadCardActuals(apiFor([
    entry('recognized', 100000), entry('recognized', 50000), entry('collection', 0),
    entry('collection', 88888, null), entry('recognized', 500000, 'o2'),
    { ...entry('recognized', 1000000), customer_id: 'other-customer' },
  ]), rows);
  assert.equal(result[0].recognizedLabel, '15万');
  assert.equal(result[0].collectionLabel, '0元');
  assert.equal(result[1].recognizedLabel, '50万');
  assert.equal(result[1].collectionLabel, '未登记');
  assert.equal(result[2].recognizedLabel, '未登记');
  assert.equal(result[0].amount, 999999);
});
test('确收与回款均读取全部分页，并且请求不限年度或单一类型', async () => {
  const calls = [];
  const result = await loadCardActuals({ getCustomerAssets: async options => {
    calls.push(options);
    if (!options.customer_id) return { view: 'customers', items: [{ customer_id: options.offset ? 'c1' : 'other' }], has_more: !options.offset };
    return { view: 'entries', items: [entry(options.offset ? 'collection' : 'recognized', options.offset ? 40000 : 60000)], has_more: !options.offset };
  } }, rows);
  assert.equal(result[0].recognizedLabel, '6万');
  assert.equal(result[0].collectionLabel, '4万');
  assert.equal(calls.length, 4);
  assert.ok(calls.every(options => options.period === 'all' && !options.kind));
});
test('分页中途失败时不显示部分金额或伪零，其他无登记客户正常显示', async () => {
  const result = await loadCardActuals({ getCustomerAssets: async options => {
    if (!options.customer_id) return { view: 'customers', items: [{ customer_id: 'c1' }], has_more: false };
    if (options.offset) throw new Error('offline');
    return { view: 'entries', items: [entry('recognized', 10000)], has_more: true };
  } }, rows);
  assert.equal(result[0].recognizedLabel, '暂不可用');
  assert.equal(result[1].collectionLabel, '暂不可用');
  assert.equal(result[2].recognizedLabel, '未登记');
});
test('实际接口失败或缺失返回暂不可用，不能冒充没有登记', async () => {
  for (const api of [{}, { getCustomerAssets: async () => { throw new Error('offline'); } }]) {
    const result = await loadCardActuals(api, rows);
    assert.ok(result.every(item => item.recognizedLabel === '暂不可用'));
  }
});
test('无效金额和停滞分页不能产生看似有效的汇总', async () => {
  for (const value of [null, '', true, 'bad']) {
    const result = await loadCardActuals(apiFor([entry('recognized', value)]), rows);
    assert.equal(result[0].recognizedLabel, '暂不可用');
  }
  const result = await loadCardActuals({ getCustomerAssets: async () => ({ view: 'customers', items: [], has_more: true }) }, rows);
  assert.equal(result[0].recognizedLabel, '暂不可用');
});
