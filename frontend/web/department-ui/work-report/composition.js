import {calendarHint} from './state.js';
import {toPeriod} from './presentation.js';
import {todayKey} from '../pc/period.js';
import {SALES_WEEK_FACTS,WEEK_SECTION_LABELS} from './sales-week.js';

export function reportSupplements({supplement='',supplementId,supplementSection,facts_contract}) {
  if(supplementSection && (facts_contract!==SALES_WEEK_FACTS || !Object.hasOwn(WEEK_SECTION_LABELS,supplementSection)))throw Error('请选择当前销售周报中的补充章节');
  const text=supplement.trim();
  if(text.length>16000)throw Error('补充说明最多16000字');
  return text?[{id:supplementId,text,declared_kind:['next_week_plan','next_week_collection'].includes(supplementSection)?'plan':'context',
    ...(supplementSection?{section_id:supplementSection}:{})}]:[];
}

// Only selectors cross this boundary. Resolved members, counts and time bounds
// always come back from the server's authorized snapshot.
export function snapshotSelection({period,anchor,scope,sources,supplement,supplementId,supplementSection,facts_contract},options) {
  if(!calendarHint(period,anchor) || anchor>todayKey())throw Error('请选择今天或过去周期内的有效日期');
  if(!options.periods.includes(toPeriod(period)))throw Error('该报告周期暂不可用');
  if(facts_contract && facts_contract!==SALES_WEEK_FACTS)throw Error('此方案的材料版本暂不受支持，请更新后重试');
  if(facts_contract && toPeriod(period)!=='week')throw Error('此销售周报方案仅适用于自然周，请选择本周或切换通用方案');
  if(!facts_contract && sources.includes('actual'))throw Error('正式回款材料需要使用新版销售周报方案');
  const supplements=reportSupplements({supplement,supplementId,supplementSection,facts_contract});
  if(!options.available_scope_kinds.includes(scope.kind))throw Error('请选择当前可用的报告范围');
  const target={kind:scope.kind};
  if(scope.kind==='member') {
    const ids=[...(scope.member_ids || (scope.member_id ? [scope.member_id] : []))];
    if(!ids.length || ids.length>100 || ids.some(id=>!options.members.some(item=>item.id===id)))throw Error('请选择 1 至 100 个可查看的成员');
    target.contract_version='work_report.scope_selection.v2';
    target.member_ids=[...new Set(ids)];
  }
  if(scope.kind==='team') {
    const ids=[...(scope.team_ids || (scope.team_id ? [scope.team_id] : []))];
    if(!ids.length || ids.length>100 || ids.some(id=>!options.teams.some(item=>item.id===id)))throw Error('请选择 1 至 100 个可查看的部门');
    target.contract_version='work_report.scope_selection.v2';
    target.team_ids=[...new Set(ids)];
  }
  if(target.contract_version){
    if((scope.kind==='team' && scope.member_ids?.length) || (scope.kind==='member' && scope.team_ids?.length))throw Error('请选择同类成员或部门');
    if(scope.expected_scope_revision!=null){
      if(!/^[a-f0-9]{64}$/i.test(scope.expected_scope_revision))throw Error('范围版本无效，请重新读取概览');
      target.expected_scope_revision=scope.expected_scope_revision;
    }
  }
  const available=new Set(options.sources.filter(item=>item.available).map(item=>item.id));
  if(sources.some(id=>!available.has(id)))throw Error('所选来源暂不可用，请调整材料来源');
  if(!sources.length && !supplements.length)throw Error('至少选择一种材料来源或填写补充说明');
  return {...(facts_contract?{facts_contract}:{}),period:{kind:toPeriod(period),anchor_date:anchor,timezone:'Asia/Shanghai'},scope:target,
    sources:[...new Set(sources)],supplements};
}

export const accessLost=error=>[401,403,404].includes(error?.statusCode || error?.status) || error?.code==='REPORT_IDENTITY_CHANGED';
