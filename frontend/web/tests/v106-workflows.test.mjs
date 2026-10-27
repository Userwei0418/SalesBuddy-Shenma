/** v1.0.6 shared apiClient against an isolated in-memory preview. No network API exists. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)),mini=path.join(root,'source/miniprogram');
const clone=value=>JSON.parse(JSON.stringify(value));
function workspace(){
 const storage=new Map(),wxStorage=new Map(),cache=new Map();let clock=Date.now();
 class TestDate extends Date {constructor(...args){super(...(args.length?args:[clock]));}static now(){return clock;}}
 const window={SALES_MODE:'preview',setTimeout,clearTimeout,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}};
 const context=vm.createContext({window,Date:TestDate,URL,URLSearchParams,setTimeout,clearTimeout,console});
 for(const name of ['preview-workflow.js','preview-api.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context,{filename:name});
 context.wx={request:o=>window.SalesPreview.request(o),uploadFile:o=>window.SalesPreview.uploadFile(o),getStorageSync:k=>wxStorage.get(k),setStorageSync:(k,v)=>wxStorage.set(k,v),removeStorageSync:k=>wxStorage.delete(k)};
 function load(filename){const target=path.resolve(mini,filename.endsWith('.js')?filename:filename+'.js');if(cache.has(target))return cache.get(target).exports;const module={exports:{}};cache.set(target,module);vm.runInContext('(function(require,module,exports){'+fs.readFileSync(target,'utf8')+'\n})',context)(specifier=>load(path.relative(mini,path.resolve(path.dirname(target),specifier))),module,module.exports);return module.exports;}
 const api=load('utils/apiClient');
 return {api,load,advance:ms=>clock+=ms,due:()=>new TestDate(clock+86400000).toISOString(),async login(role='sales'){await api.loginWithAccount('PREVIEW_'+role.toUpperCase(),'synthetic');await api.getBusinessOptions();return api.getAuth().actor;}};
}
const taskInput=(w,assigneeAccount='PREVIEW_FDE')=>({description:'合成回归：完成工作并提交书面反馈',associationKind:'daily',assigneeAccount,dueAt:w.due(),priority:'中'});
const rejectsStatus=(promise,status)=>assert.rejects(promise,error=>error.statusCode===status);
async function assignedTask(w){await w.login('sales');const task=await w.api.createTask(taskInput(w));await w.login('fde');return w.api.respondTask(task.id,'accept','',task.version_no);}
async function notices(w,id){return (await w.api.listNotifications()).items.filter(row=>row.object_id===id).map(row=>row.template_code);}

test('v1.0.6 completion rejection, resubmission and creator confirmation retain history and recipient notifications',async()=>{
 const w=workspace(),accepted=await assignedTask(w);
 const submitted=await w.api.completeTask(accepted.id,'第一版合成完成说明',accepted.version_no);
 assert.equal(submitted.status,'pending_review');assert.equal(submitted.completed_at,null);assert.equal(submitted.last_event_type,'submit_completion');
 await w.login('sales');assert.ok((await notices(w,submitted.id)).includes('task_completion_submitted'));
 const rejected=await w.api.respondTask(submitted.id,'reject_completion','请补充验收结果',submitted.version_no);
 assert.equal(rejected.status,'in_progress');assert.equal(rejected.completed_at,null);assert.equal(rejected.completion_review_note,'请补充验收结果');
 await w.login('fde');assert.ok((await notices(w,submitted.id)).includes('task_completion_rejected'));
 const resubmitted=await w.api.completeTask(submitted.id,'已补充验收结果的第二版说明',rejected.version_no);
 assert.equal(resubmitted.status,'pending_review');assert.equal(resubmitted.completion_review_note,null);
 await w.login('sales');const completed=await w.api.respondTask(submitted.id,'approve_completion','确认验收',resubmitted.version_no);
 assert.equal(completed.status,'completed');assert.ok(completed.completed_at);assert.equal(completed.completion_note,'已补充验收结果的第二版说明');
 assert.deepEqual(clone(completed.events.map(row=>row.event_type)),['accept','submit_completion','reject_completion','submit_completion','approve_completion']);
 assert.deepEqual(clone(completed.events.filter(row=>row.event_type==='submit_completion').map(row=>row.note)),['第一版合成完成说明','已补充验收结果的第二版说明']);
 await w.login('fde');assert.ok((await notices(w,submitted.id)).includes('task_completed'));assert.equal((await w.api.getTask(submitted.id)).status,'completed');
});

test('v1.0.6 a task created and claimed by the same actor completes without a review stage',async()=>{
 const w=workspace();await w.login();const task=await w.api.createTask({...taskInput(w),assigneeAccount:undefined,targetPosition:'self'});
 const accepted=await w.api.respondTask(task.id,'accept','',task.version_no),completed=await w.api.completeTask(task.id,'本人创建并领取的合成任务已完成',accepted.version_no);
 assert.equal(completed.status,'completed');assert.ok(completed.completed_at);assert.equal(completed.last_event_type,'complete');
 assert.equal(completed.assignees[0].user_id,completed.creator_user_ref_id);
 assert.deepEqual(clone(completed.events.map(row=>row.event_type)),['accept','complete']);
 await rejectsStatus(w.api.respondTask(task.id,'approve_completion','',completed.version_no),403);
});

test('v1.0.6 review permissions, required notes and version conflicts reject changes without corrupting task state',async()=>{
 const w=workspace(),accepted=await assignedTask(w);
 await rejectsStatus(w.api.completeTask(accepted.id,'   ',accepted.version_no),422);
 assert.equal((await w.api.getTask(accepted.id)).version_no,accepted.version_no);
 const submitted=await w.api.completeTask(accepted.id,'等待发起人验收的合成说明',accepted.version_no);
 await rejectsStatus(w.api.respondTask(accepted.id,'approve_completion','不能自验收',submitted.version_no),403);
 await w.login('manager');await rejectsStatus(w.api.respondTask(accepted.id,'approve_completion','管理员也不是发起人',submitted.version_no),403);
 await w.login('sales');await rejectsStatus(w.api.respondTask(accepted.id,'reject_completion','',submitted.version_no),422);
 await rejectsStatus(w.api.respondTask(accepted.id,'approve_completion','旧页面版本',accepted.version_no),409);
 const unchanged=await w.api.getTask(accepted.id);assert.equal(unchanged.status,'pending_review');assert.equal(unchanged.version_no,submitted.version_no);
 assert.equal(unchanged.events.length,submitted.events.length);
});

test('v1.0.6 authorized empty teams retain stable IDs and FDE target reads distinguish empty from unauthorized',async()=>{
 const w=workspace();await w.login('fde_lead');
 const directory=await w.api.getTeamDirectory(),scopes=await w.api.getFdeScopeOptions(),empty=directory.teams.find(row=>row.active&&row.member_count===0);
 assert.ok(empty);assert.ok(scopes.teams.some(row=>row.id===empty.id));
 assert.equal(scopes.members.some(row=>(row.team_ids||[row.team_id]).includes(empty.id)),false);
 const query={scope:'team',team_id:empty.id,period_type:'quarter',anchor_date:'2026-07-01'};
 const targets=await w.api.getTargets(query);assert.equal(targets.team_id,empty.id);assert.equal(targets.scope,'team');assert.equal(targets.editable,false);assert.deepEqual(clone(targets.items),[]);
 await rejectsStatus(w.api.getTargets({...query,team_id:'unknown-team'}),403);
 await rejectsStatus(w.api.saveTargetBatch({...query,reason:'不可越权写团队',items:[{kind:'visit_count',amount:'1',version_no:null}]}),403);
 await w.login('fde');assert.equal((await w.api.getTeamDirectory()).teams.some(row=>row.id===empty.id),false);await rejectsStatus(w.api.getTargets(query),403);
 const own=await w.api.getTargets({...query,scope:'self',team_id:undefined});assert.equal(own.editable,true);assert.equal(own.team_id,null);
});

async function archivedVisit(w,customer,visitDate){
 const text=`沟通内容：合成客户确认本次验收清单并提出补充数据范围。\n下一步计划：明天补充数据范围后发送验收清单。\n跟进日期：${visitDate}\n对接人：合成联系人甲`;
 const structured=await w.api.submitVisitStage('structure',{customer_id:customer.id,text,is_first_visit:false}),run=await w.api.waitVisitRun(structured.run_id);
 const payload={customer_id:customer.id,source_run_id:run.id,fields:run.result.fields,summary:run.result.summary,opportunity_id:null,source_import_id:null,collaborator_ids:[],fde_participant_ids:[],opportunity_mutation:null};
 const checked=await w.api.submitVisitStage('quality',payload),quality=await w.api.waitVisitRun(checked.run_id);
 return w.api.createVisit(customer.id,{...payload.fields,opportunity_id:null,source_import_id:null,collaborator_ids:[],_opportunity_mutation:null,_quality_review_run_id:quality.id},[]);
}
test('v1.0.6 created_desc visit ordering uses entry time before pagination even when visit dates run in reverse',async()=>{
 const w=workspace();await w.login();const customer=(await w.api.listCustomers()).items[0];
 // The seeded visit is at 10:00 today, which may be later than this test's start time.
 w.advance(86400000);const earlierEntry=await archivedVisit(w,customer,'2026-01-02');w.advance(60000);const laterEntry=await archivedVisit(w,customer,'2026-01-01');
 const first=await w.api.listVisits({customer_id:customer.id,sort:'created_desc',page_size:1,offset:0}),second=await w.api.listVisits({customer_id:customer.id,sort:'created_desc',page_size:1,offset:1});
 assert.equal(first.sort,'created_desc');assert.equal(first.items[0].id,laterEntry.id);assert.equal(second.items[0].id,earlierEntry.id);
 assert.ok(first.items[0].created_at>second.items[0].created_at);assert.ok(first.items[0].visit_date<second.items[0].visit_date);
});
