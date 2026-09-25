import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));});
async function fixture(fn,{size=[1366,900],role='sales'}={}) {
 const ctx=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block',permissions:['clipboard-read','clipboard-write']});
 await ctx.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
 const errors=[],blocked=[];
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){blocked.push(u.href);return route.abort();}return route.continue();});
 const p=await ctx.newPage();p.setDefaultTimeout(12000);p.on('pageerror',e=>errors.push(e.message));
 try {await p.goto(base+'/?mode=preview#/pages/index/index');await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
 await p.evaluate(()=>{window.weeklyProbe={fail:false,empty:false,requests:[],custom:false};const preview=SalesPreview;window.SalesPreview={...preview,request(o){const u=new URL(o.url,location.href);weeklyProbe.requests.push({path:u.pathname,method:o.method||'GET',search:u.search});if(u.pathname.endsWith('/visits')&&(weeklyProbe.fail||weeklyProbe.empty||weeklyProbe.custom)){
 let items=[];const offset=Number(u.searchParams.get('offset'));const uid=SalesRuntime.app.globalData.session.userId;const base={created_at:new Date().toISOString(),customer_name:'分页客户',follow_up_record:'分页记录事实',next_action:'确认需求'};
 if(weeklyProbe.custom)items=offset===0?[{...base,id:'other',recorder_id:'other-user'},{...base,id:'own-a',recorder_id:uid}]:[{...base,id:'own-b',recorder_id:uid}];
 const res={statusCode:weeklyProbe.fail?503:200,data:weeklyProbe.fail?{message:'合成读取失败'}:{items,has_more:weeklyProbe.custom&&offset===0,next_offset:offset===0?100:null,sort:'created_desc'}};queueMicrotask(()=>{o.success?.(res);o.complete?.(res);});return {abort(){}};}return preview.request(o);}};});
 await fn(p);
 assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
 const writes=await p.evaluate(()=>(window.weeklyProbe?.requests||[]).filter(r=>!['GET','get'].includes(r.method)&&!/^\/api\/v1\/notifications\/[^/]+\/read$/.test(r.path)));assert.deepEqual(writes,[]);
 }catch(e){console.error('WEEKLY_DIAGNOSTIC', await p.evaluate(()=>({route:SalesRuntime.current.route,error:SalesRuntime.current.data.error,errors:SalesRuntime.errors})));throw e;}finally{await ctx.close();}
}
async function go(p){await p.evaluate(()=>SalesRuntime.userRoute('/pages/weekly-report/index'));await p.waitForFunction(()=>SalesRuntime.current.route==='pages/weekly-report/index'&&!SalesRuntime.current.data.loading);await p.locator('.ds-weekly-record').first().waitFor();}
async function generate(p){await p.getByRole('button',{name:'一键生成周报',exact:true}).click();await p.getByLabel('周报正文',{exact:true}).waitFor();await p.waitForFunction(()=>document.querySelector('#weekly-report-body')?.value.includes('本周概览'));}
async function shot(p,name){if(process.env.WEEKLY_SHOTS){await mkdir(process.env.WEEKLY_SHOTS,{recursive:true});await p.waitForTimeout(350);await p.evaluate(()=>{document.querySelectorAll('.ant-message').forEach(e=>e.style.visibility='hidden');window.scrollTo(0,0);});await p.screenshot({path:`${process.env.WEEKLY_SHOTS}/${name}.png`,animations:'disabled',fullPage:true});}}
test('sidebar entry, source scope, generate, edit, copy, refresh restoration, regeneration cancel',()=>fixture(async p=>{
 await p.locator('#frame-sidenav').getByText('周报',{exact:true}).click();await p.waitForFunction(()=>SalesRuntime.current.route==='pages/weekly-report/index'&&!SalesRuntime.current.data.loading);
 await p.locator('.ds-weekly-record').first().waitFor();assert.ok(await p.locator('.ds-weekly-record').count());assert.ok(await p.evaluate(()=>SalesRuntime.current.data.records.every(r=>r.recorder_id===SalesRuntime.app.globalData.session.userId)));
 await shot(p,'01-周报-待生成');await generate(p);await shot(p,'02-周报-已生成');
 const editor=p.getByLabel('周报正文',{exact:true});const original=await editor.inputValue();assert.match(original,/客户与商机进展/);assert.match(original,/整理试点清单/);
 await editor.fill(original+'\n人工补充：需要售前协同。');await p.getByRole('button',{name:'复制周报',exact:true}).click();assert.match(await p.evaluate(()=>navigator.clipboard.readText()),/需要售前协同/);
 await p.getByRole('button',{name:'重新生成周报',exact:true}).click();await p.getByRole('button',{name:'保留当前内容',exact:true}).click();assert.match(await editor.inputValue(),/需要售前协同/);
 await p.reload();await p.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/weekly-report/index'&&!SalesRuntime.current.data.loading);assert.match(await editor.inputValue(),/需要售前协同/);
 await p.getByRole('checkbox',{name:'全选近 14 天素材',exact:true}).uncheck();assert.equal(await p.getByRole('button',{name:'重新生成周报',exact:true}).isDisabled(),true);
 await p.getByRole('checkbox',{name:'全选近 14 天素材',exact:true}).check();await p.getByRole('button',{name:'重新生成周报',exact:true}).click();await p.getByRole('button',{name:'替换并重新生成',exact:true}).click();await p.waitForFunction(()=>!document.querySelector('#weekly-report-body').value.includes('人工补充'));
}));
test('all pages loaded, stable user ID filtered, empty/error/retry and selected-only sources',()=>fixture(async p=>{
 await p.evaluate(()=>weeklyProbe.custom=true);await go(p);assert.equal(await p.locator('.ds-weekly-record').count(),2);
 await p.getByLabel('选择记录 own-a',{exact:true}).uncheck();await generate(p);assert.match(await p.getByLabel('周报正文',{exact:true}).inputValue(),/1 条记录/);
 await p.evaluate(()=>{weeklyProbe.fail=true;SalesRuntime.current.loadRecords();});await p.getByText('更新记录读取失败',{exact:true}).waitFor();await shot(p,'04-周报-读取失败');assert.equal(await p.getByRole('button',{name:'重新生成周报',exact:true}).isDisabled(),true);
 await p.evaluate(()=>{weeklyProbe.fail=false;weeklyProbe.custom=false;weeklyProbe.empty=true;SalesRuntime.current.loadRecords();});await p.getByText('这个周期还没有更新记录',{exact:true}).waitFor();
 await p.evaluate(()=>{weeklyProbe.empty=false;SalesRuntime.current.loadRecords();});await p.waitForFunction(()=>!SalesRuntime.current.data.loading&&SalesRuntime.current.data.records.length>0);await p.locator('.ds-weekly-record').first().waitFor();assert.ok(await p.locator('.ds-weekly-record').count());
}));
for(const size of [[1366,768],[1024,600],[390,844]])test(`weekly workspace fits ${size.join('x')}`,()=>fixture(async p=>{
 await go(p);await generate(p);assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 assert.ok(await p.locator('#weekly-report-body').isVisible());await shot(p,`03-周报-${size.join('x')}`);
},{size}));
test('period switch and navigation preserve separate edited drafts; search does not silently change selection',()=>fixture(async p=>{
 await go(p);await generate(p);const editor=p.getByLabel('周报正文',{exact:true});await editor.fill('本周人工草稿');
 await p.getByLabel('搜索更新记录',{exact:true}).fill('不匹配的关键词');await p.getByText('没有匹配的记录',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'重新生成周报',exact:true}).isEnabled(),true);
 await p.getByRole('combobox',{name:'周报周期',exact:true}).click();await p.locator('.ant-select-item-option').filter({hasText:'上一个自然周'}).click();
 await p.getByRole('button',{name:'一键生成周报',exact:true}).waitFor();await generate(p);assert.notEqual(await editor.inputValue(),'本周人工草稿');
 await p.getByRole('combobox',{name:'周报周期',exact:true}).click();await p.locator('.ant-select-item-option').filter({hasText:'本周（截至当前）'}).click();await p.waitForFunction(()=>document.querySelector('#weekly-report-body')?.value==='本周人工草稿');
 await p.evaluate(()=>SalesRuntime.userRoute('/pages/bi/index'));await p.waitForFunction(()=>SalesRuntime.current.route==='pages/bi/index');await go(p);assert.equal(await editor.inputValue(),'本周人工草稿');
}));
