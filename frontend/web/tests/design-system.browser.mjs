/** Real shared controls, isolated synthetic catalog, no business requests. */
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base='http://127.0.0.1:'+server.address().port;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server.close(r));});
async function fixture(run,viewport={width:1440,height:900}){
 const ctx=await browser.newContext({viewport,serviceWorkers:'block'}),errors=[],blocked=[],httpErrors=[];
 await ctx.addInitScript(()=>{localStorage.setItem('sales-web:test-preserved','untouched');sessionStorage.setItem('sales-web:preview-role','manager');});
 await ctx.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base||/^\/(api|local-login|local-preview)(\/|$)/.test(u.pathname)){blocked.push(u.pathname);return route.abort();}return route.continue();});
 const page=await ctx.newPage();page.setDefaultTimeout(5000);page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)httpErrors.push(r.url());});
 try{await page.goto(base+'/design-system/index.html');await page.locator('.swatch').first().waitFor();await run(page);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(httpErrors,[]);assert.equal(await page.evaluate(()=>window.SalesRuntime),undefined);assert.equal(await page.evaluate(()=>localStorage.getItem('sales-web:test-preserved')),'untouched');assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:preview-role')),'manager');}finally{await ctx.close();}
}
async function go(p,hash){await p.evaluate(hash=>{location.hash=hash;},hash);await p.waitForFunction(hash=>{const template=hash.startsWith('template-');return !document.querySelector(template?'[data-template="'+hash.slice(9)+'"]':'[data-panel="'+hash+'"]').hidden;},hash);}
async function multi(p,trigger,values){await p.locator(trigger).click();for(const value of values)await p.locator('#web-select-dialog .option[data-value="'+value+'"]').click();await p.locator('#web-select-apply').click();}

