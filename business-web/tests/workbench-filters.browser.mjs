/** Exercise independent overview/list filters with the unchanged shared handlers. */
import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function ready(page){await paint(page);await page.waitForFunction(()=>SalesRuntime?.current?.data.dataReady&&!SalesRuntime.current.data.opportunityListLoading&&!SalesRuntime.current.data.opportunityOverviewLoading);await paint(page);}
async function picker(page,selector){await page.locator(selector).click();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);}
async function choose(page,selector,value){await picker(page,selector);await page.locator(`#web-select-dialog .option[data-value="${value}"]`).click();await page.waitForSelector('#web-select-dialog',{state:'detached'});await ready(page);}
async function fixture(run,role='sales'){
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),errors=[],blocked=[];
 await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const page=await context.newPage();page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview#/pages/workbench/index');await page.waitForFunction(()=>SalesRuntime?.current?.data.opportunityTotal>0);await ready(page);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}finally{await context.close();}
}
async function overview(page){return page.evaluate(()=>({serial:SalesRuntime.current.opportunityOverviewSerial,board:SalesRuntime.current.data.opportunityBoard,selection:SalesRuntime.current.data.summaryQuarter}));}
async function list(page){return page.evaluate(()=>({serial:SalesRuntime.current.opportunityListSerial,total:SalesRuntime.current.data.opportunityTotal,ids:SalesRuntime.current.data.filteredOpportunities.map(r=>r.id),params:SalesRuntime.current.opportunityParams()}));}
async function countMatches(page){assert.equal(Number(await page.locator('.web-list-count').innerText()),await page.evaluate(()=>SalesRuntime.current.data.opportunityTotal));}

test('summary and list filters affect their own results; reset does not clear summary selection',()=>fixture(async page=>{
 const initialList=await list(page);
 await page.locator('.quarter-option[data-scope=summary][data-value="3"]').click();await ready(page);
 assert.deepEqual(await list(page),initialList);
 const summary=await overview(page);
 await picker(page,'.filter-stage');assert.match(await page.locator('#web-select-dialog').innerText(),/仅筛选商机列表/);
 await page.locator('#web-select-dialog .option[data-value=solution]').click();await page.locator('#web-select-apply').click();await ready(page);
 assert.deepEqual(await overview(page),summary);assert.ok((await list(page)).total<initialList.total);
 assert.match(await page.locator('.web-list-conditions').innerText(),/阶段：方案沟通/);await countMatches(page);
 await choose(page,'.filter-grade select','1');assert.deepEqual(await overview(page),summary);
 assert.match(await page.locator('.web-list-conditions').innerText(),/等级：A/);
 await picker(page,'.filter-close');await page.locator('#web-select-dialog .option[data-value="3"]').click();await page.locator('#web-select-apply').click();await ready(page);
 assert.deepEqual(await overview(page),summary);assert.match(await page.locator('.web-list-conditions').innerText(),/预计关单：\d{4}年 Q3/);
 const filtered=await list(page);assert.deepEqual(filtered.params.quarters,[3]);await countMatches(page);
 await page.locator('.workbench-filter-reset').click();await ready(page);
 assert.deepEqual(await overview(page),summary);assert.equal((await list(page)).total,initialList.total);
 assert.equal(await page.locator('.web-list-conditions').count(),0);assert.equal(await page.locator('.workbench-filter-reset').count(),0);
 assert.deepEqual((await list(page)).params,initialList.params);
}));

test('manager team and owner belong only to the list; active conditions and clear reflect both',()=>fixture(async page=>{
 const summary=await overview(page),initial=await list(page);
 await choose(page,'.filter-team select','1');assert.deepEqual(await overview(page),summary);
 const team=await page.evaluate(()=>SalesRuntime.current.data.executionTeamLabel);
 assert.ok((await page.locator('.web-list-conditions').innerText()).includes('团队：'+team));
 await choose(page,'.filter-owner select','1');assert.deepEqual(await overview(page),summary);await countMatches(page);
 const owner=await page.evaluate(()=>SalesRuntime.current.data.opportunityOwnerOptions[SalesRuntime.current.data.opportunityOwnerIndex].label);
 assert.ok((await page.locator('.web-list-conditions').innerText()).includes('负责人：'+owner));
 assert.ok((await list(page)).total<=initial.total);
 await picker(page,'.filter-owner select');await page.keyboard.press('Escape');assert.equal((await list(page)).params.owner,owner);
 await page.locator('.workbench-filter-reset').click();await ready(page);assert.deepEqual(await overview(page),summary);
 assert.deepEqual((await list(page)).params,initial.params);
},'manager'));

test('zero matches remain inside the filter region and clearing restores the list',()=>fixture(async page=>{
 const summary=await overview(page);
 await picker(page,'.filter-close');await page.locator('#web-select-dialog button[aria-label=下一年]').click();
 await page.locator('#web-select-dialog .option[data-value="1"]').click();await page.locator('#web-select-apply').click();await ready(page);
 assert.equal((await list(page)).total,0);await countMatches(page);
 assert.equal(await page.locator('.web-opportunity-results .workbench-opportunity-empty').count(),1);
 assert.match(await page.locator('.web-list-conditions').innerText(),/预计关单：\d{4}年 Q1/);
 assert.deepEqual(await overview(page),summary);
 await page.locator('.workbench-filter-reset').click();await ready(page);
 assert.ok((await list(page)).total>0);assert.equal(await page.locator('.workbench-opportunity-empty').count(),0);
}));

test('desktop filters stay with list; narrow layouts expose all controls without clipping',()=>fixture(async page=>{
 for(const [width,height] of [[1440,1000],[1024,600],[630,800],[390,844],[320,740]]){
  await page.setViewportSize({width,height});await page.locator('#page-root').evaluate(el=>el.scrollTop=0);await paint(page);
  const facts=await page.evaluate(()=>{const root=document.querySelector('#page-root'),area=document.querySelector('.web-opportunity-results'),tools=document.querySelector('.workbench-opportunity-tools');return {overflow:document.documentElement.scrollWidth>innerWidth,rootOverflow:root.scrollWidth>root.clientWidth+1,grouped:area.contains(tools)&&!!area.querySelector('.workbench-opportunity-list'),controls:[...tools.querySelectorAll('.workbench-filter-item')].map(el=>{const r=el.getBoundingClientRect(),c=el.querySelector('.workbench-filter-control').getBoundingClientRect();return r.width>120&&r.height>=35&&c.width<=r.width+2&&c.height<=r.height+2;})};});
  assert.equal(facts.overflow,false,JSON.stringify({width,facts}));assert.equal(facts.rootOverflow,false);assert.equal(facts.grouped,true);assert.ok(facts.controls.every(Boolean),JSON.stringify({width,facts}));
  await picker(page,'.filter-stage');const box=await page.locator('#web-select-dialog').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width+1);await page.keyboard.press('Escape');
  if(width>600){
   const before=await page.locator('.workbench-opportunity-tools').boundingBox();
   await page.locator(width>=1180?'.web-opportunity-results':'.web-opportunity-results-scroll').evaluate(el=>el.scrollTop=650);await paint(page);
   const after=await page.locator('.workbench-opportunity-tools').boundingBox();
   assert.ok(Math.abs(after.y-before.y)<2&&after.y>=0&&after.y+after.height<height,JSON.stringify({before,after}));
   assert.equal(await page.locator('#page-root').evaluate(el=>el.scrollTop),0);
  }
 }
}));
