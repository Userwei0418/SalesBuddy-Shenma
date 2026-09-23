const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const pageResponse = require('./helpers/opportunity-pages');
const filename = path.resolve(__dirname, '../miniprogram/components/fde-projects/index.js');
const lead = {workspaceId:'workspace',userId:'lead',role:'fde_lead',permissionVersion:'v1',loginAt:'first',capabilities:{'team.view':true}};
const project = {id:'op1',name:'协助项目',customer_name:'客户一',amount:128000,status:'open',stage_code:'solution',probability:50,fde_members:[{id:'member-a',name:'成员甲'}]};
const flush = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => {resolve=yes;reject=no;});
  return {promise,resolve,reject};
}
function setup() {
  const app = {globalData:{session:{...lead}}};
  const projects = [], directories = [], updates = [], overviews = [], modals = [];
  const timers = new Map(); let timerId = 0;
  const api = {
    listOpportunities(params, cancelled) {const request={...deferred(),params,cancelled:()=>page.serial!==request.serial||page.projectIdentity!==require('../miniprogram/utils/access').identity(app.globalData.session)};request.serial=page.serial;const original=request.resolve;request.resolve=value=>original(value&&Array.isArray(value.items)&&!value.summary?pageResponse(value.items,params):value);projects.push(request);return request.promise;},
    getDirectoryMembers() {const request={...deferred(),params:{}};const resolve=request.resolve;request.resolve=value=>resolve(value&&value.members?{items:value.members.map(row=>({role:'fde',...row}))}:value);directories.push(request);return request.promise;},
    getOpportunityOverview:async params=>{overviews.push(JSON.parse(JSON.stringify(params)));return {metrics:{won:0,total:1,active:1,newCount:1,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}};},
  };
  let definition;
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Component:value => {definition=value;},getApp:() => app,Date,Set,
    wx:{showModal:value=>modals.push(value)},
    setTimeout:(run,delay)=>{const id=++timerId;timers.set(id,{run,delay});return id;},
    clearTimeout:id=>timers.delete(id),
    require:name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename),name)),
  });
  const page = {...definition,...definition.methods,properties:{memberId:'',customerId:'',initialScope:''},
    data:JSON.parse(JSON.stringify(definition.data)),
    setData(values) {updates.push(values);Object.assign(this.data,values);},
  };
  page.setData({scope:'team',year:2026});
  const runTimers=()=>{const pending=[...timers.values()];timers.clear();pending.forEach(timer=>timer.run());};
  return {page,app,projects,directories,updates,timers,runTimers,overviews,modals};
}
const ids = rows => Array.from(rows, row => row.id);

test('FDE总览选择年份进入全年且与销售共用存量和历史日期说明，列表筛选仍独立', async () => {
  const {page,overviews,modals}=setup();
  const {quarterSelection,OPPORTUNITY_OVERVIEW_HELP}=require('../miniprogram/utils/opportunityQuarter');
  page.setData({years:[2025,2026],year:2025,quarters:[3],summaryQuarter:quarterSelection(2025)});
  page.summaryYear({detail:{value:1}});await flush();
  assert.deepEqual(overviews[0].quarters,[1,2,3,4]);assert.equal(overviews[0].year,2026);
  assert.equal(page.data.summaryQuarter.label,'2026年 全年');assert.equal(page.data.year,2025);
  assert.deepEqual(Array.from(page.data.quarters),[3]);
  page.summaryQuarterChange({currentTarget:{dataset:{q:0}}});await flush();
  assert.deepEqual(overviews[1].quarters,[]);assert.match(page.data.summaryQuarter.label,/跨年/);
  page.showOpportunityMetricHelp();assert.equal(modals[0].content,OPPORTUNITY_OVERVIEW_HELP);
  const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/components/fde-projects/index.wxml'),'utf8');
  assert.match(wxml,/>全部商机</);assert.match(wxml,/全部商机不随季度变化/);
  assert.match(wxml,/选择年份/);assert.doesNotMatch(wxml,/未计入季度总量/);
});

test('FDE列表从跨年全部切年份会查询全年，保留显式季度且不改变总览', () => {
  const {page}=setup();const requests=[];page.filter=()=>requests.push(JSON.parse(JSON.stringify(page.pageParams())));
  const {quarterSelection}=require('../miniprogram/utils/opportunityQuarter');
  page.setData({years:[2025,2026],summaryQuarter:quarterSelection(2026,[3]),quarters:[]});
  page.year({detail:{value:0}});assert.equal(requests[0].year,2025);assert.deepEqual(requests[0].quarters,[1,2,3,4]);
  assert.ok(page.data.quarterOptions.every(item=>item.selected));
  page.setData({quarters:[2]});page.year({detail:{value:1}});
  assert.deepEqual(requests[1].quarters,[2]);assert.deepEqual(Array.from(page.data.summaryQuarter.quarters),[3]);
});

