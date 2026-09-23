const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const load = () => import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/customer-profile.js')).href);

test('customer profile shows business facts, zero years, primary contact and read-only links safely', async () => {
  const {customerProfileDetails,customerProfileForm} = await load();
  const row = {name:'测试客户',cooperation_years:0,main_business:'<script>业务</script>',
    customer_budget:'预算待确认',demand_summary:'项目需求',next_action:'拜访',contact_name:'联系人',
    customer_code:'C-1',external_customer_id:'CRM-1',company_reference:'APPROVED-1'};
  const detail = customerProfileDetails(row);
  for (const text of ['0 年','项目需求','联系人','C-1','CRM-1','APPROVED-1']) assert.ok(detail.includes(text));
  assert.ok(detail.includes('&lt;script&gt;')); assert.ok(!detail.includes('<script>'));
  const form = customerProfileForm(row);
  assert.match(form,/value="CRM-1"[^>]*disabled/);
  assert.match(form,/name="main_business"/);
});

test('profile edits send only changed whitelist fields and distinguish blank from zero without default overwrites', async () => {
  const {customerProfileChanges} = await load();
  const row = {version_no:7,name:'原名称',industry_code:'软件',cooperation_years:null,customer_type_code:'历史分类'};
  assert.deepEqual(customerProfileChanges(new Map([
    ['name','原名称'],['industry',''],['cooperation_years','0'],['customer_type','历史分类'],
    ['customer_code','不能修改'],['workspace_id','不能切换'],
  ]),row),{version_no:7,industry:'',cooperation_years:0});
  assert.deepEqual(customerProfileChanges(new Map([['cooperation_years','']]),{...row,cooperation_years:0}),
    {version_no:7,cooperation_years:null});
  assert.deepEqual(customerProfileChanges(new Map([['name','原名称']]),row),{version_no:7});
});

test('edit loads a fresh profile and discards a late response after navigation', async () => {
  const profile = await load();
  const core = await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
  core.clearSession();
  Object.assign(core.state,{actor:{workspace_id:'w',user_id:'ops',role:'administrator'},token:'fixture',page:'customers'});
  const dialog = {innerHTML:'another dialog'};
  global.document = {querySelector:()=>dialog};
  let answer, requested;
  global.fetch = url => {requested=url;return new Promise(resolve=>{answer=resolve;});};
  const editing = profile.editCustomerProfile('c');
  assert.match(requested,/customers\/c\/overview$/);
  core.state.page='accounts';
  answer({ok:true,status:200,json:async()=>({name:'迟到档案',version_no:4})});
  await editing;
  assert.equal(dialog.innerHTML,'another dialog');
});


test('editing from a detail tolerates history updates, but cannot reopen a dismissed dialog', async () => {
  const profile = await load();
  const core = await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
  core.clearSession();
  Object.assign(core.state,{actor:{workspace_id:'w',user_id:'ops',role:'administrator'},token:'fixture',page:'customers'});
  const form = {addEventListener(){}};
  const d = {innerHTML:'detail and loading history',open:true,querySelector:()=>form,querySelectorAll:()=>[],showModal(){this.open=true;}};
  global.document = {querySelector:selector=>selector==='#dialog-form'?form:d};
  let respond;
  global.fetch = () => new Promise(resolve=>{respond=resolve;});
  const opening = profile.editCustomerProfile('c');
  d.innerHTML='detail and loaded history';
  respond({ok:true,status:200,json:async()=>({name:'当前客户',version_no:3})});
  await opening;
  assert.match(d.innerHTML,/编辑客户档案 · 当前客户/);
  const second = profile.editCustomerProfile('c');
  d.open=false; d.innerHTML='已关闭';
  respond({ok:true,status:200,json:async()=>({name:'迟到客户',version_no:3})});
  await second;
  assert.equal(d.open,false);assert.equal(d.innerHTML,'已关闭');
});
