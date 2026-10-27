/** Isolated IME regression: native Chromium composition, actual Web adapters, no business backend. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {build} from 'esbuild';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const evidence = resolve(process.env.IME_EVIDENCE || 'docs/ime-fix/evidence');
let server, browser, base;
const results = [];
before(async () => {
  await mkdir(evidence, {recursive:true});
  server = createSalesWebServer({previewOnly:true});
  await new Promise(r => server.listen(0,'127.0.0.1',r));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({channel:'chrome',headless:true});
});
after(async () => {
  await writeFile(resolve(evidence,'results.json'),JSON.stringify({time:new Date().toISOString(),browser:browser?.version(),environment:'isolated synthetic preview; Chromium native IME via CDP, not OS input-method UI',results},null,2));
  await browser?.close(); server?.closeAllConnections(); await new Promise(r=>server?.close(r));
});
const paint = p => p.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
async function fixture(name, run, {native=false, width=1440, role='manager'}={}) {
  const ctx=await browser.newContext({viewport:{width,height:1000},serviceWorkers:'block'}), errors=[],blocked=[];
  await ctx.addInitScript(({role,native})=>{
    sessionStorage.setItem('sales-web:preview-role',role);
    if(native)Object.defineProperty(window,'SalesDepartmentUI',{get:()=>undefined,set:()=>{},configurable:true});
  },{role,native});
  await ctx.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==base||/^\/api(?:\/|$)|^\/local-login/.test(u.pathname)){blocked.push(u.pathname);return r.abort();}return r.continue();});
  const p=await ctx.newPage();p.setDefaultTimeout(12000);p.on('pageerror',e=>errors.push(e.message));
  const row={name,native,width,status:'running'};
  try {
    await p.goto(base+'/?mode=preview#/pages/index/index');
    await p.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
    await run(p,ctx,row);
    assert.deepEqual(errors,[]);assert.deepEqual(blocked,[]);assert.deepEqual(await p.evaluate(()=>SalesRuntime.errors),[]);
    row.status='passed';row.screenshot=name+'.png';await p.screenshot({path:resolve(evidence,row.screenshot)});
  } catch(e){row.status='failed';row.error=e.message;await p.screenshot({path:resolve(evidence,name+'-failed.png')}).catch(()=>{});throw e;}
  finally{results.push(row);await ctx.close();}
}
async function go(p,route) {
  await p.evaluate(route=>SalesRuntime.wx.reLaunch({url:'/pages/'+route}),route);
  await p.waitForFunction(route=>SalesRuntime.current.route==='pages/'+route.split('?')[0],route);await paint(p);
}
async function compose(p,input,word='你好',syllables=['n','ni','nihao'],{refresh=true,enter=false}={}) {
  await input.focus(); const before=await input.inputValue();
  await input.evaluate(el=>{window.imeNode=el;window.imeEvents=[];for(const type of ['compositionstart','compositionend'])el.addEventListener(type,e=>imeEvents.push({type,data:e.data}));});
  const cdp=await p.context().newCDPSession(p);
  for(const s of syllables){
    await cdp.send('Input.imeSetComposition',{text:s,selectionStart:s.length,selectionEnd:s.length});
    if(refresh)await p.evaluate(()=>SalesRuntime.current.setData({imeUnrelatedStatus:Date.now()}));
    await paint(p);assert.equal(await input.inputValue(),before+s,'preedit must replace its previous syllable');
  }
  if(enter)await input.dispatchEvent('keydown',{key:'Enter',code:'Enter',keyCode:229,isComposing:true});
  await cdp.send('Input.insertText',{text:word});await paint(p);
  assert.equal(await input.inputValue(),before+word,'composition must commit only the Chinese candidate');
  assert.equal(await input.evaluate(el=>el===window.imeNode&&document.activeElement===el),true,'same input keeps focus');
  await cdp.detach();
}
for(const [name,route,selector,field] of [
 ['visit-note','visit-entry/index','textarea.sb-textarea','transcript'],
 ['visit-search','visit-entry/index','input[type=search]','customerQuery'],
 ['task-description','management-task-create/index','#department-task-description','description'],
 ['customer-search','customers/index','input[placeholder="搜索客户或负责人"]','customerPortfolioQuery'],
 ['claim-search','customer-claim/index','input[placeholder="客户名称 / 拼音 / 首字母"]','query'],
 ['customer-create','customer-create/index','#customer-create-customer_name','customer_name'],
 ['opportunity-search','workbench/index','input[placeholder="搜索商机或客户"]','opportunityWorkspaceQuery'],
])test(name+' supports composition during host refresh',()=>fixture(name,async(p,ctx,row)=>{
  // New PC list pages own their React state; assert the committed query sent to
  // the isolated API instead of the retired Mini Program search field.
  await p.evaluate(()=>{window.imeRequests=[];const api=SalesPreview;window.SalesPreview={...api,request(o){imeRequests.push({url:o.url,data:structuredClone(o.data)});return api.request(o);}};});
  await go(p,route);const input=p.locator(selector);await input.waitFor();await compose(p,input);
  if(field==='customerPortfolioQuery'||field==='opportunityWorkspaceQuery'){
    await p.waitForFunction(field=>imeRequests.some(r=>field==='customerPortfolioQuery'?new URL(r.url,location.href).pathname.endsWith('/customers/portfolio')&&new URL(r.url,location.href).searchParams.get('q')==='你好':new URL(r.url,location.href).pathname.endsWith('/opportunity-views/query')&&r.data?.config?.q==='你好'),field);
    row.entry=route;row.expected='你好';row.actual=await input.inputValue();return;
  }
  const saved=await p.evaluate(field=>field==='customer_name'?SalesRuntime.current.data.fields.find(f=>f.key===field)?.value:SalesRuntime.current.data[field],field);
  assert.equal(saved,'你好');row.entry=route;row.expected='你好';row.actual=saved;
}));

test('mixed text, cursor, deletion, draft recovery and external fill',()=>fixture('visit-edit-and-draft',async p=>{
 await go(p,'visit-entry/index');const input=p.locator('textarea.sb-textarea');await input.waitFor();await compose(p,input);
 await input.pressSequentially(' ABC123',{delay:5});await paint(p);assert.equal(await input.inputValue(),'你好 ABC123');
 await compose(p,input,'客户',['k','ke','kehu']);
 await input.evaluate(el=>el.setSelectionRange(1,1));await p.keyboard.insertText('上午');await paint(p);assert.equal(await input.inputValue(),'你上午好 ABC123客户');
 await input.press('Backspace');await paint(p);assert.equal(await input.inputValue(),'你上好 ABC123客户');
 const saved=await input.inputValue();await p.reload();await input.waitFor();await paint(p);assert.equal(await input.inputValue(),saved);
 await p.evaluate(()=>SalesRuntime.current.setData({transcript:'语音转写回填测试'}));await paint(p);assert.equal(await input.inputValue(),'语音转写回填测试');
 await p.getByRole('button',{name:'一键清空',exact:true}).click();await paint(p);assert.equal(await input.inputValue(),'');
 await p.getByRole('button',{name:'撤销',exact:true}).click();await paint(p);assert.equal(await input.inputValue(),'语音转写回填测试');
}));

test('person picker modal supports Chinese search and closes back to form',()=>fixture('person-picker',async p=>{
 await go(p,'management-task-create/index');await p.getByRole('button',{name:'选择任务负责人，可多选'}).click();
 const input=p.getByRole('textbox',{name:'搜索人员'});await input.waitFor();await compose(p,input,'王',['w','wa','wang']);
 assert.equal(await p.evaluate(()=>SalesRuntime.current.selectComponent('#personPicker').data.query),'王');
 await p.getByRole('dialog').getByRole('button',{name:/^返\s*回$/}).click();await p.getByRole('dialog').waitFor({state:'hidden'});await p.waitForFunction(()=>document.activeElement?.textContent?.includes('选择任务负责人'));
 await p.locator('#department-task-description').fill('保留输入的任务描述');await paint(p);
}));

test('native fallback preserves composition across unrelated renders',()=>fixture('native-visit-note',async p=>{
 await go(p,'visit-entry/index');const input=p.locator('textarea.note-input');await input.waitFor();await compose(p,input);
 assert.equal(await p.evaluate(()=>SalesRuntime.current.data.transcript),'你好');
},{native:true}));

test('narrow viewport keeps input and focus',()=>fixture('narrow-visit-note',async p=>{
 await go(p,'visit-entry/index');const input=p.locator('textarea.sb-textarea');await input.waitFor();await compose(p,input);
},{width:390}));

// Isolated component contract uses the same wrapper and the host's frame-delayed echo.
test('all input variants: one final commit, Enter, clearing, normalization, external values and maxLength',async()=>{
 const bundle=await build({stdin:{contents:`
 import React,{useState} from 'react';import{createRoot}from'react-dom/client';
 import{Input,SbTextarea,SbSearch,InputSyncContext}from'./department-ui/ImeInput.jsx';
 function App(){const [state,setState]=useState({value:'',revision:0});const [kind,setKind]=useState('input');
 window.probe={setKind,fill:value=>setState(s=>({value,revision:s.revision+1})),echo:()=>setState(s=>({...s,revision:s.revision+1}))};
 const commit=value=>{window.commits.push(value);requestAnimationFrame(()=>setState(s=>({value:window.normalize?value.replace(/[^0-9.]/g,''):value,revision:s.revision+1})));};
 const shared={value:state.value,maxLength:12,onPressEnter:()=>window.enters++,onSearch:()=>window.searches++,allowClear:true,'aria-label':'probe'};
 const C=({input:Input,password:Input.Password,textarea:Input.TextArea,search:Input.Search,sbtextarea:SbTextarea,sbsearch:SbSearch})[kind];
 return <InputSyncContext.Provider value={state.revision}><C key={kind} {...shared} onChange={kind.startsWith('sb')?commit:e=>commit(e.target.value)}/></InputSyncContext.Provider>;}
 window.commits=[];window.enters=0;window.searches=0;createRoot(document.getElementById('app')).render(<App/>);
 `,resolveDir:process.cwd(),loader:'jsx'},bundle:true,write:false,format:'iife',platform:'browser',define:{'process.env.NODE_ENV':'"production"'}});
 const ctx=await browser.newContext();const p=await ctx.newPage();await p.setContent('<div id="app"></div>');await p.addScriptTag({content:bundle.outputFiles[0].text});
 try{
 for(const kind of ['input','password','textarea','search','sbtextarea','sbsearch']){
  await p.evaluate(kind=>{probe.fill('');probe.setKind(kind);window.commits=[];window.enters=0;window.searches=0;},kind);await paint(p);
  const input=p.locator('input,textarea').filter({visible:true}).first();await input.focus();
  const cdp=await ctx.newCDPSession(p);
  for(const s of ['n','ni','nihao']){await cdp.send('Input.imeSetComposition',{text:s,selectionStart:s.length,selectionEnd:s.length});await p.evaluate(()=>probe.fill('异步回填'));await paint(p);assert.equal(await input.inputValue(),s,kind);}
  await input.dispatchEvent('keydown',{key:'Enter',keyCode:229,isComposing:true});
  assert.deepEqual(await p.evaluate(()=>({commits,enters,searches})),{commits:[],enters:0,searches:0},kind+' must not publish preedit or act on Enter');
  await cdp.send('Input.insertText',{text:'你好'});await paint(p);assert.equal(await input.inputValue(),'你好',kind);assert.deepEqual(await p.evaluate(()=>commits),['你好'],kind+' commits once');
  if(kind==='search'||kind==='sbsearch'){await input.press('Enter');assert.equal(await p.evaluate(()=>searches),1,kind+' normal Enter still searches');}
  await input.press('Tab');await paint(p);assert.equal(await input.inputValue(),'你好');
  await p.evaluate(()=>probe.fill('外部草稿'));await paint(p);assert.equal(await input.inputValue(),'外部草稿');
  if(kind!=='sbsearch'){await input.fill('1234567890123456');await paint(p);assert.equal((await input.inputValue()).length,12,kind+' maxlength');}
  if(kind!=='sbsearch'){
   await p.evaluate(()=>probe.fill('12345678901'));await paint(p);await input.focus();await input.press('End');
   await cdp.send('Input.imeSetComposition',{text:'nihao',selectionStart:5,selectionEnd:5});
   await cdp.send('Input.insertText',{text:'你好'});await paint(p);
   assert.equal((await input.inputValue()).length,12,kind+' Chinese candidate respects maxlength');
   assert.equal(await p.evaluate(()=>commits.at(-1)),await input.inputValue(),kind+' saved value matches bounded text');
  }
  await p.evaluate(()=>probe.fill(''));await paint(p);assert.equal(await input.inputValue(),'');
  if(kind==='input'){await p.evaluate(()=>window.normalize=true);await input.pressSequentially('a1b.2');await paint(p);assert.equal(await input.inputValue(),'1.2');await p.evaluate(()=>window.normalize=false);}
  await cdp.detach();
 }
 results.push({name:'component-contract-six-variants',status:'passed'});
 }finally{await ctx.close();}
});

const customerId='00000010-0000-4000-8000-000000000001';
const taskId='00000015-0000-4000-8000-000000000001';
test('customer edit contact and partner fields',()=>fixture('customer-edit',async p=>{
 await go(p,'customer-edit/index?customerId='+customerId);
 await p.getByRole('button',{name:'选择关联伙伴',exact:true}).click();
 const partner=p.getByRole('textbox',{name:'搜索伙伴名称',exact:true});await partner.waitFor();await compose(p,partner);
 await partner.press('Enter');
 await p.waitForFunction(()=>SalesRuntime.current.data.partnerPickerQuery==='你好'&&!SalesRuntime.current.data.partnerPickerLoading);
 await p.getByRole('dialog').locator('.ant-modal-close').click();await p.getByRole('dialog').waitFor({state:'hidden'});
 for(const placeholder of ['姓名','职位']){
  const input=p.getByPlaceholder(placeholder,{exact:true});await input.waitFor();await input.fill('');await paint(p);await compose(p,input);
 }
 assert.equal(await p.evaluate(()=>SalesRuntime.current.data.form.contact_name),'你好');
}));
test('opportunity form name and numeric normalization',()=>fixture('opportunity-create',async p=>{
 await go(p,'opportunity-create/index?customerId='+customerId);
 const name=p.getByPlaceholder('填写项目名，可用部门、场景区分');await name.waitFor();await compose(p,name);
 assert.equal(await p.evaluate(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.form.name),'你好');
 const amount=p.getByRole('textbox',{name:'ACV（万元）',exact:true});await amount.fill('123.456');await paint(p);assert.equal(await amount.inputValue(),'123.456');
}));
test('followup confirmation contact, partner and narrative fields',()=>fixture('visit-confirm',async p=>{
 await go(p,'visit-entry/index');await p.locator('.ds-ve-results button').first().click();// This isolated legacy demo actor omits the optional first-visit grant.
 await p.evaluate(()=>{const s=SalesRuntime.app.globalData.session;if(s.permissions)s.permissions['visit.first_visit']=true;else s.capabilities['visit.first_visit']=true;SalesRuntime.current.setData({canFirstVisit:true});});
 await p.locator('.ds-ve-first input').click();await p.waitForFunction(()=>SalesRuntime.current.data.isFirstVisit);await paint(p);
 await p.locator('textarea.sb-textarea').fill('沟通内容：双方核对试点验收清单，客户希望补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送给客户。\n跟进日期：2026-09-30\n对接人：合成联系人');
 await p.getByRole('button',{name:'提交结构化',exact:true}).click();
 await p.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.values.contact_name==='合成联系人');
 const contact=p.getByPlaceholder('填写本次沟通的客户对接人');await contact.fill('');await paint(p);await compose(p,contact);
 const areas=p.locator('.ds-vconfirm textarea.sb-textarea');
 const actualAreas=await areas.count()?areas:p.locator('textarea.sb-textarea');
 assert.ok(await actualAreas.count()>=2);
 for(let i=0;i<await actualAreas.count();i++){const input=actualAreas.nth(i);await input.fill('');await paint(p);await compose(p,input);}
 assert.equal(await p.evaluate(()=>SalesRuntime.current.data.values.contact_name),'你好');
 for(const key of ['follow_up_record','next_action','customer_main_business','customer_needs'])assert.equal(await p.evaluate(key=>SalesRuntime.current.data.values[key],key),'你好',key+' persisted');
},{role:'sales'}));
test('task coordination and cancellation dialog',()=>fixture('task-detail-inputs',async p=>{
 await go(p,'task-detail/index?id='+taskId);
 await p.getByRole('button',{name:'协调任务',exact:true}).click();
 const reason=p.getByPlaceholder('请说明转交原因');await reason.waitFor();await compose(p,reason);
 await p.getByRole('button',{name:'取消任务',exact:true}).click();
 const cancel=p.getByRole('textbox',{name:'取消原因'});await cancel.waitFor();await p.waitForFunction(()=>!document.getElementById('task-cancel-note').disabled);await compose(p,cancel);
 assert.equal(await p.evaluate(()=>SalesRuntime.current.selectComponent('task-cancel-dialog').data.note),'你好');
}));

test('login account allows Chinese without submitting credentials',async()=>{
 const loginServer=createSalesWebServer({target:''});await new Promise(r=>loginServer.listen(0,'127.0.0.1',r));
 const origin=`http://127.0.0.1:${loginServer.address().port}`;const ctx=await browser.newContext();const p=await ctx.newPage();p.setDefaultTimeout(12000);
 await ctx.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==origin)return r.abort();if(u.pathname.startsWith('/api/'))return r.fulfill({status:401,json:{detail:'isolated unauthenticated fixture'}});return r.continue();});
 try{await p.goto(origin+'/?mode=live#/pages/login/index');const input=p.getByPlaceholder('请输入账号或已绑定的手机号');await input.waitFor();await compose(p,input);
 assert.equal(await p.evaluate(()=>SalesRuntime.current.data.account),'你好');await p.screenshot({path:resolve(evidence,'login-account.png')});results.push({name:'login-account',status:'passed',screenshot:'login-account.png',environment:'no upstream configured; unauthenticated synthetic API'});
 }finally{await ctx.close();loginServer.closeAllConnections();await new Promise(r=>loginServer.close(r));}
});