test('项目先显示，迟到的成员目录失败不清空项目、数量或金额，下一次加载可恢复目录', async () => {
  const {page,projects,directories} = setup();
  let completed = false;
  const first = page.load().then(() => {completed=true;});
  projects[0].resolve({items:[project]});
  await flush();
  assert.equal(completed,true,'项目加载不等待成员目录');
  assert.equal(page.data.loading,false);
  assert.equal(page.data.membersLoading,true);
  const before = JSON.stringify({items:page.data.items,filtered:page.data.filtered,count:page.data.count,acv:page.data.acv});
  directories[0].reject(Error('成员目录请求超时'));
  await flush();await first;
  assert.equal(page.data.error,'');
  assert.equal(page.data.membersLoading,false);
  assert.match(page.data.membersError,/超时/);
  assert.equal(JSON.stringify({items:page.data.items,filtered:page.data.filtered,count:page.data.count,acv:page.data.acv}),before);
  const second = page.load();
  assert.equal(page.data.membersError,'');
  directories[1].resolve({members:[{id:'member-a',name:'成员甲'}]});
  projects[1].resolve({items:[project]});
  await second;await flush();
  assert.deepEqual(ids(page.data.members),['','member-a']);
  assert.equal(page.data.count,1);
  assert.equal(page.data.acv,'12.8');
  assert.equal(page.data.scope,'team');
});

test('目录先失败仍可等待并显示主列表；目录成功也不会掩盖主列表失败', async () => {
  const {page,projects,directories} = setup();
  const first = page.load();
  directories[0].reject(Error('目录离线'));
  await flush();
  assert.equal(page.data.loading,true);
  assert.equal(page.data.error,'');
  projects[0].resolve({items:[project]});
  await first;
  assert.deepEqual(ids(page.data.filtered),['op1']);
  const second = page.load();
  directories[1].resolve({members:[{id:'member-a',name:'成员甲'}]});
  projects[1].reject(Error('商机查询失败'));
  await second;await flush();
  assert.match(page.data.error,/商机查询失败/);
  assert.deepEqual(ids(page.data.items),[]);
  assert.deepEqual(ids(page.data.members),['','member-a']);
  assert.equal(page.data.membersError,'');
});

test('负责人通过成员筛选查看本人，旧目录的成功或失败不能覆盖当前目录状态', async () => {
  for (const outcome of ['resolve','reject']) {
    const {page,projects,directories,updates} = setup();
    const first = page.load();projects[0].resolve({items:[project]});await first;
    page.member({detail:{ids:['lead']}});
    projects[1].resolve({items:[{...project,id:'self-project'}]});
    await flush();
    assert.equal(projects[1].params.scope,'team');
    assert.equal(JSON.stringify(projects[1].params.memberIds),'["lead"]');
    assert.equal(page.data.membersLoading,true);
    const updateCount = updates.length;
    directories[0][outcome](outcome === 'resolve' ? {members:[{id:'old',name:'旧目录'}]} : Error('旧请求失败'));
    await flush();
    assert.equal(updates.length,updateCount,'旧目录包括finally都不回填');
    directories[1].resolve({members:[{id:'lead',name:'本人'}]});await flush();
    assert.deepEqual(ids(page.data.members),['','lead']);
    assert.deepEqual(ids(page.data.filtered),['self-project']);
    assert.equal(page.data.membersError,'');
  }
});

test('快速切成员保持精确请求范围，目录重排按成员ID恢复选择', async () => {
  const {page,projects,directories} = setup();
  page.setData({members:[{id:'',name:'全部成员'},{id:'member-a',name:'甲'},{id:'member-b',name:'乙'}],memberIds:['member-a']});
  const first=page.load();
  page.member({detail:{ids:['member-b']}});
  assert.equal(JSON.stringify(projects[0].params.memberIds),'["member-a"]');
  assert.equal(JSON.stringify(projects[1].params.memberIds),'["member-b"]');
  assert.equal(projects[0].cancelled(),true);
  projects[1].resolve({items:[{...project,id:'b-project'}]});
  directories[1].resolve({members:[{id:'member-b',name:'乙'},{id:'member-a',name:'甲'}]});
  await flush();
  assert.equal(page.data.memberIds[0],'member-b');
  directories[0].resolve({members:[{id:'member-a',name:'旧甲'}]});
  projects[0].resolve({items:[{...project,id:'a-project'}]});
  await first;await flush();
  assert.deepEqual(ids(page.data.filtered),['b-project']);
  assert.equal(page.data.memberIds[0],'member-b');
});

