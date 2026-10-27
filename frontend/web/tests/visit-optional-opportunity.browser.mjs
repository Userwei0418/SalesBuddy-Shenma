import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'@playwright/test');
let browser,server,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();if(server)await new Promise(r=>{server.close(r);server.closeAllConnections();});});
const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const transcript=`对接人：合成联系人\n沟通内容：本次与客户确认试点需求，客户同意安排测试并核对验收范围。\n下一步计划：${today}由我整理并发送测试方案给客户确认。\n跟进日期：${today}\n创建时间：${today}`;
async function setup(role){
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),page=await context.newPage(),errors=[];
  await context.addInitScript(role=>sessionStorage.setItem('sales-web:preview-role',role),role);
  page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
  await page.goto(`${base}/?mode=preview#/pages/visit-entry/index`);
  await page.waitForFunction(()=>window.SalesRuntime?.current?.data?.customerResults?.length>0);
  const customer=await page.evaluate(()=>SalesRuntime.current.data.customerResults[0]);
  await page.locator('.ds-ve-results').getByText(customer.name,{exact:true}).click();
  await page.waitForFunction(()=>SalesRuntime.current.selectComponent('#visitEntryOpportunity')?.data?.items?.length>0);
  return {context,page,errors,customer};
}
for(const role of ['sales','fde']) test(`${role}: optional opportunity renders, selected draft restores and clearing persists into confirmation`,{timeout:50000},async()=>{
  const c=await setup(role),{page}=c;
  try{
    assert.equal(await page.getByText('选填，可只记录客户跟进',{exact:true}).count(),1);
    const op=await page.evaluate(()=>SalesRuntime.current.selectComponent('#visitEntryOpportunity').data.items[0]);
    await page.locator('.ds-fvo-list').getByText(op.name,{exact:true}).click();
    await page.waitForFunction(id=>SalesRuntime.current.data.opportunityId===id&&SalesRuntime.current.data.opportunityResolved,op.id);
    await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').fill(transcript);
    await page.reload();
    await page.getByRole('button',{name:'清除关联',exact:true}).waitFor();
    await page.waitForFunction(id=>SalesRuntime.current.data.opportunityResolved&&SalesRuntime.current.data.opportunityId===id,op.id);
    assert.equal(await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').inputValue(),transcript);
    await page.getByRole('button',{name:'清除关联',exact:true}).click();
    await page.waitForFunction(()=>!SalesRuntime.current.data.opportunityId&&SalesRuntime.current.data.canSubmit);
    await page.getByRole('button',{name:'提交结构化',exact:true}).click();
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.opportunityOptions?.length>0);
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityId),'');
    assert.match(await page.locator('.ds-vc-picker').first().innerText(),/不关联商机/);
    await page.locator('.ds-vc-picker').first().click();
    await page.locator('.ds-vc-opp-row').filter({hasText:op.name}).first().click();
    await page.getByRole('button',{name:/^确\s*定$/}).click();
    await page.waitForFunction(id=>SalesRuntime.current.data.opportunityId===id,op.id);
    await page.locator('.ds-vc-picker').first().click();await page.locator('.ds-vc-opp-row').getByText('不关联商机',{exact:true}).click();
    await page.getByRole('button',{name:/^确\s*定$/}).click();await page.reload();
    await page.waitForFunction(()=>window.SalesRuntime?.current?.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.customerConfirmed);
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityId),'');
    if(role==='fde'){assert.equal(await page.locator('.ds-vc-inline-form').count(),0);await page.getByRole('button',{name:'下一步 · AI 质检',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);}
    if(process.env.VISIT_SHOTS){fs.mkdirSync(process.env.VISIT_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.VISIT_SHOTS}/confirm-${role}.png`});}
    assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});
test('server rejects customer-only write without dropping original notes or changing association',{timeout:30000},async()=>{
  const c=await setup('fde'),{page}=c;
  try{
    await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').fill(transcript);
    await page.evaluate(()=>{const original=SalesPreview.request.bind(SalesPreview);window.SalesPreview={...SalesPreview,request:options=>{if(new URL(options.url,location.href).pathname.endsWith('/visit-flow/structure')){window.rejectedWriteSeen=true;queueMicrotask(()=>options.success?.({statusCode:403,data:{code:'FORBIDDEN',message:'当前客户不在可录入范围'}}));return{abort(){}};}return original(options);}};});
    await page.getByRole('button',{name:'提交结构化',exact:true}).click();await page.getByText('当前客户不在可录入范围；原文已保留，可修改后重新提交',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>window.rejectedWriteSeen),true);
    assert.equal(await page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……').inputValue(),transcript);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.customerId),c.customer.id);
    assert.equal(await page.evaluate(()=>SalesRuntime.current.data.opportunityId),'');assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});
