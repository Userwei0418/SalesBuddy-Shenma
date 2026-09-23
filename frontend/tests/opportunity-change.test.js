const test = require('node:test');
const assert = require('node:assert/strict');
const {businessChangeLight} = require('../miniprogram/utils/statusLight');

test('Agent 变化色不被阶段或客户关系绝对分数覆盖', () => {
  for (const color of ['green','yellow','red','gray']) {
    const result=businessChangeLight({opportunity_id:'o1',tone:'green',ai_review:{relationship_after:59},
      change_review:{status:'completed',color,summary:'本次变化依据',title:'变化结论'}});
    assert.equal(result.tone,color);
    assert.equal(result.reason,'本次变化依据');
    assert.equal(result.title,'变化结论');
  }
});
test('等待评估保持中性，原规则兜底不再被客户关系75到78改黄', () => {
  assert.equal(businessChangeLight({opportunity_id:'o1',tone:'green',change_review:{status:'pending'}}).tone,'gray');
  assert.equal(businessChangeLight({opportunity_id:'o1',tone:'green',ai_review:{relationship_after:78}}).tone,'green');
  assert.equal(businessChangeLight({opportunity_id:'o1',tone:'orange'}).tone,'yellow');
  assert.equal(businessChangeLight({opportunity_id:'o1',tone:'blue'}).tone,'gray');
});
