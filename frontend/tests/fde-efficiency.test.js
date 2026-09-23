const test=require('node:test');
const assert=require('node:assert/strict');
const {memberRanking}=require('../miniprogram/utils/fdeEfficiency');
test('个人同级排名按期间指标排序，同值并列且未知不当作零',()=>{
 const result=memberRanking([{user_id:'a',followup_count:4},{user_id:'b',followup_count:6},{user_id:'c',followup_count:4},{user_id:'d',followup_count:0},{user_id:'e',followup_count:null}],'followup','quarter','b');
 assert.deepEqual(result.map(x=>x.rank),[1,2,2,4]);
 assert.equal(result[0].barWidth,100);
 assert.equal(result[0].isSelf,true);
 assert.equal(result[3].barWidth,0);
});
test('团队商机榜使用期间去重数量与团队ID，不把团队标成个人FDE',()=>{
 const result=memberRanking([{user_id:'north',name:'北区团队',role:'fde_team',opportunity_count:7},{user_id:'south',name:'南区团队',role:'fde_team',opportunity_count:4},{user_id:'empty',name:'新团队',role:'fde_team',opportunity_count:0}],'opportunities','month','south','team');
 assert.deepEqual(result.map(row=>[row.user_id,row.value,row.rank]),[['north',7,1],['south',4,2],['empty',0,3]]);
 assert.equal(result[1].isSelected,true);assert.equal(result[1].isSelf,false);assert.equal(result[0].isSelected,false);assert.equal(result[1].roleLabel,'FDE 团队');assert.equal(result[1].meta,'公司同类团队 · FDE 团队');
});
test('个人榜保留负责人同级身份及所属团队，不将team_name丢失',()=>{
 const [row]=memberRanking([{user_id:'lead',role:'fde_lead',team_name:'南区团队',followup_count:3}],'followup','week','lead');
 assert.equal(row.roleLabel,'FDE主管');assert.equal(row.meta,'南区团队 · FDE主管');assert.equal(row.isSelected,true);assert.equal(row.isSelf,true);
});
test('不把当前商机数量当作本年新增，不把全年拜访当作当周',()=>{
 assert.deepEqual(memberRanking([{opportunities:9}],'opportunities','year',''),[]);
 assert.deepEqual(memberRanking([{visits:9}],'followup','week',''),[]);
});
