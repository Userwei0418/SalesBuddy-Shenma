/** Six native detail/review routes, local synthetic fixtures; all business network blocked. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const customer = '00000010-0000-4000-8000-000000000001', opportunity = '00000011-0000-4000-8000-000000000001';
const task = '99999999-1111-4111-8111-111111111111', risk = '99999999-2222-4222-8222-222222222222', visit = '99999999-3333-4333-8333-333333333333';
const screenshots = new URL('../docs/desktop-forms-20260920/截图/', import.meta.url);
let server, browser, base;
before(async () => {await mkdir(screenshots, {recursive:true}); server = createSalesWebServer({target:''}); await new Promise(r => server.listen(0, '127.0.0.1', r)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel:'chrome', headless:true});});
after(async () => {await browser?.close(); await new Promise(r => server?.close(r));});
async function paint(p) {await p.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));}
async function fixture(run, size = [1366,768]) {
 const ctx = await browser.newContext({viewport:{width:size[0], height:size[1]}, serviceWorkers:'block'}), blocked=[], errors=[];
 await ctx.route('**/*', r => {const u=new URL(r.request().url()); if(u.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();} return r.continue();});
 const p=await ctx.newPage();p.setDefaultTimeout(9000);p.on('pageerror',e=>errors.push(e.message));
 try {await p.goto(base+'/?mode=preview'); await p.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
 await p.evaluate(({customer,opportunity,task,risk,visit})=>{
  const actor=SalesRuntime.app.globalData.session;
  const m=window.__extended={calls:[], failVisit:false,failReport:false,
   task:{id:task,title:'【合成】确认试点交付与验收清单',description:'核对客户试点范围，确认交付清单与责任人。\n保留交付证据并提交验收。',status:'pending_execution',creator_user_ref_id:'synthetic-other-creator',creator_name:'合成发起人',assignees:[{user_id:actor.userId,name:'合成执行人',responsibility:'owner'}],version_no:3,events:[],priority_code:'high',due_at:'2026-09-25T09:00:00Z',created_at:'2026-09-20T01:00:00Z',customer_name:'【合成】客户',customer_id:customer,opportunity_id:opportunity,opportunity_name:'【合成】试点商机',can_coordinate:true},
   risk:{id:risk,title:'【合成】验收时间待确认',customer_name:'【合成】客户',owner_name:'合成负责人',team_name:'合成团队',status:'open',severity_code:'high',description:'试点尚未取得客户书面验收确认，需要核实交付范围与验收时间。',evidence:['客户会议中提出补充交付清单。','双方约定下次会议确认验收。'],next_action:'在下次会议核对验收清单并确认时间。',opened_at:'2026-09-20T01:00:00Z'},
   visit:{id:visit,customer_id:customer,customer_name:'【合成】客户',opportunity_id:opportunity,opportunity_name:'【合成】试点商机',interaction_at:'2026-09-20T01:00:00Z',created_at:'2026-09-20T01:00:00Z',visit_goal:'确认试点验收安排',follow_up_record:'双方确认现有交付内容，讨论数据范围与验收负责人。',next_action:'2026年9月25日由负责人组织客户验收会议。',expectation_code:'met',contact_name_snapshot:'合成对接人',recorder_name:'合成跟进人',interaction_mode_code:'online_meeting',duration_minutes:45,is_first_visit:true,customer_main_business:'合成制造业务',customer_needs:'合成试点验证',customer_budget:'100万元',contact_role:'决策人',status:'archived',version_no:2},
   report:{id:'synthetic-report',status:'succeeded',created_at:'2026-09-20T01:00:00Z',result:{title:'【合成】个人即时总结',scope:'仅本人',period:'当前实时状态',attention_customers:[{title:'【合成】需跟进客户',detail:'客户试点验收时间尚待确认。\n请核对负责人和清单，及时同步最新记录。',ai_recommendation:{title:'安排验收沟通',detail:'确认验收日期，并将共识录入拜访记录。'}}]}}};
  const original=SalesRuntime.wx.request;
  SalesRuntime.wx.request=o=>{const path=new URL(o.url,location.origin).pathname, method=o.method||'GET';let data,statusCode=200;
   if(path.endsWith('/tasks/'+task)) data=m.task;
   else if(path.endsWith('/tasks/'+task+'/events')){const d=o.data;m.calls.push({path,method,data:structuredClone(d)});m.task={...m.task,version_no:m.task.version_no+1,status:d.event_type==='accept'?'pending_execution':d.event_type==='reject'?'cancelled':d.event_type==='approve_completion'?'completed':d.event_type==='reject_completion'?'in_progress':m.task.creator_user_ref_id===actor.userId?'completed':'pending_review',completion_note:d.note,last_event_type:d.event_type,completion_review_note:d.event_type==='reject_completion'?d.note:'',events:[...m.task.events,{id:String(m.task.version_no),event_type:d.event_type,actor_name:'合成账号',note:d.note,occurred_at:'2026-09-20T01:00:00Z'}]};data=m.task;}
   else if(path.endsWith('/risks/'+risk)) data=m.risk;
   else if(path.endsWith('/risks/'+risk+'/resolve')){m.calls.push({path,method,data:structuredClone(o.data)});m.risk={...m.risk,status:'resolved',resolution_note:o.data.resolution_note,resolved_by_name:'合成负责人',resolved_at:'2026-09-20T03:00:00Z'};data=m.risk;}
   else if(path.endsWith('/visits/'+visit)){if(m.failVisit){data={detail:'合成拜访读取失败'};statusCode=503;}else data=m.visit;}
   else if(path.endsWith('/agent/runs/synthetic-report')){if(m.failReport){data={detail:'合成报告读取失败'};statusCode=503;}else data=m.report;}
   else return original(o);
   const timer=setTimeout(()=>{const result={statusCode,data:structuredClone(data),header:{}};o.success?.(result);o.complete?.(result);},5);return {abort(){clearTimeout(timer);}};
  };
 },{customer,opportunity,task,risk,visit});
 await run(p);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
 } catch(e) {console.error('EXTENDED_DETAIL_DIAGNOSTIC',await p.evaluate(()=>({route:SalesRuntime.current.route,data:{loading:SalesRuntime.current.data.loading,error:SalesRuntime.current.data.error,flowStep:SalesRuntime.current.data.flowStep,blockReason:SalesRuntime.current.data.blockReason},boxes:[...document.querySelectorAll('#page-root,.web-extended-detail,.web-extended-grid,.web-extended-scroll,.web-op-columns,.web-op-main,.bottom-bar,.footer')].map(e=>{const r=e.getBoundingClientRect();return {class:e.className,x:r.x,y:r.y,w:r.width,h:r.height,client:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY};})})));throw e;}finally{await ctx.close();}
}
async function go(p,path){await p.evaluate(path=>SalesRuntime.wx.navigateTo({url:'/pages/'+path}),path);await paint(p);}
async function ready(p,type){if(type==='task') {await go(p,'task-detail/index?id='+task);await p.waitForFunction(()=>SalesRuntime.current.data.task&&!SalesRuntime.current.data.loading);}
 else if(type==='risk'){await go(p,'risk-detail/index?id='+risk);await p.waitForFunction(()=>SalesRuntime.current.data.risk&&!SalesRuntime.current.data.loading);}
 else if(type==='visit'){await go(p,`visit-detail/index?customer_id=${customer}&visit_id=${visit}`);await p.waitForFunction(()=>SalesRuntime.current.data.visit&&!SalesRuntime.current.data.loading);}
 else if(type==='report'){await go(p,'report-detail/index?runId=synthetic-report&action=report_personal_daily&section=attention_customers&row=0');await p.waitForFunction(()=>SalesRuntime.current.data.detail&&!SalesRuntime.current.data.loading);}
 else if(type==='opportunity'){await go(p,`customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}`);await p.waitForFunction(()=>SalesRuntime.current.data.opportunity&&!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.opportunityLoading);}
 else if(type==='assets'){await go(p,'customer-assets/index?customer_id='+customer);await p.waitForFunction(()=>SalesRuntime.current.data.summary&&!SalesRuntime.current.data.loading);}
 else if(type==='confirm'){await go(p,'visit-entry/index?customerId='+customer);await p.waitForFunction(()=>SalesRuntime.current.data.customerConfirmed);await p.locator('textarea.note-input').fill('沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送给客户。\n跟进日期：2026-09-20\n对接人：合成UI联系人');await p.locator('[data-handler=submitTranscript]').click();await p.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.core.length);}
 await paint(p);}
