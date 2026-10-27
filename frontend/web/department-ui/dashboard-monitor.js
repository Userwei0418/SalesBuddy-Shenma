import {api,identity} from './pc/data.js';
import {isDecimal,REVIEW_METRICS} from './review/overview-model.js';

// Dashboard definitions are separate from the work-report Review cards.
export const DASHBOARD_FORECAST_METRICS = REVIEW_METRICS.map(row=>({...row,
  label:row.code==='forecast_recognized'?'预测确收（含税）':row.label,basis:'latest_sales_forecast'}));
export const DASHBOARD_ACTUAL_METRICS = [
  {code:'actual_recognized_exclusive',label:'实际不含税确收',kind:'recognized',basis:'adopted_actuals',taxBasis:'tax_exclusive',definition:'所选季度已采用的实际不含税确收；仅计入已登记、可核实的金额，未知不补为零。'},
  {code:'actual_collection',label:'实际回款',kind:'collection',basis:'adopted_actuals',taxBasis:'cash_collection_not_applicable',definition:'所选季度已采用的实际回款；明确登记为零与尚未登记分开显示。'},
];
export const DASHBOARD_METRICS = [...DASHBOARD_FORECAST_METRICS,...DASHBOARD_ACTUAL_METRICS];
export const isDashboardActual = code => DASHBOARD_ACTUAL_METRICS.some(row=>row.code===code);
const coverages = ['complete','partial','unavailable'];
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const sorted = values => [...new Set(values || [])].sort();

export function currentMonitorQuarter(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Shanghai',year:'numeric',month:'numeric'}).formatToParts(now);
  const part = name => Number(parts.find(row=>row.type===name).value);
  return {year:part('year'),quarter:Math.ceil(part('month')/3)};
}

// Only the dashboard's existing scope goes into this independent quarterly read.
// Its multi-quarter chart choices never replace the monitor's selected quarter.
export function dashboardMonitorSelection(data,period) {
  const personal = !data.canViewTeam || data.viewMode !== 'team';
  return {personal,member_id:personal ? data.selectedMemberId || null : null,
    team_groups:personal ? [] : sorted(data.selectedTeamGroups),year:period.year,quarter:period.quarter};
}
export const dashboardMonitorKey = selection => JSON.stringify([selection.personal,selection.member_id || null,sorted(selection.team_groups),selection.year,selection.quarter]);
const DETAIL_REASONS = {missing_forecast_amount:'本季度预测金额未填写',missing_amount:'预测金额未填写',missing_forecast:'尚未填写本季度预测',
  unknown_eligibility:'阶段概率或回款信心尚未填写，暂不能确定是否纳入',currency_unavailable:'币种缺失或非人民币，暂不能纳入汇总',
  source_scope_not_fully_authorized:'部分来源不在当前可见范围',
  actual_not_registered:'本季度存在未登记金额，未将缺失值计为零',actual_incomplete:'部分实际金额尚未登记完整，仅展示已知小计',
  actual_attribution_unresolved:'部分历史实绩归属待核对，暂不计入个人或部门业绩',
  actual_tax_basis_unresolved:'部分记录的税额口径待确认，暂不能计入完整的不含税确收',
  actual_source_conflict:'部分实际记录来源待核对',actual_withdrawn:'部分原登记已撤回，不计入当前采用金额',not_eligible:'不符合本指标纳入条件'};
export const dashboardMetricReason = reason => DETAIL_REASONS[reason] || '暂不可计算';

function matchingEnvelope(value,selection) {
  const returned = value?.selection,quarter = value?.quarter;
  const expectedKind = selection.personal ? selection.member_id ? ['self','member'] : ['self'] : ['team','authorized'];
  if (!returned || returned.personal !== selection.personal ||
      (selection.member_id && returned.member_id !== selection.member_id) ||
      (!selection.personal && returned.member_id!=null) || !expectedKind.includes(value?.scope?.kind) ||
      !Array.isArray(returned.team_groups) || !same(sorted(returned.team_groups),sorted(selection.team_groups)) ||
      quarter?.year !== selection.year || quarter?.quarter !== selection.quarter ||
      !isDecimal(quarter.time_progress) || Number(quarter.time_progress)<0 || Number(quarter.time_progress)>1 ||
      !value.scope || typeof value.scope.label !== 'string' || !['self','member','team','authorized'].includes(value.scope.kind) ||
      typeof value.data_as_of !== 'string' || !Number.isFinite(Date.parse(value.data_as_of))) {
    throw Error('监控范围或季度已变化，请重新读取');
  }
  return value;
}

