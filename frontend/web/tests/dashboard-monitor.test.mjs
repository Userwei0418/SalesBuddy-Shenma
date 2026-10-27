import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {currentMonitorQuarter,dashboardMonitorSelection,dashboardMonitorKey,matchingDashboardMetrics,matchingDashboardMetricPage,matchingDashboardActualPage,createDashboardMonitorApi,DASHBOARD_METRICS,dashboardMetricReason} from '../department-ui/dashboard-monitor.js';
import {metricCoverageText} from '../department-ui/review/overview-model.js';

const selection={personal:true,member_id:null,team_groups:[],year:2026,quarter:4};
const envelope=()=>({selection:{...selection,member_id:'self'},scope:{kind:'self',label:'本人'},quarter:{year:2026,quarter:4,time_progress:'0.25'},data_as_of:'2026-10-25T10:00:00Z'});
const overview=()=>({...envelope(),contract_version:'dashboard.metrics.v1',values_basis:'current_authorized_quarter_snapshot',metrics:DASHBOARD_METRICS.map(({code,basis,taxBasis})=>({metric_code:code,basis,tax_basis:taxBasis,registered_fact_count:1,registered_period_count:1,incomplete_period_count:0,missing_material_count:0,tax_unknown_period_count:0,conflict_period_count:0,value:'100000.00',known_subtotal:'100000.00',coverage:'complete',target:{status:'effective',value:'200000.00'},rate:'0.5',gap:'100000.00',day_change:null,baseline:{status:'no_baseline',reason:'当前看板范围暂无可比的昨日快照'},status:'on_track'}))});

test('monitor quarter defaults to Shanghai current quarter and stays separate from Bi chart quarters',()=>{
  assert.deepEqual(currentMonitorQuarter(new Date('2026-09-30T16:30:00Z')),{year:2026,quarter:4});
  const data={canViewTeam:true,viewMode:'team',selectedTeamGroups:['team:b','team:a'],selectedQuarter:{quarter:1},selectedQuarterKeys:['2026Q1','2026Q2']};
  const request=dashboardMonitorSelection(data,{year:2026,quarter:4});
  assert.deepEqual(request,{personal:false,member_id:null,team_groups:['team:a','team:b'],year:2026,quarter:4});
  assert.equal(dashboardMonitorKey(request),dashboardMonitorKey({...request,team_groups:['team:b','team:a']}));
  assert.notEqual(dashboardMonitorKey(request),dashboardMonitorKey({...request,quarter:3}));
  assert.deepEqual(dashboardMonitorSelection({...data,canViewTeam:false},selection),selection);
});

test('monitor rejects wrong company scope, member or quarter instead of displaying a broader response',()=>{
  assert.equal(matchingDashboardMetrics(overview(),selection).metrics.length,5);
  for (const edit of [r=>r.scope.kind='member',r=>r.scope.kind='authorized',r=>r.selection.personal=false,r=>r.quarter.quarter=3,r=>r.selection.team_groups=['team:a']]) {
    const value=overview();edit(value);assert.throws(()=>matchingDashboardMetrics(value,selection),/范围或季度/);
  }
  const team={...selection,personal:false,team_groups:['team:a']},value=overview();
  value.selection={...team};value.scope={kind:'team',label:'团队'};
  assert.equal(matchingDashboardMetrics(value,team).scope.kind,'team');
  value.selection.member_id='other';assert.throws(()=>matchingDashboardMetrics(value,team),/范围或季度/);
  const member={...selection,member_id:'other'},wrong=overview();
  assert.throws(()=>matchingDashboardMetrics(wrong,member),/范围或季度/);
});

test('missing values and denied targets cannot become complete zero or a guessed rate',()=>{
  const value=overview(),row=value.metrics[0];row.coverage='partial';row.value=null;row.rate=null;row.gap=null;
  row.target={status:'permission_denied',value:null};assert.equal(matchingDashboardMetrics(value,selection).metrics[0].value,null);
  row.rate='0.5';assert.throws(()=>matchingDashboardMetrics(value,selection),/目标可见范围/);
  row.rate=null;row.value='100000.00';assert.throws(()=>matchingDashboardMetrics(value,selection),/部分数据/);
  assert.equal(dashboardMetricReason('missing_forecast_amount'),'本季度预测金额未填写');
  assert.equal(dashboardMetricReason('SQL private_field required'),'暂不可计算');
});

