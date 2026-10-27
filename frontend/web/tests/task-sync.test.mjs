// Deterministic Web lifecycle tests using canonical Page readers and synthetic API responses.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const pageFile = new URL('../../miniprogram/pages/tasks/index.js', import.meta.url);
const req = createRequire(pageFile);
const access = req('../../utils/access');
const executionPage = req('../task-detail/taskExecutionPage');
const clone = value => JSON.parse(JSON.stringify(value));
const flush = async () => {for (let i = 0; i < 25; i++) await Promise.resolve();};
const deferred = () => {let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject};};
function harness() {
  let now = Date.parse('2026-10-17T03:00:00Z'), serial = 0;
  const timers = new Map(), session = {userId:'actor',workspaceId:'company',capabilities:{task_execution:{available:true,protocol_version:1}}};
  const events = () => {const listeners=new Map();return {addEventListener(k,fn){if(!listeners.has(k))listeners.set(k,new Set());listeners.get(k).add(fn);},removeEventListener(k,fn){listeners.get(k)?.delete(fn);},dispatch(k){for(const fn of listeners.get(k)||[])fn();}};};
  const doc = {...events(),visibilityState:'visible'}, network = {onLine:true}, win = events();
  class ClockDate extends Date {constructor(...args){super(...(args.length ? args : [now]));}static now(){return now;}}
  const context=vm.createContext({Intl,Date:ClockDate,document:doc,navigator:network,...win,
    setTimeout(fn,ms){const id=++serial;timers.set(id,{fn,at:now+ms});return id;},clearTimeout(id){timers.delete(id);},clearInterval(id){timers.delete(id);},
    SalesRuntime:{app:{globalData:{session}},require:()=>access,replacePageQuery:()=>true,wx:{setNavigationBarTitle(){}}}});
  vm.runInContext(fs.readFileSync(new URL('../task-workspace.js',import.meta.url),'utf8'),context);
  async function advance(ms){const end=now+ms;while(true){const due=[...timers].filter(([,v])=>v.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].fn();await flush();}now=end;await flush();}
  function attach(page){page.patches=[];page.setData=function(values){this.patches.push(clone(values));Object.assign(this.data,values);};context.SalesTasks.configure(page);return page;}
  return {session,context,doc,network,win,timers,advance,attach};
}
const row = (id='one',fields={}) => ({id,title:'合成任务',status:'in_progress',execution_mode:'execution_v1',version_no:1,executor:{name:'原执行人'},progress:{basis:'checklist',completed:0,total:5,percent:0,requirements_version:1},...fields});
const response = (items,offset=0,total=items.length) => ({items,has_more:offset+items.length<total,next_offset:offset+items.length,summary:{total,pending_count:total,completed_count:0,cancelled_count:0,overdue_count:0}});
async function listFixture() {
  const h=harness(), api={listTaskManagement:async()=>response([row()])};let definition;
  vm.runInNewContext(fs.readFileSync(pageFile,'utf8'),{Page:p=>definition=p,require:p=>p==='../../utils/apiClient'?api:req(p),getApp:()=>({globalData:{session:h.session}}),wx:{}});
  const page=h.attach({...definition,route:'pages/tasks/index',data:clone(definition.data),taskSerial:1,taskPageVisible:true,closed:false,onShow(){},onHide(){},onUnload(){}});
  await page.fetchTaskPage(false);page.onShow();page.patches=[];return {...h,page,api};
}
function raw(fields={}) {return {task_id:'task',protocol_version:1,status:'in_progress',version_no:1,requirements_version:1,responsibility_version:1,items:[{item_id:'item',definition_version:1,title:'推进',state:'not_started'}],events:[],progress:{basis:'checklist',completed:0,total:1,percent:0,requirements_version:1},capabilities:{set_item_progress:true,append_feedback:true},executor:{name:'原执行人'},reviewer:{name:'验收人'},...fields};}
async function detailFixture() {
  const h=harness(),api={getTaskExecution:async()=>raw()},writes=[];
  api.taskExecutionCommand=async(...args)=>{writes.push(args);return {execution:raw()};};
  const page=h.attach({...executionPage.create(api,()=>h.session),route:'pages/task-detail/index',taskPageVisible:true,data:{taskId:'task',task:{id:'task',execution_mode:'execution_v1'}},onShow(){this.executionPoll=h.context.setTimeout(()=>{throw Error('duplicate Mini timer');},3000);},onHide(){}});
  await page.loadExecution();page.onShow();page.patches=[];return {...h,page,api,writes};
}