async function fitted(p){await paint(p);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'horizontal overflow');if(p.viewportSize().width>600){const r=await p.locator('#page-root').evaluate(e=>({client:e.clientHeight,scroll:e.scrollHeight}));assert.ok(r.scroll<=r.client+2,JSON.stringify(r));}}
async function visible(p,selector){const rs=await p.locator(selector).evaluateAll(els=>els.map(e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom};}));assert.ok(rs.length,selector);for(const r of rs)assert.ok(r.w>0&&r.h>0&&r.x>=0&&r.y>=0&&r.right<=p.viewportSize().width+1&&r.bottom<=p.viewportSize().height-(p.viewportSize().width>600&&p.viewportSize().width<=900?66:0)+2,selector+JSON.stringify(r));}
async function modal(p,confirm){await p.locator('.wx-modal-buttons button').nth(confirm?1:0).click();await paint(p);}
const pages=['task','risk','visit','report','opportunity','assets','confirm'];
for(const size of [[1366,768],[1024,600],[630,800],[390,844]]) test(`${size.join('x')} all six detail routes and both asset modes fit with visible object and original actions`,()=>fixture(async p=>{
 for(const type of pages){await ready(p,type);await fitted(p);const header={task:'.detail-hero',risk:'.detail-hero',visit:'.visit-hero',report:'.report-hero',opportunity:'.actual-heading',assets:'.actual-heading',confirm:'.hero'}[type];await visible(p,header);
  if(size[0]>600&&['task','risk','confirm'].includes(type))await visible(p,type==='confirm'?'.footer button':'.bottom-bar button');
  if(size[0]===1366){await p.screenshot({path:fileURLToPath(new URL((process.env.DETAIL_SCREENSHOT_PREFIX||'全页-详情-')+type+'-1366x768.png',screenshots))});console.log('DETAIL_FIT',type,await p.locator('#page-root,[data-web-page-scroll]').evaluateAll(els=>els.map(e=>({class:e.className,client:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY}))));}
 }
},size));

