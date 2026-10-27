/** Core Web pages: real shared handlers and isolated synthetic data. */
import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
const customer='00000010-0000-4000-8000-000000000001';
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function fixture(run,size){const context=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),errors=[],blocked=[];
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}finally{await context.close();}}
const routes=[
 ['index','index/index','.web-home-metrics,.web-home-actions'],
 ['customers','customers/index','.asset-page-heading,.asset-overview,.web-customer-list-controls'],
 ['customer-detail','customer-detail/index?id='+customer,'.web-detail-header,.detail-tabs'],
 ['workbench','workbench/index','.page-head,.opportunity-summary-card,.workbench-opportunity-tools'],
 ['tasks','tasks/index?tab=all','.web-task-heading,.web-task-controls'],
 ['opportunity-create','opportunity-create/index?customerId='+customer,'.opportunity-create-hero,.footer .submit'],
 ['management-task-create','management-task-create/index','.task-hero,.bottom-bar .submit'],
 ['visit-entry','visit-entry/index','.entry-hero,.submit-button'],
 ['customer-claim','customer-claim/index','.claim-hero,.claim-footer'],
];
async function go(page,path){await page.evaluate(path=>SalesRuntime.wx.navigateTo({url:'/pages/'+path}),path);await page.waitForFunction(()=>!SalesRuntime.current.data.loading&&!SalesRuntime.current.data.opportunityLoading);if(path.startsWith('workbench/'))await page.waitForFunction(()=>SalesRuntime.current.data.dataReady&&!SalesRuntime.current.data.opportunityListLoading&&!SalesRuntime.current.data.opportunityOverviewLoading);if(path.startsWith('customer-detail/'))await page.waitForFunction(()=>SalesRuntime.current.data.customer&&SalesRuntime.current.data.detailSummary?.loaded);await paint(page);}
async function fitted(page,selector){await paint(page);const v=await page.evaluate(selector=>{let r=document.querySelector('#page-root');return {route:SalesRuntime.current.route,ih:innerHeight,iw:innerWidth,dh:document.documentElement.scrollHeight,dw:document.documentElement.scrollWidth,root:[r.clientHeight,r.scrollHeight],controls:[...document.querySelectorAll(selector)].map(e=>{let b=e.getBoundingClientRect();return {cls:e.className,x:b.x,y:b.y,bottom:b.bottom,right:b.right,h:b.height}})}},selector);assert.ok(v.dh<=v.ih+1&&v.dw<=v.iw+1&&v.root[1]<=v.root[0]+2,JSON.stringify(v));assert.ok(v.controls.length,selector);for(const b of v.controls)assert.ok(b.h>0&&b.y>=0&&b.bottom<=v.ih-(v.iw<=900?66:0)+2&&b.x>=0&&b.right<=v.iw+1,JSON.stringify(v));}
for(const size of [[1366,768],[1024,600],[630,800]])for(const [name,path,selector] of routes)test(`${size.join('x')} ${name} keeps its header and actions in a fitted Web frame`,()=>fixture(async page=>{await go(page,path);await fitted(page,selector);if(name==='workbench'&&size[0]===1024){const card=await page.locator('.workbench-opportunity-card').first().boundingBox();const area=await page.locator('.web-opportunity-results-scroll').boundingBox();assert.ok(area.height>=180&&card.y+card.height<=area.y+area.height+1,JSON.stringify({card,area}));}if(size[0]===1366&&['tasks','workbench','visit-entry'].includes(name)){await mkdir(new URL('../docs/desktop-forms-20260920/截图/',import.meta.url),{recursive:true});await page.screenshot({path:fileURLToPath(new URL(`../docs/desktop-forms-20260920/截图/全页-核心-${name}.png`,import.meta.url))});}},size));
for(const size of [[1366,768],[1024,600],[630,800],[390,844]])test(`${size.join('x')} login keeps account fields and login action reachable without horizontal overflow`,()=>fixture(async page=>{await page.goto(base+'/?mode=live#/pages/login/index');await page.locator('input[placeholder="请输入账号名或手机号"]').waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);const b=page.locator('[data-handler=submitLogin]');const before=await b.boundingBox();assert.ok(before.y>=0&&before.y+before.height<=size[1]);assert.ok(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));assert.ok(await b.isVisible());},size));
for(const size of [[1366,768],[630,800]])test(`${size[0]}px task primary scroller keeps native paging, pageScrollTo and return position`,()=>fixture(async page=>{
 await go(page,'tasks/index?tab=all');await page.waitForFunction(()=>SalesRuntime.current.data.filteredTasks.length===20&&SalesRuntime.current.data.hasMore);
 await page.evaluate(()=>{window.__pageReads=[];const original=SalesRuntime.wx.request;SalesRuntime.wx.request=o=>{__pageReads.push(o.url);return original(o)};});
 const taskHost=size[0]>=1180?'.web-task-content':'.web-task-results-scroll';
 const pane=page.locator(taskHost);await pane.evaluate(e=>e.scrollTop=e.scrollHeight);await page.waitForFunction(()=>SalesRuntime.current.data.filteredTasks.length===22&&!SalesRuntime.current.data.loadingMore);
 assert.equal(await page.evaluate(()=>__pageReads.filter(u=>u.includes('/tasks?')&&new URL(u,location.href).searchParams.get('offset')==='20').length),1);
 await page.evaluate(()=>SalesRuntime.wx.pageScrollTo({scrollTop:240,duration:0}));await page.waitForFunction(selector=>Math.abs(document.querySelector(selector).scrollTop-240)<2,taskHost);assert.equal(await page.locator('#page-root').evaluate(e=>e.scrollTop),0);
 await go(page,'visit-entry/index');await page.evaluate(()=>SalesRuntime.wx.navigateBack());await page.waitForFunction(()=>SalesRuntime.current.route==='pages/tasks/index'&&!SalesRuntime.current.data.loading);await page.waitForFunction(selector=>Math.abs(document.querySelector(selector).scrollTop-240)<3,taskHost);
 await fitted(page,'.web-task-heading,.web-task-controls');
},size));

