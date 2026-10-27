const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const mini=path.join(__dirname,'../miniprogram');
const {create}=require(path.join(mini,'pages/task-detail/taskExecutionPage'));
const taskHistory=require(path.join(mini,'utils/taskHistory'));
const taskId='11111111-1111-4111-8111-111111111111';
const requirements=()=>({title:'QA任务',description:'原要求',deliverable:'QA交付',acceptance_criteria:'可读回',due_at:'2026-12-01T09:00:00+08:00',priority_code:'normal',checklist:[{item_id:'22222222-2222-4222-8222-222222222222',definition_version:1,title:'第一项',acceptance_criteria:'证据'}]});
function source(){return {task_id:taskId,protocol_version:1,version_no:4,requirements_version:2,responsibility_version:3,status:'in_progress',requirements:requirements(),executor_user_id:'old',executor:{user_id:'old',name:'原执行人'},current_reviewer:'reviewer',reviewer:{user_id:'reviewer',name:'验收人'},items:[],events:[],progress:{requirements_version:2,completed:0,total:0,percent:null,basis:'missing_checklist'},capabilities:{propose_requirements:true,initiate_executor_handover:true,initiate_reviewer_handover:true}};}
function setup(normalizeTask){let value=source();const calls=[],session={workspaceId:'qa',userId:'reviewer',capabilities:{task_execution:{available:true,protocol_version:1}}};
 const api={getTaskExecution:async()=>value,taskExecutionCommand:async(id,action,body)=>{calls.push({id,action,body});return {execution:value};}};
 const p={...create(api,()=>session,normalizeTask),taskPageVisible:true,data:{taskId,task:{execution_mode:'execution_v1',id:taskId,status:'in_progress',owner:'原执行人',assignees:[{user_id:'old',name:'原执行人',responsibility:'owner'}]}},setData(x){Object.assign(this.data,x);}};
 return {p,api,calls,session,setValue(x){value=x;}};
}
function edit(p){p.openRequirements();p.inputRequirements({currentTarget:{dataset:{key:'title'}},detail:{value:'QA保留的编辑稿'}});p.inputRequirements({currentTarget:{dataset:{key:'reason'}},detail:{value:'QA范围调整'}});p.previewRequirements();}
test('QA-01 三个版本各自变化时禁止旧预览提交且保留草稿',async()=>{
 for(const key of ['version_no','requirements_version','responsibility_version']){const {p,calls,setValue}=setup();await p.loadExecution();edit(p);const next=source();next[key]++;next.progress.requirements_version=next.requirements_version;setValue(next);await p.loadExecution();await p.submitRequirements();assert.equal(calls.length,0,key);assert.equal(p.data.requirementsDraft.title,'QA保留的编辑稿',key);assert.ok(p.data.requirementsPreview.length,key);}
});
test('QA-02 迟到轮询不覆盖命令后的读回，保存期间读请求失效',async()=>{
 const {p,api}=setup();await p.loadExecution();edit(p);let release,reads=0;const old=source(),next=source();next.version_no=5;
 api.getTaskExecution=()=>++reads===1?new Promise(r=>release=r):Promise.resolve(next);api.taskExecutionCommand=async()=>({execution:next});
 const stale=p.loadExecution();await p.submitRequirements();release(old);await stale;assert.equal(p.data.execution.versions.version_no,5);assert.equal(p.data.requirementsDraft,null);
});
test('QA-03 实际409重读不自动重发，要求草稿与预览仍在',async()=>{
 const {p,api,calls}=setup();await p.loadExecution();edit(p);api.taskExecutionCommand=async(id,action,body)=>{calls.push({id,action,body});throw Object.assign(Error('conflict'),{statusCode:409});};
 await p.submitRequirements();assert.equal(calls.length,1);assert.equal(p.data.requirementsDraft.title,'QA保留的编辑稿');assert.ok(p.data.requirementsPreview.length);assert.match(p.data.requirementsError,/草稿已保留/);
});
test('QA-04 轮询后执行责任面板与详情负责人应为同一新执行人',async()=>{
 let normalize;const filename=path.join(mini,'pages/task-detail/index.js');
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page(){},require(name){
   if(name.endsWith('taskExecutionPage'))return {create(api,session,n){normalize=n;return {};}};
   if(name.endsWith('statusLight'))return {taskLight:()=>({tone:'gray'})};
   if(name.endsWith('access'))return {can:()=>false};
   if(name.endsWith('taskCancellation'))return {canCancelTask:()=>false};
   if(name.endsWith('taskPresentation'))return {taskStageLabel:()=>''};
   if(name.endsWith('taskHistory'))return taskHistory;
   return {};
 }},{filename});
 const {p,setValue}=setup(normalize);await p.loadExecution();const next=source();next.version_no=5;next.responsibility_version=4;next.executor_user_id='new';next.executor={user_id:'new',name:'新执行人'};next.execution_handover={id:'handover',status:'accepted',target_user_id:'new',target_name:'新执行人'};p.data.requirementsDraft={title:'保留中的人工草稿'};setValue(next);await p.loadExecution();assert.equal(p.data.requirementsDraft.title,'保留中的人工草稿');
 assert.equal(p.data.execution.executorLabel,'新执行人');assert.equal(p.data.task.owner,'新执行人','详情负责人仍取上次getTask的旧owner');assert.equal(p.data.task.ownerUserId,'new');
 assert.equal(p.data.task.assignees.filter(person=>person.responsibility==='owner').length,1);
 assert.equal(p.data.task.assignees[0].user_id,'new');
 const unassigned={...next,version_no:6,responsibility_version:5,executor_user_id:null,executor:null};setValue(unassigned);await p.loadExecution();
 assert.equal(p.data.task.owner,'待确认');assert.equal(p.data.task.ownerUserId,'');assert.equal(p.data.task.assignees[0].user_id,'');
 assert.equal(p.data.requirementsDraft.title,'保留中的人工草稿');
});
