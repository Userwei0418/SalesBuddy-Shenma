import {dateLabel} from './presentation.js';
import {meetingAmount,countText} from './meeting-display.js';
import {MEETING_CONTRACT} from './meeting-selection.js';

const amounts=[['acv','acv'],['quarter_revenue','recognized'],['quarter_collection','collection']];
const counts=[['new_claimed_customers','customers','新认领客户','家','按认领审批生效时间'],['formal_visits','followups','正式跟进','条','按实际跟进日期'],['new_opportunities','new_opportunities','新增商机','个','按创建日期'],['lost_opportunities','lost','丢单商机','个','按丢单发生时间']];
const reasons={new_opportunity:'新增商机',amount_change:'金额调整',forecast_change:'预测调整',lost:'丢单减少',reopened:'重新在推',scope_transfer_in:'转入',scope_transfer_out:'转出',ownership_change:'归属变更',history_gap:'历史待核对',other_recorded:'已登记变动'};
const kinds={customer_claim:'客户认领',opportunity:'商机',opportunity_history:'商机历史',business_change:'业务变更',forecast_history:'预测历史',visit:'正式跟进',task:'已登记任务'};
const unique=values=>[...new Set(values.filter(Boolean))];
const date=value=>value && Number.isFinite(Date.parse(value))?dateLabel(value):'时间未登记';
const bad=()=>{throw Error('会议看板事实响应不完整，请重新读取');};
const list=value=>Array.isArray(value)?value:bad();

function money(item,id) {
  if(!item || item.currency!=='CNY' || item.unit!=='元' || !item.coverage || !item.known_subtotals)bad();
  for(const key of ['opening','change','closing']) {
    const coverage=item.coverage[key],value=item[key];
    if(!['complete','partial','unavailable'].includes(coverage) ||
      (coverage==='complete'?(typeof value!=='string' || !/^-?\d{1,24}(?:\.\d{1,8})?$/.test(value)):value!==null))bad();
  }
  const notes=list(item.missing_reasons);
  const known=['opening','change','closing'].filter(key=>item.coverage[key]==='partial' && item.known_subtotals[key]!=null)
    .map(key=>`${{opening:'期初',change:'本期变动',closing:'期末／当前'}[key]}已知小计 ${meetingAmount(item.known_subtotals[key],{signed:key==='change'})} 万元（非完整总额）`);
  return {id,opening:item.opening,change:item.change,closing:item.closing,reasons:[...notes,...known]};
}
const moneyView=value=>amounts.map(([key,id])=>money(value?.[key],id));
function countView(value) {
  return counts.map(([key,id,label,unit,basis])=>{
    const item=value?.[key];
    if(!item || !['complete','partial','unavailable'].includes(item.coverage) ||
      (item.coverage==='complete'?countText(item.value)==='—':item.value!==null))bad();
    return {id,label,unit,value:item.value,note:item.coverage==='complete'?basis:
      `${item.coverage==='partial'?`已确认 ${countText(item.known_count)} ${unit} · `:''}统计未完整`,reasons:list(item.missing_reasons)};
  });
}

