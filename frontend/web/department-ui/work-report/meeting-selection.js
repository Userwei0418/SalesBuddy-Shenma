import {calendarHint,stable} from './state.js';
import {todayKey} from '../pc/period.js';
import {toPeriod} from './presentation.js';

export const MEETING_CONTRACT='work_report.meeting_facts.v1';
const fail=message=>{throw Error(message);};
export function meetingOptions(value) {
  if(value?.contract_version!==MEETING_CONTRACT || !Array.isArray(value.available_scope_kinds) ||
    value.available_scope_kinds.some(kind=>!['self','managed_team'].includes(kind)) ||
    !Array.isArray(value.teams) || !Array.isArray(value.default_schemes) ||
    ['can_prepare','can_save_draft','can_share'].some(key=>typeof value[key]!=='boolean'))fail('会议看板服务尚未配套，请更新后重试');
  return value;
}
export function meetingScopes(options) {
  return [...(options.available_scope_kinds.includes('self')?[{value:'self',label:'本人'}]:[]),
    ...(options.available_scope_kinds.includes('managed_team')?options.teams.map(team=>({value:team.team_id,label:`团队 · ${team.name}`})):[])];
}
export function meetingSelection({period,anchor,scopeKey},options) {
  meetingOptions(options);
  if(!options.can_prepare)fail('当前账号暂不可生成会议看板');
  if(!calendarHint(period,anchor) || anchor>todayKey())fail('请选择今天或过去周期内的有效日期');
  const kind=toPeriod(period);
  if(!options.default_schemes.some(item=>item.period_kind===kind))fail('该周期的默认模板暂不可用');
  if(!meetingScopes(options).some(item=>item.value===scopeKey))fail('请选择本人或当前所管理的团队');
  return {facts_contract:MEETING_CONTRACT,period:{kind,anchor_date:anchor,timezone:'Asia/Shanghai'},
    scope:scopeKey==='self'?{kind:'self'}:{kind:'managed_team',team_id:scopeKey}};
}
export function meetingDraft(snapshot,options) {
  meetingOptions(options);
  if(!options.can_save_draft)fail('当前账号暂不可保存会议报告');
  if(snapshot?.status!=='ready' || !snapshot.id || !/^[0-9a-f]{64}$/.test(snapshot.snapshot_hash || '') || snapshot.meeting_facts?.contract_version!==MEETING_CONTRACT)fail('请先生成完整的会议看板');
  const scheme=options.default_schemes.find(item=>item.period_kind===snapshot.period.kind);
  if(!scheme)fail('该周期的默认模板已变化，请重新读取');
  if(!meetingScopes(options).some(item=>item.value===(snapshot.scope.kind==='self'?'self':snapshot.scope.team_id)))fail('报告生成范围已变化，请重新读取');
  return {snapshot_id:snapshot.id,snapshot_hash:snapshot.snapshot_hash,title:`${snapshot.period.label} · ${snapshot.scope.label} · ${scheme.name}`.slice(0,240),builtin_scheme_id:scheme.builtin_id};
}
export function matchingMeetingSnapshot(snapshot,selection) {
  // A delayed response must not substitute the prior account's period/team.
  const selector=value=>({facts_contract:value?.facts_contract,period:value?.period,scope:{kind:value?.scope?.kind,team_id:value?.scope?.team_id || null}});
  if(!snapshot || !['preparing','ready','failed'].includes(snapshot.status) || !snapshot.id || stable(selector(snapshot.selection))!==stable(selector(selection)))fail('看板结果与本次周期或范围不一致，请重新生成');
  if(snapshot.scope?.kind!==selection.scope.kind || (snapshot.scope?.team_id || null)!==(selection.scope.team_id || null) || snapshot.period?.kind!==selection.period.kind)fail('看板解析范围与本次选择不一致，请重新读取');
  if(snapshot.status==='ready' && (!snapshot.meeting_facts || stable(snapshot.period)!==stable(snapshot.meeting_facts.period) || stable(snapshot.scope)!==stable(snapshot.meeting_facts.scope) || snapshot.data_as_of!==snapshot.meeting_facts.data_as_of))fail('看板快照与事实不一致，请重新读取');
  return snapshot;
}
