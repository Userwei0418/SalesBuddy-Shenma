/** Browser lifecycle against a synthetic API only; no customer or internal network calls. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server.close(r));});
const week='2026-09-21',previous='2026-09-14';
const permissions=Object.fromEntries(['access.mini_program','visit.read','overview.read','task.read','weekly_report.read','weekly_report.generate','weekly_report.edit','weekly_report.cancel'].map(k=>[k,true]));
const actor={workspace_id:'test-ws',user_id:'test-a',account_code:'XS001',display_name:'合成测试销售',role:'sales',team_ids:['test-team'],team_names:['测试团队'],capabilities:{'visit.create':true},permissions,permission_grants:[],permission_version:'1'};
const row={id:'source-a',customer_id:'customer-a',customer_name:'合成客户',recorder_id:'test-a',opportunity_id:null,opportunity_name:null,created_at:'2026-09-24T02:00:00Z',visit_date:'2026-09-20',follow_up_record:'完整沟通内容。'.repeat(100)+'尾部事实不可丢失',next_action:'下周核对方案',version_no:1,status:'confirmed'};
const period=w=>({start_date:w===week?'2026-09-11':'2026-09-07',end_date:w===week?'2026-09-24':'2026-09-20',timezone:'Asia/Shanghai',date_basis:'created_at'});
const sources=w=>({report_week:w,report_week_end:w===week?'2026-09-27':'2026-09-20',period:period(w),source_cutoff_at:'2026-09-24T03:00:00Z',snapshot_at:'2026-09-24T03:00:00Z',items:[row],total:1,has_more:false,next_offset:null,statistics:{record_count:1,customer_count:1,opportunity_count:0}});
async function fixture(run,{width=1440}={}){
 const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'}),page=await context.newPage();
 const reports=[],calls=[],errors=[],unexpected=[];let conflict=false,hold=false,loseNext=false;
 page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());
  if(u.origin!==base){unexpected.push(u.origin);return route.abort();}
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  calls.push({path:u.pathname,method:req.method(),body:req.postDataJSON(),authorization:req.headers().authorization});
  if(u.pathname==='/api/v1/auth/password/login')return route.fulfill({json:{access_token:'synthetic-a',refresh_token:'synthetic-refresh',actor,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:actor});
  if(u.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(u.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  const root='/api/v1/web/weekly-reports';
  if(u.pathname===root+'/sources')return route.fulfill({json:sources(u.searchParams.get('report_week')||week)});
  if(u.pathname===root){
   if(req.method()==='GET'){const w=u.searchParams.get('report_week');return route.fulfill({json:{current_week:week,items:reports.filter(r=>!w||r.report_week===w),has_more:false}});}
   const data=req.postDataJSON();let report=reports.find(r=>r.request_id===data.request_id);
   if(!report){report={id:'report-'+(reports.length+1),request_id:data.request_id,report_week:data.report_week,period:period(data.report_week),status:'queued',result_status:null,draft_version:0,body_markdown:null,title:'合成销售周报',created_at:new Date().toISOString()};reports.unshift(report);}
   if(loseNext){loseNext=false;return route.fulfill({status:503,json:{detail:'Synthetic response lost after commit'}});}return route.fulfill({status:202,json:report});
  }
  if(u.pathname.startsWith(root+'/')){
   const [id,action]=u.pathname.slice(root.length+1).split('/'),r=reports.find(x=>x.id===id);
   if(!r)return route.fulfill({status:404,json:{detail:'WEEKLY_REPORT_NOT_FOUND'}});
   if(action==='sources')return route.fulfill({json:sources(r.report_week)});
   if(action==='cancel'){r.status='cancelled';return route.fulfill({json:r});}
   if(action==='draft'){
    if(conflict){conflict=false;r.draft_version++;r.body_markdown='另一页面已保存的正文';return route.fulfill({status:409,json:{detail:'WEEKLY_DRAFT_VERSION_CONFLICT'}});}
    assert.equal(req.postDataJSON().expected_version,r.draft_version);r.body_markdown=req.postDataJSON().body_markdown;r.draft_version++;r.draft_source='manual';return route.fulfill({json:r});
   }
   if(!hold&&r.status==='queued'){r.status='succeeded';r.result_status='ready';r.body_markdown='## 合成客户\n完整事实与后续计划。';r.draft_version=1;r.draft_source='agent';}
   return route.fulfill({json:r});
  }
  if(['/api/v1/notifications','/api/v1/tasks','/api/v1/customers','/api/v1/directory/colleagues'].includes(u.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
  unexpected.push(u.pathname);return route.fulfill({status:503,json:{detail:'Synthetic unexpected request'}});
 });
 try{
  await page.goto(base+'/?mode=live');await page.getByPlaceholder('请输入账号名或手机号').fill('XS001');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');await page.waitForFunction(()=>SalesRuntime.current.data.password==='synthetic-only');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.getByRole('checkbox',{name:'我已阅读并同意',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.agreed===true&&document.querySelector('.ds-login-agree input').checked);await page.locator('[data-handler="submitLogin"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  await page.evaluate(()=>SalesRuntime.userRoute('/pages/weekly-report/index'));
  await page.getByRole('button',{name:'生成周报',exact:true}).waitFor();await page.waitForFunction(()=>!document.querySelector('.ds-weekly-generate button').disabled);
  await run({page,reports,calls,setConflict:()=>{conflict=true;},setHold:v=>{hold=v;},loseNext:()=>{loseNext=true;}});
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  assert(calls.some(c=>c.path==='/api/v1/auth/password/login'));
  assert(!calls.some(c=>c.path.startsWith('/api/v1/web/auth')));
  assert(calls.filter(c=>c.path.startsWith('/api/v1/web/weekly')).every(c=>c.authorization==='Bearer synthetic-a'));
 }catch(e){await mkdir('/tmp/shenma-web-browser',{recursive:true});await page.screenshot({path:'/tmp/shenma-web-browser/failure.png',fullPage:true});await writeFile('/tmp/shenma-web-browser/failure.json',JSON.stringify({body:await page.locator('body').innerText(),errors,calls:calls.map(({path,method})=>({path,method})),storage:await page.evaluate(()=>Object.keys(sessionStorage)),aria:await page.locator('body').ariaSnapshot()},null,2));throw e;}
 finally{await context.close();}
}
test('shared login, server draft, reload, manual conflict and previous week',{timeout:45000},()=>fixture(async({page,reports,setConflict})=>{
 assert.equal(await page.locator('.ds-weekly-record').count(),1);
 await page.getByText('查看完整记录',{exact:true}).click();await page.getByText(/尾部事实不可丢失/).last().waitFor();
 await page.getByRole('button',{name:'生成周报',exact:true}).click();const editor=page.getByLabel('周报正文',{exact:true});await editor.waitFor();
 await editor.fill('人工核对后的周报');await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.getByText('已保存到服务器',{exact:true}).waitFor();assert.equal(reports[0].body_markdown,'人工核对后的周报');
 await page.reload();await editor.waitFor();assert.equal(await editor.inputValue(),'人工核对后的周报');
 setConflict();await editor.fill('本页需要保留的修改');await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.getByRole('button',{name:'核对服务器最新版本'}).waitFor();assert.equal(await editor.inputValue(),'本页需要保留的修改');
 assert(await page.getByRole('button',{name:'保存修改',exact:true}).isDisabled());
 await page.getByRole('button',{name:'核对服务器最新版本'}).click();await page.getByLabel('服务器最新正文').waitFor();await page.getByRole('button',{name:'已核对，继续编辑本地内容'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.getByRole('button',{name:'保存修改',exact:true}).click();await page.getByText('已保存到服务器',{exact:true}).waitFor();assert.equal(reports[0].body_markdown,'本页需要保留的修改');
 await page.getByRole('combobox',{name:'周报周期',exact:true}).click();await page.getByText('上一个自然周',{exact:true}).last().click();await page.getByRole('button',{name:'生成周报',exact:true}).click();await editor.waitFor();assert.equal(reports[0].report_week,previous);
 await mkdir('/tmp/shenma-web-browser',{recursive:true});await page.getByRole('heading',{name:'周报',exact:true}).click();await page.screenshot({path:'/tmp/shenma-web-browser/weekly-live.png',fullPage:true});
}));
test('unknown response retries same generation id and cancellation retains history',{timeout:45000},()=>fixture(async({page,reports,calls,setHold,loseNext})=>{
 setHold(true);loseNext();await page.getByRole('button',{name:'生成周报',exact:true}).click();await page.getByRole('button',{name:'重试生成请求',exact:true}).click();await page.getByRole('button',{name:'取消本次生成',exact:true}).click();await page.getByText('本次生成已取消',{exact:true}).waitFor();
 const posts=calls.filter(c=>c.method==='POST'&&c.path==='/api/v1/web/weekly-reports');assert.equal(posts.length,2);assert.equal(posts[0].body.request_id,posts[1].body.request_id);assert.equal(reports.length,1);assert.equal(reports[0].status,'cancelled');
}));
for(const width of [1024,390])test(`live weekly fits ${width}px`,{timeout:30000},()=>fixture(async({page})=>{
 await page.getByRole('button',{name:'生成周报',exact:true}).click();await page.getByLabel('周报正文').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
},{width}));
