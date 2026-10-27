import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';

const source = fileURLToPath(new URL('../source/miniprogram/', import.meta.url));
const clone = value => JSON.parse(JSON.stringify(value));
const order = 'recent_follow_up';
function harness(api = {}, session = {workspaceId:'workspace',userId:'self',role:'sales'}) {
  let definition;
  const context = vm.createContext({getApp:()=>({globalData:{session}}),wx:{getStorageSync(){return '';},showToast(){}},
    Page:value=>{definition=value;},Component:value=>{definition=value;},console,setTimeout,clearTimeout});
  const modules = new Map();
  function load(relative) {
    const filename = path.resolve(source,relative.endsWith('.js')?relative:relative+'.js');
    if(filename===path.join(source,'utils/apiClient.js'))return api;
    if(modules.has(filename))return modules.get(filename).exports;
    const module={exports:{}};modules.set(filename,module);
    vm.runInContext(`(function(require,module,exports){${fs.readFileSync(filename,'utf8')}\n})`,context,{filename})(
      name=>load(path.relative(source,path.resolve(path.dirname(filename),name))),module,module.exports);
    return module.exports;
  }
  function instance(relative) {
    load(relative);
    return {...definition,...definition.methods,data:clone(definition.data),setData(patch){Object.assign(this.data,patch);}};
  }
  return {load,instance};
}

test('API order is opt-in and preserves unrelated directory defaults and filters',()=>{
  const code=fs.readFileSync(path.join(source,'utils/apiClient.js'),'utf8');
  const names=['listCustomers','listCustomerClaimPool','listOpportunityCustomerReferences','listOpportunities','assetQuery'];
  const functions=names.map(name=>{
    const match=code.match(new RegExp(`function ${name}\\([^]*?^}`, 'm'));
    assert.ok(match,`${name} exists`);return match[0];
  }).join('\n');
  const followUp=code.match(/^module\.exports\.listOpportunityFollowUpOptions=.*$/m)[0];
  const context={request:options=>options,module:{exports:{}}};
  vm.runInNewContext(`${functions}\n${followUp}\nthis.api={${names.slice(0,4).join(',')},...module.exports};`,context);
  for(const name of ['listCustomers','listCustomerClaimPool','listOpportunities','listOpportunityFollowUpOptions']) {
    assert.equal(new URLSearchParams(context.api[name]({}).path.split('?')[1]).has('order'),false,name);
    const query=new URLSearchParams(context.api[name]({order,q:'测试',query:'测试',scope:'mine',pageSize:20,offset:20}).path.split('?')[1]);
    assert.equal(query.get('order'),order,name);assert.equal(query.get('q'),'测试');
  }
  const reference=new URLSearchParams(context.api.listOpportunityCustomerReferences({q:'客户'}).path.split('?')[1]);
  assert.equal(reference.has('order'),false);assert.equal(reference.get('business_identity'),'customer');
  const filtered=new URLSearchParams(context.api.listCustomerClaimPool({order,claimStatus:'unclaimed',industry:'软件',offset:20}).path.split('?')[1]);
  assert.equal(filtered.get('claim_status'),'unclaimed');assert.equal(filtered.get('industry'),'软件');assert.equal(filtered.get('offset'),'20');
});

test('both visit customer pages retain authorization routing and server order',async()=>{
  const rows=[{id:'recent',name:'Z客户'},{id:'older',name:'A客户'}];
  const sessions=[
    [{role:'sales',permissions:{'visit.create':true,'customer.claim_directory':true},permissionGrants:[{permission_code:'visit.create',effect:'allow',scope_code:'assigned'}]},'listCustomers','mine'],
    [{role:'sales',permissions:{'customer.claim_directory':true}},'listCustomerClaimPool',undefined],
    [{role:'sales',permissions:{}},'listCustomers','company'],
  ];
  for(const [grant,expected,scope] of sessions)for(const pageName of ['visit-entry','visit-confirm']) {
    const calls=[],api=Object.fromEntries(['listCustomers','listCustomerClaimPool'].map(name=>[name,async params=>{calls.push([name,clone(params)]);return {items:rows};}]));
    const page=harness(api,{workspaceId:'workspace',userId:'self',...grant}).instance(`pages/${pageName}/index.js`);
    page.data.customerQuery=' 客户 ';
    await (pageName==='visit-entry'?page.searchDepartmentCustomers():page.searchCustomers());
    assert.equal(calls.length,1);assert.equal(calls[0][0],expected);assert.equal(calls[0][1].scope,scope);
    assert.equal(calls[0][1].order,order);assert.equal(calls[0][1].q,'客户');
    assert.deepEqual(clone((page.data.customerResults||page.data.customers).map(row=>row.id)),['recent','older']);
  }
});

