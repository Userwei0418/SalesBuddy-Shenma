const test=require('node:test'),assert=require('node:assert/strict');
const {create,feedbackRows}=require('../miniprogram/pages/task-detail/taskExecutionPage');
const definitions=require('../miniprogram/utils/taskChecklist');
const checklist=require('../miniprogram/pages/task-detail/taskChecklistEditor');
const feedback=require('../miniprogram/utils/taskFeedback');
const mode=require('../miniprogram/utils/taskExecutionMode');
const id='11111111-1111-4111-8111-111111111111',itemId='22222222-2222-4222-8222-222222222222';
const versions={version_no:1,requirements_version:1,responsibility_version:1,checklist_revision:1};
function raw(extra={}){return {task_id:id,protocol_version:2,execution_mode:'execution_v2',status:'pending_execution',...versions,self_managed:true,review_required:false,items:[],events:[],progress:{requirements_version:1,total:0,completed:0,percent:null,basis:'missing_checklist'},capabilities:{save_checklist:true,append_feedback:true,submit_completion:true,initialize_execution:true},...extra};}
function setup(value=raw(),taskMode='execution_v2'){
 const session={userId:'user',workspaceId:'workspace',capabilities:{task_execution:{available:true,protocol_version:1,supported_versions:[1,2],creation_enabled:false,v2_creation_enabled:true,default_creation_mode:'execution_v2'}}},calls=[];
 const api={getTaskExecution:async()=>value,taskExecutionCommand:async(...args)=>{calls.push(args);return {execution:value};}};
 const page={...create(api,()=>session),taskPageVisible:true,data:{taskId:id,task:{id,execution_mode:taskMode}},setData(x){Object.assign(this.data,x);}};
 return {page,api,calls,session};
}
test('v2 allows empty checklist while v1 keeps historical validation',()=>{
 assert.deepEqual(mode.creation({executionMode:'execution_v2',checklist:[]}),{execution_protocol_version:2,checklist:[]});
 assert.deepEqual(definitions.definitions([],{allowEmpty:true}),[]);
 assert.throws(()=>mode.creation({executionMode:'execution_v1',checklist:[]}));
 assert.deepEqual(mode.creation({description:'old caller'}),{});
});
test('creation capabilities keep v1 rollout separate and require explicit v2 enablement',()=>{
 assert.deepEqual(mode.creationSupport({protocol_version:1,creation_enabled:true}),{v1:true,v2:false,v3:false,defaultMode:'legacy'});
 assert.deepEqual(mode.creationSupport({protocol_version:2,creation_enabled:true,default_creation_mode:'execution_v2'}),{v1:true,v2:false,v3:false,defaultMode:'legacy'});
 const support={protocol_version:1,supported_versions:[1,2],creation_enabled:false,v2_creation_enabled:true,default_creation_mode:'execution_v2'};
 assert.deepEqual(mode.creationSupport(support),{v1:false,v2:true,v3:false,defaultMode:'execution_v2'});
 assert.deepEqual(mode.creationSupport({...support,v2_creation_enabled:false}),{v1:false,v2:false,v3:false,defaultMode:'legacy'});
 assert.equal(mode.creationSupport({...support,supported_versions:[1]}).v2,false);
 assert.equal(mode.creationSupport({...support,default_creation_mode:'legacy'}).defaultMode,'legacy');
});
test('checklist definition changes advance only definition version and preserve IDs',()=>{
 const before=[{item_id:itemId,definition_version:3,title:'初稿',acceptance_criteria:'范围已确认'}];
 assert.equal(checklist.prepare(before,before)[0].definition_version,3);
 assert.equal(checklist.prepare([{...before[0],title:'终稿'}],before)[0].definition_version,4);
 assert.throws(()=>checklist.prepare([],before),/至少保留/);
 assert.match(checklist.impact([{...before[0],acceptance_criteria:'新标准'}],[{...before[0],state:'completed'}]),/1 项已完成标准改变/);
});
test('independent checklist sends all four versions; a polling conflict preserves draft',async()=>{
 const {page,api,calls}=setup();await page.loadExecution();page.openChecklist();page.addChecklist();page.data.checklistDraft[0].title='交付方案';page.data.checklistDraft[0].acceptance_criteria='确认范围';page.data.checklistReason='执行拆解';
 api.getTaskExecution=async()=>raw({version_no:2});await page.loadExecution();await page.saveChecklist();assert.equal(calls.length,0);assert.equal(page.data.checklistDraft[0].title,'交付方案');assert.equal(page.data.checklistNeedsReview,true);
 page.confirmChecklistLatest();await page.saveChecklist();assert.equal(calls[0][1],'save_checklist');assert.equal(calls[0][2].checklist_revision,1);assert.equal(calls[0][2].version_no,2);assert.equal(calls[0][2].checklist[0].title,'交付方案');
});
test('legacy reads adoption capability, only explicit adoption emits initialize command',async()=>{
 const {page,calls}=setup(raw({execution_mode:'legacy'}),'legacy');await page.loadExecution();assert.equal(page.data.execution,null);assert.equal(page.data.taskMigration.caps.initialize_execution,true);assert.equal(calls.length,0);
 page.openTaskMigration();page.data.migrationNote='本人确认接入';page.loadTask=async()=>{};await page.initializeTask();assert.equal(calls[0][1],'initialize_execution');assert.equal(calls[0][2].checklist_revision,1);
});
test('self completion still uses server capability, missing deliverables require resolution',async()=>{
 const {page,api,calls}=setup(raw({missing_deliverables:'补齐附件'}));await page.loadExecution();assert.equal(page.data.execution.selfManaged,true);page.executionAction({detail:{action:'complete'}});page.data.executionNote='所有成果已交付';await page.submitExecutionDialog();assert.equal(calls.length,0);
 page.data.executionMissingResolution='附件已经补齐并核对';await page.submitExecutionDialog();assert.equal(calls[0][1],'submit_completion');assert.equal(calls[0][2].missing_deliverables_resolution,'附件已经补齐并核对');
 api.getTaskExecution=async()=>raw({capabilities:{}});await page.loadExecution();assert.equal(await page.sendExecution('submit_completion',{...versions,note:'不应提交'}),false);assert.equal(calls.length,1);
});
test('feedback supports structured fields and safe links; references remain explicit',()=>{
 const body=feedback.prepare({text:'已提交初稿',next_step:'明天评审',blocker:'待评审意见',evidence_urls:'https://example.com/result',reply_to_event_id:'event',feedback_kind:'comment',item_id:itemId},versions);
 assert.equal(body.checklist_revision,1);assert.equal(body.feedback_kind,'comment');assert.deepEqual(body.evidence_urls,['https://example.com/result']);assert.equal(body.reply_to_event_id,'event');
 assert.throws(()=>feedback.prepare({text:'反馈',evidence_urls:'javascript:alert(1)'},versions));
});
test('history uses stable timestamp and unique ID order, merges pages without duplicate feedback',async()=>{
 const event=(id,text)=>({id,kind:'append_feedback',occurred_at:'2026-10-23T10:00:00Z',actor_user_id:'user',body:{actor_name:'张三',command:{text,next_step:'继续',blocker:'等待',evidence_urls:['https://example.com/evidence']}}});
 const a=event('a','前一条'),b=event('b','后一条');assert.deepEqual(feedbackRows([b,a]).map(x=>x.id),['b','a']);
 const {page,api}=setup(raw({events:[b]}));api.getTaskExecutionHistory=async()=>({items:[b,a],next_cursor:null});await page.loadExecution();await page.loadExecutionHistory({currentTarget:{dataset:{kind:'events'}}});
 assert.deepEqual(page.data.execution.feedback.map(x=>x.id),['b','a']);assert.equal(page.data.execution.feedback[0].nextStep,'继续');await page.loadExecution();assert.deepEqual(page.data.execution.feedback.map(x=>x.id),['b','a']);
});
test('task error codes map to reviewed copy, unsupported clients are asked to upgrade',()=>{
 const errors=require('../miniprogram/utils/errorMessages');for(const code of ['TASK_CHECKLIST_INVALID','TASK_EXECUTION_CURSOR_INVALID','TASK_FEEDBACK_CORRECTION_FORBIDDEN','TASK_INITIALIZE_FORBIDDEN','TASK_MISSING_DELIVERABLES_RESOLUTION_REQUIRED'])assert.ok(errors.MESSAGES[code]);
 assert.match(errors.messageFor({code:'TASK_EXECUTION_CLIENT_UPGRADE_REQUIRED'},409),/升级/);
 assert.doesNotMatch(errors.messageFor({code:'TASK_UNKNOWN_INTERNAL_FIELD'},400),/TASK_|FIELD/);
});
test('historical deferred adoption requires one of server allowed resume stages',async()=>{
 const {page,calls}=setup(raw({execution_mode:'legacy',status:'deferred',initialize_resume_options:['pending_confirm']}),'legacy');await page.loadExecution();page.openTaskMigration();page.data.migrationNote='核对历史任务';page.loadTask=async()=>{};await page.initializeTask();assert.equal(calls.length,0);assert.match(page.data.migrationError,/继续的阶段/);
 page.chooseMigrationResume({detail:{value:0}});await page.initializeTask();assert.equal(calls[0][2].resume_status,'pending_confirm');
});
test('summary history cursor starts exactly after the initial read window',async()=>{
 const {page,api}=setup(raw({events_next_cursor:'after-fifty',submissions_next_cursor:null,submissions:[]}));let passed;api.getTaskExecutionHistory=async(_id,options)=>{passed=options;return {items:[],next_cursor:null};};await page.loadExecution();await page.loadExecutionHistory({currentTarget:{dataset:{kind:'events'}}});assert.equal(passed.cursor,'after-fifty');
});
test('v2 serialization retains explicit whole-task requirements for single, batch and AI adoption',()=>{
 const api=require('../miniprogram/utils/apiClient');const body=api.taskCreateBody({description:'已确认任务',executionMode:'execution_v2',deliverable:' 方案与报价 ',acceptanceCriteria:' 客户明确确认 ',checklist:[],assigneeAccount:'self-account',dueAt:Date.now()+60000,priority:'中'});
 assert.equal(body.execution_protocol_version,2);assert.deepEqual(body.checklist,[]);assert.equal(body.deliverable,'方案与报价');assert.equal(body.acceptance_criteria,'客户明确确认');assert.equal(body.assignee_account_code,'self-account');
});
test('bootstrap upgrade selects v2 for untouched page and offers draft-preserving migration otherwise',()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{createRequire}=require('node:module');
 const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js');let definition;
 const session={workspaceId:'w',userId:'u',capabilities:{task_execution:{protocol_version:1,supported_versions:[1,2],creation_enabled:false,v2_creation_enabled:true,default_creation_mode:'execution_v2'}}};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:page=>definition=page,require:createRequire(file),getApp:()=>({globalData:{session}}),setTimeout,clearTimeout},{filename:file});
 const page={...definition,data:JSON.parse(JSON.stringify(definition.data)),setData(next){Object.assign(this.data,next);}};
 page.onShow();assert.equal(page.data.executionMode,'execution_v2');
 page.data.executionMode='legacy';page.data.description='保留的人工描述';page.onShow();assert.equal(page.data.executionMode,'legacy');assert.equal(page.data.executionUpgradeAvailable,true);page.chooseExecutionMode({currentTarget:{dataset:{mode:'execution_v2'}}});assert.equal(page.data.description,'保留的人工描述');assert.equal(page.data.executionMode,'execution_v2');
 session.capabilities.task_execution.v2_creation_enabled=false;page.onShow();page.submitTask();assert.equal(page.data.executionMode,'execution_v2');assert.match(page.data.checklistError,/暂不能下发/);assert.equal(page.data.description,'保留的人工描述');
});

