import {api,identity} from './pc/data.js';
import {isDecimal,money,exactMoney,percentage} from './review/overview-model.js';

export const PERFORMANCE_METRICS=[
  {code:'forecast_collection',label:'预测回款',basis:'latest_sales_forecast',taxBasis:'cash_collection_not_applicable'},
  {code:'collection_ppl',label:'回款 PPL 储备',basis:'latest_sales_forecast',taxBasis:'cash_collection_not_applicable'},
  {code:'forecast_recognized_exclusive',label:'预测不含税确收',reference:true,basis:'material_reference',taxBasis:'tax_exclusive'},
  {code:'recognized_pipeline_exclusive',label:'确收 Pipeline',reference:true,basis:'material_reference',taxBasis:'tax_exclusive'},
  {code:'actual_recognized_exclusive',label:'实际不含税确收',actual:true,basis:'adopted_actuals',taxBasis:'tax_exclusive'},
  {code:'actual_collection',label:'实际回款',actual:true,basis:'adopted_actuals',taxBasis:'cash_collection_not_applicable'},
];
const coverages=['complete','partial','unavailable'];
const count=value=>Number.isInteger(value)&&value>=0;
const targetStatuses=['effective','no_target','permission_denied','not_configured'];
const metricFor=code=>PERFORMANCE_METRICS.find(row=>row.code===code);
const decimalOrNull=value=>value===null||isDecimal(value);
const positive=value=>isDecimal(value)&&/^\d/.test(value)&&/[1-9]/.test(value);

export function performanceRankingQuery(period,metric_code) {
  if(!Number.isInteger(period?.year)||period.year<2000||period.year>2100||![1,2,3,4].includes(period.quarter)||!metricFor(metric_code)) throw Error('请选择有效的排名季度和指标');
  // The main dashboard's selection highlights rows; it never reduces or widens the server's authorized cohort.
  return {year:period.year,quarter:period.quarter,metric_code};
}
export const performanceRankingKey=query=>JSON.stringify([query.year,query.quarter,query.metric_code]);

export function matchingPerformanceRankings(value,query) {
  const metric=metricFor(query.metric_code),cohort=value?.cohort;
  if(value?.contract_version!=='dashboard.performance_rankings.v1'||value.year!==query.year||value.quarter!==query.quarter||value.metric_code!==query.metric_code||
    value.unit!=='元'||value.currency!=='CNY'||value.tax_basis!==metric?.taxBasis||
    (metric.basis&&value.basis!==metric.basis)||typeof value.basis!=='string'||
    typeof value.data_as_of!=='string'||!Number.isFinite(Date.parse(value.data_as_of))||
    !['rate','amount'].includes(value.department_sort_basis)||!['comparable_targets','no_comparable_targets'].includes(value.department_sort_reason)||
    !coverages.includes(value.departments_coverage)||!coverages.includes(value.people_coverage)||
    !['attribution_unknown_period_count','department_excluded_fact_count','people_excluded_fact_count'].every(key=>count(value[key]))||
    cohort?.scope!=='current_authorized_ranking_scope'||!count(cohort.departments_count)||!count(cohort.people_count)||
    !Array.isArray(value.definitions)||value.definitions.some(text=>typeof text!=='string')||
    !Array.isArray(value.departments)||!Array.isArray(value.people)||value.departments.length!==cohort.departments_count||value.people.length!==cohort.people_count) {
    throw Error('排名指标、季度或可见范围已变化，请重新读取');
  }
  if((value.department_sort_basis==='rate')!==(value.department_sort_reason==='comparable_targets')) throw Error('部门排名口径不一致，请重新读取');
  for(const [kind,rows] of [['departments',value.departments],['people',value.people]]) {
    if(new Set(rows.map(row=>row?.id)).size!==rows.length) throw Error('排名名单重复，请重新读取');
    for(const row of rows) {
      const target=row?.target,amount=row?.value??row?.known_subtotal;
      if(!row||typeof row.id!=='string'||!row.id||typeof row.name!=='string'||!coverages.includes(row.coverage)||
        !['value','known_subtotal','rate'].every(key=>decimalOrNull(row[key]))||
        !count(row.missing_amount_count)||!count(row.unknown_eligibility_count)||!Array.isArray(row.missing_reasons)||
        row.missing_reasons.some(reason=>typeof reason!=='string')||typeof row.provisional!=='boolean'||
        (row.rank!==null&&(!Number.isInteger(row.rank)||row.rank<1))||row.rank_basis!==(kind==='departments'?value.department_sort_basis:'amount')||
        !target||!targetStatuses.includes(target.status)||!coverages.includes(target.coverage)||
        !decimalOrNull(target.value)||!decimalOrNull(target.known_subtotal)) throw Error('排名数据不完整，请重新读取');
      if((row.coverage==='complete'&&!isDecimal(row.value))||(row.coverage!=='complete'&&row.value!==null)||
        (amount==null&&row.rank!==null)||(row.coverage!=='complete'&&row.rank!==null&&!row.provisional)||
        (target.status==='effective'&&!isDecimal(target.value))||(target.status!=='effective'&&target.value!==null)||
        (target.status==='permission_denied'&&(target.value!==null||target.known_subtotal!==null||row.rate!==null))||
        (row.rate!==null&&(!positive(target.value)||target.status!=='effective'||amount==null))||
        (row.rank_basis==='rate'&&row.rank!==null&&row.rate===null)) throw Error('排名金额或目标状态不一致，请重新读取');
    }
  }
  return value;
}