test('task original permission chain, required evidence, confirmation cancel and creator approval remain intact',()=>fixture(async p=>{
 await ready(p,'task');await p.locator('[data-handler=markCompleted]').click();assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('.completion-card textarea').fill('合成验收交付说明');await p.locator('[data-handler=markCompleted]').click();await modal(p,false);assert.equal(await p.evaluate(()=>__extended.calls.length),0);
 await p.locator('[data-handler=markCompleted]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='pending_review');assert.equal(await p.locator('[data-handler=reviewCompletion]').count(),0);
 await p.evaluate(()=>{__extended.task.creator_user_ref_id=SalesRuntime.app.globalData.session.userId;return SalesRuntime.current.loadTask();});await paint(p);await visible(p,'.bottom-bar button');
 await p.locator('[data-event=reject_completion]').click();assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('.completion-card textarea').fill('请补齐合成证据');await p.locator('[data-event=reject_completion]').click();await modal(p,false);assert.equal(await p.evaluate(()=>__extended.calls.length),1);
 await p.locator('[data-event=approve_completion]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='completed');assert.equal(await p.locator('.bottom-bar').count(),0);
 assert.deepEqual(await p.evaluate(()=>__extended.calls.map(c=>[c.data.event_type,c.data.version_no])),[['complete',3],['approve_completion',4]]);
}));

test('risk original 5-character gate, confirmation cancel and authorized resolution stay intact',()=>fixture(async p=>{
 await ready(p,'risk');await p.evaluate(()=>SalesRuntime.current.setData({canResolveRisk:false}));await paint(p);assert.equal(await p.locator('[data-handler=confirmResolve]').count(),0);
 await p.evaluate(()=>SalesRuntime.current.setData({canResolveRisk:true}));await paint(p);await p.locator('.resolution-card textarea').fill('太短');await p.locator('[data-handler=confirmResolve]').click();assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('.resolution-card textarea').fill('客户已确认合成验收清单');await p.locator('[data-handler=confirmResolve]').click();await modal(p,false);assert.equal(await p.evaluate(()=>__extended.calls.length),0);
 await p.locator('[data-handler=confirmResolve]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.risk?.status==='resolved');assert.equal(await p.locator('[data-handler=confirmResolve]').count(),0);assert.equal(await p.evaluate(()=>__extended.calls[0].data.resolution_note),'客户已确认合成验收清单');
}));

test('archived visit opens commercial facts read-only; report and visit failures keep native retry',()=>fixture(async p=>{
 await ready(p,'visit');assert.equal(await p.locator('textarea').count(),0);await p.locator('[data-handler=openOpportunity]').click();await p.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-assets/index'&&SalesRuntime.current.data.opportunity);await paint(p);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.readOnly),true);assert.equal(await p.locator('[data-handler=openForm],[data-handler=voidEntry]').count(),0);
 // readonly is the native actuals-only flag; commercial edit retains capability and ownership checks.
 assert.equal(await p.locator('[data-handler=webEditOpportunity]').count(),1);
 await p.evaluate(()=>{SalesRuntime.app.globalData.session.capabilities['opportunity.edit']=false;SalesRuntime.current.setData({});});await paint(p);assert.equal(await p.locator('[data-handler=webEditOpportunity]').count(),0);
 await p.evaluate(()=>SalesRuntime.current.webEditOpportunity());assert.equal(await p.evaluate(()=>SalesRuntime.current.route),'pages/customer-assets/index');
 await p.evaluate(()=>__extended.failVisit=true);await go(p,`visit-detail/index?customer_id=${customer}&visit_id=${visit}`);await p.locator('.state-card.error').waitFor();await p.evaluate(()=>__extended.failVisit=false);await p.locator('[data-handler=loadVisit]').click();await p.waitForFunction(()=>SalesRuntime.current.data.visit&&!SalesRuntime.current.data.loading);
 await p.evaluate(()=>__extended.failReport=true);await go(p,'report-detail/index?runId=synthetic-report&action=report_personal_daily&section=attention_customers&row=0');await p.locator('[data-handler=loadReport]').waitFor();await p.evaluate(()=>__extended.failReport=false);await p.locator('[data-handler=loadReport]').click();await p.waitForFunction(()=>SalesRuntime.current.data.detail&&!SalesRuntime.current.data.loading);assert.equal(await p.locator('#page-root button,#page-root textarea,#page-root input').count(),0);
}));

test('visit review retains analysis, field edit invalidation and human archive confirmation in compact frame',()=>fixture(async p=>{
 await ready(p,'confirm');await p.locator('[data-handler=review]').click();await p.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);await fitted(p);await visible(p,'.hero,.flow-steps,.footer');
 await p.locator('[data-handler=archive]').click();await modal(p,false);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.archived),false);
 await p.locator('[data-handler=backToEdit]').click();await p.waitForFunction(()=>SalesRuntime.current.data.flowStep==='edit');await p.locator('textarea[data-key=follow_up_record]').fill('修改后的合成沟通内容，需重新审核。');assert.equal(await p.evaluate(()=>SalesRuntime.current.data.canSubmit),false);
 await p.locator('[data-handler=review]').click();await p.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);await p.locator('[data-handler=archive]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.archived===true);await fitted(p);assert.equal(await p.locator('[data-handler=archive]').count(),0);assert.equal(await p.locator('[data-handler=openCustomer]').count(),1);
},[1024,600]));

