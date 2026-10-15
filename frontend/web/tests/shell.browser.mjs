/** SaaS shell checks use only isolated local preview; API requests are blocked. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const url = process.env.APP_URL || 'http://127.0.0.1:5186';
if (!['localhost','127.0.0.1','[::1]'].includes(new URL(url).hostname)) throw new Error('Use a local Web server.');
async function inBrowser(run) {
  const browser = await chromium.launch({channel:'chrome',headless:true});
  const context = await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const page = await context.newPage(), errors=[], apiRequests=[];
  page.setDefaultTimeout(10000);
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',route=>{
    const target=new URL(route.request().url());
    if(target.origin!==new URL(url).origin) return route.abort();
    if(target.pathname.startsWith('/api/')||target.pathname==='/local-login') {apiRequests.push(target.pathname);return route.abort();}
    if(target.pathname==='/connection-status') return route.fulfill({json:{configured:true,reachable:true,label:'Synthetic connection check',checkedAt:new Date().toISOString()}});
    return route.continue();
  });
  try {await run(page);assert.deepEqual(errors,[]);assert.deepEqual(apiRequests,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);}
  finally {await context.close();await browser.close();}
}
test('fresh SaaS entry requires enterprise login and exposes preview as an explicit secondary action', {timeout:45000}, ()=>inBrowser(async page=>{
  await page.goto(url);
  await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index');
  await page.locator('#login-connection-status').getByText('服务已连接 · 请登录',{exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>SALES_MODE),'live');
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);
  assert.equal(await page.locator('#workspace-dialog').evaluate(el=>el.open),false);
  assert.equal(await page.locator('.web-sidebar').isVisible(),false);
  assert.equal(await page.locator('.web-auth-intro').isVisible(),true);
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isVisible(),true);
  assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).isVisible(),true);
  assert.equal(await page.locator('[data-handler="selectRole"]').count(),0);
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),false);
  assert.match(await page.locator('.web-auth-form-heading > p').textContent(),/由账号自动识别/);
  await page.locator('#login-preview').click();
  await page.waitForFunction(()=>window.SALES_MODE==='preview'&&window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.current.data.messages?.length>0);
  assert.equal(await page.locator('#preview-notice').isVisible(),true);
  await page.locator('#enter-live').click();
  await page.waitForFunction(()=>window.SALES_MODE==='live'&&window.SalesRuntime?.current?.route==='pages/login/index');
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#login-preview').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
}));
test('grouped navigation and simultaneous customer map/list retain real page state across desktop and mobile layouts', {timeout:45000}, ()=>inBrowser(async page=>{
  await page.goto(url+'/?mode=preview');
  await page.waitForFunction(()=>window.SalesRuntime?.current?.data.messages?.length>0);
  for(const route of ['tasks','workbench','bi','profile','visit-entry','customers']){
    await page.locator(route === 'profile' ? '#account-button' : `#desktop-nav [data-path="pages/${route}/index"]`).click();
    if (route === 'profile') await page.locator('#account-profile').click();
    await page.waitForFunction(route=>SalesRuntime.current.route===`pages/${route}/index`,route);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,route+' must fit desktop');
  }
  await page.locator('.customer-card').first().waitFor();
  const pageId=await page.evaluate(()=>SalesRuntime.current._id);
  assert.equal(await page.locator('.battle-section').isVisible(),true);
  assert.equal(await page.locator('#customer-view-switch,[data-customer-view]').count(),0);
  await page.getByPlaceholder('搜索客户或负责人').fill('星河');
  await page.waitForFunction(()=>SalesRuntime.current.data.keyword==='星河');
  assert.equal(await page.locator('.battle-section').isVisible(),true);
  assert.equal(await page.getByPlaceholder('搜索客户或负责人').inputValue(),'星河');
  assert.equal(await page.evaluate(()=>SalesRuntime.current._id),pageId);
  await page.locator('.customer-card').filter({hasText:'星河'}).click();
  await page.locator('[data-handler="editCustomer"]').waitFor();
  await page.locator('[data-handler="editCustomer"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/customer-edit/index'&&!SalesRuntime.current.data.loading);
  const submit=page.locator('button[data-handler="submit"]');
  await submit.scrollIntoViewIfNeeded();
  const point=await submit.boundingBox();
  assert.ok(point&&point.x>=232&&point.x+point.width<=1440,'fixed submit bar must clear the desktop sidebar');
  await page.locator('#desktop-nav [data-path="pages/customers/index"]').click();
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.locator('#customer-view-switch,[data-customer-view]').count(),0);
  assert.equal(await page.locator('.battle-section').isVisible(),true,'mobile keeps the original map and list layout');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
}));

const APP_URL = url;

test('opening the source file gives a working launch entry without loading a missing business bundle', async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage(); const errors = [], requests = [];
    await page.route('**/*', route => {
      const target = new URL(route.request().url());
      return target.protocol === 'file:' ? route.continue() : route.abort();
    });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => requests.push(request.url()));
    await page.goto(new URL('../index.html', import.meta.url).href);
    await page.getByRole('heading', {name: '启动商汤销售小浣熊 Web'}).waitFor();
    assert.equal(await page.getByRole('link', {name: '进入本地工作空间 →'}).getAttribute('href'), 'http://127.0.0.1:5186/');
    assert.ok((await page.locator('body').innerText()).includes('启动小浣熊SalesBuddy.command'));
    assert.equal(requests.some(url => /bundle\.js|preview-api\.js|\/api\//.test(url)), false);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('mobile quick actions open source forms and follow the active role permissions', {timeout: 30000}, async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 844}});
    await page.route('**/*', route => {
      const target = new URL(route.request().url());
      return target.origin === new URL(APP_URL).origin && !target.pathname.startsWith('/api/') && target.pathname !== '/local-login' ? route.continue() : route.abort();
    });
    await page.goto(APP_URL + '/?mode=preview');
    await page.getByRole('button', {name: '快捷操作', exact: true}).click();
    await page.locator('#mobile-quick-actions').getByRole('button', {name: '客户建档', exact: true}).click();
    await page.locator('#page-root [data-handler="openEditor"]').first().waitFor();
    assert.equal(await page.evaluate(() => SalesRuntime.current.route), 'pages/customer-create/index');
    assert.equal(await page.locator('#quick-dialog').isVisible(), false);
    await page.locator('#mobile-nav [data-path="pages/index/index"]').click();
    await page.locator('#preview-role').selectOption('fde');
    await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session?.role === 'fde');
    await page.getByRole('button', {name: '快捷操作', exact: true}).click();
    assert.equal(await page.locator('#mobile-quick-actions [data-path="pages/customer-create/index"]').count(), 0);
    assert.equal(await page.locator('#mobile-quick-actions [data-path="pages/customer-assign-confirm/index"]').count(), 0);
    assert.equal(await page.locator('#mobile-quick-actions [data-path="pages/visit-entry/index"]').count(), 1);
    await page.getByRole('button', {name: '关闭快捷操作'}).click();
    assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
  } finally {await browser.close();}
});

