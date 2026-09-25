/** Synthetic live-mode account transitions. No configured accounts or upstream requests. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async () => { server=createSalesWebServer({target:''}); await new Promise(r=>server.listen(0,'127.0.0.1',r)); base=`http://127.0.0.1:${server.address().port}`; browser=await chromium.launch({channel:'chrome',headless:true}); });
after(async () => { await browser?.close(); await new Promise(r=>server?.close(r)); });
const actors = {
  a:{workspace_id:'synthetic-workspace',user_id:'synthetic-a',account_code:'ACCOUNT_A',display_name:'合成销售甲',role:'sales',role_name:'一线销售',scope_name:'仅本人',team_ids:['synthetic-team-a'],team_names:['合成一组'],capabilities:{'customer.read':true,'task.create':true,'visit.create':true},permission_version:'1'},
  b:{workspace_id:'synthetic-workspace',user_id:'synthetic-b',account_code:'ACCOUNT_B',display_name:'合成总监乙',role:'supervisor',role_name:'销售总监',scope_name:'直属团队',team_ids:['synthetic-team-b'],team_names:['合成二组'],capabilities:{'customer.read':true,'task.create':false,'visit.create':false},permission_version:'2'}
};
const auth = (id, change=false) => ({access_token:`synthetic-${id}`,refresh_token:`synthetic-refresh-${id}`,actor:actors[id],must_change_password:change,auth_method:'password'});
async function fixture(run,{width=1440,change=false,slowLogout=false,slowActor=false}={}) {
  const context=await browser.newContext({viewport:{width,height:width<900?844:1000},serviceWorkers:'block'});
  const calls=[], unexpected=[], errors=[]; let heldLogout,heldActor;
  await context.route('**/*',route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.origin!==base) {unexpected.push(url.origin);return route.abort();}
    if(url.pathname==='/connection-status') return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:true,role:'sales'}}});
    if(url.pathname!=='/local-login'&&!url.pathname.startsWith('/api/'))return route.continue();
    calls.push({path:url.pathname,method:request.method(),actor:request.headers().authorization});
    if(url.pathname==='/local-login')return route.fulfill({json:auth('a',change)});
    if(url.pathname==='/api/v1/auth/password/login'){
      const data=request.postDataJSON(),id=data.account_code==='ACCOUNT_B'?'b':'a';
      if(data.password==='wrong')return route.fulfill({status:401,json:{detail:'合成登录：账号或密码错误'}});
      return route.fulfill({json:auth(id,change&&id==='a')});
    }
    const id=request.headers().authorization==='Bearer synthetic-b'?'b':'a';
    if(url.pathname==='/api/v1/auth/me'){
      if(slowActor&&id==='a'&&!heldActor){heldActor=route;return;}
      return route.fulfill({json:{actor:actors[id]}});
    }
    if(url.pathname==='/api/v1/auth/logout') {if(slowLogout){heldLogout=route;return;}return route.fulfill({status:503,json:{detail:'合成注销离线'}});}
    if(url.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
    if(url.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
    if(['/api/v1/customers','/api/v1/tasks','/api/v1/notifications','/api/v1/directory/colleagues'].includes(url.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
    if(url.pathname.startsWith('/api/v1/profile/'))return route.fulfill({status:503,json:{detail:'合成画像不可用'}});
    unexpected.push(url.pathname);return route.fulfill({status:503,json:{detail:'Unexpected isolated request'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
  try {
    await page.goto(base+'/?mode=live');await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index'&&document.querySelector('#login-connection-status').dataset.state==='ready');
    await run({page,calls,releaseLogout:()=>heldLogout.fulfill({status:503,json:{detail:'合成注销失败'}}),releaseActor:()=>heldActor.fulfill({json:{actor:actors.a}})});
    assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } catch (error) {
    const state = await page.evaluate(() => ({route:SalesRuntime.current?.route,role:SalesRuntime.current?.data.selectedRole,account:SalesRuntime.current?.data.account,agreed:SalesRuntime.current?.data.agreed,passwordLength:SalesRuntime.current?.data.password?.length,loading:SalesRuntime.current?.data.loading,sessionRole:SalesRuntime.app.globalData.session?.role,loginError:document.getElementById('login-error')?.textContent}));
    console.error('Synthetic diagnostic: ' + JSON.stringify({state,calls,unexpected,errors}));
    error.message += '\nSynthetic diagnostic: ' + JSON.stringify({state,calls});
    throw error;
  } finally {await context.close();}
}
async function quickLogin(page) {await page.locator('#local-login-agreement').check();await page.locator('#local-login-button').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');}
async function openAccount(page) {await page.locator((await page.viewportSize()).width<900?'#mobile-account-button':'#account-button').click();await page.locator('#account-dialog').waitFor();}
async function confirmExit(page,intent='switch') {await openAccount(page);await page.locator('#account-'+intent).click();await page.locator('.wx-modal-mask').getByRole('button',{name:intent==='switch'?'切换账号':'退出登录',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/login/index'&&!SalesRuntime.app.globalData.session);}
async function loginB(page) {await page.getByPlaceholder('请输入账号名或手机号').fill('ACCOUNT_B');await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');if(await page.locator('[data-handler="toggleAgreement"]').getAttribute('aria-checked')!=='true') await page.locator('[data-handler="toggleAgreement"] .checkbox').click();await page.locator('[data-handler="submitLogin"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.userId==='synthetic-b');}

for (const width of [1440,390]) test(`${width}px account menu cancels safely, then switches shortcut A to typed B without stale navigation`,{timeout:45000},()=>fixture(async({page,calls})=>{
  await quickLogin(page);await page.evaluate(()=>SalesRuntime.route('/pages/tasks/index?tab=pending',{tab:true}));
  await openAccount(page);assert.equal(calls.some(c=>c.path.startsWith('/api/v1/profile/')),false,'account menu must not load profile review');
  await page.locator('#account-switch').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'继续使用'}).click();
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.userId),'synthetic-a');
  await page.evaluate(()=>{SalesRuntime.wx.setStorageSync('pendingOpenCustomerId','old-account-customer');SalesRuntime.wx.setStorageSync('visitEntryDraft:synthetic-workspace:synthetic-a',{text:'account scoped draft'});});
  await confirmExit(page);
  await page.waitForFunction(()=>document.activeElement?.getAttribute('placeholder')==='请输入账号名或手机号');
  assert.equal(await page.locator('#local-login-button').isVisible(),false);
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').inputValue(),'');
  assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).inputValue(),'');
  assert.equal(await page.evaluate(()=>!!SalesRuntime.wx.getStorageSync('salesApiAuth')),false);
  assert.equal(await page.evaluate(()=>!!SalesRuntime.wx.getStorageSync('pendingOpenCustomerId')),false);
  assert.equal(await page.evaluate(()=>!!SalesRuntime.wx.getStorageSync('visitEntryDraft:synthetic-workspace:synthetic-a')),true);
  await page.goBack();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/login/index');
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:pending-path:live')),null);
  await page.reload();await page.getByPlaceholder('请输入账号名或手机号').waitFor();
  assert.equal(await page.locator('#local-login-button').isVisible(),false,'manual login stays open after reload');
  await loginB(page);assert.equal(await page.evaluate(()=>SalesRuntime.app.can('task.create')),false);
  await page.goBack();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.userId),'synthetic-b');
  await confirmExit(page,'logout');assert.equal(await page.locator('#login-session-notice').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
}, {width}));

test('forced password change can exit without changing credentials or bypassing the requirement',{timeout:30000},()=>fixture(async({page,calls})=>{
  await page.locator('#local-login-agreement').check();await page.locator('#local-login-button').click();await page.getByPlaceholder('请输入12–128位新密码').waitFor();
  await page.locator('#cancel-password-change').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'切换账号',exact:true}).click();
  await page.waitForFunction(()=>!SalesRuntime.app.globalData.session&&!SalesRuntime.current.data.mustChangePassword);
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').isEnabled(),true);
  assert.equal(await page.locator('#cancel-password-change').isVisible(),false);
  await loginB(page);assert.equal(calls.filter(c=>c.path==='/api/v1/auth/password').length,0);
}, {change:true}));

test('slow logout and old actor responses do not block or overwrite the next account',{timeout:30000},()=>fixture(async({page,calls,releaseLogout,releaseActor})=>{
  await quickLogin(page);await page.waitForFunction(()=>!!SalesRuntime.app._capabilityFlight);
  await confirmExit(page);await loginB(page);
  await page.waitForFunction(()=>!SalesRuntime.app._capabilityFlight);
  assert.ok(calls.some(c=>c.path==='/api/v1/auth/me'&&c.actor==='Bearer synthetic-b'));
  await releaseActor();await releaseLogout();await page.waitForTimeout(100);
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.userId),'synthetic-b');
  assert.equal(await page.evaluate(()=>SalesRuntime.wx.getStorageSync('salesApiAuth').actor.user_id),'synthetic-b');
}, {slowLogout:true,slowActor:true}));

