const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const access = require('../miniprogram/utils/access');

function user(codes) {
  return {role:'sales', permissions:Object.fromEntries(codes.map(code=>[code,true]))};
}
test('新建入口可见性遵循实际页面权限，申请权不升级为直接创建权', () => {
  for (const code of ['customer.create_request','customer.create']) {
    const session=user(['access.mini_program',code]);
    assert.equal(access.flags(session,'customers').canOpenCustomerCreate,true);
    assert.equal(access.flags(session,'customer-claim').canOpenCustomerCreate,true);
    assert.equal(access.flags(session).canCreateCustomer,code==='customer.create');
    session.permissionGrants=[{permission_code:code,effect:'deny'}];
    assert.equal(access.flags(session).canOpenCustomerCreate,false);
  }
  for (const session of [null,user([]),user(['customer.claim','battle_map.read','access.mini_program']),user(['customer.create_request'])]) {
    assert.equal(access.flags(session).canOpenCustomerCreate,false);
  }
});

function setup(route, code) {
  const session=user(['access.mini_program',code]), navigations=[], notices=[];
  const app={globalData:{session},can:key=>access.can(session,key)};
  const wx={navigateTo:params=>navigations.push(params),showToast:params=>notices.push(params)};
  const accessFile=path.resolve(__dirname,'../miniprogram/utils/access.js'),module={exports:{}};
  vm.runInNewContext(fs.readFileSync(accessFile,'utf8'),{module,wx});
  const filename=path.resolve(__dirname,'../miniprogram/pages/'+route+'/index.js');
  let page;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
    Page:definition=>page=definition,getApp:()=>app,wx,
    require:name=>name.endsWith('apiClient')?new Proxy({}, {get:()=>()=>assert.fail('入口不应触发API或提交')}):require(path.resolve(path.dirname(filename),name)),
    Date,Map,Set,setTimeout,clearTimeout,
  });
  page.data={...page.data};
  module.exports.protectActions(app,page,route);
  return {page,session,navigations,notices};
}
for (const route of ['customers','customer-claim']) {
  test(route+' 的入口只跳转原建档页，点击时复核权限撤回及拒绝', () => {
    for (const code of ['customer.create_request','customer.create']) {
      const h=setup(route,code);
      h.page.openCustomerCreate();
      assert.equal(h.navigations.length,1);
      assert.equal(h.navigations[0].url,'/pages/customer-create/index');
      h.session.permissions[code]=false;
      h.page.openCustomerCreate();
      assert.equal(h.navigations.length,1);
      h.session.permissions[code]=true;
      h.session.permissionGrants=[{permission_code:code,effect:'deny'}];
      h.page.openCustomerCreate();
      assert.equal(h.navigations.length,1);
      assert.equal(h.notices.length,2);
    }
  });
}
test('认领正在提交时不会跳去新建，更不会携带搜索词自动创建', () => {
  const h=setup('customer-claim','customer.create_request');
  h.page.data.query='同名客户';
  h.page.data.submitting=true;
  h.page.openCustomerCreate();
  assert.equal(h.navigations.length,0);
  h.page.data.submitting=false;
  h.page.openCustomerCreate();
  assert.equal(h.navigations[0].url,'/pages/customer-create/index');
});
