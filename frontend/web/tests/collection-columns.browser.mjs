/** Summary/list desktop columns: isolated preview, original filters and pagination. */
import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function fixture(run,size,role,route){
 const c=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),blocked=[],errors=[];
 await c.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 await c.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const page=await c.newPage();page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview#/pages/'+route+'/index');await page.waitForFunction(()=>SalesRuntime.current&&!SalesRuntime.app._capabilityFlight&&!SalesRuntime.current.data.loading);await run(page);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}finally{await c.close();}
}
async function columns(page,aside,main){await paint(page);const m=await page.evaluate(({aside,main})=>{
 const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height,client:e.clientHeight,scroll:e.scrollHeight};};
 const rail=document.querySelector(aside),list=document.querySelector(main),workspace=rail.parentElement;const railStyle=getComputedStyle(rail),listStyle=getComputedStyle(list),frameStyle=getComputedStyle(workspace);
 return {frame:{gap:frameStyle.columnGap,border:frameStyle.borderTopWidth,divider:railStyle.borderRightWidth,railBackground:railStyle.backgroundColor,listBackground:listStyle.backgroundColor,railPadding:railStyle.paddingLeft},width:innerWidth,height:innerHeight,document:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],root:box(document.querySelector('#page-root')),aside:box(document.querySelector(aside)),main:box(document.querySelector(main)),hosts:[...document.querySelectorAll('[data-web-page-scroll]')].filter(e=>e.getBoundingClientRect().height>0&&/^(auto|scroll)$/.test(getComputedStyle(e).overflowY)).map(e=>e.className)};
 },{aside,main});assert.ok(m.document[0]<=m.width+1&&m.document[1]<=m.height+1,JSON.stringify(m));assert.ok(m.root.scroll<=m.root.client+1,JSON.stringify(m));assert.ok(m.aside.right<=m.main.x+1&&Math.abs(m.aside.y-m.main.y)<2,JSON.stringify(m));assert.ok(m.main.height>=m.root.height*.85&&m.main.bottom<=m.height,JSON.stringify(m));assert.equal(m.hosts.length,1,JSON.stringify(m));assert.ok(Math.abs(m.aside.bottom-m.main.bottom)<2,'context rail fills the same frame height as the list');assert.ok(Math.abs(m.aside.right-m.main.x)<2,'rail and list share one continuous frame without an empty gutter');assert.deepEqual({gap:m.frame.gap,border:m.frame.border,divider:m.frame.divider,padding:m.frame.railPadding},{gap:'0px',border:'1px',divider:'1px',padding:'18px'});assert.notEqual(m.frame.railBackground,m.frame.listBackground,'subtle context rail is distinct from the white reading surface');return m;}
for(const role of ['sales','manager'])for(const size of [[1366,768],[1366,600],[1180,650]])test(`${role} workbench ${size.join('x')}: summary and list align, native last page loads in one surface`,()=>fixture(async page=>{
 await page.waitForFunction(()=>SalesRuntime.current.data.opportunityDataReady&&!SalesRuntime.current.data.opportunityListLoading);await page.locator('.workbench-opportunity-card').first().waitFor();
 const before=await columns(page,'.web-workbench-overview','.web-opportunity-results');
 const values=await page.locator('.opportunity-summary-value').allTextContents(),title=await page.locator('.workbench-opportunity-tools').boundingBox();
 const first=await page.locator('.workbench-opportunity-card').first().boundingBox();assert.ok(first.y+first.height<before.main.bottom,'first result is readable in the first screen');
 await page.locator('.web-opportunity-results').evaluate(e=>e.scrollTop=e.scrollHeight);
 await page.waitForFunction(()=>!SalesRuntime.current.data.opportunityHasMore&&!SalesRuntime.current.data.opportunityLoadingMore);
 await paint(page);
 assert.deepEqual(await page.locator('.opportunity-summary-value').allTextContents(),values);
 const tools=await page.locator('.workbench-opportunity-tools').boundingBox();assert.ok(Math.abs(tools.y-title.y)<2,'list filters stay available when reading the final page');
 assert.equal(await page.locator('.web-opportunity-results-scroll').evaluate(e=>getComputedStyle(e).overflowY),'visible');
 await page.locator('.web-opportunity-results').evaluate(e=>e.scrollTop=0);await paint(page);
 if(role==='sales'&&size[0]===1366&&size[1]===768){const dir=new URL('file:///tmp/sales-sidebar-validation/');await mkdir(dir,{recursive:true});await page.screenshot({path:fileURLToPath(new URL('侧栏统一-商机-1366x768.png',dir))});}
},size,role,'workbench'));
for(const size of [[1366,768],[1366,600],[1180,650]])test(`tasks ${size.join('x')}: one list surface and visible summary retain status navigation`,()=>fixture(async page=>{
 await page.locator('.web-task-row').first().waitFor();await columns(page,'.web-task-overview','.web-task-content');
 const before=await page.locator('.web-task-heading').boundingBox();await page.locator('.web-task-content').evaluate(e=>e.scrollTop=e.scrollHeight);await paint(page);
 const after=await page.locator('.web-task-heading').boundingBox();assert.equal(after.y,before.y);
 await page.locator('.web-task-tab[data-key="completed"]').click();await page.waitForFunction(()=>SalesRuntime.current.data.activeTab==='completed'&&!SalesRuntime.current.data.loading);
 await page.locator('.web-task-tab[data-key="completed"][aria-pressed="true"]').waitFor();
 assert.equal(await page.locator('.web-task-results-scroll').evaluate(e=>getComputedStyle(e).overflowY),'visible');
 if(size[0]===1366&&size[1]===768){await page.locator('.web-task-tab[data-key="pending"]').click();await page.waitForFunction(()=>SalesRuntime.current.data.activeTab==='pending'&&!SalesRuntime.current.data.loading);await paint(page);const dir=new URL('file:///tmp/sales-sidebar-validation/');await page.screenshot({path:fileURLToPath(new URL('侧栏统一-任务-1366x768.png',dir))});}
},size,'sales','tasks'));
