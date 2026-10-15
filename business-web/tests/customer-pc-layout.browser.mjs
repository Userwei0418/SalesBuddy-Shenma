/** PC 2.0 customer layout through the real Page and access flags; synthetic preview only. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(resolve=>server.close(resolve));});
for(const width of [1440,390])test(`PC customer layout preserves filters, actions and reachable pagination at ${width}px`,{timeout:45000},async()=>{
 const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),page=await context.newPage(),errors=[],blocked=[];
 page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==base||url.pathname.startsWith('/api/')||url.pathname==='/local-login'){blocked.push(url.pathname);return route.abort();}return route.continue();});
 const go=async()=>{await page.goto(base+'/?mode=preview#/pages/customers/index');await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/customers/index'&&SalesRuntime.current.data.plotCustomers?.length>0&&!SalesRuntime.current.data.assetLoading);await page.locator('.ds-customer-name').first().waitFor();};
 try{
  await go();await page.getByRole('heading',{name:'客户经营',exact:true}).waitFor();
  const flags=await page.evaluate(()=>({create:!!SalesRuntime.current.data.canCreateCustomer,request:!!SalesRuntime.current.data.canRequestCustomer,claim:!!SalesRuntime.current.data.canClaimCustomer}));
  const create=page.getByRole('button',{name:flags.request?'客户建档 · 申请与进度':'创建客户',exact:true});
  assert.equal(await create.isVisible(),flags.create);assert.equal(await page.getByRole('button',{name:'客户认领',exact:true}).isVisible(),flags.claim);
  if(flags.create){await create.click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-create/index');await go();}
  if(flags.claim){await page.getByRole('button',{name:'客户认领',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-claim/index');await go();}
  await page.getByPlaceholder('搜索客户或负责人').fill('PC布局无匹配合成客户');await page.waitForFunction(()=>SalesRuntime.current.data.keyword==='PC布局无匹配合成客户');await page.getByText('没有匹配的客户',{exact:true}).waitFor();
  await page.getByRole('button',{name:'清除筛选',exact:true}).first().click();await page.waitForFunction(()=>!SalesRuntime.current.data.keyword&&SalesRuntime.current.data.customers.length>0);
  const more=page.getByRole('button',{name:/^更多筛选/});if(await more.isVisible()){await more.click();await page.getByRole('group',{name:'更多筛选',exact:true}).waitFor();assert.ok(await page.locator('.pc-filterbar-overflow .pc-filterbar-item').count()>0);await page.getByRole('button',{name:/^收\s*起$/}).click();}
  await page.getByRole('combobox',{name:'客户每页条数',exact:true}).scrollIntoViewIfNeeded();await page.getByRole('combobox',{name:'客户每页条数',exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:'20 条'}).click();await page.waitForFunction(()=>SalesRuntime.current._departmentCustomerPageSize===20);
  await page.getByRole('button',{name:/^待评估 \d+ 家$/}).click();await page.getByRole('heading',{name:/^待评估客户/}).waitFor();await page.getByRole('button',{name:'返回已定位客户',exact:true}).click();
  const geometry=await page.evaluate(()=>{const box=s=>document.querySelector(s).getBoundingClientRect().toJSON();return{map:box('.sb-bmap'),list:box('.ds-customer-list'),workbench:box('.ds-customer-workbench'),overflow:document.documentElement.scrollWidth-innerWidth};});
  assert.ok(geometry.map.height>200,'map remains usable');assert.ok(geometry.list.bottom<=geometry.workbench.bottom+2,'the workbench contains the list');assert.ok(geometry.overflow<=1,'no document horizontal overflow');
  await page.getByRole('combobox',{name:'客户每页条数',exact:true}).scrollIntoViewIfNeeded();const pager=await page.getByRole('combobox',{name:'客户每页条数',exact:true}).boundingBox();assert.ok(pager&&pager.y>=0&&pager.y+pager.height<=1000,'pagination is reachable');
  await page.keyboard.press('Escape');await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});await page.evaluate(()=>{window.scrollTo(0,0);document.querySelectorAll('#page-root,[data-page-scroll],#page').forEach(el=>el.scrollTo(0,0));return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});await mkdir('/tmp/shenma-pc-pages',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-pages/customers-${width}.png`,fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
 }catch(error){await mkdir('/tmp/shenma-pc-pages',{recursive:true});await page.screenshot({path:`/tmp/shenma-pc-pages/customer-failure-${width}.png`,fullPage:true});throw error;}finally{await context.close();}
});
