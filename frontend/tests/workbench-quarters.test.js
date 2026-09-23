const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const pageResponse=require('./helpers/opportunity-pages');
const {
  beijingDateParts,
  quarterSelection,
  matchesQuarter, OPPORTUNITY_OVERVIEW_HELP,
} = require('../miniprogram/utils/opportunityQuarter');

const tick = () => new Promise(resolve => setImmediate(resolve));
const snapshot = value => JSON.parse(JSON.stringify(value));
const ids = page => Array.from(page.data.filteredOpportunities, item => item.id);
const quarterEvent = (scope, value) => ({ currentTarget: { dataset: { scope, value } } });

function pageWith(api = {}, role = 'manager') {
  let page;
  const file = path.resolve(__dirname, '../miniprogram/pages/workbench/index.js');
  const app = {
    ensureLogin: () => true,
    globalData: {
      role,
      session: { userId: 'quarter-test-user', workspaceId: 'quarter-test-workspace', role },
      roles: {
        sales: { name: '一线销售', scope: '仅本人' },
        supervisor: { name: '销售主管', scope: '团队' },
        manager: { name: '总经理', scope: '全部团队' },
      },
    },
  };
  const modals = [];
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: definition => { page = definition; },
    getApp: () => app,
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(file), name)),
    Date, setTimeout, clearTimeout,
    wx: {
      getStorageSync() {}, removeStorageSync() {}, showToast() {}, navigateTo() {},
      showNavigationBarLoading() {}, hideNavigationBarLoading() {},
      showModal(value) {modals.push(value);},
    },
  });
  page.data = snapshot(page.data);
  page.setData = (values, callback) => {
    Object.assign(page.data, values);
    if (callback) callback();
  };
  page.fixtureApp = app;
  page.fixtureModals = modals;
  return page;
}

function fixtures(year) {
  return [
    { id: 'open-q1', status: 'open', probability: 30, expected_close_date: `${year}-03-12`, created_at: `${year}-01-02T10:00:00+08:00`, owner_name: '销售甲', team_name: '南区', amount: 100000 },
    { id: 'open-q2', status: 'open', probability: 70, expected_close_date: `${year}-05-12`, created_at: `${year}-04-02T10:00:00+08:00`, owner_name: '销售乙', team_name: '北区', amount: 200000 },
    { id: 'won-q1', status: 'won', probability: 100, expected_close_date: `${year}-06-12`, closed_at: `${year}-02-02T10:00:00+08:00`, created_at: `${year}-04-03T10:00:00+08:00`, owner_name: '销售乙', team_name: '北区', amount: 300000 },
    { id: 'lost-q2', status: 'lost', probability: null, expected_close_date: `${year}-05-22`, closed_at: `${year}-05-01T10:00:00+08:00`, created_at: `${year}-02-03T10:00:00+08:00`, owner_name: '销售甲', team_name: '南区', amount: 400000 },
    { id: 'previous-year', status: 'open', probability: 10, expected_close_date: `${year - 1}-01-12`, created_at: `${year - 1}-01-01T10:00:00+08:00`, owner_name: '销售甲', team_name: '南区', amount: 500000 },
  ];
}

async function loadedPage(items, listResult = { items }) {
  const calls = [];
  const page = pageWith({
    getOpportunityOverview: async selection => ({ metrics: {
      won: items.length ? (selection.year === new Date().getFullYear() ? 1 : 0) : 0,
      total: items.length ? 5 : 0,
      active: items.length ? 1 : 0, newCount: items.length ? (selection.quarters.length ? 2 : 5) : 0,
      missingCloseDates: 0, missingWonDates: 0, missingCreatedDates: 0,
    } }),
    getWorkbench: async () => ({
      summary: {},
      members: [
        { name: '销售甲', team: '南区', role: 'sales' },
        { name: '销售乙', team: '北区', role: 'sales' },
      ],
      customers: [], tasks: [], risks: [],
      opportunities: items.filter(item => item.status === 'open'),
    }),
    listOpportunities: async options => {
      calls.push(snapshot(options));
      if (listResult instanceof Error) throw listResult;
      return pageResponse(listResult.items,options);
    },
  });
  await page.loadData();
  await tick();
  return { page, calls };
}

