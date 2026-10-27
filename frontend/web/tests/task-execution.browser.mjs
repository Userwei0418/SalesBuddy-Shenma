// Actual built Web + shared Page handlers; all backend responses synthetic and isolated.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
import {buildOutput} from '../scripts/build-output.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true,staticDir:buildOutput()});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>{server.close(r);server.closeAllConnections();});});
const id='10000000-0000-4000-8000-000000000001';
async function fixture(run,{ready=true,creationEnabled=ready,taskMode='execution_v1',globalProtocol=true}={}){
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),page=await context.newPage(),errors=[];
 await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','manager'));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
  await page.evaluate(({id,ready,creationEnabled,taskMode,globalProtocol})=>{
   const session=SalesRuntime.app.globalData.session;session.capabilities={...session.capabilities,task_execution:globalProtocol?{protocol_version:1,available:ready,creation_enabled:creationEnabled,legacy_available:true,default_creation_mode:'legacy'}:undefined};
   const owner=session.userId,now=new Date().toISOString(),original=SalesPreview.request.bind(SalesPreview);
   const item={item_id:'20000000-0000-4000-8000-000000000001',definition_version:1,title:'交付合成方案',acceptance_criteria:'含需求清单并通过评审',state:'not_started',note:'',requires_revalidation:false};
   const task={id,...(taskMode?{execution_mode:taskMode,execution_protocol_version:taskMode==='execution_v1'?1:null}:{}),title:'合成任务执行验收',description:'交付客户方案并核对验收标准',status:'pending_confirm',priority_code:'medium',version_no:1,created_at:now,due_at:'2099-10-22T09:00:00Z',creator_user_ref_id:owner,creator_name:'合成发起人',assignees:[{user_id:owner,name:'合成执行人',responsibility:'owner'}],action_permissions:taskMode==='execution_v1'?{}:{'task.accept':true,'task.decline':true,'task.complete':true,'task.review':true,'task.coordinate':true},events:[]};
   const x={task_id:id,status:task.status,protocol_version:1,version_no:1,requirements_version:1,responsibility_version:1,creator_user_ref_id:owner,current_reviewer:owner,executor_user_id:owner,executor:{user_id:owner,name:'合成执行人'},reviewer:{user_id:owner,name:'合成验收人'},requirements:{title:task.title,description:task.description,due_at:task.due_at,priority_code:'medium',deliverable:'方案',acceptance_criteria:'评审通过',checklist:[item]},items:[item],events:[],submissions:[],pause:null,current_submission:null,progress:{basis:'checklist',completed:0,total:1,percent:0,requirements_version:1},capabilities:{}};
   window.taskProbe={calls:[],mode:'normal',task,x,governance:false,candidateQueries:[],reads:[]};
   function caps(){const x=taskProbe.x;const active=x.status==='in_progress';x.capabilities={accept_task:x.status==='pending_confirm',decline_task:x.status==='pending_confirm',append_feedback:active,set_item_progress:active&&!x.pause,pause:active&&!x.pause,resume:active&&!!x.pause,submit_completion:active&&!x.pause&&x.progress.percent===100,review_completion:x.status==='pending_review',withdraw_completion:x.status==='pending_review',recheck_pause:active&&!!x.pause,...(taskProbe.governance?{initiate_executor_handover:!x.execution_handover,initiate_reviewer_handover:!x.reviewer_handover,respond_executor_handover:x.execution_handover?.status==='pending',respond_reviewer_handover:x.reviewer_handover?.status==='pending',propose_requirements:!x.pending_requirements,respond_requirements:!!x.pending_requirements,withdraw_requirements:!!x.pending_requirements}:{})};}
   caps();
   window.SalesPreview={...SalesPreview,request(options){
    const path=new URL(options.url,location.href).pathname,method=options.method||'GET',p=taskProbe;if(method==='GET')p.reads.push(path);
    const reply=(statusCode,data)=>{queueMicrotask(()=>options.success({statusCode,data:structuredClone(data)}));return {abort(){}};};
    if(path==='/api/v1/tasks/alignment-capabilities')return reply(200,{supported_versions:[2],preferred_version:2});
    if(path==='/api/v1/review/metrics')return reply(200,{items:[{code:'daily_routine',name:'日常事务',active:true,source_kind:'category',unit:'项',decimal_places:0,goal_selectable:false},{code:'new_customers',name:'新增客户数',active:true,source_kind:'manual',unit:'家',decimal_places:0,goal_selectable:true}]});
    if(['/api/v1/review/targets','/api/v1/review/key-results'].includes(path))return reply(200,{items:[],total:0});
    if(method==='POST'&&path==='/api/v1/tasks'){p.calls.push({path,body:structuredClone(options.data),header:structuredClone(options.header)});if(p.mode==='rollout_rejected')return reply(409,{detail:'清单任务创建已关闭',code:'TASK_EXECUTION_ROLLOUT_DISABLED'});return reply(201,{...task,id:'10000000-0000-4000-8000-000000000002'});}
    if(path===`/api/v1/tasks/${id}`)return reply(200,{...task,status:p.x.status,version_no:p.x.version_no});
    if(method==='POST'&&path===`/api/v1/tasks/${id}/events`){
      p.calls.push({path,body:structuredClone(options.data),header:structuredClone(options.header)});
      if(p.mode==='denied')return reply(403,{detail:'合成旧任务权限撤销'});
      const b=options.data;if(b.version_no!==p.x.version_no)return reply(409,{detail:'版本已变化'});
      p.x.version_no++;p.x.status=({accept:'pending_execution',reject:'cancelled',complete:'completed',approve_completion:'completed',reject_completion:'in_progress'})[b.event_type]||p.x.status;
      return reply(200,{...task,status:p.x.status,version_no:p.x.version_no,last_event_type:b.event_type,completion_note:b.note});
    }
    if(path.startsWith(`/api/v1/tasks/${id}/execution`)){
     if(method==='GET'){if(path.endsWith('/candidates')){p.candidateQueries.push(options.url);return reply(200,{items:[{user_id:'40000000-0000-4000-8000-000000000001',display_name:'合成接手人',teams:[]}],total:1,limit:20,offset:0});}caps();return reply(200,p.x);}
     p.calls.push({path,body:structuredClone(options.data),header:structuredClone(options.header)});
     if(p.mode==='conflict'){p.mode='normal';p.x.version_no++;return reply(409,{detail:'合成任务版本冲突'});}
     if(p.mode==='denied')return reply(403,{detail:'合成任务权限已撤销'});
     const action=path.split('/').at(-1),b=options.data,x=p.x;
     if(b.version_no!==x.version_no)return reply(409,{detail:'版本已变化'});
     if(action==='accept_task')x.status='in_progress';
     if(action==='set_item_progress'){x.items[0].state=b.state;x.items[0].note=b.note;x.progress.completed=b.state==='completed'?1:0;x.progress.percent=b.state==='completed'?100:0;}
     if(action==='append_feedback')x.events.push({id:crypto.randomUUID(),kind:action,occurred_at:now,payload:{actor_name:'合成执行人',command:b}});
     if(action==='pause')x.pause={id:crypto.randomUUID(),reason:b.reason,recheck_at:b.recheck_at};
     if(action==='resume')x.pause=null;
     if(action==='recheck_pause')x.pause={...x.pause,reason:b.reason,recheck_at:b.recheck_at};
     if(action==='withdraw_completion'){x.status='in_progress';x.current_submission=null;}
     if(action==='initiate_executor_handover'||action==='initiate_reviewer_handover'){x[action.includes('executor')?'execution_handover':'reviewer_handover']={id:crypto.randomUUID(),target_user_id:b.target_user_id,target_name:'合成接手人',reason:b.reason,status:'pending'};}
     if(action==='respond_executor_handover'||action==='respond_reviewer_handover'){const key=action.includes('executor')?'execution_handover':'reviewer_handover',h=x[key];h.status=b.decision==='accept'?'accepted':'rejected';if(b.decision==='accept'){x[action.includes('executor')?'executor':'reviewer']={user_id:h.target_user_id,name:h.target_name};x.responsibility_version++;}}
     if(action==='propose_requirements')x.pending_requirements={id:crypto.randomUUID(),reason:b.reason,before:structuredClone(x.requirements),after:b.requirements};
     if(action==='respond_requirements'){if(b.decision==='accept'){x.requirements=x.pending_requirements.after;x.requirements_version++;x.progress.requirements_version=x.requirements_version;}x.pending_requirements=null;}
     if(action==='withdraw_requirements')x.pending_requirements=null;
     if(action==='submit_completion'){if(b.actual_contribution)p.task.alignment.actual_contribution=b.actual_contribution;x.status='pending_review';x.current_submission=crypto.randomUUID();x.submissions=[{id:x.current_submission,snapshot:{note:b.note}}];}
     if(action==='review_completion'){x.status=b.decision==='approve'?'completed':'in_progress';x.events.push({id:crypto.randomUUID(),kind:action,occurred_at:now,body:{command:b,to_status:x.status}});if(b.decision==='reject'){x.items[0].state='rework';x.progress.completed=0;x.progress.percent=0;}}
     x.version_no++;caps();return reply(200,{changed:true,event_id:crypto.randomUUID(),version_no:x.version_no,requirements_version:1,responsibility_version:1,execution:x});
    }
    return original(options);
   }};
  },{id,ready,creationEnabled,taskMode,globalProtocol});await run(page);assert.deepEqual(errors,[]);
 }finally{await context.close();}
}
async function detail(page){await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/task-detail/index?id='+id}),id);await page.locator('.task-execution').waitFor();await page.waitForFunction(()=>!SalesRuntime.current.data.executionLoading);}
async function saveDialog(page,note){if(note!==undefined)await page.locator('.ant-modal').getByRole('textbox').last().fill(note);await page.locator('.ant-modal').getByRole('button',{name:'确认保存',exact:true}).click();await page.waitForFunction(()=>!SalesRuntime.current.data.executionDialog&&!SalesRuntime.current.data.executionBusy);await page.locator('.ant-modal').waitFor({state:'hidden'});}
for(const status of ['pending_review','completed'])test(`completion projection uses current snapshot and matched approval event: ${status}`,()=>fixture(async page=>{
 await page.evaluate(status=>{
  const x=taskProbe.x,current='30000000-0000-4000-8000-000000000001',old='30000000-0000-4000-8000-000000000002';
  x.status=status;x.current_submission=current;
  x.submissions=[{id:current,snapshot:{note:'当前提交的完整完成说明'}},{id:old,note:'旧版平铺说明不可显示',snapshot:{note:'旧提交不可显示'}}];
  x.events=[{id:'approval-current',kind:'review_completion',occurred_at:'2026-10-18T03:04:00Z',body:{to_status:'completed',command:{submission_id:current,decision:'approve'}}},{id:'approval-old',kind:'review_completion',occurred_at:'2026-10-17T01:02:00Z',body:{to_status:'completed',command:{submission_id:old,decision:'approve'}}}];
 },status);
 await detail(page);const panel=page.locator('.task-execution-submit');await panel.getByText('当前提交的完整完成说明',{exact:true}).waitFor();
 assert.equal(await panel.getByText(/旧提交不可显示|旧版平铺说明不可显示/).count(),0);
 assert.equal(await panel.locator('time').count(),status==='completed'?1:0);
 if(status==='completed')assert.equal(await panel.locator('time').getAttribute('datetime'),'2026-10-18T03:04:00Z');
 assert.deepEqual(await page.evaluate(()=>taskProbe.calls),[]);
}));
for(const invalid of ['missing','ambiguous','old-approval'])test(`completion projection does not infer history when ${invalid}`,()=>fixture(async page=>{
 await page.evaluate(invalid=>{
  const x=taskProbe.x,current='30000000-0000-4000-8000-000000000001',old='30000000-0000-4000-8000-000000000002';
  x.status='completed';x.current_submission=current;
  x.submissions=[{id:old,snapshot:{note:'历史完成说明不应透传'}}];
  if(invalid!=='missing')x.submissions.unshift({id:current,snapshot:{note:'当前说明'}});
  if(invalid==='ambiguous')x.submissions.push({id:current,snapshot:{note:'重复记录不应透传'}});
  x.events=[{id:'approval-old',kind:'review_completion',occurred_at:'2026-10-17T01:02:00Z',body:{to_status:'completed',command:{submission_id:old,decision:'approve'}}}];
 },invalid);
 await detail(page);const panel=page.locator('.task-execution-submit');
 await panel.getByText(invalid==='old-approval'?'当前说明':'当前完成说明暂不可用，请刷新',{exact:true}).waitFor();
 await panel.getByText('完成时间待核对',{exact:false}).waitFor();assert.equal(await panel.locator('time').count(),0);
 assert.equal(await panel.getByText(/历史完成说明不应透传|重复记录不应透传/).count(),0);
 assert.deepEqual(await page.evaluate(()=>taskProbe.calls),[]);
}));
test('PC task accepts, records feedback, pauses/resumes, completes checklist and submits a separate review',()=>fixture(async page=>{
 await detail(page);await page.getByText('0%',{exact:true}).waitFor();await page.getByRole('button',{name:'接受任务',exact:true}).click();await saveDialog(page);
 await page.getByRole('textbox',{name:'过程反馈内容',exact:true}).fill('已与客户确认需求');await page.getByRole('button',{name:'发布过程反馈',exact:true}).click();await page.getByText('已与客户确认需求',{exact:true}).waitFor();
 await page.getByRole('button',{name:'暂停任务',exact:true}).click();await page.getByRole('textbox',{name:'暂停复查时间'}).fill('2099-10-20 10:00');await page.getByRole('textbox',{name:'暂停复查时间'}).press('Enter');await saveDialog(page,'等待客户补充资料');await page.getByText('任务已暂停',{exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'更新推进项 1',exact:true}).count(),0);
 await page.getByRole('button',{name:'恢复任务',exact:true}).click();await saveDialog(page,'资料已齐全');
 await page.getByRole('button',{name:'更新推进项 1',exact:true}).click();await page.getByRole('combobox',{name:'推进状态'}).click();await page.locator('.ant-select-item-option').filter({hasText:'已完成'}).click();await saveDialog(page,'方案已通过评审');
 await page.getByText('100%',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.x.status),'in_progress');
 if(process.env.TASK_SHOTS){fs.mkdirSync(process.env.TASK_SHOTS,{recursive:true});await page.screenshot({path:process.env.TASK_SHOTS+'/checklist-complete.png',fullPage:true});}
 await page.getByRole('button',{name:'提交验收',exact:true}).click();await saveDialog(page,'方案已交付，附评审记录');await page.getByRole('button',{name:'确认验收',exact:true}).click();await saveDialog(page,'成果通过核验');await page.getByText('任务已通过验收。',{exact:true}).waitFor();
 const writes=await page.evaluate(()=>taskProbe.calls);assert.deepEqual(writes.map(w=>w.path.split('/').at(-1)),['accept_task','append_feedback','pause','resume','set_item_progress','submit_completion','review_completion']);assert.ok(writes.every(w=>w.body.requirements_version===1&&w.body.responsibility_version===1&&/^[a-f\d-]{36}$/.test(w.header['Idempotency-Key'])));assert.equal(new Set(writes.map(w=>w.header['Idempotency-Key'])).size,7);
}));
test('conflict retains feedback draft and requires reread/ack; revoked capabilities do not fallback to legacy events',()=>fixture(async page=>{
 await detail(page);await page.getByRole('button',{name:'接受任务',exact:true}).click();await saveDialog(page);
 await page.getByRole('textbox',{name:'过程反馈内容'}).fill('保留这段未提交反馈');await page.evaluate(()=>{taskProbe.mode='conflict';});await page.getByRole('button',{name:'发布过程反馈',exact:true}).click();
 await page.getByRole('button',{name:'已核对最新要求',exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'过程反馈内容'}).inputValue(),'保留这段未提交反馈');assert.equal(await page.getByRole('button',{name:'发布过程反馈',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:'刷新执行信息',exact:true}).click();await page.waitForFunction(()=>!SalesRuntime.current.data.executionError&&!SalesRuntime.current.data.executionLoading);await page.getByRole('button',{name:'已核对最新要求',exact:true}).click();await page.evaluate(()=>{taskProbe.mode='denied';});await page.getByRole('button',{name:'发布过程反馈',exact:true}).click();await page.waitForFunction(()=>Object.keys(SalesRuntime.current.data.execution.caps).length===0);
 assert.equal(await page.getByRole('button',{name:'提交验收',exact:true}).count(),0);assert.equal(await page.evaluate(()=>taskProbe.calls.some(w=>w.path.endsWith('/events'))),false);
}));
test('missing protocol is visibly read-only without fabricated progress or old completion button',()=>fixture(async page=>{
 await detail(page);await page.getByText(/当前服务尚未提供新版任务执行能力/).waitFor();assert.equal(await page.getByText('42%',{exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'确认完成',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'接受任务',exact:true}).count(),0);assert.deepEqual(await page.evaluate(()=>taskProbe.calls),[]);
},{ready:false}));
test('PC creation explicitly selects human checklist and retains alignment in versioned create payload',()=>fixture(async page=>{
 await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/management-task-create/index'}));await page.locator('.department-task-create').waitFor();await page.waitForFunction(()=>SalesRuntime.current.data.members.length&&!SalesRuntime.current.data.recipientLoading);
 await page.getByRole('textbox',{name:'任务描述',exact:true}).fill('整理客户方案并提交评审');
 await page.evaluate(()=>{const p=SalesRuntime.current,m=p.data.members[0];p.confirmRecipients({detail:{ids:[m.id]}});});
 await page.getByRole('radio',{name:'其他原因',exact:true}).check();await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('日常团队方案评审');
 await page.getByText('清单任务',{exact:true}).click();await page.getByRole('button',{name:'下发任务',exact:true}).click();await page.getByText('请填写 1–100 项推进清单',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
 await page.getByRole('button',{name:'添加推进项',exact:true}).click();await page.getByRole('textbox',{name:'推进项 1',exact:true}).fill('提交方案');await page.getByRole('textbox',{name:'验收标准 1',exact:true}).fill('需求范围明确且通过评审');await page.getByRole('button',{name:'下发任务',exact:true}).click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认下发',exact:true}).click();
 await page.waitForFunction(()=>taskProbe.calls.length===1);const body=await page.evaluate(()=>taskProbe.calls[0].body);assert.equal(body.execution_protocol_version,1);assert.equal(body.checklist[0].title,'提交方案');assert.equal(body.checklist[0].acceptance_criteria,'需求范围明确且通过评审');assert.equal(body.alignment.other_reason,'日常团队方案评审');assert.equal(body.alignment_contract_version,2);
}));

test('review rejection requires explicit rework selection and never silently completes the task',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.x.status='pending_review';taskProbe.x.current_submission='30000000-0000-4000-8000-000000000001';taskProbe.x.items[0].state='completed';taskProbe.x.progress.completed=1;taskProbe.x.progress.percent=100;});
 await detail(page);await page.getByRole('button',{name:'选择返工项并驳回',exact:true}).click();await page.getByRole('textbox',{name:'验收意见',exact:true}).fill('需补充客户确认依据');
 await page.locator('.ant-modal').getByRole('button',{name:'确认保存',exact:true}).click();await page.getByText('请选择至少一个返工项',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
 await page.locator('.ant-modal input[type=checkbox]').check();await saveDialog(page);await page.getByText('返工',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.x.status),'in_progress');
 const body=await page.evaluate(()=>taskProbe.calls[0].body);assert.deepEqual(body.rework_item_ids,['20000000-0000-4000-8000-000000000001']);assert.equal(body.submission_id,'30000000-0000-4000-8000-000000000001');
}));
test('rollout disabled keeps standard creation usable and hides checklist choice',()=>fixture(async page=>{
 await fillCreate(page);assert.equal(await page.getByRole('radio',{name:'清单任务',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'添加推进项',exact:true}).count(),0);
 await dispatch(page);const body=await page.evaluate(()=>taskProbe.calls[0].body);assert.equal(body.execution_protocol_version,undefined);assert.equal(body.checklist,undefined);assert.equal(body.alignment.other_reason,'日常团队方案评审');
},{ready:false,creationEnabled:false}));

