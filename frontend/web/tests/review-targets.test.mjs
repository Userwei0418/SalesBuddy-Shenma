import {test} from 'node:test';
import assert from 'node:assert/strict';
import {decimalText, positiveDecimal, rowDraft, targetBody, targetRows} from '../department-ui/review/targets/model.js';

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
  assert.deepEqual(targetBody(draft, metrics, options), {scope: 'team', team_id: 'team1', kind: 'collection', amount: '1.29', reason: '季度计划', period_type: 'quarter', anchor_date: '2026-10-14', version_no: 3});
  assert.throws(() => targetBody({...draft, subjectId: 'outside'}, metrics, options));
  assert.throws(() => targetBody(draft, [{...metrics[0], active: false}], options));
});
