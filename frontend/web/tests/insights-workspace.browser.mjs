/** Read-only synthetic coverage for insight and opportunity workspaces. No production requests. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir, readFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const project = new URL('../', import.meta.url);
let browser, server, base;
before(async () => {server=createSalesWebServer({target:'',localLogin:null});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async () => {await browser?.close();await new Promise(r=>server?.close(r));});
const scenarios = [
 {name:'bi',role:'manager'}, {name:'profile',role:'manager'}, {name:'member-growth',role:'manager',query:'?account=salesa@example.com'},
 {name:'fde-records',role:'fde_lead',query:'?context='+encodeURIComponent(JSON.stringify({scope:'team',year:2026,quarters:[3],periodLabel:'2026年第三季度',scopeLabel:'示例团队'}))},
 {name:'risks',role:'manager'}, {name:'opportunities',role:'manager'},
];
async function paint(page){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
async function scrollPane(page){
 for(const node of await page.locator('[data-web-page-scroll]').all()){
  if(await node.evaluate(e=>e.clientHeight>0&&/auto|scroll/.test(getComputedStyle(e).overflowY)&&e.getClientRects().length))return node;
 }
 return page.locator('.web-insights-scroll,.activity-scroll').first();
}
async function session(scenario, size, run) {
 const context=await browser.newContext({viewport:{width:size[0],height:size[1]},serviceWorkers:'block'}),blocked=[],errors=[];
 await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),scenario.role);
 await context.route('**/*',async route=>{const u=new URL(route.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return route.abort();}
  if(process.env.INSIGHTS_SOURCE==='1'){
   if(u.pathname==='/bundle.js')return route.fulfill({contentType:'text/javascript',body:await readFile(new URL('dist/bundle.js',project),'utf8')});
   if(u.pathname.endsWith('.css')||u.pathname==='/runtime.js') {try{return route.fulfill({contentType:u.pathname.endsWith('.css')?'text/css':'text/javascript',body:await readFile(new URL('.'+u.pathname,project),'utf8')});}catch{}}
  }
  return route.continue();});
 const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
  if(process.env.INSIGHTS_SOURCE==='1'){for(const file of ['workspace-frame.css','insights-workspace.css','collection-columns.css'])await page.addStyleTag({content:await readFile(new URL(file,project),'utf8')});}
  await page.evaluate(route=>SalesRuntime.route(route,{tab:true}),'/pages/'+scenario.name+'/index'+(scenario.query||''));
  await page.waitForFunction(name=>SalesRuntime.current?.route===`pages/${name}/index`,scenario.name);
  await page.waitForFunction(()=>{const p=SalesRuntime.current,c=p.selectComponent('#fdeContent')||[...(p._components?.values?.()||[])][0];return (p.data.isFde||!p.data.loading)&&!p.data.directoryLoading&&!p.data.performanceLoading&&(!c||(c.properties.recordsOnly||!c.data.loading)&&!c.data.activityLoading)});
  await page.waitForTimeout(100);await seed(page,scenario.name);await paint(page);await run(page);
  assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
 }finally{await context.close();}
}
async function seed(page,name){await page.evaluate(name=>{
 const p=SalesRuntime.current;
 if(name==='risks')p.setRisks(Array.from({length:30},(_,i)=>({id:'insight-risk-'+i,title:'合成风险 '+i,description:'这是合成的风险说明，完整内容保留；由销售确认处理。',status:i%3?'open':'resolved',statusLabel:i%3?'待解除':'已解除',severity:i%3?'高':'中',signal:{tone:i%3?'yellow':'green',label:i%3?'提醒':'正常'},customerName:'合成客户 '+i,customerId:'customer-'+i,opportunityId:'op-'+i,opportunityName:'合成商机 '+i,owner:'合成负责人',team:'示例团队',openedLabel:'9月20日',resolvedLabel:'9月20日'})));
 if(name==='fde-records'){
  const c=Object.values(p._components||{}).find(c=>c._module==='components/fde-dashboard/index');
  // Component registry is a Map in the Web host.
  const component=c||[...(p._components?.values?.()||[])].find(c=>c._module==='components/fde-dashboard/index');
  if(!component)throw Error('FDE records component not mounted');
  component.setData({activityLoading:false,activityError:'',activityMore:true,activityTotal:50,activity:Array.from({length:24},(_,i)=>({id:'insight-visit-'+i,customer_id:'customer-'+i,customer_name:'合成客户 '+i,opportunity_name:'合成跟进项目 '+i,dateText:'2026年9月20日',recorder_name:'合成记录人',can_read_detail:i%2===0}))});
 }
},name);}
async function metrics(page){return page.evaluate(()=>{
 const root=document.querySelector('#page-root'),s=[...document.querySelectorAll('[data-web-page-scroll]')].find(e=>e.clientHeight>0&&/auto|scroll/.test(getComputedStyle(e).overflowY)&&e.getClientRects().length)||document.querySelector('.web-insights-scroll,.activity-scroll'),w=document.querySelector('.web-insights-workspace'),h=document.querySelector('.web-insights-header')||document.querySelector('.activity-head');
 const box=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height,client:e.clientHeight,scroll:e.scrollHeight,overflow:getComputedStyle(e).overflowY}};
 return {width:innerWidth,height:innerHeight,body:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],root:box(root),workspace:box(w),scroll:box(s),head:box(h)};
});}
async function assertCollectionFrame(page){
 const m=await page.evaluate(()=>{const rail=document.querySelector('.web-collection-aside'),main=document.querySelector('.web-collection-main'),frame=rail.parentElement,r=rail.getBoundingClientRect(),l=main.getBoundingClientRect(),s=getComputedStyle(rail),f=getComputedStyle(frame);return {rail:{top:r.top,right:r.right,bottom:r.bottom,width:r.width},main:{top:l.top,left:l.left,bottom:l.bottom},gap:f.columnGap,border:f.borderTopWidth,divider:s.borderRightWidth,padding:s.paddingLeft,railBackground:s.backgroundColor,listBackground:getComputedStyle(main).backgroundColor}});
 assert.ok(Math.abs(m.rail.top-m.main.top)<2&&Math.abs(m.rail.bottom-m.main.bottom)<2,'context rail fills the same frame height as the list');assert.ok(Math.abs(m.rail.right-m.main.left)<2,'one continuous frame has no empty column gutter');assert.equal(m.rail.width,280);assert.deepEqual({gap:m.gap,border:m.border,divider:m.divider,padding:m.padding},{gap:'0px',border:'1px',divider:'1px',padding:'18px'});assert.notEqual(m.railBackground,m.listBackground);
}
function frame(m){assert.ok(m.body[0]<=m.width+1,JSON.stringify(m));if(m.width>=601){assert.ok(m.body[1]<=m.height+1,JSON.stringify(m));assert.ok(m.root.scroll<=m.root.client+1,JSON.stringify(m));assert.ok(m.workspace.scroll<=m.workspace.client+1,JSON.stringify(m));assert.ok(m.scroll.client>=155,JSON.stringify(m));assert.ok(m.scroll.bottom<=m.root.bottom+1,JSON.stringify(m));assert.equal(m.scroll.overflow,'auto');}}
for(const size of [[1366,768],[1024,600],[630,800],[390,844]]) for(const scenario of scenarios) {
 test(`${scenario.name} ${size.join('x')}: compact controls and complete content stay reachable`,()=>session(scenario,size,async page=>{
  const before=await metrics(page);frame(before);
  if(size[0]>=1180&&scenario.name==='risks')await assertCollectionFrame(page);
  if(scenario.name==='opportunities'){
   const heading=await page.locator('.web-collection-aside').boundingBox(),main=await page.locator('.web-collection-main').boundingBox();
   assert.ok(heading.y+heading.height<=main.y+1,'list-only opportunities places its title above results, without an empty summary rail');
   assert.ok(main.width>=heading.width-40,'the opportunity table uses the available reading width');
  }
  const dataBefore=await page.evaluate(()=>JSON.stringify(SalesRuntime.current.data));
  await (await scrollPane(page)).evaluate(e=>e.scrollTop=e.scrollHeight);await paint(page);
  if(scenario.name!=='opportunities')assert.equal(await page.evaluate(()=>JSON.stringify(SalesRuntime.current.data)),dataBefore);
  if(size[0]>=601){const after=await metrics(page);frame(after);assert.equal(after.head.top,before.head.top);}
  if(process.env.INSIGHTS_SCREENSHOTS==='1'&&size[0]===1366){await (await scrollPane(page)).evaluate(e=>e.scrollTop=0);await paint(page);const dir=new URL('file:///tmp/sales-sidebar-validation/');await mkdir(dir,{recursive:true});await page.screenshot({path:fileURLToPath(new URL('侧栏统一-经营-'+scenario.name+'.png',dir))});}
 }));
}
for(const name of ['bi','opportunities','workbench'])test(`FDE ${name}: authorized component uses the same contained frame`,()=>session({name,role:'fde_lead'},[630,800],async page=>{frame(await metrics(page));assert.equal(await page.locator('.create-opportunity').count(),0);if(name!=='bi'){
 await page.evaluate(()=>{const c=SalesRuntime.current.selectComponent('#fdeContent');window.__moreCalls=0;c.loadMore=()=>{__moreCalls++;c.setData({hasMore:false})};c.setData({hasMore:true});});
 await (await scrollPane(page)).evaluate(e=>{e.scrollTop=e.scrollHeight;e.dispatchEvent(new Event('scroll',{bubbles:true}));});await paint(page);
 await page.waitForFunction(()=>__moreCalls===1);assert.equal(await page.evaluate(()=>__moreCalls),1,'one scroll edge invokes the native delegate once');
 for(const width of [1180,1366]){
  await page.setViewportSize({width,height:600});await paint(page);frame(await metrics(page));
  const aside=await page.locator('.web-collection-aside').boundingBox(),main=await page.locator('.web-collection-main').boundingBox();
  assert.ok(Math.abs(aside.x+aside.width-main.x)<2&&Math.abs(aside.y-main.y)<2,'FDE summary and projects must share the desktop two-column frame');await assertCollectionFrame(page);
 }
 const list=await page.evaluate(()=>SalesRuntime.current.selectComponent('#fdeContent').data.filtered.map(item=>item.id));
 await page.locator('.web-collection-aside .quarter-option[data-q="3"]').click();
 await page.waitForFunction(()=>!SalesRuntime.current.selectComponent('#fdeContent').data.overviewLoading);
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.selectComponent('#fdeContent').data.summaryQuarter.quarters),[3]);
 assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.selectComponent('#fdeContent').data.filtered.map(item=>item.id)),list,'summary period must not change the project list');
}}));
test('opportunities preserves create permission, expanded stage filters, retries and original pagination entry',()=>session(scenarios[5],[1024,600],async page=>{
 await page.locator('.filter-stage').click();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);await page.locator('#web-select-dialog .option').first().waitFor();assert.ok(await page.locator('#web-select-dialog .option').count()>0);await page.keyboard.press('Escape');await page.locator('#web-select-dialog').waitFor({state:'detached'});frame(await metrics(page));
 // Isolate the explicit footer binding here. Automatic primary-scroll paging is tested separately;
 // Playwright scrolls this footer into view and may legitimately invoke both entry points.
 await page.evaluate(()=>{const p=SalesRuntime.current;window.__moreCalls=0;p.onReachBottom=()=>{};p.loadMore=()=>{__moreCalls++;p.setData({hasMore:false})};p.setData({hasMore:true,loadingMore:false,moreError:''})});
 // Source event attributes are compiled by runtime; the exact native label remains the explicit pagination entry.
 await page.getByText(/已显示.*加载更多/).last().click();assert.equal(await page.evaluate(()=>__moreCalls),1);
 await page.evaluate(()=>SalesRuntime.current.setData({canCreate:false,loadError:'合成读取错误',loading:false}));await paint(page);assert.equal(await page.locator('.create-opportunity').count(),0);assert.ok((await page.locator('.web-insights-scroll').innerText()).includes('合成读取错误'));
 await page.evaluate(()=>{SalesRuntime.current.loadPage=()=>{window.__retry=true};});await page.getByText('合成读取错误 · 点击重试').click();assert.equal(await page.evaluate(()=>__retry),true);
}));
test('profile retains hierarchy, native tab transitions, target edit guard and account exit action',()=>session(scenarios[1],[630,800],async page=>{
 assert.deepEqual(await page.locator('profile-scope-picker .role-scope-option').allTextContents(),['部门','团队','个人']);
 for(const key of ['efficiency','profile','maturity']){await page.locator(`.profile-insight-tabs [data-tab="${key}"]`).click();assert.equal(await page.evaluate(()=>SalesRuntime.current.data.activeProfileTab),key);frame(await metrics(page));}
 await page.waitForFunction(()=>Boolean(SalesRuntime.current.selectComponent('#salesQuarterTarget')));assert.equal(await page.locator('.web-insights-header .logout-button').count(),1);assert.equal(await page.evaluate(()=>SalesRuntime.current.selectComponent('#salesQuarterTarget').properties.editable),await page.evaluate(()=>SalesRuntime.current.data.canEditSalesTarget));
 await page.evaluate(()=>{SalesRuntime.current.logout=()=>{window.__logout=true};});await page.locator('.logout-button').click();assert.equal(await page.evaluate(()=>__logout),true);
}));
test('risk status tabs and nested opportunity action retain the original event boundary',()=>session(scenarios[4],[630,800],async page=>{
 await page.locator('.risk-tab[data-key="resolved"]').click();await paint(page);assert.equal(await page.locator('.risk-card').count(),10);
 await page.evaluate(()=>{const p=SalesRuntime.current;window.__riskClicks=[];p.openRisk=()=>__riskClicks.push('risk');p.openOpportunity=e=>__riskClicks.push(e.currentTarget.dataset.opportunityId)});
 await page.locator('.risk-opportunity').first().click();assert.deepEqual(await page.evaluate(()=>__riskClicks),['op-0']);
 await page.locator('.risk-title').first().click();assert.deepEqual(await page.evaluate(()=>__riskClicks),['op-0','risk']);
 await page.evaluate(()=>SalesRuntime.current.setData({loading:true}));await paint(page);assert.equal(await page.locator('.risk-card').count(),0);assert.ok((await page.locator('.web-insights-scroll').innerText()).includes('正在加载风险'));
}));
test('FDE records keep history-only guard and manual pagination accessible inside the Web frame',()=>session(scenarios[3],[630,800],async page=>{
 const layer=page.locator('.web-fde-records');assert.equal(await layer.evaluate(e=>getComputedStyle(e).position),'static');await page.locator('.activity-card[data-id="insight-visit-1"]').click();await page.getByText('当前仅可查看这条历史摘要',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/fde-records/index');
 await page.evaluate(()=>{const p=SalesRuntime.current,c=[...p._components.values()].find(c=>c._module==='components/fde-dashboard/index');window.__recordMore=0;c.loadActivity=()=>{__recordMore++;c.setData({activityMore:false})}});
 await page.getByText('加载更多记录',{exact:true}).click();assert.equal(await page.evaluate(()=>__recordMore),1);
 assert.equal(await page.locator('.activity-history').count(),12);assert.equal(await page.locator('.activity-arrow').count(),12);
}));
