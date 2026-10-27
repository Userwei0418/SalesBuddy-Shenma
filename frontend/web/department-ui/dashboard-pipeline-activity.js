import {api,identity} from './pc/data.js';
import {dashboardMonitorKey} from './dashboard-monitor.js';
import {isDecimal,money,exactMoney,percentage} from './review/overview-model.js';

export const PIPELINE_METRICS=[
  {code:'forecast_collection',label:'预测回款',rule:'回款信心为高'},
  {code:'forecast_recognized',label:'预测确收（含税）',rule:'阶段概率 ≥70%'},
];
const coverages=['complete','partial','unavailable'];
const stages=['30','50','70','90','100','unknown'];
const count=value=>Number.isInteger(value)&&value>=0;
const sorted=values=>[...new Set(values || [])].sort();
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const validDate=value=>typeof value==='string'&&/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)&&Number.isFinite(Date.parse(value));
const REASONS={source_scope_not_fully_authorized:'仅展示当前权限可见的商机和跟进，不能代表整个公司的情况',
  currency_unavailable:'部分金额的币种缺失或非人民币，暂不计入人民币汇总',unknown_eligibility:'部分商机的阶段或回款信心待补充或核对',
  missing_forecast_amount:'部分商机未填写本季度预测金额',missing_forecast:'部分商机尚未填写本季度预测',missing_amount:'部分预测金额未填写',
  followup_time_unavailable:'部分跟进日期待核对，暂不能判断是否超过 7 天',
  follow_up_visibility_incomplete:'部分商机的跟进查看范围或日期不完整，暂不能判断是否超过 7 天',stage_unrecognized:'部分商机的阶段待核对，单独列示'};
export const analysisWarnings=value=>[...new Set((value?.missing_reasons || []).map(reason=>REASONS[reason] || '部分记录待核对，暂不计入完整统计'))].join('；');

// These controls are deliberately independent of the legacy multi-quarter BI controls.
export function analysisSelection(data,choice,period) {
  const canTeam=Boolean(data.canViewTeam),personal=!canTeam||choice?.mode==='personal';
  const teams=(data.teamOptions || []).filter(row=>row.id!=='all').map(row=>row.id);
  const team=teams.includes(choice?.teamId)?choice.teamId:'all';
  const member=(data.memberOptions || []).some(row=>row.id===choice?.memberId)?choice.memberId:null;
  return {personal,member_id:personal&&canTeam?member:null,team_groups:personal?[]:sorted(team==='all'?teams:[team]),year:period.year,quarter:period.quarter};
}
export const analysisKey=dashboardMonitorKey;

function envelope(value,selection,contract) {
  const returned=value?.selection,scope=value?.scope,period=value?.quarter;
  const kinds=selection.personal?(selection.member_id?['self','member']:['self']):['team','authorized'];
  if(value?.contract_version!==contract||!returned||returned.personal!==selection.personal||
    (selection.member_id&&returned.member_id!==selection.member_id)||(!selection.personal&&returned.member_id!=null)||
    !Array.isArray(returned.team_groups)||!same(sorted(returned.team_groups),sorted(selection.team_groups))||
    !kinds.includes(scope?.kind)||typeof scope?.label!=='string'||period?.year!==selection.year||period?.quarter!==selection.quarter||
    !validDate(value.data_as_of)||!coverages.includes(value.coverage)||!Array.isArray(value.missing_reasons)) {
    throw Error('图表范围、季度或数据格式已变化，请刷新后重试');
  }
  return value;
}

export function matchingPipeline(value,selection) {
  envelope(value,selection,'dashboard.pipeline.v1');
  if(value.basis!=='latest_sales_forecast'||!Array.isArray(value.stages)||
    !['eligible_count','excluded_count','unknown_eligibility_count'].every(key=>count(value[key]))||
    new Set(value.stages.map(row=>row.code)).size!==value.stages.length) throw Error('PPL 阶段统计不完整，请重新读取');
  for(const row of value.stages) {
    if(!stages.includes(row.code)||typeof row.label!=='string'||!count(row.count)||
      (row.code==='unknown'?row.probability!==null:row.probability!==Number(row.code))) throw Error('PPL 阶段统计不完整，请重新读取');
    for(const {code} of PIPELINE_METRICS) {
      const metric=row.metrics?.[code];
      if(!metric||!coverages.includes(metric.coverage)||!Array.isArray(metric.missing_reasons)||metric.unit!=='元'||metric.currency!=='CNY'||
        metric.tax_basis!==(code==='forecast_recognized'?'tax_inclusive':'cash_collection_not_applicable')||
        !['eligible_count','excluded_count','unknown_eligibility_count','known_amount_count','missing_amount_count'].every(key=>count(metric[key]))||
        ['value','known_subtotal'].some(key=>metric[key]!=null&&!isDecimal(metric[key]))||
        (metric.coverage!=='complete'&&metric.value!=null)) throw Error('PPL 阶段金额不完整，请重新读取');
    }
  }
  if(value.stages.reduce((sum,row)=>sum+row.count,0)!==value.eligible_count) throw Error('PPL 阶段数量不一致，请重新读取');
  return value;
}

