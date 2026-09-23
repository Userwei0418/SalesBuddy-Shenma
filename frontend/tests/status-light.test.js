const test=require('node:test'),assert=require('node:assert/strict');
const {scoreLight,riskLight,taskLight}=require('../miniprogram/utils/statusLight');
const {opportunitySignal,visitSignal}=require('../miniprogram/utils/customerSignals');
const {displayScore}=require('../miniprogram/utils/profileScores');
test('评分按显示值统一红黄绿，零与缺失分开，47分为红色告警',()=>{
 for(const [value,tone,label] of [[0,'red','告警'],[47,'red','告警'],[59,'red','告警'],[60,'yellow','提醒'],[79,'yellow','提醒'],[80,'green','健康'],[100,'green','健康'],['--','gray','待评估'],[null,'gray','待评估'],['','gray','待评估']])assert.deepEqual([scoreLight(value).tone,scoreLight(value).label],[tone,label]);
 const score=displayScore({value:79.6,text:'80'});assert.equal(score.text,'80');assert.equal(score.signal.tone,'green');
});
test('风险统一黄色提醒，已解除绿色健康，接受风险不等于健康',()=>{
 for(const severity_code of ['critical','high','medium','low'])assert.equal(riskLight({status:'open',severity_code}).tone,'yellow');
 assert.equal(riskLight({status:'accepted'}).label,'提醒');assert.equal(riskLight({status:'resolved'}).label,'健康');
 const state=opportunitySignal({id:'o1',status:'open',expected_close_date:'2099-01-01'},[{opportunity_id:'o1',status:'open',severity_code:'critical'}]);assert.equal(state.label,'提醒');
});
test('任务完成健康、待办提醒、逾期告警，正常取消不判为表现差',()=>{
 const now=Date.parse('2026-09-12T00:00:00Z');
 assert.equal(taskLight({status:'completed',due_at:'2026-01-01'},now).label,'健康');
 assert.equal(taskLight({status:'pending_execution',due_at:'2026-01-01'},now).label,'告警');
 assert.equal(taskLight({status:'in_progress',due_at:'2026-10-01'},now).label,'提醒');
 assert.equal(taskLight({status:'cancelled',due_at:'2026-01-01'},now).label,'提醒');
 assert.equal(taskLight({},now).label,'待评估');
});
test('商机保留健康信号，跟进独立显示质检等级与原因',()=>{
 assert.equal(opportunitySignal({status:'won'}).label,'健康');
 assert.equal(opportunitySignal({status:'lost'}).label,'告警');
 assert.equal(visitSignal({expectation_code:'not_met'}).label,'暂无质检结果');
 assert.equal(visitSignal({status:'archived',follow_up_score:75,quality_review:{grade:'合格'}}).label,'质检合格');
 assert.ok(visitSignal({expectation_code:'not_met'}).reason);
});

test('业务动态92分不变仍健康，低分改善仍告警，无评分更新不冒充健康',()=>{
 const {businessChangeLight}=require('../miniprogram/utils/statusLight');
 for(const before of [90,92,95])assert.equal(businessChangeLight({tone:'blue',ai_review:{relationship_before:before,relationship_after:92}}).tone,'green');
 assert.equal(businessChangeLight({tone:'blue',ai_review:{relationship_before:20,relationship_after:47}}).tone,'red');
 assert.equal(businessChangeLight({tone:'blue',ai_review:{relationship_after:69}}).label,'提醒');
 assert.equal(businessChangeLight({tone:'blue'}).label,'待评估');
 assert.equal(businessChangeLight({tone:'red',ai_review:{relationship_after:92}}).tone,'red');
 assert.equal(businessChangeLight({tone:'orange',ai_review:{relationship_after:92}}).tone,'yellow');
});