test('商机季度按北京时间切分，跨季和跨年时间戳归入正确季度', () => {
  assert.equal(beijingDateParts('2026-03-31T15:59:59Z').quarter, 1);
  assert.equal(beijingDateParts('2026-03-31T16:00:00Z').quarter, 2);
  assert.equal(beijingDateParts('2025-12-31T16:00:00Z').year, 2026);
  assert.equal(beijingDateParts('2026-04-01').quarter, 2);
  const q2 = quarterSelection(2026, [2]);
  assert.equal(matchesQuarter('2026-03-31T15:59:59Z', q2), false);
  assert.equal(matchesQuarter('2026-03-31T16:00:00Z', q2), true);
  assert.equal(matchesQuarter('2025-04-01', q2), false);
  assert.equal(matchesQuarter('invalid-date', q2), false);
  assert.equal(matchesQuarter(null, q2), false);
});

test('季度多选是同年的并集，空选择覆盖全部时间和未填写日期', () => {
  const selected = quarterSelection(2026, [1, 3]);
  assert.deepEqual(selected.options.filter(item => item.selected).map(item => item.value), [1, 3]);
  assert.equal(matchesQuarter('2026-01-02', selected), true);
  assert.equal(matchesQuarter('2026-08-02', selected), true);
  assert.equal(matchesQuarter('2026-04-02', selected), false);
  assert.equal(matchesQuarter('2025-08-02', selected), false);
  const all = quarterSelection(2026);
  assert.equal(all.label, '全部时间（跨年）');
  assert.equal(matchesQuarter('2024-04-02', all), true);
  assert.equal(matchesQuarter(null, all), true);
  assert.equal(matchesQuarter('invalid-date', all), true);
});

test('总览独立读取服务端指标，绝不从商机列表推导', async () => {
  const year = beijingDateParts(new Date()).year;
  const rows = fixtures(year);
  const { page, calls } = await loadedPage(rows);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].includeClosed, true);
  assert.equal(page.data.opportunityDataReady, true);
  assert.equal(page.data.opportunityBoard.won, 1);
  assert.equal(page.data.opportunityBoard.total, 5);
  assert.equal(page.data.opportunityBoard.active, 1);
  assert.equal(page.data.opportunityBoard.newCount, 5);
  assert.equal(page.data.summaryQuarter.year, year);
  assert.deepEqual(Array.from(page.data.summaryQuarter.quarters), []);
  assert.deepEqual(Array.from(page.data.listQuarter.quarters), []);
});

test('总览季度与列表季度、阶段和经理团队筛选彼此独立', async () => {
  const year = beijingDateParts(new Date()).year;
  const { page } = await loadedPage(fixtures(year));
  const initialList = ids(page);
  page.toggleQuarter(quarterEvent('summary', 1));
  await tick();
  assert.deepEqual(ids(page), initialList);
  assert.equal(page.data.opportunityBoard.won, 1);
  assert.equal(page.data.opportunityBoard.total, 5);
  assert.equal(page.data.opportunityBoard.active, 1);
  assert.equal(page.data.opportunityBoard.newCount, 2);
  const summary = snapshot(page.data.opportunityBoard);

  page.toggleQuarter(quarterEvent('list', 2));
  await tick();
  assert.deepEqual(ids(page), ['won-q1', 'open-q2', 'lost-q2']);
  assert.deepEqual(snapshot(page.data.opportunityBoard), summary);
  page.changeExecutionTeam({ detail: { value: page.data.executionTeamOptions.findIndex(item => item.value === '南区') } });
  await tick();
  assert.deepEqual(ids(page), ['lost-q2']);
  assert.deepEqual(snapshot(page.data.opportunityBoard), summary);
  page.toggleOpportunityStage({ currentTarget: { dataset: { value: 'won' } } });
  await tick();
  assert.deepEqual(ids(page), []);
  assert.deepEqual(snapshot(page.data.opportunityBoard), summary);
});

