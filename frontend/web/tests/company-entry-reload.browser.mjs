// Actual built browser runtime, normal login and company-entry validation.
// All API responses are synthetic and no upstream is configured.
import {before,after,test} from 'node:test';
import assert from 'node:assert/strict';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let browser,server,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>server?.close(r));});
const actor={workspace_id:'synthetic-workspace',user_id:'synthetic-user',account_code:'SYNTHETIC',display_name:'合成销售',role:'sales',company_code:'company-a',company_name:'合成公司甲',capabilities:{'customer.read':true},team_ids:[],team_names:[]};
const config={sections:[],group_by:'topic',style:{focus:[]},reference_texts:[],default_period:'week',default_sources:['visit']};
const options={can_create:false,generation_available:false,default_scheme:config,periods:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self'],members:[],teams:[],sources:[{id:'visit',label:'拜访跟进',available:true}]};
async function fixture(run, {general = false, remembered = false} = {}) {
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),calls=[],errors=[];
 let rejectLookup=false, rejectLogin=false;page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 if(remembered)await context.addInitScript(base=>{
  const baseUrl='/api/v1';
  localStorage.setItem('sales-web:live:v1:account-history:'+encodeURIComponent(base+baseUrl),JSON.stringify({version:3,entries:[
   {baseUrl,workspace:'other-company',account:'OTHER',loginType:'account'},
   {baseUrl,workspace:'old-company',account:'SYNTHETIC',loginType:'account'}
  ]}));
 },base);
 await context.route('**/*',route=>{const request=route.request(),u=new URL(request.url());if(u.origin!==base)return route.abort();
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  const body=request.method()==='POST'?request.postDataJSON():null;
  calls.push({path:u.pathname,method:request.method(),...(body?{workspace:body.workspace,account:body.account_code,loginType:body.login_type}:{})});
  if(u.pathname==='/api/v1/auth/company-entry')return rejectLookup?route.fulfill({status:503,json:{detail:'Synthetic lookup unavailable'}}):route.fulfill({json:{company_code:u.searchParams.get('company_code'),company_name:'合成公司'}});
  if(u.pathname==='/api/v1/auth/password/login')return rejectLogin?route.fulfill({status:401,json:{detail:'账号或密码错误'}}):route.fulfill({json:{access_token:'synthetic-only',refresh_token:'synthetic-refresh',actor,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:{actor}});
  if(u.pathname==='/api/v1/work-reports/options')return route.fulfill({json:options});
  if(u.pathname==='/api/v1/work-reports/documents')return route.fulfill({json:{items:[],total:0,limit:40,offset:0,has_more:false,next_offset:null}});
  return route.fulfill({status:503,json:{detail:'Synthetic unrelated page data unavailable'}});
 });
 try {
  if(general){
   await page.goto(base+'/?mode=live#/pages/login/index');
   await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index' && document.querySelector('#login-connection-status')?.dataset.state==='ready');
   await page.locator('.ds-login').waitFor();
   await run({page,calls,setRejectLogin:value=>{rejectLogin=value;}});assert.deepEqual(errors,[]);return;
  }
  await page.goto(base+'/?mode=live&company=company-a#/pages/login/index');
  await page.waitForFunction(()=>window.SalesRuntime?.current?.data.companyState==='ready' && document.querySelector('#login-connection-status')?.dataset.state==='ready');
  await page.getByPlaceholder('请输入账号或已绑定的手机号').fill('SYNTHETIC');
  await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.locator('[data-handler="toggleAgreement"] input[type=checkbox]').click();
  await page.waitForFunction(()=>SalesRuntime.current.data.agreed===true);
  await page.locator('[data-handler="submitLogin"]').click();
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  assert.equal(calls.find(c=>c.path==='/api/v1/auth/password/login').workspace,'company-a');
  await run({page,calls,rejectLookup:()=>{rejectLookup=true;}});assert.deepEqual(errors,[]);
 } finally {await context.close();}
}
const report='/pages/work-report/index?period=week&report_tab=documents';
test('same-company report reload validates the entry and retains its report tab',()=>fixture(async({page,calls})=>{
 await page.goto(base+'/?mode=live&company=company-a#'+report);
 await page.getByText('还没有保存的报告',{exact:true}).waitFor();
 const before=calls.filter(c=>c.path==='/api/v1/auth/company-entry').length;
 await page.reload();await page.getByText('还没有保存的报告',{exact:true}).waitFor();
 assert.equal(new URL(page.url()).hash,'#'+report);
 assert.equal(calls.filter(c=>c.path==='/api/v1/auth/company-entry').length,before+1);
 assert.equal(calls.filter(c=>c.path==='/api/v1/auth/password/login').length,1);
}));

test('remembered Web login resolves company without a code, preserves failed input and forgets only the selected record',()=>fixture(async({page,calls,setRejectLogin})=>{
 const account=page.getByPlaceholder('请输入账号，支持数字账号');
 const password=page.getByPlaceholder('请输入密码',{exact:true});
 await account.waitFor();assert.equal(await account.inputValue(),'SYNTHETIC');
 assert.equal(await page.locator('#login-company, .ds-company-entry').count(),0);
 assert.equal(await page.getByText('使用公司入口',{exact:true}).count(),0);
 await password.fill('synthetic-only');
 if(process.env.LOGIN_SCREENSHOT)await page.screenshot({path:process.env.LOGIN_SCREENSHOT,fullPage:true});
 await page.locator('[data-handler="submitLogin"]').click();
 assert.equal(calls.filter(c=>c.path==='/api/v1/auth/password/login').length,0,'privacy agreement remains required');
 await page.locator('[data-handler="toggleAgreement"] input[type=checkbox]').click();
 setRejectLogin(true);await password.press('Enter');
 await page.waitForFunction(()=>SalesRuntime.current.data.loginHelp && !SalesRuntime.current.data.loading);
 assert.equal(await password.inputValue(),'synthetic-only');
 const attempt=calls.find(c=>c.path==='/api/v1/auth/password/login');
 assert.equal(attempt.workspace,undefined);assert.equal(attempt.account,'SYNTHETIC');assert.equal(attempt.loginType,'account');
 assert.equal(calls.filter(c=>c.path==='/api/v1/auth/company-entry').length,0);
 await page.getByRole('checkbox',{name:'记住账号 不保存密码'}).click();
 await page.waitForFunction(()=>!SalesRuntime.current.data.rememberAccount && !document.querySelector('.ds-login-checks input[type=checkbox]').checked);
 const history=await page.evaluate(()=>SalesRuntime.wx.getStorageSync('salesAccountHistoryV3'));
 assert.deepEqual(history.entries.map(row=>row.account),['OTHER']);
 setRejectLogin(false);await page.locator('[data-handler="submitLogin"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
 assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.companyCode),'company-a');
}, {general:true,remembered:true}));

