import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';

const root = fileURLToPath(new URL('..', import.meta.url));
const plain = value => JSON.parse(JSON.stringify(value));
let opportunityPresentation;
function component(file) {
  const module = {exports:{}};
  const source = transformSync(readFileSync(resolve(root, 'department-ui', file), 'utf8'), {loader:'jsx',format:'cjs'}).code;
  vm.runInNewContext(source, {module,exports:module.exports,require:name => name === 'react' ? React : name === './Opportunities.jsx' ? opportunityPresentation : name.endsWith('/MetricStrip.jsx') ? {__esModule: true, default: 'MetricStrip'} : new Proxy({}, {get:(_, key) => key})});
  return module.exports;
}
const {FdeProjects} = component('FdePages.jsx');
opportunityPresentation = component('Opportunities.jsx');
const {default:Opportunities, OpportunityCloseFilter} = opportunityPresentation;
const {OpportunityBoard} = component('SmallPages.jsx');
function find(tree, type, label) {
  if (!tree || typeof tree !== 'object') return;
  if (tree.type === type && (!label || tree.props.label === label)) return tree.props;
  for (const child of [tree.props?.extra, tree.props?.content, ...React.Children.toArray(tree.props?.children)]) {
    const result = find(child,type,label); if(result) return result;
  }
}
function runtime(fde, board = false) {
  let definition;
  const cache = new Map();
  const session = {workspaceId:'synthetic',userId:'user',role:fde?'fde':'sales',capabilities:{}};
  function load(file) {
    if(cache.has(file)) return cache.get(file);
    const module = {exports:{}};cache.set(file,module.exports);
    vm.runInNewContext(readFileSync(file,'utf8'), {module,exports:module.exports,Date,Set,Map,setTimeout,clearTimeout,
      Page:d=>{definition=d;},Component:d=>{definition=d;},getApp:()=>({globalData:{session}}),wx:{},
      require:name=>name.endsWith('apiClient')?{}:load(resolve(dirname(file),name+'.js'))});
    cache.set(file,module.exports);return module.exports;
  }
  load(resolve(root,'source/miniprogram',fde?'components/fde-projects/index.js':board?'pages/opportunities/index.js':'pages/workbench/index.js'));
  const page = {...definition,...definition.methods,properties:{customerId:'',memberId:'',initialScope:''},data:plain(definition.data),
    setData(values,done){Object.assign(this.data,values);done?.();}};
  const {summaryQuarterSelection,listQuarterSelection} = load(resolve(root,'source/miniprogram/utils/opportunityQuarter.js'));
  const years = [{value:'all',label:'全部年份'},{value:2025,label:'2025年'},{value:2026,label:'2026年'}];
  page.setData({years:[2025,2026],summaryYears:years,summaryYearOptions:years,quarterYearOptions:years.slice(1),summaryYearIndex:2,
    summaryQuarter:summaryQuarterSelection(2026),listQuarter:listQuarterSelection(2026),year:2026,yearIndex:0});
  const requests=[];
  const record=()=>requests.push(plain(fde ? {...page.pageParams(),overviewYear:page.data.summaryQuarter.year,overviewQuarters:page.data.summaryQuarter.quarters} : page.opportunityParams()));
  page.load=page.filter=page.loadAllOpportunities=record;
  const invoke=(name,payload={})=>page[name]({currentTarget:{dataset:payload.dataset||{}},detail:payload.detail||{}});
  const render=()=>fde?FdeProjects({projects:page,invokeOn:(_,name,payload)=>invoke(name,payload)}):board?OpportunityBoard({page,data:page.data,invoke}):Opportunities({page,data:page.data,invoke});
  return {page,render,requests,invoke};
}

for(const fde of [false,true]) test(`${fde?'FDE':'sales'} Web year and quarter controls use the embedded runtime contract`,()=>{
  const {page,render,requests}=runtime(fde);
  let strip=find(render(), fde ? 'SbMetricStrip' : 'MetricStrip');
  assert.equal(strip.extra.props.options[strip.extra.props.value].label,'2026年');
  strip.extra.props.onChange(1);
  assert.equal(requests.at(-1).overviewYear,2025);
  assert.deepEqual(requests.at(-1).overviewQuarters,[1,2,3,4]);
  find(render(), fde ? 'SbMetricStrip' : 'MetricStrip').onPeriodChange(3);
  assert.deepEqual(requests.at(-1).overviewQuarters,[3],'全年切Q3必须只选Q3');
  find(render(), fde ? 'SbMetricStrip' : 'MetricStrip').onPeriodChange(1);
  assert.deepEqual(requests.at(-1).overviewQuarters,[1]);
  if (!fde) assert.equal(requests.length, 3, 'year, Q3 and Q1 each issue exactly one request');
  find(render(), fde ? 'SbMetricStrip' : 'MetricStrip').onPeriodChange('all');
  assert.deepEqual(requests.at(-1).overviewQuarters,[1,2,3,4]);
  find(render(), fde ? 'SbMetricStrip' : 'MetricStrip').extra.props.onChange(0);
  assert.equal(page.data.summaryQuarter.isAllYears,true);
  assert.deepEqual(requests.at(-1).overviewQuarters,[]);
  assert.equal(find(render(), fde ? 'SbMetricStrip' : 'MetricStrip').periods.length,0,'跨年不展示全年或季度按钮');
  assert.deepEqual(requests.at(-1).quarters,[],'活动统计时间不改变预计关单范围');
});