test('两组季度可独立多选，列表重置和总览重置只清理各自筛选', async () => {
  const year = beijingDateParts(new Date()).year;
  const { page } = await loadedPage(fixtures(year));
  page.toggleQuarter(quarterEvent('summary', 1));
  await tick();
  page.toggleQuarter(quarterEvent('list', 1));
  await tick();
  page.toggleQuarter(quarterEvent('list', 2));
  await tick();
  assert.deepEqual(ids(page), ['open-q1', 'won-q1', 'open-q2', 'lost-q2']);
  const summary = snapshot(page.data.opportunityBoard);
  page.resetOpportunityFilters();
  await tick();
  assert.equal(ids(page).length, 5);
  assert.deepEqual(Array.from(page.data.listQuarter.quarters), []);
  assert.deepEqual(Array.from(page.data.summaryQuarter.quarters), [1]);
  assert.deepEqual(snapshot(page.data.opportunityBoard), summary);

  page.toggleQuarter(quarterEvent('list', 2));
  await tick();
  const filteredList = ids(page);
  page.resetSummaryQuarter();
  await tick();
  assert.deepEqual(Array.from(page.data.summaryQuarter.quarters), []);
  assert.deepEqual(Array.from(page.data.listQuarter.quarters), [2]);
  assert.deepEqual(ids(page), filteredList);
  assert.equal(page.data.opportunityBoard.total, 5);
});

test('切换总览年份明确进入全年，全部存量不变且不改变列表筛选', async () => {
  const year = beijingDateParts(new Date()).year;
  const { page } = await loadedPage(fixtures(year));
  page.toggleQuarter(quarterEvent('summary', 1));
  await tick();
  page.toggleQuarter(quarterEvent('list', 2));
  await tick();
  const before = ids(page);
  const yearIndex = page.data.quarterYearOptions.findIndex(item => Number(item.value) === year - 1);
  assert.ok(yearIndex >= 0, '存在历史商机时应可选择其年份');
  page.changeQuarterYear({ currentTarget: { dataset: { scope: 'summary' } }, detail: { value: yearIndex } });
  await tick();
  assert.equal(page.data.summaryQuarter.year, year - 1);
  assert.deepEqual(Array.from(page.data.summaryQuarter.quarters), [1, 2, 3, 4]);
  assert.equal(page.data.summaryQuarter.label, `${year - 1}年 全年`);
  assert.equal(page.data.opportunityBoard.total, 5);
  assert.equal(page.data.opportunityBoard.active, 1);
  assert.equal(page.data.opportunityBoard.won, 0);
  assert.equal(page.data.listQuarter.year, year);
  assert.deepEqual(ids(page), before);
});

test('列表失败或超过300条不影响独立总览', async () => {
  const { page: empty } = await loadedPage([]);
  assert.equal(empty.data.opportunityDataReady, true);
  assert.equal(empty.data.opportunityBoard.total, 0);
  const { page: failed } = await loadedPage([], new Error('网络中断'));
  assert.equal(failed.data.opportunityDataReady, true);
  assert.ok(failed.data.opportunityListError);
  const rows = Array.from({ length: 301 }, (_, index) => ({ id: `opportunity-${index}`, status: 'open', probability: 30, amount: 100 }));
  const { page: complete } = await loadedPage(rows);
  assert.equal(complete.data.opportunityDataReady, true);
  assert.equal(complete.data.filteredOpportunities.length, 20);
  assert.equal(complete.data.opportunityTotal,301);
  assert.equal(complete.data.opportunityHasMore,true);
  assert.equal(complete.data.opportunityBoard.total, 5); // Deliberately different server scope; no local recount.
});

