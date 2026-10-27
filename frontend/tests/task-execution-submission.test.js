const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {create,project}=require('../miniprogram/pages/task-detail/taskExecutionPage');
const taskHistory=require('../miniprogram/utils/taskHistory');
const taskId='f6ed494a-cb35-416d-90a0-40e0e0662bfe',submissionId='914dcaeb-a140-431a-8a2e-79994d6fd004';
const otherId='11111111-1111-4111-8111-111111111111',reviewerId='09f803dd-fc17-5c48-bba2-21ec60d479a7';
const note='LOCAL 1.2.2 验收：已完成两项清单，暂停恢复保留成果；要求变更经双方确认，旧成果复核后恢复进度，页面已显示 100%。提交经理验收。';
const completedAt='2026-10-18T01:48:50.065912+08:00';
// Relevant fields retained from the coordinator's LOCAL v12→v13 readback.
// This is a projection fixture, not a new native-client acceptance claim.
function raw(){return {task_id:taskId,protocol_version:1,status:'pending_review',version_no:12,requirements_version:2,responsibility_version:1,
 current_submission:submissionId,current_reviewer:reviewerId,reviewer:{user_id:reviewerId,name:'本地测试甲公司-MANAGER'},executor:{name:'本地测试甲公司-SALES'},
 items:[],events:[],progress:{requirements_version:2,total:2,completed:2,percent:100,basis:'checklist'},capabilities:{review_completion:true},
 submissions:[{id:submissionId,created_at:'2026-10-18T01:47:10.409054+08:00',snapshot:{note,version_no:12,requirements_version:2,responsibility_version:1,reviewer_user_id:reviewerId}}]};}
function approval(id=submissionId){return {id:'9cb4cb70-0a03-4b8e-8e77-e56cd2d9595b',kind:'review_completion',occurred_at:completedAt,
 body:{command:{decision:'approve',submission_id:id,version_no:12,requirements_version:2,responsibility_version:1},to_status:'completed',from_status:'pending_review',version_no:13}};}
