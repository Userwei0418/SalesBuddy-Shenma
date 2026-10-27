import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {PERFORMANCE_METRICS,performanceRankingQuery,matchingPerformanceRankings,performanceAmount,performanceTarget,performanceRate,performanceRowNote,highlightedRankingRow,createPerformanceRankingApi} from '../department-ui/dashboard-performance-ranking.js';

const period={year:2026,quarter:4};
const query=performanceRankingQuery(period,'forecast_collection');
const target=()=>({kind:'collection',status:'effective',value:'200000.00',known_subtotal:'200000.00',coverage:'complete'});
const row=(id,rankBasis='amount')=>({id,name:`名称${id}`,team_id:'department',team_name:'部门甲',value:'100000.01',known_subtotal:'100000.01',coverage:'complete',missing_amount_count:0,unknown_eligibility_count:0,missing_reasons:[],target:target(),rate:'0.50000005',rank:1,rank_basis:rankBasis,provisional:false});
const response=(code=query.metric_code)=>{
  const metric=PERFORMANCE_METRICS.find(item=>item.code===code);
  return {contract_version:'dashboard.performance_rankings.v1',...period,metric_code:code,label:metric.label,unit:'元',currency:'CNY',tax_basis:metric.taxBasis,basis:metric.basis,
    data_as_of:'2026-10-25T10:00:00+08:00',own_user_id:'self',department_sort_basis:'rate',department_sort_reason:'comparable_targets',
    departments_coverage:'complete',people_coverage:'complete',cohort:{scope:'current_authorized_ranking_scope',departments_count:1,people_count:1},
    departments:[row('department','rate')],people:[row('self')],definitions:['仅当前授权的部门与个人；按所选季度读取。'],
    attribution_unknown_period_count:0,department_excluded_fact_count:0,people_excluded_fact_count:0};
};

test('six metric requests use one quarter and no main-dashboard member or team filters',()=>{
  assert.equal(PERFORMANCE_METRICS.length,6);
  for(const metric of PERFORMANCE_METRICS){
    assert.deepEqual(performanceRankingQuery({...period,member_id:'other',team_groups:['team:private']},metric.code),{...period,metric_code:metric.code});
  }
  assert.throws(()=>performanceRankingQuery({...period,quarter:5},query.metric_code));
  assert.throws(()=>performanceRankingQuery(period,'forecast_recognized'));
  assert.equal(highlightedRankingRow(row('self'),'people',{selectedMemberId:'self'},'another'),true);
  assert.equal(highlightedRankingRow(row('department'),'departments',{selectedTeamGroups:['team:department']},'self'),true);
});

test('response validation rejects stale quarters, wrong metric bases, wider cohorts and fabricated ranks',()=>{
  for(const metric of PERFORMANCE_METRICS){
    const selection=performanceRankingQuery(period,metric.code),value=response(metric.code);
    assert.equal(matchingPerformanceRankings(value,selection).metric_code,metric.code);
    assert.throws(()=>matchingPerformanceRankings({...value,basis:'unverified'},selection));
  }
  for(const change of [value=>value.quarter=3,value=>value.tax_basis='tax_inclusive',value=>value.basis='unverified',value=>value.cohort.scope='company_all',value=>value.cohort.people_count=2,
    value=>value.department_sort_basis='amount',value=>Object.assign(value.people[0],{value:null,known_subtotal:null}),
    value=>Object.assign(value.people[0],{coverage:'partial',value:null}),
    value=>value.departments[0].target={...target(),status:'permission_denied'}]){
    const value=response();change(value);assert.throws(()=>matchingPerformanceRankings(value,query));
  }
  const unknown=response();Object.assign(unknown.people[0],{value:null,known_subtotal:null,coverage:'unavailable',rank:null,rate:null,missing_amount_count:1});
  assert.equal(matchingPerformanceRankings(unknown,query).people[0].rank,null);
});

test('zero, unknown, partial, denied and unconfigured targets stay distinct with exact money formatting',()=>{
  const partial={...row('p'),value:null,known_subtotal:'1234567890123456.78',coverage:'partial',missing_amount_count:2,provisional:true};
  assert.equal(performanceAmount(partial).text,'已知小计 123,456,789,012.35');
  assert.equal(performanceAmount(partial).exact,'1,234,567,890,123,456.78 元');
  assert.equal(performanceAmount({...row('p'),value:'0',known_subtotal:'0'}).text,'0.00');
  assert.equal(performanceAmount({...row('p'),value:null,known_subtotal:null,coverage:'unavailable',missing_amount_count:1}).text,'数据未齐');
  assert.equal(performanceAmount({...row('p'),value:null,known_subtotal:null,coverage:'unavailable',missing_reasons:['actual_not_registered']}).text,'未登记');
  assert.equal(performanceAmount({...row('p'),value:null,known_subtotal:null,coverage:'unavailable',missing_reasons:['actual_attribution_unresolved']}).text,'待核对');
  assert.equal(performanceTarget({...target(),status:'permission_denied',value:null,known_subtotal:null}),'不可查看');
  assert.equal(performanceTarget({...target(),status:'not_configured',value:null,known_subtotal:null}),'未配置此目标');
  assert.equal(performanceTarget({...target(),status:'no_target',value:null,known_subtotal:null}),'未设置');
  assert.equal(performanceTarget({...target(),value:'0'}),'0.00');
  assert.equal(performanceRate({...row('p'),rate:null,target:{...target(),value:'0'}}),'目标为 0');
  assert.match(performanceRowNote(partial),/2 项金额待补充/);
  const referenceNote=performanceRowNote({...partial,missing_reasons:['material_tax_rate_missing','material_schedule_missing','material_manual_input_required','material_lines_missing']});
  for(const note of ['冻结税率缺失','交付或服务周期未填写','手填口径','尚未填写交易物料'])assert.ok(referenceNote.includes(note));
  assert.doesNotMatch(performanceRowNote({...row('p'),missing_reasons:['SQL private-field']}),/SQL|private-field/);
});

