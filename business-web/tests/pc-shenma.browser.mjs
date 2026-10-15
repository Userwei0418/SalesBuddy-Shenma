/** Built PC UI against synthetic local HTTP. Real RLS/DB tests are separate. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
import {todayKey} from '../department-ui/pc/period.js';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const options=JSON.parse(await readFile(new URL('../../frontend/tests/helpers/business-options.json',import.meta.url),'utf8'));
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server.close(r));});
const today=todayKey(), permissions=Object.fromEntries(['access.mini_program','overview.read','opportunity.create','visit.read','visit.create','directory.read','weekly_report.read','access.console'].map(k=>[k,true]));
const actor={workspace_id:'pc-ws',user_id:'pc-user',account_code:'pcuser',display_name:'神码合成主管',role:'supervisor',team_ids:['pc-team'],team_names:['合成团队'],capabilities:{},permissions,permission_grants:[{permission_code:'visit.read',effect:'allow',scope_code:'teams'}],permission_version:'pc1'};
const raw={id:'pc-visit',customer_id:'pc-customer',customer_name:'合成客户',recorder_id:'pc-user',recorder_name:'合成主管',recording_team_id:'pc-team',recording_role_code_snapshot:'supervisor',visit_date:today,interaction_at:`${today}T10:00:00+08:00`,created_at:`${today}T02:00:00Z`,follow_up_record:'已确认试点范围，进入方案验证',next_action:'下周完成方案评审',status:'archived',is_summary:true,can_read_detail:true,can_open_customer:false,linked_opportunities:[{id:'pc-opportunity',name:'合成试点项目',can_open:false}]};
for(const width of [1440,390])test(`new PC frame/workspace/follow-ups at ${width}`,{timeout:90000},async()=>{
 const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),page=await context.newPage();
 const calls=[],unexpected=[],errors=[];let deny=false,failNext=false;
 page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());if(u.origin!==base){unexpected.push(u.origin);return route.abort();}
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  calls.push({path:u.pathname,query:u.search,method:req.method()});
  if(u.pathname==='/api/v1/auth/password/login')return route.fulfill({json:{access_token:'synthetic-pc',refresh_token:'synthetic-pc-refresh',actor,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:actor});
  if(u.pathname==='/api/v1/metadata/business-options')return route.fulfill({json:options});
  if(u.pathname==='/api/v1/directory/members')return route.fulfill({json:{teams:[{id:'pc-team',name:'合成团队'}],items:[{id:'pc-user',name:'合成主管',team_id:'pc-team',role:'supervisor'}]}});
  if(u.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(u.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  if(['/api/v1/notifications','/api/v1/tasks','/api/v1/customers','/api/v1/directory/colleagues'].includes(u.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
  if(u.pathname==='/api/v1/visits/range'){
   if(deny)return route.fulfill({status:403,json:{detail:'权限已调整'}});
   if(failNext){failNext=false;return route.fulfill({status:503,json:{detail:'服务暂不可用，请重试'}});}
   const rows=u.searchParams.get('q')==='无匹配'?[]:[raw];return route.fulfill({json:{items:rows,summary:{total:rows.length,subject_count:rows.length,scored_count:0},has_more:false,next_offset:null,as_of:new Date().toISOString()}});
  }
  if(u.pathname==='/api/v1/visits/pc-visit')return route.fulfill({json:{...raw,is_summary:false,follow_up_record:'完整沟通正文：已确认试点范围，待完成方案验证。'}});
  if(u.pathname==='/api/v1/web/weekly-reports')return route.fulfill({json:{items:[],has_more:false,current_week:today}});
  unexpected.push(u.pathname);return route.fulfill({status:503,json:{detail:'Unexpected synthetic request'}});
 });
 try{
  await page.goto(base+'/?mode=live');await page.getByPlaceholder('请输入账号名或手机号').fill('pcuser');await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.waitForFunction(()=>SalesRuntime.current.data.password==='synthetic-only');await page.getByRole('checkbox',{name:'我已阅读并同意',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.agreed);await page.locator('[data-handler="submitLogin"]').click();
  await page.getByRole('heading',{name:'工作台',exact:true}).waitFor();await page.getByText('已确认试点范围，进入方案验证',{exact:true}).waitFor();
  assert(await page.evaluate(()=>document.body.classList.contains('pc-frame')));
  assert.equal(await page.getByRole('button',{name:'新增商机',exact:true}).count(),0, 'Shenma keeps opportunity creation out of global quick actions');
  if(width>900){await page.getByRole('button',{name:'收起导航',exact:true}).click();await page.getByRole('button',{name:'展开导航',exact:true}).waitFor();await page.getByRole('button',{name:'展开导航',exact:true}).click();}
  await mkdir('/tmp/shenma-pc-browser',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-browser/workspace-${width}.png`,fullPage:true,animations:'disabled'});
  await page.getByRole('button',{name:'查看全部跟进记录 →',exact:true}).click();await page.getByRole('heading',{name:'跟进记录',exact:true}).waitFor();
  await page.getByRole('button',{name:'详情',exact:true}).first().click();await page.getByText('完整沟通正文：已确认试点范围，待完成方案验证。',{exact:true}).waitFor();
  await page.locator('.ant-drawer-close').click();await page.locator('.ant-drawer-open').waitFor({state:'hidden'});
  const search=page.getByPlaceholder('搜索客户、商机、人员或完整内容');await search.fill('无匹配');await search.press('Enter');await page.getByText('没有匹配的跟进记录',{exact:true}).waitFor();
  assert(calls.some(c=>c.path.endsWith('/visits/range')&&c.query.includes('q=')));
  await page.getByRole('button',{name:'清除条件',exact:true}).click();await page.getByText('已确认试点范围，进入方案验证',{exact:true}).waitFor();
  await page.getByText('表格',{exact:true}).click();await page.getByRole('columnheader',{name:'沟通内容',exact:false}).first().waitFor();
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:`/tmp/shenma-pc-browser/records-${width}.png`,fullPage:true,animations:'disabled'});
  deny=true;await page.getByRole('button',{name:'刷新跟进记录',exact:true}).click();await page.getByText('权限已调整',{exact:true}).waitFor();assert.equal(await page.getByText('已确认试点范围，进入方案验证',{exact:true}).count(),0);
  deny=false;await page.getByRole('button',{name:/重\s*试/}).last().click();await page.getByText('已确认试点范围，进入方案验证',{exact:true}).waitFor();
  await page.evaluate(()=>{SalesRuntime.app.globalData.session={...SalesRuntime.app.globalData.session,userId:'pc-other',permissionVersion:'pc-denied',permissions:{'access.mini_program':true,'overview.read':true}};SalesRuntime.current.setData({});});
  await page.getByText('当前身份没有跟进记录读取权限',{exact:true}).waitFor();assert.equal(await page.getByText('已确认试点范围，进入方案验证',{exact:true}).count(),0);
  assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
 }catch(e){await mkdir('/tmp/shenma-pc-browser',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-browser/failure-${width}.png`,fullPage:true,animations:'disabled'});await writeFile(`/tmp/shenma-pc-browser/failure-${width}.json`,JSON.stringify({errors,unexpected,calls,body:await page.locator('body').innerText()},null,2));throw e;}
 finally{await context.close();}
});