test('PC handovers use purpose-scoped candidates and change responsibility only after explicit acceptance',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.governance=true;taskProbe.x.status='in_progress';});await detail(page);
 for(const [label,purpose,key,person] of [['执行','executor','execution_handover','executor'],['验收','reviewer','reviewer_handover','reviewer']]){
  const before=await page.evaluate(k=>taskProbe.x[k].name,person);
  await page.getByRole('button',{name:`转交${label}责任`,exact:true}).click();await page.getByRole('button',{name:'合成接手人',exact:true}).click();await page.getByRole('textbox',{name:'交接原因'}).fill('按职责安排交接');await page.getByRole('button',{name:'确认发起交接',exact:true}).click();await page.locator('.ant-modal').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(k=>taskProbe.x[k].name,person),before);assert.equal(await page.evaluate(k=>taskProbe.x[k].status,key),'pending');
  await page.getByRole('button',{name:`确认接手${label}`,exact:true}).click();await saveDialog(page);assert.equal(await page.evaluate(k=>taskProbe.x[k].name,person),'合成接手人');
 }
 const result=await page.evaluate(()=>({calls:taskProbe.calls,queries:taskProbe.candidateQueries}));assert.ok(result.queries.some(u=>u.includes('purpose=executor')));assert.ok(result.queries.some(u=>u.includes('purpose=reviewer')));assert.deepEqual(result.calls.map(c=>c.path.split('/').at(-1)),['initiate_executor_handover','respond_executor_handover','initiate_reviewer_handover','respond_reviewer_handover']);
}));
test('PC requirement edits show diff and remain pending until the other confirmation',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.governance=true;taskProbe.x.status='in_progress';});await detail(page);const before=await page.evaluate(()=>taskProbe.x.requirements.title);
 await page.getByRole('button',{name:'申请修改要求',exact:true}).click();await page.getByRole('textbox',{name:'变更任务标题',exact:true}).fill('修订后的交付方案');await page.getByRole('textbox',{name:'变更原因',exact:true}).fill('客户新增评审范围');await page.getByRole('button',{name:'核对变更差异',exact:true}).click();await page.getByText('请核对变更前后',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
 await page.getByRole('button',{name:'确认并提交变更申请',exact:true}).click();await page.locator('.ant-modal').waitFor({state:'hidden'});await page.getByText('要求变更待确认',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.x.requirements.title),before);
 await page.getByRole('button',{name:'确认以上变更',exact:true}).click();await saveDialog(page);assert.equal(await page.evaluate(()=>taskProbe.x.requirements.title),'修订后的交付方案');assert.equal(await page.evaluate(()=>taskProbe.x.requirements_version),2);
}));
test('PC pause recheck and review withdrawal send actual object IDs with required reasons',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.x.status='in_progress';taskProbe.x.pause={id:'50000000-0000-4000-8000-000000000001',reason:'等客户资料',recheck_at:'2099-10-20T01:00:00Z'};});await detail(page);
 await page.getByRole('button',{name:'复查暂停情况',exact:true}).click();await page.getByRole('textbox',{name:'暂停复查时间'}).fill('2099-10-21 10:00');await page.getByRole('textbox',{name:'暂停复查时间'}).press('Enter');await saveDialog(page,'资料仍未提供，下次复查');assert.equal(await page.evaluate(()=>taskProbe.calls[0].body.pause_id),'50000000-0000-4000-8000-000000000001');
 await page.evaluate(()=>{taskProbe.x.status='pending_review';taskProbe.x.pause=null;taskProbe.x.current_submission='60000000-0000-4000-8000-000000000001';});await page.getByRole('button',{name:'刷新执行信息',exact:true}).click();await page.getByRole('button',{name:'撤回验收申请',exact:true}).click();await saveDialog(page,'补充交付依据后再提交');assert.equal(await page.evaluate(()=>taskProbe.x.status),'in_progress');assert.equal(await page.evaluate(()=>taskProbe.calls[1].body.submission_id),'60000000-0000-4000-8000-000000000001');
}));

