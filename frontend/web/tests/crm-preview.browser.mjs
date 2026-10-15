/** Opt-in test of the private local CRM dataset; never ships the data as fixtures. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const url=process.env.APP_URL || 'http://127.0.0.1:5196';
assert.ok(['localhost','127.0.0.1'].includes(new URL(url).hostname));
const data=JSON.parse(readFileSync(process.env.CRM_DATASET || new URL('../.runtime/crm-preview.json',import.meta.url)));

test('CRM data renders full lists, five demo histories and partner/unknown values without business network', {timeout:120000}, async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const page=await context.newPage(), errors=[], api=[], localReads=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route('**/*',route=>{
    const target=new URL(route.request().url());
    if(target.origin!==new URL(url).origin)return route.abort();
    if(target.pathname.startsWith('/api/')||target.pathname==='/local-login'){api.push(target.pathname);return route.abort();}
    if(target.pathname==='/local-preview-data')localReads.push(target.pathname);
    return route.continue();
  });
  try {
    await page.goto(url+'/?mode=live');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index');
    assert.equal(localReads.length,0,'enterprise mode must not load CRM samples');
    await page.goto(url+'/?mode=preview');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index' && SalesRuntime.current.data.messages?.length>0);
    assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.role),'manager');
    await page.getByText('CRM 历史跟进记录',{exact:true}).first().waitFor();
    assert.doesNotMatch(await page.locator('body').innerText(),/undefined|星河制造|远山科技/);
    await page.locator('#desktop-nav [data-path="pages/customers/index"]').click();
    await page.locator('.customer-card').first().waitFor();
    if(data.scope==='full') {
      assert.equal(await page.evaluate(()=>SalesRuntime.current.data.scopeCustomerCount),17834);
      assert.equal(await page.locator('.customer-card').count(),50);
      assert.equal(await page.evaluate(()=>SalesRuntime.current.data.plotCustomers.length),0,'no invented customer scores');
      const first=await page.locator('.customer-card').first().getAttribute('data-id');
      await page.locator('#crm-customer-next').click();
      await page.waitForFunction(id=>document.querySelector('.customer-card')?.dataset.id!==id,first);
      assert.match(await page.locator('#crm-customer-pager').innerText(),/第 2 \/ 357 页/);
      const last=data.state.customers.at(-1);
      await page.getByPlaceholder('搜索客户或负责人').fill(last.name);
      await page.locator(`.customer-card[data-id="${last.id}"]`).waitFor();
      await page.getByPlaceholder('搜索客户或负责人').fill('');
      await page.waitForFunction(()=>SalesRuntime.current._crmCustomers?.length===17834);
      await page.screenshot({path:fileURLToPath(new URL('../.runtime/crm-full-customers.png',import.meta.url)),fullPage:false});
    } else for(const customer of data.state.customers) assert.ok((await page.locator('#page-root').innerText()).includes(customer.name));
    await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
    await page.waitForFunction(count=>SalesRuntime.current?.data.opportunityTotal===count,data.state.opportunities.length);
    const board=await page.evaluate(()=>SalesRuntime.current.data.opportunityBoard);
    assert.equal(board.total,data.state.opportunities.length);assert.equal(board.active,data.state.opportunities.filter(o=>o.status==='open').length);assert.equal(board.won,data.state.opportunities.filter(o=>o.status==='won').length);assert.equal(board.missingCloseDates,data.state.opportunities.length);
    if(data.scope!=='full') for(const op of data.state.opportunities) assert.ok((await page.locator('#page-root').innerText()).includes(op.name));
    await page.screenshot({path:fileURLToPath(new URL('../.runtime/crm-opportunities.png',import.meta.url)),fullPage:true});
    await page.locator('#crm-samples-open').click();
    let checked=0;
    for(const op of data.state.opportunities.filter(o=>!data.demo_opportunity_ids || data.demo_opportunity_ids.includes(o.id))){
      await page.locator('#crm-sample-select').selectOption(op.id);
      const chain=data.state.visits.filter(v=>v.opportunity_id===op.id).sort((a,b)=>a.source_sequence-b.source_sequence);
      for(let i=0;i<chain.length;i++){
        const text=await page.locator('#crm-timeline-record').innerText(),v=chain[i];
        assert.ok(text.includes(v.visit_date));assert.ok(text.includes(`第 ${v.source_ref.row} 行`));
        assert.ok(text.includes(v.follow_up_record));assert.ok(text.includes(v.next_action));
        assert.ok(text.includes(v.customer_type ? v.customer_type+'跟进' : '跟进类型未填写'));
        checked++;
        if(i<chain.length-1)await page.locator('#crm-next').click();
      }
    }
    assert.equal(checked,26);
    const partnerOp=data.state.opportunities.find(op=>op.source_ref.row===125);
    await page.locator('#crm-sample-select').selectOption(partnerOp.id);
    await page.screenshot({path:fileURLToPath(new URL('../.runtime/crm-timeline.png',import.meta.url)),fullPage:true});
    await page.getByText('在当前页面查看这条跟进',{exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current?.route==='pages/visit-detail/index'&&!SalesRuntime.current.data.loading&&!!SalesRuntime.current.data.visit);
    await page.locator('.hero-meta').getByText('伙伴跟进',{exact:true}).waitFor();
    const firstFlag=page.locator('.field-grid > *').filter({hasText:'是否首次拜访'});
    await firstFlag.getByText('未记录',{exact:true}).waitFor();
    const unknown=data.state.visits.find(v=>v.customer_type===null && v.customer_id && v.opportunity_id);
    await page.evaluate(v=>SalesRuntime.route(`/pages/visit-detail/index?customer_id=${v.customer_id}&visit_id=${v.id}`),unknown);
    await page.waitForFunction(id=>SalesRuntime.current?.data.visit?.id===id&&!SalesRuntime.current.data.loading,unknown.id);
    await page.locator('.hero-meta').getByText('跟进类型未填写',{exact:true}).waitFor();
    await page.reload();
    await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='manager');
    await page.locator('#desktop-nav [data-path="pages/workbench/index"]').click();
    await page.waitForFunction(count=>SalesRuntime.current?.data.opportunityTotal===count,data.state.opportunities.length);
    if(data.scope==='full') {
      await page.locator('#crm-all-open').click();
      await page.locator('#crm-all [data-kind="visits"]').click();
      await page.locator('#crm-all-filter').selectOption('伙伴');
      assert.match(await page.locator('#crm-all nav').last().innerText(),/338 条/);
      await page.locator('#crm-all-filter').selectOption('unknown');
      assert.match(await page.locator('#crm-all nav').last().innerText(),/31 条/);
      await page.locator('#crm-all-filter').selectOption('pending');
      const orphan=data.state.visits.find(v=>!v.opportunity_id && !v.opportunity_ids.length);
      await page.locator('#crm-all-search').fill(orphan.source_ref.key.toString());
      await page.locator(`#crm-all-list [data-id="${orphan.id}"]`).click();
      assert.match(await page.locator('#crm-source-detail').innerText(),/原表无关联商机/);
      await page.locator('#crm-all header button').click();
      const verify=await page.evaluate(async()=>{
        const call=(path,method='GET',data)=>new Promise(resolve=>SalesPreview.request({url:'/api/v1'+path,method,data,header:{Authorization:'Bearer preview-access-manager'},success:resolve}));
        const ids=[];for(let offset=0;offset<975;offset+=100){const r=await call('/visits?limit=100&offset='+offset);if(r.statusCode!==200)throw Error(JSON.stringify(r));ids.push(...r.data.items.map(v=>v.id));}
        const source=SalesPreview.inspect().customers.at(-1);
        const edited=await call('/customers/'+source.id,'PATCH',{industry:'本地浏览器验证标记'});
        const savedKey=Object.keys(localStorage).find(k=>k.startsWith('sales-web:crm-preview:')&&k.includes('-full-'));
        return {visits:ids.length,unique:new Set(ids).size,customer:source.id,saved:edited.statusCode,storageSize:localStorage.getItem(savedKey).length};
      });
      assert.equal(verify.visits,975);assert.equal(verify.unique,975);assert.equal(verify.saved,200);assert.ok(verify.storageSize<10000,'only local edits persist');
      await page.reload();await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='manager');
      assert.equal(await page.evaluate(id=>SalesPreview.inspect().customers.find(c=>c.id===id).industry_code,verify.customer),'本地浏览器验证标记');
      await page.evaluate(()=>SalesPreview.reset());
    }
    assert.deepEqual(errors,[]);assert.deepEqual(api,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
    console.log('CRM_BROWSER_OK:',data.counts,'26 demo histories; partner/unknown preserved; full search and pagination; no business API requests');
  } finally {await context.close();await browser.close();}
});
