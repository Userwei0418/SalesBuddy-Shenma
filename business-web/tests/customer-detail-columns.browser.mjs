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
 try {await p.goto(base+'/?mode=preview');await p.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);await run(p);assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);}
 finally{await c.close();}
}
async function paint(p){await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function go(p,embedded){
 await p.evaluate(({customer,embedded})=>SalesRuntime.wx.navigateTo({url:embedded?'/pages/customers/index':'/pages/customer-detail/index?id='+customer}),{customer,embedded});
 if(embedded){await p.waitForFunction(()=>!SalesRuntime.current.data.loading);await p.evaluate(id=>SalesRuntime.current.showCustomerDetail(id),customer);}
 await p.waitForFunction(()=>SalesRuntime.current.data.detailSummary?.loaded && (SalesRuntime.current.data.selectedCustomer || SalesRuntime.current.data.customer));await paint(p);
}
async function fitted(p){assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);if(p.viewportSize().width>600)assert.equal(await p.locator('#page-root').evaluate(e=>e.scrollHeight<=e.clientHeight+2),true);}

for(const size of [[1180,650],[1366,768],[1700,800],[1920,900],[1024,600],[630,800],[390,844]])for(const embedded of [true,false])test(`customer ${embedded?'overlay':'standalone'} ${size.join('x')} column layout and native tabs`,()=>fixture(size,async p=>{
 await go(p,embedded);await fitted(p);
 const root=embedded?'.web-customer-workspace':'.web-standalone-customer';
 if(size[0]>=1180){
  const left=await p.locator('.web-customer-identity').boundingBox(),right=await p.locator('.web-customer-primary').boundingBox();
  assert.equal(Math.round(left.width),268);assert.ok(right.x>=left.x+left.width+15);assert.ok(Math.abs(right.y-left.y)<=1);assert.ok(right.height>size[1]-190);
  assert.equal(await p.locator('.web-detail-main').evaluate(e=>getComputedStyle(e).overflowY),'visible');
  if(size[0]>=1700){const main=await p.locator('.web-detail-main').boundingBox(),aside=await p.locator('.web-detail-aside').boundingBox();assert.ok(aside.x>=main.x+main.width+15);}
  const titleY=(await p.locator('.web-detail-header').boundingBox()).y;
  await p.locator('.web-customer-primary').evaluate(e=>e.scrollTop=e.scrollHeight);await paint(p);
  assert.equal((await p.locator('.web-detail-header').boundingBox()).y,titleY);assert.ok((await p.locator(root+' .detail-tabs').boundingBox()).y<=right.y+2);
 } else assert.equal(await p.locator('.web-customer-detail-frame').evaluate(e=>getComputedStyle(e).display),'contents');
 for(const tab of embedded?['tasks','visits','opportunity','overview']:['visits','opportunity','overview']){
  await p.locator(root+` .detail-tabs [data-tab=${tab}]`).click();await p.waitForFunction(({tab,embedded})=>SalesRuntime.current.data[embedded?'detailTab':'activeTab']===tab,{tab,embedded});await paint(p);await fitted(p);
  if(size[0]>=1180)assert.equal(await p.locator('.web-customer-primary').evaluate(e=>e.scrollTop),0);
 }
 if([1366,1920].includes(size[0])){const dir=new URL('../docs/desktop-forms-20260920/截图/',import.meta.url);await mkdir(dir,{recursive:true});await p.screenshot({path:fileURLToPath(new URL(`客户详情-${embedded?'浮层':'独立'}-${size[0]>=1700?'三':'两'}栏-${size.join('x')}.png`,dir))});}
}));
