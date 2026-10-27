import assert from 'node:assert/strict';
import {test,before,after} from 'node:test';
import fs from 'node:fs';
import {createSalesWebServer} from '../server.mjs';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const customer='00000010-0000-4000-8000-000000000001',opportunity='00000011-0000-4000-8000-000000000001';
let server,browser,base;
before(async()=>{server=createSalesWebServer({previewOnly:true});await new Promise(r=>server.listen(0,'127.0.0.1',r));base=`http://127.0.0.1:${server.address().port}`;browser=await chromium.launch({channel:'chrome',headless:true});});
after(async()=>{await browser?.close();await new Promise(r=>{server.close(r);server.closeAllConnections();});});
async function fixture(run,manage=true,transfer=false){
  const context=await browser.newContext({viewport:{width:1440,height:900},serviceWorkers:'block'}),page=await context.newPage(),errors=[];
  await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
  await context.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
  page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(12000);
  try{
    await page.goto(`${base}/?mode=preview`);await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session&&!SalesRuntime.app._capabilityFlight);
    await page.evaluate(({customer,opportunity,manage,transfer})=>{
      const original=SalesPreview.request.bind(SalesPreview);
      const member={user_id:'00000001-0000-4000-8000-000000000005',display_name:'合成协作成员',teams:[{team_id:'00000030-0000-4000-8000-000000000001',name:'合成南区'},{team_id:'00000030-0000-4000-8000-000000000002',name:'合成北区'}]};
      window.collaborationProbe={calls:[],mode:'normal',record:{contract_version:1,opportunity_id:opportunity,name:'合成商机',version_no:1,owner:{user_id:'00000001-0000-4000-8000-000000000001',display_name:'合成主责',team_id:null,team_name:'合成团队'},customer_reference:{id:customer,name:'合成客户'},collaborators:[],capabilities:{can_transfer:transfer,can_manage_collaborators:manage,can_follow_up:true,can_edit:false},capability_reasons:{transfer:'TRANSFER_NOT_READY'}}};
      window.SalesPreview={...SalesPreview,request(options){
        const path=new URL(options.url,location.href).pathname,probe=collaborationProbe;
        if(/\/opportunities\/[^/]+\/(access|access-candidates|collaborators|transfer)$/.test(path)){
          probe.calls.push({path,url:options.url,body:structuredClone(options.data),header:structuredClone(options.header)});
          let statusCode=200,data;
          if(path.endsWith('/access')) data=probe.record;
          else if(path.endsWith('/access-candidates')) data={items:[member],total:1};
          else if(probe.mode==='conflict') {probe.mode='normal';probe.record.version_no++;statusCode=409;data={code:'VERSION_CONFLICT',detail:'合成版本冲突'};}
          else if(probe.mode==='denied'){statusCode=403;data={code:'OPPORTUNITY_ACCESS_DENIED',detail:'合成撤权'};}
          else if(path.endsWith('/transfer')){probe.record.version_no++;probe.record.owner={user_id:member.user_id,display_name:member.display_name,team_id:options.data.new_owner_team_id,team_name:member.teams.find(t=>t.team_id===options.data.new_owner_team_id).name};data={changed:true,event_id:'00000020-0000-4000-8000-000000000001',version_no:probe.record.version_no,owner:probe.record.owner,collaborators:[],sync_status:'pending'};}
          else {probe.record.version_no++;probe.record.collaborators=options.data.member_ids.length?[member]:[];data={changed:true,event_id:'00000020-0000-4000-8000-000000000001',version_no:probe.record.version_no,collaborators:probe.record.collaborators};}
          queueMicrotask(()=>options.success({statusCode,data:structuredClone(data)}));return {abort(){}};
        }
        return original(options);
      }};
      SalesRuntime.wx.navigateTo({url:`/pages/customer-assets/index?customer_id=${customer}&opportunity_id=${opportunity}&period=all&readonly=1`});
    },{customer,opportunity,manage,transfer});
    await page.getByRole('button',{name:transfer?'转交商机主责':'商机协作',exact:true}).click();await page.getByText('合成主责 · 合成团队',{exact:true}).waitFor();
    await run(page);assert.deepEqual(errors,[]);
  }finally{await context.close();}
}
async function choose(page){await page.getByRole('button',{name:'查看可添加成员',exact:true}).click();await page.getByRole('checkbox',{name:'合成协作成员',exact:true}).check();await page.getByText('本次选择（1/30）',{exact:true}).waitFor();}
test('owner opens named collaborative access, searches, saves and reopens same persisted selection',()=>fixture(async page=>{
  await choose(page);
  if(process.env.ACCESS_SHOTS){fs.mkdirSync(process.env.ACCESS_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.ACCESS_SHOTS}/collaboration-selected.png`});}
  await page.getByRole('button',{name:'保存协作名单',exact:true}).click();await page.getByText('协作名单已保存',{exact:true}).waitFor();
  await page.locator('.ant-drawer-footer').getByRole('button',{name:/^关\s*闭$/}).click();
  await page.getByRole('button',{name:'商机协作',exact:true}).click();await page.getByText('本次选择（1/30）',{exact:true}).waitFor();
  const writes=await page.evaluate(()=>collaborationProbe.calls.filter(r=>r.path.endsWith('/collaborators')));assert.equal(writes.length,1);assert.equal(writes[0].body.version_no,1);assert.match(writes[0].header['Idempotency-Key'],/^[a-f\d-]{36}$/);
}));
test('read-only collaborator sees current names without manager controls, no candidate/write calls',()=>fixture(async page=>{
  await page.getByText('当前账号无权维护名单，由商机主责管理。',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'保存协作名单',exact:true}).count(),0);
  assert.equal(await page.evaluate(()=>collaborationProbe.calls.filter(r=>!r.path.endsWith('/access')).length),0);
},false));
test('conflict requires reread and confirmation; revoked write preserves selected names and blocks save',()=>fixture(async page=>{
  await choose(page);await page.evaluate(()=>{collaborationProbe.mode='conflict';});
  await page.getByRole('button',{name:'保存协作名单',exact:true}).click();await page.getByRole('button',{name:'已核对最新名单',exact:true}).waitFor();
  assert.equal(await page.evaluate(()=>collaborationProbe.calls.filter(r=>r.path.endsWith('/collaborators')).length),1);
  await page.getByRole('button',{name:'已核对最新名单',exact:true}).click();await page.evaluate(()=>{collaborationProbe.mode='denied';});
  await page.getByRole('button',{name:'保存协作名单',exact:true}).click();await page.getByText('协作管理权限已变化，本次选择未提交。请重新读取商机核对。',{exact:true}).waitFor();
  await page.getByText('本次选择（1/30）',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'保存协作名单',exact:true}).count(),0);
  const bodies=await page.evaluate(()=>collaborationProbe.calls.filter(r=>r.path.endsWith('/collaborators')).map(r=>r.body));assert.deepEqual(bodies.map(b=>b.version_no),[1,2]);
}));

test('transfer requires explicit sales and team, uses its own capability/purpose, and reads back success',()=>fixture(async page=>{
 await page.getByRole('button',{name:'查找销售',exact:true}).click();await page.getByRole('button',{name:'合成协作成员',exact:true}).click();await page.getByRole('textbox',{name:'转交原因',exact:true}).fill('按区域职责调整商机主责');await page.getByRole('button',{name:'确认转交',exact:true}).click();await page.getByText('请选择接手销售、明确所属团队，并填写 1–1000 字转交原因。',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>collaborationProbe.calls.filter(r=>r.path.endsWith('/transfer')).length),0);
 await page.getByRole('combobox',{name:'接手团队',exact:true}).click();await page.locator('.ant-select-item-option').filter({hasText:'合成北区'}).click();if(process.env.ACCESS_SHOTS){fs.mkdirSync(process.env.ACCESS_SHOTS,{recursive:true});await page.screenshot({path:process.env.ACCESS_SHOTS+'/transfer-confirmation.png'});}await page.getByRole('button',{name:'确认转交',exact:true}).click();await page.getByText('商机主责已转交',{exact:true}).waitFor();await page.getByText('同步请求已排队，尚未确认外部系统更新',{exact:true}).waitFor();
 const calls=await page.evaluate(()=>collaborationProbe.calls),write=calls.find(r=>r.path.endsWith('/transfer'));assert.deepEqual(write.body,{version_no:1,new_owner_user_ref_id:'00000001-0000-4000-8000-000000000005',new_owner_team_id:'00000030-0000-4000-8000-000000000002',reason:'按区域职责调整商机主责'});assert.match(write.header['Idempotency-Key'],/^[a-f\d-]{36}$/);assert.ok(calls.find(r=>r.url.includes('purpose=transfer')));assert.ok(calls.at(-1).path.endsWith('/access'));
},false,true));
test('transfer version conflict preserves target and reason but requires explicit acknowledgment before retry',()=>fixture(async page=>{
 await page.getByRole('button',{name:'查找销售',exact:true}).click();await page.getByRole('button',{name:'合成协作成员',exact:true}).click();await page.getByRole('combobox',{name:'接手团队',exact:true}).click();await page.locator('.ant-select-item-option').filter({hasText:'合成南区'}).click();await page.getByRole('textbox',{name:'转交原因',exact:true}).fill('保持这段交接依据');await page.evaluate(()=>{collaborationProbe.mode='conflict';});await page.getByRole('button',{name:'确认转交',exact:true}).click();await page.getByRole('button',{name:'已核对最新主责',exact:true}).waitFor();assert.equal(await page.getByRole('textbox',{name:'转交原因',exact:true}).inputValue(),'保持这段交接依据');assert.equal(await page.evaluate(()=>collaborationProbe.calls.filter(c=>c.path.endsWith('/transfer')).length),1);
 await page.getByRole('button',{name:'已核对最新主责',exact:true}).click();await page.getByRole('button',{name:'确认转交',exact:true}).click();await page.getByText('商机主责已转交',{exact:true}).waitFor();assert.deepEqual(await page.evaluate(()=>collaborationProbe.calls.filter(c=>c.path.endsWith('/transfer')).map(c=>c.body.version_no)),[1,2]);
},true,true));
