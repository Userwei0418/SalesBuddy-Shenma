import {fromPeriod,scopeOptions} from './presentation.js';
import {scopeFilter} from '../review/scope-selection.js';
import {OVERVIEW_REASONS} from './overview.js';

// Navigation carries selectors, never creates a snapshot or grants permission.
// A usable signed overview is kept intact. Other entries are checked again by
// the existing options, snapshot selection and prepare API in the composer.
export function composeEntry(overview,filters){
  if(overview?.kind==='scope_v2')return {...overview,notice:overview.capabilities.can_generate?null:'当前概览材料暂不能直接生成，请在设置中核对范围和可用材料。'};
  if(overview)return {
    overview:overview.capabilities.can_generate?overview:undefined,
    period:fromPeriod(overview.selection.period.kind),anchor:overview.selection.period.anchor_date,
    scope:overview.generation_binding?.snapshot_selection.scope || overview.selection.scope,
    profile:overview.selection.generation_profile,
    notice:overview.capabilities.can_generate?null:overview.capabilities.generation_unavailable_reason==='SOURCE_NOT_GENERATABLE'?'当前概览不能直接生成，请重新选择授权材料后准备。':`${OVERVIEW_REASONS[overview.capabilities.generation_unavailable_reason] || '本期概览材料暂不能直接生成'}。可核对并调整授权材料后准备；提交时会重新检查权限。`,
  };
  if(!filters)return null;
  const {kind,ids}=scopeFilter(filters);
  return {period:filters.period,anchor:filters.anchor,profile:filters.profile,
    scope:{kind,...(kind==='self'?{}:{contract_version:'work_report.scope_selection.v2',[kind==='team'?'team_ids':'member_ids']:ids})},
    notice:'已保留当前周期和范围，请在设置中核对授权材料后准备。'};
}
export function overviewGenerateHint({value,loading,error}){
  if(loading)return '正在读取概览材料；可先进入生成设置。';
  if(error)return '概览材料未能读取；可重试，或进入生成设置核对授权材料。';
  if(value?.capabilities.generation_unavailable_reason==='SOURCE_NOT_GENERATABLE')return '进入设置，重新选择授权材料。';
  if(value && !value.capabilities.can_generate)return OVERVIEW_REASONS[value.capabilities.generation_unavailable_reason] || '当前概览材料暂不能直接生成，请在设置中核对。';
  return value?'进入设置核对材料后生成，不会立即提交。':'进入生成设置，按当前授权范围准备材料。';
}


// Fallback labels explain an existing selection only. The authorization
// directory is untouched and snapshotSelection still rejects unavailable IDs.
export function composeScopeOptions(scope,options){
  const kinds=scopeOptions(options),available=kinds.some(row=>row.value===scope.kind);
  if(!available)kinds.push({value:scope.kind,label:`${({self:'本人',team:'团队',member:'成员',workspace:'全部可见范围'})[scope.kind] || '原范围'}（当前不可生成）`,disabled:true});
  const rows=scope.kind==='member'?options.members:scope.kind==='team'?options.teams:[];
  const subjects=rows.map(row=>({value:row.id,label:row.name,disabled:!available}));
  const ids=scope.kind==='member'?(scope.member_ids || (scope.member_id?[scope.member_id]:[])):scope.kind==='team'?(scope.team_ids || (scope.team_id?[scope.team_id]:[])):[];
  for(const id of ids)if(!subjects.some(row=>row.value===id))subjects.push({value:id,label:`原选${scope.kind==='team'?'团队':'成员'}（当前不可生成）`,disabled:true});
  return {kinds,subjects};
}
