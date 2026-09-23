const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const load = () => import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/organization.js')).href);

test('company administrator needs no business team and hybrid manager retains appointment', async()=>{
  const {accountPayload} = await load();
  const f=new FormData(); f.set('display_name','公司运营'); f.append('company_roles','administrator'); f.set('organization_team_id','growth-team');
  const p=accountPayload(f);
  assert.equal(p.organization_team_id,'growth-team'); assert.equal(Object.hasOwn(p,'email'),false);
  assert.equal(p.team_id,null); assert.deepEqual(p.memberships,[]); assert.deepEqual(p.roles,['administrator']);
  f.set('membership:0:team','business-team'); f.append('membership:0:roles','manager'); f.set('team_id','business-team');
  const hybrid=accountPayload(f);
  assert.deepEqual(hybrid.memberships,[{team_id:'business-team',roles:['manager'],acting:false}]);
  assert.deepEqual(hybrid.roles,['manager','administrator']);
  assert.deepEqual(hybrid.company_roles,['administrator']);
});

test('empty authorization and incomplete business appointments cannot be submitted', async()=>{
  const {accountPayload} = await load();
  const f=new FormData(); assert.throws(()=>accountPayload(f),/公司管理权限/);
  f.append('company_roles','administrator'); f.set('membership:0:team','team');
  assert.throws(()=>accountPayload(f),/至少一个岗位/);
});
