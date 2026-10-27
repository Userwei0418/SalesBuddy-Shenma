import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const customer='00000010-0000-4000-8000-000000000001',opportunity='00000011-0000-4000-8000-000000000001';
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>{server.close(r);server.closeAllConnections();});});
const today=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
const transcript=`对接人：合成联系人\n沟通内容：本次与客户确认试点需求，客户同意安排测试并核对验收范围。\n下一步计划：${today}由我整理并发送测试方案给客户确认。\n跟进日期：${today}\n创建时间：${today}`;
async function fixture(run){
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'}),page=await context.newPage(),errors=[];
 await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
 await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
 page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
 try{
  await page.goto(`${base}/?mode=preview`);await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
  await page.evaluate(({customer,opportunity})=>{
   const original=SalesPreview.request.bind(SalesPreview);
   window.followUpProbe={calls:[],deny:false,listDenied:false,record:{contract_version:1,opportunity_id:opportunity,name:'合成协作商机',version_no:1,status:'open',stage_code:null,owner:{user_id:null,display_name:null,team_id:null,team_name:null},customer_reference:{id:customer,name:'仅名称引用客户'},collaborators:[],capabilities:{can_transfer:false,can_manage_collaborators:false,can_follow_up:true,can_edit:false},capability_reasons:{transfer:'TRANSFER_NOT_READY'}}};
   window.SalesPreview={...SalesPreview,request(options){
    const url=new URL(options.url,location.href),path=url.pathname.replace(/^\/api\/v1/,''),probe=followUpProbe;
    probe.calls.push({path,url:options.url,body:structuredClone(options.data)});
    let statusCode=200,data;
    if(path==='/opportunities/follow-up-options'){
     if(probe.listDenied){statusCode=403;data={detail:'合成目录拒绝'};}
     else{const offset=Number(url.searchParams.get('offset')||0),q=url.searchParams.get('q');const items=q?[probe.record]:offset===0?Array.from({length:20},(_,i)=>({...probe.record,opportunity_id:i?`00000011-0000-4000-8000-${String(i+1).padStart(12,'0')}`:opportunity,name:i?`分页商机${i+1}`:probe.record.name})):[{...probe.record,opportunity_id:'00000011-0000-4000-8000-000000000020',name:'分页商机20'},{...probe.record,opportunity_id:'00000011-0000-4000-8000-000000000021',name:'分页商机21'}];data={contract_version:1,items,total:q?1:22,offset,limit:20};}
    }else if(path.endsWith('/access')){data={...probe.record,capabilities:{...probe.record.capabilities,can_follow_up:!probe.deny}};}
    else if(/^\/customers(?:\/|$)/.test(path)||path==='/opportunities'){statusCode=403;data={detail:'合成：无完整客户或普通商机目录权限'};}
    else return original(options);
    queueMicrotask(()=>options.success({statusCode,data:structuredClone(data)}));return{abort(){}};
   }};
   SalesRuntime.wx.navigateTo({url:'/pages/visit-entry/index'});
  },{customer,opportunity});
  await page.getByRole('button',{name:'按商机选择（含协作商机）',exact:true}).click();await page.getByText('合成协作商机',{exact:true}).waitFor();
  await run(page);assert.deepEqual(errors,[]);
 }finally{await context.close();}
}
async function select(page){await page.getByText('合成协作商机',{exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.followUpOnly&&SalesRuntime.current.data.opportunityResolved&&!SalesRuntime.current.data.followUpPickerOpen);}
const notes=page=>page.getByPlaceholder('可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……');
test('object follow-up discovery paginates, deduplicates and searches without full customer access',()=>fixture(async page=>{
 await page.getByRole('button',{name:'加载更多商机',exact:true}).click();await page.getByText('分页商机21',{exact:true}).waitFor();assert.equal(await page.getByText('分页商机20',{exact:true}).count(),1);assert.equal(await page.getByRole('button',{name:'加载更多商机',exact:true}).count(),0);
 await page.getByPlaceholder('搜索商机或客户名称').fill('协作');await page.getByPlaceholder('搜索商机或客户名称').press('Enter');await page.getByText('合成协作商机',{exact:true}).waitFor();
 assert.ok((await page.evaluate(()=>followUpProbe.calls)).some(c=>c.url.includes('q=%E5%8D%8F%E4%BD%9C')));
 await select(page);await page.locator('.ds-ve-results').waitFor({state:'detached'});await page.getByRole('dialog').waitFor({state:'hidden'});assert.equal(await page.locator('.ds-fvo-list').count(),0);
 if(process.env.FOLLOWUP_SHOTS){fs.mkdirSync(process.env.FOLLOWUP_SHOTS,{recursive:true});await page.screenshot({path:process.env.FOLLOWUP_SHOTS+'/selected.png',animations:'disabled'});}
}));
test('minimal reference enters structure, quality and archive with fresh object access and no full directory reads',{timeout:50000},()=>fixture(async page=>{
 await select(page);await page.evaluate(()=>{followUpProbe.calls=[];});await notes(page).fill(transcript);await page.getByRole('button',{name:'提交结构化',exact:true}).click();
 await page.waitForFunction(()=>SalesRuntime.current.route==='pages/visit-confirm/index'&&SalesRuntime.current.data.followUpOnly&&SalesRuntime.current.data.opportunityResolved);
 assert.equal(await page.locator('.ds-vc-picker').count(),0);assert.equal(await page.locator('.ds-vc-inline-form').count(),0);
 await page.getByRole('button',{name:'下一步 · AI 质检',exact:true}).click();await page.waitForFunction(()=>SalesRuntime.current.data.flowStep==='result'&&SalesRuntime.current.data.canSubmit);
 await page.getByRole('button',{name:'确认保存',exact:true}).click();
 const dialog=page.getByRole('dialog');await dialog.getByRole('button',{name:/确/}).last().click();
 await page.waitForFunction(()=>SalesRuntime.current.data.archived);
 const calls=await page.evaluate(()=>followUpProbe.calls);assert.ok(calls.filter(c=>c.path.endsWith('/access')).length>=4);assert.equal(calls.filter(c=>/^\/customers(?:\/|$)/.test(c.path)||c.path==='/opportunities').length,0);
 for(const c of calls.filter(c=>['/visit-flow/structure','/visit-flow/quality'].includes(c.path))){assert.equal(c.body.opportunity_id,opportunity);assert.equal(c.body.customer_id,customer);assert.equal(c.body.opportunity_mutation==null,true);}
}));
test('revoked object access blocks structure and preserves original transcript',()=>fixture(async page=>{
 await select(page);await notes(page).fill(transcript);await page.evaluate(()=>{followUpProbe.deny=true;followUpProbe.calls=[];});await page.getByRole('button',{name:'提交结构化',exact:true}).click();
 await page.getByText('当前已无权跟进该商机，请保留草稿并重新选择',{exact:true}).first().waitFor();assert.equal(await notes(page).inputValue(),transcript);assert.equal(await page.evaluate(()=>SalesRuntime.current.data.canSubmit),false);assert.equal(await page.evaluate(()=>followUpProbe.calls.filter(c=>c.path==='/visit-flow/structure').length),0);
}));

test('follow-up directory denial is shown without falling back to a broader directory',()=>fixture(async page=>{
 await page.evaluate(()=>{followUpProbe.listDenied=true;followUpProbe.calls=[];});await page.getByPlaceholder('搜索商机或客户名称').fill('未知');await page.getByPlaceholder('搜索商机或客户名称').press('Enter');await page.getByText('合成目录拒绝',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>followUpProbe.calls.some(c=>/^\/customers(?:\/|$)/.test(c.path)||c.path==='/opportunities')),false);assert.equal(await page.getByText('合成协作商机',{exact:true}).count(),0);
}));
