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

test('company terminology and product department titles only change labels',async()=>{
  const {departmentRole}=await load();
  const {state}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
  state.org={roles:[{code:'sales',name:'客户经理'}]};
  assert.equal(departmentRole('sales',{kind:'sales'}),'客户经理');
  assert.equal(departmentRole('sales',{kind:'product_sales'}),'产品销售');
  assert.equal(departmentRole('supervisor',{kind:'product_sales'}),'产品销售主管');
  assert.equal(departmentRole('fde',{kind:'fde'}),'FDE');
  state.org={roles:[{code:'sales',name:'一线销售'}]};
  assert.equal(departmentRole('sales',{kind:'sales'}),'一线销售');
});

for (const role of ['administrator', 'operations']) {
  for (const allowed of [true, false]) {
    test(`company rename entry follows effective permission for ${role}: ${allowed}`, async()=>{
      const {companyCards}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/companies.js')).href);
      const {state}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
      const previous={actor:state.actor,permissions:state.permissions,company:state.company,companies:state.companies};
      try {
        state.actor={role};
        state.permissions={'company.update':allowed};
        state.company={id:'current-company'};
        state.companies=[{id:'current-company',name:'当前公司',code:'current',kind:'production',status:'active'}];
        assert.equal(companyCards().includes('data-company-edit="current-company"'),allowed);
      } finally { Object.assign(state,previous); }
    });
  }
}

test('company rename entry stays hidden until effective permissions are loaded',async()=>{
  const {companyCards}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/companies.js')).href);
  const {state}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
  const previous={actor:state.actor,permissions:state.permissions,company:state.company,companies:state.companies};
  try {
    state.actor={role:'administrator'};state.permissions=undefined;
    state.company={id:'current-company'};
    state.companies=[{id:'current-company',name:'当前公司',code:'current',kind:'production',status:'active'}];
    assert.equal(companyCards().includes('data-company-edit='),false);
  } finally { Object.assign(state,previous); }
});

test('company rename permission does not expose editing another company',async()=>{
  const {companyCards}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/companies.js')).href);
  const {state}=await import(pathToFileURL(path.resolve(__dirname,'../../backend/src/sales_backend/web/assets/core.js')).href);
  const previous={actor:state.actor,permissions:state.permissions,company:state.company,companies:state.companies};
  try {
    state.actor={role:'administrator'};state.permissions={'company.update':true};
    state.company={id:'current-company'};
    state.companies=[{id:'other-company',name:'其他公司',code:'other',kind:'production',status:'active'}];
    const html=companyCards();
    assert.equal(html.includes('data-company-edit='),false);
    assert.equal(html.includes('data-company-select="other-company"'),true);
  } finally { Object.assign(state,previous); }
});
