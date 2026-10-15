/** Actual PC UI + shared controllers; synthetic transport, no service access. */
import test, {before,after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdir,mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
let server,browser,base;
before(async()=>{
  server=createSalesWebServer({previewOnly:true,staticDir:process.env.SALES_WEB_TEST_DIST});
  await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;
  browser=await chromium.launch({channel:'chrome',headless:true});
});
after(async()=>{await browser?.close();server?.closeAllConnections();await new Promise(r=>server?.close(r));});
async function fixture(run){
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),errors=[],blocked=[];
  await context.addInitScript(()=>{if(location.origin!=='null')sessionStorage.setItem('sales-web:preview-role','sales');});
  context.on('page',page=>{page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));});
  await context.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(url.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(url.pathname)){blocked.push(url.href);return route.abort();}
    return route.continue();
  });
  try{await run(context);assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);}
  finally{await context.close();}
}
async function entry(context,suffix=''){
  const page=await context.newPage();await page.goto(base+'/?mode=preview#/pages/visit-entry/index'+suffix);
  await page.waitForFunction(()=>window.SalesRuntime?.current?.draftStore && document.querySelector('.ds-ve-main textarea'));
  return page;
}
const textArea=page=>page.locator('.ds-ve-main textarea');
async function saved(page,text){await page.waitForFunction(text=>{
  const p=SalesRuntime.current,key='sales-web:preview:v1:'+p.draftKey;
  return JSON.parse(localStorage.getItem(key)||'{}').transcript===text && !p.data.draftSaveError;
},text);}
test('actual PC input survives refresh and tab close and shows restoration',()=>fixture(async context=>{
  let page=await entry(context);await textArea(page).fill('今天讨论验收范围，明天上午发送方案。');
  await saved(page,'今天讨论验收范围，明天上午发送方案。');
  await page.reload();await textArea(page).waitFor();
  assert.equal(await textArea(page).inputValue(),'今天讨论验收范围，明天上午发送方案。');
  assert.match(await page.locator('.ds-visit-draft-status').innerText(),/恢复/);
  await page.close();page=await entry(context);
  assert.equal(await textArea(page).inputValue(),'今天讨论验收范围，明天上午发送方案。');
  await mkdir('.runtime/identity-draft-20261013-01/screenshots',{recursive:true});
  await page.screenshot({path:'.runtime/identity-draft-20261013-01/screenshots/restored.png',fullPage:true});
}));
test('two tabs cannot silently overwrite; blocked edit can retry after other tab closes',()=>fixture(async context=>{
  const first=await entry(context);await textArea(first).fill('共同起点');await saved(first,'共同起点');
  const second=await entry(context);await textArea(second).fill('第二页未保存内容');
  await second.getByRole('alert').filter({hasText:'另一标签页'}).waitFor();
  assert.equal(await first.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview:v1:'+SalesRuntime.current.draftKey)).transcript),'共同起点');
  await first.close();await second.getByRole('button',{name:'重试保存草稿'}).click();await saved(second,'第二页未保存内容');
}));
test('in-progress Chinese composition flushes to draft on pagehide without a model request',()=>fixture(async context=>{
  const page=await entry(context);await textArea(page).fill('已输入');await saved(page,'已输入');await textArea(page).focus();
  const cdp=await context.newCDPSession(page);await cdp.send('Input.imeSetComposition',{text:'zhongwen',selectionStart:8,selectionEnd:8});
  const visible=await textArea(page).inputValue();assert.match(visible,/zhongwen/);
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide')));
  assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview:v1:'+SalesRuntime.current.draftKey)).transcript),visible);
}));
test('explicit customer context keeps independent drafts and failed storage keeps typed content visible',()=>fixture(async context=>{
  const a=await entry(context,'?customerId=customer-A');await textArea(a).fill('客户 A 的草稿');await saved(a,'客户 A 的草稿');await a.close();
  const b=await entry(context,'?customerId=customer-B');assert.equal(await textArea(b).inputValue(),'');await textArea(b).fill('客户 B 的草稿');await saved(b,'客户 B 的草稿');
  await b.evaluate(()=>{Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError');};});
  await textArea(b).fill('存储失败仍保留本页');await b.getByRole('alert').filter({hasText:'草稿'}).waitFor();
  assert.equal(await textArea(b).inputValue(),'存储失败仍保留本页');
  assert.equal(await b.evaluate(()=>JSON.parse(localStorage.getItem('sales-web:preview:v1:'+SalesRuntime.current.draftKey)).transcript),'客户 B 的草稿');
  assert.equal(await b.evaluate(()=>{const e=new Event('beforeunload',{cancelable:true});window.dispatchEvent(e);return e.defaultPrevented;}),true,'unsaved content requests a close warning');
}));
test('editing a saved visit restores supplementary fields and reverting to original does not revive an intermediate draft',()=>fixture(async context=>{
  const page=await entry(context);
  const id=await page.evaluate(async()=>{const r=await SalesRuntime.require('utils/apiClient').request({path:'/visits'});return r.items[0].id;});
  await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index?visitId='+id}),id);
  await page.waitForFunction(()=>SalesRuntime.current.draftReady && SalesRuntime.current.data.editing);
  const input=page.getByPlaceholder('填写本次沟通的客户对接人');const original=await input.inputValue();
  await input.fill('补充联系人');await page.waitForFunction(()=>SalesRuntime.current.data.draftSavedAt>0 && !SalesRuntime.current.data.draftSaveError);
  await page.reload();await input.waitFor();assert.equal(await input.inputValue(),'补充联系人');
  await input.fill(original);await page.reload();await input.waitFor();assert.equal(await input.inputValue(),original);
}));
test('legacy text requires explicit recovery and keeps the original key without reusing remote references',()=>fixture(async context=>{
  const page=await entry(context);const oldKey=await page.evaluate(()=>{
    const p=SalesRuntime.current,key='sales-web:preview:v1:visitEntryV2:'+p.userKey.split(':service:')[0];
    sessionStorage.setItem(key,JSON.stringify({draftId:'old',transcript:'旧版未完成文字',customerId:'old-customer',importId:'old-import'}));return key;
  });
  await page.reload();await page.getByRole('button',{name:'恢复旧稿文字'}).waitFor();assert.equal(await textArea(page).inputValue(),'');
  await page.getByRole('button',{name:'恢复旧稿文字'}).click();await page.getByRole('dialog').getByText('恢复文字',{exact:true}).click();
  await saved(page,'旧版未完成文字');
  assert.deepEqual(await page.evaluate(()=>({customer:SalesRuntime.current.data.customerId,importId:SalesRuntime.current.data.importId})),{customer:'',importId:''});
  assert.equal(await page.evaluate(k=>JSON.parse(sessionStorage.getItem(k)).customerId,oldKey),'old-customer');
}));
test('failed supplementary draft save prevents ordinary navigation from discarding typed content',()=>fixture(async context=>{
  const page=await entry(context);
  const id=await page.evaluate(async()=>{const r=await SalesRuntime.require('utils/apiClient').request({path:'/visits'});return r.items[0].id;});
  await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index?visitId='+id}),id);
  await page.waitForFunction(()=>SalesRuntime.current.draftReady && SalesRuntime.current.data.editing);
  await page.evaluate(()=>{Storage.prototype.setItem=function(){throw new DOMException('full','QuotaExceededError');};});
  await page.getByPlaceholder('填写本次沟通的客户对接人').fill('未保存的补充联系人');
  await page.evaluate(()=>SalesRuntime.userRoute('/pages/index/index'));
  await page.getByRole('dialog').filter({hasText:'本页草稿尚未保存'}).waitFor();
  assert.equal(await page.evaluate(()=>SalesRuntime.current.route),'pages/visit-confirm/index');
  assert.equal(await page.getByPlaceholder('填写本次沟通的客户对接人').inputValue(),'未保存的补充联系人');
}));
test('closing and reopening Chrome retains the local draft in its isolated profile',async()=>{
  const profile=await mkdtemp(join(tmpdir(),'sales-draft-profile-'));let context;const blocked=[];
  const open=async()=>{
    context=await chromium.launchPersistentContext(profile,{channel:'chrome',headless:true,viewport:{width:1440,height:1000},serviceWorkers:'block'});
    await context.addInitScript(()=>{if(location.origin!=='null')sessionStorage.setItem('sales-web:preview-role','sales');});
    await context.route('**/*',route=>{const u=new URL(route.request().url());if(u.origin!==base || /^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.href);return route.abort();}return route.continue();});
    return entry(context);
  };
  try{let page=await open();await textArea(page).fill('浏览器关闭后保留');await saved(page,'浏览器关闭后保留');await context.close();context=null;
    page=await open();assert.equal(await textArea(page).inputValue(),'浏览器关闭后保留');assert.deepEqual(blocked,[]);
  }finally{await context?.close();await rm(profile,{recursive:true,force:true});}
});

test('logging out from a saved supplementary draft does not read a null actor or overwrite its contents',()=>fixture(async context=>{
  const page=await entry(context);
  const id=await page.evaluate(async()=>{const r=await SalesRuntime.require('utils/apiClient').request({path:'/visits'});return r.items[0].id;});
  await page.evaluate(id=>SalesRuntime.wx.navigateTo({url:'/pages/visit-confirm/index?visitId='+id}),id);
  await page.waitForFunction(()=>SalesRuntime.current.draftReady && SalesRuntime.current.data.editing);
  await page.getByPlaceholder('填写本次沟通的客户对接人').fill('退出生命周期合成联系人');
  await page.waitForFunction(()=>SalesRuntime.current.data.draftSavedAt>0 && !SalesRuntime.current.data.draftSaveError);
  const before=await page.evaluate(()=>{
    window.logoutToasts=[];const toast=SalesRuntime.wx.showToast;SalesRuntime.wx.showToast=o=>{logoutToasts.push(o.title);return toast(o);};
    return Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.includes('visitSupplementV1:')));
  });
  assert.equal(Object.keys(before).length,1);
  await page.evaluate(()=>SalesRuntime.signOut());
  await page.waitForFunction(()=>SalesRuntime.current.route==='pages/login/index');
  assert.deepEqual(await page.evaluate(()=>logoutToasts),[]);
  assert.deepEqual(await page.evaluate(()=>SalesRuntime.errors),[]);
  assert.deepEqual(await page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([key])=>key.includes('visitSupplementV1:')))),before);
}));