for (const board of [false, true]) test(`${board ? 'opportunity board' : 'opportunity management'} renders actual API page order after shared accept and append`, () => {
  const {page, render} = runtime(false, board);
  page.pageRequest = {};
  page.setData({loading:false, opportunityListLoading:false});
  const base = {customer_id:'synthetic-customer', customer_name:'合成客户', amount:100, actuals:{recognized_amount:null,collection_amount:null}, owner_id:'user'};
  const rows = [
    {...base,id:'open-q1',name:'在推 Q1',status:'open',probability:30,expected_close_date:'2026-02-01'},
    {...base,id:'open-q2',name:'在推 Q2',status:'open',probability:30,expected_close_date:'2026-05-01'},
    {...base,id:'won-q1',name:'成单 Q1',status:'won',probability:100,expected_close_date:'2026-02-01'},
  ];
  const response = (items,more) => ({items,summary:{total:3},has_more:more,next_offset:more?2:null,
    overview:{definition_version:'opportunity_filtered_overview_v1',year:2026,quarters:[1,2,3,4],metrics:{total:3,won:1,active:2,newCount:3,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0,demo_scene_count:0}}});
  const accept = (...args) => board ? page.acceptPage(...args) : page.acceptOpportunityPage(...args);
  const renderedIds = () => {
    const ids = [];
    function visit(node) {
      if (!node || typeof node !== 'object') return;
      if (node.type === 'SbTable') ids.push(...node.props.rows.map(row=>row.id));
      for (const child of React.Children.toArray(node.props?.children)) visit(child);
    }
    visit(render()); return ids;
  };
  accept(response(rows.slice(0,2),true),false);
  assert.deepEqual(renderedIds(),['open-q1','open-q2']);
  accept(response(rows.slice(2),false),true);
  assert.notDeepEqual(plain((board?page.data.filtered:page.data.filteredOpportunities).map(row=>row.id)),rows.map(row=>row.id),'fixture must expose the shared native regrouping');
  assert.deepEqual(renderedIds(),rows.map(row=>row.id),'Web must consume original API sequence, not the native regrouped rows');
  if (board) {
    page.setData({role:'sales',canViewTeam:true});
    assert.ok(find(render(),'LabeledSearchSelect','团队'));
    assert.ok(find(render(),'LabeledSearchSelect','负责人'));
    page.setData({role:'manager',canViewTeam:false});
    assert.equal(find(render(),'LabeledSearchSelect','负责人'),undefined,'role name cannot grant team directory access');
  }
});

test('sales stage and historical close filters apply one whole selection without changing statistics time', () => {
  const {page, render, requests, invoke} = runtime(false);
  const stages = page.data.opportunityStageOptions.filter(row => row.value !== 'all').slice(0, 3).map(row => row.value);
  find(render(), 'SbLabeledSelect', '阶段').onChange(stages);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests.at(-1).stages, stages);
  const close = () => OpportunityCloseFilter({data: page.data, invoke});
  find(close(), 'SbLabeledSelect', '关单年份').onChange(2025);
  assert.equal(requests.length, 2);
  assert.equal(requests.at(-1).year, 2025);
  assert.deepEqual(requests.at(-1).quarters, [1,2,3,4]);
  find(close(), 'SbLabeledSelect', '关单季度').onChange([2,4]);
  assert.equal(requests.length, 3);
  assert.deepEqual(requests.at(-1).quarters, [2,4]);
  assert.equal(requests.at(-1).overviewYear, 2026);
  find(close(), 'SbLabeledSelect', '关单年份').onChange(undefined);
  assert.equal(requests.length, 4);
  assert.deepEqual(requests.at(-1).quarters, []);
  assert.equal(page.data.listQuarter.isAllYears, true);
  find(render(), 'SbLabeledSelect', '阶段').onChange(['not-a-stage']);
  assert.equal(requests.length, 4, 'invalid set is rejected without a request or partial state change');
  assert.deepEqual(plain(page.data.opportunitySelectedStages), stages);
});

test('sales activity selection updates the request and reset affordance together', () => {
  const {page, render, requests} = runtime(false);
  find(render(), 'SbLabeledSelect', '跟进时间').onChange('week');
  assert.equal(requests.at(-1).activityWindow, 'week');
  assert.equal(page.data.opportunityFilterActive, true);
  find(render(), 'SbLabeledSelect', '跟进时间').onChange('all');
  assert.equal(requests.at(-1).activityWindow, 'all');
  assert.equal(page.data.opportunityFilterActive, false);
});

test('FDE close-year options match the runtime index and quarterly deselection preserves the chosen set',()=>{
  const {page,render,requests}=runtime(true);
  let year=find(render(),'SbLabeledSelect','关单年份');
  assert.equal(year.options[year.value].label,'全部年份','默认不限关单年份，不能误标2021年');
  year.onChange(1);
  assert.equal(requests.at(-1).year,2025);
  assert.deepEqual(requests.at(-1).quarters,[1,2,3,4]);
  find(render(),'SbLabeledSelect','关单季度').onChange([2,3,4]);
  assert.deepEqual(requests.at(-1).quarters,[2,3,4],'取消Q1保留其余三季');
  find(render(),'SbLabeledSelect','关单季度').onChange([3]);
  assert.deepEqual(requests.at(-1).quarters,[3]);
  find(render(),'SbLabeledSelect','关单年份').onChange(0);
  assert.equal(page.data.listQuarter.isAllYears,true);
  assert.deepEqual(requests.at(-1).quarters,[]);
  assert.equal(requests.at(-1).overviewYear,2026);
});
