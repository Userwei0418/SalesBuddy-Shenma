/** Real shared handlers in an isolated synthetic workspace; business network blocked. */
import assert from 'node:assert/strict';
import {before,after,test} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const customer='00000010-0000-4000-8000-000000000001',opportunity='00000011-0000-4000-8000-000000000001';
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function fixture(run,size=[1440,900],role='sales'){
 const context=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),errors=[],blocked=[];
 await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
 const page=await context.newPage();page.setDefaultTimeout(8000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}finally{await context.close();}
}
async function go(page,path){await page.evaluate(path=>SalesRuntime.wx.navigateTo({url:'/pages/'+path}),path);await paint(page);}
async function visit(page){await go(page,'visit-entry/index');await page.locator('.customer-result[data-handler=chooseCustomer]').first().waitFor();}
async function openCustomer(page){await go(page,'customers/index');await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customers/index'&&!SalesRuntime.current.data.loading);await page.evaluate(id=>SalesRuntime.current.showCustomerDetail(id),customer);await page.waitForFunction(()=>SalesRuntime.current.data.selectedCustomer?.opportunities?.length&&SalesRuntime.current.data.detailSummary.loaded);await paint(page);}
async function openOpportunity(page){await go(page,`customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}`);await page.waitForFunction(()=>SalesRuntime.current.data.opportunity&&!SalesRuntime.current.data.opportunityLoading&&SalesRuntime.current.data.detailSummary.loaded);await paint(page);}
async function inViewport(page,selector){const boxes=await page.locator(selector).evaluateAll(nodes=>nodes.map(el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,width:r.width,height:r.height,viewport:[innerWidth,innerHeight]};}));assert.ok(boxes.length,selector);for(const r of boxes)assert.ok(r.height>0&&r.top>=0&&r.bottom<=r.viewport[1]+2&&r.left>=0&&r.right<=r.viewport[0]+2,selector+': '+JSON.stringify(r));}
async function noOverflow(page){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}
async function fittedEditor(page){const size=await page.locator('#page-root').evaluate(el=>({height:el.clientHeight,scroll:el.scrollHeight,top:el.scrollTop}));assert.ok(size.scroll<=size.height+2,JSON.stringify(size));assert.equal(size.top,0);await inViewport(page,'.note-input,.capture-card,button[data-handler=submitTranscript],.customer-picker-head');await noOverflow(page);}

for(const size of [[1366,768],[1024,600]])test(`${size.join('x')} visit entry needs no page scroll, including long text, import and disabled states`,()=>fixture(async page=>{
 await visit(page);await fittedEditor(page);
 await page.locator('.customer-result').first().click();
 const input=page.locator('textarea.note-input'),value='合成记录：确认试点范围与后续安排。\n'.repeat(400);
 await input.fill(value);await input.pressSequentially('123');await paint(page);assert.equal(await input.inputValue(),value+'123');
 assert.equal(await page.evaluate(()=>SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey).transcript),value+'123');
 const textScroll=await input.evaluate(el=>({height:el.clientHeight,scroll:el.scrollHeight}));assert.ok(textScroll.scroll>textScroll.height*2);await fittedEditor(page);
 await page.locator('[data-mode=file][data-handler=switchInputMode]').click();await page.locator('[data-handler=chooseMaterial]').waitFor();await fittedEditor(page);
 await page.locator('[data-handler=toggleInputHelp]').click();await page.locator('.input-help-popover').waitFor();await inViewport(page,'.input-help-popover');await page.locator('.help-heading button').click();
 await page.evaluate(()=>SalesRuntime.current.setData({fileName:'合成验收清单.docx',importStatus:'uploading',uploadProgress:35,isProcessing:true}));await paint(page);await fittedEditor(page);
 assert.equal(await input.isDisabled(),true);assert.equal(await page.locator('[data-handler=submitTranscript]').isDisabled(),true);assert.ok((await page.locator('.material-status').innerText()).includes('35%'));
 await page.evaluate(()=>SalesRuntime.current.setData({importStatus:'succeeded',isProcessing:false}));await paint(page);await fittedEditor(page);assert.equal(await page.locator('[data-handler=submitTranscript]').isDisabled(),false);
},size));

test('customer workspace exposes tabs before scrolling and keeps source signals, actions and section loaders',()=>fixture(async page=>{
 await openCustomer(page);await inViewport(page,'.web-customer-workspace .detail-tabs,.web-detail-actions');
 const before=await page.evaluate(()=>({id:SalesRuntime.current.data.selectedCustomer.id,signal:SalesRuntime.current.data.selectedCustomer.signal,opps:SalesRuntime.current.data.selectedCustomer.opportunities.map(o=>({id:o.id,signal:o.signal}))}));
 assert.ok((await page.locator('.detail-risk').getAttribute('class')).includes('traffic-'+before.signal.tone));
 const positions=await page.evaluate(()=>({identity:document.querySelector('.web-customer-identity').getBoundingClientRect().right,main:document.querySelector('.web-customer-primary').getBoundingClientRect().x}));assert.ok(positions.main>positions.identity, 'persistent identity stays left of tab content');
 assert.equal(await page.locator('.web-detail-aside').evaluate(el=>getComputedStyle(el).overflowY),'visible');
 for(const key of ['tasks','visits','opportunity','overview']){
  await page.locator('.web-customer-primary').evaluate(el=>el.scrollTop=el.scrollHeight);
  await page.locator(`.detail-tabs [data-tab=${key}]`).click();await page.waitForFunction(key=>SalesRuntime.current.data.detailTab===key,key);await paint(page);
  assert.equal(await page.locator('.web-customer-primary').evaluate(el=>el.scrollTop),0);
  const section={tasks:'tasks',visits:'visits',opportunity:'opportunities',overview:'contacts'}[key];await page.waitForFunction(section=>SalesRuntime.current.data.detailPages[section]?.loaded,section);
 }
 assert.equal(await page.locator('.customer-agent-panel').count(),1);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.selectedCustomer.id),before.id);
 const current=await page.evaluate(()=>SalesRuntime.current.data.selectedCustomer.opportunities.map(o=>({id:o.id,signal:o.signal})));assert.deepEqual(current,before.opps);
 await page.locator('.web-detail-actions [data-handler=createTask]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/management-task-create/index');assert.equal(await page.evaluate(()=>SalesRuntime.current.data.customerId),customer);
}));

