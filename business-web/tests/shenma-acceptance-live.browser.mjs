/**
 * Real local acceptance. No response mocking, database setup, or production access.
 * SHENMA_ACCEPTANCE_URL=http://127.0.0.1:<port>/workspace/
 * SHENMA_ACCEPTANCE_PASSWORD=<password of the eight isolated acceptance accounts>
 * Optional: SHENMA_ACCEPTANCE_EVIDENCE_DIR, PLAYWRIGHT_MODULE.
 * Run only against the dedicated acceptance database. Each run leaves its uniquely
 * named approved/unclaimed customer as evidence; personal views are deleted by UI.
 */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {captureViewConfig} from '../department-ui/opportunityViewState.mjs';
import {periodRange,statisticsRange} from '../department-ui/pc/period.js';

const input=process.env.SHENMA_ACCEPTANCE_URL,password=process.env.SHENMA_ACCEPTANCE_PASSWORD;
assert.ok(input&&password,'SHENMA_ACCEPTANCE_URL and SHENMA_ACCEPTANCE_PASSWORD are required');
const entry=new URL(input),localHosts=new Set(['localhost','127.0.0.1','[::1]']);
assert.ok(['http:','https:'].includes(entry.protocol)&&localHosts.has(entry.hostname)&&!entry.username&&!entry.password,'Acceptance URL must be a literal loopback host without credentials');
entry.searchParams.set('mode','live');entry.hash='';
const origin=entry.origin,runId=`${new Date().toISOString().replace(/\D/g,'').slice(0,14)}-${randomUUID().slice(0,8)}`;
const evidenceDir=process.env.SHENMA_ACCEPTANCE_EVIDENCE_DIR?resolve(process.env.SHENMA_ACCEPTANCE_EVIDENCE_DIR,runId):resolve(`/tmp/shenma-real-acceptance-${runId}`);
const {chromium,request}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const expectedRoles={smxs:'sales',smxs2:'sales',smzg:'supervisor',smzjl:'manager',smyy:'operations',smadmin:'administrator',smxs3:'sales',smother:'sales'};
const native=new Map(),consoleApi=new Map(),contexts=[];
const evidence={run_id:runId,base_url:origin,started_at:new Date().toISOString(),accounts:[],api:[],cases:[]};
let browser;
const ids=items=>items.map(item=>String(item.id)).sort();
const pathOf=response=>new URL(response.url()).pathname;
const encode=value=>encodeURIComponent(value);
async function readJson(response,label,expected=200){assert.equal(response.status(),expected,`${label}: HTTP ${response.status()}`);return response.json();}
async function call(client,path,{method='GET',body,expected=200,key}={}){
  assert.ok(path.startsWith('/api/v1/')&&!path.startsWith('//'),'Only same-origin business API paths allowed');
  const response=await client.context.fetch(origin+path,{method,data:body,headers:{Authorization:`Bearer ${client.token}`,...(key?{'Idempotency-Key':key}:{})},maxRedirects:0});
  evidence.api.push({account:client.account,method,path,status:response.status()});
  if(expected!==null)assert.equal(response.status(),expected,`${method} ${path}: HTTP ${response.status()}`);
  let data=null;try{data=await response.json();}catch{}
  return {status:response.status(),data};
}
async function signInApi(account,consoleMode=false){
  const context=await request.newContext({baseURL:origin,maxRedirects:0,extraHTTPHeaders:{Origin:origin}});contexts.push(context);
  const path=consoleMode?'/api/v1/console/auth/login':'/api/v1/auth/password/login';
  const response=await context.post(path,{data:{account_code:account,password},maxRedirects:0});
  const auth=await readJson(response,`${account} real ${consoleMode?'console':'business'} login`);
  assert.equal(auth.actor.role,expectedRoles[account]);assert.equal(auth.must_change_password,false,'Dedicated acceptance accounts must already have a usable test password');
  assert.ok(auth.access_token&&auth.actor.user_id&&auth.actor.workspace_id);
  const client={account,context,token:auth.access_token,actor:auth.actor};
  if(!consoleMode)evidence.accounts.push({account,role:auth.actor.role,user_id:auth.actor.user_id,workspace_id:auth.actor.workspace_id});
  return client;
}
async function allRange(account,scope='all',extra={}){
  const range=statisticsRange(periodRange('week')),items=[];let offset=0,total;
  for(let n=0;n<100;n++){
    const query=new URLSearchParams({date_basis:'interaction',date_from:range.start,date_to:range.end,scope,page_size:'100',offset:String(offset),...extra});
    const {data}=await call(native.get(account),`/api/v1/visits/range?${query}`);
    assert.ok(Array.isArray(data.items));if(total===undefined)total=data.summary.total;else assert.equal(data.summary.total,total);
    items.push(...data.items);if(!data.has_more){assert.equal(items.length,total);assert.equal(new Set(ids(items)).size,items.length);return items;}
    assert.ok(Number.isInteger(data.next_offset)&&data.next_offset>offset);offset=data.next_offset;
  }
  throw Error('Acceptance range exceeded 100 pages');
}
before(async()=>{
  await mkdir(evidenceDir,{recursive:true});
  const healthContext=await request.newContext({baseURL:origin,maxRedirects:0,extraHTTPHeaders:{Origin:origin}});contexts.push(healthContext);
  const status=await readJson(await healthContext.get('/connection-status',{maxRedirects:0}),'dedicated acceptance marker');
  assert.equal(status.acceptanceMarker,'shenma-isolated-acceptance-v1','Refuse login and mutation unless the dedicated isolated launcher identifies itself');
  assert.equal(status.reachable,true);assert.equal(status.checks?.database,true);assert.equal(status.checks?.auth_configured,true);assert.equal(status.checks?.model_gateway_configured,false,'Real model calls are disabled for this local acceptance');evidence.environment=status;
  const health=await healthContext.get('/api/v1/health/version',{maxRedirects:0});evidence.version=await readJson(health,'acceptance version');
  for(const account of Object.keys(expectedRoles))native.set(account,await signInApi(account));
  assert.equal(new Set([...native.values()].filter(v=>v.account!=='smother').map(v=>v.actor.workspace_id)).size,1,'The primary acceptance accounts must belong to the isolated acceptance company');
  assert.notEqual(native.get('smother').actor.workspace_id,native.get('smxs').actor.workspace_id,'Second company must be independently scoped');
  for(const account of ['smyy','smadmin'])consoleApi.set(account,await signInApi(account,true));
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>{
  await browser?.close();for(const context of contexts)await context.dispose();
  evidence.finished_at=new Date().toISOString();await mkdir(evidenceDir,{recursive:true});await writeFile(resolve(evidenceDir,'evidence.json'),JSON.stringify(evidence,null,2));
  console.log(`REAL_ACCEPTANCE_EVIDENCE ${evidenceDir}`);
});
async function observedPage(width,record){
  const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),page=await context.newPage();
  page.setDefaultTimeout(15000);page.on('pageerror',error=>record.errors.push(error.message));
  await context.route('**/*',route=>{const url=new URL(route.request().url());if(!localHosts.has(url.hostname)){record.unexpected.push({origin:url.origin,path:url.pathname});return route.abort();}return route.continue();});
  page.on('response',response=>{const url=new URL(response.url());if(url.pathname.startsWith('/api/'))record.network.push({method:response.request().method(),path:url.pathname,query:url.search,status:response.status()});});
  return {context,page};
}
async function login(page,account,{navigate=true}={}){
  if(navigate)await page.goto(entry.href);
  await page.getByPlaceholder('请输入账号名或手机号').fill(account);await page.getByPlaceholder('请输入密码',{exact:true}).fill(password);
  await page.waitForFunction(()=>Boolean(window.SalesRuntime?.current?.data?.password));
  const consent=page.getByRole('checkbox',{name:'我已阅读并同意',exact:true});if(!await consent.isChecked())await consent.click();
  await page.waitForFunction(()=>SalesRuntime.current.data.agreed===true);
  const pending=page.waitForResponse(r=>pathOf(r)==='/api/v1/auth/password/login'&&r.request().method()==='POST');
  await page.locator('[data-handler="submitLogin"]').click();const auth=await readJson(await pending,`${account} browser login`);
  assert.equal(auth.actor.user_id,native.get(account).actor.user_id);await page.waitForFunction(()=>SalesRuntime.current.route!=='pages/login/index');
}
async function go(page,path){await page.evaluate(path=>SalesRuntime.userRoute(path),path);await page.waitForFunction(route=>SalesRuntime.current.route===route,path.slice(1).split('?')[0]);}
async function logout(page){await go(page,'/pages/profile/index');await page.getByRole('button',{name:'退出登录 / 切换账号',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();await page.getByPlaceholder('请输入账号名或手机号').waitFor();}
async function activeDropdown(page,input){
  await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});await input.click();
  const control=await input.getAttribute('aria-controls');assert.ok(control,'Select must identify its own listbox');
  const dropdown=page.locator('.ant-select-dropdown').filter({has:page.locator(`[id="${control}"]`)});await dropdown.waitFor({state:'visible'});return dropdown;
}
async function select(page,label,text){const dropdown=await activeDropdown(page,page.getByRole('combobox',{name:label,exact:true}));await dropdown.locator('.ant-select-item-option').filter({hasText:text}).first().click();await page.keyboard.press('Escape');await dropdown.waitFor({state:'hidden'});}
async function fieldSelectFirst(page,key){const dropdown=await activeDropdown(page,page.locator(`#customer-create-${key}`));await dropdown.locator('.ant-select-item-option:not(.ant-select-item-option-disabled)').first().click();await page.keyboard.press('Escape');await dropdown.waitFor({state:'hidden'});}
async function businessResponse(page,path,method,action,status=200){const pending=page.waitForResponse(r=>pathOf(r)===path&&r.request().method()===method);await action();return readJson(await pending,`${method} ${path}`,status);}
async function popupGeometry(page,selector,control){
  await page.locator(selector).waitFor({state:'visible'});
  if(control)await page.locator(control).waitFor({state:'visible'});
  await page.waitForFunction(({selector,control})=>[document.querySelector(selector),control?document.querySelector(control):null].filter(Boolean).every(element=>{const r=element.getBoundingClientRect();return r.height>0&&r.height<innerHeight&&r.top>=-1&&r.bottom<=innerHeight+1&&r.left>=-1&&r.right<=innerWidth+1;}),{selector,control});
  return page.locator(selector).evaluate(element=>({rect:element.getBoundingClientRect().toJSON(),viewport:{width:innerWidth,height:innerHeight},minHeight:getComputedStyle(element).minHeight}));
}
async function screenshot(page,name){await page.evaluate(()=>{window.scrollTo(0,0);document.querySelectorAll('#page-root,[data-page-scroll],#page').forEach(el=>el.scrollTo(0,0));});await page.screenshot({path:resolve(evidenceDir,name),fullPage:true,animations:'disabled'});}
async function consoleLogin(page,account){await page.goto(origin+'/admin/#creation_requests');await page.locator('#login-form [name="account"]').fill(account);await page.locator('#login-password').fill(password);const pending=page.waitForResponse(r=>pathOf(r)==='/api/v1/console/auth/login'&&r.request().method()==='POST');await page.locator('#login-form button[type="submit"]').click();const auth=await readJson(await pending,'console browser login');assert.equal(auth.actor.user_id,native.get(account).actor.user_id);await page.locator('[data-nav="creation_requests"]').waitFor();await page.locator('[data-nav="creation_requests"]').click();await page.getByRole('heading',{name:'客户建档审批',exact:true}).waitFor();}

