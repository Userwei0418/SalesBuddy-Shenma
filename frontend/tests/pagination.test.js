const test = require('node:test');
const assert = require('node:assert/strict');
const { collectPages } = require('../miniprogram/utils/pagination');
const { cardFields, loadCardActuals } = require('../miniprogram/utils/opportunityCard');

test('完整商机读取超过单页上限，保留真正结束后的集合', async () => {
  const calls = [];
  const result = await collectPages(async offset => {
    calls.push(offset);
    const count = offset ? 1 : 300;
    return { items: Array.from({length:count}, (_,i) => ({id:String(offset+i)})), has_more: !offset, next_offset: offset ? null : 300 };
  });
  assert.deepEqual(calls,[0,300]); assert.equal(result.items.length,301);
});
test('分页失败、不前进、重复ID或取消时不返回伪完整列表', async () => {
  await assert.rejects(collectPages(async () => ({items:[]})), /分页数据不完整/);
  await assert.rejects(collectPages(async () => ({items:[{id:'a'}],has_more:true,next_offset:0})), /分页未继续/);
  await assert.rejects(collectPages(async offset => ({items:[{id:'a'}],has_more:!offset,next_offset:1})), /正在变化/);
  let cancelled = false;
  const result = await collectPages(async () => { cancelled=true; return {items:[],has_more:false}; }, () => cancelled);
  assert.equal(result,null);
});
test('列表直接采用数据库商机实绩，未登记和真实零不同且不再请求客户流水', async () => {
  const items = [{id:'a',actuals:{recognized_amount:null,collection_amount:'0'}}];
  const result = await loadCardActuals({ getCustomerAssets: () => {throw Error('不应读取');} },items);
  assert.equal(result[0].recognizedLabel,'未登记');
  assert.equal(result[0].collectionLabel,cardFields(items[0]).collectionLabel);
  assert.notEqual(result[0].collectionLabel,'未登记');
});
