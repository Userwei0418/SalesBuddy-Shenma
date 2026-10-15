/** UI acceptance against an explicit in-memory contract fixture, never a DB/API acceptance. */
import assert from 'node:assert/strict';
import {test, before, after} from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createSalesWebServer} from '../server.mjs';
const {chromium} = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
let browser, server, base;
before(async () => {server = createSalesWebServer({previewOnly: true, staticDir: path.resolve(fileURLToPath(new URL('../', import.meta.url)), process.env.SALES_WEB_DIST_DIR || 'dist')}); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); base = `http://127.0.0.1:${server.address().port}`; browser = await chromium.launch({channel: 'chrome', headless: true});});
after(async () => {await browser?.close(); if (server) await new Promise(resolve => {server.close(resolve); server.closeAllConnections();});});
const clone = value => JSON.parse(JSON.stringify(value));
const field = (key, label, type, extra = {}) => ({key, label, type, width: 160, nullable: true, editable: false, read_only: true, edit_mode: 'none', filter_ops: ['eq','is_empty','is_not_empty'], sortable: true, groupable: true, ...extra});
function fixture() {
  const options = {definition_version: 'opportunity_workspace_v2', fields: [field('name','商机名称','text',{width:240,nullable:false,editable:true,read_only:false,edit_mode:'inline',max_length:200,filter_ops:['contains','eq','is_empty']}),field('customer_name','客户名称','text'),field('amount','ACV','money',{groupable:false,filter_ops:['gte','between','is_empty']}),field('summary','一句话总结','text',{editable:true,read_only:false,edit_mode:'inline',max_length:500}),field('is_key','是否重点','boolean',{editable:true,read_only:false,edit_mode:'inline'}),field('owner','销售负责人','person'),field('stage','商机阶段','enum',{options:[{value:'qualified',label:'商机确认'},{value:'solution',label:'方案沟通'}]}),field('customer_source','客户来源','multi_enum',{editable:true,read_only:false,edit_mode:'inline',groupable:false,filter_ops:['has_any','has_all','is_empty'],options:[{value:'sales_outbound',label:'销售自拓'},{value:'company_lead',label:'公司线索'}]})],
    default_config:{schema_version:2,columns:[{key:'name',width:240,pinned:'left'},{key:'customer_name',width:180},{key:'amount',width:160},{key:'summary',width:240},{key:'is_key',width:100}],q:'',filters:[],sorts:[],group_by:null,scope:{kind:'authorized'},period:{kind:'current_quarter'},density:'comfortable',wrap:false},publish_scopes:['private'],teams:[],scope_options:{kinds:[{value:'self',label:'本人'},{value:'authorized',label:'授权范围'}],teams:[{id:'south',name:'南区'},{id:'east',name:'东区'}],owners:[{id:'a',name:'成员甲',team_ids:['south']},{id:'b',name:'成员乙',team_ids:['east']},{id:'c',name:'成员丙',team_ids:['south']}]}};
  const rows=Array.from({length:65},(_,i)=>({id:`row-${i}`,customer_id:`customer-${i}`,version_no:1,values:{name:`样例商机 ${String(i).padStart(2,'0')}`,customer_name:`客户 ${i}`,amount:i ? String(i * 10000) : null,summary:`原总结 ${i}`,is_key:false,customer_source:[],stage:i%2?'solution':'qualified'},capabilities:{editable_fields:i%2?[]:['name','summary','is_key','customer_source'],can_manage_fde_members:false}}));
  rows.forEach((row,i)=>Object.assign(row.values,{team:i%2?'east':'south',owner:i%2?'b':i%4?'c':'a',business_created_on:'2026-10-12',expected_close_date:i===1?null:'2026-12-31'}));
  let views=[], preference={default_view_id:null,effective_view_id:null,version_no:0,unavailable_reason:null};
  const people=[{id:'fde-a',name:'协作甲',is_active:true},{id:'fde-b',name:'协作乙',is_active:true}];
  rows[0].values.primary_fde_id=people[0];rows[0].values.fde_member_ids=people;rows[0].values.participants=[];rows[0].capabilities.can_manage_fde_members=true;
  const calls=[]; let denied=false;
  const handle = ({url,method='GET',data={},header={}}) => {
    const parsed=new URL(url,base),path=parsed.pathname.replace('/api/v1',''); calls.push({path,method,data:clone(data),header});
    const ok=data=>({statusCode:200,data:clone(data)}),fail=(statusCode,message)=>({statusCode,data:{code:statusCode===409?'VERSION_CONFLICT':'VALIDATION_ERROR',message}});
    if(path==='/opportunity-views/options')return ok(options);
    if(path==='/opportunity-views/preference') {
      if(method==='PUT') {if(!header['Idempotency-Key'])return fail(422,'需要提交标识');if(data.version_no!==preference.version_no)return fail(409,'默认方案已由其他端修改'); preference={default_view_id:data.default_view_id,effective_view_id:data.default_view_id,version_no:preference.version_no+1,unavailable_reason:null};}
      return ok(preference);
    }
    if(path==='/opportunity-views/query') {
      if(denied)return fail(403,'当前商机查看权限已撤销');
      assert.ok(data.limit<=100);assert.equal(data.config.schema_version,2);
      let items=rows.filter(row=>!data.config.q||row.values.name.includes(data.config.q));
      const scope=data.config.scope;
      const teamIds=scope.team_ids || (scope.team_id?[scope.team_id]:[]), ownerIds=scope.owner_ids || (scope.owner_id?[scope.owner_id]:[]);
      if(teamIds.length)items=items.filter(row=>teamIds.includes(row.values.team));
      if(ownerIds.length)items=items.filter(row=>ownerIds.includes(row.values.owner));
      const date=data.config.date_filter;if(date)items=items.filter(row=>row.values[date.field]&&row.values[date.field]>=date.start&&row.values[date.field]<=date.end);
      for(const filter of data.config.filters) {if(filter.operator==='contains')items=items.filter(row=>row.values[filter.field]?.includes(filter.value));else if(filter.operator==='is_empty') {assert.ok(!Object.hasOwn(filter,'value'));items=items.filter(row=>row.values[filter.field]==null);} }
      for(const sort of [...data.config.sorts].reverse())items.sort((a,b)=>(Number(a.values[sort.field])-Number(b.values[sort.field]))*(sort.direction==='asc'?1:-1));
      if(data.config.group_by) {items.sort((a,b)=>String(a.values[data.config.group_by]).localeCompare(String(b.values[data.config.group_by])));items=items.map(row=>({...row,group_key:row.values[data.config.group_by],group_label:row.values[data.config.group_by]}));}
      const offset=data.offset||0,next=offset+data.limit,total=items.length;
      return ok({items:items.slice(offset,next),total,has_more:next<total,next_offset:next<total?next:null,summary:{count:total,amount:null,amount_unknown_count:1,known_amount_subtotal:'20800000'},groups:[],resolved_period:{year:data.config.period.year || 2026,quarter:data.config.period.quarter || 4,timezone:'Asia/Shanghai'}});
    }
    if(path==='/opportunity-views') {
      if(method==='POST') {assert.ok(header['Idempotency-Key']);assert.equal(data.visibility,'private');assert.ok(!Object.hasOwn(data,'team_id'));const view={...clone(data),id:`view-${views.length+1}`,version_no:1,can_edit:true,can_delete:true};views.push(view);return ok(view);}
      return ok({items:views,total:views.length});
    }
    if(path.startsWith('/opportunity-views/')) {
      const view=views.find(row=>row.id===path.split('/').at(-1));if(!view)return fail(404,'方案不可用');
      if(data.version_no!==view.version_no)return fail(409,'方案已由其他端修改，请重新读取');
      if(method==='PUT') {Object.assign(view,clone(data),{version_no:view.version_no+1,migration_blocked:false,migration_notice:null});return ok(view);}
      if(method==='DELETE') {views=views.filter(row=>row!==view);if(preference.default_view_id===view.id)preference={...preference,effective_view_id:null,unavailable_reason:'unavailable'};return ok({ok:true});}
    }
    if(path==='/directory/opportunity-members')return ok({items:people,total:people.length,has_more:false,next_offset:null});
    const peopleMatch=path.match(/^\/opportunities\/(.+)\/people$/);
    if(peopleMatch){const row=rows.find(row=>row.id===peopleMatch[1]);assert.equal(method,'PUT');assert.ok(header['Idempotency-Key']);assert.ok(data.reason);if(data.version_no!==row.version_no)return fail(409,'人员已变化');
      assert.deepEqual(data.fde_member_ids,['fde-a','fde-b']);assert.equal(data.primary_fde_id,'fde-b');assert.equal(Object.hasOwn(data,'participants'),false);
      row.values.primary_fde_id=people[1];row.version_no++;return ok(row);}
    const match=path.match(/^\/opportunities\/(.+)\/fields$/);
    if(match) {const row=rows.find(row=>row.id===match[1]);if(method==='GET')return ok(row);assert.ok(header['Idempotency-Key']);if(data.version_no!==row.version_no)return fail(409,'商机已由其他端修改，请重新读取');assert.ok(data.reason);Object.assign(row.values,data.values);row.version_no++;return ok(row);}
    return fail(501,`未实现的测试接口 ${path}`);
  };
  return {handle,calls,rows,set denied(value){denied=value;},get views(){return views;},get preference(){return preference;}};
}
async function client(f, width=1366) {
  const context=await browser.newContext({viewport:{width,height:900},serviceWorkers:'block'});
  await context.exposeBinding('__workspaceFixture',(_,request)=>f.handle(request));
  await context.addInitScript(()=>{
    sessionStorage.setItem('sales-web:preview-role','sales');
    let provider;
    Object.defineProperty(window,'SalesPreview',{configurable:true,get:()=>provider,set(value){
      const original=value.request.bind(value);
      provider={...value, request:options=>{
        const path=new URL(options.url,location.href).pathname;
        if(!path.includes('/opportunity-views')&&!path.includes('/directory/opportunity-members')&&!/\/opportunities\/[^/]+\/(fields|people)$/.test(path))return original(options);
        let cancelled=false;
        window.__workspaceFixture({url:options.url,method:options.method,data:options.data,header:options.header}).then(response=>{if(cancelled)return;options.success?.(response);options.complete?.(response);});
        return {abort(){cancelled=true;}};
      }};
    }});
  });
  const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(10000);
  const go=async route=>{await page.goto(`${base}/?mode=preview#/pages/${route}/index`);try {await page.getByRole('combobox',{name:'选择看板方案'}).waitFor();} catch (e) {console.error({body:await page.locator('body').innerText(),errors,calls:f.calls});throw e;}await page.getByText(/本页 \d+ 条/).waitFor();};
  return {context,page,go,errors};
}
async function settled(page) {await page.waitForFunction(()=>!document.querySelector('.ant-spin-spinning,.ant-spin-blur')&&[...document.querySelectorAll('.ant-spin-container')].every(node=>Number(getComputedStyle(node).opacity)===1));}
async function select(page,label,option) {await page.getByRole('combobox',{name:label,exact:true}).click();await page.locator('.ant-select-dropdown:visible').getByText(option,{exact:true}).click();await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});}
async function revealFilter(page, label) {
  if (!await page.getByRole('combobox',{name:label,exact:true}).isVisible()) await page.getByRole('button',{name:/更多筛选/}).click();
}
async function chooseMany(page,label,options) {
  await revealFilter(page,label);
  await page.getByRole('combobox',{name:label,exact:true}).click();
  for(const option of options) await page.locator('.ant-select-dropdown:visible').getByText(option,{exact:true}).click();
  await page.keyboard.press('Escape');
}
async function waitRequest(f, predicate) {
  const until=Date.now()+4000;
  while(Date.now()<until) {const last=f.calls.filter(call=>call.path.endsWith('/query')).at(-1);if(last&&predicate(last.data))return last.data;await new Promise(resolve=>setTimeout(resolve,30));}
  assert.fail('expected query was not sent');
}