test('REAL local cross-team and cross-company API isolation',{timeout:60000},async()=>{
  const record={name:'Cross-team and cross-company API isolation',status:'running',checks:[]};evidence.cases.push(record);
  try{
    const rows={};for(const account of ['smxs','smxs2','smxs3','smother','smzg','smzjl'])rows[account]=await allRange(account,'all');
    for(const account of ['smxs','smxs2','smxs3','smother']){assert.ok(rows[account].length,`${account} requires seeded current-week records`);assert.ok(rows[account].every(row=>row.recorder_id===native.get(account).actor.user_id),`${account} can only list its own follow-ups`);}
    const north=ids([...rows.smxs,...rows.smxs2]),east=ids(rows.smxs3),other=ids(rows.smother);
    assert.ok(north.every(id=>ids(rows.smzg).includes(id)));assert.ok(east.every(id=>!ids(rows.smzg).includes(id)),'North supervisor cannot see East team');
    assert.ok([...north,...east].every(id=>ids(rows.smzjl).includes(id)),'Company manager sees both company teams');assert.ok(other.every(id=>!ids(rows.smzjl).includes(id)),'Company manager cannot see a second company');
    for(const account of ['smxs3','smother'])assert.ok(north.every(id=>!ids(rows[account]).includes(id)),`${account} cannot see North`);
    for(const [account,foreignAccount] of [['smxs3','smxs'],['smzg','smxs3'],['smzjl','smother'],['smother','smxs']]){
      const foreign=rows[foreignAccount].find(row=>row.customer_id);assert.ok(foreign,`${foreignAccount} needs a linked customer fixture`);
      for(const path of [`/api/v1/visits/${foreign.id}`,`/api/v1/customers/${foreign.customer_id}`]){
        const denied=await call(native.get(account),path,{expected:null});assert.ok([403,404].includes(denied.status),`${account} must be denied ${path}`);
        record.checks.push({account,foreign_account:foreignAccount,path,status:denied.status});
      }
    }
    record.visible_record_ids=Object.fromEntries(Object.entries(rows).map(([account,items])=>[account,ids(items)]));record.status='passed';
  }catch(error){record.status='failed';record.failure=error.message;throw error;}
});