test('全部时间转年份请求全年四季，统计说明区分存量、原建单日期与关单筛选', async () => {
  const requests=[];
  const page=pageWith({getOpportunityOverview:async params=>{
    requests.push(snapshot(params));return {metrics:{won:14,total:258,active:176,newCount:params.quarters.length ? 251 : 258,
      missingCloseDates:0,missingWonDates:14,missingCreatedDates:0}};
  }});
  page.setData({summaryQuarter:quarterSelection(2026),quarterYearOptions:[{value:2025},{value:2026}]});
  page.changeQuarterYear({currentTarget:{dataset:{scope:'summary'}},detail:{value:1}});await tick();
  assert.deepEqual(requests[0].quarters,[1,2,3,4]);assert.equal(requests[0].year,2026);
  assert.equal(page.data.opportunityBoard.total,258);assert.equal(page.data.opportunityBoard.newCount,251);
  page.toggleQuarter(quarterEvent('summary','all'));await tick();
  assert.deepEqual(requests[1].quarters,[]);assert.match(page.data.summaryQuarter.label,/跨年/);
  assert.equal(page.data.opportunityBoard.newCount,258);
  page.showOpportunityMetricHelp();assert.equal(page.fixtureModals[0].content,OPPORTUNITY_OVERVIEW_HELP);
  assert.match(OPPORTUNITY_OVERVIEW_HELP,/季度筛选不改变当前存量/);
  assert.match(OPPORTUNITY_OVERVIEW_HELP,/原始建单日期/);assert.match(OPPORTUNITY_OVERVIEW_HELP,/不按导入时间补算/);
  const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/workbench/index.wxml'),'utf8');
  assert.match(wxml,/>全部商机</);assert.match(wxml,/选择年份/);
  assert.doesNotMatch(wxml,/未计入总量的季度统计/);
  assert.match(wxml,/按预计关单时间.*筛选结果/);
});

test('总览失败、慢响应及离开页面不会显示错误季度的旧指标', async () => {
  let resolveFirst;
  const page = pageWith({ getOpportunityOverview: selection => selection.year === 2025
    ? new Promise(resolve => { resolveFirst = resolve; }) : Promise.reject(new Error('稍后重试')) });
  page.data.summaryQuarter = quarterSelection(2025, [1]);
  const first = page.applyOpportunitySummary();
  page.data.summaryQuarter = quarterSelection(2026, [2]);
  await page.applyOpportunitySummary();
  assert.equal(page.data.opportunityDataReady, false);
  assert.equal(page.data.opportunityDataError, '稍后重试');
  resolveFirst({ metrics: { won: 9, total: 9, active: 9, newCount: 9, missingCloseDates: 0, missingWonDates: 0, missingCreatedDates: 0 } });
  await first;
  assert.equal(page.data.opportunityDataReady, false);
  page.data.summaryQuarter = quarterSelection(2025, [1]);
  const pending = page.applyOpportunitySummary();
  page.onUnload(); resolveFirst({ metrics: {} }); await pending;
  assert.equal(page.data.opportunityDataReady, false);
});