test('contract fixture: configure → save → board → independent browser restores preference and layout',{timeout:60000},async()=>{
  const f=fixture(),first=await client(f);const {page,go}=first;
  try {
    await go('workbench');await page.getByText('本页 20 条').waitFor();
    const before=f.calls.filter(call=>call.path.endsWith('/query')).length;
    await page.getByRole('separator',{name:'调整amount列宽'}).focus();await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(250);assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).length,before,'layout only does not requery');
    await page.getByRole('button',{name:'字段与布局',exact:true}).click();
    await page.getByRole('button',{name:'amount前移',exact:true}).click();
    await select(page,'分组字段','商机阶段');
    await page.getByLabel('长文本换行').check();
    if(process.env.WORKSPACE_SHOTS){fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await page.locator('.ant-drawer-body').evaluate(node=>node.scrollTo(0,0));await settled(page);await page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/layout.png`});}
    await page.getByRole('button',{name:'应用到当前表格'}).click();
    await page.getByRole('button',{name:'保存方案',exact:true}).click();await page.getByRole('textbox',{name:'方案名称'}).fill('周会商机');await page.getByRole('button',{name:/^保\s*存$/}).click();
    await page.getByText('方案已保存，可在商机看板选择').waitFor();assert.equal(f.views.length,1);assert.equal(f.views[0].config.columns[1].key,'amount');assert.equal(f.views[0].config.columns[1].width,176);assert.equal(f.views[0].config.group_by,'stage');
    await page.getByRole('button',{name:'更多 ▾',exact:true}).click();await page.getByRole('menuitem',{name:'设为默认看板'}).click();await page.getByText('已设为默认看板').waitFor();
    await page.getByRole('button',{name:'打开看板'}).click();await page.locator('.opp-workspace[data-mode="board"]').waitFor();await page.getByText('本页 20 条').waitFor();
    assert.equal(await page.getByRole('button',{name:'字段与布局',exact:true}).count(),0);
    assert.equal(await page.getByRole('separator').count(),0);
    assert.deepEqual(first.errors,[]);
    const second=await client(f,1440);
    try {await second.go('opportunities');await second.page.getByText('本页 20 条').waitFor();assert.match(await second.page.locator('.opp-scheme-bar').innerText(),/周会商机/);assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.config.group_by,'stage');assert.deepEqual(second.errors,[]);
      if(process.env.WORKSPACE_SHOTS){fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await settled(second.page);await second.page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/board.png`});}
    } finally {await second.context.close();}
  } finally {await first.context.close();}
});