test('PC completion retains optional actual contribution, validates count precision, and uses execution command',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.task.alignment={contract_version:1,metric_codes:['new_customers'],key_results:[]};taskProbe.x.status='in_progress';taskProbe.x.progress.percent=100;taskProbe.x.progress.completed=1;taskProbe.x.items[0].state='completed';});await detail(page);
 await page.getByRole('button',{name:'提交验收',exact:true}).click();await page.getByRole('button',{name:'填写实际贡献（选填）',exact:true}).click();await page.getByRole('combobox',{name:'实际贡献指标'}).click();await page.locator('.ant-select-item-option').filter({hasText:'新增客户数'}).click();await page.getByRole('textbox',{name:'实际贡献量',exact:true}).fill('2.5');await page.getByRole('textbox',{name:'实际贡献说明',exact:true}).fill('客户已完成核验，附认领记录');await page.getByRole('textbox',{name:'完成说明',exact:true}).fill('本次交付完成');await page.getByRole('button',{name:'确认保存',exact:true}).click();await page.getByText('计数指标请填写整数',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
 await page.getByRole('textbox',{name:'实际贡献量',exact:true}).fill('2');await saveDialog(page);const write=await page.evaluate(()=>taskProbe.calls[0]);assert.ok(write.path.endsWith('/execution/submit_completion'));assert.deepEqual(write.body.actual_contribution,{metric_code:'new_customers',value:'2',note:'客户已完成核验，附认领记录'});assert.equal(write.body.requirements_version,1);assert.equal(write.body.responsibility_version,1);
}));
test('PC requirement conflict keeps draft and never resubmits with a newer version automatically',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.governance=true;taskProbe.x.status='in_progress';});await detail(page);await page.getByRole('button',{name:'申请修改要求',exact:true}).click();await page.getByRole('textbox',{name:'变更任务标题'}).fill('保留待确认的新要求');await page.getByRole('textbox',{name:'变更原因'}).fill('新增验收要求');await page.getByRole('button',{name:'核对变更差异',exact:true}).click();await page.evaluate(()=>{taskProbe.mode='conflict';});await page.getByRole('button',{name:'确认并提交变更申请',exact:true}).click();await page.waitForFunction(()=>!!SalesRuntime.current.data.requirementsError&&!SalesRuntime.current.data.executionBusy);assert.equal(await page.getByRole('textbox',{name:'变更任务标题'}).inputValue(),'保留待确认的新要求');assert.equal(await page.evaluate(()=>taskProbe.calls.length),1);await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(button=>button.textContent==='确认并提交变更申请'&&button.disabled));assert.equal(await page.getByRole('button',{name:'确认并提交变更申请',exact:true}).isDisabled(),true);
}));

