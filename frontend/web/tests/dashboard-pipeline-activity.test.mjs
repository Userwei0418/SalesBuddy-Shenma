import test from 'node:test';
import assert from 'node:assert/strict';
import {analysisSelection,analysisKey,matchingPipeline,pipelineTable,stageAmount,matchingActivity,matchingActivityPage,matchingActivitySnapshot,activityPresentation,createDashboardAnalysisApi} from '../department-ui/dashboard-pipeline-activity.js';

const selection={personal:false,member_id:null,team_groups:['team:a'],year:2026,quarter:4};
const envelope=()=>({selection:{...selection},scope:{kind:'team',label:'所选团队'},quarter:{year:2026,quarter:4},data_as_of:'2026-10-25T07:00:00+08:00',coverage:'complete',missing_reasons:[]});
const metric=(code,overrides={})=>({eligible_count:1,excluded_count:0,unknown_eligibility_count:0,value:'0.00',known_subtotal:'0.00',known_amount_count:1,missing_amount_count:0,coverage:'complete',missing_reasons:[],unit:'元',currency:'CNY',tax_basis:code==='forecast_recognized'?'tax_inclusive':'cash_collection_not_applicable',...overrides});
const pipeline=()=>({...envelope(),contract_version:'dashboard.pipeline.v1',basis:'latest_sales_forecast',eligible_count:2,excluded_count:1,unknown_eligibility_count:0,
  stages:[{code:'30',label:'需求明确',probability:30,count:1,metrics:{forecast_collection:metric('forecast_collection',{value:'0.01',known_subtotal:'0.01'}),forecast_recognized:metric('forecast_recognized',{eligible_count:0,excluded_count:1,known_amount_count:0})}},
    {code:'70',label:'商务推进',probability:70,count:1,metrics:{forecast_collection:metric('forecast_collection'),forecast_recognized:metric('forecast_recognized')}}]});

test('new chart scope defaults to authorized teams, remains independent and cannot retain a removed member',()=>{
  const data={canViewTeam:true,viewMode:'personal',selectedTeamGroups:['legacy'],teamOptions:[{id:'all'},{id:'team:b'},{id:'team:a'}],memberOptions:[{id:'m1'}]};
  const period={year:2026,quarter:4};
  assert.deepEqual(analysisSelection(data,null,period),{...selection,team_groups:['team:a','team:b']});
  assert.deepEqual(analysisSelection(data,{mode:'personal',memberId:'m1'},period),{...selection,personal:true,team_groups:[],member_id:'m1'});
  assert.equal(analysisSelection(data,{mode:'personal',memberId:'removed'},period).member_id,null);
  assert.deepEqual(analysisSelection({...data,canViewTeam:false},{mode:'team',memberId:'m1'},period),{...selection,personal:true,team_groups:[],member_id:null});
  assert.equal(analysisKey(selection),analysisKey({...selection,team_groups:['team:a','team:a']}));
  assert.notEqual(analysisKey(selection),analysisKey({...selection,quarter:3}));
});

test('pipeline validates identity scope, quarter, financial basis and count rather than trusting a broader response',()=>{
  assert.equal(matchingPipeline(pipeline(),selection).eligible_count,2);
  for(const change of [v=>v.selection.personal=true,v=>v.selection.team_groups=['team:b'],v=>v.quarter.quarter=3,v=>v.scope.kind='self',v=>v.eligible_count=3,
    v=>v.stages[0].metrics.forecast_recognized.tax_basis='tax_exclusive',v=>v.stages[0].metrics.forecast_collection.value=0]) {
    const value=pipeline();change(value);assert.throws(()=>matchingPipeline(value,selection),/范围|不完整|不一致/);
  }
});

test('stage amount views distinguish noneligible, unknown, missing, partial and explicit zero with exact cents',()=>{
  assert.equal(stageAmount(metric('forecast_collection')).text,'0.00');
  assert.equal(stageAmount(metric('forecast_collection',{value:'0.01',known_subtotal:'0.01'})).text,'<0.01');
  assert.equal(stageAmount(metric('forecast_collection',{value:'0.01',known_subtotal:'0.01'})).exact,'0.01 元');
  const partial=stageAmount(metric('forecast_collection',{value:null,known_subtotal:'12345.67',coverage:'partial',eligible_count:2,missing_amount_count:1}));
  assert.equal(partial.text,'已知小计 1.23');assert.equal(partial.exact,'12,345.67 元');assert.match(partial.note,/1 个金额待补充/);
  const missing=stageAmount(metric('forecast_collection',{value:null,known_subtotal:null,coverage:'unavailable',known_amount_count:0,missing_amount_count:1}));
  assert.equal(missing.text,'未填写');assert.equal(missing.value,null);
  assert.equal(stageAmount(metric('forecast_collection',{eligible_count:0,excluded_count:1})).text,'未纳入');
  assert.equal(stageAmount(metric('forecast_collection',{eligible_count:0,unknown_eligibility_count:1})).text,'待补充');
  const value=pipeline();assert.equal(pipelineTable(value,'70').length,1);assert.equal(value.stages.length,2);
});

