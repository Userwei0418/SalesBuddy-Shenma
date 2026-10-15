import {test, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {alignmentPayload, contributionError, effectiveMetricCodes, alignmentDraft, normalizeTaskAlignment} from '../department-ui/review/tasks/model.js';
import {taskBody, completeAlignedTask, loadTaskKeyResults, saveTaskAlignment, loadTaskTargets, loadTaskAlignmentCapabilities} from '../department-ui/review/tasks/api.js';
import {submitAlignedTask} from '../department-ui/review/tasks/create-adapter.js';

const metrics = [{code:'daily_routine',name:'日常事务',active:true,source_kind:'category',unit:'项',decimal_places:0,goal_selectable:false}, {code:'visit_count',name:'跟进',active:true,source_kind:'actual',unit:'次',decimal_places:0,goal_selectable:true}, {code:'forecast_collection',name:'预测回款',active:true,source_kind:'forecast',unit:'元',decimal_places:2,goal_selectable:true}];
const period={type:'quarter',start:'2026-10-01',end:'2026-12-31',timezone:'Asia/Shanghai'},scope={scope_type:'team',team_id:'team-a'};
const krs = [{id:'kr-a',metric_code:'visit_count',status:'active',linkable:true,period,scope}, {id:'kr-b',metric_code:'visit_count',status:'active',linkable:true,period,scope,effective_target:{id:'do-not-link'}}];
const targets=[{id:'target-a',kind:'visit_count',metric:metrics[1],state:'pending_change',period_type:'quarter',period_start:period.start,period_end:period.end,scope_type:'person',user_ref_id:'person-a'}, {id:null,row_key:'pending',kind:'visit_count',metric:metrics[1],state:'pending_new',period_type:'quarter',period_start:period.start,period_end:period.end,scope_type:'team',team_id:'team-a'}];
test('V2 requires effective objects or explicit other reason, derives metrics and never copies a KR target',()=>{
  assert.throws(()=>alignmentPayload({basis:'objects',target_ids:[],key_result_ids:[]},metrics),/至少/);
  assert.deepEqual(effectiveMetricCodes({basis:'objects',target_ids:['target-a'],key_result_ids:['kr-a','kr-b']},krs,targets),['visit_count']);
  const result=alignmentPayload({basis:'objects',target_ids:['target-a','target-a'],key_result_ids:['kr-a','kr-b','kr-a']},metrics,krs,targets);
  assert.deepEqual(result,{target_ids:['target-a'],key_result_ids:['kr-a','kr-b'],other_reason:null,expected_contribution:null});
  assert.deepEqual(alignmentPayload({basis:'objects',target_ids:[],key_result_ids:['kr-b']},metrics,krs,targets).target_ids,[]);
  assert.throws(()=>alignmentPayload({basis:'objects',target_ids:['pending'],key_result_ids:[]},metrics,krs,targets),/未生效/);
  assert.throws(()=>alignmentPayload({basis:'objects',target_ids:[],key_result_ids:['hidden']},metrics,krs,targets),/不可见/);
  assert.match(contributionError({metric_code:'visit_count',value:'1.5'},metrics,['visit_count']),/整数/);
  assert.match(contributionError({metric_code:'daily_routine',value:'0'},metrics,['daily_routine']),/数值指标/);
  assert.equal(contributionError({metric_code:'forecast_collection',value:'9999999999999999.99'},metrics,['forecast_collection']), '');
  assert.equal(contributionError({metric_code:'forecast_collection',value:'0'},metrics,['forecast_collection']), '');
  assert.match(contributionError({metric_code:'forecast_collection',value:'1e6'},metrics,['forecast_collection']), /非负数/);
  assert.match(contributionError({metric_code:'visit_count',value:'2',note:''},metrics,['visit_count'],true), /说明/);
  assert.deepEqual(alignmentDraft({contract_version:1,metric_codes:['daily_routine'],key_result_ids:[]}),{basis:'objects',target_ids:[],key_result_ids:[],other_reason:'',expected_contribution:null});
});
test('other reason uses JS Unicode trimming and scalar length before immutable intent; old metric payload rejected',()=>{
  const value={basis:'other',target_ids:[],key_result_ids:[],other_reason:'\uFEFF\u00A0  业务培训\n后续跟进  \u3000',expected_contribution:null};
  assert.equal(alignmentPayload(value,metrics).other_reason,'业务培训\n后续跟进');
  assert.equal(alignmentPayload({...value,other_reason:'😀'.repeat(2000)},metrics).other_reason.length,4000);
  assert.throws(()=>alignmentPayload({...value,other_reason:'😀'.repeat(2001)},metrics),/最多/);
  assert.throws(()=>alignmentPayload({...value,other_reason:'\uFEFF\u00A0'},metrics),/请说明/);
  assert.throws(()=>alignmentPayload({...value,target_ids:['target-a']},metrics),/清空/);
  assert.throws(()=>alignmentPayload({...value,expected_contribution:{value:'1'}},metrics),/清空/);
  assert.throws(()=>normalizeTaskAlignment({metric_codes:['daily_routine']}),/需要目标/);
  assert.throws(()=>normalizeTaskAlignment({target_ids:[],key_result_ids:[],other_reason:null}),/二选一/);
  assert.throws(()=>alignmentPayload({basis:'objects',target_ids:Array.from({length:21},(_,i)=>'t'+i),key_result_ids:[]},metrics),/最多/);
});

let page, calls, actor, modal, transport;
const identity = s => `${s.workspaceId}:${s.userId}`;
function fixture({batch = false, advice = false} = {}) {
  calls=[];actor={workspaceId:'workspace-a',userId:'actor-a'};modal=null;
  transport=async options=>{calls.push(options);return options.path==='/tasks/batch'?{items:[{id:'task-a'},{id:'task-b'}]}:options.path.startsWith('/advice')?{task:{id:'task-a'}}:{id:'task-a',status:'completed'};};
  const client={request:options=>transport(options),decideSuggestion:(id,data)=>transport({path:`/advice/suggestions/${id}/decision`,method:'POST',data})};
  const access={identity,can:()=>true};
  const writeModule={exports:{}};
  vm.runInNewContext(readFileSync(new URL('../source/miniprogram/utils/pageWriteContext.js',import.meta.url),'utf8'),{require:()=>access,module:writeModule});
  const app={globalData:{get session(){return actor;}},can:()=>true};
  const wx={showModal:value=>{modal=value;},showToast:()=>{},setStorageSync:()=>{}};
  globalThis.SalesRuntime={app,wx,require:name=>name==='utils/access'?access:name==='utils/pageWriteContext'?writeModule.exports:client};
  let definition;
  vm.runInNewContext(readFileSync(new URL('../source/miniprogram/pages/management-task-create/index.js',import.meta.url),'utf8'),{
    require:name=>name.endsWith('/access')?access:name.endsWith('/apiClient')?client:name.endsWith('/pageWriteContext')?writeModule.exports:name.endsWith('/draftScope')?{draftScope:identity}:{},
    getApp:()=>app,wx,Page:value=>{definition=value;},setTimeout,clearTimeout,Date,console,
  });
  page={...definition,data:{...definition.data,description:'确认客户需求以及后续计划',selectedMembers:[{id:'one',account:'sales-one',name:'成员甲'},...(batch?[{id:'two',account:'sales-two',name:'成员乙'}]:[])],customDueDate:'2099-10-14',customDueTime:'17:00',selectedDue:'2099-10-14 17:00',adviceSource:advice?{id:'suggestion-a',version:3}:null},setData(value){Object.assign(this.data,value);},getSelectedDueAt:()=>Date.parse('2099-10-14T09:00:00Z'),returnAfterSubmission(){}};
  page.writeIdentity=identity(actor);
  return () => page.submitTask();
}
afterEach(()=>{clearTimeout(page?.returnTimer);delete globalThis.SalesRuntime;});
const daily={target_ids:[],key_result_ids:[],other_reason:'开展团队培训',expected_contribution:null};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('real existing create confirmation cancels with no write, then sends required alignment once',async()=>{
  const invoke=fixture();submitAlignedTask(page,daily,invoke);assert.equal(calls.length,0);modal.success({confirm:false});assert.equal(calls.length,0);
  submitAlignedTask(page,daily,invoke);modal.success({confirm:true});await flush();
  assert.equal(calls.length,1);assert.equal(calls[0].path,'/tasks');assert.deepEqual(calls[0].data.alignment,daily);assert.equal(page._submitted,true);assert.equal(calls[0].data.alignment_contract_version,2);
  submitAlignedTask(page,daily,invoke);await flush();assert.equal(calls.length,1);
});
test('batch tasks and adopted suggestion preserve original transaction path with alignment',async()=>{
  let invoke=fixture({batch:true});submitAlignedTask(page,daily,invoke);modal.success({confirm:true});await flush();
  assert.equal(calls[0].path,'/tasks/batch');assert.equal(calls[0].data.tasks.length,2);assert.ok(calls[0].data.tasks.every(task=>task.alignment_contract_version===2 && task.alignment.other_reason==='开展团队培训'));clearTimeout(page.returnTimer);
  invoke=fixture({advice:true});submitAlignedTask(page,daily,invoke);modal.success({confirm:true});await flush();
  assert.equal(calls[0].path,'/advice/suggestions/suggestion-a/decision');assert.equal(calls[0].data.version_no,3);assert.deepEqual(calls[0].data.task.alignment,daily);assert.equal(calls[0].data.task.alignment_contract_version,2);
});
test('uncertain retry retains frozen payload; account switch invalidates confirmation and receipts',async()=>{
  const invoke=fixture();let attempt=0;transport=async options=>{calls.push(options);if(++attempt===1)throw Error('synthetic lost reply');return {id:'task-a'};};
  submitAlignedTask(page,daily,invoke);modal.success({confirm:true});await flush();assert.equal(page.data.submissionPending,true);
  submitAlignedTask(page,{target_ids:['target-a'],key_result_ids:[],other_reason:null,expected_contribution:null},invoke);await flush();
  assert.deepEqual(calls[0].data,calls[1].data);assert.equal(page._submitted,true);clearTimeout(page.returnTimer);
  const again=fixture();submitAlignedTask(page,daily,again);actor={...actor,userId:'actor-b'};modal.success({confirm:true});await flush();assert.equal(calls.length,0);
});
test('actual contribution only appends a reported declaration to the original versioned complete event',async()=>{
  fixture();await completeAlignedTask({id:'task-a',version_no:7},'已提交报告',{metric_code:'forecast_collection',value:'9999999999999999.99',note:'计划已更新，待正式回款'});
  assert.equal(calls.length,1);assert.deepEqual(calls[0],{path:'/tasks/task-a/events',method:'POST',data:{event_type:'complete',note:'已提交报告',version_no:7,actual_contribution:{metric_code:'forecast_collection',value:'9999999999999999.99',note:'计划已更新，待正式回款'}}});
  assert.equal(taskBody({description:'任务',assigneeAccount:'one',dueAt:'2099-10-14',priority:'高',associationKind:'daily'},daily).priority_code,'high');
});
test('KR pagination continues past entirely archived pages and rejects missing eligibility metadata',async()=>{
  fixture();transport=async options=>{calls.push(options);return options.path.endsWith('offset=0')?{items:Array.from({length:100},(_,i)=>({id:'old-'+i,status:'archived',linkable:false})),total:101}:{items:[{id:'current',status:'active',linkable:true,metric_code:'visit_count',period,scope}],total:101};};
  const rows=await loadTaskKeyResults();assert.equal(rows.length,101);assert.deepEqual(rows.filter(row=>row.linkable).map(row=>row.id),['current']);assert.equal(calls.length,2);assert.match(calls[1].path,/offset=100$/);
  transport=async()=>({items:[{id:'unsupported',status:'active'}],total:1});await assert.rejects(()=>loadTaskKeyResults(),/缺少可关联状态/);
  let current=true;transport=async()=>{current=false;return {items:[{id:'late',linkable:true}],total:1};};assert.equal(await loadTaskKeyResults({current:()=>current}),null);
});

// This endpoint is outside the shared Mini transport's mutation allowlist.
test('alignment PATCH has a persistent idempotency receipt across an uncertain response',async()=>{
  fixture();const storage=new Map(),receiptModule={exports:{}};
  vm.runInNewContext(readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8'),{module:receiptModule,Math,wx:{getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value)}});
  const previous=SalesRuntime.require,client=previous('utils/apiClient');
  client.getAuth=()=>({actor:{workspace_id:'workspace-a',user_id:'actor-a'}});client.getBaseUrl=()=>'https://task-alignment.test/api/v1';
  SalesRuntime.require=name=>name==='utils/requestIdentity'?receiptModule.exports:previous(name);
  let count=0;transport=async options=>{calls.push(options);if(++count===1)throw Object.assign(Error('lost reply'),{statusCode:502});return {id:'task-a',version_no:4,alignment:daily};};
  await assert.rejects(()=>saveTaskAlignment({id:'task-a',version_no:3},{...daily,other_reason:'\uFEFF '+daily.other_reason+'\u00A0'}));
  await saveTaskAlignment({id:'task-a',version_no:3},daily);
  assert.equal(calls.length,2);assert.match(calls[0].idempotencyKey,/^[\da-f-]{36}$/);
  assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.equal(calls[0].method,'PATCH');
  assert.deepEqual(calls[0].data,{version_no:3,alignment_contract_version:2,alignment:daily});
  assert.deepEqual(Object.keys(storage.get(receiptModule.exports.STORAGE_KEY)),[]);
});

test('target pagination skips no pages, exposes pending first creation without inventing a target ID',async()=>{
 fixture();transport=async options=>{calls.push(options);return options.path.endsWith('offset=0')?{items:Array.from({length:100},(_,i)=>({...targets[1],row_key:'pending-'+i})),total:101}:{items:[targets[0]],total:101};};
 const rows=await loadTaskTargets({period_type:'quarter',anchor_date:'2026-10-14'});
 assert.equal(rows.length,101);assert.equal(rows.filter(row=>row.id).length,1);assert.match(calls[1].path,/period_type=quarter&anchor_date=2026-10-14&limit=100&offset=100$/);
 transport=async()=>({items:[],total:1});await assert.rejects(()=>loadTaskTargets({period_type:'year',anchor_date:'2026-01-01'}),/未完整/);
 let current=true;transport=async()=>{current=false;return {items:[],total:0};};assert.equal(await loadTaskTargets({period_type:'year',anchor_date:'2026-01-01'},{current:()=>current}),null);
});
test('V2 capability must be explicit; absent or V1-only cannot fall back to category creation',async()=>{
 fixture();transport=async()=>({supported_versions:[1],preferred_version:1});await assert.rejects(()=>loadTaskAlignmentCapabilities(),/配套升级/);
 transport=async()=>({});await assert.rejects(()=>loadTaskAlignmentCapabilities(),/配套升级/);
 transport=async()=>({supported_versions:[1,2],preferred_version:2});assert.equal((await loadTaskAlignmentCapabilities()).preferred_version,2);
 const invoke=fixture();assert.throws(()=>submitAlignedTask(page,null,invoke),/需要目标/);assert.equal(calls.length,0);
});
