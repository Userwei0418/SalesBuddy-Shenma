/** PC upload recovery, using ordinary preview or an explicitly synthetic receipt fixture. No backend/DB acceptance. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async () => {server=createSalesWebServer({previewOnly:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async () => {await browser?.close();if(server)await new Promise(resolve=>{server.close(resolve);server.closeAllConnections();});});
const content='【测试材料】客户拜访原文，必须在确认接收前保留。';
async function client(fixture, permissions = {'visit.upload':true,'visit.retry_import':true,'visit.read':true}) {
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
  if(fixture){
    await context.exposeBinding('__uploadFixture',(_,request)=>fixture(request));
    await context.addInitScript(permissions=>{
      let provider;
      Object.defineProperty(window,'SalesPreview',{configurable:true,get:()=>provider,set(value){
        // Explicit test-only authorization; ordinary preview intentionally has no upload capability.
        const authorize=response=>{const data=response.data;const actor=data?.actor;
          const target=actor||data,grants=actor?.permission_grants||data?.grants;
          if(target?.permissions&&Array.isArray(grants))for(const [permission_code,allowed] of Object.entries(permissions)){
            target.permissions[permission_code]=allowed;
            grants.push({permission_code,scope_code:'self',team_ids:[],source_code:'test_fixture',source_id:'upload_recovery',effect:allowed?'allow':'deny'});
          }
          return response;
        };
        const wrap=(method,uploaded)=>options=>{
          if(!new URL(options.url,location.href).pathname.startsWith('/api/v1/visit-imports'))return method({...options,success:r=>options.success?.(authorize(r))});
          let cancelled=false;
          window.__uploadFixture({url:options.url,method:options.method||'GET',header:options.header,formData:options.formData,uploaded}).then(response=>{
            if(cancelled)return;
            if(response===null){method(options);return;}
            if(response.failure){options.fail?.({errMsg:response.failure});return;}
            options.success?.({...response,data:uploaded?JSON.stringify(response.data):response.data});options.complete?.(response);
          });
          return {abort(){cancelled=true;},onProgressUpdate(){}};
        };
        provider={...value,request:wrap(value.request.bind(value),false),uploadFile:wrap(value.uploadFile.bind(value),true)};
      }});
    },permissions);
  }
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
  await page.goto(`${base}/?mode=preview#/pages/visit-entry/index`);
  await page.getByRole('region',{name:'记录客户拜访',exact:true}).waitFor();
  await page.getByText('文件录入',{exact:true}).click();
  return {context,page,errors};
}
async function choose(page){const chooser=page.waitForEvent('filechooser');await page.getByRole('button',{name:'选择文件',exact:true}).click();await(await chooser).setFiles({name:'恢复验收.txt',mimeType:'text/plain',buffer:Buffer.from(content)});}
async function read(page,path){return page.evaluate(filePath=>new Promise(resolve=>SalesRuntime.wx.getFileSystemManager().readFile({filePath,encoding:'utf8',success:r=>resolve({text:r.data}),fail:r=>resolve({error:r.errMsg})})),path);}
async function removed(page,path){await page.waitForFunction(filePath=>new Promise(resolve=>SalesRuntime.wx.getFileSystemManager().readFile({filePath,encoding:'utf8',success:()=>resolve(false),fail:()=>resolve(true)})),path);}
async function state(page){return page.evaluate(()=>{const p=SalesRuntime.current,d=p.data;return {key:d.uploadIntentKey,state:d.uploadIntentState,path:d.localFilePath,id:d.importId,text:d.transcript,applied:p.appliedImportId||d.appliedImportId};});}
async function shot(page,name){if(!process.env.UPLOAD_SHOTS)return;fs.mkdirSync(process.env.UPLOAD_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.UPLOAD_SHOTS}/${name}.png`});}

test('ordinary preview visibly respects upload and transcription capability boundaries',{timeout:15000},async()=>{
  const c=await client(),{page}=c;
  try{
    assert.equal(await page.getByRole('button',{name:'选择文件',exact:true}).isDisabled(),true);
    await page.getByText('演示环境支持文字录入，文件上传请在正式工作区使用',{exact:true}).waitFor();
    await page.getByText('语音录入',{exact:true}).click();assert.equal(await page.getByRole('button',{name:'开始录音',exact:true}).isDisabled(),true);
    assert.deepEqual(c.errors,[]);await shot(page,'preview-capability');
  }finally{await c.context.close();}
});

test('synthetic upload permission + preview provider: not registered, retains original after reload, deletes only on remove',{timeout:30000},async()=>{
  const c=await client(()=>null),{page}=c;
  try{
    await choose(page);await page.getByText('未接收',{exact:true}).waitFor();
    const before=await state(page);assert.equal(before.state,'retryable_failure');assert.equal(before.id,'');assert.match(before.path,/^browser-saved:/);
    assert.deepEqual(await read(page,before.path),{text:content});
    await page.reload();await page.getByText('未接收',{exact:true}).waitFor();
    const after=await state(page);assert.equal(after.key,before.key);assert.equal(after.path,before.path);assert.equal(after.text,'');
    assert.deepEqual(await read(page,before.path),{text:content});
    await page.getByText('演示模式未接收文件，请保留原件。可移除附件后使用文字录入。',{exact:true}).waitFor();await shot(page,'preview-not-received');
    await page.getByRole('button',{name:'移除附件',exact:true}).click();
    await page.waitForFunction(()=>!SalesRuntime.current.data.localFilePath);
    await removed(page,before.path);assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});

test('synthetic receipt fixture: lost upload response → reload → same-key lookup, one upload and one text insertion',{timeout:30000},async()=>{
  const calls=[],id='8a6bc38d-c30b-41b2-92f8-d9dc2a4d4499';let key,filename;
  const c=await client(request=>{
    calls.push(request);const path=new URL(request.url,base).pathname;
    if(request.uploaded){key=request.header['Idempotency-Key'];filename=request.formData.original_filename;return {failure:'uploadFile:fail 测试丢失回执'};}
    if(path.includes('/requests/')){assert.equal(path.split('/').at(-1),key);return {statusCode:200,data:{upload_intent_id:key,state:'registered',id,status:'succeeded',filename,retryable:false}};}
    assert.equal(path,`/api/v1/visit-imports/${id}`);return {statusCode:200,data:{id,status:'succeeded',filename,extracted_text:content}};
  }),{page}=c;
  try{
    await choose(page);await page.getByText('待确认接收',{exact:true}).waitFor();
    const before=await state(page);assert.equal(before.key,key);assert.equal(before.state,'uncertain');assert.equal(before.id,'');
    assert.deepEqual(await read(page,before.path),{text:content});
    assert.equal(await page.getByRole('button',{name:'移除附件',exact:true}).isDisabled(),true);
    assert.equal(await page.getByRole('button',{name:'选择文件',exact:true}).isDisabled(),true);await shot(page,'receipt-missing');
    await page.reload();await page.getByText('已提取',{exact:true}).waitFor();
    await page.waitForFunction(text=>SalesRuntime.current.data.transcript===text,content);
    const after=await state(page);assert.equal(after.key,key);assert.equal(after.id,id);assert.equal(after.text,content);assert.equal(after.applied,id);assert.equal(after.path,'');
    await removed(page,before.path);
    await page.reload();await page.getByText('已提取',{exact:true}).waitFor();assert.equal((await state(page)).text,content);
    assert.equal(calls.filter(call=>call.uploaded).length,1);assert.equal(calls.filter(call=>call.url.includes('/requests/')).length,1);assert.deepEqual(c.errors,[]);await shot(page,'receipt-recovered');
  }finally{await c.context.close();}
});

test('synthetic separate permissions: upload can recover its receipt without retrying a registered failure',{timeout:30000},async()=>{
  const calls=[],id='8a6bc38d-c30b-41b2-92f8-d9dc2a4d4499';let key,filename;
  const c=await client(request=>{
    calls.push(request);const path=new URL(request.url,base).pathname;
    if(request.uploaded){key=request.header['Idempotency-Key'];filename=request.formData.original_filename;return {failure:'uploadFile:fail 测试丢失回执'};}
    if(path.includes('/requests/'))return {statusCode:200,data:{upload_intent_id:key,state:'registered',id,status:'failed',filename,retryable:false}};
    assert.equal(request.method,'GET');assert.equal(path,`/api/v1/visit-imports/${id}`);
    return {statusCode:200,data:{id,status:'failed',filename,error_message:'【测试】已登记任务处理失败'}};
  },{'visit.upload':true,'visit.retry_import':false,'visit.read':true}),{page}=c;
  try{
    await choose(page);await page.getByText('待确认接收',{exact:true}).waitFor();
    const button=page.locator('.ds-ve-file-actions button').first();assert.equal(await button.isDisabled(),false);
    await button.click();await page.getByText('【测试】已登记任务处理失败',{exact:true}).waitFor();
    assert.equal(await button.isDisabled(),true);assert.equal(calls.filter(call=>call.uploaded).length,1);assert.equal(calls.filter(call=>call.url.includes('/requests/')).length,1);
    assert.equal(calls.filter(call=>call.url.endsWith('/retry')).length,0);assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});

test('synthetic separate permissions: retry does not grant upload lookup; local explanations need neither',{timeout:20000},async()=>{
  const calls=[],c=await client(request=>{calls.push(request);return null;},{'visit.upload':false,'visit.retry_import':true,'visit.read':false}),{page}=c;
  try{
    await page.evaluate(()=>SalesRuntime.current.setData({fileName:'旧草稿.txt',localFilePath:'fixture-only-local-path',uploadIntentState:'uncertain',uploadIntentKey:'f3312a55-606a-4ef9-9fbc-6e7c8db33a68',importId:'',importStatus:'failed',isProcessing:false}));
    const button=page.locator('.ds-ve-file-actions button').first();await button.waitFor();assert.equal(await button.isDisabled(),true);
    await page.evaluate(()=>{SalesRuntime.app.globalData.session.permissions['visit.retry_import']=false;SalesRuntime.current.setData({canRetryImport:false});});
    for(const state of ['legacy_unknown','conflict']){
      await page.evaluate(state=>SalesRuntime.current.setData({uploadIntentState:state,errorText:''}),state);
      await page.getByRole('button',{name:'查看原因',exact:true}).waitFor();
      assert.equal(await button.isDisabled(),false);await button.click();await page.getByText(/请联系管理员核对/).waitFor();
    }
    assert.equal(calls.length,0);assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});

test('synthetic separate permissions: read-only task access can continue viewing without upload or retry',{timeout:15000},async()=>{
  const calls=[],id='8a6bc38d-c30b-41b2-92f8-d9dc2a4d4499';
  const c=await client(request=>{calls.push(request);assert.equal(request.uploaded,false);assert.equal(request.method,'GET');assert.equal(new URL(request.url,base).pathname,`/api/v1/visit-imports/${id}`);return {statusCode:200,data:{id,status:'succeeded',filename:'已登记.txt',extracted_text:content}};},{'visit.upload':false,'visit.retry_import':false,'visit.read':true}),{page}=c;
  try{
    await page.evaluate(id=>SalesRuntime.current.setData({fileName:'已登记.txt',localFilePath:'',uploadIntentState:'registered',importId:id,importStatus:'processing',isProcessing:false}),id);
    const button=page.locator('.ds-ve-file-actions button').first();await button.waitFor();assert.equal(await button.isDisabled(),false);await button.click();
    await page.waitForFunction(text=>SalesRuntime.current.data.transcript===text,content);assert.equal(calls.length,1);assert.deepEqual(c.errors,[]);
  }finally{await c.context.close();}
});