export function stageAmount(metric) {
  const amount=metric.value??metric.known_subtotal;
  const missing=metric.missing_amount_count,unknown=metric.unknown_eligibility_count;
  const note=[missing?`${missing} 个金额待补充`:null,unknown?`${unknown} 个纳入条件待补充`:null,
    metric.excluded_count?`${metric.excluded_count} 个不符合纳入条件`:null,analysisWarnings(metric)].filter(Boolean).join('；');
  if(metric.eligible_count===0&&metric.excluded_count>0&&!unknown) return {amount:null,value:null,text:'未纳入',note};
  if(metric.eligible_count===0&&unknown>0) return {amount:null,value:null,text:'待补充',note};
  if(amount==null) return {amount:null,value:null,text:missing?'未填写':'待核对',note};
  const subtotal=metric.value==null;
  return {amount,value:Number(amount)/10000,text:`${subtotal?'已知小计 ':''}${money(amount)}`,exact:exactMoney(amount),note};
}

export function pipelineTable(value,selected='') {
  return value.stages.filter(row=>!selected||row.code===selected).map(row=>({
    ...row,amounts:Object.fromEntries(PIPELINE_METRICS.map(metric=>[metric.code,stageAmount(row.metrics[metric.code])])),
  }));
}

function activityWindow(window) {
  return window?.days===7&&window.timezone==='Asia/Shanghai'&&
    ['start','end_exclusive','as_of'].every(key=>validDate(window[key]))&&
    Date.parse(window.end_exclusive)-Date.parse(window.start)===7*86400000&&
    Date.parse(window.as_of)>=Date.parse(window.start)&&Date.parse(window.as_of)<Date.parse(window.end_exclusive);
}

export function matchingActivity(value,selection) {
  envelope(value,selection,'dashboard.activity.v1');
  if(value.basis!=='quarter_positive_sales_forecast'||!activityWindow(value.window)||
    !['active_count','recent_count','overdue_count','never_count','unknown_count'].every(key=>count(value[key]))||
    value.recent_count+value.overdue_count+value.unknown_count!==value.active_count||value.never_count>value.overdue_count) {
    throw Error('季度活跃商机跟进统计不完整，请重新读取');
  }
  return value;
}

export function matchingActivityPage(value,selection,query) {
  envelope(value,selection,'dashboard.activity_page.v1');
  if(value.basis!=='quarter_positive_sales_forecast'||!activityWindow(value.window)||value.limit!==query.limit||value.offset!==query.offset||
    !count(value.known_total)||(value.total!=null&&(!count(value.total)||value.total!==value.known_total))||
    !Array.isArray(value.items)||value.items.length>query.limit||typeof value.has_more!=='boolean'||
    value.has_more!==(query.offset+value.items.length<value.known_total)||new Set(value.items.map(row=>row.id)).size!==value.items.length) {
    throw Error('未跟进明细或分页已变化，请重新读取');
  }
  for(const row of value.items) {
    if(typeof row.id!=='string'||typeof row.name!=='string'||typeof row.customer_id!=='string'||row.can_read!==true||
      !['overdue','never'].includes(row.follow_up_status)||
      (row.follow_up_status==='never'?(row.last_follow_up_at!==null||row.days_since_follow_up!==null):
        (!validDate(row.last_follow_up_at)||!count(row.days_since_follow_up)||row.days_since_follow_up<7||Date.parse(row.last_follow_up_at)>=Date.parse(value.window.start)))||
      ['collection_amount','recognized_amount'].some(key=>row[key]!=null&&!isDecimal(row[key]))) {
      throw Error('未跟进明细日期或金额格式不完整，请重新读取');
    }
  }
  return value;
}

export function matchingActivitySnapshot(page,summary) {
  if(page.window.start!==summary.window.start||page.window.end_exclusive!==summary.window.end_exclusive||
    page.known_total!==summary.overdue_count)throw Error('跟进数据已变化，请刷新图表与明细');
  return page;
}

export function activityPresentation(value) {
  const rateAvailable=value.coverage==='complete'&&value.unknown_count===0&&value.active_count>0;
  return {rate:rateAvailable?percentage(String(value.recent_count/value.active_count)):'—',rateAvailable,
    parts:[{label:'近 7 天已跟进',count:value.recent_count},{label:'超 7 天或尚未跟进',count:value.overdue_count},
      ...(value.unknown_count?[{label:'跟进情况待核对',count:value.unknown_count}]:[])],
    windowLabel:`${new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'numeric',day:'numeric'}).format(new Date(value.window.start))}起的近 7 天（含今天）`,
  };
}

export function createDashboardAnalysisApi({getClient=api,getIdentity=identity}={}) {
  const read=async(path,selection,query={})=>{
    const owner=getIdentity();if(!owner)throw Error('请先登录后查看经营分析');
    const params=new URLSearchParams();
    for(const [key,value] of Object.entries({...selection,...query})) {
      if(value===null||value===undefined||value==='')continue;
      for(const item of Array.isArray(value)?value:[value])params.append(key,String(item));
    }
    try {
      const value=await getClient().request({path:`/dashboard/metrics/${path}?${params}`,method:'GET',fresh:true});
      if(getIdentity()!==owner)throw Error('身份或权限已变化，请重新打开经营看板');
      return value;
    } catch(error) {
      if(getIdentity()!==owner)throw Error('身份或权限已变化，请重新打开经营看板');
      if(Number(error?.statusCode)===403)throw Error('当前账号无权查看所选范围，请重新选择');
      if(Number(error?.statusCode)===404)throw Error('图表服务尚未就绪，请确认后端已同步');
      throw error;
    }
  };
  return {
    pipeline:async selection=>matchingPipeline(await read('pipeline',selection),selection),
    activity:async selection=>matchingActivity(await read('activity',selection),selection),
    overdue:async(selection,query)=>matchingActivityPage(await read('activity/opportunities',selection,query),selection,query),
  };
}
export const dashboardAnalysisApi=createDashboardAnalysisApi();