test('manual, batch and explicit AI adoption submit v2 when the old v1 rollout is disabled',async()=>{
 const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),{createRequire}=require('node:module');
 const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js'),requirePage=createRequire(file);
 for(const kind of ['single','batch','advice']){
  let definition,modal;const sent=[],session={userId:'user',workspaceId:'workspace',capabilities:{task_execution:{protocol_version:1,supported_versions:[1,2],creation_enabled:false,v2_creation_enabled:true,default_creation_mode:'execution_v2'}}};
  const api={taskCreateBody:require('../miniprogram/utils/apiClient').taskCreateBody,createTask:async input=>{sent.push(input);return{id:'created'};},createTasks:async inputs=>{sent.push(inputs);return{items:inputs.map((_,i)=>({id:'created-'+i}))};},decideSuggestion:async(_id,input)=>{sent.push(input);return{tasks:[{id:'created'}]};}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:page=>definition=page,require:name=>name.endsWith('apiClient')?api:requirePage(name),getApp:()=>({globalData:{session}}),wx:{showModal:value=>{modal=value;},showToast(){},setStorageSync(){}},setTimeout:()=>0,clearTimeout},{filename:file});
  const page={...definition,data:{...JSON.parse(JSON.stringify(definition.data)),executionMode:'execution_v2',description:'核对客户资料并反馈确认结论',deliverable:'已核对资料',acceptanceCriteria:'结论完整',selectedMembers:[{id:'user',name:'本人',account:'XS001'},...(kind==='batch'?[{id:'other',name:'同事',account:'XS002'}]:[])],...(kind==='advice'?{adviceSource:{id:'advice',version:1}}:{})},setData(next){Object.assign(this.data,next);},getSelectedDueAt:()=>Date.now()+86400000};
  page.submitTask();assert.ok(modal,kind+' requires human confirmation');assert.equal(sent.length,0);modal.success({confirm:true});for(let i=0;i<10;i++)await Promise.resolve();assert.equal(sent.length,1);
  if(kind==='advice'){assert.equal(sent[0].task.execution_protocol_version,2);assert.equal(sent[0].task.assignee_account_code,'XS001');}
  else for(const input of [].concat(sent[0]))assert.equal(input.executionMode,'execution_v2');
 }
});

