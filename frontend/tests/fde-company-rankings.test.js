const test=require('node:test');
const assert=require('node:assert/strict');
const {companyRankings}=require('../miniprogram/utils/fdeCompanyRankings');
const data=items=>({data_source:'database',scope:'all_fde',complete:true,year:2026,quarters:[1],total:items.length,items});
test('禁止把团队排行、部分数据和其他周期冒充全员榜单',()=>{
 assert.equal(companyRankings(null,2026,[1]).companyRankingReady,false);
 for(const patch of [{scope:'team'},{complete:false},{total:9},{year:2025},{quarters:[2]}])assert.throws(()=>companyRankings({...data([]),...patch},2026,[1]));
});
test('跟进与确收分别排名，同值并列，零有效而未知值不排名',()=>{
 const items=[{user_id:'a',name:'甲',followup_count:2,recognized_amount:0},{user_id:'b',name:'乙',followup_count:2,recognized_amount:12000},{user_id:'c',name:'丙',followup_count:0,recognized_amount:null}];
 const result=companyRankings(data(items),2026,[1]);
 assert.deepEqual(result.followupRanking.map(r=>r.rank),[1,1,3]);
 assert.deepEqual(result.recognizedRanking.map(r=>r.user_id),['b','a','c']);
 assert.deepEqual(result.recognizedRanking.map(r=>r.value),['1.2','0','未登记']);
 assert.equal(result.recognizedRanking[2].rank,'—');
});
test('重复成员、缺失指标和负数不生成排行榜',()=>{
 const row={user_id:'a',name:'甲',followup_count:0,recognized_amount:0};
 assert.throws(()=>companyRankings(data([row,row]),2026,[1]));
 for(const patch of [{followup_count:undefined},{followup_count:1.5},{recognized_amount:-1}])assert.throws(()=>companyRankings(data([{...row,...patch}]),2026,[1]));
});