for(const [width,approver] of [[1440,'smyy'],[390,'smadmin']])test(`REAL local S2/S3/follow-ups and account isolation ${width}px`,{timeout:240000},async()=>{
  const record={width,approver,status:'running',errors:[],unexpected:[],network:[],checks:[]};evidence.cases.push(record);
  const sales=await observedPage(width,record),management=await observedPage(1440,record),page=sales.page;
  const marker=`本机联调${width}-${runId}`,reference=`LOCAL-E2E-${width}-${runId}`,viewName=`E2E ${width} ${runId.slice(-8)}`;
  try{
    await login(page,'smxs');await go(page,'/pages/customer-create/index');await page.getByText('我的申请',{exact:true}).waitFor();
    for(const [key,value] of Object.entries({customer_name:marker,company_reference:reference,contact_name:'本机验收联系人',contact_title:'采购经理'}))await page.locator(`#customer-create-${key}`).fill(value);
    for(const key of ['level_code','lead_source','target_team','contact_role'])await fieldSelectFirst(page,key);
    const submitted=await businessResponse(page,'/api/v1/customers/creation-requests','POST',async()=>{await page.getByRole('button',{name:'提交建档申请',exact:true}).click();await page.locator('.wx-modal-buttons button').last().click();},201);
    assert.equal(submitted.status,'pending');assert.equal(submitted.created_customer_id,null);assert.equal(submitted.payload.company_reference,reference);assert.equal(submitted.applicant_user_ref_id,native.get('smxs').actor.user_id);
    record.request_id=submitted.id;record.customer_reference=reference;
    const pre=await call(consoleApi.get(approver),`/api/v1/console/customers?q=${encode(reference)}`);assert.equal(pre.data.items.length,0,'A pending request must not create a customer');
    const other=await call(native.get('smxs2'),`/api/v1/customers/creation-requests?q=${encode(reference)}`);assert.equal(other.data.items.length,0,'Another sales account cannot read the request');
    const direct=await call(native.get('smxs'),'/api/v1/customers',{method:'POST',body:submitted.payload,key:randomUUID(),expected:null});assert.equal(direct.status,403,'Sales cannot bypass the application flow');
    await screenshot(page,`s2-pending-${width}.png`);
    await consoleLogin(management.page,approver);
    const search=management.page.getByPlaceholder('搜索申请人 / 客户 / 公司编号');await search.fill(reference);await search.press('Enter');
    const review=management.page.locator('tr').filter({hasText:reference});await review.waitFor();await review.getByRole('button',{name:'审核',exact:true}).click();
    await management.page.locator('[name="reason"]').fill('本机真实端到端验收，核对公司编号后批准');
    const approved=await businessResponse(management.page,`/api/v1/console/customer-creation-requests/${submitted.id}/decision`,'POST',()=>management.page.getByRole('button',{name:'确认处理',exact:true}).click());
    assert.equal(approved.status,'approved');assert.ok(approved.created_customer_id);record.customer_id=approved.created_customer_id;
    const customers=(await call(consoleApi.get(approver),`/api/v1/console/customers?q=${encode(reference)}&state=unclaimed`)).data.items;
    assert.equal(customers.length,1);assert.equal(customers[0].id,approved.created_customer_id);assert.equal(customers[0].ownership_state,'unclaimed');assert.equal(customers[0].owner_user_ref_id,null);
    const mine=(await call(native.get('smxs'),`/api/v1/customers/creation-requests?q=${encode(reference)}`)).data.items;assert.equal(mine.length,1);assert.equal(mine[0].status,'approved');assert.equal(mine[0].created_customer_id,approved.created_customer_id);
    await page.reload();const refreshedRequests=await businessResponse(page,'/api/v1/customers/creation-requests','GET',()=>page.getByRole('button',{name:'我的申请',exact:true}).click());
    assert.ok(refreshedRequests.items.some(row=>row.id===submitted.id&&row.status==='approved'&&row.created_customer_id===approved.created_customer_id));
    const approvedCard=page.locator('.ds-cc-request').filter({hasText:reference});await approvedCard.getByText('客户已建档。客户归属需另行申请认领，审批通过后生效。',{exact:true}).waitFor();await approvedCard.getByRole('button',{name:'前往客户认领',exact:true}).waitFor();
    await screenshot(page,`s2-approved-${width}.png`);record.checks.push({name:'S2 approval/readback',request_id:submitted.id,customer_id:approved.created_customer_id,ownership_state:customers[0].ownership_state,owner_user_ref_id:null});

    await go(page,'/pages/workbench/index');await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor();await page.waitForFunction(()=>!SalesRuntime.current.data.opportunityListLoading);
    await page.getByRole('button',{name:'视图设置',exact:true}).click();record.checks.push({name:'View settings popup fits viewport',...await popupGeometry(page,'.sm-view-popover','[aria-label="商机分组"]')});await select(page,'商机分组','不分组');await page.getByRole('button',{name:'视图设置',exact:true}).click();
    const moreFilters=page.getByRole('button',{name:/更多筛选/});if(await moreFilters.count()){await moreFilters.click();record.checks.push({name:'More filters popup fits viewport',...await popupGeometry(page,'.pc-filterbar-popover')});await page.locator('.pc-filterbar-popover').getByRole('button',{name:/收\s*起/}).click();await page.locator('.pc-filterbar-popover').waitFor({state:'hidden'});}
    const query=`E2E-${width}-${runId.slice(-8)}`,searchOpp=page.getByPlaceholder('搜索客户、商机或产品线');await searchOpp.fill(query);await searchOpp.press('Enter');await page.waitForFunction(q=>SalesRuntime.current.data.opportunityQuery===q,query);
    const expected=captureViewConfig(await page.evaluate(()=>SalesRuntime.current.data));
    await page.getByRole('button',{name:'保存为新视图',exact:true}).click();await page.getByRole('textbox',{name:'视图名称',exact:true}).fill(viewName);
    const created=await businessResponse(page,'/api/v1/opportunity-views','POST',()=>page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click(),201);await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});
    assert.deepEqual(created.config,expected);record.view_id=created.id;
    assert.deepEqual((await call(native.get('smxs'),`/api/v1/opportunity-views/${created.id}`)).data.config,expected);
    assert.equal((await call(native.get('smxs2'),`/api/v1/opportunity-views/${created.id}`,{expected:null})).status,404,'Private view detail must not leak to another account');
    await page.reload();await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor();await select(page,'个人视图',viewName);await page.waitForFunction(q=>SalesRuntime.current.data.opportunityQuery===q,query);assert.deepEqual(captureViewConfig(await page.evaluate(()=>SalesRuntime.current.data)),expected);
    const renamed=viewName+' 改名';await page.getByRole('button',{name:'重命名',exact:true}).click();await page.getByRole('textbox',{name:'视图名称',exact:true}).fill(renamed);
    const renamedRow=await businessResponse(page,`/api/v1/opportunity-views/${created.id}`,'PUT',()=>page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click());await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});assert.equal(renamedRow.name,renamed);assert.deepEqual(renamedRow.config,expected);
    await searchOpp.fill(query+'-updated');await searchOpp.press('Enter');await page.waitForFunction(q=>SalesRuntime.current.data.opportunityQuery===q,query+'-updated');const updatedConfig=captureViewConfig(await page.evaluate(()=>SalesRuntime.current.data));
    await page.getByRole('button',{name:'更新当前视图',exact:true}).click();const updated=await businessResponse(page,`/api/v1/opportunity-views/${created.id}`,'PUT',()=>page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click());await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});assert.deepEqual(updated.config,updatedConfig);assert.ok(updated.version_no>created.version_no);
    await logout(page);await login(page,'smxs2',{navigate:false});await go(page,'/pages/workbench/index');await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor();await page.getByRole('combobox',{name:'个人视图',exact:true}).click();assert.equal(await page.locator('.ant-select-dropdown:visible').getByText(renamed,{exact:true}).count(),0);await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityQuery),'');
    await logout(page);await login(page,'smxs',{navigate:false});await go(page,'/pages/workbench/index');await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor();await select(page,'个人视图',renamed);await page.waitForFunction(q=>SalesRuntime.current.data.opportunityQuery===q,query+'-updated');await screenshot(page,`s3-restored-${width}.png`);
    await page.getByRole('button',{name:'删除视图',exact:true}).click();const deleted=await businessResponse(page,`/api/v1/opportunity-views/${created.id}`,'DELETE',()=>page.getByRole('dialog').getByRole('button',{name:'确认删除',exact:true}).click());await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});assert.equal(deleted.deleted,true);assert.equal((await call(native.get('smxs'),`/api/v1/opportunity-views/${created.id}`,{expected:null})).status,404);
    record.checks.push({name:'S3 real CRUD/reload/account isolation',view_id:created.id,created_version:created.version_no,updated_version:updated.version_no,deleted:true,config:updatedConfig});

    const own=await allRange('smxs','mine'),second=await allRange('smxs2','mine'),team=await allRange('smzg','all'),company=await allRange('smzjl','all');
    assert.ok(own.length&&second.length,'Seed at least one current-week confirmed/archived follow-up for each sales account');
    const firstId=native.get('smxs').actor.user_id,secondId=native.get('smxs2').actor.user_id;
    assert.ok(own.every(row=>row.recorder_id===firstId));assert.ok(second.every(row=>row.recorder_id===secondId));assert.ok(own.every(row=>!ids(second).includes(row.id)));
    assert.ok([...own,...second].every(row=>ids(team).includes(row.id)),'Supervisor must see both seeded team members');assert.ok(ids(team).every(id=>ids(company).includes(id)),'Manager must see the team records');
    const forged=await allRange('smxs','all',{recorder_ids:secondId});assert.equal(forged.length,0,'Sales recorder filter must not broaden RLS');
    await go(page,'/pages/visit-records/index?from=workspace&period=week&scope=self');await page.getByRole('heading',{name:'跟进记录',exact:true}).waitFor();await page.getByRole('button',{name:'详情',exact:true}).first().waitFor();
    const filtered=own.find(row=>row.can_read_detail&&row.follow_up_record)||own[0],term=(filtered.customer_name||filtered.subject_name||filtered.follow_up_record).slice(0,40);
    const followupSearch=page.getByPlaceholder('搜索客户、商机、人员或完整内容');const queryResponse=page.waitForResponse(r=>pathOf(r)==='/api/v1/visits/range'&&new URL(r.url()).searchParams.get('q')===term);await followupSearch.fill(term);await followupSearch.press('Enter');const filteredPage=await readJson(await queryResponse,'real follow-up search');assert.ok(filteredPage.items.some(row=>row.id===filtered.id));assert.ok(filteredPage.items.every(row=>row.recorder_id===firstId));
    const detailResponse=page.waitForResponse(r=>/^\/api\/v1\/visits\/[0-9a-f-]+$/.test(pathOf(r))&&r.request().method()==='GET');await page.getByRole('button',{name:'详情',exact:true}).first().click();const full=await readJson(await detailResponse,'real follow-up detail');assert.ok(ids(own).includes(full.id));await page.locator('.pc-fu-detail').waitFor();const text=await page.locator('.pc-fu-detail').innerText();assert.ok(typeof full.follow_up_record==='string'&&full.follow_up_record.trim().length>0,'Seeded full detail must contain non-empty follow-up content');assert.ok(text.includes(full.follow_up_record));await screenshot(page,`followup-detail-${width}.png`);await page.locator('.ant-drawer-close').click();await page.locator('.ant-drawer-open').waitFor({state:'hidden'});
    await followupSearch.fill(`NO-MATCH-${runId}`);await followupSearch.press('Enter');await page.getByText('没有匹配的跟进记录',{exact:true}).waitFor();await page.getByRole('button',{name:'清除条件',exact:true}).click();await page.getByRole('button',{name:'详情',exact:true}).first().waitFor();
    await logout(page);await login(page,'smzg',{navigate:false});
    const uiTeamPages=[],range=statisticsRange(periodRange('week'));
    const collectTeamResponse=response=>{const url=new URL(response.url());if(url.pathname==='/api/v1/visits/range'&&url.searchParams.get('scope')==='all'&&url.searchParams.get('date_from')===range.start&&url.searchParams.get('date_to')===range.end&&!url.searchParams.get('q'))uiTeamPages.push(readJson(response,'supervisor UI team scope').then(data=>({offset:Number(url.searchParams.get('offset')),page_size:Number(url.searchParams.get('page_size')),data})));};
    page.on('response',collectTeamResponse);await go(page,'/pages/visit-records/index?from=workspace&period=week&scope=team');
    await page.locator('.pc-page-head').filter({hasText:`${team.length} 条`}).waitFor();await page.getByRole('button',{name:'详情',exact:true}).first().waitFor();page.off('response',collectTeamResponse);
    const teamPages=await Promise.all(uiTeamPages),uiRows=teamPages.flatMap(part=>part.data.items);assert.ok(teamPages.length>0);assert.deepEqual(ids(uiRows),ids(team),'Supervisor UI must load the complete authorized API set, including both North sales');
    for(const part of teamPages){assert.equal(part.data.summary.total,team.length);assert.ok(part.data.items.length<=part.page_size);assert.ok(part.data.items.every(row=>![native.get('smxs3').actor.user_id,native.get('smother').actor.user_id].includes(row.recorder_id)));}
    assert.equal(await page.getByPlaceholder('搜索客户、商机、人员或完整内容').inputValue(),'');
    await screenshot(page,`followups-supervisor-${width}.png`);record.checks.push({name:'Follow-up real scope/filter/detail',sales_ids:ids(own),second_sales_ids:ids(second),supervisor_ids:ids(team),manager_ids:ids(company),detail_id:full.id});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'No document horizontal overflow');assert.deepEqual(record.errors,[]);assert.deepEqual(record.unexpected,[]);assert.deepEqual(record.network.filter(row=>row.status>=500),[],'No server error may be hidden by a successful button state');record.status='passed';
  }catch(error){record.status='failed';record.failure=error.message;record.ui=await page.locator('body').innerText();record.overlay_geometry=await page.locator('.ant-popover,.ant-drawer,[aria-label="商机分组"]').evaluateAll(elements=>elements.map(element=>({class:element.className,aria:element.getAttribute('aria-label'),rect:element.getBoundingClientRect().toJSON(),display:getComputedStyle(element).display,visibility:getComputedStyle(element).visibility})));await screenshot(page,`failure-${width}.png`).catch(()=>{});throw error;}
  finally{await sales.context.close();await management.context.close();await writeFile(resolve(evidenceDir,`case-${width}.json`),JSON.stringify(record,null,2));}
});
