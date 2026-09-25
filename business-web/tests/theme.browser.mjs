/* Theme behavior uses a fresh synthetic browser and rejects all business network writes. */
import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server.close(r));});
async function fixture(run,width=1440){
 const ctx=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),errors=[],business=[];
 await ctx.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base||u.pathname.startsWith('/api/')||u.pathname==='/local-login'){business.push(u.pathname);return route.abort();}return route.continue();});
 const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));p.setDefaultTimeout(8000);
 try{await p.goto(base+'/?mode=preview');await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session);await run(p,ctx);assert.deepEqual(errors,[]);assert.deepEqual(business,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);}finally{await ctx.close();}
}
async function go(p,name){await p.evaluate(n=>SalesRuntime.route('/pages/'+n+'/index',{tab:true}),name);await p.waitForFunction(n=>SalesRuntime.current.route==='pages/'+n+'/index'&&!SalesRuntime.current.data.loading,name);}
async function readable(locator){
 await locator.evaluate(async el=>{await Promise.allSettled(el.getAnimations().filter(a=>Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a=>a.finished));});
 const result=await locator.evaluate(el=>{const lum=s=>{const c=s.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);return c.reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);};let bg=el;while(getComputedStyle(bg).backgroundColor==='rgba(0, 0, 0, 0)'&&bg.parentElement)bg=bg.parentElement;const foreground=lum(getComputedStyle(el).color),background=lum(getComputedStyle(bg).backgroundColor);return {contrast:(Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05),fill:getComputedStyle(bg).backgroundColor};});
 assert.ok(result.contrast>=4.5,`${await locator.getAttribute('class')}: ${JSON.stringify(result)}`);return result;
}
const chartFacts=()=>{const chart=echarts.getInstanceByDom(document.querySelector('.web-echart')),option=chart.getOption();return{id:chart.id,values:option.series[0].data.map(d=>d.value),bar:option.series[0].data[0].itemStyle.color,axis:option.yAxis[0].axisLabel.color};};
async function lightWorkspace(p){
 const colors=await p.evaluate(()=>['.web-main','.web-topbar','.web-kpi-primary .kpi-card','.chart-card'].map(selector=>({selector,rgb:getComputedStyle(document.querySelector(selector)).backgroundColor.match(/[\d.]+/g).slice(0,3).map(Number)})));
 assert.ok(colors.every(c=>c.rgb.every(v=>v>=230)),JSON.stringify(colors));
 assert.equal(await p.locator('meta[name="color-scheme"]').getAttribute('content'),'light');
}
test('navy navigation and light workspace preserve route, chart data and instances across theme changes',()=>fixture(async p=>{
 assert.equal(await p.evaluate(()=>SalesTheme.current),'navy');await go(p,'bi');await p.waitForFunction(()=>SalesDashboard.count()===3);await lightWorkspace(p);
 assert.equal(await p.locator('.web-sidebar .web-brand-logo').getAttribute('src'),'assets/brand/raccoon-salesbuddy-horizontal-white-sidebar.svg');
 assert.ok(await p.locator('.web-sidebar').evaluate(el=>getComputedStyle(el).backgroundColor.match(/[\d.]+/g).slice(0,3).map(Number).every(v=>v<100)));
 const navy=await p.evaluate(chartFacts);assert.equal(navy.bar,'#4d84dc');
 const before=await p.evaluate(()=>({route:location.hash,kpis:JSON.stringify(SalesRuntime.current.data.kpis)}));
 await p.locator('#theme-toggle').click();assert.equal(await p.evaluate(()=>SalesTheme.current),'light');
 const light=await p.evaluate(chartFacts);assert.deepEqual(light,navy);await lightWorkspace(p);
 assert.deepEqual(await p.evaluate(()=>({route:location.hash,kpis:JSON.stringify(SalesRuntime.current.data.kpis)})),before);
 assert.equal(await p.evaluate(()=>localStorage.getItem('sales-web:color-theme')),'light');
 await p.reload();await p.waitForFunction(()=>window.SalesTheme?.current==='light'&&window.SalesRuntime?.current?.route==='pages/bi/index');
 assert.equal(await p.locator('.web-brand-logo').first().getAttribute('src'),'assets/brand/raccoon-salesbuddy-horizontal-navy-sidebar.svg');
 await p.locator('#theme-toggle').focus();await p.keyboard.press('Enter');assert.equal(await p.evaluate(()=>SalesTheme.current),'navy');
 const contrast=await p.locator('.web-kpi-primary .kpi-card').first().evaluate(card=>{const rgb=s=>s.match(/[\d.]+/g).slice(0,3).map(Number),lum=c=>c.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);const bg=lum(rgb(getComputedStyle(card).backgroundColor));return Array.from(card.children).map(el=>{const fg=lum(rgb(getComputedStyle(el).color));return (Math.max(fg,bg)+.05)/(Math.min(fg,bg)+.05);});});assert.ok(contrast.every(c=>c>=4.5),JSON.stringify(contrast));
}));
test('switching themes retains a typed visit draft and keeps the transparent date trigger readable',()=>fixture(async p=>{
 await go(p,'visit-entry');await p.locator('.customer-result').filter({hasText:'星河制造'}).click();
 await p.locator('.note-input').fill('沟通内容：核对客户验收范围。\n下一步计划：2026年9月20日由我发送方案。\n跟进日期：2026-09-16\n对接人：合成测试联系人');
 await p.locator('[data-handler="submitTranscript"]').click();await p.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.core.length);
 const input=p.locator('textarea[data-key="next_action"]'),text='2026年9月21日由我确认验收清单';await input.fill(text);await input.evaluate(el=>window.draftInput=el);
 await p.locator('#theme-toggle').click();await p.locator('#theme-toggle').click();
 assert.equal(await input.inputValue(),text);assert.equal(await input.evaluate(el=>el===window.draftInput),true);
 assert.equal(await p.evaluate(()=>SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey).values.next_action),text);
 const trigger=p.locator('.wx-temporal-trigger').first();assert.equal(await trigger.evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
 await trigger.click();await p.locator('.wx-date-dialog').waitFor();
 await readable(p.locator('.wx-date-dialog'));await readable(p.locator('.wx-date-apply'));await readable(p.locator('.wx-date-cancel'));
 await p.keyboard.press('Escape');assert.equal(await input.inputValue(),text);
}));
for(const width of [1440,390,320])test(`${width}px light workspace and theme switch fit without horizontal overflow`,()=>fixture(async p=>{
 for(const route of ['customers','workbench','tasks','visit-entry','bi']){
  await go(p,route);if(route==='bi')await p.waitForFunction(()=>SalesDashboard.count()>0);
  await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),route);
  const toggle=await p.locator('#theme-toggle').boundingBox();assert.ok(toggle&&toggle.x>=0&&toggle.x+toggle.width<=width,route);
 }
 await p.locator('.quarter-picker').first().click();await p.locator('#web-select-dialog').waitFor();
 await readable(p.locator('#web-select-dialog'));await readable(p.locator('#web-select-dialog .option.selected b').first());await readable(p.locator('#web-select-dialog .option.selected small').first());
},width));
test('light controls distinguish selection, hover and keyboard focus with readable text',()=>fixture(async p=>{
 await go(p,'bi');await readable(p.locator('#desktop-nav .active'));await readable(p.locator('.quarter-picker'));
 const primary=p.locator('.web-create-button'),rest=await readable(primary);await primary.hover();const hover=await readable(primary);assert.notEqual(rest.fill,hover.fill);
 await primary.focus();assert.ok(await primary.evaluate(el=>{const s=getComputedStyle(el);return s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>=2;}));
 const before=await p.evaluate(()=>JSON.stringify(SalesRuntime.current.data.selectedQuarter));
 await p.locator('.quarter-picker').first().click();const selected=p.locator('#web-select-dialog .web-select-main .option.selected').first(),unselected=p.locator('#web-select-dialog .web-select-main .option:not(.selected)').first();
 assert.notEqual((await readable(selected)).fill,(await readable(unselected)).fill);await selected.hover();await readable(selected.locator('b'));await readable(selected.locator('small'));await readable(p.locator('.web-select-apply'));
 await p.keyboard.press('Escape');assert.equal(await p.evaluate(()=>JSON.stringify(SalesRuntime.current.data.selectedQuarter)),before);
 await go(p,'tasks');await readable(p.locator('.web-task-tab.active'));await readable(p.locator('.web-task-sort'));await readable(p.locator('.web-task-action').first());await readable(p.locator('.web-task-priority').first());await readable(p.locator('.web-task-state>text').first());
 await p.locator('#workspace-switch').click();await p.locator('#workspace-dialog').waitFor();assert.equal(await p.locator('#workspace-dialog .web-brand-logo').getAttribute('src'),'assets/brand/raccoon-salesbuddy-horizontal-navy-sidebar.svg');for(const selector of ['#choose-live b','#choose-live small','#choose-preview b','#choose-preview small'])await readable(p.locator(selector));await p.keyboard.press('Escape');
}));
for(const legacy of ['url','saved'])test(`legacy dark ${legacy} opens navy navigation with light content`,()=>fixture(async p=>{
 if(legacy==='saved'){await p.evaluate(()=>localStorage.setItem('sales-web:color-theme','dark'));await p.reload();}
 await p.goto(base+'/?mode=preview'+(legacy==='url'?'&theme=dark&ui=20260916-dark':'')+'#/pages/bi/index');
 await p.waitForFunction(()=>window.SalesDashboard?.count()>0&&!SalesRuntime.current.data.loading);
 assert.equal(await p.evaluate(()=>SalesTheme.current),'navy');assert.equal(await p.evaluate(()=>localStorage.getItem('sales-web:color-theme')),'navy');await lightWorkspace(p);
 if(legacy==='url')assert.equal(new URL(p.url()).searchParams.get('theme'),'navy');
 await p.reload();await p.waitForFunction(()=>window.SalesDashboard?.count()>0&&!SalesRuntime.current.data.loading);await lightWorkspace(p);
}));