test('最新目录缺少当前筛选成员时不将现有项目误标为全部成员', async () => {
  const {page,projects,directories} = setup();
  page.setData({members:[{id:'',name:'全部成员'},{id:'member-a',name:'甲'}],memberIds:['member-a']});
  const pending=page.load();
  directories[0].resolve({members:[]});projects[0].resolve({items:[project]});
  await pending;await flush();
  assert.equal(page.data.memberIds[0],'member-a');
  assert.match(page.data.membersError,/当前成员/);
  assert.deepEqual(ids(page.data.filtered),['op1']);
  assert.equal(page.data.error,'');
});

test('换账号、权限或重新登录后，未启动新加载也拒绝旧请求的成功与失败', async () => {
  for (const change of [{userId:'other'},{permissionVersion:'v2'},{loginAt:'second'}]) {
    for (const outcome of ['resolve','reject']) {
      const {page,app,projects,directories,updates} = setup();
      const pending = page.load();
      app.globalData.session={...app.globalData.session,...change};
      assert.equal(projects[0].cancelled(),true);
      const updateCount=updates.length;
      directories[0][outcome](outcome === 'resolve' ? {members:[{id:'old',name:'旧成员'}]} : Error('旧目录失败'));
      projects[0][outcome](outcome === 'resolve' ? {items:[project]} : Error('旧商机失败'));
      await pending;await flush();
      assert.equal(updates.length,updateCount);
    }
  }
});

test('新身份加载立即清掉旧目录选项，不把旧成员ID带进新身份查询', async () => {
  const {page,app,projects,directories} = setup();
  const first=page.load();
  directories[0].resolve({members:[{id:'member-a',name:'甲'}]});projects[0].resolve({items:[project]});
  await first;await flush();
  page.setData({memberIds:['member-a']});
  app.globalData.session={...lead,userId:'other-lead',loginAt:'second'};
  const second=page.load();
  assert.deepEqual(ids(page.data.members),['']);
  assert.deepEqual(ids(page.data.items),[]);
  assert.equal(projects[1].params.memberId,'');
  directories[1].resolve({members:[{id:'member-b',name:'乙'}]});
  projects[1].resolve({items:[{...project,id:'new-project'}]});
  await second;await flush();
  assert.deepEqual(ids(page.data.members),['','member-b']);
  assert.deepEqual(ids(page.data.filtered),['new-project']);
});

test('组件离页后目录与项目均不回填，普通FDE不读取团队目录', async () => {
  const {page,projects,directories,updates}=setup();
  const pending=page.load();page.lifetimes.detached.call(page);
  const updateCount=updates.length;
  directories[0].resolve({members:[{id:'late',name:'迟到成员'}]});projects[0].reject(Error('迟到失败'));
  await pending;await flush();assert.equal(updates.length,updateCount);
  const own=setup();own.app.globalData.session={...lead,role:'fde',capabilities:{}};
  const ownPending=own.page.load();
  assert.equal(own.directories.length,0);
  assert.equal(own.projects[0].params.scope,'self');
  own.projects[0].resolve({items:[project]});await ownPending;
  assert.deepEqual(ids(own.page.data.filtered),['op1']);
});

test('连续五次输入只发最终关键词一次查询，目录在途结果可正常完成而不重读', async () => {
  const {page,projects,directories,timers,runTimers}=setup();
  const first=page.load();
  for(const query of ['s','sa','sal','sale','sales'])page.search({detail:{value:query}});
  assert.equal(projects.length,1,'防抖期间不发送逐字请求');
  assert.equal(directories.length,1,'防抖不触发辅助目录请求');
  assert.equal(timers.size,1);assert.equal([...timers.values()][0].delay,250);
  projects[0].resolve({items:[project]});await first;
  assert.equal(page.data.filtered.length,0,'旧筛选回包不可覆盖输入中的筛选');
  runTimers();
  assert.equal(projects.length,2);assert.equal(projects[1].params.query,'sales');
  assert.equal(directories.length,1,'最终查询也复用该身份的在途目录');
  directories[0].resolve({members:[{id:'member-a',name:'成员甲'}]});
  projects[1].resolve({items:[project],summary:{total:85,open_amount:900000},has_more:true,next_offset:20});
  await flush();
  assert.deepEqual(ids(page.data.members),['','member-a']);assert.equal(page.data.membersLoading,false);
  assert.equal(page.data.count,85);assert.equal(page.data.acv,'90');
  assert.equal(page.data.filtered.length,1,'统计继续使用服务端完整筛选集合');
  page.search({detail:{value:'sales更多'}});runTimers();
  assert.equal(directories.length,1,'已成功目录同样不随关键词重读');
  projects[2].resolve({items:[]});await flush();
});

