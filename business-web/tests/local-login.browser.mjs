/** One-click login UI tests use intercepted local fixtures only; never the configured account. */
import {test} from 'node:test';
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
const APP_URL=process.env.APP_URL||'http://127.0.0.1:5186',origin=new URL(APP_URL).origin;
if(!['localhost','127.0.0.1','[::1]'].includes(new URL(APP_URL).hostname))throw new Error('Use a local frontend.');
const actor={workspace_id:'00000000-0000-4000-8000-000000000001',user_id:'00000000-0000-4000-8000-000000000002',account_code:'LOCAL_UI_FIXTURE',display_name:'本机界面测试',role:'sales',role_name:'一线销售',scope_name:'仅本人',team_ids:[],team_names:['本机测试'],capabilities:{'customer.read':true,'visit.create':true,'opportunity.edit':false},permission_version:'fixture'};
const auth={access_token:'local-ui-fixture',refresh_token:'local-ui-fixture-refresh',token_type:'bearer',expires_at:new Date(Date.now()+3600000).toISOString(),auth_method:'password',must_change_password:false,actor};
async function fixture({width=1440,metadata={available:true,role:'sales',label:'交付账号 · 一线销售'},mode='success',preview=false}={},run){
 const browser=await chromium.launch({channel:'chrome',headless:true});const context=await browser.newContext({viewport:{width,height:width===390?844:1000},serviceWorkers:'block'});
 const requests=[],unexpected=[],errors=[];let localPending,manualPending;
 await context.route('**/*',route=>{
  const request=route.request(),url=new URL(request.url());if(url.origin!==origin){unexpected.push(url.origin);return route.abort();}
  if(url.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,...(metadata?{localQuickLogin:metadata}:{})}});
  if(url.pathname==='/local-login'){
   requests.push({kind:'local',method:request.method(),body:request.postData(),authorization:request.headers().authorization});
   if(mode==='pending'){localPending=route;return;}
   if(mode==='failure')return route.fulfill({status:503,json:{detail:'交付账号暂不可用（本机测试）'}});
   return route.fulfill({json:{...auth,must_change_password:mode==='change'}});
  }
  if(!url.pathname.startsWith('/api/'))return route.continue();
  if(url.pathname==='/api/v1/auth/password/login'){
   requests.push({kind:'manual'});if(mode==='manual-pending'){manualPending=route;return;}
   return route.fulfill({status:401,json:{detail:'手填登录失败（本机测试）'}});
  }
  if(url.pathname==='/api/v1/auth/me')return route.fulfill({json:{actor}});
  if(url.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(url.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  if(['/api/v1/customers','/api/v1/notifications'].includes(url.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
  unexpected.push(url.pathname);return route.fulfill({status:503,json:{detail:'Missing isolated fixture'}});
 });
 const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto(APP_URL+(preview?'/?mode=preview':'/?mode=live#/pages/login/index'));
  await page.waitForFunction(preview=>window.SalesRuntime?.current?.route===(preview?'pages/index/index':'pages/login/index'),preview);
  if(!preview)await page.waitForFunction(()=>document.querySelector('#login-connection-status').dataset.state==='ready');
  await run({page,requests,releaseLocal:()=>localPending.fulfill({json:auth}),releaseManual:()=>manualPending.fulfill({status:401,json:{detail:'手填登录失败（本机测试）'}})});
  assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  for(const request of requests.filter(request=>request.kind==='local')){assert.equal(request.method,'POST');assert.equal(request.body,null);assert.equal(request.authorization,undefined);}
 }finally{await context.close();await browser.close();}
}
for(const width of [1440,390])test(`${width}px explicit one-click login sends no credentials and preserves the returned actor permissions`,{timeout:30000},()=>fixture({width,mode:'pending'},async({page,requests,releaseLocal})=>{
 const button=page.locator('#local-login-button');assert.equal(await button.isVisible(),true);assert.equal(await page.locator('#page-root').isVisible(),false);
 assert.match(await page.locator('#local-login-choice').innerText(),/本机配置的快捷销售账号.*企业真实数据/);
 assert.equal(await page.locator('#local-login-agreement').isChecked(),false);
 await page.locator('#local-login-privacy').click();
 assert.equal(await page.locator('#local-login-agreement').isChecked(),false,'Privacy information does not imply agreement');
 await button.click();await page.locator('#login-error').filter({hasText:'请先同意'}).waitFor();
 assert.equal(requests.length,0);assert.equal(await page.locator('#local-login-agreement').evaluate(e=>document.activeElement===e),true);
 await page.locator('#show-account-login').click();await page.getByPlaceholder('请输入账号名或手机号').fill('UI_FIXTURE_ONLY');await page.getByPlaceholder('请输入密码',{exact:true}).fill('SyntheticOnly_2026!');
 assert.equal(await button.isVisible(),false,'Manual and shortcut login each have one primary action');
 await page.locator('#show-account-login').click();
 await page.locator('#local-login-agreement').check();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),true);
 await button.click();await page.waitForFunction(()=>SalesRuntime.current.data.loading===true);await page.locator('[data-handler="submitLogin"][disabled]').waitFor({state:'attached'});
 await button.evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));await page.locator('[data-handler="submitLogin"]').evaluate(el=>el.click());
 assert.equal(requests.length,1,'Repeated shortcut and manual submission cannot start another login');
 await releaseLocal();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index'&&!SalesRuntime.app._capabilityFlight);
 assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.userName),'本机界面测试');assert.equal(await page.evaluate(()=>SalesRuntime.app.can('opportunity.edit')),false);assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.role),'sales');
 assert.equal(await page.locator('#login-error').textContent(),'');assert.equal(await page.locator('#local-login-choice').isVisible(),false);
}));
test('an in-flight manual password login disables the one-click path until it settles',{timeout:30000},()=>fixture({mode:'manual-pending'},async({page,requests,releaseManual})=>{
 await page.locator('#show-account-login').click();await page.getByPlaceholder('请输入账号名或手机号').fill('UI_FIXTURE_ONLY');await page.getByPlaceholder('请输入密码',{exact:true}).fill('SyntheticOnly_2026!');await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),true);assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).inputValue(),'SyntheticOnly_2026!');
 await page.locator('[data-handler="submitLogin"]').click();
 await page.locator('#local-login-button[disabled]').waitFor({state:'attached'});await page.locator('#local-login-button').evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));assert.deepEqual(requests.map(request=>request.kind),['manual']);
 await releaseManual();await page.locator('#login-error').filter({hasText:'手填登录失败'}).waitFor();await page.locator('#local-login-button:not([disabled])').waitFor({state:'attached'});
}));
test('one-click failures stay readable and do not create a session',{timeout:30000},()=>fixture({mode:'failure'},async({page,requests})=>{
 await page.locator('#local-login-agreement').check();await page.locator('#local-login-button').click();await page.locator('#login-error').filter({hasText:'交付账号暂不可用（本机测试）'}).waitFor();await page.waitForTimeout(3100);
 assert.equal(await page.locator('#login-error').isVisible(),true);assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);assert.equal(await page.locator('#local-login-button').isEnabled(),true);assert.equal(requests.length,1);
}));
test('a required password change stays on the original form and cannot be bypassed by the shortcut',{timeout:30000},()=>fixture({mode:'change'},async({page,requests})=>{
 await page.locator('#local-login-agreement').check();await page.locator('#local-login-button').click();await page.getByPlaceholder('请输入12–128位新密码').waitFor();
 assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/login/index');assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.mustChangePassword),true);assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.role),'sales');
 assert.equal(await page.locator('#local-login-choice').isVisible(),false);assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).inputValue(),'');assert.match(await page.locator('#login-error').innerText(),/首次修改密码/);assert.equal(requests.length,1);
}));
test('missing configuration, unsupported roles, and preview never show a shortcut',{timeout:30000},async()=>{
 for(const options of [{metadata:null},{metadata:{available:false,role:'sales'}},{metadata:{available:true,role:'manager'}},{preview:true}])await fixture(options,async({page,requests})=>{assert.equal(await page.locator('#local-login-choice').isVisible(),false);assert.equal(requests.length,0);});
});
test('a delayed manual-login focus request never steals the password focus or typed input',{timeout:30000},()=>fixture({},async({page,requests})=>{
 await page.evaluate(()=>{
  const original=requestAnimationFrame;const pending=[];
  window.requestAnimationFrame=callback=>{pending.push(callback);return pending.length;};
  window.flushLoginFocus=()=>{window.requestAnimationFrame=original;const callbacks=pending.splice(0);callbacks.forEach(callback=>callback(performance.now()));};
 });
 await page.locator('#show-account-login').click();
 const passwordInput=page.getByPlaceholder('请输入密码',{exact:true});await passwordInput.focus();
 await page.evaluate(()=>window.flushLoginFocus());
 assert.equal(await passwordInput.evaluate(e=>document.activeElement===e),true,'Scheduled account focus must respect a newer password focus');
 await page.keyboard.type('SYNTHETIC_FOCUS_ONLY');
 assert.equal(await passwordInput.inputValue(),'SYNTHETIC_FOCUS_ONLY');
 assert.equal(await page.getByPlaceholder('请输入账号名或手机号').inputValue(),'');assert.equal(requests.length,0);
}));