test('contract fixture: row capability and conflict reread keep peer data read-only',{timeout:45000},async()=>{
  const f=fixture(),c=await client(f);const {page}=c;
  try {
    await c.go('workbench');await page.getByText('本页 20 条').waitFor();
    assert.equal(await page.getByRole('button',{name:'编辑样例商机 01的一句话总结'}).count(),0);
    await page.getByRole('button',{name:'编辑样例商机 00的一句话总结'}).click();
    await page.getByRole('textbox',{name:'一句话总结',exact:true}).fill('本端尚未保存');
    f.rows[0].version_no++;f.rows[0].values.summary='其他端更新';
    await page.getByRole('button',{name:/^保\s*存$/}).click();await page.getByText('商机已由其他端修改，请重新读取').waitFor();
    assert.equal(await page.getByRole('textbox',{name:'一句话总结',exact:true}).inputValue(),'本端尚未保存');assert.equal(f.rows[0].values.summary,'其他端更新');
    await page.getByRole('button',{name:'重新读取，放弃本次修改'}).click();await page.getByText('其他端更新',{exact:true}).waitFor();
    await page.getByText('本页 20 条').waitFor();await page.locator('.opp-table-footer .ant-pagination-item-2').click();await settled(page);
    assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.offset,20);
    await page.getByRole('button',{name:'刷新商机'}).click();await page.getByText('本页 20 条').waitFor();await settled(page);
    assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.offset,20);
    assert.deepEqual(c.errors,[]);
    if(process.env.WORKSPACE_SHOTS){fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await page.waitForFunction(()=>!document.querySelector('.ant-spin-spinning,.ant-spin-blur'));await page.getByRole('region',{name:'商机明细'}).evaluate(node=>node.scrollTo(0,0));await settled(page);await page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/editor.png`});}
  } finally {await c.context.close();}
});

 test('revoked query clears rows, metrics and the page cache instead of retaining prior data',{timeout:30000},async()=>{
 const f=fixture(),c=await client(f);try {
  await c.go('workbench');await c.page.getByText('本页 20 条').waitFor();f.denied=true;
  await c.page.getByRole('button',{name:'刷新商机'}).click();await c.page.getByText('当前商机查看权限已撤销').waitFor();
  assert.equal(await c.page.locator('.opp-name-link').count(),0);
  assert.deepEqual(await c.page.locator('.pc-kpi-value').allTextContents(),['—','—','—']);
  assert.equal(await c.page.evaluate(()=>SalesRuntime.current._opportunityWorkspace.result.items.length),0);
  assert.equal(await c.page.evaluate(()=>SalesRuntime.current._opportunityWorkspace.resultSignature),'');
  assert.deepEqual(c.errors,[]);
 } finally {await c.context.close();}
 });

 test('shared form and normal preview use one optional-field payload with no second PATCH',{timeout:45000},async()=>{
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
 await context.addInitScript(()=>sessionStorage.setItem('sales-web:preview-role','sales'));
 const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));page.setDefaultTimeout(12000);
 try {
  await page.goto(`${base}/?mode=preview#/pages/index/index`);await page.waitForFunction(()=>window.SalesRuntime?.app?.globalData?.session);
  await page.evaluate(()=>{
   const provider=SalesPreview;window.extraCalls=[];
   window.SalesPreview={...provider,request(options){extraCalls.push({path:new URL(options.url,location.href).pathname,method:options.method||'GET',data:structuredClone(options.data)});return provider.request(options);}};
  });
  await page.evaluate(()=>SalesRuntime.wx.navigateTo({url:'/pages/opportunity-create/index?customerId=00000010-0000-4000-8000-000000000001&opportunityId=00000011-0000-4000-8000-000000000001'}));
  await page.getByRole('button',{name:/更多信息/}).click();await page.getByRole('textbox',{name:'一句话总结',exact:true}).waitFor();
  await page.getByRole('textbox',{name:'一句话总结',exact:true}).fill('本次统一维护业务信息');
  await page.getByRole('checkbox',{name:'公司线索',exact:true}).click();
  await page.waitForFunction(()=>[...document.querySelectorAll('.ant-checkbox-wrapper')].some(label=>label.textContent==='公司线索'&&label.querySelector('input')?.checked));
  await select(page,'是否重点','非重点');await page.keyboard.press('Escape');await page.locator('.ant-select-dropdown:visible').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(()=>SalesRuntime.current.selectComponent('#opportunityForm').data.form.extra_values.is_key),false);
  if(process.env.WORKSPACE_SHOTS){fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await page.locator('.ds-oc-extras').evaluate(node=>node.scrollIntoView({block:'start'}));await settled(page);await page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/more-information.png`});}
  await page.getByRole('button',{name:'确认保存商机'}).click();
  await page.waitForFunction(()=>extraCalls.some(call=>call.method==='POST'&&/customers\/[^/]+\/opportunities$/.test(call.path)));
  const writes=await page.evaluate(()=>extraCalls.filter(call=>['POST','PUT','PATCH'].includes(call.method)&&call.path.includes('/opportunit')));
  assert.equal(writes.length,1);assert.equal(writes[0].data.fields_schema_version,2);assert.equal(writes[0].data.summary,'本次统一维护业务信息');assert.equal(writes[0].data.is_key,false);assert.deepEqual(writes[0].data.customer_source,['company_lead']);assert.equal(Object.hasOwn(writes[0].data,'participants'),false);
  await page.waitForFunction(()=>!SalesRuntime.current.data.busy);
  const saved=await page.evaluate(async()=>SalesRuntime.require('utils/apiClient').request({path:'/opportunities/00000011-0000-4000-8000-000000000001/fields',fresh:true}));
  assert.equal(saved.values.summary,'本次统一维护业务信息');assert.deepEqual(saved.values.customer_source,['company_lead']);
  assert.deepEqual(errors,[]);
 } catch(e) {console.error({body:await page.locator('body').innerText(),errors});throw e;} finally {await context.close();}
 });


test('switching primary FDE to an existing collaborator sends the complete roster atomically',{timeout:30000},async()=>{
 const f=fixture(),c=await client(f);try {
  await c.go('workbench');await c.page.getByRole('button',{name:'样例商机 00操作',exact:true}).click();await c.page.getByRole('menuitem',{name:'项目人员',exact:true}).click();
  await select(c.page,'主责FDE','协作乙');await c.page.getByRole('textbox',{name:'人员调整说明'}).fill('由现有协作人员担任主责');
  await c.page.getByRole('button',{name:'保存人员',exact:true}).click();await c.page.getByText('项目人员已更新').waitFor();
  const requests=f.calls.filter(call=>call.path.endsWith('/people'));assert.equal(requests.length,1);assert.equal(requests[0].data.version_no,1);
  assert.deepEqual(requests[0].data.fde_member_ids,['fde-a','fde-b']);assert.equal(f.rows[0].values.primary_fde_id.id,'fde-b');assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});


test('nonconvertible legacy view blocks querying until the user reapplies reviewed configuration',{timeout:30000},async()=>{
 const f=fixture();f.views.push({id:'old-view',name:'旧周会',visibility:'private',version_no:3,can_edit:true,can_delete:true,config:null,migration_blocked:true,migration_notice:{code:'migration_required',message:'旧方案范围无法等价转换，请重新选择条件'}});
 const c=await client(f);try{
  await c.page.goto(`${base}/?mode=preview#/pages/workbench/index?view=old-view`);
  await c.page.getByText('旧方案范围无法等价转换，请重新选择条件').waitFor();
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).length,0);assert.equal(await c.page.getByRole('button',{name:'保存方案',exact:true}).isDisabled(),true);
  assert.equal(await c.page.locator('.opp-name-link').count(),0);
  await c.page.getByRole('button',{name:'更多 ▾',exact:true}).click();
  assert.equal(await c.page.getByRole('menuitem',{name:'另存为新方案'}).getAttribute('aria-disabled'),'true');
  assert.equal(await c.page.getByRole('menuitem',{name:'设为默认看板'}).getAttribute('aria-disabled'),'true');
  await c.page.getByRole('button',{name:'更多 ▾',exact:true}).click();
  await c.page.getByRole('menuitem',{name:'另存为新方案'}).waitFor({state:'hidden'});
  await c.page.getByRole('button',{name:'编辑方案',exact:true}).click();await c.page.getByRole('button',{name:'应用到当前表格',exact:true}).click();
  await c.page.getByText('本页 20 条').waitFor();assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).length,1);
  await c.page.getByText('未保存调整',{exact:true}).waitFor();
  await c.page.getByRole('button',{name:'保存方案',exact:true}).click();await c.page.getByRole('button',{name:/^保\s*存$/}).click();await c.page.getByText('方案已保存，可在商机看板选择').waitFor();
  await c.page.getByRole('button',{name:'更多 ▾',exact:true}).click();assert.notEqual(await c.page.getByRole('menuitem',{name:'设为默认看板'}).getAttribute('aria-disabled'),'true');
  assert.equal(f.views[0].config.schema_version,2);assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});


