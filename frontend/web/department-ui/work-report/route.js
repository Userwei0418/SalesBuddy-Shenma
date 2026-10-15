import {readScopeQuery,scopeFilter} from '../review/scope-selection.js';
import {readScope} from '../pc/workspace-selection.js';

const id=value=>typeof value==='string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)?value:null;
// Keep workbench links within the fresh report directory. A URL is a requested
// selector, never evidence that a member or department is readable.
export function initialReportScope(query,options) {
  if(query?.review_scope){
    const saved=readScopeQuery(query,{...options,period_kinds:options.period_kinds || options.periods});
    if(saved?.error)return {error:saved.error};
    const {kind,ids}=scopeFilter(saved.filters);
    return {scope:{kind,...(kind==='self'?{}:{contract_version:'work_report.scope_selection.v2',[kind==='team'?'team_ids':'member_ids']:ids})}};
  }
  const previous=readScope(query);
  if(!previous)return null;
  const scope=previous.kind==='member'?{kind:'member',member_id:previous.memberId}:
    previous.kind==='team'?previous.teamId==='all'?{kind:'workspace'}:{kind:'team',team_id:previous.teamId}:{kind:'self'};
  const allowed=options.available_scope_kinds.includes(scope.kind) &&
    (scope.kind!=='member' || options.members.some(item=>item.id===scope.member_id)) &&
    (scope.kind!=='team' || options.teams.some(item=>item.id===scope.team_id));
  return allowed?{scope}:{error:'原入口的报告范围已不可用，请重新选择。'};
}
export function reportRoute(options={}) {
  const documentId=id(options.document_id),runId=id(options.run_id);
  let tab=['meeting','compose','documents','schemes','targets','okr'].includes(options.report_tab)?options.report_tab:documentId?'documents':runId || id(options.scheme_id)?'compose':'meeting';
  // A reloaded Review selection must be revalidated and bound by the overview
  // before creating a report, so its membership revision is always refreshed.
  // Existing runs/documents retain their own immutable server-side scope.
  if(options.review_scope && tab==='compose' && !runId && !documentId)tab='meeting';
  return {tab,documentId,
    schemeId:id(options.scheme_id),activeRun:runId?{id:runId,documentId:id(options.run_document_id)}:null};
}
export function reportQuery(route) {
  return {report_tab:route.tab,document_id:route.documentId || null,scheme_id:route.schemeId || null,
    run_id:route.activeRun?.id || null,run_document_id:route.activeRun?.documentId || null};
}
