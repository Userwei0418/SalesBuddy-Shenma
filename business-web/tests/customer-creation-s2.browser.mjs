/** S2 browser interactions against an in-memory synthetic API; all non-local traffic blocked. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const options=JSON.parse(await readFile(new URL('../../frontend/tests/helpers/business-options.json',import.meta.url),'utf8'));
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server.close(r));});
const permissions=Object.fromEntries(['access.mini_program','overview.read','customer.create_request','customer.claim','directory.read'].map(k=>[k,true]));
const actor={workspace_id:'s2-test-ws',user_id:'s2-sales',account_code:'s2sales',display_name:'S2合成销售',role:'sales',team_ids:['s2-team'],team_names:['测试团队'],capabilities:{},permissions,permission_grants:[],permission_version:'s2'};
for(const [role,width] of [['sales',1440],['sales',390],['operations',1440]])test(`S2 ${role} browser flow and recovery at ${width}px`,{timeout:60000},async()=>{
 const activeActor=role==='sales'?actor:{...actor,role:'operations',account_code:'s2ops',display_name:'S2合成运营',permissions:Object.fromEntries(['access.mini_program','overview.read','customer.create','directory.read'].map(k=>[k,true]))};
 const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'}),page=await context.newPage();
 const calls=[],errors=[],unexpected=[],records=[];let failNext=true;
 page.setDefaultTimeout(10000);page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());
  if(u.origin!==base){unexpected.push(u.origin);return route.abort();}
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  const call={path:u.pathname,method:req.method(),body:req.postDataJSON(),key:req.headers()['idempotency-key'],query:u.search};calls.push(call);
  if(u.pathname==='/api/v1/auth/password/login')return route.fulfill({json:{access_token:'synthetic-s2',refresh_token:'synthetic-s2-refresh',actor:activeActor,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:activeActor});
  if(u.pathname==='/api/v1/metadata/business-options')return route.fulfill({json:options});
  if(u.pathname==='/api/v1/directory/teams')return route.fulfill({json:{teams:[{id:'s2-team',name:'测试团队'}]}});
  if(u.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(u.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  if(['/api/v1/notifications','/api/v1/tasks','/api/v1/customers','/api/v1/directory/colleagues'].includes(u.pathname)&&req.method()==='GET')return route.fulfill({json:{items:[],total:0,has_more:false}});
  if(u.pathname==='/api/v1/customers'&&req.method()==='POST')return route.fulfill({status:201,json:{id:'customer-direct'}});
  if(u.pathname==='/api/v1/customers/creation-requests'){
   if(req.method()==='GET')return route.fulfill({json:{items:records,total:records.length,limit:20,offset:0,has_more:false,next_offset:null}});
   if(failNext){failNext=false;return route.fulfill({status:503,json:{detail:'合成网络故障，请重试'}});}
   const row={id:'request-s2',status:'pending',payload:req.postDataJSON(),requested_at:'2026-10-15T02:00:00Z',decision_reason:''};records.unshift(row);return route.fulfill({status:201,json:row});
  }
  if(u.pathname==='/api/v1/customers/creation-requests/request-s2/withdraw'){
   records[0].status='cancelled';return route.fulfill({json:records[0]});
  }
  unexpected.push(u.pathname);return route.fulfill({status:503,json:{detail:'Unexpected synthetic request'}});
 });
 try{
  await page.goto(base+'/?mode=live');await page.getByPlaceholder('请输入账号名或手机号').fill('s2sales');await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');
  await page.waitForFunction(()=>SalesRuntime.current.data.password==='synthetic-only');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await page.getByRole('checkbox',{name:'我已阅读并同意',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.agreed===true);
  await page.locator('[data-handler="submitLogin"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');
  await page.evaluate(()=>SalesRuntime.userRoute('/pages/customer-create/index'));
  if(role==='sales')await page.getByText('我的申请',{exact:true}).waitFor();
  else assert.equal(await page.getByText('我的申请',{exact:true}).count(),0);
  await page.locator('#customer-create-company_reference').waitFor();
  assert.equal(await page.locator('.voice-card').count(),0);
  for(const [key,value] of Object.entries({customer_name:'联调测试合成客户',company_reference:'S2-BROWSER-001',contact_name:'合成联系人',contact_title:'采购经理'}))await page.locator(`#customer-create-${key}`).fill(value);
  for(const [key,value] of Object.entries({level_code:'Tier-2',lead_source:'销售线索',target_team:'测试团队',contact_role:'决策者'})){
   await page.locator(`#customer-create-${key}`).click();
   await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:value}).first().click();
  }
  await page.keyboard.press('Escape');
  if(role==='operations'){
   await page.getByRole('button',{name:'创建客户',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();
   await page.getByText('客户创建成功',{exact:true}).waitFor();
   const direct=calls.filter(c=>c.path==='/api/v1/customers'&&c.method==='POST');assert.equal(direct.length,1);assert.equal(direct[0].body.company_reference,'S2-BROWSER-001');assert(direct[0].key);
   assert.equal((await page.evaluate(()=>SalesRuntime.wx.getStorageSync('lastCreatedCustomer'))).id,'customer-direct');
   assert(!calls.some(c=>c.path.startsWith('/api/v1/customers/creation-requests')));assert(calls.some(c=>c.query.includes('purpose=assignment')));
   assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);return;
  }
  await page.getByRole('button',{name:'提交建档申请',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();
  await page.waitForFunction(()=>SalesRuntime.current.data.submitting===false);
  assert.equal(await page.locator('#customer-create-company_reference').inputValue(),'S2-BROWSER-001');
  assert.equal(records.length,0);assert(await page.evaluate(()=>!!SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey)));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await page.locator('.ds-cc-progress-head').click();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
  await mkdir('/tmp/shenma-s2-browser',{recursive:true});await page.screenshot({path:`/tmp/shenma-s2-browser/form-${width}.png`,fullPage:true});
  await page.getByRole('button',{name:'提交建档申请',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();
  await page.getByText('待审批',{exact:true}).waitFor();assert.equal(records.length,1);assert.equal(records[0].payload.company_reference,'S2-BROWSER-001');assert.equal(records[0].payload.target_team_id,'s2-team');
  assert.equal(await page.evaluate(()=>SalesRuntime.wx.getStorageSync('lastCreatedCustomer')||null),null);
  assert.equal(await page.evaluate(()=>SalesRuntime.wx.getStorageSync(SalesRuntime.current.draftKey)||null),null);
  await page.getByRole('button',{name:'撤回申请',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();await page.getByText('已撤回',{exact:true}).waitFor();
  records.push({id:'rejected-s2',status:'rejected',payload:{name:'驳回演示客户',company_reference:'S2-REJECTED'},decision_reason:'公司编号需补充',requested_at:'2026-10-15T02:00:00Z'});
  records.push({id:'approved-s2',status:'approved',payload:{name:'已通过合成客户',company_reference:'S2-APPROVED'},created_customer_id:'customer-s2-approved',requested_at:'2026-10-15T02:00:00Z'});
  await page.locator('.ds-cc-requests-heading').getByRole('button',{name:/刷\s*新/}).click();await page.getByText('驳回原因：公司编号需补充',{exact:true}).waitFor();
  await page.getByText('客户已建档。客户归属需另行申请认领，审批通过后生效。',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'前往客户认领',exact:true}).count(),1);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.screenshot({path:`/tmp/shenma-s2-browser/requests-${width}.png`,fullPage:true});
  const posts=calls.filter(c=>c.path==='/api/v1/customers/creation-requests'&&c.method==='POST');assert.equal(posts.length,2);assert(posts[0].key);assert.equal(posts[0].key,posts[1].key);
  assert(calls.some(c=>c.path==='/api/v1/directory/teams'&&c.query.includes('purpose=creation_request')));
  assert(!calls.some(c=>c.path==='/api/v1/customers'&&c.method==='POST'));assert(calls.find(c=>c.path.endsWith('/withdraw')).key);
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
 }catch(e){await mkdir('/tmp/shenma-s2-browser',{recursive:true});await page.screenshot({path:`/tmp/shenma-s2-browser/failure-${width}.png`,fullPage:true});await writeFile(`/tmp/shenma-s2-browser/failure-${width}.json`,JSON.stringify({body:await page.locator('body').innerText(),errors,unexpected,calls},null,2));throw e;}
 finally{await context.close();}
});
