/** Real shared Page handlers + synthetic transport. No real customer/task writes. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir} from 'node:fs/promises';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const customer = '00000010-0000-4000-8000-000000000001';
let server, browser, base;
before(async () => {
  server = createSalesWebServer({previewOnly:true, ...(process.env.SALES_WEB_TEST_DIST ? {staticDir:process.env.SALES_WEB_TEST_DIST} : {})});
  await new Promise(r=>server.listen(0,'127.0.0.1',r)); base=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));});
async function fixture(run, contact=null) {
  const context=await browser.newContext({viewport:{width:1440,height:950},serviceWorkers:'block'}),errors=[],blocked=[];
  await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','manager'));
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)){blocked.push(url.pathname);return route.abort();}
    return route.continue();
  });
  const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
  try {
    await page.goto(base+'/?mode=preview#/pages/index/index');
    await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session && !SalesRuntime.app._capabilityFlight);
    await page.evaluate(({customer,contact})=>{
      window.contractProbe={writes:[],toasts:[],fail:false};
      const provider=SalesPreview,toast=SalesRuntime.wx.showToast;
      SalesRuntime.wx.showToast=o=>{contractProbe.toasts.push(o.title);return toast(o);};
      window.SalesPreview={...provider,request(o){
        const path=new URL(o.url,location.href).pathname,method=o.method || 'GET';
        if(/POST|PUT|PATCH|DELETE/.test(method))contractProbe.writes.push({path,method,data:structuredClone(o.data)});
        if(path.endsWith(`/customers/${customer}/overview`))return provider.request({...o,success(r){o.success?.({...r,data:{...r.data,primary_contact:contact,source_code:'公司分配',customer_type_code:'存量客户'}});}});
        if(path.endsWith(`/customers/${customer}`) && method==='PATCH' && contractProbe.fail){
          const r={statusCode:422,data:{detail:[{loc:['body','contact_title'],type:'string_too_long',msg:'server-internal-do-not-render',input:'private-do-not-render',ctx:{secret:'do-not-render'}}]}};
          queueMicrotask(()=>{o.success?.(r);o.complete?.(r);});return {abort(){}};
        }
        return provider.request(o);
      }};
    },{customer,contact});
    await run(page);
    assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  } catch(error) {
    console.error('FORM_CONTRACT_DIAGNOSTIC',await page.evaluate(()=>({route:SalesRuntime.current?.route,probe:contractProbe,text:document.body.innerText.slice(-2200)})));throw error;
  } finally {await context.close();}
}
async function edit(page) {
  await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/customer-edit/index?customerId='+id}),customer);
  await page.getByRole('region',{name:'维护客户信息',exact:true}).waitFor();
  await page.waitForFunction(()=>!SalesRuntime.current.data.loading && Boolean(SalesRuntime.current.loadedForm));
}
const patches=page=>page.evaluate(()=>contractProbe.writes.filter(r=>r.method==='PATCH' && /\/customers\//.test(r.path)));
async function shot(page,name){if(process.env.FORM_CONTRACT_SHOTS){await mkdir(process.env.FORM_CONTRACT_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.FORM_CONTRACT_SHOTS}/${name}.png`,animations:'disabled'});}}

test('customer maintenance without contact omits optional empty fields and retains historical directory values',()=>fixture(async page=>{
  await edit(page);
  assert.equal(await page.getByRole('textbox',{name:'联系人姓名',exact:true}).getAttribute('maxlength'),'100');
  assert.equal(await page.getByRole('textbox',{name:'联系人职位',exact:true}).getAttribute('maxlength'),'100');
  assert.equal(await page.getByRole('textbox',{name:'合作伙伴',exact:true}).getAttribute('maxlength'),'200');
  await page.getByRole('textbox',{name:'合作伙伴',exact:true}).fill('合成维护伙伴');
  await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await page.waitForFunction(()=>contractProbe.writes.some(r=>r.method==='PATCH'));
  const [request]=await patches(page);
  assert.equal(request.data.partner_name,'合成维护伙伴');assert.equal(request.data.source,'公司分配');assert.equal(request.data.customer_type,'存量客户');
  for(const key of ['contact_name','contact_title','contact_role','name','demand_summary','next_action'])assert.equal(Object.hasOwn(request.data,key),false,key);
}));

test('customer maintenance preserves explicit clear and failed edits with persistent field-specific feedback',()=>fixture(async page=>{
  await edit(page);
  const name=page.getByRole('textbox',{name:'联系人姓名',exact:true}),title=page.getByRole('textbox',{name:'联系人职位',exact:true});
  await name.fill('');await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'联系人姓名暂不支持清空'}).waitFor();assert.deepEqual(await patches(page),[]);assert.equal(await name.inputValue(),'');
  await name.fill('合成已登记联系人');await title.fill('合成新职位');
  await page.evaluate(()=>contractProbe.fail=true);await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'请检查联系人职位的填写内容'}).waitFor();
  assert.equal(await title.inputValue(),'合成新职位');assert.equal(await name.inputValue(),'合成已登记联系人');
  assert.equal((await patches(page)).length,1);assert.equal(Object.hasOwn((await patches(page))[0].data,'contact_name'),false);
  assert.doesNotMatch(await page.locator('body').innerText(),/server-internal|private-do-not-render/);
  await shot(page,'customer-maintenance-field-error');
  await page.evaluate(()=>contractProbe.fail=false);await page.getByRole('button',{name:'保存修改',exact:true}).click();
  await page.waitForFunction(()=>contractProbe.writes.filter(r=>r.method==='PATCH').length===2);
},{name:'合成已登记联系人',title:'原职位',relationship_role_code:'decision_maker'}));

test('task draft overflow and Unicode minimum show errors, keep content and never enter confirmation or write',()=>fixture(async page=>{
  await page.evaluate(()=>{
    const {draftScope}=SalesRuntime.require('utils/draftScope');
    SalesRuntime.wx.setStorageSync(`retryTaskDraft:${draftScope(SalesRuntime.app.globalData.session)}`,{description:'甲'.repeat(501),priority:'中'});
    SalesRuntime.wx.navigateTo({url:'/pages/management-task-create/index?retry=1'});
  });
  await page.getByRole('region',{name:'创建任务',exact:true}).waitFor();
  await page.waitForFunction(()=>!SalesRuntime.current.data.recipientLoading);
  assert.equal(await page.getByRole('textbox',{name:'任务描述',exact:true}).inputValue(),'甲'.repeat(501));
  await page.getByRole('button',{name:'下发任务',exact:true}).click();
  await page.locator('.ant-form-item-explain-error').filter({hasText:'任务描述最多填写 500 字'}).waitFor();
  assert.equal(await page.getByRole('textbox',{name:'任务描述',exact:true}).inputValue(),'甲'.repeat(501));
  assert.equal(await page.locator('.wx-modal-mask').count(),0);
  await shot(page,'task-description-overflow');
  await page.evaluate(()=>SalesRuntime.current.inputDescription({detail:{value:'𠮷𠮷𠮷'}}));
  await page.getByRole('button',{name:'下发任务',exact:true}).click();
  await page.locator('.ant-form-item-explain-error').filter({hasText:'至少 5 个字'}).waitFor();
  assert.equal(await page.locator('.wx-modal-mask').count(),0);
  await page.getByRole('textbox',{name:'任务描述',exact:true}).fill('已修正为正常的任务描述');
  await page.waitForFunction(()=>document.querySelector('#department-task-description')?.getAttribute('aria-invalid')==='false');
  assert.equal(await page.locator('#department-task-description').getAttribute('aria-invalid'),'false');
  assert.deepEqual(await page.evaluate(()=>contractProbe.writes.filter(r=>r.path.endsWith('/tasks'))),[]);
}));
