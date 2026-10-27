import test from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const crm=process.env.APP_URL || 'http://127.0.0.1:5196';
async function session(base,run,width=1440){
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[],business=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{const url=new URL(route.request().url());if(url.origin!==new URL(base).origin)return route.abort();if(url.pathname.startsWith('/api/')||url.pathname==='/local-login'){business.push(url.pathname);return route.abort();}return route.continue();});
  try{await page.goto(base+'/?mode=preview');await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session);await run(page);assert.deepEqual(errors,[]);assert.deepEqual(business,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}finally{await browser.close();}
}
async function go(page,route){await page.evaluate(path=>SalesRuntime.route('/pages/'+path+'/index',{tab:true}),route);}
async function readyPicker(page){await page.locator('#web-select-dialog').waitFor();await page.waitForFunction(()=>document.querySelector('#web-select-input')?.tomselect?.isOpen);}
test('mature native and multi-select controls preserve filters, cancellation and keyboard interaction',async()=>{
  await session(crm,async page=>{
    await go(page,'workbench');await page.waitForFunction(()=>SalesRuntime.current?.data.opportunityTotal===489);
    await page.locator('.filter-owner select').click();await readyPicker(page);
    const name=await page.locator('#web-select-dialog .option').nth(1).innerText();
    await page.locator('#web-select-dialog .ts-control input').fill(name);
    await page.locator('#web-select-dialog .option').filter({hasText:name}).first().click();
    await page.waitForSelector('#web-select-dialog',{state:'detached'});
    await page.waitForFunction(()=>SalesRuntime.current.data.opportunityOwnerIndex>0&&!SalesRuntime.current.data.opportunityListLoading);
    const before=await page.evaluate(()=>SalesRuntime.current.data.opportunityOwnerIndex);
    await page.locator('.filter-owner select').focus();await page.keyboard.press('ArrowDown');await readyPicker(page);await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityOwnerIndex),before);
    await page.evaluate(()=>SalesRuntime.current.resetOpportunityFilters());
    await page.waitForFunction(()=>SalesRuntime.current.data.opportunityTotal===489);
    await page.locator('.filter-stage').click();await readyPicker(page);
    for(const value of ['qualified','solution'])await page.locator(`#web-select-dialog .option[data-value="${value}"]`).click();
    await page.locator('#web-select-apply').click();
    await page.waitForFunction(()=>SalesRuntime.current.data.opportunitySelectedStages.length===2&&SalesRuntime.current.data.opportunityTotal===97&&!SalesRuntime.current.data.opportunityListLoading);
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityTotal),97);
    await page.locator('.filter-close').click();await readyPicker(page);await page.keyboard.press('Escape');
    assert.equal(await page.locator('#web-select-dialog').count(),0);
    await page.locator('#crm-all-open').click();await page.locator('#crm-all [data-kind="visits"]').click();
    await page.locator('#crm-all-filter').click();await readyPicker(page);
    assert.equal(await page.locator('#web-select-dialog').evaluate(el=>el.closest('dialog')?.id),'crm-all');
    await page.locator('#web-select-dialog .option[data-value="伙伴"]').click();
    await page.waitForSelector('#web-select-dialog',{state:'detached'});assert.equal(await page.locator('#crm-all-filter').inputValue(),'伙伴');
    assert.match(await page.locator('#crm-all nav').last().innerText(),/338/);
  });
});
test('CRM dashboard uses ECharts, honest missing-date values and mature team/quarter selectors',async()=>{
  await session(crm,async page=>{
    await go(page,'bi');await page.waitForFunction(()=>SalesDashboard.count()===2&&!SalesRuntime.current.data.loading);
    assert.equal(await page.locator('.web-echart svg').count(),2);
    assert.match(await page.locator('.web-chart-coverage').innerText(),/缺少完整关单年份/);
    const kpi=page.locator('.kpi-card').filter({hasText:'总商机 ACV'});assert.match(await kpi.innerText(),/—/);assert.doesNotMatch(await kpi.innerText(),/¥0/);
    const facts=await page.evaluate(()=>{const chart=document.querySelector('.web-snapshot-chart .web-echart');return {expected:SalesRuntime.current._raw.opportunities.length,actual:echarts.getInstanceByDom(chart).getOption().series[0].data.reduce((sum,r)=>sum+r.value,0)};});assert.equal(facts.actual,facts.expected);
    await page.locator('.quarter-picker').first().click();await readyPicker(page);
    const trigger=await page.locator('.quarter-picker').first().boundingBox(),panel=await page.locator('#web-select-dialog').boundingBox();
    assert.ok(Math.abs(panel.y-trigger.y-trigger.height-8)<2,'quarter picker stays beside its trigger');
    assert.equal(await page.evaluate(()=>!!document.querySelector(':modal')),false,'routine filters must not open a blocking modal');
    await page.locator('#web-select-dialog .option[data-value="4"]').click();await page.locator('#web-select-apply').click();
    await page.waitForFunction(()=>SalesRuntime.current.data.selectedQuarter.quarters.includes(4));
    await page.locator('dashboard-picker button').first().click();await readyPicker(page);assert.equal(await page.locator('#web-select-dialog .ts-wrapper').count(),1);await page.keyboard.press('Escape');
    await page.setViewportSize({width:1100,height:800});await page.waitForTimeout(80);
    assert.equal(await page.evaluate(()=>SalesDashboard.count()),2);
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
    await page.setViewportSize({width:1440,height:1000});
    await go(page,'workbench');await page.waitForFunction(()=>SalesDashboard.count()===0);
    await go(page,'bi');await page.waitForFunction(()=>SalesDashboard.count()===2);
  });
});
test('synthetic sales and FDE charts retain source values and dispose on navigation',async()=>{
  const server=createSalesWebServer({target:''});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{await session('http://127.0.0.1:'+server.address().port,async page=>{
    await go(page,'bi');await page.waitForFunction(()=>SalesDashboard.count()===3&&!SalesRuntime.current.data.loading);
    assert.equal(await page.locator('.web-chart-coverage').count(),0);
    assert.equal(await page.locator('.web-kpi-primary .kpi-card').count(),4);
    assert.equal(await page.locator('.web-kpi-secondary .kpi-card').count(),4);
    const amount=await page.evaluate(()=>SalesRuntime.current.data.kpis[0].value);
    const metric=page.locator('.web-kpi-primary .web-kpi-value').first();
    assert.equal(await metric.getAttribute('title'),amount+' 元');
    assert.equal(await metric.locator('.web-kpi-unit').innerText(),'万元');
    await metric.focus();assert.equal(await metric.locator('.web-kpi-exact').isVisible(),true);
    assert.equal(await metric.locator('.web-kpi-exact').innerText(),amount+' 元');
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.kpis[0].value),amount,'display formatting does not rewrite source facts');
    await metric.evaluate(el=>el.blur());
    const source=await page.evaluate(()=>({values:SalesRuntime.current.data.funnel.map(r=>r.value/10000),chart:echarts.getInstanceByDom(document.querySelector('.web-echart')).getOption().series[0].data.map(r=>r.value)}));assert.deepEqual(source.chart,source.values);
    await page.locator('#preview-role').click();await readyPicker(page);await page.locator('#web-select-dialog .option[data-value="fde_lead"]').click();
    await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='fde_lead');await go(page,'bi');
    await page.waitForFunction(()=>SalesDashboard.count()>=2);
    assert.ok(await page.locator('fde-dashboard .web-echart svg').count()>=2);
    await page.locator('fde-dashboard .quarter-picker').first().click();await readyPicker(page);await page.keyboard.press('Escape');
    await go(page,'index');await page.waitForFunction(()=>SalesDashboard.count()===0);
  });}finally{await new Promise(resolve=>server.close(resolve));}
});
test('mobile selector fits viewport and keeps multi-selection confirmation reachable',async()=>{
  await session(crm,async page=>{
    await go(page,'bi');await page.waitForFunction(()=>SalesDashboard.count()===2);
    await page.locator('.quarter-picker').first().click();await readyPicker(page);
    const box=await page.locator('#web-select-dialog').boundingBox();assert.ok(box.x>=0&&box.x+box.width<=390);
    const layout=await page.evaluate(()=>{const dialog=document.querySelector('#web-select-dialog');return {width:dialog.clientWidth,scroll:dialog.scrollWidth};});assert.ok(layout.scroll<=layout.width+1);
    await page.locator('#web-select-apply').click();await page.waitForSelector('#web-select-dialog',{state:'detached'});
    await page.waitForFunction(()=>document.documentElement.scrollWidth<=innerWidth);
  },390);
});
test('partner and FDE selectors retain form callbacks, remote options and cancellation',async()=>{
  const server=createSalesWebServer({target:''});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try{await session('http://127.0.0.1:'+server.address().port,async page=>{
    await page.evaluate(()=>SalesRuntime.route('/pages/opportunity-create/index?customerId=00000010-0000-4000-8000-000000000001'));
    await page.locator('opportunity-form select').first().waitFor();
    await page.locator('opportunity-form select').nth(1).click();await readyPicker(page);
    await page.locator('#web-select-dialog .option[data-value="1"]').click();
    await page.waitForFunction(()=>document.querySelector('#web-select-dialog')?.getAttribute('aria-label')==='选择合作伙伴');
    await page.locator('#web-select-dialog .option[data-selectable]').first().click();
    await page.waitForFunction(()=>!!SalesRuntime.current.selectComponent('#opportunityForm').data.form.partner_id);
    await page.locator('fde-picker [data-handler="open"]').click();await readyPicker(page);
    await page.locator('#web-select-dialog .option[data-selectable]').first().click();await page.locator('#web-select-apply').click();
    await page.waitForFunction(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.form.fde_member_ids.length===1);
    const selected=await page.evaluate(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.form.fde_member_ids[0]);
    await page.locator('fde-picker [data-handler="open"]').click();await readyPicker(page);
    await page.locator('#web-select-dialog .web-select-clear').click();await page.keyboard.press('Escape');
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.form.fde_member_ids),[selected],'cancellation preserves the form draft');
  });}finally{await new Promise(resolve=>server.close(resolve));}
});
test('multi-select limits, outside cancellation and page changes discard uncommitted selections',async()=>{
  await session(crm,async page=>{
    await go(page,'bi');await page.waitForFunction(()=>SalesDashboard.count()===2);
    await page.evaluate(()=>{window.selectionProbe=[];SalesSelect.open({owner:SalesRuntime.current,anchor:document.querySelector('.quarter-picker'),title:'选择成员',multiple:true,maxItems:30,options:Array.from({length:32},(_,i)=>({value:String(i),text:'测试成员 '+i})),selected:[],commit:ids=>window.selectionProbe=ids});});await readyPicker(page);
    await page.evaluate(()=>{const ts=document.querySelector('#web-select-input').tomselect;for(let i=0;i<32;i++)ts.addItem(String(i));});
    assert.equal(await page.evaluate(()=>document.querySelector('#web-select-input').tomselect.items.length),30);
    await page.locator('.page-heading').first().click();assert.equal(await page.locator('#web-select-dialog').count(),0);
    assert.deepEqual(await page.evaluate(()=>selectionProbe),[]);
    await page.evaluate(()=>SalesSelect.open({owner:SalesRuntime.current,anchor:document.querySelector('.quarter-picker'),title:'只读成员',multiple:true,options:[{value:'locked',text:'已关联成员',disabled:true,locked:true}],selected:['locked'],commit:ids=>window.selectionProbe=ids}));await readyPicker(page);
    await page.locator('#web-select-dialog .web-select-clear').click();await page.locator('#web-select-apply').click();
    assert.deepEqual(await page.evaluate(()=>selectionProbe),['locked'],'clear preserves locked selected members');
    await page.locator('.quarter-picker').first().click();await readyPicker(page);await go(page,'workbench');
    await page.waitForSelector('#web-select-dialog',{state:'detached'});
  });
});
