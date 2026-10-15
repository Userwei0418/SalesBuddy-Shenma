/** Fixed e6099547 PC frame on Shenma contracts, synthetic network only. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const options=JSON.parse(await readFile(new URL('../../frontend/tests/helpers/business-options.json',import.meta.url),'utf8'));
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server.close(r));});
for(const [role,width] of [['manager',1440],['sales',1440],['sales',390]])test(`PC frame ${role} ${width}px retains supported navigation and permissions`,{timeout:45000},async()=>{
 const allowed=['access.mini_program','overview.read','opportunity.read',...(role==='manager'?['access.console','visit.read','visit.create','task.create','weekly_report.read']:[])];
 const actor={workspace_id:'frame-ws',user_id:'frame-'+role,account_code:role,display_name:'合成'+role,role,team_ids:[],team_names:['合成团队'],capabilities:{},permissions:Object.fromEntries(allowed.map(p=>[p,true])),permission_grants:[],permission_version:'frame'};
 const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[],unexpected=[];page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const u=new URL(route.request().url());if(u.origin!==base){unexpected.push(u.origin);return route.abort();}
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  if(u.pathname==='/api/v1/auth/password/login')return route.fulfill({json:{access_token:'frame-synthetic',refresh_token:'frame-refresh',actor,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:actor});
  if(u.pathname==='/api/v1/metadata/business-options')return route.fulfill({json:options});
  if(u.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(u.pathname==='/api/v1/visits/range')return route.fulfill({json:{items:[],summary:{total:0},has_more:false,next_offset:null}});
  if(u.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  if(['/api/v1/notifications','/api/v1/tasks','/api/v1/customers'].includes(u.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
  if(u.pathname==='/api/v1/opportunities/overview')return route.fulfill({json:{metrics:{won:0,total:0,active:0,newCount:0,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}});
  if(u.pathname==='/api/v1/opportunities')return route.fulfill({json:{items:[],summary:{total:0},has_more:false,next_offset:null,team_options:[],facets:{owner_options:[],years:[]}}});
  if(u.pathname==='/api/v1/opportunity-views/options')return route.fulfill({json:{columns:[{value:'name',label:'商机 / 客户'}],can_create_private:true}});
  if(u.pathname==='/api/v1/opportunity-views')return route.fulfill({json:{items:[],total:0,limit:100,offset:0}});
  unexpected.push(u.pathname);return route.fulfill({status:503,json:{detail:'Unexpected synthetic request'}});
 });
 try{
  await page.goto(base+'/?mode=live');await page.getByPlaceholder('请输入账号名或手机号').fill(role);await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');await page.waitForFunction(()=>SalesRuntime.current.data.password==='synthetic-only');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.getByRole('checkbox',{name:'我已阅读并同意',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.agreed);await page.locator('[data-handler="submitLogin"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  if(width>900){
   await page.locator('.pc-nav').waitFor();assert.equal(await page.locator('.pc-nav-menu').getByText('知识库',{exact:true}).count(),0);assert.equal(await page.locator('.pc-nav-menu').getByText('工作报告',{exact:true}).count(),0);
   assert.equal(await page.locator('.pc-nav-menu').getByText('周报',{exact:true}).count(),role==='manager'?1:0);assert.equal(await page.locator('.pc-nav-menu').getByText('跟进记录',{exact:true}).count(),role==='manager'?1:0);
   assert.equal(await page.locator('.pc-nav-admin').count(),role==='manager'?1:0);if(role==='manager')assert.equal(await page.locator('.pc-nav-admin').getAttribute('href'),'/admin');
   const openedBefore=await page.locator('.pc-nav-menu .ant-menu-submenu-open').count();
   await page.getByRole('button',{name:'收起导航',exact:true}).click();assert.equal(await page.evaluate(()=>document.getElementById('frame-sidenav').getBoundingClientRect().width),64);
   await page.getByRole('button',{name:'展开导航',exact:true}).click();assert.equal(await page.evaluate(()=>document.getElementById('frame-sidenav').getBoundingClientRect().width),224);
   assert.equal(await page.locator('.pc-nav-menu .ant-menu-submenu-open').count(),openedBefore);
   // Rapid toggles must restore labels in the same rendered state, without
   // waiting for Ant Menu's former width/opacity transition to finish.
   assert.deepEqual(await page.locator('.pc-nav-menu > .ant-menu').evaluate(menu=>({collapsed:menu.classList.contains('ant-menu-inline-collapsed'),width:Math.round(menu.getBoundingClientRect().width),labelOpacity:getComputedStyle(menu.querySelector('.ant-menu-title-content')).opacity})),{collapsed:false,width:223,labelOpacity:'1'});
   for(let repeat=0;repeat<2;repeat++){
    await page.getByRole('button',{name:'收起导航',exact:true}).click();await page.getByRole('button',{name:'展开导航',exact:true}).click();
    assert.deepEqual(await page.locator('.pc-nav-menu > .ant-menu').evaluate(menu=>({collapsed:menu.classList.contains('ant-menu-inline-collapsed'),width:Math.round(menu.getBoundingClientRect().width),labelOpacity:getComputedStyle(menu.querySelector('.ant-menu-title-content')).opacity})),{collapsed:false,width:223,labelOpacity:'1'});
   }
   await page.locator('.pc-nav-menu').getByText('商机',{exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/workbench/index');await page.locator('.pc-topbar .ant-breadcrumb').getByText('商机',{exact:true}).waitFor();
   assert.equal(await page.locator('.pc-topbar-create').count(),role==='manager'?1:0);
   if(role==='manager'){await page.locator('.pc-topbar-create').click();await page.getByRole('menuitem',{name:'记录客户拜访',exact:true}).waitFor();assert.equal(await page.getByRole('menuitem',{name:'建档并下发',exact:true}).count(),0);await page.keyboard.press('Escape');await page.locator('.pc-topbar .ant-breadcrumb').click();await page.locator('.ant-dropdown:visible').waitFor({state:'hidden'});}
   await page.locator('.pc-nav-account').click();await page.locator('#account-dialog').waitFor({state:'visible'});assert.equal(await page.locator('#account-dialog-name').innerText(),'合成'+role);await page.locator('#close-account').click();
  }else{
   await page.locator('#mobile-nav').waitFor();assert.equal(await page.locator('#frame-sidenav').isVisible(),false);
   assert.equal(await page.locator('#mobile-account-button').isVisible(),false);
   await page.getByRole('button',{name:'账号与登录设置',exact:true}).click();await page.locator('#account-dialog').waitFor({state:'visible'});assert.equal(await page.locator('#account-dialog-name').innerText(),'合成'+role);await page.locator('#close-account').click();
   await page.locator('#mobile-nav [data-path="pages/workbench/index"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/workbench/index');
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--ui-primary').trim()),'#2863CD');
  await page.waitForFunction(()=>!SalesRuntime.current.data.loading);await page.locator('.ant-spin-spinning').waitFor({state:'hidden'});await page.evaluate(()=>{window.scrollTo(0,0);return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});
  await mkdir('/tmp/shenma-pc-foundation',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-foundation/${role}-${width}.png`,fullPage:true,animations:'disabled'});assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
 }catch(e){await mkdir('/tmp/shenma-pc-foundation',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-foundation/failure-${role}-${width}.png`,fullPage:true});await writeFile(`/tmp/shenma-pc-foundation/failure-${role}-${width}.json`,JSON.stringify({errors,unexpected,body:await page.locator('body').innerText()},null,2));throw e;}finally{await context.close();}
});
