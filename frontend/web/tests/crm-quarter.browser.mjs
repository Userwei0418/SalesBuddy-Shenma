/** Local CRM screenshot regression: missing dates must not look like zero business. */
import test from 'node:test';
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base=process.env.APP_URL || 'http://127.0.0.1:5196';
assert.ok(['localhost','127.0.0.1'].includes(new URL(base).hostname));

test('quarter selection distinguishes unavailable date statistics from zero opportunities', {timeout:45000}, async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  try {
    await page.goto(base+'/?mode=preview#/pages/workbench/index');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.data.opportunityTotal===489);
    for(const quarter of [1,2,3]) await page.locator(`.quarter-option[data-scope="summary"][data-value="${quarter}"]`).click();
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    await page.waitForFunction(()=>SalesRuntime.current.data.summaryQuarter.quarters.length===3 && SalesRuntime.current.data.opportunityDataReady && !SalesRuntime.current.data.opportunityOverviewLoading);
    // Local source labels are applied on the next animation frame after native rendering.
    await page.waitForFunction(()=>[...document.querySelectorAll('.opportunity-summary-value')].slice(0,3).every(n=>n.textContent.includes('—')), null, {timeout:5000});
    const values=await page.locator('.opportunity-summary-value').allTextContents();
    console.log('Q1-Q3 rendered metrics:',values);
    assert.ok(values.slice(0,3).every(v=>v.includes('—')), 'missing close dates must show unavailable, not three misleading zeroes');
    assert.match(await page.locator('#crm-quarter-explanation').innerText(),/300.*季度|季度.*300/);
    assert.match(await page.locator('#crm-quarter-explanation').innerText(),/年份/);
    // Changing a list filter rerenders the shared page; unavailable overview data
    // must remain unavailable after that rerender, with its original scope intact.
    const selection=await page.evaluate(()=>SalesRuntime.current.data.summaryQuarter);
    await page.evaluate(()=>SalesRuntime.current.toggleOpportunityStage({currentTarget:{dataset:{value:'qualified'}}}));
    await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
    await page.waitForFunction(()=>!SalesRuntime.current.data.opportunityListLoading);
    await page.waitForFunction(()=>[...document.querySelectorAll('.opportunity-summary-value')].slice(0,3).every(n=>n.textContent.includes('日期不足')));
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.summaryQuarter),selection);
    await page.locator('#crm-show-all-periods').click();
    await page.waitForFunction(()=>SalesRuntime.current.data.summaryQuarter.quarters.length===0 && !SalesRuntime.current.data.opportunityOverviewLoading);
    await page.waitForFunction(()=>document.querySelector('.opportunity-summary-value')?.textContent==='25个');
    assert.deepEqual((await page.locator('.opportunity-summary-value').allTextContents()).slice(0,3).map(v=>Number(v.replace(/\D/g,''))),[25,489,337]);
  } finally {await browser.close();}
});