test('real legacy adoption projection has no execution progress and preserves original owners/reviewer rights',async()=>{
 const legacy={task_id:id,execution_mode:'legacy',protocol_version:null,supported_protocol_version:2,status:'pending_review',...versions,executor_user_id:null,requires_single_owner:true,checklist:[],events:[],events_next_cursor:null,submissions:[],submissions_next_cursor:null,capabilities:{initialize_execution:false}};
 const {page}=setup(legacy,'legacy');const owners=[{user_id:'one',responsibility:'owner'},{user_id:'two',responsibility:'owner'}];Object.assign(page.data.task,{assignees:owners,owner:'原主责',canReview:true});
 await page.loadExecution();assert.equal(page.data.executionError,'');assert.equal(page.data.execution,null);assert.equal(page.data.taskMigration.raw.progress,undefined);assert.equal(page.data.taskMigration.raw.requires_single_owner,true);assert.deepEqual(page.data.task.assignees,owners);assert.equal(page.data.task.canReview,true);
});

test('related task synchronization refreshes one bounded window, preserves other sections and fences identity',async()=>{
 const {DetailReadSession}=require('../miniprogram/utils/detailReadSession');let identity='one';const r=new DetailReadSession(()=>{},()=>identity);r.reset('customer');
 const result=(ids,total=ids.length)=>({items:ids.map(id=>({id,status:'pending_execution'})),has_more:total>ids.length,next_offset:total>ids.length?ids.length:null,summary:{filtered_total:total}});
 await r.load('tasks',()=>result(['t1','t2']));r.pages.visits={items:[{id:'visit'}]};let calls=0;
 await r.refreshTasks(options=>{calls++;assert.deepEqual(options,{offset:0,page_size:100});return {...result(['t1','t2']),items:[{id:'t1',status:'completed'},{id:'t2',status:'pending_execution'}]};});
 assert.equal(calls,1);assert.equal(r.state('tasks').items[0].status,'completed');assert.deepEqual(r.state('visits').items,[{id:'visit'}]);
 await r.refreshTasks(()=>{throw Error('offline');});assert.equal(r.state('tasks').items.length,2);assert.match(r.state('tasks').syncError,/offline/);
 let finish;const pending=r.refreshTasks(()=>new Promise(resolve=>finish=resolve));identity='two';finish(result(['private']));await pending;assert.equal(r.state('tasks').items.some(row=>row.id==='private'),false);
});

