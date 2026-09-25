/** Preview exits must never present a form that sends enterprise credentials to the demo adapter. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async () => {
  server = createSalesWebServer({target: ''});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel: 'chrome', headless: true});
});
after(async () => { await browser?.close(); await new Promise(resolve => server?.close(resolve)); });
const actor = {workspace_id:'preview-login-test-workspace', user_id:'preview-login-test-live', account_code:'SYNTHETIC_LIVE',
  display_name:'合成企业销售', role:'sales', role_name:'一线销售', scope_name:'仅本人', team_ids:[], team_names:[],
  capabilities:{'customer.read':true,'task.create':true,'visit.create':true}, permission_version:'1'};
const liveAuth = {access_token:'synthetic-preview-boundary-access', refresh_token:'synthetic-preview-boundary-refresh', actor, must_change_password:false, auth_method:'password'};
async function fixture(run, {width=1440, live=false, initialPath, storage={}}={}) {
  const context = await browser.newContext({viewport:{width,height:width<900?844:900},serviceWorkers:'block'});
  await context.addInitScript(values => {for (const [key,value] of Object.entries(values)) sessionStorage.setItem(key,value);},storage);
  const page = await context.newPage(), errors=[], blocked=[], calls=[];
  page.setDefaultTimeout(10000); page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    const request=route.request(), url=new URL(request.url());
    if (url.origin!==base) {blocked.push(url.origin);return route.abort();}
    if (url.pathname==='/connection-status') return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
    if (url.pathname==='/local-login') {blocked.push(url.pathname);return route.abort();}
    if (!url.pathname.startsWith('/api/')) return route.continue();
    calls.push({path:url.pathname,method:request.method(),account:request.postDataJSON?.()?.account_code});
    if (url.pathname==='/api/v1/auth/password/login') {
      return request.postDataJSON().account_code==='SYNTHETIC_LIVE'
        ? route.fulfill({json:liveAuth})
        : route.fulfill({status:401,json:{detail:'合成企业接口：账号未开通'}});
    }
    if (url.pathname==='/api/v1/auth/me') return route.fulfill({json:{actor}});
    if (url.pathname==='/api/v1/auth/logout') return route.fulfill({json:{ok:true}});
    if (url.pathname==='/api/v1/assistant/home') return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
    if (url.pathname==='/api/v1/tasks/overview') return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
    if (['/api/v1/customers','/api/v1/tasks','/api/v1/notifications','/api/v1/directory/colleagues'].includes(url.pathname)) return route.fulfill({json:{items:[],total:0,has_more:false}});
    blocked.push(url.pathname); return route.fulfill({status:503,json:{detail:'Unexpected isolated request'}});
  });
  try {
    await page.goto(base+(initialPath??(live?'/?mode=live#/pages/login/index':'/?mode=preview')));
    await page.waitForFunction(live=>window.SalesRuntime?.current?.route===(live?'pages/login/index':'pages/index/index'),live);
    await run({page,calls});
    assert.deepEqual(errors,[]); assert.deepEqual(blocked,[]);
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } finally {await context.close();}
}
async function exitPreview(page) {
  await page.locator(page.viewportSize().width<900?'#mobile-account-button':'#account-button').click();
  await page.locator('#account-logout').click();
  await page.locator('.wx-modal-mask').getByRole('button',{name:'退出示例',exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/login/index'&&!SalesRuntime.app.globalData.session);
  assert.match(await page.locator('#login-session-notice').textContent(),/示例/,'the logout notice must identify the example workspace');
}
async function assertPreviewEntry(page) {
  assert.equal(await page.evaluate(()=>SALES_MODE),'preview');
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isVisible(),false);
  assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).isVisible(),false);
  for (const selector of ['#preview-entry-choice','#preview-entry-role','#preview-enter','#preview-enter-live']) {
    assert.equal(await page.locator(selector).isVisible(),true,selector+' must be reachable after leaving preview');
  }
  for (const selector of ['#preview-enter','#preview-enter-live']) {
    await page.locator(selector).scrollIntoViewIfNeeded();
    const box=await page.locator(selector).boundingBox(), size=page.viewportSize();
    assert.ok(box&&box.x>=0&&box.y>=0&&box.x+box.width<=size.width&&box.y+box.height<=size.height,selector+' must fit the visible viewport');
  }
  assert.equal(await page.locator('#local-login-choice').isVisible(),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
}
for (const width of [1440,390,320]) test(`${width}px preview logout keeps a clear password-free entry after refresh`,{timeout:45000},()=>fixture(async({page,calls})=>{
  await exitPreview(page); await assertPreviewEntry(page);
  await page.reload(); await page.locator('#preview-entry-choice').waitFor(); await assertPreviewEntry(page);
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null,'refresh must respect explicit logout');
  await page.locator('#preview-entry-role').selectOption('fde'); await page.locator('#preview-enter').click();
  await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.role==='fde');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:preview-role')),'fde');
  assert.deepEqual(calls,[],'preview must not call enterprise authentication');
},{width}));
test('all five preview roles can be selected after logout without entering account credentials',{timeout:60000},()=>fixture(async({page,calls})=>{
  for (const role of ['sales','supervisor','manager','fde','fde_lead']) {
    await exitPreview(page); await assertPreviewEntry(page);
    await page.locator('#preview-entry-role').selectOption(role); await page.locator('#preview-enter').click();
    await page.waitForFunction(role=>window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.role===role,role);
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:preview-role')),role);
    assert.equal(await page.locator('#preview-role').inputValue(),role);
  }
  assert.deepEqual(calls,[]);
}));
test('preview login offers an explicit enterprise switch and sends the next attempt only to the synthetic live endpoint',{timeout:45000},()=>fixture(async({page,calls})=>{
  await exitPreview(page); await page.locator('#preview-enter-live').click();
  await page.waitForFunction(()=>window.SALES_MODE==='live'&&window.SalesRuntime?.current?.route==='pages/login/index');
  assert.equal(await page.locator('#preview-entry-choice').isVisible(),false);
  await page.getByPlaceholder('请输入账号名或手机号').fill('SYNTHETIC_UNOPENED');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  await page.locator('[data-handler="submitLogin"]').click();
  await page.locator('#login-error').filter({hasText:'合成企业接口：账号未开通'}).waitFor();
  assert.deepEqual(calls,[{path:'/api/v1/auth/password/login',method:'POST',account:'SYNTHETIC_UNOPENED'}]);
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
},{width:320}));
test('leaving preview retains the separate existing live session without logging it out or replacing its actor',{timeout:45000},()=>fixture(async({page,calls})=>{
  await page.getByPlaceholder('请输入账号名或手机号').fill('SYNTHETIC_LIVE');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  await page.locator('[data-handler="submitLogin"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.userId==='preview-login-test-live');
  const authBefore=await page.evaluate(()=>sessionStorage.getItem('sales-web:live:v1:salesApiAuth'));
  assert.ok(authBefore);
  await page.locator('#workspace-switch').click(); await page.locator('#choose-preview').click();
  await page.waitForFunction(()=>window.SALES_MODE==='preview'&&window.SalesRuntime?.current?.route==='pages/index/index');
  await exitPreview(page); await page.locator('#preview-enter-live').click();
  await page.waitForFunction(()=>window.SALES_MODE==='live'&&window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.userId==='preview-login-test-live');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:live:v1:salesApiAuth')),authBefore);
  assert.equal(calls.filter(call=>call.path==='/api/v1/auth/password/login').length,1);
  assert.equal(calls.some(call=>call.path==='/api/v1/auth/logout'),false,'preview logout must not terminate the enterprise session');
},{live:true}));

for (const path of ['/#/pages/login/index','/?mode=unknown#/pages/login/index']) test(`mode lifecycle: ${path} never inherits a hidden preview workspace`,{timeout:25000},()=>fixture(async({page,calls})=>{
  assert.equal(await page.evaluate(()=>SALES_MODE),'live');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'live','the visible URL must match the active workspace');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:mode')),null,'remove the obsolete mode cache');
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isVisible(),true);
  assert.equal(await page.locator('#preview-entry-choice').isVisible(),false);
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);
  assert.deepEqual(calls,[]);
},{live:true,initialPath:path,storage:{'sales-web:mode':'preview'}}));

test('mode lifecycle: explicit preview overrides the old live cache and stays explicit after reload',{timeout:25000},()=>fixture(async({page,calls})=>{
  assert.equal(await page.evaluate(()=>SALES_MODE),'preview');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'preview');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:mode')),null);
  await page.reload();
  await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData.session?.role==='sales');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'preview');
  assert.deepEqual(calls,[]);
},{storage:{'sales-web:mode':'live'}}));

test('mode lifecycle: an unsupported cached example role recovers to the same sales identity in both selectors',{timeout:25000},()=>fixture(async({page,calls})=>{
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.role),'sales');
  assert.equal(await page.locator('#preview-role').inputValue(),'sales');
  assert.equal(await page.locator('#preview-entry-role').inputValue(),'sales');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:preview-role')),'sales');
  assert.equal(await page.locator('.web-startup-error').count(),0);
  assert.deepEqual(calls,[]);
},{storage:{'sales-web:preview-role':'retired-demo-role'}}));

test('mode lifecycle: leaving an example object drops its identifier and the old enterprise pending path',{timeout:45000},()=>fixture(async({page,calls})=>{
  const id='00000010-0000-4000-8000-000000000001';
  await page.goto(base+'/?mode=preview#/pages/customer-detail/index?customerId='+id);
  await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/customer-detail/index'&&!SalesRuntime.current.data.loading);
  await page.evaluate(()=>sessionStorage.setItem('sales-web:pending-path:live','/pages/customer-detail/index?customerId=STALE_LIVE_OBJECT'));
  await page.locator('#enter-live').click();
  await page.waitForFunction(()=>window.SALES_MODE==='live'&&window.SalesRuntime?.current?.route==='pages/login/index');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'live');
  assert.equal(new URL(page.url()).hash,'#/pages/login/index');
  assert.ok([null,'/pages/index/index'].includes(await page.evaluate(()=>sessionStorage.getItem('sales-web:pending-path:live'))),'only a neutral overview destination may survive the workspace switch');
  await page.getByPlaceholder('请输入账号名或手机号').fill('SYNTHETIC_LIVE');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  await page.locator('[data-handler="submitLogin"]').click();
  await page.waitForFunction(actorId=>SalesRuntime.current.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.userId===actorId,'preview-login-test-live');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:pending-path:live')),null);
  assert.equal(calls.some(call=>call.path.includes(id)||call.path.includes('STALE_LIVE_OBJECT')),false);
}));

test('mode lifecycle: switching to another example role returns to overview instead of reopening the prior object',{timeout:25000},()=>fixture(async({page,calls})=>{
  await page.goto(base+'/?mode=preview#/pages/customer-detail/index?customerId=00000010-0000-4000-8000-000000000001');
  await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/customer-detail/index'&&!SalesRuntime.current.data.loading);
  await page.locator('#preview-role').selectOption('fde');
  await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.role==='fde');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'preview');
  assert.equal(new URL(page.url()).hash,'#/pages/index/index');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:pending-path:preview')),null);
  assert.deepEqual(calls,[]);
}));

test('mode lifecycle: browser history across workspaces reloads the matching login transport',{timeout:45000},()=>fixture(async({page,calls})=>{
  await page.evaluate(()=>{
    window.__priorPreviewDocument=true;
    // Two same-document history entries expose the mismatch without changing the current adapter.
    history.replaceState(history.state,'','/?mode=live#/pages/login/index');
    history.pushState(history.state,'','/?mode=preview#/pages/index/index');
  });
  await page.goBack();
  await page.waitForFunction(()=>window.SALES_MODE==='live'&&window.SalesRuntime?.current?.route==='pages/login/index');
  assert.equal(await page.evaluate(()=>window.__priorPreviewDocument),undefined,'a workspace transition needs a new adapter/document');
  await page.getByPlaceholder('请输入账号名或手机号').fill('SYNTHETIC_UNOPENED');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  await page.locator('[data-handler="submitLogin"]').click();
  await page.locator('#login-error').filter({hasText:'合成企业接口：账号未开通'}).waitFor();
  assert.deepEqual(calls,[{path:'/api/v1/auth/password/login',method:'POST',account:'SYNTHETIC_UNOPENED'}]);
  await page.goForward();
  await page.waitForFunction(()=>window.SALES_MODE==='preview'&&window.SalesRuntime?.current?.route==='pages/index/index');
  assert.equal(new URL(page.url()).searchParams.get('mode'),'preview');
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.account),'PREVIEW_SALES');
  assert.equal(calls.length,1,'returning to preview must leave enterprise transport idle');
}));
