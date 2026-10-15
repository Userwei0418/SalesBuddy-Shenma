require('./helpers/business-options');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../miniprogram/pages/customer-create/index.js');
const teams = [{id:'south', name:'南区'}, {id:'allowed-team', name:'授权团队'}];
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(permissions, directory) {
  const session = {userId:'u1', workspaceId:'w1', role:'sales', team:'南区',
    permissions, permissionVersion:'v1'};
  const app = {ensureLogin:()=>true, globalData:{session}};
  const calls = [], toasts = [], storage = new Map();
  const api = {
    getBusinessOptions:async()=>require('./helpers/business-options.json'),
    getTeamDirectory:async(purpose, options={})=>{
      calls.push({purpose, ...options});
      if (directory) return directory(purpose, options);
      // Model the server's legacy assignment default, not a permissive page mock.
      const permission = options.permission || 'customer.create';
      if (permissions[permission] !== true) throw Error('没有所需权限');
      return {teams};
    },
    listCustomerCreationRequests:async()=>({items:[]}),
  };
  let definition;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    require:name=>name.endsWith('/apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    Page:value=>{definition=value;}, getApp:()=>app,
    wx:{getStorageSync:key=>storage.get(key), showToast:value=>toasts.push(value.title),
      navigateBack:()=>{}, vibrateShort:()=>{}},
    setTimeout:()=>{}, setInterval, clearInterval,
  });
  const page = {...definition, data:structuredClone(definition.data),
    setData(update){Object.assign(this.data, update);}};
  return {page, app, calls, toasts, storage};
}

for (const [name, permissions, expected, requestMode] of [
  ['request-only', {'customer.create_request':true}, 'customer.create_request', true],
  ['direct-only', {'customer.create':true}, 'customer.create', false],
  ['both actions', {'customer.create_request':true, 'customer.create':true}, 'customer.create', false],
]) {
  test(`customer creation ${name} loads teams for its actual write action`, async()=>{
    const h=harness(permissions);
    await h.page.onLoad(); await flush();
    assert.deepEqual(h.calls, [{purpose:'assignment', permission:expected}]);
    assert.equal(h.page.data.requestMode, requestMode);
    assert.deepEqual(h.toasts, []);
    const index=h.page.data.fields.findIndex(field=>field.key==='target_team');
    assert.equal(h.page.data.fields[index].teamId, 'south');
    h.page.openEditor({currentTarget:{dataset:{index}}});
    assert.deepEqual(Array.from(h.page.data.editorOptions, option=>option.id), ['south','allowed-team']);
    h.page.selectOption({currentTarget:{dataset:{index:1}}});
    h.page.saveEditor();
    assert.equal(h.page.data.fields[index].teamId, 'allowed-team');
  });
}

test('customer creation without either action never requests a directory', async()=>{
  const h=harness({}); await h.page.onLoad(); await flush();
  assert.deepEqual(h.calls, []);
  assert.deepEqual(h.toasts, ['当前账号没有客户建档权限']);
});

test('customer creation preserves directory failures and never invents selectable teams', async()=>{
  for (const [directory, message] of [
    [async()=>{throw Error('当前团队无申请权限');}, '当前团队无申请权限'],
    [async()=>({teams:null}), '团队目录不可用'],
  ]) {
    const h=harness({'customer.create_request':true}, directory);
    await h.page.onLoad(); await flush();
    assert.deepEqual(h.toasts, [message]);
    const index=h.page.data.fields.findIndex(field=>field.key==='target_team');
    h.page.openEditor({currentTarget:{dataset:{index}}});
    assert.deepEqual(Array.from(h.page.data.editorOptions), []);
  }
});

test('customer creation clears restored team IDs outside the returned action scope', async()=>{
  const h=harness({'customer.create_request':true}, async()=>({teams:[teams[1]]}));
  h.storage.set('customerCreateDraft:w1:u1', {fields:[{key:'target_team', value:'南区', teamId:'south'}]});
  await h.page.onLoad(); await flush();
  const field=h.page.data.fields.find(field=>field.key==='target_team');
  assert.equal(field.teamId, ''); assert.equal(field.value, '');
  assert.equal(field.missing, true);
});

test('customer creation ignores late directory results and errors after identity or permission changes', async()=>{
  for (const changed of ['userId','permissionVersion']) {
    for (const failure of [false,true]) {
      let resolve, reject;
      const pending=new Promise((yes,no)=>{resolve=yes; reject=no;});
      const h=harness({'customer.create_request':true}, ()=>pending);
      await h.page.onLoad();
      h.app.globalData.session={...h.app.globalData.session, [changed]:'changed'};
      const before=JSON.stringify(h.page.data.fields);
      if (failure) reject(Error('旧会话错误')); else resolve({teams});
      await flush();
      assert.equal(JSON.stringify(h.page.data.fields), before);
      assert.deepEqual(h.toasts, []);
      assert.equal(h.page.directoryTeams, undefined);
    }
  }
});
