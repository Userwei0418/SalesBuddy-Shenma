/** Actual PC UI with frozen shared controllers and synthetic transport only. */
import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true,staticDir:process.env.SALES_WEB_TEST_DIST});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));await mkdir('.runtime/identity-business-20261013-01',{recursive:true});await writeFile('.runtime/identity-business-20261013-01/browser-window.json',JSON.stringify({base,isolated:true,mockOnly:true,closed:true}));});
async function fixture(run){
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),errors=[],blocked=[];
 await context.addInitScript(()=>{if(location.origin!=='null')sessionStorage.setItem('sales-web:preview-role','sales');});
 await context.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.href);return r.abort();}return r.continue();});
 const p=await context.newPage();p.setDefaultTimeout(10000);p.on('pageerror',e=>errors.push(e.message));
 try{
  await p.goto(base+'/?mode=preview#/pages/index/index');await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
  await p.evaluate(()=>{
   SalesRuntime.app.globalData.session.permissions['partner.read']=true;
   window.identityProbe={requests:[],toasts:[],directory:false,own:'10000000-0000-4000-8000-000000000001',external:'10000000-0000-4000-8000-000000000002'};
   const old=SalesPreview,toast=SalesRuntime.wx.showToast;SalesRuntime.wx.showToast=o=>{identityProbe.toasts.push(o.title);return toast(o);};
   const respond=(o,data,statusCode=200)=>{const r={statusCode,data};queueMicrotask(()=>{o.success?.(r);o.complete?.(r);});return {abort(){}};};
   window.SalesPreview={...old,request(o={}){
    const u=new URL(o.url,location.href),path=u.pathname,method=String(o.method||'GET').toUpperCase();identityProbe.requests.push({path,query:u.search,method,data:structuredClone(o.data)});
    if(path.endsWith('/metadata/business-options'))return old.request({...o,success(r){const data=structuredClone(r.data);data.customer_creation.business_identities=[{value:'customer',label:'客户'},{value:'partner',label:'合作伙伴'}];o.success?.({...r,data});}});
    if(path.endsWith('/directory/teams') && u.searchParams.get('purpose')==='assignment'){u.searchParams.set('permission','customer.read');return old.request({...o,url:u.href});}
    if(path.endsWith('/directory/partners'))return respond(o,{items:[{id:identityProbe.own,name:'本企业既有伙伴档案',status:'active',linked_customer_id:null},{id:identityProbe.external,name:'外部合作方',status:'active',linked_customer_id:null},{id:'bound',name:'已绑定其他企业',status:'active',linked_customer_id:'other'}],has_more:false,next_offset:null});
    if(path.endsWith('/customers/claim-pool')){
     if(!identityProbe.directory)return respond(o,{items:[],total:0,has_more:false,next_offset:null});
     const offset=Number(u.searchParams.get('offset')||0),size=Number(u.searchParams.get('page_size')||20),role=u.searchParams.get('business_identity');
     const all=Array.from({length:43},(_,i)=>({id:'enterprise-'+i,name:'合成企业 '+i,business_identities:i%2?['customer','partner']:['partner'],can_read_customer:i%2===0,team_name:'合成团队',owner_name:'合成人员',customer_type_code:i%2?'prospect':null})).filter(r=>role!=='customer'||r.business_identities.includes('customer'));
     return respond(o,{items:all.slice(offset,offset+size),total:all.length,has_more:offset+size<all.length,next_offset:offset+size<all.length?offset+size:null});
    }
    if(method==='POST' && /\/customers(?:\/creation-requests)?$/.test(path))return respond(o,{detail:'合成提交失败，保留录入'},503);
    if(identityProbe.pureDetail && /\/customers\/[^/]+\/(?:header|overview)$/.test(path))return old.request({...o,success(r){o.success?.({...r,data:{...r.data,business_identities:['partner'],partner_profile_id:identityProbe.own,primary_partner_id:identityProbe.external,primary_partner_name:'外部合作方',customer_type_code:null,customer_analysis_applicable:false}});}});
    if(method==='PATCH' && /\/customers\//.test(path))return respond(o,{detail:'合成保存失败，保留输入'},503);
    return old.request(o);
   }};
  });
  await p.evaluate(()=>SalesRuntime.require('utils/apiClient').getBusinessOptions({fresh:true}));
  await run(p);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
 }catch(e){console.error('IDENTITY_UI_DIAGNOSTIC',await p.evaluate(()=>({route:SalesRuntime.current?.route,data:SalesRuntime.current?.data,probe:identityProbe,errors:SalesRuntime.errors,text:document.body.innerText.slice(-1200)})));throw e;}finally{await context.close();}
}
async function go(p,route){await p.evaluate(url=>SalesRuntime.wx.navigateTo({url:'/pages/'+url}),route);await p.waitForFunction(route=>SalesRuntime.current.route==='pages/'+route.split('?')[0],route);}
async function create(p){await go(p,'customer-create/index');await p.waitForFunction(()=>SalesRuntime.current.data.identityOptionsReady && !SalesRuntime.current.data.loading);await p.locator('#customer-create-customer_name').waitFor();}
async function choose(p,key){const control=p.locator('#customer-create-'+key);await control.click();const id=await control.getAttribute('aria-controls');await p.locator('.ant-select-dropdown').filter({has:p.locator('#'+id)}).locator('.ant-select-item-option').first().click();}
async function fill(p){await p.locator('#customer-create-customer_name').fill('合成伙伴企业');await choose(p,'level_code');await choose(p,'lead_source');if(!await p.evaluate(()=>SalesRuntime.current.data.fields.find(f=>f.key==='target_team').teamId))await choose(p,'target_team');await p.locator('#customer-create-contact_name').fill('合成联系人');await p.locator('#customer-create-contact_title').fill('负责人');await choose(p,'contact_role');await p.waitForFunction(()=>SalesRuntime.current.data.missingCount===0);}
async function pick(p,own,name){await p.getByRole('button',{name:own?'选择已有伙伴档案':'选择关联伙伴',exact:true}).click();const dialog=p.getByRole('dialog');await dialog.getByText(name,{exact:true}).waitFor();await dialog.locator('.ds-partner-result').filter({hasText:name}).getByRole('button').click();await dialog.waitFor({state:'hidden'});}
test('pure partner create uses conditional type, stable own/external links and preserves form on failure',()=>fixture(async p=>{
 await create(p);await p.getByRole('checkbox',{name:'合作伙伴',exact:true}).check();await p.getByRole('checkbox',{name:'客户',exact:true}).uncheck();await p.locator('#customer-create-customer_type').waitFor({state:'detached'});
 await fill(p);await pick(p,true,'本企业既有伙伴档案');await pick(p,false,'外部合作方');
 await p.getByRole('button',{name:'创建客户',exact:true}).click();await p.getByRole('button',{name:'确认创建',exact:true}).click();await p.waitForFunction(()=>identityProbe.requests.some(r=>r.method==='POST'&&/\/customers$/.test(r.path)));
 const request=await p.evaluate(()=>identityProbe.requests.findLast(r=>r.method==='POST'&&/\/customers$/.test(r.path)));assert.deepEqual(request.data.business_identities,['partner']);assert.equal(request.data.customer_type,null);assert.equal(request.data.partner_profile_id,'10000000-0000-4000-8000-000000000001');assert.equal(request.data.partner_id,'10000000-0000-4000-8000-000000000002');
 assert.equal(await p.locator('#customer-create-customer_name').inputValue(),'合成伙伴企业');await mkdir('.runtime/identity-business-20261013-01/screenshots',{recursive:true});await p.screenshot({path:'.runtime/identity-business-20261013-01/screenshots/partner-create.png',fullPage:true});
}));
test('at least one identity remains and customer type reappears for dual role; picker excludes own link',()=>fixture(async p=>{
 await create(p);await p.getByRole('checkbox',{name:'客户',exact:true}).click();await p.waitForFunction(()=>identityProbe.toasts.includes('请至少保留一种业务身份'));assert.deepEqual(await p.evaluate(()=>SalesRuntime.current.data.businessIdentities),['customer']);
 await p.getByRole('checkbox',{name:'合作伙伴',exact:true}).check();await pick(p,true,'本企业既有伙伴档案');await p.getByRole('button',{name:'选择关联伙伴',exact:true}).click();await p.getByRole('dialog').getByText('外部合作方',{exact:true}).waitFor();assert.equal(await p.getByRole('dialog').getByText('本企业既有伙伴档案',{exact:true}).count(),0);
 await p.getByRole('dialog').locator('.ant-modal-close').click();await p.getByRole('checkbox',{name:'客户',exact:true}).uncheck();await p.getByRole('checkbox',{name:'客户',exact:true}).check();await p.locator('#customer-create-customer_type').waitFor();
}));
test('enterprise directory filters before pagination, preserves totals and permission-gated links',()=>fixture(async p=>{
 await p.evaluate(()=>identityProbe.directory=true);await go(p,'customers/index');await p.getByText('企业档案',{exact:true}).first().click();await p.getByRole('heading',{name:'企业档案'}).waitFor();await p.getByText('合成企业 0',{exact:true}).waitFor();
 assert.equal(await p.getByRole('button',{name:'查看档案',exact:true}).count(),10);assert.equal(await p.getByText('仅目录可见',{exact:true}).count(),10);assert.match(await p.locator('.pc-enterprise-heading').innerText(),/43 家/);
 await p.locator('.pc-enterprise-heading').evaluate(el=>el.scrollIntoView());await p.getByRole('listitem',{name:'2',exact:true}).click();await p.getByText('合成企业 20',{exact:true}).waitFor();
 await p.screenshot({path:'.runtime/identity-business-20261013-01/screenshots/enterprise-directory.png',fullPage:true});
 const params=await p.evaluate(()=>identityProbe.requests.filter(r=>r.path.endsWith('/customers/claim-pool')).at(-1).query);assert.match(params,/offset=20/);
 await p.locator('.pc-filterbar [role="combobox"]').first().click();
 await p.locator('.ant-select-dropdown .ant-select-item-option').filter({hasText:/^客户$/}).click();
 await p.waitForFunction(()=>identityProbe.requests.filter(r=>r.path.endsWith('/customers/claim-pool')).at(-1).query.includes('business_identity=customer'));
 await p.getByText('合成企业 1',{exact:true}).waitFor();assert.match(await p.locator('.pc-enterprise-heading').innerText(),/21 家/);
 assert.equal(await p.getByRole('button',{name:'查看档案',exact:true}).count(),0);
 const filtered=await p.evaluate(()=>identityProbe.requests.filter(r=>r.path.endsWith('/customers/claim-pool')).at(-1).query);assert.match(filtered,/offset=0/);
}));
test('pure partner detail and edit avoid customer analysis, keep stable self profile, clear external ID explicitly',()=>fixture(async p=>{
 const id=await p.evaluate(async()=>{const x=await SalesRuntime.require('utils/apiClient').listCustomers();return x.items[0].id;});await p.evaluate(()=>identityProbe.pureDetail=true);
 await go(p,'customer-detail/index?id='+id);await p.getByText('客户评分与作战地图不适用。',{exact:true}).waitFor();assert.equal(await p.getByText('象限判断依据',{exact:true}).count(),0);assert.equal(await p.getByText('AI 生成 · 经营建议',{exact:true}).count(),0);
 await go(p,'customer-edit/index?customerId='+id);await p.getByText('纯伙伴档案不适用客户评分',{exact:true}).waitFor();assert.equal(await p.getByRole('button',{name:'选择已有伙伴档案',exact:true}).count(),0);
 await p.getByRole('button',{name:'清除关联',exact:true}).click();await p.getByRole('button',{name:'保存修改',exact:true}).click();await p.waitForFunction(()=>identityProbe.requests.some(r=>r.method==='PATCH'));
 const data=await p.evaluate(()=>identityProbe.requests.findLast(r=>r.method==='PATCH').data);assert.equal(data.partner_id,null);assert.equal(data.partner_profile_id,'10000000-0000-4000-8000-000000000001');assert.equal(Object.hasOwn(data,'customer_type'),false);
}));
