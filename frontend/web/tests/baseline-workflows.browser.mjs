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

test('sales UI structure→quality→edit invalidation→quality→human archive→customer readback', {timeout:60000},()=>fixture(async page=>{
 await enterVisit(page);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.quality),null);await review(page);
 const oldRun=await page.evaluate(()=>SalesRuntime.current.data.reviewRunId);
 await page.locator('[data-handler="backToEdit"]').click();await page.locator('input[data-key="contact_name"]').fill('合成UI联系人已更新');
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.reviewStale),true);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.canSubmit),false);
 await review(page);assert.notEqual(await page.evaluate(()=>SalesRuntime.current.data.reviewRunId),oldRun);await archive(page);
 const visitId=await page.evaluate(()=>SalesRuntime.current.data.visitId);assert.ok(visitId);await page.locator('[data-handler="openCustomer"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customers/index' && SalesRuntime.current.data.selectedCustomer);await page.locator('[data-handler="selectDetailTab"][data-tab="visits"]').click();await page.locator('.detail-visit[data-id="'+visitId+'"]').click();
 await page.getByText('合成UI联系人已更新',{exact:false}).first().waitFor();assert.ok((await page.locator('body').innerText()).includes('2026年9月15日')||(await page.locator('body').innerText()).includes('2026-09-15'));
}));

test('FDE UI chooses participating opportunity, archives and opens the new records page', {timeout:60000},()=>fixture(async page=>{
 await enterVisit(page,true);await review(page);await archive(page);const ids=await page.evaluate(()=>({customer:SalesRuntime.current.data.customerId,opportunity:SalesRuntime.current.data.opportunityId,visit:SalesRuntime.current.data.visitId}));assert.ok(ids.opportunity&&ids.visit);
 const context=encodeURIComponent(JSON.stringify({scope:'self',year:new Date().getFullYear(),quarters:[],periodLabel:'本年',scopeLabel:'本人'}));await go(page,'fde-records','?context='+context);
 await page.locator('.activity-card[data-id="'+ids.visit+'"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-detail/index');await page.getByText('合成UI联系人',{exact:true}).first().waitFor();
},'fde'));

test('customer task UI selects customer and opportunity, confirms a recipient and persists detail', {timeout:60000},()=>fixture(async page=>{
 await go(page,'management-task-create');await page.locator('button[data-type="customer"]').click();
 await page.locator('[data-handler="openTaskSelector"][data-kind="customer"]').click();await page.locator('.task-selector-row').filter({hasText:'星河制造'}).click();
 await page.locator('[data-handler="openTaskSelector"][data-kind="opportunity"]').click();await page.locator('.task-selector-row').first().click();
 await page.locator('.description-card textarea').fill('合成UI客户任务：整理验收清单并交付确认');await page.locator('.assignee-picker select').selectOption('0');
 await page.locator('button[data-date-mode="date"]').click();await page.locator('#web-date-dialog').getByRole('button',{name:'明天',exact:true}).click();
 await page.locator('[data-handler="submitTask"]').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'确认下发',exact:true}).click();
 await page.waitForFunction(()=>JSON.parse(sessionStorage.getItem('sales-web:preview:v1:lastManagementTaskCreated')||'null')?.id);
 const id=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('sales-web:preview:v1:lastManagementTaskCreated')).id);await go(page,'task-detail','?id='+id);
 await page.getByText('合成UI客户任务：整理验收清单并交付确认',{exact:false}).first().waitFor();const task=await page.evaluate(()=>SalesRuntime.current.data.task);assert.equal(task.association_kind,'customer');assert.ok(task.customer_id&&task.opportunity_id);
}));

test('FDE Demo page saves a scene, reads its detail and source versioned editing remains available', {timeout:60000},()=>fixture(async page=>{
 const customer='00000010-0000-4000-8000-000000000001',opportunity='00000011-0000-4000-8000-000000000001';
 await go(page,'demo-create','?customer_id='+customer+'&opportunity_id='+opportunity);await page.waitForFunction(()=>SalesRuntime.current.data.eligible===true);
 await page.locator('input[data-field="name"]').fill('合成UI知识检索场景');await page.locator('textarea[data-field="description"]').fill('登记检索流程，未运行模型、未部署应用。');
 await page.locator('button[data-handler="submitDemoScenes"]').click();await page.waitForFunction(()=>{const s=JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')||'{}');return s.demoScenes?.some(r=>r.name==='合成UI知识检索场景');});
 const scene=await page.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).demoScenes.find(r=>r.name==='合成UI知识检索场景'));
 await go(page,'demo-create','?customer_id='+customer+'&opportunity_id='+opportunity+'&demo_id='+scene.id+'&view=1');await page.waitForFunction(()=>SalesRuntime.current.data.sceneDetail?.version_no===1);await page.getByText('合成UI知识检索场景',{exact:true}).first().waitFor();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.sceneDetail.can_edit),true);
},'fde'));

test('quarter target portal saves first values then submits whole change for approval while old values remain', {timeout:60000},()=>fixture(async page=>{
 await go(page,'profile');await page.locator('.target-edit-button').waitFor();await page.locator('.target-edit-button').click();await page.locator('.target-sheet').waitFor();
 await page.locator('.target-sheet input[data-field="collection"]').fill('12.345678');await page.locator('.target-sheet input[data-field="recognized"]').fill('20');await page.locator('.target-sheet textarea').fill('合成UI首次目标商定依据');await page.locator('.target-submit').click();await page.locator('.target-sheet').waitFor({state:'detached'});
 await page.locator('.target-edit-button').click();await page.locator('.target-sheet').waitFor();assert.equal(await page.locator('.target-sheet input[data-field="collection"]').inputValue(),'12.345678');
 await page.locator('.target-sheet input[data-field="collection"]').fill('15');await page.locator('.target-sheet textarea').fill('合成UI后续调整需运营审批');await page.locator('.target-submit').click();await page.locator('.target-sheet').waitFor({state:'detached'});
 await page.locator('.target-notice').waitFor();assert.match(await page.locator('.target-notice').innerText(),/待运营审批/);assert.match(await page.locator('.target-notice').innerText(),/123,456.78/);
 const state=await page.evaluate(()=>Object.values(JSON.parse(localStorage.getItem('sales-web:preview-workspace:v1')).periodTargets)[0]);assert.equal(state.items.find(r=>r.kind==='collection').amount_text,'123456.78');assert.equal(state.pending_batches.length,1);
}));