test('opportunity workspace retains complete information, scoped tabs and traffic signal on every tab',()=>fixture(async page=>{
 await openOpportunity(page);await inViewport(page,'.web-op-summary,.op-detail-tabs');
 const before=await page.evaluate(()=>({amount:SalesRuntime.current.data.opportunity.amount,signal:SalesRuntime.current.data.opportunity.signal,name:SalesRuntime.current.data.opportunity.name}));
 assert.ok((await page.locator('.web-op-summary').innerText()).includes(before.amount));
 assert.equal(await page.locator('.opportunity-view-grid>view').count(),11);
 for(const key of ['tasks','visits','opportunity','overview']){
  await page.locator('.web-op-workarea').evaluate(el=>el.scrollTop=el.scrollHeight);
  await page.locator(`.op-detail-tab[data-tab=${key}]`).click();await page.waitForFunction(key=>SalesRuntime.current.data.opportunityTab===key,key);await paint(page);
  assert.equal(await page.locator('.web-op-workarea').evaluate(el=>el.scrollTop),0);
  assert.equal(await page.locator('#page-root').evaluate(el=>el.scrollTop),0);
  assert.ok((await page.locator('.web-op-summary .traffic-badge').getAttribute('class')).includes('traffic-'+before.signal.tone));
  const section={tasks:'tasks',visits:'visits',opportunity:'timeline'}[key];if(section)await page.waitForFunction(section=>SalesRuntime.current.data.detailPages[section]?.loaded,section);
 }
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunity.amount),before.amount);await noOverflow(page);
}));

test('independent customer route keeps asset and visit actions with the same customer context',()=>fixture(async page=>{
 await go(page,'customer-detail/index?id='+customer);await page.waitForFunction(()=>SalesRuntime.current.data.customer&&SalesRuntime.current.data.detailSummary.loaded);await inViewport(page,'.web-detail-header,.detail-tabs');
 assert.equal(await page.locator('.score-card').count(),1);await page.locator('[data-handler=recordVisit]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-entry/index'&&SalesRuntime.current.data.customerConfirmed);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.customerId),customer);
}));

test('FDE visit verifies participating opportunity and keeps its controls and submit visible on a short screen',()=>fixture(async page=>{
 await visit(page);await page.locator('.customer-result').first().click();await page.locator('.fde-choice-select').first().waitFor();await fittedEditor(page);await inViewport(page,'.fde-choice-search,.fde-choice-row:first-child .fde-choice-select');
 await page.locator('.fde-choice-select').last().scrollIntoViewIfNeeded();await fittedEditor(page);await page.locator('.fde-choice-list').evaluate(el=>el.scrollTop=0);
 await page.locator('textarea.note-input').fill('合成技术沟通记录，等待商机核验。');assert.equal(await page.locator('[data-handler=submitTranscript]').isDisabled(),true);
 await page.locator('.fde-choice-select').first().click();await page.waitForFunction(()=>SalesRuntime.current.data.fdeOpportunityVerified===true&&SalesRuntime.current.data.canSubmit);await page.locator('[data-handler=submitTranscript]:enabled').waitFor();await fittedEditor(page);assert.equal(await page.locator('[data-handler=submitTranscript]').isDisabled(),false);
 const draft=await page.evaluate(()=>SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey));assert.ok(draft.opportunityId&&draft.customerId);
},[1024,600],'fde'));

for(const size of [[390,844],[320,740]])test(`${size[0]}px narrow layouts preserve readable details and access to visit controls`,()=>fixture(async page=>{
 await openCustomer(page);await noOverflow(page);await page.locator('.detail-tabs [data-tab=visits]').click();await page.waitForFunction(()=>SalesRuntime.current.data.detailPages.visits?.loaded);assert.ok(await page.locator('.detail-visit').count());
 await openOpportunity(page);await noOverflow(page);await page.locator('.op-detail-tab[data-tab=visits]').click();await page.waitForFunction(()=>SalesRuntime.current.data.detailPages.visits?.loaded);
 await visit(page);await noOverflow(page);await page.locator('.customer-result').first().click();await page.locator('textarea.note-input').fill('手机端合成拜访文本');await page.locator('[data-handler=submitTranscript]').scrollIntoViewIfNeeded();assert.equal(await page.locator('[data-handler=submitTranscript]').isEnabled(),true);
},size));