test('rankings use fresh authenticated GET and discard late account results without fallback',async()=>{
  let owner='tenant:user:permission1',finish;const calls=[];
  const service=createPerformanceRankingApi({getIdentity:()=>owner,getClient:()=>({request:async options=>{calls.push(options);return response();}})});
  await service.rankings({...query,member_id:'other',team_groups:['team:private']});
  assert.equal(calls[0].method,'GET');assert.equal(calls[0].fresh,true);
  const url=new URL(calls[0].path,'https://test.invalid');assert.equal(url.pathname,'/dashboard/metrics/rankings');
  assert.deepEqual([...url.searchParams.keys()],['year','quarter','metric_code']);
  const late=createPerformanceRankingApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>finish=resolve)})});
  const pending=late.rankings(query);owner='tenant:user:permission2';finish(response());await assert.rejects(pending,/身份或权限/);
  const denied=createPerformanceRankingApi({getIdentity:()=>owner,getClient:()=>({request:async()=>{throw {statusCode:403};}})});
  await assert.rejects(denied.rankings(query),/无权查看业绩排名/);
});

const root=fileURLToPath(new URL('../',import.meta.url));
const code=buildSync({absWorkingDir:root,entryPoints:['department-ui/DashboardPerformanceRanking.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const compiled=new Module(root+'performance-ranking-test.cjs');compiled.filename=root+'performance-ranking-test.cjs';compiled.paths=Module._nodeModulePaths(root);compiled._compile(code,compiled.filename);
const render=result=>renderToStaticMarkup(React.createElement(compiled.exports.PerformanceRankingTables,{result,metric:PERFORMANCE_METRICS[0],data:{selectedMemberId:'self'}}));

test('real rendering preserves two rankings, money precision, sorting fallback and local seven-row pagination',()=>{
  const value=response();
  Object.assign(value,{department_sort_basis:'amount',department_sort_reason:'no_comparable_targets'});
  value.departments[0]={...row('department'),target:{...target(),status:'no_target',value:null,known_subtotal:null},rate:null};
  value.people=Array.from({length:8},(_,i)=>({...row(i?'person'+i:'self'),rank:i+1}));
  const html=render({value});
  assert.match(html,/部门业绩排名/);assert.match(html,/个人业绩排名/);assert.match(html,/销售及销售主管 · 按金额排名/);assert.match(html,/无可比目标，按金额排名/);
  assert.match(html,/金额（万元）/);assert.match(html,/title="100,000.01 元"/);assert.match(html,/>10\.00</);assert.match(html,/ds-performance-highlight/);
  assert.match(html,/名称person6/);assert.doesNotMatch(html,/名称person7/);assert.match(html,/title="2"/);assert.doesNotMatch(html,/ant-pagination-options-size-changer/);
  assert.match(html,/未设置目标/);
  value.attribution_unknown_period_count=2;value.people_excluded_fact_count=3;
  const notes=render({value});assert.match(notes,/2 项季度实绩归属待核对/);assert.match(notes,/3 条可见记录未匹配/);assert.doesNotMatch(notes,/5 项|5 条/);
  for(const state of [{loading:true,value},{error:'读取失败',value},{loading:true},{loading:true,value:undefined},{error:'读取失败'}]){
    let markup;assert.doesNotThrow(()=>markup=render(state));assert.doesNotMatch(markup,/名称self|10\.00/);
  }
});

test('one metric choice controls both tables and the full component can render before any data',()=>{
  let selected;const choices=compiled.exports.PerformanceMetricChoices({selected:'forecast_collection',onChange:code=>selected=code});
  const buttons=choices.props.children;
  assert.equal(buttons.length,6);assert.equal(buttons.filter(button=>button.props['aria-pressed']).length,1);
  for(const button of buttons){button.props.onClick();assert.ok(PERFORMANCE_METRICS.some(metric=>metric.code===selected));assert.equal(performanceRankingQuery(period,selected).metric_code,selected);}
  const html=renderToStaticMarkup(React.createElement(compiled.exports.default,{data:{},period,service:{rankings(){throw Error('SSR must not request');}}}));
  assert.match(html,/部门和个人同步切换/);assert.match(html,/当前授权范围/);assert.match(html,/正在读取业绩排名/);
});