test('source profile logout also opens blank manual login; failed re-login remains editable',{timeout:30000},()=>fixture(async({page})=>{
  await quickLogin(page);await openAccount(page);await page.locator('#account-profile').click();
  await page.locator('[data-handler="logout"]').click();await page.locator('.wx-modal-mask').getByRole('button',{name:'退出登录',exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/login/index'&&!SalesRuntime.app.globalData.session);
  await page.getByPlaceholder('请输入账号名或手机号').fill('ACCOUNT_B');await page.getByPlaceholder('请输入密码',{exact:true}).fill('wrong');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();await page.locator('[data-handler="submitLogin"]').click();await page.locator('#login-error').filter({hasText:'账号或密码错误'}).waitFor();
  assert.equal(await page.getByPlaceholder('请输入账号名或手机号').inputValue(),'ACCOUNT_B');assert.equal(await page.locator('#local-login-button').isVisible(),false);
  await loginB(page);
}));

test('account dialog keyboard wraps and Escape restores the trigger',{timeout:30000},()=>fixture(async({page})=>{
  await quickLogin(page);await openAccount(page);
  for(let i=0;i<8;i++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#account-dialog')),true);}
  await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('#account-dialog').open);
  await page.waitForFunction(()=>document.activeElement===document.querySelector('#account-button'));
  assert.equal(await page.locator('#account-button').getAttribute('aria-expanded'),'false');
  assert.equal(await page.locator('#account-button').evaluate(el=>document.activeElement===el),true);
}));
