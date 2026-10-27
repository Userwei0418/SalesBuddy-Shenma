import {calendarHint} from '../work-report/state.js';
import {toPeriod} from '../work-report/presentation.js';
import {overviewRequest} from '../work-report/overview.js';
import {bindReviewScope} from './overview-model.js';
import {todayKey} from '../pc/period.js';

export const MULTI_REPORT_UNAVAILABLE='所选范围的报告生成尚未配套，请保留选择或切回单一范围';
const unique=ids=>[...new Set(ids || [])].sort();
export function scopeFilter(filters) {
  const [legacyKind,legacyId]=String(filters.scopeKey || 'self').split(':');
  return {kind:filters.scopeKind || legacyKind,ids:unique(filters.subjectIds ?? (legacyId?[legacyId]:[]))};
}
export function reviewScopeRequest(filters,options) {
  const {kind,ids}=scopeFilter(filters),periodType=toPeriod(filters.period);
  if(!calendarHint(filters.period,filters.anchor) || filters.anchor>todayKey() || !options.period_kinds.includes(periodType))throw Error('请选择当前可用周期内的有效日期');
  if(!options.available_scope_kinds.includes(kind))throw Error('请选择当前可查看的成员或团队');
  const directory=kind==='team'?options.teams:kind==='member'?options.members:[];
  if(kind==='self'?ids.length:!ids.length || ids.length>100 || ids.some(id=>!directory.some(row=>row.id===id)))throw Error(kind==='self'?'本人范围不能同时选择成员或团队':'请选择 1 至 100 个当前可查看的同类对象');
  const review={scope:kind,period_type:periodType,anchor_date:filters.anchor,...(kind==='self'?{}:{[kind==='team'?'team_ids':'member_ids']:ids})};
  // The old signed report binding has a single scope contract. Never project a
  // multiple selection onto its first subject or split it into separate reports.
  const legacy=ids.length>1?null:overviewRequest({...filters,scopeKey:kind==='self'?'self':`${kind}:${ids[0]}`},options);
  return {review,legacy,multiple:ids.length>1};
}
export function scopeQuery(filters) {
  const {kind,ids}=scopeFilter(filters);
  return {review_scope:kind,review_subjects:ids.join(','),review_anchor:filters.anchor,review_profile:filters.profile,period:filters.period};
}
export function readScopeQuery(query,options) {
  if(!query?.review_scope)return null;
  const scopeKind=query.review_scope,subjectIds=String(query.review_subjects || '').split(',').filter(Boolean);
  const filters={scopeKind,subjectIds,period:query.period || 'day',anchor:query.review_anchor || todayKey(),profile:query.review_profile || 'general'};
  try{reviewScopeRequest(filters,options);return {filters};}catch(error){return {error:'已保存的查看范围已不可用，请重新选择。'};}
}

// A v2 composer preset carries selectors and the verified scope revision only.
// It is deliberately not a legacy signed overview/generation_binding.
export function multiComposePreset(filters,overview,options) {
  const selected=scopeFilter(filters);
  if(selected.ids.length<2 || !overview || !options)return null;
  const selection=bindReviewScope({scope:selected.kind,period_type:toPeriod(filters.period),anchor_date:filters.anchor,
    [selected.kind==='team'?'team_ids':'member_ids']:selected.ids},overview);
  const directory=selected.kind==='team'?options.teams:options.members;
  const allowed=options.can_create && options.generation_available && options.periods?.includes(selection.period_type) &&
    options.available_scope_kinds?.includes(selected.kind) && selected.ids.every(id=>directory?.some(row=>row.id===id));
  return {kind:'scope_v2',period:filters.period,anchor:filters.anchor,profile:filters.profile,
    scope:{contract_version:'work_report.scope_selection.v2',kind:selected.kind,
      [selected.kind==='team'?'team_ids':'member_ids']:selected.ids,expected_scope_revision:selection.expected_scope_revision},
    capabilities:{can_generate:Boolean(allowed)}};
}