for(const width of [1366,630]) test(`${width}px long detail text remains reachable in marked local host without moving headers`,()=>fixture(async p=>{
 await p.evaluate(()=>{__extended.task.description='合成完整任务依据。'.repeat(400);__extended.report.result.attention_customers[0].detail='合成完整经营报告。'.repeat(400);__extended.visit.follow_up_record='合成完整拜访沟通正文。'.repeat(400);});
 for(const type of ['task','visit','report']){await ready(p,type);await fitted(p);const metrics=await p.evaluate(()=>{const e=[...document.querySelectorAll('[data-web-page-scroll]')].find(e=>e.clientHeight>0&&/auto|scroll/.test(getComputedStyle(e).overflowY));e.scrollTop=e.scrollHeight;return {client:e.clientHeight,scroll:e.scrollHeight,top:e.scrollTop,root:document.querySelector('#page-root').scrollTop};});assert.ok(metrics.scroll>metrics.client*1.5,JSON.stringify(metrics));assert.ok(metrics.top>0);assert.equal(metrics.root,0);await visible(p,type==='report'?'.report-hero':type==='visit'?'.visit-hero':'.detail-hero');}
},[width,800]));

test('customer actuals dialog retains required source, currency, confirmation cancel and saved readback',()=>fixture(async p=>{
 await ready(p,'assets');await p.locator('[data-handler=openForm]').click();await p.waitForFunction(()=>SalesRuntime.current.data.formOpen&&!SalesRuntime.current.data.formTargetLoading);await paint(p);await visible(p,'.actual-form-header,.actual-form-actions button');
 await p.locator('.actual-form-submit').click();await p.waitForFunction(()=>!!SalesRuntime.current.data.formError);assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('input[data-field=amount]').fill('12.3');await p.locator('input[data-field=sourceRef]').fill('SYNTHETIC-DETAIL-REVIEW');await p.locator('.actual-form-submit').click();await modal(p,false);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.formOpen),true);assert.equal(await p.locator('input[data-field=amount]').inputValue(),'12.3');
 await p.locator('.actual-form-submit').click();await modal(p,true);await p.waitForFunction(()=>!SalesRuntime.current.data.formOpen&&!SalesRuntime.current.data.loading&&SalesRuntime.current.data.items.some(i=>i.source_ref==='SYNTHETIC-DETAIL-REVIEW'));
 const record=await p.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).actuals.find(i=>i.source_ref==='SYNTHETIC-DETAIL-REVIEW'));assert.equal(Number(record.amount),123000);assert.equal(record.customer_id,customer);assert.equal(record.confirmed,true);
},[1024,600]));