test('list reads real normalizer within 2 seconds without losing current page, cancellation or filter',async()=>{
  const h=await listFixture(),{page,api}=h;page._departmentTaskPage=2;page._webTaskVisibleEnd=40;
  const items=Array.from({length:40},(_,i)=>row(String(i)));page.acceptTaskPage(response(items.slice(0,20),0,40));page.acceptTaskPage(response(items.slice(20),20,40),true,20);
  Object.assign(page.data,{cancelOpen:true,cancelTaskVersion:1,cancelTaskId:'2'});
  const calls=[];api.listTaskManagement=async params=>{calls.push(params);return response(items.slice(params.offset,params.offset+20).map(r=>({...r,version_no:2,executor:{name:'新执行人'},progress:{...r.progress,completed:3,percent:60}})),params.offset,40);};
  await h.advance(2000);
  assert.deepEqual(calls.map(p=>p.offset),[0,20]);assert.equal(page.data.tasks[25].owner,'新执行人');assert.equal(page.data.tasks[25].progressView.label,'60%');
  assert.equal(page.data.nextOffset,40);assert.equal(page._departmentTaskPage,2);assert.equal(page.data.cancelOpen,true);assert.equal(page.data.cancelTaskVersion,1);
  assert.equal(page.data.webTaskSync.status,'current');assert.ok(page.data.webTaskSync.lastSyncedAt);
  assert.equal(page.patches.some(p=>p.loading===true||p.tasks?.length===0),false);
});
test('single flight drops hidden response, stops background polling and refreshes immediately on foreground',async()=>{
  const h=await listFixture(),pending=deferred();let calls=0;h.api.listTaskManagement=()=>{calls++;return calls===1?pending.promise:Promise.resolve(response([row('new')]));};
  await h.advance(2000);h.win.dispatch('online');h.doc.dispatch('visibilitychange');await flush();assert.equal(calls,1);
  h.doc.visibilityState='hidden';h.doc.dispatch('visibilitychange');pending.resolve(response([row('stale')]));await flush();
  assert.equal(h.page.data.tasks[0].id,'one');assert.equal(h.page.data.webTaskSync.status,'paused');await h.advance(20000);assert.equal(calls,1);
  h.doc.visibilityState='visible';h.doc.dispatch('visibilitychange');await flush();assert.equal(calls,2);assert.equal(h.page.data.tasks[0].id,'new');
  h.page.onHide();await h.advance(20000);assert.equal(calls,2);
});
test('current server count allows task progress to refresh without waiting for a slow owner directory',async()=>{
  const h=await listFixture(),{page,api}=h;
  const directory=deferred();let directoryCalls=0;
  api.listTaskManagementOwners=()=>{directoryCalls++;return directory.promise;};
  api.listTaskManagement=async()=>({...response([row('one',{version_no:2,progress:{basis:'checklist',completed:1,total:2,percent:50,requirements_version:1}})]),team_member_count:3});
  Object.assign(page.data,{cancelOpen:true,cancelTaskId:'one',cancelTaskVersion:1});
  await h.advance(2000);
  assert.equal(directoryCalls,0);assert.equal(page.data.teamMemberCount,3);
  assert.equal(page.data.tasks[0].progressView.label,'50%');assert.equal(page.data.webTaskSync.status,'current');
  assert.equal(page.data.cancelTaskVersion,1);assert.equal(page.data.cancelOpen,true);
});
test('older server without aggregate owner count retains directory fallback on normal loading',async()=>{
  const h=await listFixture();let calls=0;
  h.api.listTaskManagementOwners=async()=>{calls++;return {items:[{task_count:2},{task_count:0},{task_count:1}]};};
  await h.page.fetchTaskPage(false);
  assert.equal(calls,1);assert.equal(h.page.data.teamMemberCount,2);
});
test('offline and failed reads retain confirmed rows and timestamp; online event immediately repairs',async()=>{
  const h=await listFixture();await h.advance(2000);const stamp=h.page.data.webTaskSync.lastSyncedAt;let calls=0;
  h.api.listTaskManagement=async()=>{calls++;throw Error('合成网络中断');};h.network.onLine=false;h.win.dispatch('offline');await h.advance(10000);
  assert.equal(calls,0);assert.equal(h.page.data.webTaskSync.status,'offline');assert.equal(h.page.data.webTaskSync.lastSyncedAt,stamp);
  h.network.onLine=true;h.win.dispatch('online');await flush();assert.equal(calls,1);assert.equal(h.page.data.webTaskSync.status,'error');assert.equal(h.page.data.tasks[0].id,'one');assert.equal(h.page.data.webTaskSync.lastSyncedAt,stamp);
  h.api.listTaskManagement=async()=>response([row('recovered')]);h.win.dispatch('online');await flush();assert.equal(h.page.data.tasks[0].id,'recovered');assert.equal(h.page.data.webTaskSync.status,'current');
});
test('query and manual page reload invalidate late polling responses',async()=>{
  for(const mutate of [p=>p.data.webTaskTeamId='new-team',p=>p.taskSerial++,p=>p.data.nextOffset=40]){
    const h=await listFixture(),pending=deferred();h.api.listTaskManagement=()=>pending.promise;await h.advance(2000);mutate(h.page);pending.resolve(response([row('stale')]));await flush();assert.equal(h.page.data.tasks[0].id,'one');
  }
});
test('identity change invalidates inflight list read and stops subsequent reads',async()=>{
  const h=await listFixture(),pending=deferred();let calls=0;h.api.listTaskManagement=()=>{calls++;return pending.promise;};await h.advance(2000);
  h.session.userId='other';pending.resolve(response([row('stale')]));await flush();await h.advance(10000);assert.equal(calls,1);assert.equal(h.page.data.tasks[0].id,'one');
});
test('hide/show during an old flight resumes without allowing stale data or duplicate requests',async()=>{
  const h=await listFixture(),pending=deferred();let calls=0;h.api.listTaskManagement=()=>{calls++;return calls===1?pending.promise:Promise.resolve(response([row('fresh')]));};
  await h.advance(2000);h.page.onHide();h.page.onShow();await h.advance(2000);assert.equal(calls,1);
  pending.resolve(response([row('stale')]));await flush();assert.equal(h.page.data.tasks[0].id,'one');await h.advance(0);assert.equal(calls,2);assert.equal(h.page.data.tasks[0].id,'fresh');
});
test('detail refresh updates responsibility and progress but preserves all open drafts and their original versions',async()=>{
  const h=await detailFixture(),p=h.page;p.executionAction({detail:{action:'progress',itemId:'item'}});p.data.executionNote='尚未提交的证据';
  p.data.requirementsDraft={title:'未保存要求'};p.data.handoverReason='未提交交接';const versions=clone(p.executionDraftVersions);
  h.api.getTaskExecution=async()=>raw({version_no:2,responsibility_version:2,executor:{name:'新执行人'},progress:{basis:'checklist',total:1,completed:1,percent:100,requirements_version:1}});
  await h.advance(4000);assert.equal(p.data.execution.executorLabel,'新执行人');assert.equal(p.data.execution.progress.percent,100);assert.equal(p.executionPoll,null);
  assert.equal(p.data.executionNote,'尚未提交的证据');assert.equal(p.data.requirementsDraft.title,'未保存要求');assert.equal(p.data.handoverReason,'未提交交接');assert.deepEqual(p.executionDraftVersions,versions);assert.ok(p.data.executionDialog);
  assert.equal(p.patches.some(v=>v.executionLoading===true),false);
  await p.submitExecutionDialog();assert.match(p.data.executionDialogError,/任务版本已变化/);assert.equal(h.writes.length,0);
});
test('manual save/load suppresses detail background reads then resumes; legacy detail remains untouched',async()=>{
  const h=await detailFixture();let calls=0;h.api.getTaskExecution=async()=>{calls++;return raw();};h.page.data.executionBusy=true;await h.advance(5000);assert.equal(calls,0);
  h.page.data.executionBusy=false;await h.advance(250);assert.equal(calls,1);
  h.page.data.execution=null;await h.advance(10000);assert.equal(calls,1);
});
test('malformed detail projection leaves previous data and unsent draft visible',async()=>{
  const h=await detailFixture();h.page.data.executionNote='保留草稿';h.api.getTaskExecution=async()=>raw({progress:{basis:'checklist',total:1,completed:99,percent:9900,requirements_version:1}});
  await h.advance(2000);assert.equal(h.page.data.webTaskSync.status,'error');assert.equal(h.page.data.execution.progress.percent,0);assert.equal(h.page.data.executionNote,'保留草稿');
});
test('revoked list authorization hides the previous rows instead of stamping a false successful sync',async()=>{
  const h=await listFixture();h.api.listTaskManagement=async()=>{throw Object.assign(Error('权限撤销'),{statusCode:403});};
  await h.advance(2000);assert.equal(h.page.data.accessBlocked,true);assert.equal(h.page.data.tasks.length,0);assert.equal(h.page.data.webTaskSync.status,'error');assert.equal(h.page.data.webTaskSync.lastSyncedAt,null);
});


