import {test} from 'node:test';
import assert from 'node:assert/strict';
import {insightAmount, insightCell, insightContext, insightDefinitions, insightWan} from '../department-ui/insights-presentation.js';

test('shared financial values retain their unit, precision, zero and missing state', () => {
  assert.equal(insightCell('¥123,456.78', 'money'), '¥123,456.78');
  assert.equal(insightCell('已登记小计 0.01 万元', 'money'), '已登记小计 0.01 万元');
  assert.equal(insightCell(0), '0');
  assert.equal(insightCell(null), '未登记');
  assert.equal(insightCell(NaN), '未登记');
  assert.equal(insightCell({secret: 'do not render'}), '—');
  assert.deepEqual(insightAmount('¥0.49'), {value:0.49,unit:'元'});
  assert.deepEqual(insightAmount('¥0'), {value:0,unit:'元'});
  assert.deepEqual(insightAmount('¥123,456.78'), {value:12.345678,unit:'万元'});
  assert.equal(insightWan(0.01), 0.000001);
});

test('scope identifiers and periods distinguish otherwise identically named drawers', () => {
  const data = {viewMode:'personal', selectedMemberId:'one', memberLabel:'同名', selectedQuarterKeys:['2026-Q1']};
  assert.notEqual(insightContext(data), insightContext({...data, selectedMemberId:'two'}));
  assert.notEqual(insightContext(data), insightContext({...data, selectedQuarterKeys:['2026-Q2']}));
  assert.notEqual(insightContext(data), insightContext({...data, sourceDate:'2026-10-10 10:10'}));
});

test('definitions use the shared metric evidence and distinguish the selected period', () => {
  const result = insightDefinitions({selectedQuarter:{label:'2026 Q1'}, kpis:[{key:'actual',label:'实际',sub:'旧说明'}], metricDetails:{actual:{definition:'当前采用的不含税实绩',periodLabel:'2026 Q1'}}});
  assert.equal(result[0].definition, '当前采用的不含税实绩');
  assert.equal(result[0].periodLabel, '2026 Q1');
});