test('pointer drag and resize plus configured filtering/sorting survive a saved scheme',{timeout:45000},async()=>{
 const f=fixture(),c=await client(f);const {page}=c;
 try{
  await c.go('workbench');await settled(page);
  const before=f.calls.filter(call=>call.path.endsWith('/query')).length;
  const resize=await page.getByRole('separator',{name:'调整amount列宽'}).boundingBox();
  await page.mouse.move(resize.x+4,resize.y+resize.height/2);await page.mouse.down();await page.mouse.move(resize.x+44,resize.y+resize.height/2,{steps:5});await page.mouse.up();
  await page.waitForFunction(()=>SalesRuntime.current._opportunityWorkspace.config.columns.find(column=>column.key==='amount').width===200);
  await page.locator('.ant-table-thead th').filter({hasText:'ACV'}).dragTo(page.locator('.ant-table-thead th').filter({hasText:'一句话总结'}));
  await page.waitForFunction(()=>SalesRuntime.current._opportunityWorkspace.config.columns[3].key==='amount');
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).length,before);
  await page.getByRole('button',{name:'字段与布局',exact:true}).click();await page.getByRole('button',{name:'添加条件',exact:true}).click();
  await page.getByRole('textbox',{name:'商机名称',exact:true}).fill('样例商机 2');
  await page.getByRole('button',{name:'添加排序',exact:true}).click();await select(page,'排序字段1','ACV');await select(page,'排序方向1','降序');
  await page.getByRole('button',{name:'应用到当前表格',exact:true}).click();await page.getByText('本页 10 条').waitFor();
  assert.deepEqual(await page.locator('.opp-name-link').allTextContents(),Array.from({length:10},(_,i)=>`样例商机 ${29-i}`));
  await page.getByRole('button',{name:'保存方案',exact:true}).click();await page.getByRole('textbox',{name:'方案名称'}).fill('拖动布局与条件');await page.getByRole('button',{name:/^保\s*存$/}).click();await page.getByText('方案已保存，可在商机看板选择').waitFor();
  assert.deepEqual(f.views[0].config.filters,[{field:'name',operator:'contains',value:'样例商机 2'}]);assert.deepEqual(f.views[0].config.sorts,[{field:'amount',direction:'desc'}]);assert.equal(f.views[0].config.columns[3].width,200);
  assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});

