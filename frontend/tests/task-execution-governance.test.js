const test=require('node:test'),assert=require('node:assert/strict');
const req=require('../miniprogram/pages/task-detail/taskRequirements'),{create,project}=require('../miniprogram/pages/task-detail/taskExecutionPage');
const taskId='11111111-1111-4111-8111-111111111111',target='22222222-2222-4222-8222-222222222222';
const original=()=>({title:'交付方案',description:'说明',deliverable:'方案',acceptance_criteria:'评审通过',due_at:'2026-12-01T09:00:00+08:00',priority_code:'normal',checklist:[{item_id:'a',definition_version:3,title:'原推进项',acceptance_criteria:'原标准'}]});
function source(){return {task_id:taskId,protocol_version:1,version_no:4,requirements_version:2,responsibility_version:3,status:'in_progress',requirements:original(),items:[],events:[],progress:{requirements_version:2,completed:0,total:0,percent:null,basis:'missing_checklist'},capabilities:{initiate_executor_handover:true,initiate_reviewer_handover:true,respond_executor_handover:true,respond_reviewer_handover:true,propose_requirements:true,respond_requirements:true,withdraw_requirements:true},execution_handover:{id:'eh',status:'pending',target_user_id:target},reviewer_handover:{id:'rh',status:'pending',target_user_id:target},pending_requirements:{id:'change',before:original(),after:{...original(),title:'新要求'}}};}
function setup(){const calls=[],session={workspaceId:'w',userId:'u',capabilities:{task_execution:{available:true,protocol_version:1}}};let value=source();
 const api={getTaskExecution:async()=>value,getTaskExecutionCandidates:async(id,options)=>{calls.push({read:options});return {items:[{user_id:target,display_name:'接手同事',teams:[]}],total:1,limit:20,offset:0};},taskExecutionCommand:async(id,action,body)=>{calls.push({action,body});return {execution:value};}};
 const p={...create(api,()=>session),taskPageVisible:true,data:{taskId,task:{execution_mode:'execution_v1',id:taskId}},setData(x){Object.assign(this.data,x);}};return {p,api,calls,session,setValue:x=>value=x};}
test('要求差异展示增删改，原项文本修正加定义版本，恢复原值不加',()=>{
 const base=original(),draft=req.draft(base);draft.checklist[0].title='修正错字';let body=req.prepare(draft,base);assert.equal(body.checklist[0].definition_version,4);
 draft.checklist[0].title=base.checklist[0].title;assert.equal(req.prepare(draft,base).checklist[0].definition_version,3);
 draft.checklist=[{...req.addItem(),title:'新项',acceptance_criteria:'新标准'}];body=req.prepare(draft,base);assert.equal(body.checklist[0].definition_version,1);assert.deepEqual(req.differences(base,body).map(x=>x.label),['移除推进项','新增推进项']);assert.equal(base.checklist[0].title,'原推进项');
});
test('两种交接使用各自候选purpose和独立命令，不提前改责任人',async()=>{
 for(const purpose of ['executor','reviewer']){const {p,calls}=setup();await p.loadExecution();await p.openExecutionHandover({currentTarget:{dataset:{purpose}}});assert.equal(calls[0].read.purpose,purpose);
 p.selectHandoverCandidate({currentTarget:{dataset:{id:target}}});p.inputHandover({currentTarget:{dataset:{key:'handoverReason'}},detail:{value:'岗位交接'}});await p.submitExecutionHandover();
 const write=calls.find(c=>c.action);assert.equal(write.action,`initiate_${purpose}_handover`);assert.equal(write.body.target_user_id,target);assert.equal(write.body.responsibility_version,3);}
});
test('执行/验收响应绑定正确轮次，拒绝须原因，撤回要求绑定change_id',async()=>{
 for(const [intent,action,idKey,id] of [['executorAccept','respond_executor_handover','handover_id','eh'],['reviewerReject','respond_reviewer_handover','handover_id','rh'],['requirementsWithdraw','withdraw_requirements','change_id','change']]){
 const {p,calls}=setup();await p.loadExecution();p.executionAction({detail:{action:intent}});if(intent!=='executorAccept')p.data.executionNote='明确原因';await p.submitExecutionDialog();const write=calls.find(c=>c.action);assert.equal(write.action,action);assert.equal(write.body[idKey],id);}
 const {p,calls}=setup();await p.loadExecution();p.executionAction({detail:{action:'executorReject'}});await p.submitExecutionDialog();assert.equal(calls.length,0);
});
test('变更先预览后提交完整要求；责任变化不自动重发且保留编辑稿',async()=>{
 const {p,calls,setValue}=setup();await p.loadExecution();p.openRequirements();p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'新任务标题'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'范围调整'}});
 await p.submitRequirements();assert.equal(calls.length,0);p.previewRequirements();assert.ok(p.data.requirementsPreview.length);
 setValue({...source(),responsibility_version:4,version_no:5});await p.loadExecution();await p.submitRequirements();assert.equal(calls.length,0);assert.equal(p.data.requirementsDraft.title,'新任务标题');assert.match(p.data.requirementsError,/责任/);
});
test('body与payload读取兼容，旧pending差异只读不会修改当前要求',()=>{
 const a=source();a.events=[{id:'e',kind:'append_feedback',payload:{actor_name:'甲',command:{text:'进展'}}}];const projected=project(a,taskId);assert.equal(projected.feedback[0].text,'进展');assert.equal(projected.requirementsDiff[0].before,'交付方案');assert.equal(projected.raw.requirements.title,'交付方案');
});
test('切交接purpose后旧候选迟到不污染；后台完成也不更新其他身份',async()=>{
 const {p,api}=setup();await p.loadExecution();let resolve;api.getTaskExecutionCandidates=()=>new Promise(r=>resolve=r);const pending=p.openExecutionHandover({currentTarget:{dataset:{purpose:'executor'}}});p.closeExecutionHandover();resolve({items:[{user_id:target,display_name:'迟到',teams:[]}],total:1,offset:0,limit:20});await pending;assert.deepEqual(p.data.handoverCandidates,[]);
});
test('要求确认后刷新任务摘要，待确认提案不提前覆盖当前要求',async()=>{
 const {p,setValue}=setup();await p.loadExecution();assert.equal(p.data.task.title,'交付方案');
 const next=source();next.requirements={...original(),title:'已确认标题',description:'已确认说明',priority_code:'high'};next.requirements_version=3;next.progress.requirements_version=3;setValue(next);await p.loadExecution();
 assert.equal(p.data.task.title,'已确认标题');assert.equal(p.data.task.description,'已确认说明');assert.equal(p.data.task.priority_code,'high');
 assert.equal(p.data.execution.requirementsDiff[0].after,'新要求');
});
test('预览后提交完整要求，待确认回读不假装已经生效',async()=>{
 const {p,calls}=setup();await p.loadExecution();p.openRequirements();p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'申请新标题'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'范围变更'}});p.previewRequirements();await p.submitRequirements();
 const write=calls.find(c=>c.action);assert.equal(write.action,'propose_requirements');assert.equal(write.body.requirements.title,'申请新标题');assert.deepEqual(write.body.requirements.checklist,original().checklist);assert.equal(write.body.requirements.description,'说明');assert.equal(p.data.task.title,'交付方案');assert.equal(p.data.requirementsDraft,null);
});
test('需求预览绑定身份与任务，换身份后同版本也不能提交旧稿',async()=>{
 const {p,calls,session}=setup();await p.loadExecution();p.openRequirements();p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'人工改稿'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'原因'}});p.previewRequirements();session.userId='other';await p.loadExecution();await p.submitRequirements();assert.equal(calls.length,0);assert.equal(p.data.requirementsDraft.title,'人工改稿');assert.ok(p.data.requirementsError);
});

