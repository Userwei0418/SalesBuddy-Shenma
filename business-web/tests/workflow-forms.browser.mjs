/** Real shared creation handlers, synthetic data only; all business network blocked. */
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const customer='00000010-0000-4000-8000-000000000001';
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function fixture(run,size=[1440,900]){
 const context=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),errors=[],blocked=[];
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const page=await context.newPage();page.setDefaultTimeout(9000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}catch(error){console.error('WORKFLOW_DIAGNOSTIC',await page.evaluate(()=>({route:SalesRuntime.current.route,error:SalesRuntime.current.data.error,style:[...document.querySelectorAll('.web-workflow,.web-workflow-scroll,.web-workflow-panel,.footer,.bottom-bar')].map(e=>{let r=e.getBoundingClientRect();return {class:e.className,y:r.y,h:r.height,w:r.width,scroll:e.scrollHeight,client:e.clientHeight}})})));throw error;}finally{await context.close();}
}
async function go(page,path){await page.evaluate(path=>SalesRuntime.wx.navigateTo({url:'/pages/'+path}),path);await paint(page);}
async function visible(page,selector){const boxes=await page.locator(selector).evaluateAll(els=>els.map(e=>{let r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,iw:innerWidth,ih:innerHeight}}));assert.ok(boxes.length,selector);for(const r of boxes)assert.ok(r.w>0&&r.h>0&&r.x>=0&&r.y>=0&&r.x+r.w<=r.iw+1&&r.y+r.h<=r.ih+1,selector+' '+JSON.stringify(r));}
async function fitted(page){await paint(page);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);const d=await page.locator('#page-root').evaluate(e=>({client:e.clientHeight,scroll:e.scrollHeight}));assert.ok(d.scroll<=d.client+2,JSON.stringify(d));}
async function select(page,locator,value){await locator.click();await page.locator('#web-select-dialog').waitFor();await page.locator(`#web-select-dialog .option[data-value="${value}"]`).click();await page.locator('#web-select-dialog').waitFor({state:'detached'});}
async function shot(page,name){if(!process.env.WORKFLOW_SHOTS)return;await mkdir(process.env.WORKFLOW_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.WORKFLOW_SHOTS}/${name}.png`});}

test('desktop task form exposes recipient/date/action and preserves daily task confirmation and readback',()=>fixture(async page=>{
 await go(page,'management-task-create/index');await page.waitForFunction(()=>SalesRuntime.current.data.members.length>0);await fitted(page);await visible(page,'.hero-title,.description-card textarea,.assignee-picker,.custom-due-fields,.bottom-bar button');
 const positions=await page.evaluate(()=>({left:document.querySelector('.web-task-content').getBoundingClientRect().x,right:document.querySelector('.web-task-settings').getBoundingClientRect().x}));assert.ok(positions.right>positions.left);
 await page.locator('.description-card textarea').fill('桌面表单合成任务：核对清单并交付确认');await select(page,page.locator('.assignee-picker select'),'0');
 await page.locator('button[data-date-mode="date"]').click();await page.locator('#web-date-dialog').getByRole('button',{name:'明天',exact:true}).click();await shot(page,'创建任务-1440');
 await page.locator('[data-handler=submitTask]').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'取消',exact:true}).click();assert.equal(await page.locator('.description-card textarea').inputValue(),'桌面表单合成任务：核对清单并交付确认');
 await page.locator('[data-handler=submitTask]').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认下发',exact:true}).click();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('sales-web:preview:v1:lastManagementTaskCreated')||'null')?.id);
 const id=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('sales-web:preview:v1:lastManagementTaskCreated')).id);
 await go(page,'task-detail/index?id='+id);await page.waitForFunction(()=>!!SalesRuntime.current.data.task);
 const t=await page.evaluate(()=>SalesRuntime.current.data.task);assert.equal(t.association_kind,'daily');assert.ok(!t.customer_id&&!t.opportunity_id);assert.match(JSON.stringify(t),/桌面表单合成任务/);
}));

test('opportunity search and grouped form retain stage forecast rules, FDE selection, currency and saved readback',()=>fixture(async page=>{
 await go(page,'opportunity-create/index');await fitted(page);await shot(page,'新增商机-选择客户-1440');
 await page.getByPlaceholder('搜索客户名称',{exact:true}).fill('星河');await page.locator('.customer-picker-card [data-handler=selectCustomer]').filter({hasText:'星河'}).first().click();
 await page.locator('#opportunityForm input[data-key=name]').waitFor();await fitted(page);await visible(page,'.footer .submit');
 await page.locator('#opportunityForm input[data-key=name]').fill('桌面表单合成商机');await page.locator('#opportunityForm input[data-key=amount]').fill('12.5');
 await select(page,page.locator('#opportunityForm select').first(),'1');await page.waitForFunction(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.forecastRequired);
 await page.locator('.footer .submit').click();await page.waitForFunction(()=>!!SalesRuntime.current.data.error);assert.ok(await page.locator('#opportunityForm').count());
 await page.locator('#opportunityForm button[data-date-mode=date]').click();await page.locator('#web-date-dialog').getByRole('button',{name:'明天',exact:true}).click();
 await select(page,page.locator('#opportunityForm select').nth(1),'0');
 await page.locator('#opportunityForm input[data-key=collection]').fill('10');await page.locator('#opportunityForm input[data-key=recognized]').fill('0');await page.waitForFunction(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.predictedCollection==='3');
 await page.locator('fde-picker [data-handler=open]').click();await page.locator('#web-select-dialog').waitFor();await page.locator('#web-select-dialog .option[data-selectable]').first().click();await page.locator('#web-select-apply').click();
 assert.equal(await page.locator('#opportunityForm input[data-key=name]').inputValue(),'桌面表单合成商机');await page.locator('.web-workflow-scroll').evaluate(e=>e.scrollTop=0);await shot(page,'新增商机-填写-1440');
 await page.locator('.footer .submit').click();await page.waitForFunction(()=>JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')||'{}').opportunities?.some(o=>o.name==='桌面表单合成商机'));
 const row=await page.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).opportunities.find(o=>o.name==='桌面表单合成商机'));assert.equal(Number(row.amount),125000);assert.equal(row.customer_id,customer);assert.equal(row.version_no,1);assert.equal(row.quarterly_forecasts[0].collection_amount,100000);assert.equal(row.quarterly_forecasts[0].recognized_amount,0);
}));

for(const size of [[1366,768],[1024,600]])test(`${size.join('x')} desktop creation keeps action visible and scroll inside form`,()=>fixture(async page=>{
 await go(page,'management-task-create/index');await page.waitForFunction(()=>SalesRuntime.current.data.members.length>0);await fitted(page);await visible(page,'.bottom-bar button,.hero-title');await page.locator('.custom-due-fields').scrollIntoViewIfNeeded();await fitted(page);await visible(page,'.bottom-bar button,.custom-due-fields');
 await go(page,'opportunity-create/index?customerId='+customer);await page.locator('#opportunityForm').waitFor();await fitted(page);await visible(page,'.footer button,.hero-title');await page.locator('#opportunityForm [data-handler=toggleMore]').scrollIntoViewIfNeeded();await fitted(page);await visible(page,'.footer button');
},size));

for(const size of [[390,844],[320,740]])test(`${size[0]}px creation forms stay readable without horizontal overflow`,()=>fixture(async page=>{
 for(const path of ['management-task-create/index','opportunity-create/index?customerId='+customer]){
  await go(page,path);await page.locator(path.startsWith('management')?'.assignee-picker':'#opportunityForm').waitFor();await paint(page);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  const action=page.locator(path.startsWith('management')?'.bottom-bar button':'.footer>.submit');await action.scrollIntoViewIfNeeded();await visible(page,path.startsWith('management')?'.bottom-bar button':'.footer>.submit');
 }
},size));
