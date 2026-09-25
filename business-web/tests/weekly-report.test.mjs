import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composeReport, periodRecords, reportPeriod} from '../department-ui/weekly-report-model.mjs';
test('weekly period uses Shanghai dates, natural report week and fourteen-day source window', () => {
  const now = Date.parse('2026-09-22T16:01:00Z');
  assert.deepEqual(reportPeriod('recent', now), {start:'2026-09-21',end:'2026-09-27',inputStart:'2026-09-10',inputEnd:'2026-09-23',cutoffAt:'2026-09-22T16:01:00.000Z'});
  assert.deepEqual(reportPeriod('previous', now), {start:'2026-09-14',end:'2026-09-20',inputStart:'2026-09-07',inputEnd:'2026-09-20',cutoffAt:'2026-09-20T15:59:59.999Z'});
  assert.deepEqual(reportPeriod('previous', Date.parse('2026-09-20T16:01:00Z')), {start:'2026-09-14',end:'2026-09-20',inputStart:'2026-09-07',inputEnd:'2026-09-20',cutoffAt:'2026-09-20T15:59:59.999Z'});
});
test('upload boundaries exclude old, future and undated rows, independently of visit date', () => {
  const dates = ['2026-09-16T15:59:59Z', '2026-09-16T16:00:00Z', '2026-09-23T15:59:59Z', '2026-09-23T16:00:00Z', null];
  const rows = dates.map((created_at, id) => ({id, created_at, visit_date: '2026-09-23'}));
  assert.deepEqual(periodRecords(rows, {start:'2026-09-17',end:'2026-09-23'}).map(r => r.id), [2, 1]);
});
test('report uses only selected factual text, retains action meaning and flags missing fields', () => {
  const rows = [{id:'1',customer_id:'a',customer_name:'A',opportunity_id:'b',follow_up_record:'  已沟通\r\n需核对范围 ',next_action:'下周再确认',created_at:'2026-09-20'}, {id:'2',customer_id:'a',customer_name:'A',created_at:'2026-09-21'}];
  const original = JSON.stringify(rows), result = composeReport(rows, {start:'2026-09-17',end:'2026-09-23'}, '小王');
  assert.match(result.text,/2 条记录/); assert.match(result.text,/1 个客户、1 个商机/);
  assert.match(result.text,/下周再确认。 \[1\]/); assert.match(result.text,/请补充沟通内容、下一步行动/);
  assert.equal(JSON.stringify(rows), original); assert.deepEqual(result.sources.map(r=>r.id),['1','2']);
  assert.throws(()=>composeReport([],{},''),/至少选择/);
});