test('catalog uses product tokens and mature picker assets without auth, API or storage writes',()=>fixture(async p=>{
 assert.equal(await p.locator('.swatch').count(),6);assert.ok(await p.evaluate(()=>!!window.SalesSelect&&!!window.SalesDatePicker&&!!window.echarts));
 const colors=await p.evaluate(()=>{const s=getComputedStyle(document.documentElement);return ['--ui-background','--ui-primary','--ui-sidebar','--ui-muted'].map(n=>s.getPropertyValue(n).trim());});assert.deepEqual(colors,['#f3f5f9','#2863cd','#142f54','#617188']);
 const links=await p.locator('link[rel=stylesheet]').evaluateAll(ns=>ns.map(n=>n.getAttribute('href')));assert.ok(links.includes('../design-tokens.css'));assert.ok(links.includes('../theme.css'));
}));
test('shared multi-select searches, applies, cancels with Esc and clears selections',()=>fixture(async p=>{
 await go(p,'components');await p.locator('#member-picker').click();
 const search=p.locator('#web-select-dialog .web-select-main .ts-control input');await search.fill('销售甲');
 await p.locator('#web-select-dialog .option[data-value="0"]').click();await search.fill('FDE');await p.locator('#web-select-dialog .option[data-value="4"]').click();await p.locator('#web-select-apply').click();assert.match(await p.locator('#member-result').innerText(),/已选择 2 人/);
 await p.locator('#member-picker').click();await p.locator('#web-select-dialog .option[data-value="1"]').click();await p.keyboard.press('Escape');assert.match(await p.locator('#member-result').innerText(),/已选择 2 人/);assert.equal(await p.evaluate(()=>document.activeElement.id),'member-picker');
 await p.locator('#member-picker').click();await p.locator('.web-select-clear').click();await p.locator('#web-select-apply').click();assert.equal(await p.locator('#member-result').innerText(),'尚未选择成员');
}));
test('immediate Escape cancels before popup autofocus without an orphaned portal',()=>fixture(async p=>{
 await go(p,'components');const remaining=await p.evaluate(()=>{document.querySelector('#member-picker').click();document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));return document.querySelectorAll('#web-select-dialog').length;});assert.equal(remaining,0);await p.evaluate(()=>new Promise(r=>requestAnimationFrame(r)));assert.equal(await p.locator('#web-select-dialog').count(),0);
}));
test('opening a populated popup stays inside a short viewport on every rendered frame',()=>fixture(async p=>{
 await go(p,'components');await p.locator('#member-picker').scrollIntoViewIfNeeded();const outside=await p.evaluate(()=>new Promise(resolve=>{document.querySelector('#member-picker').click();const bad=[];let i=0;function frame(){const b=document.querySelector('#web-select-dialog').getBoundingClientRect();if(b.top<0||b.bottom>innerHeight+1)bad.push({top:b.top,bottom:b.bottom});if(++i<20)requestAnimationFrame(frame);else resolve(bad);}frame();}));assert.deepEqual(outside,[]);await p.keyboard.press('Escape');
},{width:1024,height:600}));
test('shared date picker validates manual input, commits a date and cancels without changes',()=>fixture(async p=>{
 await go(p,'components');await p.locator('#date-demo').click();await p.locator('.wx-date-input').fill('2026-02-31');await p.locator('.wx-date-apply').click();assert.equal(await p.locator('.wx-date-error').isVisible(),true);await p.locator('.wx-date-input').fill('2026-09-25');await p.locator('.wx-date-apply').click();assert.equal(await p.locator('#date-result').innerText(),'已选：2026-09-25');
 await p.locator('#date-demo').click();await p.locator('.wx-date-input').fill('2026-09-28');await p.keyboard.press('Escape');assert.equal(await p.locator('#date-demo').getAttribute('value'),'2026-09-25');assert.equal(await p.evaluate(()=>document.activeElement.id),'date-demo');
}));
test('result and AI states distinguish missing from zero and preserve input on retry and edit',()=>fixture(async p=>{
 await go(p,'components');assert.match(await p.locator('#result-demo').innerText(),/¥ 0/);assert.match(await p.locator('#result-demo').innerText(),/未登记/);
 for(const state of ['loading','empty','error']){await p.locator('[data-result-state='+state+']').click();assert.equal(await p.locator('[data-result-state='+state+']').getAttribute('aria-pressed'),'true');}
 await p.locator('#retry-demo').click();assert.equal(await p.locator('[data-result-state=normal]').getAttribute('aria-pressed'),'true');
 await p.locator('#ai-draft').fill('必须保留的中文草稿');await p.locator('[data-ai=analyzing]').click();assert.equal(await p.locator('#ai-draft').evaluate(n=>n.readOnly),true);await p.locator('[data-ai=failed]').click();await p.locator('#ai-retry').click();assert.match(await p.locator('#ai-status').innerText(),/正在分析/);await p.locator('#ai-edit').click();assert.equal(await p.locator('#ai-draft').inputValue(),'必须保留的中文草稿');assert.equal(await p.locator('#ai-draft').evaluate(n=>n.readOnly),false);await p.locator('[data-ai=review]').click();assert.match(await p.locator('#ai-status').innerText(),/尚未归档/);
}));
test('validation preserves notes and modal focus is contained and restored',()=>fixture(async p=>{
 await go(p,'components');await p.locator('#validation-note').fill('补充内容不应丢失');await p.locator('#validation-form button').click();assert.equal(await p.locator('#validation-title').getAttribute('aria-invalid'),'true');assert.equal(await p.locator('#validation-note').inputValue(),'补充内容不应丢失');await p.locator('#validation-title').fill('核对验收');await p.locator('#validation-form button').click();assert.match(await p.locator('#validation-result').innerText(),/校验通过/);
 await p.locator('#open-dialog').click();for(let i=0;i<7;i++){await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.querySelector('#catalog-dialog').contains(document.activeElement)),true);}await p.keyboard.press('Escape');assert.equal(await p.evaluate(()=>document.activeElement.id),'open-dialog');
}));
test('overview and list filters are independent, chips removable and empty results recoverable',()=>fixture(async p=>{
 await go(p,'template-list');await multi(p,'#stage-picker',['50%']);assert.equal(await p.locator('#opportunity-rows tr').count(),1);assert.equal(await p.locator('#overview-count').innerText(),'6');
 await multi(p,'#overview-picker',['3']);assert.equal(await p.locator('#overview-count').innerText(),'2');assert.equal(await p.locator('#opportunity-rows tr').count(),1);
 await p.locator('#list-search').fill('不存在');assert.equal(await p.locator('#list-empty').isVisible(),true);await p.locator('[data-remove-query]').click();assert.equal(await p.locator('#opportunity-rows tr').count(),1);await p.locator('[data-remove-stage]').click();assert.equal(await p.locator('#opportunity-rows tr').count(),6);assert.equal(await p.locator('#overview-count').innerText(),'2');
 await p.locator('#list-search').fill('销售甲');assert.equal(await p.locator('#opportunity-rows tr').count(),3);await p.locator('#list-clear').click();assert.equal(await p.locator('#opportunity-rows tr').count(),6);
 await p.getByRole('button',{name:'查看仓配异常工单助手一期与试点验收支持',exact:true}).click();assert.match(await p.locator('#dialog-content').innerText(),/确收：未登记/);await p.keyboard.press('Escape');
}));
test('home attention contains only red and yellow judgments and exposes original reasons',()=>fixture(async p=>{
 await go(p,'template-home');assert.equal(await p.locator('.feed-row').count(),5);await p.locator('[data-feed=attention]').click();assert.equal(await p.locator('.feed-row').count(),2);assert.equal(await p.locator('.feed-row.gray,.feed-row.completed,.feed-row.green').count(),0);await p.locator('.feed-row.red summary').click();assert.match(await p.locator('.feed-row.red details').innerText(),/客户评审延期/);await p.locator('[data-feed=all]').click();assert.equal(await p.locator('.feed-row').count(),5);
}));
test('detail switches object and keyboard tabs without interpreting accepted tasks as completed',()=>fixture(async p=>{
 await go(p,'template-detail');await p.locator('[data-object=opportunity]').click();assert.equal(await p.locator('#detail-title').innerText(),'智能质检试点');await p.locator('#tab-summary').focus();await p.keyboard.press('End');assert.equal(await p.locator('#tab-tasks').getAttribute('aria-selected'),'true');assert.match(await p.locator('#detail-content').innerText(),/已接受/);assert.match(await p.locator('#detail-content').innerText(),/尚未提交完成结果/);await p.locator('[data-object=customer]').click();assert.equal(await p.locator('#detail-title').innerText(),'星河制造');assert.equal(await p.locator('#tab-tasks').getAttribute('aria-selected'),'true');
}));
test('visit validation keeps long text, review is explicitly unarchived and action stays reachable',()=>fixture(async p=>{
 await go(p,'template-visit');const draft='由示例销售甲核对试点验收清单。'.repeat(80);await p.locator('#visit-next').fill(draft);await p.locator('#visit-form button[type=submit]').click();assert.equal(await p.locator('#visit-error').isVisible(),true);assert.equal(await p.locator('#visit-next').inputValue(),draft);await p.locator('#visit-note').fill('客户提出补充验收指标。');await p.locator('#visit-form button[type=submit]').click();assert.match(await p.locator('#dialog-content').innerText(),/未调用 AI，也未归档/);await p.keyboard.press('Escape');assert.equal(await p.locator('#visit-next').inputValue(),draft);
 const bounds=await p.locator('.visit-workspace').evaluate(el=>{const b=el.getBoundingClientRect(),f=el.querySelector('.visit-footer').getBoundingClientRect();return{bottom:b.bottom,footer:f.bottom,scroll:el.querySelector('textarea:last-child').scrollHeight>el.querySelector('textarea:last-child').clientHeight};});assert.ok(bounds.footer<=bounds.bottom+1);assert.equal(bounds.scroll,true);
},{width:1024,height:600}));
test('analytics quarters change actual chart data and total but retain missing amounts',()=>fixture(async p=>{
 await go(p,'template-analytics');await p.waitForFunction(()=>echarts.getInstanceByDom(document.querySelector('#analytics-chart')));assert.equal(await p.locator('#analytics-total').innerText(),'26');await p.locator('#analytics-period').click();await p.locator('.web-select-clear').click();await p.locator('#web-select-dialog .option[data-value="4"]').click();await p.locator('#web-select-apply').click();assert.equal(await p.locator('#analytics-total').innerText(),'4');assert.deepEqual(await p.evaluate(()=>echarts.getInstanceByDom(document.querySelector('#analytics-chart')).getOption().series[0].data),[4]);assert.equal(await p.locator('[data-template=analytics] .text-metric').allTextContents().then(x=>x.join(',')),'未登记,未登记');
}));
for(const viewport of [{width:1440,height:900},{width:1366,height:768},{width:1024,height:600},{width:390,height:844},{width:320,height:740}])test(`${viewport.width}x${viewport.height} catalog fits all seven sections and popovers`,()=>fixture(async p=>{
 for(const route of ['foundations','components','template-home','template-list','template-detail','template-visit','template-analytics']){
  await go(p,route);await p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,route);
 }
 await go(p,'components');await p.locator('#member-picker').click();await p.waitForFunction(()=>{const el=document.querySelector('#web-select-dialog'),b=el?.getBoundingClientRect();return document.querySelector('#web-select-input')?.tomselect?.isOpen&&b.y>=0&&b.bottom<=innerHeight+1;});const b=await p.locator('#web-select-dialog').boundingBox();assert.ok(b.x>=0&&b.y>=0&&b.x+b.width<=viewport.width+1&&b.y+b.height<=viewport.height+1,JSON.stringify(b));await p.keyboard.press('Escape');await p.locator('#date-demo').click();await p.waitForFunction(()=>{const b=document.querySelector('#web-date-dialog')?.getBoundingClientRect();return b&&b.y>=0&&b.bottom<=innerHeight+1;});const d=await p.locator('#web-date-dialog').boundingBox();assert.ok(d.x>=0&&d.y>=0&&d.x+d.width<=viewport.width+1&&d.y+d.height<=viewport.height+1,JSON.stringify(d));await p.keyboard.press('Escape');
},viewport));
test('primary text and four status labels retain readable contrast and keyboard focus',()=>fixture(async p=>{
 await go(p,'components');const primary=p.locator('#demo-primary');await primary.focus();assert.ok(await primary.evaluate(el=>parseFloat(getComputedStyle(el).outlineWidth)>=2));
 await go(p,'foundations');for(const selector of ['.status-line .red','.status-line .yellow','.status-line .green','.status-line .gray']){const contrast=await p.locator(selector).evaluate(el=>{const lum=s=>s.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);const st=getComputedStyle(el),a=lum(st.color),b=lum(st.backgroundColor);return(Math.max(a,b)+.05)/(Math.min(a,b)+.05);});assert.ok(contrast>=4.5,selector+' '+contrast);}
}));
test('capture current catalog examples for visual review',()=>fixture(async p=>{
 await mkdir('.runtime/design-system',{recursive:true});for(const route of ['foundations','components','template-home','template-list','template-detail','template-visit','template-analytics']){await go(p,route);await p.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await p.screenshot({path:'.runtime/design-system/'+route+'.png',fullPage:true});}
 await p.setViewportSize({width:1024,height:600});await go(p,'template-visit');await p.locator('.visit-workspace').scrollIntoViewIfNeeded();await p.screenshot({path:'.runtime/design-system/short-visit.png'});
 await p.setViewportSize({width:390,height:844});await go(p,'template-home');await p.evaluate(()=>{document.activeElement?.blur();window.scrollTo({top:0,behavior:'instant'});});await p.screenshot({path:'.runtime/design-system/mobile-home.png',fullPage:true});
}));
