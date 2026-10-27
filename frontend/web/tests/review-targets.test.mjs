import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decimalText, positiveDecimal, rowDraft, targetBody, targetDisplayAmount, targetRows, targetStorageAmount, targetUnit} from '../department-ui/review/targets/model.js';

test('exact target decimals preserve cents above Number safe integer precision', () => {
  assert.equal(positiveDecimal('9007199254740991.29'), '9007199254740991.29');
  assert.equal(decimalText('9007199254740991.29'), '9,007,199,254,740,991.29');
  assert.equal(decimalText(null), '未设目标');
  assert.equal(decimalText('0.00'), '0.00');
  assert.equal(positiveDecimal('0003', 0), '3');
  assert.equal(rowDraft({effective_amount: '12.00'}, {decimal_places: 0}).amount,'12');
  for (const value of ['0', '-1', '1e8', '1.234', '', 'NaN']) assert.throws(() => positiveDecimal(value));
  assert.throws(() => positiveDecimal('1.1', 0));
});

test('pending first targets remain authoritative rows without inventing effective values', () => {
  const rows = targetRows({items: [{row_key: 'pending-1', id: null, effective_amount: null, version_no: null, state: 'pending_new', pending_change: {proposed_amount: '120.00'}}]});
  assert.equal(rows.length, 1); assert.equal(rows[0].effective_amount, null); assert.equal(rows[0].key, 'pending-1');
  assert.equal(rows[0].pending_change.proposed_amount, '120.00');
  assert.throws(() => targetRows({pending_batches: []}));
});

test('target writes contain one target identity and expected version, not UI or actor fields', () => {
  const metrics = [{code: 'forecast_collection', target_kind: 'collection', decimal_places: 2, active: true, goal_selectable: true}];
  const options = {teams: [{id: 'team1', name: '团队'}]};
  const draft = {rowKey: 'ui1', scope: 'team', subjectId: 'team1', metricCode: 'forecast_collection', amount: '1.29', reason: '季度计划', period_type: 'quarter', anchor_date: '2026-10-14', version_no: 3, actor: 'wrong'};
  assert.deepEqual(targetBody(draft, metrics, options), {scope: 'team', team_id: 'team1', kind: 'collection', amount: '12900', reason: '季度计划', period_type: 'quarter', anchor_date: '2026-10-14', version_no: 3});
  assert.throws(() => targetBody({...draft, subjectId: 'outside'}, metrics, options));
  assert.throws(() => targetBody(draft, [{...metrics[0], active: false}], options));
});

test('monetary targets are edited in 万元 and written back to canonical yuan exactly', () => {
  const metric = {code: 'collection', target_kind: 'collection', decimal_places: 2, unit: '元'};
  assert.equal(targetUnit(metric), '万元');
  assert.equal(targetDisplayAmount('5000000.00', metric), '500');
  assert.equal(targetDisplayAmount('12900', metric), '1.29');
  assert.equal(targetStorageAmount('500', metric), '5000000');
  assert.equal(targetStorageAmount('1.29', metric), '12900');
  assert.equal(targetDisplayAmount('12.00', {code: 'opportunity_count', decimal_places: 0, unit: '个'}), '12.00');
});

test('explicit zero target is saved with its reason and differs from missing input', () => {
  const metrics = [{code:'collection_ppl',decimal_places:2,active:true,goal_selectable:true}];
  const options = {teams:[{id:'north'}]};
  const draft = {scope:'team',subjectId:'north',metricCode:'collection_ppl',amount:'0',reason:'北区目标由东区承接',period_type:'quarter',anchor_date:'2026-10-16'};
  assert.equal(targetBody(draft,metrics,options).amount,'0');
  for(const amount of ['', '-1']) assert.throws(()=>targetBody({...draft,amount},metrics,options));
  assert.throws(()=>targetBody({...draft,reason:''},metrics,options));
});
