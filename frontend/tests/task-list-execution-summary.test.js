const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const filename=path.resolve(__dirname,'../miniprogram/pages/tasks/index.js');let normalize;
vm.runInNewContext(fs.readFileSync(filename,'utf8')+'\nexpose(normalizeTask);',{Page(){},expose:f=>normalize=f,require:n=>require(path.resolve(path.dirname(filename),n))});
const session={userId:'reviewer'},old={id:'t',status:'pending_review',owner:'旧执行人',assignees:[{user_id:'old',name:'旧执行人',responsibility:'owner'}]};
test('列表优先服务端执行人和当前验收人，不沿用旧owner或发起人',()=>{
 const result=normalize({...old,executor:{user_id:'new',name:'新执行人'},current_reviewer:'reviewer'},session);assert.equal(result.owner,'新执行人');assert.equal(result.statusLabel,'待我验收');assert.equal(old.owner,'旧执行人');
 assert.equal(normalize({...old,execution:{executor:{user_id:'new',name:'新执行人'},current_reviewer:'other'}},session).statusLabel,'等待验收');
 assert.equal(normalize({...old,executor:null},session).owner,'待确认');
});
test('待接手/暂停仅影响未关闭任务展示，不覆盖终态且不产生金额声明',()=>{
 assert.equal(normalize({...old,execution_flags:{execution_handover_pending:true}},session).statusLabel,'待接手');
 assert.equal(normalize({...old,execution_flags:{paused:true}},session).statusLabel,'已暂停');
 const done=normalize({...old,status:'completed',execution_flags:{paused:true}},session);assert.equal(done.statusLabel,'正常完成');assert.equal(Object.hasOwn(done,'actual_contribution'),false);
});