for (const viewport of [{width: 1440, height: 1000}, {width: 390, height: 844}]) {
  test(`${viewport.width}px business footer buttons remain within the workspace and reachable`, {timeout: 45000}, async () => {
    const browser = await chromium.launch({headless: true, channel: 'chrome'});
    try {
      const page = await browser.newPage({viewport});
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        return url.origin === new URL(APP_URL).origin && !url.pathname.startsWith('/api/') && url.pathname !== '/local-login' ? route.continue() : route.abort();
      });
      await page.goto(APP_URL + '/?mode=preview');
      await page.waitForFunction(() => window.SalesRuntime?.app?.globalData?.session?.role === 'sales');
      await page.goto(APP_URL + '/?mode=preview#/pages/customers/index');
      const firstCustomer = page.locator('.customer-card[data-handler="openCustomer"]').first();
      await firstCustomer.waitFor();
      const customerId = await firstCustomer.getAttribute('data-id');
      assert.ok(customerId, 'Customer selection uses the rendered customer id');
      const routes = [
        ['management-task-create', '', '.bottom-bar button'], ['customer-claim', '', '.claim-confirm'],
        ['customer-create', '', '.bottom-bar button'],
        ['customer-edit', '?customerId=' + customerId, '.submit-bar button']
      ];
      for (const [route, query, selector] of routes) {
        await page.goto(APP_URL + '/?mode=preview#/pages/' + route + '/index' + query);
        const button = page.locator('#page-root ' + selector).last(); await button.waitFor();
        await page.waitForTimeout(150);
        const box = await button.boundingBox(); assert.ok(box, route);
        assert.ok(box.x >= (viewport.width > 900 ? 224 : 0), route + ' avoids sidebar');
        assert.ok(box.x + box.width <= viewport.width + 1, route + ' within right edge');
        assert.ok(box.y + box.height <= viewport.height - (viewport.width > 900 ? 0 : 66) + 1, route + ' avoids mobile nav');
        assert.equal(await button.evaluate(el => {const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2));}), true, route + ' center is reachable');
      }
      assert.deepEqual(await page.evaluate(() => SalesRuntime.errors), []);
    } finally { await browser.close(); }
  });
}
