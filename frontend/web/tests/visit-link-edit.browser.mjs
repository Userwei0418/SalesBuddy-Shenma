// Actual Web renderer/controller with isolated synthetic API; no shared runtime.
import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
let browser,server,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>{server.close(r);server.closeAllConnections();});});
async function fixture(run){
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(15000);
  await context.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  try{
    await page.goto(`${base}/?mode=preview#/pages/visit-entry/index`);
    await page.waitForFunction(()=>window.SalesRuntime?.current?.data.customerResults?.length>0);
    const customer=await page.evaluate(()=>SalesRuntime.current.data.customerResults[0]);
    await page.locator('.ds-ve-results').getByText(customer.name,{exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current.selectComponent('#visitEntryOpportunity')?.data.items?.length>0);
    const op=await page.evaluate(()=>SalesRuntime.current.selectComponent('#visitEntryOpportunity').data.items[0]);
    await page.locator('.ds-fvo-list').getByText(op.name,{exact:true}).click();
    const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
    await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').fill(`对接人：合成联系人\n沟通内容：本次与客户确认试点需求，客户同意安排测试并核对验收范围。\n下一步计划：${today}由我整理并发送测试方案给客户确认。\n跟进日期：${today}\n创建时间：${today}`);
    await page.getByRole('button',{name:'提交结构化',exact:true}).click();
    await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.selectedOpportunity);
    await page.getByText('仅关联此商机，保存跟进不会修改商机信息',{exact:true}).waitFor();
    await page.evaluate(()=>{window.visitWrites=[];const original=SalesPreview.request.bind(SalesPreview);window.SalesPreview={...SalesPreview,request:options=>{if(options.method==='POST')visitWrites.push({path:new URL(options.url,location.href).pathname,body:JSON.parse(JSON.stringify(options.data))});return original(options);}};});
    await run(page,op);assert.deepEqual(errors,[]);
  }catch(error){console.log('DIAGNOSTIC',await page.evaluate(()=>{const p=SalesRuntime.current;return {editing:p.data.editing,opportunityEditing:p.data.opportunityEditing,isFde:p.data.isFde,customerConfirmed:p.data.customerConfirmed,retry:p._departmentRetry,error:p.data.errorText,flow:p.data.flowStep,block:p.data.blockReason,draftReady:p.draftReady,draftSaveError:p.data.draftSaveError,draftReadFailed:p.draftReadFailed,userKey:p.userKey,draftKey:p.draftKey,preparing:Boolean(p.preparing),busy:p.data.busy,toast:[...document.querySelectorAll('.wx-toast')].map(e=>e.textContent),writes:window.visitWrites,runtimeErrors:SalesRuntime.errors,picker:p.selectComponent('fde-picker')?.data,components:[...p._components.values()].map(c=>({tag:c._tag,dead:c._destroyed,id:c._attributes.id})),text:document.querySelector('.ds-vc-link-mode')?.textContent};}));throw error;}finally{await context.close();}
}
async function quality(page){await page.getByRole('button',{name:'下一步 · AI 质检',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);return page.evaluate(()=>visitWrites.filter(r=>r.path.endsWith('/visit-flow/quality')).at(-1).body);}
test('link-only bypasses incomplete historical opportunity, keeps FDE attendance, archives without mutation',{timeout:90000},()=>fixture(async(page,op)=>{
  assert.equal(await page.locator('.ds-vc-inline-form').count(),0);
  await page.evaluate(()=>{const p=SalesRuntime.current;p.setData({selectedOpportunity:{...p.data.selectedOpportunity,expected_close_date:null,amount:null}});});
  await page.locator('.ds-vc-visit-participants').getByRole('button',{name:/^选\s*择$/}).click();
  await page.locator('.ds-oc-person input[type=checkbox]').first().click();
  await page.waitForFunction(()=>SalesRuntime.current.selectComponent('fde-picker').data.draft.length===1);
  await page.getByRole('button',{name:'确认选择',exact:true}).click();
  const participants=await page.evaluate(()=>SalesRuntime.current.data.opportunityDraft.visit_fde_member_ids);
  assert.equal(participants.length,1);
  const body=await quality(page);assert.equal(body.opportunity_id,op.id);assert.equal(body.opportunity_mutation,null);assert.deepEqual(body.fde_participant_ids,participants);
  await page.getByRole('button',{name:'返回完善',exact:true}).click();
  await page.locator('.ds-vc-visit-participants').getByRole('button',{name:/^选\s*择$/}).click();
  await page.locator('.ds-oc-person input[type=checkbox]').first().click();await page.waitForFunction(()=>SalesRuntime.current.selectComponent('fde-picker').data.draft.length===0);await page.getByRole('button',{name:'确认选择',exact:true}).click();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.canSubmit),false,'attendance changes invalidate quality');
  await page.locator('.ds-vc-visit-participants').getByRole('button',{name:/^选\s*择$/}).click();
  await page.locator('.ds-oc-person input[type=checkbox]').first().click();
  await page.waitForFunction(()=>SalesRuntime.current.selectComponent('fde-picker').data.draft.length===1);
  await page.getByRole('button',{name:'确认选择',exact:true}).click();
  await quality(page);
  await page.getByRole('button',{name:'确认保存',exact:true}).click();await page.getByRole('button',{name:'确认归档',exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.data.archived);
  const saved=await page.evaluate(()=>visitWrites.find(r=>r.path.endsWith('/visits')).body);
  assert.equal(saved.fields.opportunity_id,op.id);assert.equal(Object.hasOwn(saved.fields,'_opportunity_mutation'),false);
  assert.deepEqual(saved.fde_participant_ids,participants);
}));
test('loaded materials restored to equivalent decimals produce no mutation; failed load blocks explicit edits',{timeout:90000},()=>fixture(async(page,op)=>{
  await page.evaluate(op=>{
    window.failMaterialRead=true;
    const original=SalesPreview.request.bind(SalesPreview);
    window.SalesPreview={...SalesPreview,request:options=>{
      if(new URL(options.url,location.href).pathname.endsWith(`/opportunities/${op.id}/materials`)){
        queueMicrotask(()=>options.success({statusCode:window.failMaterialRead?503:200,data:window.failMaterialRead?{detail:'合成物料读取失败'}:{opportunity_id:op.id,version_no:op.version_no,trade_relation:'direct',items:[{id:'line-synthetic',version_no:1,material_id:'material-synthetic',quantity:'1',unit_price:'113',usage:'self_use',catalog_snapshot:{version_no:1,name:'合成验证物料',recognition_rule:'manual'}}]}}));return {abort(){}};
      }return original(options);
    }};
  },op);
  await page.getByRole('button',{name:'修改商机信息',exact:true}).click();
  await page.getByRole('button',{name:'重新读取物料',exact:true}).waitFor();
  await page.getByRole('button',{name:'下一步 · AI 质检',exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.data.errorText.includes('合成物料读取失败'));
  assert.equal(await page.evaluate(()=>visitWrites.filter(r=>r.path.endsWith('/visit-flow/quality')).length),0);
  await page.evaluate(()=>{window.failMaterialRead=false;});
  await page.getByRole('button',{name:'重新读取物料',exact:true}).click();
  await page.getByRole('textbox',{name:'数量',exact:true}).fill('2');
  await page.getByRole('textbox',{name:'数量',exact:true}).fill('1.0000');
  await page.getByRole('textbox',{name:'成交单价（元，含税）',exact:true}).fill('113.00');
  const body=await quality(page);assert.equal(body.opportunity_mutation,null);
}));
test('explicit edit can be cancelled without leaking draft changes; object denial hides edit',{timeout:90000},()=>fixture(async(page)=>{
  await page.getByRole('button',{name:'修改商机信息',exact:true}).click();
  await page.getByPlaceholder('填写项目名，可用部门、场景区分').fill('合成修改但不提交');
  await page.getByRole('button',{name:'取消修改，仅关联',exact:true}).click();
  await page.locator('.ds-vc-inline-form').waitFor({state:'detached'});
  const body=await quality(page);assert.equal(body.opportunity_mutation,null);
  await page.getByRole('button',{name:'返回完善',exact:true}).click();
  await page.evaluate(()=>{const p=SalesRuntime.current;p.setData({selectedOpportunity:{...p.data.selectedOpportunity,capabilities:{can_edit:false}}});});
  assert.equal(await page.getByRole('button',{name:'修改商机信息',exact:true}).count(),0);
  await page.evaluate(()=>SalesRuntime.current.startOpportunityEdit());
  assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityEditing),false);
}));