test('department/member unions and exact date field persist to board independently of financial quarter',{timeout:45000},async()=>{
 const f=fixture(),c=await client(f,1440),{page}=c;
 try {
  await c.go('workbench');
  await chooseMany(page,'部门',['南区','东区']);
  await chooseMany(page,'成员',['成员甲','成员乙']);
  await revealFilter(page,'商机日期字段');
  await select(page,'商机日期字段','预计关单日期');
  await page.getByPlaceholder('开始日期',{exact:true}).fill('2026-12-25');
  await page.getByPlaceholder('开始日期',{exact:true}).press('Tab');
  await page.getByPlaceholder('结束日期',{exact:true}).fill('2027-01-08');
  await page.getByPlaceholder('结束日期',{exact:true}).press('Enter');
  await waitRequest(f,data=>data.config.date_filter?.end==='2027-01-08');
  await select(page,'财务期间','指定季度');
  await select(page,'财务年份','2026年');
  await select(page,'财务季度','Q3');
  const last=await waitRequest(f,data=>data.config.period.quarter===3);await settled(page);
  assert.deepEqual(last.config.scope,{kind:'authorized',team_ids:['south','east'],owner_ids:['a','b']});
  assert.deepEqual(last.config.date_filter,{field:'expected_close_date',start:'2026-12-25',end:'2027-01-08'});
  assert.deepEqual(last.config.period,{kind:'fixed',year:2026,quarter:3});
  assert.ok(!last.config.scope.team_id&&!last.config.scope.owner_id);
  if (await page.getByRole('button',{name:'收起',exact:true}).isVisible()) await page.getByRole('button',{name:'收起',exact:true}).click();
  assert.match(await page.locator('.opp-filter-context').innerText(),/南区、东区.*成员甲、成员乙/);
  await page.getByRole('button',{name:'保存方案',exact:true}).click();await page.getByRole('textbox',{name:'方案名称'}).fill('跨部门跨年关单');await page.getByRole('button',{name:/^保\s*存$/}).click();
  await page.getByText('方案已保存，可在商机看板选择').waitFor();
  assert.deepEqual(f.views[0].config.date_filter,last.config.date_filter);
  await page.getByRole('button',{name:'打开看板'}).click();await page.locator('.opp-workspace[data-mode="board"]').waitFor();
  assert.match(await page.locator('.opp-board-context').innerText(),/预计关单日期：2026-12-25 — 2027-01-08/);
  assert.match(await page.locator('.opp-board-context').innerText(),/财务期间：2026 Q3/);
  assert.deepEqual(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.config.scope,last.config.scope);
  await page.getByRole('button',{name:'编辑方案',exact:true}).click();
  await page.locator('.opp-workspace[data-mode="editor"]').waitFor();
  await revealFilter(page,'商机日期字段');
  assert.equal(await page.getByPlaceholder('开始日期',{exact:true}).inputValue(),'2026-12-25');
  assert.equal(await page.getByPlaceholder('结束日期',{exact:true}).inputValue(),'2027-01-08');
  if(process.env.WORKSPACE_SHOTS) {fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/scope-date-finance.png`,fullPage:true});}
  assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});

for(const width of [1440,1024,600])test(`user page size, filter reset and full-height results at ${width}px`,{timeout:30000},async()=>{
 const f=fixture(),c=await client(f,width),{page}=c;
 try {
  await c.go('workbench');await page.getByText('本页 20 条').waitFor();
  const size=page.locator('.opp-table-footer').getByRole('combobox');await size.click();
  await page.locator('.ant-select-dropdown:visible').getByText(/50/,{exact:false}).click();
  await page.getByText('本页 50 条').waitFor();
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.limit,50);
  await page.locator('.opp-table-footer .ant-pagination-item-2').click();await page.getByText('本页 15 条').waitFor();
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.offset,50);
  await page.getByPlaceholder('搜索商机或客户').fill('样例商机 1');
  await page.getByText('本页 10 条').waitFor();
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.offset,0);
  assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.limit,50);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  const region=await page.getByRole('region',{name:'商机明细',exact:true}).evaluate(node=>({client:node.clientHeight,scroll:node.scrollHeight}));
  assert.ok(region.scroll<=region.client+1,'main table should expand, not trap vertical scrolling');
  if(process.env.WORKSPACE_SHOTS) {fs.mkdirSync(process.env.WORKSPACE_SHOTS,{recursive:true});await page.screenshot({path:`${process.env.WORKSPACE_SHOTS}/pagination-${width}.png`,fullPage:true});}
  await page.reload();await page.getByText('本页 50 条').waitFor();assert.equal(f.calls.filter(call=>call.path.endsWith('/query')).at(-1).data.limit,50);
  assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});


test('required opportunity name cannot clear in inline or drawer editing; optional summary still clears', {timeout:45000}, async()=>{
 const f=fixture(),c=await client(f);const {page}=c;
 const patches=()=>f.calls.filter(call=>call.method==='PATCH');
 try{
  await c.go('workbench');
  await page.getByRole('button',{name:'编辑样例商机 00的商机名称',exact:true}).click();
  await page.getByRole('textbox',{name:'商机名称',exact:true}).fill('   ');
  await page.getByRole('button',{name:/^保\s*存$/}).click();
  await page.getByRole('alert').filter({hasText:'请填写商机名称'}).waitFor();
  assert.equal(patches().length,0);assert.equal(f.rows[0].values.name,'样例商机 00');
  await page.getByRole('button',{name:/^取\s*消$/}).click();
  await page.getByRole('button',{name:'样例商机 00操作',exact:true}).click();await page.getByRole('menuitem',{name:'维护信息',exact:true}).click();
  const drawer=page.locator('.ant-drawer');
  await drawer.getByRole('textbox',{name:'商机名称',exact:true}).fill('');
  await drawer.getByRole('textbox',{name:'修改说明',exact:true}).fill('合成维护校验');
  await drawer.getByRole('button',{name:'保存修改',exact:true}).click();
  await drawer.getByRole('alert').filter({hasText:'请填写商机名称'}).waitFor();assert.equal(patches().length,0);
  await drawer.getByRole('textbox',{name:'商机名称',exact:true}).fill('合成修正名称');
  await drawer.getByRole('textbox',{name:'一句话总结',exact:true}).fill('');
  await drawer.getByRole('button',{name:'保存修改',exact:true}).click();await drawer.waitFor({state:'hidden'});
  assert.equal(patches().length,1);assert.deepEqual(patches()[0].data.values,{name:'合成修正名称',summary:null});
  assert.equal(f.rows[0].values.name,'合成修正名称');assert.equal(f.rows[0].values.summary,null);assert.deepEqual(c.errors,[]);
 }finally{await c.context.close();}
});
