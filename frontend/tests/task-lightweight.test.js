const test=require('node:test'),assert=require('node:assert/strict');
const {create,bodyFor,project,options}=require('../miniprogram/pages/task-detail/taskLightweightPage');
const mode=require('../miniprogram/utils/taskExecutionMode');
const id='11111111-1111-4111-8111-111111111111';
const versions={version_no:1,requirements_version:1,responsibility_version:1};
const support={available:true,protocol_version:1,supported_versions:[1,2,3],v3_creation_enabled:true,default_creation_mode:'execution_v3'};
function raw(extra={}){return {task_id:id,protocol_version:3,execution_mode:'execution_v3',...versions,status:'in_progress',requirements:{description:'确认客户方案',due_at:'2099-12-01T09:00:23Z'},creator:{user_id:'creator',name:'下发人'},executor:{user_id:'executor',name:'执行人'},current_submission:null,next_step:'继续核对',events:[],submissions:[],capabilities:{append_feedback:true,submit_completion:true,defer:true},...extra};}
function setup(value=raw()){
 const session={userId:'executor',workspaceId:'workspace',capabilities:{task_execution:support}},calls=[];
 const api={taskLightweightCommand:async(...args)=>{calls.push(args);return {changed:true,execution:{...value,version_no:2}};}};
 const page={...create(api,()=>session),data:{taskId:id,task:{id}},taskPageVisible:true,setData(next){Object.assign(this.data,next);},loadExecution:async()=>{}};
 page.applyLightweight(value);return {page,api,calls,session};
}
global.wx={showToast(){}};
test('v3 creation carries only minimal protocol; v1/v2 rollout flags stay separate',()=>{
 assert.equal(mode.creationSupport(support).defaultMode,'execution_v3');
 assert.equal(mode.creationSupport({...support,v3_creation_enabled:false}).v3,false);
 const api=require('../miniprogram/utils/apiClient');
 const body=api.taskCreateBody({executionMode:'execution_v3',description:'确认客户方案',assigneeAccount:'XS001',dueAt:Date.now()+60000,priority:'中',checklist:[{title:'旧草稿'}],deliverable:'旧草稿',acceptanceCriteria:'旧草稿'});
 assert.equal(body.execution_protocol_version,3);assert.equal(body.assignee_account_code,'XS001');
 for(const key of ['checklist','deliverable','acceptance_criteria','actual_contribution'])assert.equal(Object.hasOwn(body,key),false);
 assert.equal(require('../miniprogram/utils/requestIdentity').supports({method:'POST',path:`/tasks/${id}/execution-v3/submit_completion`}),true);
});
test('four-status projection never exposes an invented percentage; pause/review are explanations',()=>{
 for(const [status,label] of Object.entries({in_progress:'进行中',completed:'已结束 · 结果待核对',deferred:'已延期',needs_adjustment:'需调整'})){
  const x=project(raw({status,processing_reason:status==='needs_adjustment'?'awaiting_review':null}),id);
  assert.equal(x.statusLabel,label);assert.equal(x.showProgress,false);assert.equal(x.progress,undefined);
 }
 assert.throws(()=>project(raw({status:'needs_adjustment'}),id));
 const closed=project(raw({status:'completed',completion_action:'close',completion_result:'需求已取消',capabilities:{}}),id);
 assert.equal(closed.completionLabel,'停止说明');assert.deepEqual(options(closed),[]);
});
test('feedback preserves current state; defer, pause, review and self-completion use conditional fields',()=>{
 assert.deepEqual(bodyFor({action:'append_feedback',text:' 已联系 ',next_step:' 明天回访 '},raw({status:'deferred'})),{text:'已联系',next_step:'明天回访'});
 assert.throws(()=>bodyFor({action:'append_feedback',text:'客户已确认',next_step:''},raw()),/下一步/);
 assert.throws(()=>bodyFor({action:'pause',reason:''},raw()));
 assert.deepEqual(bodyFor({action:'pause',reason:'等客户确认'},raw()),{reason:'等客户确认'});
 assert.deepEqual(bodyFor({action:'submit_completion',note:'已交付'},raw({self_managed:true})),{note:'已交付'});
 assert.throws(()=>bodyFor({action:'review_reject',note:''},raw({current_submission:'round'})));
 assert.deepEqual(bodyFor({action:'review_reject',note:'需补报价'},raw({current_submission:'round'})),{submission_id:'round',decision:'reject',note:'需补报价'});
 assert.throws(()=>bodyFor({action:'defer',reason:'延期',dueDate:'2020-01-01',dueTime:'09:00'},raw()));
});
test('unchanged arrangements do not rewrite original seconds or reset state',()=>{
 const value=raw(),date=new Date(value.requirements.due_at);
 const draft={action:'revise',description:value.requirements.description,executor_user_id:'executor',dueDate:`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`,dueTime:`${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`,reason:''};
 assert.deepEqual(bodyFor(draft,value),{description:value.requirements.description});assert.deepEqual(bodyFor({...draft,description:'更新后的任务安排'},value),{description:'更新后的任务安排'});
});
test('stale versions, review rounds and switched identities keep drafts without posting',async()=>{
 for(const kind of ['version','round','identity']){
  const {page,calls,session}=setup(raw({current_submission:'one',capabilities:{review_completion:true}}));
  page.openLightweightUpdate({dataset:{action:'review_reject'}});page.inputLightweight({dataset:{key:'note'},detail:{value:'保留说明'}});
  if(kind==='version')page.applyLightweight(raw({version_no:2,current_submission:'one',capabilities:{review_completion:true}}));
  if(kind==='round')page.applyLightweight(raw({current_submission:'two',capabilities:{review_completion:true}}));
  if(kind==='identity')session.userId='other';
  await page.submitLightweight();assert.equal(calls.length,0);assert.equal(page.data.lightweightDialog.note,'保留说明');assert.match(page.data.lightweightDialogError,/任务已变化/);
 }
});
test('network uncertainty preserves draft and retry command exactly, while late receipts cannot cross identities',async()=>{
 const {page,api,calls,session}=setup();page.openLightweightUpdate();page.inputLightweight({dataset:{key:'text'},detail:{value:'客户已确认'}});
 api.taskLightweightCommand=async(...args)=>{calls.push(args);throw Error('网络连接中断');};
 await page.submitLightweight();await page.submitLightweight();assert.equal(page.data.lightweightDialog.text,'客户已确认');assert.deepEqual(calls[0],calls[1]);
 let resolve;api.taskLightweightCommand=()=>new Promise(r=>resolve=r);const pending=page.submitLightweight();session.userId='other';resolve({changed:true,execution:raw({version_no:8})});await pending;assert.equal(page.data.execution.versions.version_no,1);
});
test('save feedback while awaiting review keeps submitted snapshot and older loaded rounds',async()=>{
 const round=(id,note,at)=>({id,snapshot:{note},created_at:at});
 const first=round('current','原提交结果','2026-10-23T10:00:00Z'),old=round('old','旧轮次','2026-10-22T10:00:00Z');
 const {page,api,calls}=setup(raw({status:'needs_adjustment',processing_reason:'awaiting_review',current_submission:'current',submissions:[first],submissions_next_cursor:'older',capabilities:{append_feedback:true}}));
 page.data.executionSubmissions.push(old);page.data.executionHistoryCursors.submissions='past-old';
 page.openLightweightUpdate();page.inputLightweight({dataset:{key:'text'},detail:{value:'补充信息'}});
 api.taskLightweightCommand=async(...args)=>{calls.push(args);return {changed:true,execution:raw({version_no:2,status:'needs_adjustment',processing_reason:'awaiting_review',current_submission:'current',submissions:[first],submissions_next_cursor:'older',capabilities:{append_feedback:true}})};};
 await page.submitLightweight();assert.equal(calls[0][1],'append_feedback');assert.equal(page.data.execution.currentSubmissionNote,'原提交结果');assert.equal(page.data.execution.statusLabel,'需调整');assert.deepEqual(page.data.executionSubmissions.map(r=>r.id),['current','old']);assert.equal(page.data.executionHistoryCursors.submissions,'past-old');
});
test('explicit adoption uses server three-version snapshot and preserves historic task',async()=>{
 const {page,calls}=setup();page.data.execution=null;page.data.taskMigration={raw:{task_id:id,adopt_versions:versions},versions:{...versions,checklist_revision:6},caps:{adopt_v3:true}};
 page.openLightweightAdopt();await page.submitLightweight();assert.equal(calls[0][1],'adopt');assert.deepEqual(calls[0][2],{...versions,note:''});
});
test('arrangement history has readable before/after values without exposing UUID names',()=>{
 const history=require('../miniprogram/utils/taskHistory');
 const row=history.fromEvents([{id:'event',kind:'revise',body:{command:{reason:'调整分工'},before:{description:'旧内容',executor_user_id:'u1',executor_name:'张三'},after:{description:'新内容',executor_user_id:'u2',executor_name:'李四'}}}])[0];
 assert.deepEqual(row.changes,[{field:'description',label:'任务内容',before:'旧内容',after:'新内容'},{field:'executor_user_id',label:'执行人',before:'张三',after:'李四'}]);assert.equal(row.note,'调整分工');
});
test('merged defer history shows original and new deadlines and keeps its reason',()=>{
 const history=require('../miniprogram/utils/taskHistory');
 const rows=history.mergeExecutionEvents([{id:'deferred',event_type:'defer',note:'客户延后评审',actor_name:'执行人'}],[
  {id:'deferred',kind:'defer',body:{protocol_version:3,command:{reason:'客户延后评审',due_at:'2026-10-28T17:00:00'},before:{due_at:'2026-10-23T17:00:00'},after:{due_at:'2026-10-28T17:00:00'}}},
 ]);
 const row=history.fromEvents(rows)[0];
 assert.equal(row.note,'客户延后评审');assert.equal(row.actor_name,'执行人');
 assert.deepEqual(row.changes,[{field:'due_at',label:'截止时间（延期）',before:'2026-10-23 17:00',after:'2026-10-28 17:00'}]);
 const legacy=history.fromEvents([{id:'legacy',event_type:'defer',note:'历史延期',payload:{due_at:'2026-10-28T17:00:00'}}])[0];
 assert.equal(legacy.note,'历史延期');assert.deepEqual(legacy.changes,[]);
});
test('feedback history keeps each next step with its own feedback instead of the latest task plan',()=>{
 const history=require('../miniprogram/utils/taskHistory');
 const rows=history.fromEvents(history.mergeExecutionEvents([{id:'first',event_type:'append_feedback',note:'已联系客户'}],[
  {id:'first',kind:'append_feedback',body:{command:{text:'已联系客户',next_step:'明天回访'}}},
  {id:'second',kind:'append_feedback',body:{command:{text:'客户已确认',next_step:'发送最终方案'}}},
  {id:'legacy',kind:'append_feedback',body:{command:{text:'旧反馈'}}},
 ]));
 assert.equal(rows.find(row=>row.id==='first').nextStep,'明天回访');
 assert.equal(rows.find(row=>row.id==='second').nextStep,'发送最终方案');
 assert.equal(rows.find(row=>row.id==='legacy').note,'旧反馈');
 assert.equal(Object.hasOwn(rows.find(row=>row.id==='legacy'),'nextStep'),false);
});
test('manual, batch and explicit AI adoption use v3 without checklist requirements',async()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{createRequire}=require('node:module');
 const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js'),requirePage=createRequire(file);
 for(const kind of ['single','batch','advice']){
  let definition,modal;const sent=[],session={userId:'user',workspaceId:'workspace',capabilities:{task_execution:support}};
  const api={taskCreateBody:require('../miniprogram/utils/apiClient').taskCreateBody,createTask:async input=>{sent.push(input);return{id:'created'};},createTasks:async inputs=>{sent.push(inputs);return{items:inputs.map((_,i)=>({id:'created-'+i}))};},decideSuggestion:async(_id,input)=>{sent.push(input);return{tasks:[{id:'created'}]};}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>definition=p,require:n=>n.endsWith('apiClient')?api:requirePage(n),getApp:()=>({globalData:{session}}),wx:{showModal:v=>{modal=v;},showToast(){},setStorageSync(){}},setTimeout:()=>0,clearTimeout},{filename:file});
  const page={...definition,data:{...JSON.parse(JSON.stringify(definition.data)),executionMode:'execution_v3',description:'核对客户资料并反馈确认结论',selectedMembers:[{id:'user',name:'本人',account:'XS001'},...(kind==='batch'?[{id:'other',name:'同事',account:'XS002'}]:[])],...(kind==='advice'?{adviceSource:{id:'advice',version:1}}:{})},setData(x){Object.assign(this.data,x);},getSelectedDueAt:()=>Date.now()+86400000};
  page.submitTask();assert.match(modal.content,/保存后直接进行中/);assert.equal(sent.length,0);modal.success({confirm:true});for(let i=0;i<10;i++)await Promise.resolve();assert.equal(sent.length,1);
  if(kind==='advice'){assert.equal(sent[0].task.execution_protocol_version,3);assert.equal(Object.hasOwn(sent[0].task,'checklist'),false);}else for(const input of [].concat(sent[0]))assert.equal(input.executionMode,'execution_v3');
 }
});
test('four-state filters send status and retain authoritative counts beyond loaded page',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{createRequire}=require('node:module');
 const file=path.resolve(__dirname,'../miniprogram/pages/tasks/index.js');let definition;
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>definition=p,require:createRequire(file),getApp:()=>({globalData:{session:{userId:'executor'}}})},{filename:file});
 const page={...definition,data:{...JSON.parse(JSON.stringify(definition.data)),activeTab:'needs_adjustment'},setData(x){Object.assign(this.data,x);}};
 assert.equal(page.taskParams().status,'needs_adjustment');assert.equal(page.taskParams().tab,'all');
 const packet={items:[{id,status:'needs_adjustment',execution_mode:'execution_v3',processing_reason:'awaiting_review'}],summary:{total:100,pending_count:80,completed_count:20,in_progress_count:55,deferred_count:10,needs_adjustment_count:15,filtered_total:15},has_more:true,next_offset:1};
 page.acceptTaskPage(packet);assert.equal(page.data.needsAdjustmentCount,15);assert.equal(page.data.pendingCount,80);assert.equal(page.data.filteredTotal,15);assert.equal(page.data.tasks[0].processingLabel,'待验收');assert.equal(page.data.tasks[0].showProgress,false);
 delete packet.summary.needs_adjustment_count;assert.throws(()=>page.acceptTaskPage(packet),/四状态统计/);
});