async function fillCreate(page){
 await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/management-task-create/index'}));await page.locator('.department-task-create').waitFor();await page.waitForFunction(()=>SalesRuntime.current.data.members.length&&!SalesRuntime.current.data.recipientLoading);
 await page.getByRole('textbox',{name:'任务描述',exact:true}).fill('整理客户方案并提交评审');await page.evaluate(()=>{const p=SalesRuntime.current,m=p.data.members[0];p.confirmRecipients({detail:{ids:[m.id]}});});
 await page.getByRole('radio',{name:'其他原因',exact:true}).check();await page.getByRole('textbox',{name:'任务其他原因',exact:true}).fill('日常团队方案评审');
}
async function dispatch(page){await page.getByRole('button',{name:'下发任务',exact:true}).click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认下发',exact:true}).click();await page.waitForFunction(()=>taskProbe.calls.length===1);}
async function legacyDetail(page){await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/task-detail/index?id='+id}),id);await page.locator('.review-task-detail').waitFor();}
for(const explicit of [true,false])test(`legacy task accepts and completes through old events, explicit mode=${explicit}`,()=>fixture(async page=>{
 await legacyDetail(page);if(process.env.TASK_SHOTS){fs.mkdirSync(process.env.TASK_SHOTS,{recursive:true});await page.screenshot({path:process.env.TASK_SHOTS+'/legacy-detail.png',fullPage:true});}assert.equal(await page.locator('.ant-progress').count(),0);assert.equal(await page.locator('.task-execution').count(),0);
 await page.getByRole('button',{name:'接受任务',exact:true}).click();await page.locator('.wx-modal-mask').getByRole('button',{name:'接受任务',exact:true}).click();await page.getByPlaceholder('说明交付结果、附件位置或后续安排').fill('客户方案已交付并完成评审');
 await page.getByRole('button',{name:'确认完成',exact:true}).click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认完成',exact:true}).click();await page.waitForFunction(()=>taskProbe.calls.length===2);
 const p=await page.evaluate(()=>taskProbe);assert.deepEqual(p.calls.map(c=>c.body.event_type),['accept','complete']);assert.deepEqual(p.calls.map(c=>c.body.version_no),[1,2]);assert.ok(p.calls.every(c=>c.path.endsWith('/events')));assert.equal(p.reads.some(r=>r.includes('/execution')),false);
},{taskMode:explicit?'legacy':null,globalProtocol:explicit,creationEnabled:false}));
test('advertised protocol with missing task mode blocks all mutations instead of guessing legacy',()=>fixture(async page=>{
 await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/task-detail/index?id='+id}),id);await page.getByText('任务响应不完整，暂不能操作',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'接受任务',exact:true}).count(),0);assert.equal(await page.locator('.task-execution').count(),0);assert.deepEqual(await page.evaluate(()=>taskProbe.calls),[]);
},{taskMode:null}));
test('existing execution task remains actionable after creation rollout is disabled',()=>fixture(async page=>{
 await detail(page);await page.getByRole('button',{name:'接受任务',exact:true}).click();await saveDialog(page);assert.equal(await page.evaluate(()=>taskProbe.calls[0].path.split('/').at(-1)),'accept_task');assert.equal(await page.evaluate(()=>taskProbe.calls.some(c=>c.path.endsWith('/events'))),false);
},{creationEnabled:false}));
test('enabled rollout still defaults to standard creation without protocol or checklist',()=>fixture(async page=>{
 await fillCreate(page);assert.equal(await page.getByRole('radio',{name:'标准任务',exact:true}).isChecked(),true);await dispatch(page);const body=await page.evaluate(()=>taskProbe.calls[0].body);assert.equal(body.execution_protocol_version,undefined);assert.equal(body.checklist,undefined);
}));
test('closing rollout preserves explicitly selected checklist and requires manual standard switch',()=>fixture(async page=>{
 await fillCreate(page);await page.getByText('清单任务',{exact:true}).click();await page.getByRole('button',{name:'添加推进项',exact:true}).click();await page.getByRole('textbox',{name:'推进项 1',exact:true}).fill('保留的人工推进项');await page.getByRole('textbox',{name:'验收标准 1',exact:true}).fill('保留验收依据');
 await page.evaluate(()=>{SalesRuntime.app.globalData.session.capabilities.task_execution.creation_enabled=false;SalesRuntime.current.onShow();});
 await page.getByText('清单任务创建当前已关闭，填写内容已保留。可稍后重试，或手动切换为标准任务。',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'下发任务',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('textbox',{name:'推进项 1',exact:true}).inputValue(),'保留的人工推进项');assert.equal(await page.evaluate(()=>SalesRuntime.current.data.executionMode),'execution_v1');
 await page.waitForFunction(()=>!SalesRuntime.current.data.recipientLoading);await page.getByText('标准任务',{exact:true}).click();if(process.env.TASK_SHOTS)await page.screenshot({path:process.env.TASK_SHOTS+'/standard-create.png',fullPage:true});await dispatch(page);const body=await page.evaluate(()=>taskProbe.calls[0].body);assert.equal(body.execution_protocol_version,undefined);assert.equal(body.checklist,undefined);
}));

test('explicit checklist rollout rejection retains mode and draft without automatic legacy retry',()=>fixture(async page=>{
 await fillCreate(page);await page.getByText('清单任务',{exact:true}).click();await page.getByRole('button',{name:'添加推进项',exact:true}).click();await page.getByRole('textbox',{name:'推进项 1',exact:true}).fill('提交评审材料');await page.getByRole('textbox',{name:'验收标准 1',exact:true}).fill('材料通过核验');await page.evaluate(()=>{taskProbe.mode='rollout_rejected';});await dispatch(page);await page.waitForFunction(()=>!SalesRuntime.current.data.submitting);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.executionMode),'execution_v1');assert.equal(await page.getByRole('textbox',{name:'推进项 1',exact:true}).inputValue(),'提交评审材料');assert.equal(await page.evaluate(()=>taskProbe.calls.length),1);assert.equal(await page.evaluate(()=>taskProbe.calls[0].body.execution_protocol_version),1);
}));

