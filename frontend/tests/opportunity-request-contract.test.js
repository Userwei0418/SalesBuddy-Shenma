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
    getApp: () => ({ globalData: { session: { userId: 'u', workspaceId: 'w', role, capabilities: {} } } }),
  });
  // This contract tests an explicitly selected authorized team filter.
  return {...definition, data: {...JSON.parse(JSON.stringify(definition.data)), role, canViewTeam:true, executionScope:'team'}};
}

test('财务目录和期间读链使用唯一契约，旧来源选项归一effective',async()=>{
 const {api,requests}=transport();
 await api.getCustomerAssets({basis:'historical',customer_id:'c'});
 assert.equal(new URL(requests.at(-1).url).searchParams.get('basis'),'effective');
 assert.equal(api.createCustomerActual,undefined);assert.equal(api.voidCustomerActual,undefined);
 await api.getOpportunityFinancialOverview('o/a',{year:2026,quarter:4});
 assert.match(requests.at(-1).url,/\/opportunities\/o%2Fa\/financial-overview\?year=2026&quarter=4$/);
 await api.listMaterials({q:'物料 & 名称',status:'active',limit:30,offset:30});
 assert.equal(new URL(requests.at(-1).url).searchParams.get('q'),'物料 & 名称');
 await api.previewOpportunityMaterials('o',{year:2026,quarter:4});
 assert.equal(requests.at(-1).method,'POST');assert.deepEqual(requests.at(-1).data,{year:2026,quarter:4});
 const draft={customer_id:'c',year:2026,quarter:4,draft_revision:2,trade_relation:'direct',material_lines:[]};
 await api.previewMaterialDraft(draft);
 assert.match(requests.at(-1).url,/\/api\/v1\/material-forecast-preview$/);
 assert.equal(requests.at(-1).method,'POST');assert.deepEqual(requests.at(-1).data,draft);
});
test('上游目录只查询可见经销商交易，排除同客户与自身，不靠本地渠道猜测',async()=>{
 const {api,requests}=transport();
 await api.listUpstreamOpportunities({q:'同名 & 商机',customer_id:'c',opportunity_id:'o',offset:30,limit:30});
 const url=new URL(requests.at(-1).url),q=url.searchParams;
 assert.equal(url.pathname,'/api/v1/opportunities');assert.equal(requests.at(-1).method,'GET');
 assert.equal(q.get('purpose'),'upstream');assert.equal(q.has('customer_id'),false);
 assert.equal(q.get('q'),'同名 & 商机');assert.equal(q.get('page_size'),'30');
 assert.equal(q.get('trade_relation'),'distributor');assert.equal(q.get('exclude_customer_id'),'c');assert.equal(q.get('exclude_opportunity_id'),'o');assert.equal(q.get('include_closed'),'true');assert.equal(q.get('offset'),'30');
 await api.listUpstreamOpportunities({customer_id:'c'});
 const first=new URL(requests.at(-1).url).searchParams;
 assert.equal(first.get('purpose'),'upstream');assert.equal(first.get('exclude_customer_id'),'c');
 assert.equal(first.has('customer_id'),false);assert.equal(first.has('exclude_opportunity_id'),false);
 assert.equal(first.get('page_size'),'30');assert.equal(first.get('offset'),'0');
});
test('普通商机列表、客户全景与交易关系筛选不自动转成上游查询',async()=>{
 const {api,requests}=transport();
 for(const options of [{},{customerId:'c'}]){
  await api.listOpportunities(options);
  const q=new URL(requests.at(-1).url).searchParams;
  assert.equal(q.has('purpose'),false);assert.equal(q.has('trade_relation'),false);
  assert.equal(q.has('exclude_customer_id'),false);assert.equal(q.has('exclude_opportunity_id'),false);
  assert.equal(q.get('customer_id'),options.customerId||null);
 }
 for(const suffix of ['', '&purpose=list']){
  await api.request({path:'/opportunities?trade_relation=distributor'+suffix});
  const q=new URL(requests.at(-1).url).searchParams;
  assert.equal(q.get('trade_relation'),'distributor');assert.equal(q.get('purpose'),suffix?'list':null);
  assert.equal(q.has('exclude_customer_id'),false);assert.equal(q.has('exclude_opportunity_id'),false);
 }
});

for (const role of ['supervisor', 'manager']) {
 for (const view of ['opportunities', 'workbench']) {
  test(`${view}/${role}: 全部负责人不发空筛选，负责人筛选始终提交稳定ID`, async () => {
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
    const selected='11111111-1111-4111-8111-111111111111';
    page.data[ownerOptions].push({value:selected,label:'刘志德 & 南区'});
    page.data[ownerIndex] = 1;
    await api.listOpportunities(params());
    query = new URL(requests.at(-1).url).searchParams;
    assert.equal(query.get('owner_id'),selected);
    assert.equal(query.has('owner'),false);
    page.data[ownerIndex] = 0;
    await api.listOpportunities(params());
    assert.equal(new URL(requests.at(-1).url).searchParams.has('owner'), false);
    assert.equal(new URL(requests.at(-1).url).searchParams.has('owner_id'),false);
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