test('Web management API serializes progressing-group filter instead of silently querying all tasks',async()=>{
 const storage=new Map(),requests=[];
 global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{requests.push(o);o.success({statusCode:200,data:{items:[]}});}};
 const api=require('../miniprogram/utils/apiClient');api.saveAuth({access_token:'synthetic-only',actor:{workspace_id:'w',user_id:'a'}});
 await api.listTaskManagement({tab:'pending'});assert.match(requests[0].url,/[?&]tab=pending(?:&|$)/);
 await api.listTaskManagementOwners({tab:'pending'});assert.match(requests[1].url,/[?&]tab=pending(?:&|$)/);
});

test('fresh lightweight receipt replaces earlier terminal classification without changing lifecycle or history',()=>{
 const {page}=setup(raw({status:'completed',end_reason:'completed',completion_action:'review_completion',completion_result:'已交付',capabilities:{}}));
 page.applyLightweight(raw({status:'completed',completion_action:'close',completion_result:'不再继续',capabilities:{}}));
 assert.equal(page.data.execution.statusLabel,'已停止');assert.equal(page.data.execution.completionLabel,'停止说明');
 assert.equal(page.data.task.status,'completed');assert.equal(page.data.task.completion_action,'close');
 assert.equal(require('../miniprogram/utils/taskExecutionPresentation').outcome(page.data.task).isSuccessful,false);
});