test('metric detail validates exact metric and pagination, preserving unknown totals and values',()=>{
  const query={metric_code:'forecast_collection',limit:20,offset:0};
  const value={...envelope(),contract_version:'dashboard.metric_page.v1',...query,items:[{id:'opportunity',metric_contributions:{forecast_collection:{value:null,reason:'missing_forecast_amount'}}}],total:null,known_total:1,has_more:false,coverage:'partial'};
  assert.equal(matchingDashboardMetricPage(value,selection,query).total,null);
  for (const patch of [{metric_code:'collection_ppl'},{offset:20},{total:1}]) assert.throws(()=>matchingDashboardMetricPage({...value,...patch},selection,query),/明细或分页/);
});

test('monitor uses dedicated authenticated dashboard GETs with repeated team keys and rejects late identity results',async()=>{
  let owner='company-a:self',finish;const calls=[];
  const service=createDashboardMonitorApi({getIdentity:()=>owner,getClient:()=>({request:async options=>{calls.push(options);return overview();}})});
  await service.metrics(selection);
  assert.equal(calls[0].method,'GET');assert.ok(calls[0].fresh);assert.match(calls[0].path,/^\/dashboard\/metrics\?/);assert.doesNotMatch(calls[0].path,/review|work_report/);
  const team={...selection,personal:false,team_groups:['team:a','team:b']},result=overview();result.selection={...team};result.scope={kind:'authorized',label:'团队'};
  const teamService=createDashboardMonitorApi({getIdentity:()=>owner,getClient:()=>({request:async options=>{calls.push(options);return result;}})});
  await teamService.metrics(team);const params=new URLSearchParams(calls[1].path.split('?')[1]);assert.deepEqual(params.getAll('team_groups'),['team:a','team:b']);
  const late=createDashboardMonitorApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>finish=resolve)})});
  const pending=late.metrics(selection);owner='company-b:self';finish(overview());await assert.rejects(pending,/身份或权限已变化/);
});