for (const role of ['supervisor', 'manager']) {
  test(`${role} 后台刷新时保留仅有关闭商机的负责人筛选及列表`, async () => {
    const rows = [
      { id: 'active-owner-a', status: 'open', probability: 30, owner_name: '销售甲', team_name: '南区', amount: 100000 },
      { id: 'closed-owner-b', status: 'won', probability: 100, owner_name: '销售乙', team_name: '南区', amount: 200000 },
    ];
    let workbenchRequests = 0;
    let listRequests = 0;
    let resolveCompleteList;
    const page = pageWith({
      getOpportunityOverview: async () => ({metrics:{won:1,total:2,active:1,newCount:2,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}),
      getWorkbench: async () => {
        workbenchRequests += 1;
        return {
          summary: {},
          members: [
            { name: '销售甲', team: '南区', role: 'sales' },
            { name: '销售乙', team: '南区', role: 'sales' },
          ],
          customers: [], tasks: [], risks: [], opportunities: [rows[0]],
        };
      },
      listOpportunities: options => {
        listRequests += 1;
        if (listRequests <= 2) return Promise.resolve(pageResponse(rows,options));
        return new Promise(resolve => { resolveCompleteList = value=>resolve(pageResponse(value.items,options)); });
      },
    }, role);
    await page.loadData();
    await tick();
    const ownerIndex = page.data.opportunityOwnerOptions.findIndex(item => item.value === '销售乙');
    assert.ok(ownerIndex > 0);
    page.changeOpportunityFilter({ currentTarget: { dataset: { key: 'Owner' } }, detail: { value: ownerIndex } });
  await tick();
    assert.deepEqual(ids(page), ['closed-owner-b']);

    page.workbenchLoadedAt = Date.now() - 31000;
    page.onShow();
    await tick();
    assert.equal(workbenchRequests, 0, '商机主页面不再读取旧复合工作台接口');
    assert.equal(listRequests, 3);
    assert.equal(page.data.opportunityListLoading, true);
    assert.equal(page.data.opportunityOwnerOptions[page.data.opportunityOwnerIndex].value, '销售乙');
    assert.deepEqual(ids(page), ['closed-owner-b']);

    resolveCompleteList({ items: rows });
    await tick();
    assert.equal(page.data.opportunityListLoading, false);
    assert.equal(page.data.opportunityOwnerOptions[page.data.opportunityOwnerIndex].value, '销售乙');
    assert.deepEqual(ids(page), ['closed-owner-b']);
  });
}


test('默认按年份季度排列、季度内阶段降序，日期待完善的商机置底', async () => {
  const rows = [
    {id:'q2-high',status:'open',probability:90,expected_close_date:'2026-04-01'},
    {id:'q1-low',status:'open',probability:30,expected_close_date:'2026-01-01'},
    {id:'undated-won',status:'won',probability:100},
    {id:'q1-won',status:'won',probability:100,expected_close_date:'2026-03-31'},
    {id:'q1-lost',status:'lost',probability:null,expected_close_date:'2026-02-01'},
    {id:'prior',status:'open',probability:10,expected_close_date:'2025-12-31'},
    {id:'q1-low-2',status:'open',probability:30,expected_close_date:'2026-02-28'},
  ];
  const originalIds = rows.map(item => item.id);
  const {page} = await loadedPage(rows);
  assert.deepEqual(Array.from(page.data.opportunityGroups,group=>group.label),['2025年 · Q4','2026年 · Q1','2026年 · Q2','待确认季度']);
  assert.deepEqual(ids(page),['prior','q1-won','q1-low','q1-low-2','q1-lost','q2-high','undated-won']);
  assert.deepEqual(rows.map(item=>item.id),originalIds);
});

test('关单时间单入口支持季度多选，快捷时间与自选季度互斥，收起不丢筛选', async()=>{
  const year=beijingDateParts(new Date()).year;
  const {page}=await loadedPage(fixtures(year));
  const summary=snapshot(page.data.opportunityBoard);
  assert.equal(page.data.showOpportunityQuarterFilter,false);
  page.toggleOpportunityStageFilter();
  page.toggleOpportunityQuarterFilter();
  assert.equal(page.data.showOpportunityStageFilter,false);
  assert.equal(page.data.showOpportunityQuarterFilter,true);
  page.selectOpportunityClosePeriod({currentTarget:{dataset:{index:1}}});
  await tick();
  assert.equal(page.data.opportunityCloseIndex,1);
  assert.deepEqual(Array.from(page.data.listQuarter.quarters),[]);
  page.toggleQuarter(quarterEvent('list',1));
  await tick();
  page.toggleQuarter(quarterEvent('list',2));
  await tick();
  assert.equal(page.data.opportunityCloseIndex,0);
  assert.deepEqual(ids(page),['open-q1','won-q1','open-q2','lost-q2']);
  const selected=ids(page);
  page.toggleOpportunityQuarterFilter();
  assert.equal(page.data.showOpportunityQuarterFilter,false);
  assert.deepEqual(ids(page),selected);
  assert.deepEqual(snapshot(page.data.opportunityBoard),summary);
  page.selectOpportunityClosePeriod({currentTarget:{dataset:{index:3}}});
  await tick();
  assert.deepEqual(Array.from(page.data.listQuarter.quarters),[]);
  page.resetOpportunityFilters();
  await tick();
  assert.equal(page.data.opportunityCloseIndex,0);
  assert.equal(ids(page).length,5);
});


test('商机首屏缓存按账号和权限隔离，旧复合工作台接口不参与首屏',async()=>{
 const requests=[];
 const page=pageWith({listOpportunities:options=>new Promise((resolve,reject)=>requests.push({resolve:rows=>resolve(pageResponse(rows,options)),reject})),
   getOpportunityOverview:async()=>({metrics:{won:0,total:0,active:0,newCount:0,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}),
   getWorkbench:()=>{throw new Error('不得读取未展示的客户/任务/风险');}},'sales');
 page.onShow();assert.equal(requests.length,1);
 page.fixtureApp.globalData.session.userId='other-sales';page.onShow();assert.equal(requests.length,2);
 requests[0].resolve([{id:'old'}]);await tick();assert.deepEqual(ids(page),[]);
 requests[1].reject(new Error('请求超时，请重试'));await tick();assert.match(page.data.opportunityListError,/重试/);
 page.onShow();assert.equal(requests.length,3);requests[2].resolve([]);await tick();
 assert.equal(page.data.dataReady,true);page.onShow();assert.equal(requests.length,3);
 page.fixtureApp.globalData.session.permissionVersion='new';page.onShow();assert.equal(requests.length,4);
 requests[3].resolve([]);await tick();
});

test('商机第一页先呈现，不等待慢总览，也不读取旧复合工作台',async()=>{
 let resolveOverview,legacyReads=0;
 const rows=fixtures(2026);
 const page=pageWith({listOpportunities:async options=>pageResponse(rows,options),
  getOpportunityOverview:()=>new Promise(resolve=>{resolveOverview=resolve;}),
  getWorkbench:()=>{legacyReads++;throw Error('旧接口不可用');}});
 const pending=page.loadData();await tick();
 assert.equal(page.data.dataReady,true);assert.equal(page.data.opportunityListLoading,false);
 assert.equal(page.data.filteredOpportunities.length,5);assert.equal(page.data.opportunityOverviewLoading,true);
 assert.equal(page.data.opportunityDataReady,false);assert.equal(legacyReads,0);
 resolveOverview({metrics:{won:1,total:4,active:2,newCount:3,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}});
 await pending;assert.equal(page.data.opportunityBoard.total,4);
});

test('总览团队目录独立于商机数据，空团队可选并使用团队ID请求',()=>{
 const p=pageWith();p.data.role='manager';p.data.executionTeamKey='empty';
 p.acceptOpportunityPage(pageResponse([], {directoryTeams:[{id:'empty',name:'空业务组'},{id:'renamed',name:'更名后的团队'}]}),false);
 assert.deepEqual(Array.from(p.data.executionTeamOptions,row=>row.value),['all','empty','renamed']);
 assert.equal(p.data.executionTeamIndex,1);assert.equal(p.data.executionTeamLabel,'空业务组');
 assert.equal(p.data.opportunityTotal,0);assert.equal(p.opportunityParams().teamId,'empty');
 assert.equal(p.opportunityParams().team,undefined);
});
