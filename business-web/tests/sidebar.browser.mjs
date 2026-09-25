/** Sidebar regression checks. Synthetic preview only; real authentication and APIs are blocked. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const base = process.env.APP_URL || 'http://127.0.0.1:5186';
if (!['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname)) throw new Error('Use a local Web server.');

async function inPreview(run) {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const page = await context.newPage(), errors=[], blocked=[];
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',route=>{
    const target = new URL(route.request().url());
    if (target.origin !== new URL(base).origin || target.pathname.startsWith('/api/') || target.pathname === '/local-login') {
      blocked.push(target.origin === new URL(base).origin ? target.pathname : 'external');
      return route.abort();
    }
    if (target.pathname === '/connection-status') return route.fulfill({json:{configured:true,reachable:true,label:'Synthetic connection check'}});
    return route.continue();
  });
  try {
    await page.goto(base+'/?mode=preview');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index' && SalesRuntime.current.data.messages?.length>0);
    await run(page);
    assert.deepEqual(errors,[]);
    assert.deepEqual(blocked,[],'preview checks must not request real services');
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } finally {await context.close();await browser.close();}
}

async function activePaths(page, scope='#desktop-nav') {
  return page.locator(scope+' [aria-current]').evaluateAll(nodes=>nodes.map(node=>node.dataset.path));
}

async function settledScreenshot(page, path, modulePath, fullPage=false) {
  await page.mouse.move(1100,5);
  // Navigation has a 150 ms transition: wait for rendered selection, not only the new aria attribute.
  await page.waitForFunction(modulePath=>{
    const node=document.querySelector('#desktop-nav [aria-current]');
    return node?.dataset.path===modulePath && node.classList.contains('active') && getComputedStyle(node).color==='rgb(40, 99, 205)' && getComputedStyle(node).backgroundColor==='rgb(238, 243, 253)';
  },modulePath);
  await page.screenshot({path,fullPage});
}

test('the permanent sidebar has one visit entry and no duplicated account entry', {timeout:30000}, ()=>inPreview(async page=>{
  const visit = page.locator('.web-sidebar [data-path="pages/visit-entry/index"]:visible');
  assert.equal(await visit.count(),1,'visit must have one permanent sidebar entry');
  assert.equal(await page.locator('#desktop-nav [data-path="pages/profile/index"]').count(),0,'profile belongs to the existing account footer');
  assert.equal(await page.locator('#account-button').isVisible(),true);
  await page.locator('#account-button').click();
  await page.locator('#account-profile').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/profile/index');
  assert.equal(await page.locator('#account-button').getAttribute('aria-current'),'page');
  assert.deepEqual(await activePaths(page),[]);
}));

test('create actions are not a second permanent navigation group', {timeout:30000}, ()=>inPreview(async page=>{
  assert.equal(await page.locator('.web-sidebar #quick-nav .web-quick-item:visible').count(),0,'create actions belong in the top New dialog');
}));

test('FDE contextual pages keep their parent modules without adding duplicate top-level business entries', {timeout:30000}, ()=>inPreview(async page=>{
  assert.equal(await page.locator('#preview-role option[value="supervisor"]').textContent(),'销售主管');
  assert.equal(await page.locator('#preview-role option[value="fde_lead"]').textContent(),'FDE主管');
  await page.locator('#preview-role').selectOption('fde');
  await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='fde'&&SalesRuntime.current?.route==='pages/index/index');
  for(const [child,parent] of [['demo-create','workbench'],['fde-records','bi']]){
    // Missing context exercises a safe source validation message; no business read/write is needed here.
    await page.evaluate(child=>SalesRuntime.route('/pages/'+child+'/index'),child);
    await page.waitForFunction(child=>SalesRuntime.current.route==='pages/'+child+'/index',child);
    assert.deepEqual(await activePaths(page),['pages/'+parent+'/index']);
    assert.equal(await page.locator('#desktop-nav [aria-current]').getAttribute('aria-current'),'location');
    assert.equal(await page.locator('#desktop-nav [data-path="pages/'+child+'/index"]').count(),0);
    assert.equal(await page.locator('#back-button').isVisible(),true);
    await page.locator('#back-button').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  }
}));

test('visit navigation has exactly one selected sidebar entry', {timeout:30000}, ()=>inPreview(async page=>{
  await page.locator('#desktop-nav [data-path="pages/visit-entry/index"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-entry/index');
  assert.deepEqual(await activePaths(page,'.web-sidebar'),['pages/visit-entry/index']);
}));

for(const [child,parent] of [
  ['opportunity-create','workbench'],
  ['customer-create','customers'],
  ['management-task-create','tasks'],
]) {
  test(`${child} keeps its owning ${parent} module selected`, {timeout:30000}, ()=>inPreview(async page=>{
    // Hash navigation opens the original source Page without performing a business write.
    await page.goto(base+'/?mode=preview#/pages/'+child+'/index');
    await page.waitForFunction(child=>SalesRuntime.current?.route==='pages/'+child+'/index',child);
    assert.deepEqual(await activePaths(page),['pages/'+parent+'/index']);
    assert.equal(await page.locator('#desktop-nav [aria-current]').getAttribute('aria-current'),'location');
  }));
}

test('desktop New opens the original source forms and restores focus when closed', {timeout:30000}, ()=>inPreview(async page=>{
  const trigger=page.locator('#desktop-actions');
  assert.equal(await trigger.isVisible(),true);
  assert.equal(await page.locator('#quick-dialog').isVisible(),false);
  await trigger.click();
  assert.deepEqual(await page.locator('#mobile-quick-actions [data-path]').evaluateAll(nodes=>nodes.map(node=>node.dataset.path)),[
    'pages/visit-entry/index','pages/opportunity-create/index','pages/management-task-create/index','pages/customer-create/index'
  ]);
  assert.equal(await page.locator('#quick-dialog [aria-current]').count(),0,'actions must not be marked as selected module navigation');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#quick-dialog').isVisible(),false);
  assert.equal(await trigger.evaluate(node=>document.activeElement===node),true);
  for(const [route,parent] of [['opportunity-create','workbench'],['management-task-create','tasks'],['customer-create','customers'],['visit-entry','visit-entry']]) {
    await trigger.click();
    await page.locator('#mobile-quick-actions [data-path="pages/'+route+'/index"]').click();
    await page.waitForFunction(route=>SalesRuntime.current.route==='pages/'+route+'/index',route);
    assert.equal(await page.locator('#quick-dialog').isVisible(),false);
    assert.deepEqual(await activePaths(page),['pages/'+parent+'/index']);
    await page.locator('#desktop-nav [data-path="pages/index/index"]').click();
    await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  }
  await settledScreenshot(page,'/tmp/sales-sidebar-desktop.png','pages/index/index',true);
}));

test('desktop actions respect the FDE role and close when the synthetic actor has no create permissions', {timeout:30000}, ()=>inPreview(async page=>{
  await page.locator('#preview-role').selectOption('fde');
  await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='fde'&&SalesRuntime.current?.route==='pages/index/index');
  await page.locator('#desktop-actions').click();
  const paths=await page.locator('#mobile-quick-actions [data-path]').evaluateAll(nodes=>nodes.map(node=>node.dataset.path));
  assert.ok(paths.includes('pages/visit-entry/index'));
  for(const route of ['customer-create','customer-assign-confirm','opportunity-create']) assert.ok(!paths.includes('pages/'+route+'/index'),route+' must not be granted to FDE by the Web shell');
  // A synthetic permission update exercises the zero-action boundary without authenticating or changing a server actor.
  await page.evaluate(()=>{
    SalesRuntime.app.globalData.session.capabilities={};
    window.dispatchEvent(new CustomEvent('sales:navigation',{detail:{path:SalesRuntime.current.route}}));
  });
  assert.equal(await page.locator('#quick-dialog').isVisible(),false);
  assert.equal(await page.locator('#desktop-actions').isVisible(),false);
  assert.equal(await page.locator('#mobile-actions').isVisible(),false);
  assert.equal(await page.locator('#desktop-nav [data-path="pages/visit-entry/index"]').isVisible(),false);
}));

test('short desktop screens keep module navigation, account access and New actions reachable', {timeout:30000}, ()=>inPreview(async page=>{
  await page.setViewportSize({width:1280,height:540});
  const account=page.locator('#account-button');
  const assertInside=async locator=>{
    const rect=await locator.boundingBox();
    assert.ok(rect&&rect.x>=0&&rect.y>=0&&rect.x+rect.width<=1280&&rect.y+rect.height<=540,'control must fit the short viewport');
  };
  await assertInside(account);
  await page.locator('#desktop-nav [data-path="pages/bi/index"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/bi/index');
  await assertInside(page.locator('#desktop-nav [data-path="pages/bi/index"]'));
  await assertInside(account);
  await account.click();
  await page.locator('#account-profile').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/profile/index');
  await page.locator('#desktop-actions').click();
  await assertInside(page.locator('#close-quick'));
  await page.locator('#mobile-quick-actions [data-path="pages/customer-create/index"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-create/index');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await settledScreenshot(page,'/tmp/sales-sidebar-short.png','pages/customers/index');
}));

test('home and dashboard selection follows the actual route after clicks, hover and browser Back', {timeout:30000}, ()=>inPreview(async page=>{
  const home = page.locator('#desktop-nav [data-path="pages/index/index"]');
  const dashboard = page.locator('#desktop-nav [data-path="pages/bi/index"]');
  await dashboard.click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/bi/index');
  assert.deepEqual(await activePaths(page),['pages/bi/index']);
  await home.click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  await dashboard.hover();
  assert.deepEqual(await activePaths(page),['pages/index/index']);
  assert.ok(new URL(page.url()).hash.includes('/pages/index/index'));
  await page.waitForFunction(()=>{
    const node=document.querySelector('#desktop-nav [data-path="pages/bi/index"]');
    const rgb=getComputedStyle(node).color.match(/\d+/g).map(Number);
    return node.matches(':hover') && !node.classList.contains('active') && Math.max(...rgb)-Math.min(...rgb)<90;
  });
  await page.goBack();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/bi/index');
  assert.deepEqual(await activePaths(page),['pages/bi/index']);
}));