test('task sync renders updated projection within five seconds while feedback and checklist drafts survive',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.x.status='in_progress';});await detail(page);
 await page.getByRole('textbox',{name:'过程反馈内容'}).fill('轮询期间保留反馈草稿');
 await page.getByRole('button',{name:'更新推进项 1'}).click();await page.getByRole('textbox',{name:'成果依据或调整原因'}).fill('轮询期间保留清单草稿');
 const started=Date.now();await page.evaluate(()=>{taskProbe.x.version_no++;taskProbe.x.progress.completed=1;taskProbe.x.progress.percent=100;taskProbe.x.items[0].state='completed';taskProbe.x.executor.name='合成新执行人';});
 await page.waitForFunction(()=>SalesRuntime.current.data.execution?.progress.percent===100,{},{timeout:5000});
 assert.ok(Date.now()-started<5000);await page.locator('.task-execution-summary').getByText('合成新执行人',{exact:true}).waitFor();
 assert.equal(await page.getByRole('textbox',{name:'过程反馈内容'}).inputValue(),'轮询期间保留反馈草稿');assert.equal(await page.getByRole('textbox',{name:'成果依据或调整原因'}).inputValue(),'轮询期间保留清单草稿');
 await page.getByRole('button',{name:'确认保存',exact:true}).click();await page.getByText('任务版本已变化，请关闭后重新核对；说明已保留',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
}));
test('task sync shows last timestamp offline, keeps draft editable and resumes on foreground without background reads',()=>fixture(async page=>{
 await page.evaluate(()=>{taskProbe.x.status='in_progress';});await detail(page);
 await page.waitForFunction(()=>SalesRuntime.current.data.webTaskSync?.status==='current');
 const sync=page.getByRole('status',{name:'任务同步状态'});const timestamp=await page.evaluate(()=>SalesRuntime.current.data.webTaskSync.lastSyncedAt);
 await page.evaluate(()=>{Object.defineProperty(navigator,'onLine',{configurable:true,value:false});dispatchEvent(new Event('offline'));});
 assert.match(await sync.innerText(),/当前离线/);assert.match(await sync.innerText(),/最近同步/);
 await page.getByRole('textbox',{name:'过程反馈内容'}).fill('离线保留的反馈');assert.equal(await page.getByRole('button',{name:'发布过程反馈'}).isDisabled(),true);
 const count=await page.evaluate(()=>taskProbe.reads.filter(p=>p.endsWith('/execution')).length);
 await new Promise(r=>setTimeout(r,2200));assert.equal(await page.evaluate(()=>taskProbe.reads.filter(p=>p.endsWith('/execution')).length),count);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.webTaskSync.lastSyncedAt),timestamp);
 await page.evaluate(()=>{Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'});document.dispatchEvent(new Event('visibilitychange'));Object.defineProperty(navigator,'onLine',{configurable:true,value:true});dispatchEvent(new Event('online'));});
 await new Promise(r=>setTimeout(r,2200));assert.equal(await page.evaluate(()=>taskProbe.reads.filter(p=>p.endsWith('/execution')).length),count);
 await page.evaluate(()=>{taskProbe.x.version_no++;Object.defineProperty(document,'visibilityState',{configurable:true,value:'visible'});document.dispatchEvent(new Event('visibilitychange'));});
 await page.waitForFunction(()=>SalesRuntime.current.data.execution?.versions.version_no===2,{},{timeout:1500});assert.equal(await page.getByRole('textbox',{name:'过程反馈内容'}).inputValue(),'离线保留的反馈');assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
}));
test('task sync updates rendered list progress and owner without manual reload or writes',()=>fixture(async page=>{
 await page.evaluate(()=>{
  const original=SalesPreview.request.bind(SalesPreview);taskProbe.listReads=0;taskProbe.x.status='in_progress';
  window.SalesPreview={...SalesPreview,request(options){
   const path=new URL(options.url,location.href).pathname;
   if(['/api/v1/tasks/management','/api/v1/tasks/management/owners'].includes(path)){
    const p=taskProbe,x=p.x;let data;
    if(path.endsWith('/owners'))data={items:[{user_id:x.executor_user_id,name:x.executor.name,task_count:1}],total:1};
    else{p.listReads++;data={items:[{...p.task,status:x.status,version_no:x.version_no,executor:x.executor,current_reviewer:x.current_reviewer,progress:x.progress}],summary:{total:1,pending_count:1,completed_count:0,cancelled_count:0,overdue_count:0},has_more:false,next_offset:1};}
    queueMicrotask(()=>options.success({statusCode:200,data:structuredClone(data)}));return {abort(){}};
   }return original(options);
  }};
  SalesRuntime.wx.navigateTo({url:'/pages/tasks/index'});
 });
 await page.getByRole('region',{name:'任务列表'}).waitFor();await page.getByLabel('任务清单进度').filter({hasText:'0%'}).waitFor();
 const before=await page.evaluate(()=>taskProbe.listReads),started=Date.now();await page.evaluate(()=>{taskProbe.x.version_no++;taskProbe.x.progress.completed=1;taskProbe.x.progress.percent=100;taskProbe.x.executor.name='合成新执行人';});
 await page.getByLabel('任务清单进度').filter({hasText:'100%'}).waitFor({timeout:5000});assert.ok(Date.now()-started<5000);
 await page.getByRole('cell',{name:'合成新执行人',exact:true}).waitFor();assert.ok(await page.evaluate(()=>taskProbe.listReads)>before);assert.equal(await page.evaluate(()=>taskProbe.calls.length),0);
}));