test('搜索防抖遇到显式筛选或卸载会清理，不能迟发重复或离页查询', async () => {
  const {page,projects,directories,timers,runTimers}=setup();
  const first=page.load();projects[0].resolve({items:[project]});directories[0].resolve({members:[]});await first;await flush();
  page.search({detail:{value:'新项目'}});
  page.stage({currentTarget:{dataset:{code:'solution'}}});
  assert.equal(timers.size,0);assert.equal(projects.length,2);assert.equal(projects[1].params.query,'新项目');
  assert.deepEqual(Array.from(projects[1].params.stages),['solution']);
  runTimers();assert.equal(projects.length,2);
  projects[1].resolve({items:[project]});await flush();
  page.search({detail:{value:'离页输入'}});page.lifetimes.detached.call(page);
  assert.equal(timers.size,0);runTimers();assert.equal(projects.length,2);
});

test('搜索等待时权限变化不发送旧范围查询，返回组件会恢复新身份加载', async () => {
  const {page,app,projects,directories,runTimers}=setup();
  const first=page.load();projects[0].resolve({items:[project]});directories[0].resolve({members:[]});await first;await flush();
  page.search({detail:{value:'客户'}});
  app.globalData.session={...app.globalData.session,permissionVersion:'v2'};
  runTimers();assert.equal(projects.length,1);
  const returned=page.pageLifetimes.show.call(page);
  assert.equal(projects.length,2);
  page.pageLifetimes.show.call(page);assert.equal(projects.length,2,'同代际在途首屏不会重复发送');
  projects[1].resolve({items:[project]});directories[1].resolve({members:[]});await returned;await flush();
  assert.equal(page.data.loading,false);assert.equal(page.data.count,1);
});

test('FDE卡片与销售共享字段格式，实绩零值和未登记值不会被项目金额替代', async () => {
  const {page,projects,directories} = setup();
  const row = {...project, team_name:'南区', owner_name:'销售甲', product_line:'知识检索',
    expected_close_date:'2026-10-17', actuals:{recognized_amount:0,collection_amount:null}};
  const pending = page.load();
  projects[0].resolve({items:[row]});
  directories[0].resolve({members:[]});
  await pending;
  const sales = require('../miniprogram/utils/opportunityListCard').decorateOpportunity(row);
  const fde = page.data.filtered[0];
  for (const key of ['gradeLabel','gradeCode','stageName','probabilityText','progressPercent','recognizedLabel','collectionLabel','closeLabel','productLineLabel','team','owner']) {
    assert.equal(fde[key],sales[key],key);
  }
  assert.equal(JSON.stringify(fde.signal),JSON.stringify(sales.signal));
  assert.equal(fde.collectionLabel,'未登记');
  assert.notEqual(fde.recognizedLabel,fde.amountText);
});

test('FDE总览与列表季度独立，切成员后迟到的旧总览不能覆盖新范围', async () => {
  const {page,app,projects,directories} = setup();
  const pending=[];
  // Replace the component's API through a dedicated VM so both endpoints are observed.
  const api={getOpportunityOverview(params){const r={...deferred(),params};pending.push(r);return r.promise;}};
  let definition;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Component:v=>definition=v,getApp:()=>app,Date,Set,setTimeout,clearTimeout,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name))});
  const view={...definition.methods,properties:{customerId:'',memberId:''},data:JSON.parse(JSON.stringify(definition.data)),setData(v){Object.assign(this.data,v);}};
  view.setData({scope:'team',summaryQuarter:require('../miniprogram/utils/opportunityQuarter').quarterSelection(2026,[1]),quarters:[3],members:[{id:'a'},{id:'b'}],memberIds:['a']});
  const first=view.loadOverview();
  assert.equal(JSON.stringify(pending[0].params.memberIds),'["a"]');
  assert.equal(JSON.stringify(pending[0].params.quarters),'[1]');
  view.setData({memberIds:['b']});const second=view.loadOverview();
  const metrics={won:1,total:4,active:3,newCount:2,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0};
  pending[1].resolve({metrics});await second;
  pending[0].resolve({metrics:{...metrics,total:99}});await first;
  assert.equal(view.data.overview.total,4);
  assert.equal(JSON.stringify(view.data.quarters),'[3]');
  const third=view.loadOverview();app.globalData.session={...app.globalData.session,permissionVersion:'v2'};
  pending[2].resolve({metrics:{...metrics,total:100}});await third;
  assert.equal(view.data.overview.total,4);
});