const root=fileURLToPath(new URL('../',import.meta.url));
const code=buildSync({absWorkingDir:root,entryPoints:['department-ui/DashboardMonitor.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty','.svg':'dataurl'}}).outputFiles[0].text;
const compiled=new Module(root+'dashboard-monitor-test.cjs');compiled.filename=root+'dashboard-monitor-test.cjs';compiled.paths=Module._nodeModulePaths(root);compiled._compile(code,compiled.filename);
const render=result=>renderToStaticMarkup(React.createElement(compiled.exports.DashboardMonitorCards,{result,onDrill(){}}));
test('three existing-style cards show exact money, denied targets, missing snapshots and independent detail actions',()=>{
  const value=overview();value.metrics[1].target={status:'permission_denied',value:null};value.metrics[1].rate=null;value.metrics[1].gap=null;
  const html=render({value});
  for (const label of ['预测回款','回款 PPL 储备','预测确收','季度时间进度','当前看板范围暂无可比的昨日快照','目标不可查看']) assert.ok(html.includes(label),label);
  assert.equal((html.match(/查看明细/g)||[]).length,5);assert.match(html,/10\.00/);assert.match(html,/25\.0%/);
  assert.equal((html.match(/<button[^>]*type="button"[^>]*aria-label="[^"]*有效目标设置说明"/g)||[]).length,3);
  const cards=compiled.exports.DashboardMonitorCards({result:{value}}).props.children[0];
  for(const definition of cards.props.definitions){
    const help=cards.props.renderTargetHelp(definition),tooltip=help.type(help.props),copy=renderToStaticMarkup(tooltip.props.title);
    assert.match(copy,/配置目标/);assert.match(copy,/单位万元/);assert.ok(copy.includes(definition.label));
    assert.doesNotMatch(copy,/自定义|1000000|单位元[；。]/);assert.equal(tooltip.props.children.props.style.display,'inline-flex');
  }
  assert.doesNotMatch(render({loading:true,value}),/10\.00|查看明细/);
  assert.doesNotMatch(render({error:'读取失败',value,reload(){}}),/10\.00|查看明细/);
});

test('unset targets and unavailable collection explain the actual missing information and count',()=>{
  const value=overview();
  for (const metric of value.metrics) {
    metric.target={status:'no_target',coverage:'unavailable',value:null,known_subtotal:null};
    metric.rate=null;metric.gap=null;
  }
  Object.assign(value.metrics[0],{value:null,known_subtotal:null,coverage:'unavailable',unknown_eligibility_count:15,missing_amount_count:0,missing_reasons:['unknown_eligibility']});
  const html=render({value});
  assert.equal((html.match(/>未设置</g)||[]).length,3);
  assert.doesNotMatch(html,/目标未齐|目标覆盖不完整|已知目标小计/);
  assert.match(html,/暂无已确认的高信心预测回款/);
  assert.match(html,/15 条记录缺少本季度预测或回款信心度，待补充后确认是否纳入/);
  value.metrics[0].unknown_eligibility_count=7;
  assert.match(render({value}),/7 条记录缺少本季度预测或回款信心度/);
});

test('target copy keeps denied, partial and explicitly zero targets distinct',()=>{
  const value=overview();
  value.metrics[0].target={status:'permission_denied',coverage:'unavailable',value:null};
  value.metrics[1].target={status:'no_target',coverage:'partial',value:null,known_subtotal:'50000.00'};
  value.metrics[2].target={status:'effective',coverage:'complete',value:'0'};
  for (const metric of value.metrics) {metric.rate=null;metric.gap=null;}
  const html=render({value});
  assert.match(html,/>不可查看</);
  assert.match(html,/已知目标小计 5\.00 万元/);
  assert.equal((html.match(/目标覆盖不完整/g)||[]).length,1);
  assert.match(html,/目标明确为 0/);
});

test('collection reasons distinguish missing eligibility from unusable amounts and preserve scope warnings',()=>{
  const metric={metric_code:'forecast_collection',value:null,known_subtotal:'50000',unknown_eligibility_count:2,missing_amount_count:3,missing_reasons:['unknown_eligibility','currency_unavailable','source_scope_not_fully_authorized']};
  const note=metricCoverageText(metric);
  assert.match(note,/2 条记录缺少本季度预测或回款信心度/);
  assert.match(note,/3 条高信心记录的币种缺失或非人民币/);
  assert.doesNotMatch(note,/暂无|3 条高信心记录缺少本季度预测金额/);
  assert.match(note,/权限/);
  assert.equal(metricCoverageText({metric_code:'forecast_collection',value:'0',known_subtotal:'0',unknown_eligibility_count:0,missing_amount_count:0,missing_reasons:[]}), '');
});


const actualQuery={metric_code:'actual_recognized_exclusive',limit:20,offset:0};
const actualPage=()=>({...envelope(),contract_version:'dashboard.actual_page.v1',values_basis:'current_authorized_quarter_snapshot',basis:'adopted_actuals',...actualQuery,
  total:1,known_total:1,coverage:'complete',has_more:false,items:[{id:'period:customer:2026Q4',customer_id:'customer',customer_name:'客户甲',opportunity_id:null,opportunity_name:null,owner_name:null,
    year:2026,quarter:4,kind:'recognized',amount:'0.00',raw_amount:'0.00',tax_basis:'tax_exclusive',tax_unresolved:false,source:'operations',status:'confirmed',period_precision:'quarter',complete:true,missing_material_count:0}]});

test('actuals use their own endpoint and accept customer-level quarter records while rejecting stale or malformed details',async()=>{
  const page=actualPage();
  assert.equal(matchingDashboardActualPage(page,selection,actualQuery).items[0].opportunity_id,null);
  assert.equal(matchingDashboardActualPage(page,selection,actualQuery).items[0].amount,'0.00');
  assert.equal(matchingDashboardActualPage({...page,coverage:'partial'},selection,actualQuery).total,1);
  assert.equal(matchingDashboardActualPage({...page,coverage:'unavailable',items:[],total:0,known_total:0},selection,actualQuery).total,0);
  for (const patch of [{quarter:3},{kind:'collection'},{amount:0},{raw_amount:'SQL details'}]) {
    assert.throws(()=>matchingDashboardActualPage({...page,items:[{...page.items[0],...patch}]},selection,actualQuery),/实际金额/);
  }
  assert.throws(()=>matchingDashboardActualPage(page,{...selection,quarter:3},actualQuery),/范围或季度/);
  assert.throws(()=>matchingDashboardMetricPage(page,selection,actualQuery),/明细或分页/);
  let request;
  const service=createDashboardMonitorApi({getIdentity:()=> 'tenant:self',getClient:()=>({request:async options=>{request=options;return page;}})});
  await service.actuals(selection,actualQuery);assert.match(request.path,/^\/dashboard\/metrics\/actuals\?/);assert.equal(request.method,'GET');
});

test('five-card contract requires adopted actual basis and matching tax basis, without changing Review metric definitions',()=>{
  const value=overview();
  assert.equal(matchingDashboardMetrics(value,selection).metrics.length,5);
  for (const patch of [{basis:'latest_sales_forecast'},{tax_basis:'tax_inclusive'},{registered_period_count:-1}]) {
    const altered=overview();Object.assign(altered.metrics[3],patch);assert.throws(()=>matchingDashboardMetrics(altered,selection),/季度监控指标|实际金额登记/);
  }
  assert.throws(()=>matchingDashboardMetrics({...value,values_basis:'current_authorized_forecast_snapshot'},selection),/数据不完整/);
});

test('actual amounts occupy an independent row with clear zero, unknown and partial states, not forecast target cards',()=>{
  const value=overview();
  Object.assign(value.metrics[3],{value:'0.00',known_subtotal:'0.00'});
  Object.assign(value.metrics[4],{value:null,known_subtotal:null,coverage:'unavailable',missing_reasons:['actual_not_registered']});
  const html=render({value}),actual=html.slice(html.indexOf('class="ds-dashboard-actuals"'));
  assert.match(actual,/实际不含税确收/);assert.match(actual,/实际回款/);assert.match(actual,/>0\.00</);assert.match(actual,/>—</);assert.match(actual,/>未登记</);
  assert.doesNotMatch(actual,/预计达成率|有效目标|当前预测|季度时间进度|昨日/);
  assert.equal((html.match(/季度时间进度/g)||[]).length,3);
  assert.match(html,/预测确收（含税）/);
  Object.assign(value.metrics[3],{coverage:'partial',value:null,known_subtotal:'120000.01',missing_reasons:['actual_incomplete','actual_tax_basis_unresolved']});
  const partial=render({value});assert.match(partial,/已登记小计/);assert.match(partial,/12\.00/);assert.match(partial,/税额口径待确认/);
});

const actualCode=buildSync({absWorkingDir:root,entryPoints:['department-ui/DashboardActuals.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty','.svg':'dataurl'}}).outputFiles[0].text;
const actualModule=new Module(root+'dashboard-actual-test.cjs');actualModule.filename=root+'dashboard-actual-test.cjs';actualModule.paths=Module._nodeModulePaths(root);actualModule._compile(actualCode,actualModule.filename);
test('actual detail columns preserve customer-only identity, sources and partial tax state without exposing machine codes',()=>{
  const row=actualPage().items[0],columns=actualModule.exports.dashboardActualColumns('actual_recognized_exclusive');
  const renderRow=value=>columns.map(col=>renderToStaticMarkup(React.createElement('div',null,col.render(col.dataIndex?value[col.dataIndex]:null,value)))).join('');
  const html=renderRow(row);assert.match(html,/客户甲/);assert.match(html,/客户级记录 · 未关联商机/);assert.match(html,/季度汇总记录/);assert.match(html,/实绩登记/);assert.match(html,/>0\.00</);
  const partial=renderRow({...row,complete:false,tax_unresolved:true,amount:'1000',status:'partial',source:'private_field'});
  assert.match(partial,/已登记小计/);assert.match(partial,/不含税口径待确认/);assert.doesNotMatch(partial,/private_field|阶段概率|回款信心/);
  assert.equal(actualModule.exports.actualCoverageText({missing_reasons:['private_field']}),'暂不可计算');
  const note=actualModule.exports.actualCoverageText({incomplete_period_count:2,tax_unknown_period_count:2,missing_material_count:1,missing_reasons:['actual_incomplete','actual_tax_basis_unresolved']});
  assert.match(note,/2 项季度记录尚未完整登记/);assert.match(note,/2 项季度记录的不含税口径待确认/);assert.doesNotMatch(note,/5 项|共 [45]/);
});
