import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;const evidence=process.env.UI_EVIDENCE || '../evidence';
const uid=(n,i=1)=>`${String(n).padStart(8,'0')}-0000-4000-8000-${String(i).padStart(12,'0')}`;
before(async()=>{await mkdir(evidence,{recursive:true});server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));});
async function fixture(run,viewport={width:1440,height:900}){
 const ctx=await browser.newContext({viewport,serviceWorkers:'block'}),errors=[],blocked=[];await ctx.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','manager'));
 await ctx.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')){blocked.push(u.href);return r.abort();}return r.continue();});
 const p=await ctx.newPage();p.setDefaultTimeout(8000);p.on('pageerror',e=>errors.push(e.message));
 try{await p.goto(base+'/?mode=preview');await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
 await p.evaluate(()=>{window.probe={calls:[],fail:null};const api=SalesPreview;window.SalesPreview={...api,request(o){const path=new URL(o.url,location.href).pathname;probe.calls.push({path,method:o.method || 'GET',data:structuredClone(o.data),header:o.header});if(probe.fail&&path.endsWith(probe.fail)){queueMicrotask(()=>o.success?.({statusCode:503,data:{detail:'合成失败，可重试'}}));return {abort(){}};}return api.request(o);}};});
 await run(p);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
 }catch(e){console.error('UPGRADE_DIAGNOSTIC',await p.evaluate(()=>({route:SalesRuntime.current?.route,data:SalesRuntime.current?.data,errors:SalesRuntime.errors,text:document.body.innerText.slice(-2300),calls:probe?.calls.slice(-5)})));throw e;}finally{await ctx.close();}
}
async function go(p,url){await p.evaluate(url=>SalesRuntime.wx.reLaunch({url}),url);await p.waitForFunction(route=>SalesRuntime.current?.route===route,url.split('?')[0].replace(/^\//,''));}
async function shot(p,name){await p.waitForTimeout(350);await p.screenshot({path:`${evidence}/${name}.png`,animations:'disabled'});}
const modal=p=>p.locator('.wx-modal-mask');
const writes=p=>p.evaluate(()=>probe.calls.filter(r=>r.method==='POST'&&!r.path.includes('/notifications/')));
test('team/name/account picker creates independent tasks; cancelled confirm and failed submission preserve all input',()=>fixture(async p=>{
 await go(p,'/pages/management-task-create/index');await p.waitForFunction(()=>SalesRuntime.current.data.members?.length>0&&!SalesRuntime.current.data.recipientLoading);
 await p.getByPlaceholder('输入任务描述').fill('合成验收：整理客户需求并提交方案，验证多人独立任务。');await p.getByRole('button',{name:'选择任务负责人，可多选'}).click();
 await p.getByRole('dialog').waitFor();assert.equal(await p.getByRole('combobox',{name:'筛选团队'}).count(),1);
 await p.getByRole('textbox',{name:'搜索人员'}).fill('PREVIEW_SALES');await p.waitForFunction(()=>document.querySelectorAll('.ds-person-row').length===1);await p.locator('.ds-person-row input').click();await p.waitForFunction(()=>SalesRuntime.current.selectComponent('#personPicker').data.draft.length===1);
 await p.getByRole('textbox',{name:'搜索人员'}).fill('PREVIEW_SUPERVISOR');await p.waitForFunction(()=>document.querySelectorAll('.ds-person-row').length===1&&document.querySelector('.ds-person-row').textContent.includes('PREVIEW_SUPERVISOR'));await p.locator('.ds-person-row input').click();await p.waitForFunction(()=>SalesRuntime.current.selectComponent('#personPicker').data.draft.length===2);await shot(p,'task-people');
 await p.getByRole('button',{name:'确认选择',exact:true}).click();assert.equal(await p.evaluate(()=>SalesRuntime.current.data.selectedMembers.length),2);
 await p.getByRole('button',{name:'下发任务',exact:true}).click();await modal(p).getByRole('button',{name:'取消',exact:true}).click();assert.equal((await writes(p)).length,0);
 await p.evaluate(()=>probe.fail='/tasks/batch');await p.getByRole('button',{name:'下发任务',exact:true}).click();await modal(p).getByRole('button',{name:'确认下发'}).click();await p.getByRole('button',{name:'重试确认发送结果'}).waitFor();
 assert.equal(await p.evaluate(()=>SalesRuntime.current.data.selectedMembers.length),2);await p.evaluate(()=>probe.fail=null);await p.getByRole('button',{name:'重试确认发送结果'}).click();
 await p.waitForFunction(()=>SalesRuntime.wx.getStorageSync('lastManagementTaskCreated')?.ids?.length===2);
 const calls=(await writes(p)).filter(r=>r.path.endsWith('/tasks/batch'));assert.equal(calls.length,2);assert.deepEqual(calls[0].data,calls[1].data);assert.equal(calls[0].header['Idempotency-Key'],calls[1].header['Idempotency-Key']);assert.equal(new Set(calls[1].data.tasks.map(t=>t.assignee_account_code)).size,2);
}));
test('task cancellation requires reason and server version; transfer uses stable candidate and retains event history',()=>fixture(async p=>{
 await go(p,`/pages/task-detail/index?id=${uid(15)}`);await p.waitForFunction(()=>SalesRuntime.current.data.task?.id&&!SalesRuntime.current.data.loading);
 await p.getByRole('button',{name:'协调任务',exact:true}).click();await p.getByRole('button',{name:'选择接手人员',exact:true}).click();await p.getByRole('dialog').waitFor();await p.locator('.ds-person-row input').last().click();await p.waitForFunction(()=>SalesRuntime.current.selectComponent('#personPicker').data.draft.length===1);await p.getByRole('button',{name:'确认选择',exact:true}).click();
 await p.getByPlaceholder('请说明转交原因').fill('合成验收：由技术负责人协助完成交接。');await p.getByRole('button',{name:'转交任务',exact:true}).click();await modal(p).getByRole('button',{name:'确定',exact:true}).click();
 await p.waitForFunction(()=>SalesRuntime.current.data.task?.last_event_type==='reassign');assert.match(await p.locator('.ds-td-history').innerText(),/→/);await shot(p,'task-transfer');
 await p.getByRole('button',{name:'取消任务',exact:true}).click();await p.getByRole('button',{name:'核对并取消任务'}).waitFor();await p.waitForFunction(()=>SalesRuntime.current.selectComponent('task-cancel-dialog')?.data.canSubmit);
 await p.getByRole('button',{name:'核对并取消任务'}).click();await p.getByText('请填写取消原因。',{exact:true}).waitFor();
 await p.getByRole('textbox',{name:'取消原因'}).fill('合成取消：需求调整，不再需要继续执行。');await shot(p,'task-cancel');await p.getByRole('button',{name:'核对并取消任务'}).click();await modal(p).getByRole('button',{name:'确认取消',exact:true}).click();
 await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='cancelled');assert.match(await p.locator('.ds-td-stage').innerText(),/已取消/);assert.equal(await p.locator('.ds-td-side .ant-progress').count(),0);
 const events=(await writes(p)).filter(r=>r.path.endsWith('/events'));assert.equal(events.length,2);assert.equal(events[1].data.version_no,events[0].data.version_no+1);assert.ok(events[1].header['Idempotency-Key']);
}));
test('large amount shows exact yuan and company rule; returning does not save, retry keeps input and acknowledgment matches',()=>fixture(async p=>{
 await go(p,`/pages/opportunity-create/index?customerId=${uid(10)}`);await p.waitForFunction(()=>SalesRuntime.current.selectComponent('#opportunityForm')?.data.stageOptions?.length || SalesRuntime.current.selectComponent('#opportunityForm')?.data.stages?.length);
 await p.getByPlaceholder('填写项目名，可用部门、场景区分').fill('合成大额金额验证项目');await p.getByRole('textbox',{name:'ACV（万元）',exact:true}).fill('1000.123456');
 // Remaining required controls use the same native component handlers as the visible controls.
 await p.evaluate(()=>{const f=SalesRuntime.current.selectComponent('#opportunityForm');f.stage({detail:{value:0}});f.date({detail:{value:'2027-01-15'}});f.partnerMode({detail:{value:0}});});
 await p.getByText(/折合 10,001,234.56 元/).waitFor();await p.getByRole('button',{name:'确认保存商机'}).click();await modal(p).getByText('请核对商机金额',{exact:true}).waitFor();await shot(p,'amount-confirm');await modal(p).getByRole('button',{name:'返回修改'}).click();assert.equal((await writes(p)).filter(r=>r.path.endsWith('/opportunities')).length,0);
 await p.evaluate(()=>probe.fail='/company-rules/presentation');await p.getByRole('button',{name:'确认保存商机'}).click();await p.getByText('金额核对规则加载失败，表单已保留，请重试保存',{exact:true}).waitFor();assert.equal(await p.getByRole('textbox',{name:'ACV（万元）',exact:true}).inputValue(),'1000.123456');
 await p.evaluate(()=>probe.fail=null);await p.getByRole('button',{name:'确认保存商机'}).click();await modal(p).getByRole('button',{name:'金额正确'}).click();await p.waitForFunction(()=>probe.calls.some(c=>c.method==='POST'&&c.path.endsWith('/opportunities')));
 const saved=(await writes(p)).find(r=>r.path.endsWith('/opportunities'));assert.equal(saved.data.amount,10001234.56);assert.equal(saved.data.amount_confirmed_value,saved.data.amount);assert.ok(saved.header['Idempotency-Key']);
}));
test('company entry shows exact name, switching invalidates old session and requires fresh login',()=>fixture(async p=>{
 await go(p,'/pages/login/index?company=demo2');await p.locator('.ds-company-entry').getByText('第二演示公司',{exact:true}).waitFor();await p.getByRole('button',{name:'切换公司并重新登录',exact:true}).waitFor();assert.match(await p.locator('.ds-company-entry').innerText(),/第二演示公司/);await shot(p,'company-switch');
 await p.getByRole('button',{name:'切换公司并重新登录',exact:true}).click();await modal(p).getByRole('button',{name:'确认切换'}).click();await p.waitForFunction(()=>!SalesRuntime.app.globalData.session&&SalesRuntime.current.data.workspace==='demo2');assert.equal(await p.getByPlaceholder('请输入密码').inputValue(),'');
}));
for(const width of [1440,1366,1024,390])test(`responsive ${width}: map/list, task picker, login and keyboard`,()=>fixture(async p=>{
 await go(p,'/pages/customers/index');await p.waitForFunction(()=>!SalesRuntime.current.data.mapLoading&&!SalesRuntime.current.data.assetLoading);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.customers.length),await p.evaluate(()=>SalesRuntime.current.data.plotCustomers.length));await shot(p,`customers-${width}`);if(width===390){await p.locator('.ds-customer-map-panel').scrollIntoViewIfNeeded();assert.ok((await p.locator('.ds-customer-workspace-wrap').boundingBox()).height>340);await shot(p,'customers-390-map');await p.locator('.ds-customer-list').scrollIntoViewIfNeeded();await shot(p,'customers-390-list');}
 const layout=await p.locator('.ds-customer-workspace').evaluate(el=>({width:el.clientWidth,map:el.querySelector('aside').getBoundingClientRect().width,list:el.querySelector('.ds-customer-list').getBoundingClientRect().width}));if(width>900){assert.ok(Math.abs(layout.map/(layout.map+layout.list)-.42)<.02);const pager=p.locator('.ds-customer-list .ant-pagination');if(await pager.count()){const box=await pager.boundingBox();assert.ok(box.y+box.height<=900,'customer pagination is visible');}}
 await go(p,'/pages/management-task-create/index');await p.getByRole('button',{name:'选择任务负责人，可多选'}).click();await p.getByRole('textbox',{name:'搜索人员'}).fill('PREVIEW');await p.getByRole('textbox',{name:'搜索人员'}).press('Tab');assert.ok(await p.getByRole('dialog').evaluate(el=>el.contains(document.activeElement)));await shot(p,`task-picker-${width}`);await p.keyboard.press('Escape');await p.getByRole('dialog').waitFor({state:'hidden'});
 assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
}, {width,height:width===390?844:900}));

 test('map update failure hides incomplete points; retry restores consistent list and ranking expands',()=>fixture(async p=>{
 await p.evaluate(()=>{const api=SalesPreview;probe.mapMode='incomplete';window.SalesPreview={...api,request(o){const path=new URL(o.url,location.href).pathname;if(path.endsWith('/customer-assets/map')&&probe.mapMode==='incomplete'){const done=o.success;o={...o,success(r){if(r.data?.items)r={...r,data:{...r.data,items:r.data.items.map(x=>({...x,potential_score:null,relationship_score:null}))}};done?.(r);}};}return api.request(o);}};probe.fail='/customer-assets/map/refresh';});
 await go(p,'/pages/customers/index');await p.waitForFunction(()=>!!SalesRuntime.current.data.mapError);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.plotCustomers.length),0);await shot(p,'map-failed');
 await p.evaluate(()=>{probe.mapMode='ready';probe.fail=null;});await p.locator('.ds-map-status').getByRole('button').click();await p.waitForFunction(()=>!SalesRuntime.current.data.mapLoading&&!SalesRuntime.current.data.mapError&&SalesRuntime.current.data.plotCustomers.length>0);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.customers.length),await p.evaluate(()=>SalesRuntime.current.data.plotCustomers.length));
 await go(p,'/pages/bi/index');await p.getByRole('button',{name:'展开完整榜单',exact:true}).first().click();await p.getByRole('button',{name:'收起榜单',exact:true}).waitFor();await shot(p,'ranking-expanded');
}));
 test('visit final archive failure retains reviewed input and retries with the same idempotency key',()=>fixture(async p=>{
 await go(p,'/pages/visit-entry/index');await p.locator('.ds-ve-results button').first().click();
 await p.locator('.ds-ve-textarea textarea,textarea.ds-ve-textarea').first().fill('沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送给客户。\n跟进日期：2026-09-29\n对接人：合成UI联系人');await p.getByRole('button',{name:'提交结构化',exact:true}).click();await p.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.values.contact_name==='合成UI联系人');
 await p.getByRole('button',{name:'下一步 · AI 质检',exact:true}).click();await p.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);
 await p.evaluate(()=>probe.fail='/visits');await p.getByRole('button',{name:'确认保存',exact:true}).click();await modal(p).getByRole('button',{name:'确认归档',exact:true}).click();await p.waitForFunction(()=>!SalesRuntime.current.data.busy&&probe.calls.some(c=>c.method==='POST'&&c.path.endsWith('/visits')));assert.equal(await p.evaluate(()=>SalesRuntime.current.data.values.contact_name),'合成UI联系人');await shot(p,'visit-archive-retry');
 await p.evaluate(()=>probe.fail=null);await p.getByRole('button',{name:'确认保存',exact:true}).click();await modal(p).getByRole('button',{name:'确认归档',exact:true}).click();await p.waitForFunction(()=>SalesRuntime.current.data.archived);await shot(p,'visit-archived');
 const calls=(await writes(p)).filter(c=>c.path.endsWith('/visits'));assert.equal(calls.length,2);assert.deepEqual(calls[0].data,calls[1].data);assert.equal(calls[0].header['Idempotency-Key'],calls[1].header['Idempotency-Key']);
}));

