/** New Mini Program baseline, actual DOM actions, synthetic preview only; upstream disabled. */
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
const text='沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送给客户。\n跟进日期：2026-09-15\n对接人：合成UI联系人';
async function fixture(run,role='sales'){
 const ctx=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),forbidden=[],errors=[];
 await ctx.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){forbidden.push(u.pathname);return route.abort();}return route.continue();});
 const page=await ctx.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(forbidden,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}
 catch(error){const state=await page.evaluate(()=>({route:window.SalesRuntime?.current?.route,data:window.SalesRuntime?.current?.data,errors:window.SalesRuntime?.errors,modals:[...document.querySelectorAll('.wx-modal,.wx-picker,.sheet-mask')].map(el=>el.textContent.slice(0,1200)),buttons:[...document.querySelectorAll('button')].filter(el=>el.getBoundingClientRect().width).map(el=>({handler:el.dataset.handler,text:el.textContent.trim(),disabled:el.disabled})).slice(-25)})).catch(()=>({}));console.error('BASELINE_DIAGNOSTIC '+JSON.stringify({state,forbidden,errors}));throw error;}
 finally{await ctx.close();}
}
async function go(page,name,query=''){await page.goto(base+'/?mode=preview#/pages/'+name+'/index'+query);await page.waitForFunction(name=>SalesRuntime.current.route==='pages/'+name+'/index',name);}
async function enterVisit(page,fde=false){
 await go(page,'visit-entry');await page.locator('.customer-result[data-handler="chooseCustomer"]').filter({hasText:'星河制造'}).click();
 if(fde){await page.locator('.fde-choice-select').first().click();await page.waitForFunction(()=>SalesRuntime.current.data.fdeOpportunityVerified===true);}
 await page.locator('textarea.note-input').fill(text);await page.locator('button[data-handler="submitTranscript"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.values.contact_name==='合成UI联系人');
 if(fde)await page.waitForFunction(()=>SalesRuntime.current.data.fdeOpportunityVerified===true);
}
async function review(page){await page.locator('button[data-handler="review"]').click();await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit===true);}
async function archive(page){await page.locator('button[data-handler="archive"]').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认归档',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.archived===true);}

test('Web sidebar uses native visit save/discard and restores the same draft after reload', {timeout:60000},()=>fixture(async page=>{
 await go(page,'visit-entry');
 await page.locator('.customer-result[data-handler="chooseCustomer"]').first().click();
 await page.locator('textarea.note-input').fill('合成草稿：明天补充验收材料');
 const draftId=await page.evaluate(()=>SalesRuntime.current.draftId);
 await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
 await page.locator('.wx-modal-mask').getByText('是否保存本次拜访草稿？',{exact:true}).waitFor();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/visit-entry/index');
 await page.locator('.wx-modal-mask').getByRole('button',{name:'保存',exact:true}).click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/workbench/index');
 await page.evaluate(()=>SalesRuntime.userRoute('/pages/visit-entry/index'));
 await page.waitForFunction(()=>SalesRuntime.current.data.transcript==='合成草稿：明天补充验收材料');
 assert.equal(await page.evaluate(()=>SalesRuntime.current.draftId),draftId);
 await page.reload();
 await page.waitForFunction(()=>SalesRuntime.current?.data.transcript==='合成草稿：明天补充验收材料');
 assert.equal(await page.evaluate(()=>SalesRuntime.current.draftId),draftId);
 await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
 await page.locator('.wx-modal-mask').getByRole('button',{name:'放弃',exact:true}).click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/workbench/index');
 await page.evaluate(()=>SalesRuntime.userRoute('/pages/visit-entry/index'));
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-entry/index' && SalesRuntime.current.entryInitialized);
 assert.equal(await page.locator('textarea.note-input').inputValue(),'');
 assert.notEqual(await page.evaluate(()=>SalesRuntime.current.draftId),draftId);
}));

test('browser back prompts once and source-driven visit structure still opens confirmation', {timeout:60000},()=>fixture(async page=>{
 await page.evaluate(()=>SalesRuntime.route('/pages/visit-entry/index'));
 await page.locator('.customer-result[data-handler="chooseCustomer"]').first().click();
 await page.locator('textarea.note-input').fill(text);
 await page.goBack();
 await page.locator('.wx-modal-mask').waitFor();
 assert.equal(await page.locator('.wx-modal-mask').count(),1);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/visit-entry/index');
 await page.locator('.wx-modal-mask').getByRole('button',{name:'保存',exact:true}).click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
 await page.evaluate(()=>SalesRuntime.route('/pages/visit-entry/index'));
 await page.waitForFunction(()=>SalesRuntime.current.data.transcript.includes('合成UI联系人'));
 await page.locator('button[data-handler="submitTranscript"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index');
 assert.equal(await page.locator('.wx-modal-mask').count(),0);
}));

// A programmatically dismissed source modal is not a user click on “放弃”.
test('async native visit transition dismisses the exit prompt without discarding or hijacking navigation', {timeout:60000},()=>fixture(async page=>{
 await page.evaluate(()=>SalesRuntime.route('/pages/visit-entry/index'));
 await page.locator('.customer-result[data-handler="chooseCustomer"]').first().click();
 await page.locator('textarea.note-input').fill(text);
 const originalId=await page.evaluate(()=>SalesRuntime.current.draftId);
 await page.evaluate(()=>{
   const source=SalesRuntime.current;
   source.submitTranscript();
   SalesRuntime.userRoute('/pages/workbench/index',{tab:true});
 });
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index' && SalesRuntime.current.data.values.contact_name==='合成UI联系人');
 assert.equal(await page.locator('.wx-modal-mask').count(),0);
 // Both source confirm data and its original draft action survive the programmatic navigation.
 const snapshots=await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('visit')).map(key=>sessionStorage.getItem(key)));
 assert.ok(snapshots.some(value=>value.includes(originalId)));
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.values.contact_name),'合成UI联系人');
}));
