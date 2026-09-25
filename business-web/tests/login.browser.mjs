/** Live-mode login UI/transport checks. Every API request is intercepted in the browser. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const APP_URL=process.env.APP_URL||'http://127.0.0.1:5186';
const base=new URL(APP_URL);
if(!['localhost','127.0.0.1','[::1]'].includes(base.hostname)) throw new Error('Login diagnostics only run against a local frontend.');
const account='fixture_browser_login', password='SyntheticOnly_2026!';
const actor={workspace_id:'00000000-0000-4000-8000-000000000001',user_id:'00000000-0000-4000-8000-000000000002',account_code:account.toUpperCase(),display_name:'隔离登录测试',role:'sales',role_name:'一线销售',scope_name:'仅本人',team_names:['本机测试'],team_ids:[],capabilities:{'customer.read':true,'opportunity.read':true,'visit.create':true,'task.read':true},permission_version:'fixture'};
const auth={access_token:'browser-fixture-access',refresh_token:'browser-fixture-refresh',token_type:'bearer',auth_method:'password',expires_at:new Date(Date.now()+3600000).toISOString(),must_change_password:false,actor};
async function browserFixture(width,run){
  const browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width,height:width===390?844:1000},serviceWorkers:'block'});
  let mode='401',fixtureActor=actor;const requests=[],unexpected=[],errors=[];
  await context.route('**/*',route=>{
    const request=route.request(),target=new URL(request.url());
    if(target.origin!==base.origin){unexpected.push(target.origin);return route.abort();}
    if(target.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,label:'Isolated browser fixture'}});
    if(target.pathname==='/local-login'){unexpected.push(target.pathname);return route.abort();}
    if(!target.pathname.startsWith('/api/'))return route.continue();
    if(target.pathname==='/api/v1/auth/password/login'){
      requests.push({method:request.method(),body:request.postDataJSON()});
      if(mode==='network')return route.abort('failed');
      if(mode==='timeout')return; // Keep this intercepted request pending until the browser aborts its timeout.
      if(mode==='delayed401')return new Promise(resolve=>setTimeout(resolve,600)).then(()=>route.fulfill({status:401,json:{detail:'账号或密码错误（本机测试）'}}));
      if(mode==='401')return route.fulfill({status:401,json:{detail:'账号或密码错误（本机测试）'}});
      if(mode==='422')return route.fulfill({status:422,json:{detail:[{loc:['body','account_code'],msg:'Value error, 账号格式不正确（本机测试）',type:'value_error'}]}});
      return route.fulfill({json:{...auth,actor:fixtureActor}});
    }
    if(target.pathname==='/api/v1/auth/me')return route.fulfill({json:{actor:fixtureActor}});
    if(target.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
    if(target.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
    if(['/api/v1/customers','/api/v1/notifications'].includes(target.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
    unexpected.push(target.pathname);return route.fulfill({status:503,json:{detail:'Unconfigured local login fixture'}});
  });
  const page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));
  try{
    await page.goto(APP_URL+'/?mode=live#/pages/login/index');
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index');
    assert.equal(await page.evaluate(()=>SALES_MODE),'live');
    await run({page,requests,setMode:next=>{mode=next;},setActor:fields=>{fixtureActor={...actor,...fields};}});
    assert.deepEqual(unexpected,[],'No external or unconfigured API request is allowed');
    assert.deepEqual(errors,[]);
    assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  }finally{await context.close();await browser.close();}
}
for(const width of [1440,390])test(`${width}px live login keeps input, submits source fields, shows 401/422/network errors, and accepts a valid local fixture`,{timeout:45000},()=>browserFixture(width,async({page,requests,setMode})=>{
  const accountInput=page.getByPlaceholder('请输入账号名或手机号'),passwordInput=page.getByPlaceholder('请输入密码',{exact:true}),submit=page.locator('[data-handler="submitLogin"]');
  await accountInput.click();await accountInput.pressSequentially(account,{delay:12});
  await passwordInput.click();await passwordInput.pressSequentially(password,{delay:12});
  assert.equal(await page.locator('[data-handler="selectRole"]').count(),0,'The account determines its role');
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),false);
  assert.equal(await accountInput.inputValue(),account);assert.equal(await passwordInput.inputValue(),password);
  await page.locator('[data-handler="togglePassword"]').click();
  assert.equal(await passwordInput.inputValue(),password);
  await page.locator('[data-handler="openPrivacy"]').click();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),false,'Privacy link does not select consent');
  await submit.click();await page.locator('#login-error').filter({hasText:'请先同意隐私与数据使用说明'}).waitFor();
  assert.equal(requests.length,0,'Unchecked consent cannot issue an authentication request');
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  await submit.click();
  await page.locator('.wx-toast').filter({hasText:'账号或密码错误（本机测试）'}).waitFor();
  assert.deepEqual(requests[0],{method:'POST',body:{account_code:account.toUpperCase(),password,role:null}});
  assert.equal(await passwordInput.inputValue(),password);
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session),null);
  setMode('422');await submit.click();await page.locator('.wx-toast').filter({hasText:'账号格式不正确（本机测试）'}).waitFor();
  assert.equal(await accountInput.inputValue(),account);assert.equal(await passwordInput.inputValue(),password);
  setMode('network');await submit.click();await page.locator('.wx-toast').filter({hasText:/Failed to fetch|网络|连接/}).waitFor();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.loading),false);
  assert.equal(await passwordInput.inputValue(),password);
  setMode('success');await submit.click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.userName==='隔离登录测试');
  await page.waitForFunction(()=>!SalesRuntime.app._capabilityFlight);
  assert.equal(requests.length,4);assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.role),'sales');
}));
test('320px login adopts the returned supervisor identity and keeps its specific role name after refresh',{timeout:30000},()=>browserFixture(320,async({page,requests,setMode,setActor})=>{
  setActor({role:'supervisor',role_name:'产品销售主管',scope_name:'直属团队'});setMode('success');
  await page.getByPlaceholder('请输入账号名或手机号').fill(account);await page.getByPlaceholder('请输入密码',{exact:true}).fill(password);
  await page.locator('[data-handler="toggleAgreement"]').focus();await page.keyboard.press('Space');
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.agreed),true);
  await page.locator('[data-handler="submitLogin"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.role==='supervisor');
  assert.equal(requests[0].body.role,null);assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.roleName),'产品销售主管');
  await page.reload();await page.waitForFunction(()=>SalesRuntime.current?.route==='pages/index/index'&&SalesRuntime.app.globalData.session?.role==='supervisor');
  assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.roleName),'产品销售主管');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
}));
test('live login transport surfaces its 15-second timeout and releases the submit state',{timeout:35000},()=>browserFixture(1440,async({page,requests,setMode})=>{
  await page.getByPlaceholder('请输入账号名或手机号').fill(account);
  await page.getByPlaceholder('请输入密码',{exact:true}).fill(password);
  await page.locator('[data-handler="toggleAgreement"] .checkbox').click();
  setMode('timeout');const started=Date.now();await page.locator('[data-handler="submitLogin"]').click();
  await page.locator('.wx-toast').filter({hasText:'请求超时，请稍后重试'}).waitFor({timeout:20000});
  assert.ok(Date.now()-started>=14000,'The original source timeout is exercised');
  assert.equal(requests.length,1);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.loading),false);
  assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/login/index');
  assert.equal(await page.getByPlaceholder('请输入密码',{exact:true}).inputValue(),password);
}));
for(const width of [1440,390])test(`${width}px login errors remain readable after the toast and clear on input, resubmit, and success`,{timeout:30000},()=>browserFixture(width,async({page,setMode})=>{
  const accountInput=page.getByPlaceholder('请输入账号名或手机号'),passwordInput=page.getByPlaceholder('请输入密码',{exact:true}),submit=page.locator('[data-handler="submitLogin"]');
  await accountInput.fill(account);await passwordInput.fill(password);await page.locator('[data-handler="toggleAgreement"] .checkbox').click();await submit.click();
  await page.locator('.wx-toast').filter({hasText:'账号或密码错误（本机测试）'}).waitFor();
  await page.waitForTimeout(3100);
  const message=page.locator('#login-error');
  assert.equal(await message.isVisible(),true,'A login failure must remain readable after the transient toast disappears');
  assert.equal(await message.textContent(),'账号或密码错误（本机测试）');
  assert.equal(await message.getAttribute('role'),'alert');
  await accountInput.fill(account+'A');
  assert.equal(await message.isVisible(),false);assert.equal(await message.textContent(),'');
  await page.waitForFunction(()=>SalesRuntime.current.data.password===''&&document.querySelector('input[placeholder="请输入密码"]').value==='');
  assert.equal(await passwordInput.inputValue(),'','Changing to an unsaved account clears the previous account password');
  await passwordInput.fill(password);
  setMode('422');await submit.click();
  await message.filter({hasText:'账号格式不正确（本机测试）'}).waitFor();
  setMode('delayed401');await submit.click();
  assert.equal(await message.isVisible(),false,'Starting a new attempt clears the previous message immediately');
  await message.filter({hasText:'账号或密码错误（本机测试）'}).waitFor();
  setMode('success');await submit.click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  assert.equal(await message.textContent(),'');assert.equal(await message.isVisible(),false);
}));
