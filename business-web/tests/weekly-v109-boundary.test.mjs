import {test} from 'node:test';import assert from 'node:assert/strict';import {reportPeriod,periodRecords,composeReport} from '../department-ui/weekly-report-model.mjs';
test('fourteen-day upload window includes exact first day and excludes future uploads even on same day',()=>{
 const period=reportPeriod('recent',Date.parse('2026-09-24T05:00:00Z'));
 const rows=['2026-09-10T15:59:59Z','2026-09-10T16:00:00Z','2026-09-24T05:00:00Z','2026-09-24T05:00:01Z'].map((created_at,id)=>({created_at,id,visit_date:'2026-09-24'}));
 assert.deepEqual(periodRecords(rows,period).map(r=>r.id),[2,1]);assert.equal(period.start,'2026-09-21');assert.equal(period.end,'2026-09-27');assert.equal(period.inputStart,'2026-09-11');
 const report=composeReport([{id:'a',created_at:'2026-09-12',follow_up_record:'前期事实'},{id:'b',created_at:'2026-09-24T04:00:00Z',follow_up_record:'本周事实'}],period,'合成');
 assert.match(report.text,/本周上传 1 条/);assert.match(report.text,/前期背景/);
});
test('previous report week freezes inputs at Sunday, year rollover retains fourteen inclusive days',()=>{
 const p=reportPeriod('previous',Date.parse('2027-01-04T01:00:00Z'));assert.equal(p.start,'2026-12-28');assert.equal(p.end,'2027-01-03');assert.equal(p.inputStart,'2026-12-21');assert.equal(p.inputEnd,'2027-01-03');
 assert.equal(periodRecords([{id:1,created_at:'2027-01-03T16:00:00Z'}],p).length,0);
});