export function matchingDashboardMetrics(value,selection) {
  matchingEnvelope(value,selection);
  if (value.contract_version !== 'dashboard.metrics.v1' || value.values_basis !== 'current_authorized_quarter_snapshot' || !Array.isArray(value.metrics)) throw Error('季度监控数据不完整，请重新读取');
  for (const {code,basis,taxBasis} of DASHBOARD_METRICS) {
    const matches = value.metrics.filter(row=>row.metric_code===code),row=matches[0],target=row?.target;
    if (matches.length!==1 || row.basis!==basis || (taxBasis && row.tax_basis!==taxBasis) || !coverages.includes(row.coverage) || !row.baseline || !target || !['effective','no_target','permission_denied'].includes(target.status)) throw Error('季度监控指标不完整，请重新读取');
    if (isDashboardActual(code) && ['registered_fact_count','registered_period_count','incomplete_period_count','missing_material_count','tax_unknown_period_count','conflict_period_count'].some(key=>!Number.isInteger(row[key]) || row[key]<0)) throw Error('实际金额登记状态不完整，请重新读取');
    for (const key of ['value','known_subtotal','rate','gap','day_change']) if (row[key]!=null && !isDecimal(row[key])) throw Error('季度监控金额格式异常，请重新读取');
    for (const key of ['value','known_subtotal']) if (target[key]!=null && !isDecimal(target[key])) throw Error('季度目标金额格式异常，请重新读取');
    if ((target.status==='effective' && !isDecimal(target.value)) || (target.status!=='effective' && target.value!=null) ||
        (target.status==='permission_denied' && (row.rate!=null || row.gap!=null))) throw Error('季度目标可见范围已变化，请重新读取');
    const partial = row.coverage==='partial' && isDecimal(row.known_subtotal) && ['known_subtotals','known_subtotals_and_targets'].includes(row.rate_basis);
    if (row.coverage!=='complete' && ((row.value!=null && !partial) || ((row.rate!=null || row.gap!=null) && !partial))) throw Error('部分数据不能作为完整监控指标，请重新读取');
  }
  return value;
}

export function matchingDashboardMetricPage(value,selection,query) {
  matchingEnvelope(value,selection);
  if (!DASHBOARD_FORECAST_METRICS.some(row=>row.code===query.metric_code) || value.contract_version!=='dashboard.metric_page.v1' || value.metric_code!==query.metric_code || value.limit!==query.limit || value.offset!==query.offset ||
      !Array.isArray(value.items) || value.items.length>query.limit || typeof value.has_more!=='boolean' ||
      !Number.isInteger(value.known_total) || value.known_total<0 || !coverages.includes(value.coverage) ||
      (value.total!=null && (!Number.isInteger(value.total) || value.total<0)) || (value.coverage!=='complete' && value.total!=null)) throw Error('监控明细或分页已变化，请重新读取');
  for (const row of value.items) {
    if (!row || typeof row.id!=='string') throw Error('监控明细数据不完整，请重新读取');
    const amount=row.metric_contributions?.[query.metric_code]?.value;
    if (amount!=null && !isDecimal(amount)) throw Error('监控明细金额格式异常，请重新读取');
  }
  return value;
}

export function matchingDashboardActualPage(value,selection,query) {
  matchingEnvelope(value,selection);
  const definition=DASHBOARD_ACTUAL_METRICS.find(row=>row.code===query.metric_code);
  if (!definition || value.contract_version!=='dashboard.actual_page.v1' || value.values_basis!=='current_authorized_quarter_snapshot' || value.basis!=='adopted_actuals' ||
      value.metric_code!==query.metric_code || value.limit!==query.limit || value.offset!==query.offset ||
      !Array.isArray(value.items) || value.items.length>query.limit || typeof value.has_more!=='boolean' ||
      !Number.isInteger(value.known_total) || value.known_total<0 || !coverages.includes(value.coverage) ||
      (value.total!=null && (!Number.isInteger(value.total) || value.total<0))) throw Error('实际金额明细或分页已变化，请重新读取');
  for (const row of value.items) {
    if (!row || typeof row.id!=='string' || typeof row.customer_id!=='string' || row.year!==selection.year || row.quarter!==selection.quarter || row.kind!==definition.kind ||
        (row.opportunity_id!=null && typeof row.opportunity_id!=='string') || typeof row.complete!=='boolean' || typeof row.tax_unresolved!=='boolean') throw Error('实际金额明细口径不完整，请重新读取');
    for (const field of ['amount','raw_amount']) if (row[field]!=null && !isDecimal(row[field])) throw Error('实际金额格式异常，请重新读取');
  }
  return value;
}

export function createDashboardMonitorApi({getClient=api,getIdentity=identity}={}) {
  const read=async(path,selection,query={})=>{
    const owner=getIdentity();
    if (!owner) throw Error('请先登录后查看季度监控');
    const params=new URLSearchParams();
    for (const [key,value] of Object.entries({...selection,...query})) {
      if (value===null || value===undefined || value==='') continue;
      for (const item of Array.isArray(value)?value:[value]) params.append(key,String(item));
    }
    try {
      const value=await getClient().request({path:`/dashboard/metrics${path}?${params}`,method:'GET',fresh:true});
      if (getIdentity()!==owner) throw Error('身份或权限已变化，请重新打开经营看板');
      return value;
    } catch(error) {
      if (getIdentity()!==owner) throw Error('身份或权限已变化，请重新打开经营看板');
      if (Number(error?.statusCode)===403) throw Error('当前账号无权查看所选监控范围，请重新选择');
      throw error;
    }
  };
  return {metrics:async selection=>matchingDashboardMetrics(await read('',selection),selection),
    opportunities:async(selection,query)=>matchingDashboardMetricPage(await read('/opportunities',selection,query),selection,query),
    actuals:async(selection,query)=>matchingDashboardActualPage(await read('/actuals',selection,query),selection,query)};
}
export const dashboardMonitorApi=createDashboardMonitorApi();