test('pending handover, pending review and revoked cancellation permission remain distinct',()=>fixture(async p=>{
 await p.evaluate(()=>{const api=SalesPreview;probe.taskState={status:'pending_confirm',handover_required:true,can_cancel:true};window.SalesPreview={...api,request(o){if(new URL(o.url,location.href).pathname.match(/\/tasks\/[^/]+$/)&&(!o.method||o.method==='GET')){const done=o.success;o={...o,success(r){done?.({...r,data:{...r.data,...probe.taskState}});}};}return api.request(o);}};});
 await go(p,`/pages/task-detail/index?id=${uid(15)}`);await p.locator('.ds-td-tags').filter({hasText:'待交接'}).waitFor();assert.equal(await p.evaluate(()=>SalesRuntime.current.data.task.canComplete),false);await shot(p,'task-handover');
 await p.evaluate(()=>probe.taskState={status:'pending_review',handover_required:false,can_cancel:true});await go(p,`/pages/task-detail/index?id=${uid(15)}`);await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='pending_review');assert.equal(await p.getByRole('button',{name:'取消任务',exact:true}).count(),0);assert.equal(await p.evaluate(()=>SalesRuntime.current.data.task.canCancel),false);
 await p.evaluate(()=>probe.taskState={status:'in_progress',handover_required:false,can_cancel:false});await go(p,`/pages/task-detail/index?id=${uid(15)}`);await p.waitForFunction(()=>SalesRuntime.current.data.task?.status==='in_progress');assert.equal(await p.getByRole('button',{name:'取消任务',exact:true}).count(),0);assert.equal((await writes(p)).length,0);
}));