let normalize;
const filename=path.join(__dirname,'../miniprogram/pages/task-detail/index.js');
vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page(){},require(name){
 if(name.endsWith('taskExecutionPage'))return {create(api,session,n){normalize=n;return {};}};
 if(name.endsWith('statusLight'))return {taskLight:()=>({tone:'gray'})};
 if(name.endsWith('access'))return {can:()=>true};
 if(name.endsWith('taskCancellation'))return {canCancelTask:()=>false};
 if(name.endsWith('taskPresentation'))return {taskStageLabel:()=>''};
 if(name.endsWith('taskHistory'))return taskHistory;
 return {};
}},{filename});
function setup(value=raw()){
 const session={userId:reviewerId,workspaceId:'local-a',capabilities:{task_execution:{available:true,protocol_version:1}}};
 const api={getTaskExecution:async()=>value};
 const p={...create(api,()=>session,normalize),taskPageVisible:true,data:{taskId,task:{id:taskId,execution_mode:'execution_v1',creator_user_ref_id:otherId,completion_note:'旧任务说明不得透传',completed_at:'2000-01-01T00:00:00Z'}},setData(next){Object.assign(this.data,next);}};
 return {p,session,api};
}
test('current_submission UUID selects the exact snapshot, never the latest array element',async()=>{
 const value=raw();value.submissions.unshift({id:otherId,snapshot:{note:'旧提交'}});value.submissions.push({id:'22222222-2222-4222-8222-222222222222',snapshot:{note:'数组末条不是当前提交'}});
 const {p}=setup(value);await p.loadExecution();assert.equal(p.data.task.completionNote,note);assert.equal(p.data.execution.completionNote,note);
 assert.equal(p.data.task.canReview,true);assert.equal(p.data.task.statusLabel,'待我验收');assert.equal(p.data.execution.reviewerLabel,'本地测试甲公司-MANAGER');
});
test('missing, malformed, duplicate or unmatched current snapshot cannot leak old or legacy completion notes',async()=>{
 for(const change of [{current_submission:null},{current_submission:{id:submissionId}},{current_submission:otherId},{submissions:[]},{submissions:[...raw().submissions,...raw().submissions]},{submissions:[{id:submissionId,snapshot:{note:''}}]}]){
  const {p}=setup({...raw(),...change});await p.loadExecution();assert.equal(p.data.task.completionNote,'当前完成说明暂不可用，请刷新');assert.equal(p.data.execution.completedAt,null);
 }
});
test('approved completion refreshes note and time from the exact submission approval, retaining other drafts',async()=>{
 const value={...raw(),status:'completed',version_no:13,capabilities:{},events:[approval(),{...approval(otherId),occurred_at:'2099-01-01T00:00:00Z'}]};
 const {p}=setup(value);p.data.executionNote='未提交草稿';await p.loadExecution();
 assert.equal(p.data.task.completionNote,note);assert.equal(p.data.execution.completedAt,completedAt);assert.equal(p.data.task.executionCompletedAt,completedAt);assert.notEqual(p.data.task.completedLabel,'—');assert.notEqual(p.data.task.completedLabel,'完成时间待核对');assert.equal(p.data.task.completedLabel.includes('2000'),false);assert.equal(p.data.executionNote,'未提交草稿');
});
test('a rejected, unrelated, missing or ambiguous approval cannot supply a completion time',()=>{
 for(const events of [[],[approval(otherId)],[{...approval(),body:{...approval().body,command:{decision:'reject',submission_id:submissionId}}}],[approval(),approval()]]){
  assert.equal(project({...raw(),status:'completed',events},taskId).completedAt,null);
 }
 const self={kind:'submit_completion',occurred_at:completedAt,body:{to_status:'completed',after:{current_submission:submissionId}}};
 assert.equal(project({...raw(),status:'completed',events:[self]},taskId).completedAt,completedAt);
 assert.equal(project({...raw(),status:'completed',events:[{...self,body:{...self.body,after:{current_submission:otherId}}}]},taskId).completedAt,null);
});
test('new protocol never displays stale legacy note/time while execution read is pending; legacy retains its fields',()=>{
 const base={status:'pending_review',completion_note:'原完成说明',completed_at:completedAt,creator_user_ref_id:reviewerId};
 const fresh=normalize({...base,execution_mode:'execution_v1'},{userId:reviewerId});assert.equal(fresh.completionNote,'当前完成说明暂不可用，请刷新');assert.equal(fresh.completedLabel,'完成时间待核对');assert.equal(fresh.statusLabel,'等待验收');
 const old=normalize({...base,execution_mode:'legacy'},{userId:reviewerId});assert.equal(old.completionNote,'原完成说明');assert.notEqual(old.completedLabel,'完成时间待核对');assert.equal(old.statusLabel,'待我确认');
 const empty=normalize({status:'completed',execution_mode:'legacy'},{});assert.equal(empty.completionNote,'未填写完成反馈');assert.equal(empty.completedLabel,'—');
});
test('pending-review title follows current review capability; only legacy refers to creator confirmation',()=>{
 const markup=fs.readFileSync(path.join(__dirname,'../miniprogram/pages/task-detail/index.wxml'),'utf8');
 const line=markup.split('\n').find(row=>row.includes('execution.caps.review_completion ?'));
 const expression=line.match(/\{\{(.*?)\}\}/)[1];
 const title=(execution,task)=>vm.runInNewContext(expression,{execution,task});
 assert.equal(title({caps:{review_completion:true}},{canReview:false}),'待你验收');
 assert.equal(title({caps:{review_completion:false}},{canReview:true}),'等待验收');
 assert.equal(title(null,{execution_mode:'execution_v1',canReview:true}),'等待验收');
 assert.equal(title(null,{execution_mode:'legacy',canReview:false}),'等待发起人确认');
 assert.ok(markup.includes('当前验收人：{{execution.reviewerLabel}}'));
});