test('dedicated fresh GETs retain repeated authorized team keys and reject late account responses',async()=>{
  let owner='a:member',finish;const calls=[];
  const service=createDashboardAnalysisApi({getIdentity:()=>owner,getClient:()=>({request:async options=>{calls.push(options);return pipeline();}})});
  await service.pipeline(selection);assert.equal(calls[0].method,'GET');assert.ok(calls[0].fresh);
  assert.match(calls[0].path,/^\/dashboard\/metrics\/pipeline\?/);assert.equal(new URLSearchParams(calls[0].path.split('?')[1]).get('team_groups'),'team:a');
  const delayed=createDashboardAnalysisApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>finish=resolve)})});
  const pending=delayed.pipeline(selection);owner='b:member';finish(pipeline());await assert.rejects(pending,/身份或权限/);
});

const window=()=>({days:7,timezone:'Asia/Shanghai',start:'2026-10-19T00:00:00+08:00',end_exclusive:'2026-10-26T00:00:00+08:00',as_of:'2026-10-25T07:00:00+08:00'});
const activity=()=>({...envelope(),contract_version:'dashboard.activity.v1',basis:'quarter_positive_sales_forecast',window:window(),active_count:4,recent_count:1,overdue_count:2,never_count:1,unknown_count:1});
const page=()=>({...envelope(),contract_version:'dashboard.activity_page.v1',basis:'quarter_positive_sales_forecast',window:window(),limit:20,offset:0,total:2,known_total:2,has_more:false,
  items:[{id:'o1',name:'季度活跃商机',customer_id:'c1',can_read:true,follow_up_status:'overdue',last_follow_up_at:'2026-10-18T10:00:00+08:00',days_since_follow_up:7,collection_amount:'0.00',recognized_amount:'12345.67'},
    {id:'o2',name:'尚未跟进商机',customer_id:'c1',can_read:true,follow_up_status:'never',last_follow_up_at:null,days_since_follow_up:null,collection_amount:'10000',recognized_amount:null}]});

test('activity treats never as an overdue subset, keeps unknown separate, and never divides by zero',()=>{
  const value=matchingActivity(activity(),selection),view=activityPresentation(value);
  assert.equal(view.rate,'—');assert.equal(view.parts.reduce((sum,row)=>sum+row.count,0),4);
  assert.equal(view.parts.at(-1).label,'跟进情况待核对');
  assert.equal(activityPresentation({...value,unknown_count:0,overdue_count:3}).rate,'25.0%');
  assert.equal(activityPresentation({...value,unknown_count:0,overdue_count:3,coverage:'partial'}).rate,'—');
  assert.equal(activityPresentation({...value,active_count:0,recent_count:0,overdue_count:0,never_count:0,unknown_count:0}).rate,'—');
  assert.throws(()=>matchingActivity({...value,active_count:3},selection),/不完整/);
  assert.throws(()=>matchingActivity({...value,never_count:3},selection),/不完整/);
  assert.throws(()=>matchingActivity({...value,basis:'quarter_formal_followup'},selection),/不完整/);
});

test('paged overdue data must match selection/window/summary and never reveal unauthorized or recent records',()=>{
  const query={limit:20,offset:0};
  assert.equal(matchingActivitySnapshot(matchingActivityPage(page(),selection,query),activity()).items.length,2);
  for(const change of [v=>v.items[0].can_read=false,v=>v.items[0].last_follow_up_at='2026-10-24T10:00:00+08:00',v=>v.items[1].days_since_follow_up=0,v=>v.offset=20,v=>v.has_more=true]) {
    const value=page();change(value);assert.throws(()=>matchingActivityPage(value,selection,query),/不完整|已变化/);
  }
  assert.throws(()=>matchingActivitySnapshot({...page(),known_total:3},activity()),/数据已变化/);
  assert.throws(()=>matchingActivitySnapshot({...page(),window:{...window(),start:'2026-10-20T00:00:00+08:00'}},activity()),/数据已变化/);
  assert.equal(matchingActivityPage({...page(),coverage:'partial',total:null},selection,query).known_total,2);
});