// Phone form frames are bounded too; a viewport breakpoint alone cannot select the scroll owner.
test('390px fixed customer form keeps native page scrolling and return restoration inside its content',()=>fixture(async page=>{
 await go(page,'customer-create/index');
 await page.waitForFunction(()=>SalesRuntime.current.data.fields?.length===10&&SalesRuntime.current.directoryTeams?.length);await paint(page);
 await page.evaluate(()=>{const p=SalesRuntime.current,original=p.onPageScroll;p.__scrollEvents=0;p.onPageScroll=function(e){this.__scrollEvents++;return original?.call(this,e)}});
 const content=page.locator('.web-customer-create .web-extended-scroll');
 await page.evaluate(()=>SalesRuntime.wx.pageScrollTo({scrollTop:240,duration:0}));
 await page.waitForFunction(()=>document.querySelector('.web-customer-create .web-extended-scroll').scrollTop===240&&SalesRuntime.current.__scrollEvents>0);
 assert.equal(await page.evaluate(()=>scrollY),0);
 await page.evaluate(()=>SalesRuntime.wx.pageScrollTo({scrollTop:0,duration:0}));await page.waitForFunction(()=>document.querySelector('.web-customer-create .web-extended-scroll').scrollTop===0);
 await content.evaluate(e=>e.scrollTop=240);await paint(page);
 await go(page,'tasks/index?tab=all');await page.evaluate(()=>SalesRuntime.wx.navigateBack());
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-create/index'&&document.querySelector('.web-customer-create .web-extended-scroll')?.scrollTop===240);
 await page.setViewportSize({width:630,height:800});await paint(page);const resized=await content.evaluate(e=>({top:e.scrollTop,max:e.scrollHeight-e.clientHeight}));assert.equal(resized.top,Math.min(240,resized.max));
},[390,844]));