test('v1 explicit adoption uses server initialize versions without changing ordinary three-version commands',async()=>{
 const value=raw({protocol_version:1,execution_mode:'execution_v1',initialize_versions:{...versions},capabilities:{initialize_execution:true,append_feedback:true}});delete value.checklist_revision;
 const {page,api,calls}=setup(value,'execution_v1');api.taskExecutionCommand=async(...args)=>{calls.push(args);return {execution:raw()};};await page.loadExecution();assert.equal(page.data.execution.versions.checklist_revision,undefined);
 page.openTaskMigration();page.data.migrationNote='确认升级';page.loadTask=async()=>{};await page.initializeTask();assert.equal(calls[0][1],'initialize_execution');assert.equal(calls[0][2].checklist_revision,1);
});

test('real creation and explicit self-accept events remain visible with accurate sources',()=>{
 const history=require('../miniprogram/utils/taskHistory');
 const rows=history.fromEvents([{id:'created',kind:'created',body:{command:{note:'创建任务'}}},{id:'accepted',kind:'created_self_accepted',body:{source_code:'mini_program'}},{id:'advice',kind:'created_self_accepted',body:{source_code:'business_advice',source_suggestion_id:'suggestion'}}]);
 assert.equal(rows.length,3);assert.equal(rows[0].label,'创建任务');assert.match(rows[1].note,/本人确认创建给自己/);assert.match(rows[2].note,/明确采纳 AI 建议并指定自己/);
});
test('new submission snapshots appear after an initially empty read and stable merges preserve paging progress',async()=>{
 const snap=(id,at,note=id)=>({id,created_at:at,snapshot:{note,items:[],checklist_revision:1}});
 const a=snap('a','2026-10-23T10:00:00Z'),b=snap('b','2026-10-23T09:00:00Z'),c=snap('c','2026-10-23T10:00:00Z');
 const {page,api}=setup(raw({submissions:[],submissions_next_cursor:null,events_next_cursor:null}));await page.loadExecution();assert.deepEqual(page.data.executionSubmissions,[]);
 api.getTaskExecution=async()=>raw({submissions:[a],submissions_next_cursor:'older-a',events_next_cursor:null});await page.loadExecution();assert.deepEqual(page.data.executionSubmissions.map(x=>x.id),['a']);assert.equal(page.data.executionHistoryCursors.submissions,'older-a');
 api.getTaskExecutionHistory=async()=>({items:[b],next_cursor:'older-b'});await page.loadExecutionHistory({currentTarget:{dataset:{kind:'submissions'}}});
 api.getTaskExecution=async()=>raw({submissions:[c,{...a,snapshot:{...a.snapshot,note:'最新头部'}}],submissions_next_cursor:'new-head',events_next_cursor:null});await page.loadExecution();
 assert.deepEqual(page.data.executionSubmissions.map(x=>x.id),['c','a','b']);assert.equal(page.data.executionSubmissions[1].note,'最新头部');assert.equal(page.data.executionHistoryCursors.submissions,'older-b');
 api.getTaskExecution=async()=>raw({submissions:[snap('new','2026-10-24T10:00:00Z')],submissions_next_cursor:'new-gap',events_next_cursor:null});await page.loadExecution();assert.equal(page.data.executionSubmissions.length,4);assert.equal(page.data.executionHistoryCursors.submissions,'new-gap');
});
