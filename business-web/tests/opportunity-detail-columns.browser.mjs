/** Local preview fixtures only. The detail's object context is independent of its working tab. */
import assert from 'node:assert/strict';
import {before, after, test} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const customer='00000010-0000-4000-8000-000000000001',opportunity='00000011-0000-4000-8000-000000000001';
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:'',...(process.env.DETAIL_STATIC_DIR?{staticDir:process.env.DETAIL_STATIC_DIR}:{})});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function fixture(size,run,role='sales'){
 const c=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),blocked=[],errors=[];
 await c.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 await c.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const p=await c.newPage();p.setDefaultTimeout(9000);p.on('pageerror',e=>errors.push(e.message));
 try {await p.goto(base+'/?mode=preview');await p.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);await go(p,true);await run(p);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);}
 catch(e){console.error('OP_COLUMN_DIAGNOSTIC',await p.locator('#page-root,.actual-heading,.web-op-detail-frame,.web-op-identity,.web-op-workarea,.web-op-main,.web-op-aside').evaluateAll(es=>es.map(e=>{const r=e.getBoundingClientRect();return {cls:e.className,x:r.x,y:r.y,w:r.width,h:r.height,client:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY,display:getComputedStyle(e).display};})));throw e;}finally{await c.close();}
}
async function paint(p){await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function go(p,detail){await p.evaluate(({customer,opportunity,detail})=>SalesRuntime.wx.navigateTo({url:`/pages/customer-assets/index?customer_id=${customer}${detail?'&opportunity_id='+opportunity:''}`}),{customer,opportunity,detail});await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.opportunityLoading);await paint(p);}
async function fitted(p){assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);if(p.viewportSize().width>600)assert.equal(await p.locator('#page-root').evaluate(e=>e.scrollHeight<=e.clientHeight+2),true);}
for(const size of [[1180,650],[1366,768],[1700,800],[1920,900],[1024,600],[630,800],[390,844]])test(`opportunity ${size.join('x')}: responsive columns retain all native sections and asset-only fallback`,()=>fixture(size,async p=>{
 await fitted(p);assert.equal(await p.locator('.actual-heading').count(),1);assert.equal(await p.locator('.web-op-summary').count(),1);
 if(size[0]>=1180){const left=await p.locator('.web-op-identity').boundingBox(),right=await p.locator('.web-op-workarea').boundingBox(),tabs=await p.locator('.op-detail-tabs').boundingBox();assert.equal(Math.round(left.width),268);assert.ok(right.x>=left.x+left.width+15);assert.ok(Math.abs(right.y-left.y)<=1);assert.ok(tabs.y<=left.y+1);assert.equal(await p.locator('.web-op-main').evaluate(e=>getComputedStyle(e).overflowY),'visible');
  if(size[0]>=1700){const main=await p.locator('.web-op-main').boundingBox(),advice=await p.locator('.op-advice').boundingBox();assert.ok(advice.x>main.x+main.width);assert.ok(Math.abs(advice.y-main.y)<=1);}
  const titleY=(await p.locator('.actual-title').boundingBox()).y;await p.locator('.web-op-workarea').evaluate(e=>e.scrollTop=e.scrollHeight);await paint(p);assert.equal((await p.locator('.actual-title').boundingBox()).y,titleY);assert.ok((await p.locator('.op-detail-tabs').boundingBox()).y<right.y+2);assert.ok(await p.locator('.web-op-workarea').evaluate(e=>e.scrollTop>0));
  // Both source and destination tabs have overflowing content, so browser
  // clamping cannot accidentally satisfy this navigation-reset assertion.
  await p.locator('.op-detail-tab[data-tab=tasks]').click();await p.waitForFunction(()=>SalesRuntime.current.data.opportunityTab==='tasks'&&!SalesRuntime.current.data.detailPages.tasks.loading);await paint(p);
  await p.evaluate(()=>SalesRuntime.current.setData({relatedTasks:Array.from({length:18},(_,i)=>({id:'columns-task-'+i,title:'【合成】需要继续跟进的交付任务 '+i,description:'核对交付清单与负责人，保持原始任务关联范围。',statusLabel:'待处理',signal:{tone:'yellow',label:'待处理'},assigneeName:'合成执行人',timelineAt:'2026-09-20'}))}));await p.waitForFunction(()=>document.querySelectorAll('.op-related-card[data-handler=openRelatedTask]').length===18);await paint(p);
  await p.locator('.web-op-workarea').evaluate(e=>e.scrollTop=300);assert.ok(await p.locator('.web-op-workarea').evaluate(e=>e.scrollTop>=299));
  await p.locator('.op-detail-tab[data-tab=overview]').click();await p.waitForFunction(()=>SalesRuntime.current.data.opportunityTab==='overview');await p.locator('.opportunity-view').waitFor();await paint(p);assert.equal(await p.locator('.web-op-workarea').evaluate(e=>e.scrollTop),0,'changing the working tab restores its beginning');
 }else assert.equal(await p.locator('.web-op-detail-frame').evaluate(e=>getComputedStyle(e).display),'contents');
 for(const tab of ['tasks','visits','opportunity','overview']){await p.locator(`.op-detail-tab[data-tab=${tab}]`).click();await p.waitForFunction(tab=>SalesRuntime.current.data.opportunityTab===tab,tab);await fitted(p);assert.equal(await p.locator('.actual-heading').count(),1);}
 if(process.env.DETAIL_COLUMNS_SCREENSHOTS&&[1366,1920].includes(size[0])){const dir=new URL('../docs/desktop-forms-20260920/截图/',import.meta.url);await mkdir(dir,{recursive:true});await p.screenshot({path:fileURLToPath(new URL(`商机详情-${size[0]>=1700?'三':'两'}栏-${size.join('x')}.png`,dir))});}
 await go(p,false);await fitted(p);assert.equal(await p.locator('.web-opportunity-workspace').count(),0);assert.equal(await p.locator('.web-op-detail-frame').evaluate(e=>getComputedStyle(e).display),'contents');assert.equal(await p.locator('.actual-heading').count(),1);
}));
test('opportunity overlay remains above the columns with native cancel; capability still hides edit',()=>fixture([1366,768],async p=>{
 assert.equal(await p.locator('[data-handler=webEditOpportunity]').count(),1);await p.evaluate(()=>SalesRuntime.current.setData({canManage:true,readOnly:false}));await paint(p);await p.locator('[data-handler=openForm]').click();await p.waitForFunction(()=>SalesRuntime.current.data.formOpen&&!SalesRuntime.current.data.formTargetLoading);await paint(p);
 const sheet=await p.locator('.actual-form-sheet').boundingBox();assert.ok(sheet.x>=0&&sheet.x+sheet.width<=1366);assert.ok(sheet.y>=0&&sheet.y+sheet.height<=769);for(const selector of ['.actual-form-header','.actual-form-actions']){const r=await p.locator(selector).boundingBox();assert.ok(r.y>=sheet.y&&r.y+r.height<=769);}
 await p.locator('.actual-form-cancel').click();await p.waitForFunction(()=>!SalesRuntime.current.data.formOpen);await p.locator('.actual-form-layer').waitFor({state:'detached'});assert.equal(await p.locator('.actual-form-layer').count(),0);
 await p.evaluate(()=>{SalesRuntime.app.globalData.session.capabilities['opportunity.edit']=false;SalesRuntime.current.setData({});});await paint(p);assert.equal(await p.locator('[data-handler=webEditOpportunity]').count(),0);
}));
test('FDE demo tab removes the advice column, keeping original Demo and visit actions',()=>fixture([1920,900],async p=>{
 await p.locator('.op-detail-tab[data-tab=demo]').click();await p.waitForFunction(()=>SalesRuntime.current.data.opportunityTab==='demo');await paint(p);await fitted(p);assert.equal(await p.locator('.op-advice').count(),0);assert.equal(await p.locator('.web-op-workarea').evaluate(e=>getComputedStyle(e).display),'block');assert.equal(await p.locator('[data-handler=recordFdeVisit]').count(),1);assert.equal(await p.locator('[data-handler=openDemoScenes]').count(),1);
},'fde'));
