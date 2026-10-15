import {calendarHint,stable} from './state.js';
import {toPeriod} from './presentation.js';
import {todayKey} from '../pc/period.js';
import {overviewView} from './meeting-presentation.js';
import {SALES_WEEK_FACTS} from './sales-week.js';
import {taskActivityView} from './task-activity.js';

const uuid=value=>typeof value==='string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const hash=value=>typeof value==='string' && /^[0-9a-f]{64}$/i.test(value);
const fail=message=>{throw Error(message);};
export const OVERVIEW_REASONS={READ_ONLY:'当前账号可浏览，暂不可生成报告',SCOPE_NOT_GENERATABLE:'只能生成本人或所管理团队的报告',PROFILE_UNAVAILABLE:'当前报告类型暂不可生成',SOURCE_NOT_GENERATABLE:'本次材料不满足生成权限，请重新选择范围'};
export function overviewOptions(value) {
  if(value?.contract_version!=='work_report.overview_options.v1' || !Array.isArray(value.period_kinds) || !value.period_kinds.length || value.period_kinds.some(kind=>!['day','week','two_weeks','month','quarter'].includes(kind)) ||
    !Array.isArray(value.available_scope_kinds) || value.available_scope_kinds.some(kind=>!['self','member','team'].includes(kind)) ||
    !Array.isArray(value.members) || !Array.isArray(value.teams) || [...value.members,...value.teams].some(row=>!uuid(row.id) || typeof row.name!=='string') ||
    !value.generation || !Array.isArray(value.generation.profiles) || value.generation.profiles.some(profile=>!['general','sales_week_six'].includes(profile)))fail('工作报告概览服务尚未配套，请更新后重试');
  return value;
}
export function overviewScopes(options) {
  return [...(options.available_scope_kinds.includes('self')?[{value:'self',label:'本人'}]:[]),
    ...(options.available_scope_kinds.includes('team')?options.teams.map(row=>({value:`team:${row.id}`,label:`团队 · ${row.name}`})):[]),
    ...(options.available_scope_kinds.includes('member')?options.members.map(row=>({value:`member:${row.id}`,label:`成员 · ${row.name}`})):[])];
}
export function overviewProfile(period,options) {return period==='week' && options.generation.profiles.includes('sales_week_six')?'sales_week_six':'general';}
export function overviewRequest(filters,options,requestId=crypto.randomUUID()) {
  if(!uuid(requestId))fail('概览请求标识无效，请刷新');
  if(!calendarHint(filters.period,filters.anchor) || filters.anchor>todayKey() || !options.period_kinds.includes(toPeriod(filters.period)))fail('请选择当前可用周期内的有效日期');
  if(!overviewScopes(options).some(row=>row.value===filters.scopeKey))fail('请选择当前可查看的成员或团队');
  const [kind,target]=filters.scopeKey.split(':');
  const scope={kind,member_id:kind==='member'?target:null,team_id:kind==='team'?target:null};
  const profile=filters.profile || overviewProfile(filters.period,options);
  if(!['general','sales_week_six'].includes(profile) || (profile==='sales_week_six' && filters.period!=='week'))fail('销售六节周报仅适用于自然周');
  const selection={period:{kind:toPeriod(filters.period),anchor_date:filters.anchor,timezone:'Asia/Shanghai'},scope,generation_profile:profile};
  return {requestId,selection,query:{request_id:requestId,period_kind:selection.period.kind,anchor_date:filters.anchor,scope_kind:kind,
    ...(target?{[kind==='member'?'member_id':'team_id']:target}:{}),generation_profile:profile}};
}
export function matchingOverview(value,request) {
  const mismatch=()=>fail('概览结果与当前周期、范围或报告类型不一致，请重新读取');
  if(value?.contract_version!=='work_report.overview.v1' || value.request_id!==request.requestId || stable(value.selection)!==stable(request.selection) || !hash(value.selection_hash) || !hash(value.overview_hash))mismatch();
  if(value.period?.kind!==request.selection.period.kind || value.scope?.kind!==request.selection.scope.kind ||
    (value.scope.team_id || null)!==request.selection.scope.team_id || (value.scope.member_id || null)!==request.selection.scope.member_id ||
    stable(value.facts?.scope)!==stable(value.scope) || stable(value.facts?.period)!==stable(value.period) || value.facts?.data_as_of!==value.data_as_of ||
    !Number.isFinite(Date.parse(value.data_as_of)) || value.capabilities?.can_view!==true || typeof value.capabilities.can_generate!=='boolean')mismatch();
  overviewView(value.facts);
  if(value.task_activity && (stable(value.task_activity.period)!==stable(value.period) || stable(value.task_activity.scope)!==stable(value.scope)))mismatch();
  taskActivityView(value.task_activity,{dataAsOf:value.data_as_of});
  const binding=value.generation_binding;
  if(value.capabilities.can_generate) {
    const template=binding?.snapshot_selection;
    if(!binding?.token || !Number.isFinite(Date.parse(binding.expires_at)) || !template || !Array.isArray(template.sources) || !Array.isArray(template.supplements) || template.supplements.length ||
      stable(template.period)!==stable(request.selection.period) || !['self','team'].includes(template.scope?.kind) ||
      (request.selection.scope.kind==='team'?(template.scope.kind!=='team' || template.scope.team_id!==request.selection.scope.team_id):template.scope.kind!=='self') ||
      (template.facts_contract!=null && template.facts_contract!==SALES_WEEK_FACTS) ||
      (template.facts_contract===SALES_WEEK_FACTS)!==(request.selection.generation_profile==='sales_week_six'))mismatch();
  } else if(binding!==null || !Object.hasOwn(OVERVIEW_REASONS,value.capabilities.generation_unavailable_reason))mismatch();
  return value;
}
export function boundSnapshotSelection(overview,supplements,scheme,now=Date.now()) {
  const binding=overview?.generation_binding;
  if(!overview?.capabilities?.can_generate || !binding)fail(OVERVIEW_REASONS[overview?.capabilities?.generation_unavailable_reason] || '当前范围暂不可生成报告');
  if(!Number.isFinite(Date.parse(binding.expires_at)) || Date.parse(binding.expires_at)<=now)throw Object.assign(Error('概览材料已过期，请返回概览刷新后再生成'),{code:'WORK_REPORT_OVERVIEW_STALE'});
  if((scheme?.config.facts_contract===SALES_WEEK_FACTS)!==(overview.selection.generation_profile==='sales_week_six'))fail('所选方案与当前概览材料类型不一致，请返回概览切换报告类型');
  return {...binding.snapshot_selection,supplements,overview_binding:binding.token};
}
export const overviewStale=error=>[error?.serverCode,error?.code,error?.error_code].some(code=>['WORK_REPORT_OVERVIEW_STALE','WORK_REPORT_SCOPE_CHANGED'].includes(code));