// No network, aggregation, history reconstruction or permission inference.
// Member expansion selects the server's *member* money/rows: a team transfer's
// zero change must never be presented as either member's individual change.
function factsView(facts,{memberId,title}={},contract=MEETING_CONTRACT) {
  const scopes=contract===MEETING_CONTRACT?['self','managed_team']:['self','member','team'];
  if(facts?.contract_version!==contract || !facts.period?.label || !facts.scope?.label || !scopes.includes(facts.scope.kind) || !facts.quarter)bad();
  const people=list(facts.members),member=memberId?people.find(row=>row.member_id===memberId):null;
  if(memberId && !member)throw Error('该成员不在此报告的授权事实中');
  const memberNames=new Map(people.map(row=>[row.member_id,row.name || '姓名未取得']));
  const owners=ids=>unique(list(ids).map(id=>memberNames.get(id) || '姓名未取得')).join('、');
  const sources=new Map(list(facts.sources).map(row=>[row.source_id,row]));
  const visits=list(facts.formal_visits),steps=list(facts.next_steps);
  const sourceNotes=refs=>list(refs).map(ref=>{
    const source=sources.get(ref);
    return source?{id:ref,atLabel:date(source.business_at || source.observed_at),actorLabel:kinds[source.kind] || '业务材料',text:`${source.business_at?'业务时间':'取数时间'} · 来源 ${ref}${source.version?` · 版本 ${source.version}`:''}`}:
      {id:ref,atLabel:'',actorLabel:'来源暂不可读',text:'请重新读取报告核对材料权限'};
  });
  const stepText=step=>[step.text,step.original_date_text?`原计划日期：${step.original_date_text}`:null].filter(Boolean).join(' · ');
  const rawChanges=list(member?member.opportunity_changes:facts.opportunity_changes);
  const changes=rawChanges.map(row=>{
    const rowAmounts=moneyView(row.amounts),why=list(row.reasons),primary=why.find(item=>item.code==='lost') || why[0];
    return {id:row.opportunity_id,opportunityId:row.opportunity_id,name:row.name,customerId:row.customer_id,customerName:row.customer_name || '关联客户名称未取得',canOpen:Boolean(row.opportunity_id),
      ownerLabel:owners(unique([...list(row.opening_member_ids),...list(row.closing_member_ids)])),kind:primary?.code,kindLabel:reasons[primary?.code] || '业绩变动',
      reason:why.map(item=>item.text).join('；'),money:Object.fromEntries(rowAmounts.map(item=>[item.id,item])),
      progress:list(row.follow_up_ids).map(id=>visits.find(item=>item.id===id)?.summary).filter(Boolean).join('\n'),
      nextStep:list(row.next_step_ids).map(id=>steps.find(item=>item.id===id)).filter(Boolean).map(stepText).join('\n'),
      reasons:unique(rowAmounts.flatMap(item=>item.reasons)),history:sourceNotes(row.source_refs)};
  });
  const selected=(rows,ids)=>member?list(rows).filter(row=>list(ids).includes(row.id)):list(rows);
  const presentItem=row=>({id:row.id,name:row.name,customerId:row.customer_id,customerName:row.customer_name,opportunityId:row.opportunity_id,opportunityName:row.opportunity_name,
    ownerLabel:owners(row.member_ids),atLabel:date(row.occurred_at),summary:row.summary,canOpen:Boolean(row.customer_id),history:sourceNotes(row.source_refs)});
  const customers=selected(facts.new_claimed_customers,member?.new_claimed_customer_ids).map(presentItem);
  const followups=selected(facts.formal_visits,member?.formal_visit_ids).map(presentItem);
  const events=(rows,ids,kind)=>selected(rows,ids).map(row=>{
    const change=changes.find(item=>item.id===row.opportunity_id);
    return {...change,...presentItem(row),id:`${kind}:${row.id}`,canOpen:Boolean(row.opportunity_id),kind,kindLabel:kind==='lost'?'丢单':'新增',progress:row.summary || change?.progress};
  });
  const nextSteps=selected(steps,member?.next_step_ids).map(row=>({id:row.id,title:changes.find(change=>change.id===row.opportunity_id)?.name || '已登记下一步',text:row.text,ownerLabel:owners(row.member_ids),dateLabel:[row.due_at?date(row.due_at):null,row.original_date_text].filter(Boolean).join(' · ') || '计划日期未登记'}));
  const shownCounts=countView(member?member.counts:facts.counts);
  return {title:title || `${member?`${member.name || '历史成员'} · `:''}本期经营变化`,periodLabel:facts.period.label,scopeLabel:member?(member.name || '姓名未取得'):facts.scope.label,
    quarterLabel:`${facts.quarter.year} Q${facts.quarter.quarter}`,asOfLabel:`统计截至 ${date(facts.period.cutoff)} · 材料准备于 ${date(facts.data_as_of)}`,
    counts:shownCounts,amounts:moneyView(member?member.amounts:facts.amounts),changes,customers,followups,
    opportunities:[...events(facts.new_opportunities,member?.new_opportunity_ids,'created'),...events(facts.lost_opportunities,member?.lost_opportunity_ids,'lost')],nextSteps,
    members:member || ['self','member'].includes(facts.scope.kind)?[]:people.map(row=>({id:row.member_id,name:row.name || '姓名未取得',summary:`${row.membership_state==='period_only'?'本期历史成员 · ':''}${countText(row.counts.formal_visits.value)} 条跟进 · ${countText(row.counts.new_claimed_customers.value)} 家新认领客户`})),
    notices:unique([...list(facts.missing_reasons),...(member?.missing_reasons || []),...shownCounts.flatMap(row=>row.reasons)]),
    detailNotes:member?['当前为成员个人口径；团队汇总已独立去重，不等于成员数值相加。']:['managed_team','team'].includes(facts.scope.kind)?['团队内按业务记录去重；成员数据可能交叉，不相加得到团队总数。']:[],
    definitions:['新认领客户按本期审批生效时间统计，跟进按实际发生日期统计。','金额单位为万元，显示同一指定季度的期初、本期净变化和期末／当前值；历史缺失显示“—”。','丢单从预计业绩中移出并注明原因，已发生实际业绩保留。','下一步为已登记计划，尚未完成。伙伴不纳入本期统计。']};
}
export const meetingView=(facts,options)=>factsView(facts,options,MEETING_CONTRACT);
export const overviewView=(facts,options)=>factsView(facts,options,'work_report.overview_facts.v1');
