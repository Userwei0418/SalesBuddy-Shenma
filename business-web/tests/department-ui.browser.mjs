/** Real installed department packages over the existing Page and synthetic API. */
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser,server,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>{server.close(r);server.closeAllConnections();});});
async function fixture(run,{width=1366,height=768,role='sales'}={}) {
 const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block'}),page=await context.newPage(),errors=[],requests=[];
 await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){requests.push(u.pathname);return r.abort();}return r.continue();});
 const go=async key=>{await page.goto(`${base}/?mode=preview#/pages/${key}/index`);await page.waitForFunction(route=>window.SalesRuntime?.current?.route===route,`pages/${key}/index`);await page.locator(`[data-department-page="pages/${key}/index"]`).waitFor();};
 try {await run({page,go});assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);}finally{await context.close();}
}
for(const role of ['sales','supervisor','manager','fde','fde_lead']) test(`${role} sees the department overview with original scope and task drilldown`,{timeout:35000},()=>fixture(async({page,go})=>{
 await go('index');await page.waitForFunction(()=>SalesRuntime.current.data.overviewMetrics?.some(m=>m.key==='all_pending'));
 const before=await page.evaluate(()=>SalesRuntime.current.data.overviewMetrics.find(m=>m.key==='all_pending').value);
 const card=page.getByRole('button',{name:new RegExp(`全部待办 ${before}，查看任务`)});await card.click();
 await page.locator('[data-department-page="pages/tasks/index"]').waitFor();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.overviewFilter),'all_pending');
 await page.getByRole('tab',{name:/已完成/}).click();
 await page.waitForFunction(()=>!SalesRuntime.current.data.loading&&SalesRuntime.current.data.activeTab==='completed');
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.overviewFilter),'');
 assert.equal(new URL(page.url()).hash.includes('overview='),false);
},{role}));
for (const size of [{width:1440,height:900},{width:1366,height:768},{width:390,height:844}]) test(`${size.width}px customer map/table filter together and native detail roundtrip remains usable`,{timeout:35000},()=>fixture(async({page,go})=>{
 await go('customers');await page.waitForFunction(()=>SalesRuntime.current.visibleCustomers?.length>0&&!SalesRuntime.current.data.assetLoading);
 const initial=await page.evaluate(()=>({count:SalesRuntime.current.data.customers.length,acv:SalesRuntime.current.data.acvText,recognized:SalesRuntime.current.data.recognizedText}));
 await page.getByRole('button',{name:'放大主攻区',exact:true}).click();
 await page.waitForFunction(()=>SalesRuntime.current.data.quadrantIndex===1);
 await page.waitForFunction(()=>document.querySelectorAll('.ds-map-point').length===SalesRuntime.current.data.customers.length);
 assert.equal(await page.locator('.ds-map-point').count(),await page.evaluate(()=>SalesRuntime.current.data.customers.length));
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.acvText),initial.acv);
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.recognizedText),initial.recognized);
 await page.getByRole('button',{name:'返回全部象限',exact:true}).last().click();
 await page.getByPlaceholder('搜索客户或负责人').fill('星河');
 await page.waitForFunction(()=>SalesRuntime.current.data.customers.length===1);
 const name=await page.locator('.ds-customer-name').first().innerText();await page.locator('.ds-customer-name').first().click();
 await page.waitForFunction(name=>SalesRuntime.current.data.selectedCustomer?.name===name,name);
 assert.equal(await page.locator('[data-department-page]').count(),0,'original detail owns full business actions');
 await page.locator('[data-handler="closeCustomer"]').first().click();
 await page.locator('[data-department-page="pages/customers/index"]').waitFor();
 assert.equal(await page.getByPlaceholder('搜索客户或负责人').inputValue(),'星河');
 await page.getByRole('button',{name:'清除筛选',exact:true}).click();
 await page.waitForFunction(n=>SalesRuntime.current.data.customers.length===n,initial.count);
 if(size.width>900){const root=await page.locator('#page-root').evaluate(el=>({client:el.clientHeight,scroll:el.scrollHeight}));assert.ok(root.scroll<=root.client+2,`customer layout should fit desktop: ${JSON.stringify(root)}`);}
},size));
test('manager clearing scope reloads actuals while operating filters keep totals independent',{timeout:35000},()=>fixture(async({page,go})=>{
 await go('customers');await page.waitForFunction(()=>SalesRuntime.current.visibleCustomers?.length>0&&!SalesRuntime.current.data.assetLoading&&!SalesRuntime.current.data.directoryLoading);
 const expected=await page.evaluate(()=>({recognized:SalesRuntime.current.data.recognizedText,collection:SalesRuntime.current.data.collectionText}));
 await page.getByRole('combobox',{name:'团队',exact:true}).click();await page.getByText('示例空团队',{exact:true}).last().click();
 await page.waitForFunction(()=>!SalesRuntime.current.data.assetLoading&&SalesRuntime.current.data.selectedTeam!=='all');
 await page.getByRole('button',{name:'清除筛选',exact:true}).click();
 await page.waitForFunction(expected=>!SalesRuntime.current.data.assetLoading&&SalesRuntime.current.data.selectedTeam==='all'&&SalesRuntime.current.data.recognizedText===expected.recognized&&SalesRuntime.current.data.collectionText===expected.collection,expected);
},{role:'manager'}));
test('overlapping map points use the original candidate list and selected customer identity',{timeout:35000},()=>fixture(async({page,go})=>{
 await go('customers');await page.waitForFunction(()=>SalesRuntime.current.data.plotCustomers?.length>1);
 const names=await page.evaluate(()=>{const p=SalesRuntime.current,a=p.data.plotCustomers.slice(0,2);p.setData({plotCustomers:a.map(c=>({...c,plotStyle:'left:50%;bottom:50%;'}))});return a.map(c=>c.name);});
 await page.locator('.ds-map-point').last().click();await page.getByRole('dialog',{name:'选择客户'}).waitFor();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.plotCandidates.length),2);
 await page.getByRole('dialog').getByRole('button',{name:new RegExp(names[0].replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
 await page.waitForFunction(name=>SalesRuntime.current.data.selectedBattleCustomer?.name===name,names[0]);
 assert.equal(await page.locator('[data-department-page]').count(),0);
}));

// Large directories use available desktop height; the chart remains the full filtered set.
for (const role of ['manager','fde']) test(`${role} customer workspace fills height and pagination adapts without clipping long rows`,{timeout:35000},()=>fixture(async({page,go})=>{
 await go('customers');await page.waitForFunction(()=>SalesRuntime.current.data.customers?.length>0&&!SalesRuntime.current.data.assetLoading&&!SalesRuntime.current.data.directoryLoading);
 await page.evaluate(()=>{
  const p=SalesRuntime.current, seed=p.data.customers[0], point=p.data.plotCustomers[0];
  const customers=Array.from({length:267},(_,i)=>({...seed,id:`layout-${i}`,name:`【布局验证】客户 ${i+1} · 长名称与跨区域协作项目`,team:'示例跨区域协作团队',owner:'示例负责人',risk:'示例风险：采购安排与交付节点需要进一步确认，完整内容可以展开查看。',signal:{tone:'yellow',label:'提醒'},fde_members:[{id:'synthetic-fde',name:'示例协助成员'}]}));
  p.setData({customers,plotCustomers:customers.map((c,i)=>({...point,...c,plotLabel:c.name,plotStyle:`left:${5+(i*17)%90}%;bottom:${5+(i*31)%90}%;`}))});
 });
 await page.waitForFunction(()=>document.querySelector('.ds-customer-count')?.textContent==='267');
 const geometry=()=>page.evaluate(()=>{
  const box=s=>document.querySelector(s).getBoundingClientRect();
  const root=document.querySelector('#page-root'),viewport=document.querySelector('.ds-customer-table-viewport');
  return {size:SalesRuntime.current._departmentCustomerPageSize,mapBottom:box('.ds-customer-map').bottom,listBottom:box('.ds-customer-list').bottom,viewportOverflow:viewport.scrollHeight-viewport.clientHeight,rootOverflow:root.scrollHeight-root.clientHeight,footerBottom:box('.ds-customer-list footer').bottom,height:innerHeight};
 });
 await page.waitForFunction(()=>SalesRuntime.current._departmentCustomerPageSize>=5);
 const small=await geometry();
 assert.ok(small.viewportOverflow<=2,JSON.stringify(small));assert.ok(small.rootOverflow<=2,JSON.stringify(small));assert.ok(small.footerBottom<=small.height,JSON.stringify(small));
 assert.ok(Math.abs(small.listBottom-small.mapBottom-12)<=2,'chart fills the column down to the bottom padding');
 assert.equal(await page.locator('.ds-map-point').count(),267);
 await page.locator('.ant-pagination-item-2').click();
 const before=await page.locator('.ds-customer-name').first().innerText();
 await page.setViewportSize({width:1440,height:900});
 await page.waitForFunction(size=>SalesRuntime.current._departmentCustomerPageSize>size,small.size);
 const large=await geometry();assert.ok(large.viewportOverflow<=2,JSON.stringify(large));assert.ok(large.rootOverflow<=2,JSON.stringify(large));
 assert.equal(await page.locator('.ds-map-point').count(),267);
 const range=await page.locator('.ds-customer-list footer').innerText();assert.match(range,/267 家/);const firstIndex=Number(before.match(/客户 (\d+)/)[1])-1;
 const resizedPage=await page.evaluate(()=>SalesRuntime.current._departmentCustomerPage);assert.ok(firstIndex>=(resizedPage-1)*large.size&&firstIndex<resizedPage*large.size,'resize keeps the previously first customer on the current page');
 await page.locator('.ant-table-row-expand-icon').first().click();
 await page.locator('.ds-customer-expanded').first().waitFor();
 assert.match(await page.locator('.ds-customer-expanded').first().innerText(),/完整内容可以展开查看/);
 if(role==='fde') assert.match(await page.locator('.ds-customer-expanded').first().innerText(),/示例协助成员/);
 await page.locator('.ant-table-row-expand-icon').first().click();
 await page.setViewportSize({width:1024,height:600});
 await page.waitForFunction(size=>SalesRuntime.current._departmentCustomerPageSize<size,large.size);
 const compact=await geometry();assert.ok(compact.viewportOverflow<=2,JSON.stringify(compact));assert.ok(compact.rootOverflow<=2,JSON.stringify(compact));assert.ok(compact.footerBottom<=compact.height,JSON.stringify(compact));
},{role}));