test('V3 polling refreshes submission history and cursors without changing an open update draft',async()=>{
 const h=harness();h.session.capabilities.task_execution.supported_versions=[1,2,3];
 const current=extra=>({...raw(),execution_mode:'execution_v3',protocol_version:3,creator:{name:'下发人'},submissions:[],current_submission:null,...extra});
 let value=current({events_next_cursor:null,submissions_next_cursor:null});const api={getTaskExecution:async()=>value};
 const page=h.attach({...executionPage.create(api,()=>h.session),route:'pages/task-detail/index',taskPageVisible:true,data:{taskId:'task',task:{id:'task',execution_mode:'execution_v3'}},onShow(){},onHide(){}});
 await page.loadExecution();page.onShow();page.data.lightweightDialog={action:'append_feedback',text:'保留未保存反馈'};
 value=current({version_no:2,status:'needs_adjustment',processing_reason:'awaiting_review',current_submission:'round-2',submissions:[{id:'round-2',created_at:'2026-10-23T10:00:00Z',snapshot:{note:'第二轮交付结果'}}],submissions_next_cursor:'older-rounds',events_next_cursor:null});
 await h.advance(2000);
 assert.equal(page.data.executionSubmissions[0].note,'第二轮交付结果');assert.equal(page.data.executionHistoryCursors.submissions,'older-rounds');assert.equal(page.data.lightweightDialog.text,'保留未保存反馈');assert.equal(page.data.execution.currentSubmissionNote,'第二轮交付结果');
 page.onHide();
});
