/** Record detail column geometry and native actions; isolated synthetic fixtures. */
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

const records = {
 task:{hero:'.detail-hero',main:'.web-record-detail .web-extended-scroll',left:'.web-record-detail .web-extended-aside'},
 risk:{hero:'.detail-hero',main:'.web-record-detail .web-extended-scroll',left:'.web-record-detail .web-extended-aside'},
 visit:{hero:'.visit-hero',main:'.web-record-detail .web-extended-scroll',left:'.web-record-detail .web-extended-aside'},
 report:{hero:'.report-hero',main:'.web-record-report-body',left:'.web-record-detail .source-card'}
};
for(const size of [[1366,768],[1180,650],[1600,900]]) for(const type of Object.keys(records)) {
 test(`${size.join('x')} ${type} keeps object context left and primary content right`,()=>fixture(async p=>{
  await ready(p,type);await fitted(p);const s=records[type];
  const hero=await p.locator(s.hero).boundingBox(),main=await p.locator(s.main).boundingBox(),left=await p.locator(s.left).boundingBox();
  assert.ok(hero.x+hero.width<main.x&&left.x+left.width<=main.x,JSON.stringify({hero,main,left}));
  assert.ok(Math.abs(hero.y-main.y)<=2,JSON.stringify({hero,main}));
  assert.ok(main.width>=400,JSON.stringify(main));
  await visible(p,s.hero);if(['task','risk'].includes(type))await visible(p,'.bottom-bar button');
  const primary=await p.evaluate(()=>[...document.querySelectorAll('[data-web-page-scroll]')].filter(e=>e.clientHeight>0&&/auto|scroll/.test(getComputedStyle(e).overflowY)).map(e=>e.className));
  assert.equal(primary.length,1,JSON.stringify(primary));
  if(size[0]===1366)await p.screenshot({path:fileURLToPath(new URL('详情两栏-'+type+'-1366x768.png',screenshots))});
 },size));
}
for(const size of [[1024,600],[630,800],[390,844]])test(`${size.join('x')} record detail narrow layouts preserve field and action access`,()=>fixture(async p=>{
 for(const type of Object.keys(records)){
  await ready(p,type);await fitted(p);await visible(p,records[type].hero);
  if(size[0]>600&&['task','risk'].includes(type))await visible(p,'.bottom-bar button');
  assert.ok((await p.locator(records[type].main).textContent()).trim());
 }
},size));
test('long record bodies scroll once without moving the object header or hiding native actions',()=>fixture(async p=>{
 await p.evaluate(()=>{__extended.task.description='合成长任务要求与验收依据。'.repeat(400);__extended.visit.follow_up_record='合成归档拜访长正文。'.repeat(400);__extended.risk.evidence=Array.from({length:60},(_,i)=>'合成风险判断依据'+i);__extended.report.result.attention_customers[0].detail='合成长经营报告正文。'.repeat(400);});
 for(const type of Object.keys(records)){
  await ready(p,type);await fitted(p);const s=records[type];const before=await p.locator(s.hero).boundingBox();
  const scroll=await p.locator(s.main).evaluate(e=>{e.scrollTop=e.scrollHeight;return {top:e.scrollTop,client:e.clientHeight,scroll:e.scrollHeight};});
  assert.ok(scroll.top>0&&scroll.scroll>scroll.client,JSON.stringify(scroll));await paint(p);const after=await p.locator(s.hero).boundingBox();
  assert.equal(after.y,before.y);assert.equal(after.x,before.x);await visible(p,s.hero);
  if(['task','risk'].includes(type))await visible(p,'.bottom-bar button');
 }
}));
test('task completion and risk resolution keep their original permissions and confirmation guards',()=>fixture(async p=>{
 await ready(p,'task');await p.locator('[data-handler=markCompleted]').click();assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('.completion-card textarea').fill('合成两栏任务完整完成说明');await p.locator('[data-handler=markCompleted]').click();await modal(p,false);assert.equal(await p.evaluate(()=>__extended.calls.length),0);
 await p.locator('[data-handler=markCompleted]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='pending_review');assert.equal(await p.locator('[data-handler=reviewCompletion]').count(),0);
 await ready(p,'risk');await p.evaluate(()=>SalesRuntime.current.setData({canResolveRisk:false}));await paint(p);assert.equal(await p.locator('[data-handler=confirmResolve]').count(),0);
 await p.evaluate(()=>SalesRuntime.current.setData({canResolveRisk:true}));await paint(p);await p.locator('.resolution-card textarea').fill('短');await p.locator('[data-handler=confirmResolve]').click();assert.equal(await p.locator('.wx-modal-mask').count(),0);
 await p.locator('.resolution-card textarea').fill('合成客户已确认所有风险解除依据');await p.locator('[data-handler=confirmResolve]').click();await modal(p,true);await p.waitForFunction(()=>SalesRuntime.current.data.risk?.status==='resolved');assert.equal(await p.locator('[data-handler=confirmResolve]').count(),0);assert.equal(await p.locator('.resolution-card').count(),0);assert.equal(await p.locator('.resolved-card').count(),1);
}));
