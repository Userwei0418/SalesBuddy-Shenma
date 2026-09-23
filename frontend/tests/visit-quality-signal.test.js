const test = require('node:test');
const assert = require('node:assert/strict');
const {visitSignal} = require('../miniprogram/utils/customerSignals');
const {normalizeCustomerDetail} = require('../miniprogram/utils/customerDetail');

test('采用历史保存的质检等级，不按前端固定分界重新评估', () => {
  for (const [score, grade, tone] of [[75,'合格','green'],[75,'待完善','yellow'],[80,'优秀','green'],[88,'良好','green'],[0,'待完善','yellow']]) {
    const result = visitSignal({follow_up_score:score,quality_review:{follow_up_score:score,grade}});
    assert.equal(result.badgeText, `质检${grade} · ${score} 分`);
    assert.equal(result.tone, tone);
    assert.doesNotMatch(result.reason, /目标.*达成|已达标/);
  }
});

test('空值、非法评分和只有旧目标达成结果均不推断质检通过', () => {
  for (const score of [undefined,null,'',false,'75',NaN,Infinity,-1,101,75.5]) {
    const result = visitSignal({status:'archived',expectation_code:'met',next_action:'确认计划',follow_up_score:score,quality_review:{grade:'合格'}});
    assert.equal(result.badgeText, '暂无质检结果');
    assert.equal(result.detail, '');
    assert.equal(result.tone, 'gray');
  }
  assert.equal(visitSignal().badgeText, '暂无质检结果');
});

test('仅有分数保留真实分数，缺等级不自行补出合格', () => {
  assert.equal(visitSignal({follow_up_score:75}).badgeText, '质检评分 · 75 分');
  assert.equal(visitSignal({follow_up_score:75,quality_review:{grade:'未知等级'}}).tone, 'gray');
  assert.equal(visitSignal({quality_review:{follow_up_score:75,grade:'合格'}}).badgeText, '质检合格 · 75 分');
  assert.equal(visitSignal({follow_up_score:75,quality_review:{follow_up_score:90,grade:'优秀'}}).badgeText, '质检结果待核对');
});

test('下一步审核结果只描述记录质量，不将部分审核通过当作目标达成', () => {
  const input = {follow_up_score:75,quality_review:{grade:'合格',next_action_passed:false}};
  assert.match(visitSignal(input).reason, /下一步计划仍需完善/);
  assert.match(visitSignal({...input,quality_review:{...input.quality_review,next_action_passed:true}}).reason, /下一步计划审核通过/);
});

test('客户跟进与关联商机跟进使用同一份质检结果，顺序不变', () => {
  const visits = [
    {id:'new',opportunity_id:'o',status:'archived',created_at:'2026-09-16T04:17:06Z',follow_up_score:75,quality_review:{grade:'合格'}},
    {id:'old',opportunity_id:'o',status:'archived',created_at:'2026-09-16T03:55:17Z',expectation_code:'met'},
  ];
  const result = normalizeCustomerDetail({id:'c',name:'客户',visits,opportunities:[{id:'o',status:'open'}]},'o',{preserveVisitOrder:true});
  assert.deepEqual(result.visits.map(v => v.id), ['new','old']);
  assert.deepEqual(result.visits.map(v => v.signal.badgeText), ['质检合格 · 75 分','暂无质检结果']);
  assert.deepEqual(result.opportunities[0].relatedVisits.map(v => v.signal.badgeText), result.visits.map(v => v.signal.badgeText));
});
