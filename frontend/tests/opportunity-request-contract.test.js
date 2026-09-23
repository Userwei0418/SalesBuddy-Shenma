const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function transport() {
  const storage = new Map(), requests = [];
  const wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key),
    request: request => {
      if(request.url.endsWith('/metadata/business-options')){request.success({statusCode:200,data:require('./helpers/business-options.json')});return;}requests.push(request);
      request.success({statusCode: 200, data: {items: []}});
    },
  };
  const filename = path.resolve(__dirname, '../miniprogram/utils/apiClient.js');
  const module = {exports: {}};
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    module, wx, Map, Set, Date,
    require: name => require(path.resolve(path.dirname(filename), name)),
  });
  module.exports.saveAuth({access_token: 'test-only', actor: {workspace_id: 'w', user_id: 'u'}});
  return {api: module.exports, requests};
}

function opportunityPage(role, api, view = 'opportunities') {
  let definition;
  const filename = path.resolve(__dirname, `../miniprogram/pages/${view}/index.js`);
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => { definition = value; },
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    Date, Set, Map, setTimeout, clearTimeout, wx: {},
  });
  return {...definition, data: {...JSON.parse(JSON.stringify(definition.data)), role}};
}

for (const role of ['supervisor', 'manager']) {
 for (const view of ['opportunities', 'workbench']) {
  test(`${view}/${role}: 全部负责人不会序列化为 owner=all，具名负责人保留原值`, async () => {
    const {api, requests} = transport(), page = opportunityPage(role, api, view);
    const params = () => view === 'workbench' ? page.opportunityParams() : page.pageParams();
    const ownerOptions = view === 'workbench' ? 'opportunityOwnerOptions' : 'ownerOptions';
    const ownerIndex = view === 'workbench' ? 'opportunityOwnerIndex' : 'ownerIndex';
    await api.listOpportunities(params());
    let query = new URL(requests.at(-1).url).searchParams;
    assert.equal(query.has('owner'), false);
    assert.equal(query.has('team'), false);
    assert.equal(query.get('page_size'), '20');
    assert.equal(query.get('offset'), '0');
    page.data[ownerOptions].push({value: '刘志德 & 南区', label: '刘志德 & 南区'});
    page.data[ownerIndex] = 1;
    await api.listOpportunities(params());
    query = new URL(requests.at(-1).url).searchParams;
    assert.equal(query.get('owner'), '刘志德 & 南区');
    page.data[ownerIndex] = 0;
    await api.listOpportunities(params());
    assert.equal(new URL(requests.at(-1).url).searchParams.has('owner'), false);
  });
 }
}

test('商机请求边界兼容旧调用者的 all 哨兵，保留筛选和翻页参数', async () => {
  const {api, requests} = transport();
  await api.listOpportunities({owner: 'all', team: 'all', grade: 'all', closePeriod: 'all',
    query: '客户%_ & 商机', stages: ['identified', 'won'], year: 2026,
    quarters: [1, 3], scope: 'team', productLine: '知识 & 检索', pageSize: 20, offset: 40});
  const query = new URL(requests[0].url).searchParams;
  for (const key of ['owner', 'team', 'grade', 'close_period']) assert.equal(query.has(key), false);
  assert.equal(query.get('q'), '客户%_ & 商机');
  assert.deepEqual(query.getAll('stages'), ['identified', 'won']);
  assert.deepEqual(query.getAll('quarters'), ['1', '3']);
  assert.equal(query.get('scope'), 'team');
  assert.equal(query.get('product_line'), '知识 & 检索');
  assert.equal(query.get('offset'), '40');
});

test('团队接口及商机筛选传稳定ID，全部团队不发送ID哨兵',async()=>{
 const {api,requests}=transport();
 await api.getTeamDirectory();assert.match(requests.at(-1).url,/\/directory\/teams\?purpose=browse$/);
 await api.listOpportunities({teamId:'2916fc3c-45f3-42bb-a33b-5b0f1688d547'});
 let query=new URL(requests.at(-1).url).searchParams;
 assert.equal(query.get('team_id'),'2916fc3c-45f3-42bb-a33b-5b0f1688d547');assert.equal(query.has('team'),false);
 await api.listOpportunities({teamId:'all'});query=new URL(requests.at(-1).url).searchParams;assert.equal(query.has('team_id'),false);
});