function sheetActions(p){
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 const filename=path.resolve(__dirname,'../miniprogram/pages/task-detail/index.js');let page;
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>page=value,require:name=>name.endsWith('apiClient')?{}:require(path.resolve(path.dirname(filename),name))},{filename});
 p.confirmRequirementsSheet=page.confirmRequirementsSheet;p.editRequirementsSheet=page.editRequirementsSheet;
}
test('要求弹层先核对再提交，第一次确认不写入，校验失败仍保留编辑',async()=>{
 const {p,calls}=setup();sheetActions(p);await p.loadExecution();p.openRequirements();
 await p.confirmRequirementsSheet();assert.equal(calls.length,0);assert.ok(p.data.requirementsDraft);assert.ok(p.data.requirementsError);
 p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'弹层修改标题'}});
 p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'补充交付范围'}});
 await p.confirmRequirementsSheet();assert.equal(calls.length,0);assert.ok(p.data.requirementsPreview.length);
 await p.confirmRequirementsSheet();assert.equal(calls.filter(c=>c.action==='propose_requirements').length,1);assert.equal(p.data.requirementsDraft,null);
});
test('要求弹层返回编辑保留草稿与原提交基准，重新核对不吸收轮询的新版本',async()=>{
 const {p,calls,setValue}=setup();sheetActions(p);await p.loadExecution();p.openRequirements();
 p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'保留人工编辑'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'业务调整'}});
 await p.confirmRequirementsSheet();const baseline={...p.requirementsVersions};
 p.editRequirementsSheet();assert.equal(p.data.requirementsDraft.title,'保留人工编辑');assert.equal(p.data.requirementsReason,'业务调整');assert.equal(p.data.requirementsPreview,null);
 setValue({...source(),version_no:5,responsibility_version:4});await p.loadExecution();
 await p.confirmRequirementsSheet();await p.confirmRequirementsSheet();assert.deepEqual(p.requirementsVersions,baseline);assert.equal(calls.length,0);assert.ok(p.data.requirementsError);assert.equal(p.data.requirementsDraft.title,'保留人工编辑');
});
test('弹层写入中不能关闭或返回编辑，写入结束后取消不发请求',async()=>{
 const {p,calls}=setup();sheetActions(p);await p.loadExecution();p.openRequirements();
 p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'待提交稿'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'调整'}});await p.confirmRequirementsSheet();
 const preview=p.data.requirementsPreview;p.data.executionBusy=true;p.editRequirementsSheet();p.closeRequirements();await p.confirmRequirementsSheet();assert.equal(p.data.requirementsPreview,preview);assert.ok(p.data.requirementsDraft);assert.equal(calls.length,0);
 p.data.executionBusy=false;p.closeRequirements();assert.equal(p.data.requirementsDraft,null);assert.equal(calls.length,0);
});
