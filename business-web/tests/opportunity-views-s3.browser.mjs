/** PC personal views: real built React + Page + API adapter against synthetic isolated HTTP. */
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
import {defaultViewConfig} from '../department-ui/opportunityViewState.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const options=JSON.parse(await readFile(new URL('../../frontend/tests/helpers/business-options.json',import.meta.url),'utf8'));
let server,browser,base;
before(async()=>{server=createSalesWebServer({target:''});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server.close(r));});
const team='00000001-0000-4000-8000-000000000001',owner='00000002-0000-4000-8000-000000000001';
const permissions=Object.fromEntries(['access.mini_program','overview.read','opportunity.read'].map(k=>[k,true]));
const actor={workspace_id:'s3-ws',user_id:'s3-manager',account_code:'s3manager',display_name:'合成经理',role:'manager',team_ids:[team],team_names:['华北团队'],capabilities:{},permissions,permission_grants:[{permission_code:'opportunity.read',scope_code:'workspace',effect:'allow'}],permission_version:'s3'};
const row=(id,name,config=defaultViewConfig())=>({id,name,config,owner_user_ref_id:actor.user_id,version_no:1,can_edit:true,can_delete:true,config_valid:true,config_error:null});
const seeded={...defaultViewConfig(2030),columns:['name','owner','amount','product'],order:'close_date',group_by:'none',filters:{q:'年度项目',scope:'self',team_id:team,owner_id:owner,stages:['qualified'],grade:'B',product_line:'基础设施',year:2030,quarters:[2],close_period:'all',include_closed:false}};
async function reveal(page,label,role='combobox'){const control=page.getByRole(role,{name:label,exact:true});if(!await control.isVisible()){await page.getByRole('button',{name:['商机排序','商机分组','显示字段'].includes(label)?'视图设置':/^更多筛选/}).click();}await control.waitFor({state:'visible'});return control;}
async function select(page,label,text){await reveal(page,label);await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({hasText:text}).first().click();await page.keyboard.press('Escape');}
for(const width of [1440,390])test(`S3 personal views CRUD, recovery and account isolation at ${width}px`,{timeout:90000},async()=>{
 const context=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(12000);
 const errors=[],unexpected=[],calls=[];let who=actor,failPost=true,failPut=false,missing=false,forbidden=false,deferredDetail=null,hideFacets=false;
 let views=[row('seed','华北重点',seeded),{...row('invalid','旧版视图'),config_valid:false,config_error:'视图配置已失效，已恢复默认列和筛选，请重新保存。'}];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),u=new URL(req.url());if(u.origin!==base){unexpected.push(u.origin);return route.abort();}
  if(u.pathname==='/connection-status')return route.fulfill({json:{configured:true,reachable:true,localQuickLogin:{available:false}}});
  if(!u.pathname.startsWith('/api/'))return route.continue();
  const call={path:u.pathname,method:req.method(),body:req.postDataJSON(),key:req.headers()['idempotency-key'],query:u.search};calls.push(call);
  if(u.pathname==='/api/v1/auth/password/login')return route.fulfill({json:{access_token:who.account_code,refresh_token:'synthetic-refresh',actor:who,must_change_password:false,auth_method:'password'}});
  if(u.pathname==='/api/v1/auth/me')return route.fulfill({json:who});
  if(u.pathname==='/api/v1/metadata/business-options')return route.fulfill({json:options});
  if(u.pathname==='/api/v1/assistant/home')return route.fulfill({json:{archived_visits:[],display_policy:{definition:{message_order:'desc'}},team_summary:{overdue:0,claim:0,handover:0}}});
  if(u.pathname==='/api/v1/tasks/overview')return route.fulfill({json:{items:[],metrics:{today_completed:0,today_pending:0,all_pending:0}}});
  if(['/api/v1/notifications','/api/v1/tasks','/api/v1/customers'].includes(u.pathname))return route.fulfill({json:{items:[],total:0,has_more:false}});
  if(u.pathname==='/api/v1/opportunities/overview')return route.fulfill({json:{metrics:{won:0,total:2,active:1,newCount:1,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}});
  if(u.pathname==='/api/v1/opportunities')return route.fulfill({json:{items:[{id:'op-a',name:'服务器升级项目',customer_id:'customer-a',customer_name:'合成企业',stage:'qualified',amount:800000,owner_id:owner,owner_name:'合成销售',team_name:'华北团队',expected_close_date:'2030-05-15',product_line:'基础设施'},{id:'op-b',name:'数据库扩容项目',customer_id:'customer-b',customer_name:'另一企业',stage:'proposal',amount:600000,owner_id:owner,owner_name:'合成销售',team_name:'华北团队',expected_close_date:'2030-06-15'}],summary:{total:2},has_more:false,next_offset:null,team_options:hideFacets?[]:[{id:team,name:'华北团队'}],facets:{owner_options:hideFacets?[]:[{id:owner,name:'合成销售'}],years:[2030]}}});
  if(u.pathname==='/api/v1/opportunity-views/options')return route.fulfill({json:{columns:['name','stage','owner','amount','signal','money','close','product'].map(value=>({value,label:{name:'商机 / 客户',owner:'负责人',amount:'商机金额',product:'产品线'}[value]||value})),default_columns:defaultViewConfig().columns,can_create_private:true,definition_version:'shenma_opportunity_view_v1'}});
  if(u.pathname==='/api/v1/opportunity-views'){
   if(req.method()==='GET')return route.fulfill({json:{items:views,total:views.length,limit:100,offset:0,data_source:'database'}});
   if(failPost){failPost=false;return route.fulfill({status:503,json:{detail:'合成保存失败，请重试'}});}
   const next=row('created',call.body.name,call.body.config);views.push(next);return route.fulfill({status:201,json:next});
  }
  if(u.pathname.startsWith('/api/v1/opportunity-views/')){
   const id=u.pathname.split('/').at(-1),item=views.find(v=>v.id===id);
   if(req.method()==='GET'&&deferredDetail&&id==='seed'){const previous=structuredClone(item);await deferredDetail.promise;return route.fulfill({json:previous});}
   if(req.method()==='GET')return missing||forbidden||!item?route.fulfill({status:forbidden?403:404,json:{detail:forbidden?'视图权限已变化':'视图已删除'}}):route.fulfill({json:item});
   if(failPut){failPut=false;return route.fulfill({status:409,json:{detail:'视图已被更新，请刷新后重试'}});}
   if(req.method()==='DELETE'){views=views.filter(v=>v.id!==id);return route.fulfill({json:{id,deleted:true,version_no:item.version_no+1}});}
   Object.assign(item,{name:call.body.name,...(call.body.config?{config:call.body.config,config_valid:true,config_error:null}:{}),version_no:item.version_no+1});return route.fulfill({json:item});
  }
  unexpected.push(u.pathname);return route.fulfill({status:503,json:{detail:'Unexpected synthetic request'}});
 });
 try{
  await page.goto(base+'/?mode=live');await page.getByPlaceholder('请输入账号名或手机号').fill('s3manager');await page.getByPlaceholder('请输入密码',{exact:true}).fill('synthetic-only');await page.waitForFunction(()=>SalesRuntime.current.data.password==='synthetic-only');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.getByRole('checkbox',{name:'我已阅读并同意',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.agreed);await page.locator('[data-handler="submitLogin"]').click();await page.waitForFunction(()=>SalesRuntime.current.route==='pages/index/index');await page.evaluate(()=>SalesRuntime.userRoute('/pages/workbench/index'));
  await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor();await page.waitForFunction(()=>!SalesRuntime.current.data.opportunityListLoading);
  const restoredQuery=page.waitForResponse(response=>{const url=new URL(response.url());return url.pathname==='/api/v1/opportunities'&&url.searchParams.get('owner_id')===owner;});
  await select(page,'个人视图','华北重点');await restoredQuery;await page.waitForFunction(()=>SalesRuntime.current.data.opportunityQuery==='年度项目'&&!SalesRuntime.current.data.opportunityListLoading);
  const restored=await page.evaluate(()=>({params:SalesRuntime.current.opportunityParams(),columns:SalesRuntime.current.data.opportunityViewColumns,group:SalesRuntime.current.data.opportunityViewGroupBy}));
  assert.equal(restored.params.ownerId,owner);assert.equal(restored.params.teamId,team);assert.equal(restored.params.scope,'self');assert.equal(restored.params.productLine,'基础设施');assert.deepEqual(restored.params.stages,['qualified']);assert.equal(restored.params.includeClosed,false);assert.equal(restored.params.year,2030);assert.deepEqual(restored.params.quarters,[2]);assert.equal(restored.params.grade,'B');assert.equal(restored.params.order,'close_date');assert.deepEqual(restored.columns,seeded.columns);assert.equal(restored.group,'none');
  assert(calls.some(c=>c.path==='/api/v1/opportunities'&&c.query.includes('owner_id='+owner)&&!c.query.includes('owner=')));
  await page.getByRole('button',{name:'视图设置',exact:true}).click();await page.getByRole('button',{name:'产品线前移',exact:true}).click();assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityViewColumns),['name','owner','product','amount']);await page.getByRole('button',{name:'产品线后移',exact:true}).click();assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityViewColumns),seeded.columns);await page.getByRole('button',{name:'视图设置',exact:true}).click();
  hideFacets=true;await page.evaluate(()=>SalesRuntime.current.loadAllOpportunities());await page.getByRole('alert').filter({hasText:'筛选仍保留'}).waitFor();assert.equal(await page.evaluate(()=>SalesRuntime.current.opportunityParams().ownerId),owner);assert.equal(await page.evaluate(()=>SalesRuntime.current.opportunityParams().teamId),team);hideFacets=false;await page.evaluate(()=>SalesRuntime.current.loadAllOpportunities());await page.getByRole('alert').filter({hasText:'筛选仍保留'}).waitFor({state:'hidden'});
  const body=await page.locator('.ds-opp-groups').innerText();assert(body.indexOf('服务器升级项目')<body.indexOf('数据库扩容项目'));
  await reveal(page,'产品线筛选','textbox');await page.getByRole('textbox',{name:'产品线筛选',exact:true}).fill('未保存的产品线');await page.getByRole('textbox',{name:'产品线筛选',exact:true}).press('Enter');
  await page.getByRole('button',{name:'重命名',exact:true}).click();await page.getByRole('textbox',{name:'视图名称',exact:true}).fill('重点项目');await page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click();await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});
  const rename=calls.find(c=>c.path.endsWith('/seed')&&c.method==='PUT');assert.deepEqual(Object.keys(rename.body).sort(),['name','version_no']);assert(rename.key);assert.deepEqual(views.find(v=>v.id==='seed').config,seeded);
  await select(page,'个人视图','默认视图');await page.waitForFunction(()=>SalesRuntime.current.data.opportunityQuery==='');assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityViewColumns),defaultViewConfig().columns);
  await select(page,'商机排序','预计关单日期');await select(page,'商机分组','不分组');await page.getByRole('button',{name:'视图设置',exact:true}).click();await reveal(page,'产品线筛选','textbox');await page.getByRole('textbox',{name:'产品线筛选',exact:true}).fill('云平台');await page.getByRole('textbox',{name:'产品线筛选',exact:true}).press('Enter');
  await page.getByRole('button',{name:'保存为新视图',exact:true}).click();await page.getByRole('textbox',{name:'视图名称',exact:true}).fill('我常用的商机');await page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click();await page.getByRole('alert').filter({hasText:'合成保存失败'}).waitFor();assert.equal(await page.getByRole('textbox',{name:'视图名称'}).inputValue(),'我常用的商机');assert.equal(views.length,2);
  await page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click();await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});const posts=calls.filter(c=>c.path==='/api/v1/opportunity-views'&&c.method==='POST');assert.equal(posts.length,2);assert(posts[0].key);assert.equal(posts[0].key,posts[1].key);assert.equal(views.at(-1).config.filters.product_line,'云平台');
  failPut=true;await page.getByRole('button',{name:'更新当前视图',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click();await page.getByRole('alert').filter({hasText:'视图已被更新'}).waitFor();await page.getByRole('dialog').getByRole('button',{name:/取\s*消/}).click();await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'删除视图',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'确认删除',exact:true}).click();await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});assert(!views.some(v=>v.id==='created'));const deletion=calls.find(c=>c.method==='DELETE');assert(deletion.key);assert.equal(deletion.body.version_no,1);
  await select(page,'个人视图','旧版视图');await page.getByRole('alert').filter({hasText:'视图配置已失效'}).waitFor();assert.deepEqual(await page.evaluate(()=>SalesRuntime.current.data.opportunityViewColumns),defaultViewConfig().columns);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityQuery),'');
  await page.getByRole('button',{name:'更新当前视图',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:/保\s*存/}).click();await page.locator('.ant-modal-wrap').waitFor({state:'hidden'});assert.equal(views.find(v=>v.id==='invalid').config_valid,true);assert.equal(views.find(v=>v.id==='invalid').config_error,null);
  forbidden=true;await select(page,'个人视图','重点项目');await page.getByRole('alert').filter({hasText:'视图权限已变化'}).waitFor();forbidden=false;assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityQuery),'');assert.equal(await page.getByRole('button',{name:'更新当前视图',exact:true}).count(),0);await page.getByRole('combobox',{name:'个人视图',exact:true}).click();assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('重点项目',{exact:true}).count(),0);await page.keyboard.press('Escape');await page.getByRole('button',{name:'刷新视图',exact:true}).click();
  missing=true;await select(page,'个人视图','重点项目');await page.getByRole('alert').filter({hasText:'视图已删除'}).waitFor();missing=false;assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityProductLine),'');
  await page.getByRole('button',{name:'刷新视图',exact:true}).click();await select(page,'个人视图','重点项目');await page.waitForFunction(()=>SalesRuntime.current.data.opportunityQuery==='年度项目');
  await mkdir('/tmp/shenma-s3-browser',{recursive:true});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));await page.locator('.ds-opp-count').click();await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});await page.evaluate(()=>{window.scrollTo(0,0);document.querySelectorAll('[data-page-scroll],#page,.ds-opp-list').forEach(el=>el.scrollTo(0,0));return new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));});await page.screenshot({path:`/tmp/shenma-s3-browser/views-${width}.png`,fullPage:true});
  // In-place account/company switch deliberately retains the Page object to catch stale React memory.
  await select(page,'个人视图','默认视图');let release;deferredDetail={promise:new Promise(r=>release=r)};const oldResponse=page.waitForResponse(r=>r.url().endsWith('/opportunity-views/seed'));await select(page,'个人视图','重点项目');
  who={...actor,workspace_id:'another-company',user_id:'another-user',account_code:'s3second'};views=[];
  await page.evaluate(async()=>{await SalesRuntime.app.loginWithApi('manager','s3second','synthetic-only');await SalesRuntime.current.onShow();});release();await oldResponse;deferredDetail=null;
  await page.waitForFunction(()=>SalesRuntime.current.data.opportunityQuery===''&&SalesRuntime.current.data.opportunityViewIdentity.includes('another-company'));await page.getByRole('button',{name:'保存为新视图',exact:true}).waitFor({state:'visible'});
  await page.getByRole('combobox',{name:'个人视图',exact:true}).click();assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('重点项目',{exact:true}).count(),0);assert.equal(await page.locator('.ant-select-dropdown:visible').getByText('旧版视图',{exact:true}).count(),0);await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('button',{name:'更新当前视图',exact:true}).count(),0);assert.equal(await page.evaluate(()=>Object.entries(localStorage).some(([k,v])=>/重点项目|旧版视图|我常用的商机/.test(v))),false);
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
 }catch(e){await mkdir('/tmp/shenma-s3-browser',{recursive:true});await page.screenshot({path:`/tmp/shenma-s3-browser/failure-${width}.png`,fullPage:true});await writeFile(`/tmp/shenma-s3-browser/failure-${width}.json`,JSON.stringify({body:await page.locator('body').innerText(),errors,unexpected,calls},null,2));throw e;}
 finally{await context.close();}
});
