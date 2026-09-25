import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
const artifacts=process.env.ARTIFACT_ROOT || '验收/1.0.9同步-20260925/业务';
before(async()=>{await mkdir(artifacts,{recursive:true});server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));});
async function fixture(role,fn,{width=1440,height=1000}={}){
 const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block'});await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 const errors=[],blocked=[];await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){blocked.push(u.href);return route.abort();}return route.continue();});
 const p=await context.newPage();p.setDefaultTimeout(10000);p.on('pageerror',e=>errors.push(e.message));
 try{await p.goto(base+'/?mode=preview#/pages/workbench/index');await p.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/workbench/index'&&SalesRuntime.app.globalData.session);
 await fn(p);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
 }catch(e){await p.screenshot({path:artifacts+'/failure-'+role+'-'+Date.now()+'.png',fullPage:true});console.error(JSON.stringify(await p.evaluate(()=>({route:window.SalesRuntime?.current?.route,data:window.SalesRuntime?.current?.data,errors:window.SalesRuntime?.errors}))).slice(0,10000));throw e;}finally{await context.close();}
}
async function go(p,path){await p.evaluate(path=>SalesRuntime.userRoute('/pages/'+path),path);await p.waitForFunction(path=>SalesRuntime.current?.route==='pages/'+path.split('?')[0],path);}
async function shot(p,name){await p.waitForTimeout(300);await p.screenshot({path:artifacts+'/'+name+'.png',animations:'disabled',fullPage:false});}
test('1.0.9 desktop multi-person task confirmation creates independent tasks with each person handled separately',{timeout:60000},()=>fixture('manager',async p=>{
 await go(p,'management-task-create/index');await p.waitForFunction(()=>SalesRuntime.current.data.members.length>1&&!SalesRuntime.current.data.recipientLoading);await p.getByRole('textbox',{name:'任务描述',exact:true}).fill('多人待办桌面回归：请独立核对验收清单');
 await p.getByRole('combobox',{name:'任务负责人',exact:true}).click();
 await p.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'王新源'}).click();
 await p.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'周思远'}).click();
 await p.getByRole('heading',{name:'执行设置'}).click();
 await p.waitForFunction(()=>SalesRuntime.current.data.selectedMembers.length===2);
 await p.getByText('将创建 2 条独立待办',{exact:true}).waitFor();await shot(p,'01-多人待办');
 await p.getByRole('button',{name:'下发任务',exact:true}).click();
 await p.getByText('确认创建2条待办？',{exact:true}).waitFor();
 await p.getByRole('button',{name:'确认下发',exact:true}).click();
 await p.waitForFunction(()=>SalesRuntime.wx.getStorageSync('lastManagementTaskCreated')?.ids?.length===2);
 const ids=await p.evaluate(()=>SalesRuntime.wx.getStorageSync('lastManagementTaskCreated').ids);assert.equal(new Set(ids).size,2);
 await go(p,'task-detail/index?id='+ids[0]);await p.waitForFunction(()=>!!SalesRuntime.current.data.task);
 assert.match(await p.locator('.ds-td-side').innerText(),/王新源/);await shot(p,'02-独立待办详情');
}));
test('1.0.9 claim filters and active-map pending scores render with separate full portfolio',{timeout:60000},()=>fixture('manager',async p=>{
 await go(p,'customer-claim/index');await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&SalesRuntime.current.data.industryOptions.length>1);await p.locator('.ds-sync-filters').waitFor();await shot(p,'03-客户认领筛选');
 await p.evaluate(()=>SalesRuntime.current.changeClaimStatus({detail:{value:SalesRuntime.current.data.statusOptions.findIndex(s=>s.value==='unclaimed')}}));await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&SalesRuntime.current.data.hasFilters);
 assert.ok(await p.evaluate(()=>SalesRuntime.current.data.customers.every(c=>c.can_claim)));await p.getByRole('button',{name:'清除筛选',exact:true}).click();await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.hasFilters);
 await go(p,'customers/index');await p.waitForFunction(()=>!SalesRuntime.current.data.mapLoading&&!SalesRuntime.current.data.assetLoading);
 assert.equal(await p.evaluate(()=>Boolean(SalesRuntime.current.data.mapError||SalesRuntime.current.data.acvError)),false);
 const facts=await p.evaluate(()=>({all:SalesRuntime.current.data.scopeCustomerCount,mapped:SalesRuntime.current.data.customers.length,pending:SalesRuntime.current.data.pendingCustomers.length}));assert.ok(facts.pending>0);assert.ok(facts.all>facts.mapped+facts.pending);
 await p.getByRole('button',{name:/^待评估 \d+ 家$/}).click();await p.getByText('未取得完整评分，不绘制地图点',{exact:false}).waitFor();await shot(p,'04-活跃地图与待评估');
}));
test('1.0.9 team per-capita ranking opens members and keeps no-member team unranked',{timeout:60000},()=>fixture('manager',async p=>{
 await go(p,'bi/index');await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.rankingLoading);
 const d=await p.evaluate(()=>SalesRuntime.current.data);assert.equal(d.rankingMessage,'');assert.ok(d.rankingCards.some(c=>c.title==='团队人均跟进排名'));await p.getByRole('button',{name:/南区 · 成员明细/}).click();
 await p.getByRole('dialog').waitFor();assert.match(await p.getByRole('dialog').innerText(),/包含零跟进成员/);await shot(p,'05-人均跟进成员明细');
}));
for(const role of ['sales','supervisor','manager','fde','fde_lead'])test('1.0.9 '+role+' main pages remain usable',{timeout:60000},()=>fixture(role,async p=>{
 for(const route of ['index','customers','workbench','tasks','bi','profile','weekly-report']){
   await go(p,route+'/index');await p.locator('[data-department-page="pages/'+route+'/index"]').waitFor();
   await p.waitForTimeout(180);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.accessBlocked===true),false,role+':'+route);
 }
 await shot(p,'06-'+role+'-周报');
}));
