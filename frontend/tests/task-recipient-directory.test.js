const test=require('node:test'),assert=require('node:assert/strict');
const {allTaskRecipients,allTaskRecipientDirectory,allTaskReassignmentDirectory}=require('../miniprogram/utils/taskRecipients');
const metadata={teams:[{id:'a',name:'团队甲'},{id:'b',name:'团队乙'}],defaults:{team_id:'a'}};
const member=(id,team='a')=>({id,name:id,account_code:id.toUpperCase(),team_ids:[team]});
test('跨页保留团队目录和默认团队，转交每页均绑定同一任务',async()=>{
 const calls=[];const result=await allTaskReassignmentDirectory({getTaskReassignmentOptions:async(id,options)=>{
  calls.push([id,options.offset]);return {...metadata,items:[member(options.offset?'two':'one',options.offset?'b':'a')],has_more:!options.offset,next_offset:options.offset?null:1};
 }},'task-one');
 assert.deepEqual(calls,[['task-one',0],['task-one',1]]);assert.deepEqual(result.teams,metadata.teams);assert.equal(result.defaults.team_id,'a');assert.equal(result.items.length,2);
});
test('团队元数据缺失或页间变化报错，不能从人员部门名称反推',async()=>{
 await assert.rejects(()=>allTaskRecipientDirectory({listTaskRecipients:async()=>({items:[member('one')],has_more:false})}),/团队目录/);
 await assert.rejects(()=>allTaskRecipientDirectory({listTaskRecipients:async({offset})=>({...metadata,defaults:{team_id:offset?'b':'a'},items:[member(offset?'two':'one')],has_more:!offset,next_offset:1})}),/团队目录已变化/);
 await assert.rejects(()=>allTaskRecipientDirectory({listTaskRecipients:async()=>({...metadata,items:[member('one','unknown')]})}),/人员团队信息不完整/);
});
test('失效请求停止翻页，旧 items-only helper 保持兼容',async()=>{
 let valid=true,calls=0;const result=await allTaskRecipientDirectory({listTaskRecipients:async()=>{calls++;valid=false;return {...metadata,items:[member('one')],has_more:true,next_offset:1};}},()=>valid);
 assert.equal(result,null);assert.equal(calls,1);
 assert.deepEqual(await allTaskRecipients({listTaskRecipients:async()=>({items:[{id:'legacy'}]})}),[{id:'legacy'}]);
});
