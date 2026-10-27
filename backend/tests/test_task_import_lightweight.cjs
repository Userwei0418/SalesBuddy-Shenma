const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const load = () => import(pathToFileURL(path.resolve(__dirname, '../src/sales_backend/web/assets/task-imports.js')).href);
const item = {item_id:'10000000-0000-0000-0000-000000000001',description:'确认客户回访计划',assignee_account_code:'USER0',due_at:'2027-01-01T09:00:00+08:00',priority_code:'normal',association_kind:'daily'};
const people = [{account_code:'USER0',name:'合成执行人'}];
test('admin confirms V3 without legacy requirements and never auto assigns a hinted person', async()=>{
  const {confirmationItems} = await load();
  const [payload] = confirmationItems([item],people);
  assert.equal(payload.execution_protocol_version,3);
  assert.equal(payload.assignee_account_code,'USER0');
  for(const key of ['checklist','deliverable','acceptance_criteria','selected'])assert.equal(Object.hasOwn(payload,key),false);
  assert.throws(()=>confirmationItems([{...item,assignee_account_code:'',assignee_hint:'USER0'}],people),/请选择/);
});
test('old draft requirements are preserved for reference, but not silently dispatched as V2', async()=>{
  const {draftRows,confirmationItems} = await load();
  const old = {...item,execution_protocol_version:2,deliverable:'旧交付物',acceptance_criteria:'旧标准',checklist:[{title:'旧清单',acceptance_criteria:'旧验收'}]};
  const rows=draftRows({items:[old]});
  assert.equal(rows[0].deliverable,'旧交付物');
  assert.equal(rows[0].checklist[0].title,'旧清单');
  const [payload]=confirmationItems(rows,people);
  assert.equal(payload.execution_protocol_version,3);
  assert.equal(payload.description,item.description);
  assert.equal(Object.hasOwn(payload,'deliverable'),false);
});