test('fresh Web phone login uses automatic company resolution',()=>fixture(async({page,calls})=>{
 await page.getByRole('button',{name:'更多登录方式',exact:true}).click();
 await page.getByRole('button',{name:'手机号登录',exact:true}).click();
 await page.getByPlaceholder('请输入已绑定的手机号').fill('13900000000');
 await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
 await page.locator('[data-handler="toggleAgreement"] input[type=checkbox]').click();
 await page.locator('[data-handler="submitLogin"]').click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
 const attempt=calls.find(c=>c.path==='/api/v1/auth/password/login');
 assert.equal(attempt.workspace,undefined);assert.equal(attempt.loginType,'phone');
 assert.equal(calls.filter(c=>c.path==='/api/v1/auth/company-entry').length,0);
}, {general:true}));
for(const [name,company,failed]of [['different company','company-b',false],['invalid company','%00',false],['failed same-company lookup','company-a',true]])test(name+' cannot enter the old report',()=>fixture(async({page,calls,rejectLookup})=>{
 if(failed)rejectLookup();const count=calls.filter(c=>c.path.startsWith('/api/v1/work-reports')).length;
 // Change the document query as well as the hash so this exercises a full boot.
 await page.goto(base+'/?mode=live&company='+company+'&reload-check=1#'+report);
 await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/login/index' && ['ready','error'].includes(SalesRuntime.current.data.companyState));
 assert.equal(calls.filter(c=>c.path.startsWith('/api/v1/work-reports')).length,count);
 assert.equal(await page.evaluate(()=>SalesRuntime.app.globalData.session.companyCode),'company-a');
 if(!failed)assert.equal(await page.evaluate(()=>sessionStorage.getItem('sales-web:pending-path:live')),null);
}));
