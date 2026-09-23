const test=require('node:test'),assert=require('node:assert/strict');
const {progressBars}=require('../miniprogram/utils/fdePresentation');
test('日进展按日期排序，周进展合并周一至周日',()=>{
 const rows=[{date:'2026-09-14',visits:2},{date:'2026-09-13',visits:4},{date:'2026-09-07',visits:1},{date:'2026-09-11',visits:3}];
 assert.deepEqual(progressBars(rows).map(r=>r.visits),[1,3,4,2]);
 const weeks=progressBars(rows,'week');assert.deepEqual(weeks.map(r=>r.visits),[8,2]);
 assert.equal(weeks[0].date,'2026-09-07');assert.equal(weeks[0].endLabel,'09/13');assert.equal(weeks[1].height,25);
});
test('周进展先聚合全部日数据再截取展示，不受日视图7条限制',()=>{
 const rows=Array.from({length:28},(_,i)=>({date:'2026-09-'+String(i+1).padStart(2,'0'),visits:1}));
 assert.equal(progressBars(rows).length,7);assert.equal(progressBars(rows,'week').reduce((s,r)=>s+r.visits,0),28);
});
test('后端周桶截至今天，周视图不把周二扩展成未来周日',()=>{
 const rows=[{date:'2026-09-07',end_date:'2026-09-13',visits:8},{date:'2026-09-14',end_date:'2026-09-15',visits:3}];
 const result=progressBars(rows,'week');assert.equal(result[0].endLabel,'09/13');assert.equal(result[1].endLabel,'09/15');assert.equal(result[1].end_date,'2026-09-15');assert.equal(result[1].visits,3);assert.equal(result[1].height,37.5);
});
test('跨年和历史季度边界保留后端周结束日期，周桶不重复聚合计数',()=>{
 for(const row of [{date:'2025-12-29',end_date:'2026-01-01',visits:2},{date:'2026-09-28',end_date:'2026-09-30',visits:5}]){
  const result=progressBars([row],'week');assert.equal(result.length,1);assert.equal(result[0].visits,row.visits);assert.equal(result[0].endLabel,row.end_date.slice(5).replace('-','/'));
 }
});
test('跨年周边界正确，空记录不补假值，未知数不冒充0',()=>{
 const rows=progressBars([{date:'2026-01-01',visits:1},{date:'2026-01-04',visits:2}],'week');
 assert.equal(rows[0].date,'2025-12-29');assert.equal(rows[0].endLabel,'01/04');assert.equal(rows[0].visits,3);
 assert.deepEqual(progressBars([],'week'),[]);
 assert.equal(progressBars([{date:'2026-09-07',visits:null}],'week')[0].visits,null);
});