function opportunityPages() {
  const row=id=>({id,customer_id:'customer',name:id==='recent'?'Z商机':'A商机'});
  return [{items:[row('recent'),row('older')],has_more:true,next_offset:20},
    {items:[row('older'),row('oldest')],has_more:false,next_offset:null},
    {items:[row('search')],has_more:false,next_offset:null}];
}
test('entry opportunity first page, append and search preserve backend order',async()=>{
  const calls=[],responses=opportunityPages(),api={listOpportunities:async params=>{calls.push(clone(params));return responses.shift();}};
  const picker=harness(api).instance('components/visit-opportunity-selector/index.js');
  picker.properties={customerId:'customer',selectedId:''};
  await picker.load();await picker.load(true);
  assert.deepEqual(clone(picker.data.items.map(row=>row.id)),['recent','older','oldest']);
  picker.data.query='搜索';await picker.load();
  assert.deepEqual(clone(picker.data.items.map(row=>row.id)),['search']);
  assert.deepEqual(calls.map(p=>[p.offset,p.query,p.order]),[[0,'',order],[20,'',order],[0,'搜索',order]]);
});

test('confirmation candidate paging, search and AI name lookup request recent follow-up',async()=>{
  const calls=[],responses=opportunityPages(),api={listOpportunities:async params=>{calls.push(clone(params));return responses.shift();}};
  const page=harness(api).instance('pages/visit-confirm/index.js');
  Object.assign(page.data,{customerId:'customer',customerConfirmed:true});page.refresh=()=>{};page.persist=()=>{};
  await page.loadOpportunities();await page.loadOpportunities({more:true});
  assert.deepEqual(clone(page.data.opportunityItems.map(row=>row.id)),['recent','older','oldest']);
  page.data.opportunityQuery='搜索';await page.loadOpportunities({search:true});
  assert.deepEqual(clone(page.data.opportunityItems.map(row=>row.id)),['search']);
  assert.deepEqual(calls.map(p=>[p.offset,p.query,p.order]),[[0,'',order],[20,'',order],[0,'搜索',order]]);
  page.aiOpportunitySuggestion={hasContent:true,name:'识别商机',stageIndex:-1};
  responses.push({items:[],has_more:false,next_offset:null},{items:[],has_more:false,next_offset:null});
  await page.loadOpportunities();
  assert.equal(calls.at(-1).query,'识别商机');assert.equal(calls.at(-1).order,order);
  assert.equal(page.data.opportunityError,'');
});

test('object follow-up discovery keeps recent order across pages and search',async()=>{
  const uuid=n=>`00000011-0000-4000-8000-${String(n).padStart(12,'0')}`;
  const row=n=>({contract_version:1,opportunity_id:uuid(n),name:n===1?'Z商机':'A商机',version_no:1,status:'open',stage_code:null,
    customer_reference:{id:uuid(99),name:'客户'},owner:{user_id:null,display_name:null,team_id:null,team_name:null},
    capabilities:{can_transfer:false,can_manage_collaborators:false,can_follow_up:true,can_edit:false},capability_reasons:{transfer:null}});
  const calls=[],responses=[{contract_version:1,items:Array.from({length:20},(_,i)=>row(i+1)),total:22,offset:0,limit:20},
    {contract_version:1,items:[row(20),row(21)],total:22,offset:20,limit:20},
    {contract_version:1,items:[row(3),row(2)],total:2,offset:0,limit:20}];
  const api={listOpportunityFollowUpOptions:async params=>{calls.push(clone(params));return responses.shift();}};
  const page=harness(api).instance('pages/visit-entry/index.js');
  await page.openFollowUpDiscovery();await page.searchFollowUp({currentTarget:{dataset:{more:true}}});
  assert.deepEqual(clone(page.data.followUpItems.map(r=>r.opportunity_id)),Array.from({length:21},(_,i)=>uuid(i+1)));
  page.inputFollowUpQuery({detail:{value:' 搜索 '}});await page.searchFollowUp();
  assert.deepEqual(clone(page.data.followUpItems.map(r=>r.opportunity_id)),[uuid(3),uuid(2)]);
  assert.deepEqual(calls.map(p=>[p.offset,p.q,p.order]),[[0,'',order],[20,'',order],[0,'搜索',order]]);
});
