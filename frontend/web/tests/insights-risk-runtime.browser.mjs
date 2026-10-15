// Complete generated Web runtime + synthetic preview adapter. No server, database, or live API.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root=fileURLToPath(new URL('../',import.meta.url));
const dist=path.resolve(root,process.env.SALES_WEB_DIST_DIR || '.runtime/insights-risk-build');
const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.json':'application/json','.png':'image/png','.woff2':'font/woff2'};

test('generated runtime risk decision persists without closing; BI opens shared source details', {timeout:60000},async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:1470,height:1000},serviceWorkers:'block'});
  const page=await context.newPage(),errors=[],blocked=[];
  page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='https://sales-runtime.test' || url.pathname.startsWith('/api/') || url.pathname==='/local-login') {blocked.push(url.pathname);return route.abort();}
    if(url.pathname==='/web-capabilities')return route.fulfill({json:{previewOnly:true,adminUrl:''}});
    if(url.pathname==='/backend-status')return route.fulfill({json:{configured:false,previewOnly:true}});
    const file=path.resolve(dist,'.'+(url.pathname==='/'?'/index.html':url.pathname));
    if(!file.startsWith(dist+path.sep))return route.abort();
    try {return route.fulfill({body:await fs.readFile(file),contentType:types[path.extname(file)] || 'application/octet-stream'});}catch {return route.fulfill({status:404,body:''});}
  });
  try {
    await page.goto('https://sales-runtime.test/?mode=preview#/pages/risks/index');
    await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session?.role==='sales' && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(()=>SalesRuntime.route('/pages/risks/index'));
    await page.waitForFunction(()=>SalesRuntime.current?.route==='pages/risks/index' && !SalesRuntime.current.data.loading && SalesRuntime.current.data.summary);
    const info=await page.evaluate(()=>({summary:SalesRuntime.current.data.summary,rows:SalesRuntime.current.data.filteredRisks}));
    assert.ok(info.summary.total>0);assert.equal(info.rows.length,info.summary.open_count);
    await page.getByText(info.rows[0].title,{exact:true}).click();
    await page.getByRole('button',{name:'确认处理建议',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'确认解除风险',exact:true}).count(),0);
    assert.equal(await page.locator('.pc-risk-detail input[type="radio"]:checked').count(),0,'初始不能假选中采纳');
    await page.getByText('采纳建议',{exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current.data.decisionChoice==='adopt');
    await page.getByText('暂不采纳',{exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current.data.decisionChoice==='defer');
    await page.getByText('采纳建议',{exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current.data.decisionChoice==='adopt');
    await page.getByPlaceholder('说明采纳后的安排，或暂不采纳的原因').fill('合成验收：本周核实预算审批负责人');
    await page.getByRole('button',{name:'确认处理建议',exact:true}).click();
    await page.waitForFunction(()=>window.SalesRuntime?.current?.data.risk?.current_decision?.decision==='adopt');
    assert.notEqual(await page.evaluate(()=>SalesRuntime.current.data.risk.status),'resolved');
    await page.reload();
    await page.waitForFunction(()=>window.SalesRuntime?.current?.data.risk?.current_decision?.decision==='adopt');
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.risk.events.filter(e=>e.event_type==='suggestion_decided').length),1);
    await page.evaluate(()=>SalesRuntime.route('/pages/bi/index'));
    await page.waitForFunction(()=>SalesRuntime.current?.route==='pages/bi/index' && !SalesRuntime.current.data.loading && Object.keys(SalesRuntime.current.data.metricDetails || {}).length===8);
    assert.equal(await page.locator('.pc-bi .pc-kpi.is-link').count(),8);
    await page.locator('.pc-bi .pc-kpi.is-link').first().click();
    await page.getByText('本明细按季度汇总展示，不代表逐笔实际记录。',{exact:true}).waitFor();
    await page.waitForFunction(()=>{const el=document.querySelector('.ant-drawer-content-wrapper');if(!el)return false;const b=el.getBoundingClientRect();return b.width>0 && b.left>=0 && b.right<=innerWidth+1;});
    assert.equal(await page.getByRole('button',{name:'查看记录',exact:true}).count(),0);
    assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors || []),[]);
    if(process.env.RISK_SHOTS){await fs.mkdir(process.env.RISK_SHOTS,{recursive:true});await page.screenshot({path:process.env.RISK_SHOTS+'/generated-bi-details.png'});}
  } catch(error) {
    console.error('SYNTHETIC_RUNTIME_DIAGNOSTIC',await page.evaluate(()=>({route:window.SalesRuntime?.current?.route,data:window.SalesRuntime?.current?.data,errors:window.SalesRuntime?.errors,text:document.body.innerText.slice(-1800)})));
    throw error;
  } finally {await context.close();await browser.close();}
});
