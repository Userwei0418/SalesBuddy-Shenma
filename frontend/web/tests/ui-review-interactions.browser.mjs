import {test, before, after} from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server, browser, base;
before(async () => { server=createSalesWebServer({target:'',localLogin:null}); await new Promise(r=>server.listen(0,'127.0.0.1',r)); base=`http://127.0.0.1:${server.address().port}`; browser=await chromium.launch({channel:'chrome',headless:true}); });
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
test('search shortcut, editing and modal guards, help focus return and reduced motion', async () => {
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const blocked=[], errors=[];
  await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base || /^\/api\/|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return route.abort();}return route.continue();});
  const page=await context.newPage(); page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(base+'/?mode=preview#/pages/index/index');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index' && SalesRuntime.app.globalData.session && !SalesRuntime.app._capabilityFlight);
    const search=page.locator('.web-activity-search input');
    await page.locator('#page-title').click(); await page.keyboard.press('/'); await search.waitFor();
    assert.ok(await search.evaluate(e=>e===document.activeElement));
    await page.keyboard.type('/?'); assert.equal(await search.inputValue(),'/?'); assert.equal(await page.locator('#shortcut-help-dialog').evaluate(e=>e.open),false);
    await search.fill(''); await page.locator('#shortcut-help-button').focus(); await page.keyboard.press('?');
    assert.equal(await page.locator('#shortcut-help-dialog').evaluate(e=>e.open),true);
    await page.keyboard.press('/'); assert.ok(await page.locator('#shortcut-help-dialog').evaluate(e=>e.contains(document.activeElement)));
    await page.keyboard.press('Escape'); assert.equal(await page.locator('#shortcut-help-dialog').evaluate(e=>e.open),false);
    assert.ok(await page.locator('#shortcut-help-button').evaluate(e=>e===document.activeElement));
    const focus=await page.locator('#shortcut-help-button').evaluate(e=>({style:getComputedStyle(e).outlineStyle,width:getComputedStyle(e).outlineWidth}));
    assert.equal(focus.style,'solid'); assert.equal(focus.width,'2px');
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await search.evaluate(e=>getComputedStyle(e).transitionDuration),'0s');
    assert.deepEqual(blocked,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } finally {await context.close();}
});
