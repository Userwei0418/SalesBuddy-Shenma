const test = require('node:test');
const assert = require('node:assert/strict');
const {pathToFileURL} = require('node:url');
const path = require('node:path');
const load = () => import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets/organization.js')).href);

test('custom role badge and filter retain administrator and separate same-name roles', async()=>{
  const {accountHasRole,permissionRoleBadges,accountPayload} = await load();
  const row={id:'user',roles:['administrator'],company_roles:['administrator'],permission_roles:[{id:'market-role',name:'市场',scope:'workspace'}],memberships:[]};
  assert.equal(accountHasRole(row,'administrator'),true);
  assert.equal(accountHasRole(row,'operations'),false);
  assert.equal(accountHasRole(row,'permission:market-role'),true);
  assert.equal(accountHasRole(row,'permission:other-company-role'),false);
  assert.equal(accountHasRole({roles:['administrator']},'permission:market-role'),false);
  assert.match(permissionRoleBadges(row),/市场 · 公司/);
  assert.equal(permissionRoleBadges({}), '');
  assert.equal(accountHasRole(row,''),true);
  const form=new FormData();form.append('company_roles','administrator');form.set('display_name','测试市场');
  const saved=accountPayload(form,row);
  assert.deepEqual(saved.roles,['administrator']);assert.deepEqual(saved.memberships,[]);
  assert.equal(Object.hasOwn(saved,'permission_roles'),false);
});
test('custom names are escaped and scoped labels do not imply whole-company access', async()=>{
 const {permissionRoleBadges}=await load();
 const html=permissionRoleBadges({permission_roles:[{id:'x',name:'<img src=x onerror=alert(1)>',scope:'teams'}]});
 assert.ok(!html.includes('<img'));assert.match(html,/&lt;img/);assert.match(html,/指定团队/);assert.ok(!html.includes('· 公司'));
});
test('saving own profile confirms success and reauthenticates without reading with the revoked session', async()=>{
  const {completeAccountSave}=await load();
  const {state,clearSession,toast}=await import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets/core.js')).href);
  const oldDocument=global.document,oldFetch=global.fetch;
  const notice={textContent:'',style:{}},events=[];
  global.document={querySelector:()=>notice,dispatchEvent:event=>{events.push(event.type);clearSession();}};
  global.fetch=()=>{throw Error('A revoked session must not refresh organization data');};
  state.actor={workspace_id:'test',user_id:'self',role:'administrator'};
  state.token='fixture-token';
  try {
    await completeAccountSave('self');
    assert.equal(notice.textContent,'成员资料已保存，请重新登录');
    assert.deepEqual(events,['session-expired']);
    assert.equal(state.token,null);
  } finally { clearTimeout(toast.timer);global.document=oldDocument;global.fetch=oldFetch;clearSession(); }
});
test('saving another member refreshes the organization without expiring the admin session', async()=>{
  const {completeAccountSave}=await load();
  const {state,clearSession,toast}=await import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets/core.js')).href);
  const oldDocument=global.document,oldFetch=global.fetch;
  const notice={textContent:'',style:{}},calls=[],events=[];
  global.document={querySelector:()=>notice,dispatchEvent:event=>events.push(event.type)};
  global.fetch=async url=>{calls.push(url);return {ok:true,status:200,json:async()=>url.endsWith('/password-policy')?{require_initial_change:true}:{accounts:[]}};};
  state.actor={workspace_id:'test',user_id:'self',role:'administrator'};
  state.token='fixture-token';
  try {
    await completeAccountSave('another');
    assert.deepEqual(calls.sort(),['/api/v1/console/organization','/api/v1/console/password-policy']);
    assert.deepEqual(events,[]);
    assert.equal(state.token,'fixture-token');
    assert.equal(notice.textContent,'成员资料已更新');
    assert.equal(state.org.password_policy.require_initial_change,true);
  } finally { clearTimeout(toast.timer);global.document=oldDocument;global.fetch=oldFetch;clearSession(); }
});