export const PERFORMANCE_REASONS={
  missing_forecast:'本季度预测尚未填写',missing_forecast_amount:'本季度预测金额未填写',missing_amount:'金额尚未填写',
  unknown_eligibility:'纳入条件待补充',currency_unavailable:'币种缺失或非人民币',source_scope_not_fully_authorized:'部分来源不在可见范围',
  actual_not_registered:'本季度实际金额未登记',actual_incomplete:'实际金额登记不完整',actual_attribution_unresolved:'历史实绩归属待核对',
  actual_tax_basis_unresolved:'历史实绩税额口径待核对',actual_source_conflict:'实际记录来源待核对',actual_withdrawn:'部分登记已撤回',
  material_lines_missing:'尚未填写交易物料',material_tax_rate_missing:'物料冻结税率缺失',material_tax_rate_invalid:'物料冻结税率待核对',
  material_schedule_missing:'物料交付或服务周期未填写',material_manual_input_required:'物料采用手填口径，无法计算季度参考',
  material_reference_incomplete:'物料参考信息未齐',material_reference_invalid:'物料参考信息待核对',material_trade_relation_missing:'交易关系未填写',
};
export function performanceAmount(row) {
  const amount=row.value??row.known_subtotal,partial=row.coverage!=='complete';
  const reasons=new Set(row.missing_reasons||[]);
  const unresolved=[...reasons].some(reason=>/unresolved|conflict|scope|currency/.test(reason));
  const missingText=unresolved?'待核对':reasons.has('actual_not_registered')?'未登记':row.unknown_eligibility_count||row.missing_amount_count?'数据未齐':'暂无可用金额';
  return {text:amount==null?missingText:`${partial?'已知小计 ':''}${money(amount)}`,exact:amount==null?undefined:exactMoney(amount),known:amount!=null};
}
export function performanceTarget(target) {
  if(target.status==='permission_denied')return '不可查看';
  if(target.status==='not_configured')return '未配置此目标';
  if(target.status==='no_target')return target.known_subtotal==null?'未设置':`已知小计 ${money(target.known_subtotal)}`;
  return money(target.value);
}
export function performanceRate(row) {
  if(row.rate!=null)return `${percentage(row.rate)}${row.provisional?'（暂估）':''}`;
  if(row.target.status==='permission_denied')return '不可查看';
  if(row.target.status==='no_target')return '未设置目标';
  if(row.target.status==='not_configured')return '未配置此目标';
  return positive(row.target.value)?'待核对':'目标为 0';
}
export function performanceRowNote(row) {
  const notes=[row.missing_amount_count?`${row.missing_amount_count} 项金额待补充`:null,
    row.unknown_eligibility_count?`${row.unknown_eligibility_count} 项纳入条件待核对`:null,
    ...(row.missing_reasons||[]).map(reason=>PERFORMANCE_REASONS[reason]||'部分记录待核对')];
  return [...new Set(notes.filter(Boolean))].join('；');
}
export function highlightedRankingRow(row,kind,data,ownUserId) {
  if(kind==='people')return row.id===(data.selectedMemberId||ownUserId);
  return (data.selectedTeamGroups||[]).some(id=>id===row.id||id===`team:${row.id}`||(row.team_id&&(id===row.team_id||id===`team:${row.team_id}`)));
}

export function createPerformanceRankingApi({getClient=api,getIdentity=identity}={}) {
  return {rankings:async query=>{
    const owner=getIdentity();if(!owner)throw Error('请先登录后查看业绩排名');
    const selection=performanceRankingQuery(query,query.metric_code),params=new URLSearchParams(selection);
    try {
      const value=await getClient().request({path:`/dashboard/metrics/rankings?${params}`,method:'GET',fresh:true});
      if(getIdentity()!==owner)throw Error('身份或权限已变化，请重新打开经营看板');
      return matchingPerformanceRankings(value,selection);
    } catch(error) {
      if(getIdentity()!==owner)throw Error('身份或权限已变化，请重新打开经营看板');
      if(Number(error?.statusCode)===403)throw Error('当前账号无权查看业绩排名');
      throw error;
    }
  }};
}
export const performanceRankingApi=createPerformanceRankingApi();
