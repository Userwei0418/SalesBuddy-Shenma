const test=require('node:test'), assert=require('node:assert/strict');
const {recentVisits}=require('../miniprogram/utils/followupRanking');
const now=new Date('2026-01-03T04:00:00Z');
const visit=(id,recorder_id,customer_id,interaction_at)=>({id,recorder_id,customer_id,interaction_at});
test('近7天按北京时间自然日含今天，跨年边界、无时区值和非法日期正确处理', () => {
  const result = recentVisits([
    visit('before', 'a', 'c', '2025-12-27T15:59:59Z'),
    visit('start', 'a', 'c', '2025-12-27T16:00:00Z'),
    visit('local', 'a', 'c', '2025-12-28 00:00:00'),
    visit('today', 'a', 'c', '2026-01-03'),
    visit('future', 'a', 'c', '2026-01-03T16:00:00Z'),
    visit('invalid', 'a', 'c', '2025-12-32'),
    visit('missing', 'a', 'c', null),
  ], now);
  assert.deepEqual(result.map(item => item.id), ['start', 'local', 'today']);
});
test('日柱图仅保留去重后的已确认记录，不产生前端人员排名',()=>{
 const row={id:'a',interaction_at:'2026-01-03',status:'archived'};
 assert.deepEqual(recentVisits([row,row,{...row,id:'b',status:'recording'}],now),[row]);
 assert.deepEqual(recentVisits([],now),[]);
});
