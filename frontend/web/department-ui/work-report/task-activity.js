import {dateLabel} from './presentation.js';
import {countText} from './meeting-display.js';

const legacyMetrics=[['new_tasks','created','本期新增任务','按创建时间'],['completed_tasks','completed','本期完成任务','按实际完成时间']];
const lightweightMetrics=[['completed_tasks','completed','完成任务','按本期正常完成时间'],['progressing_tasks','progressing','推进任务','截至当前仍在推进的任务']];
const processingLabels={adjustment_request:'调整请求',paused:'已暂停',awaiting_review:'待验收'};
const unavailable='任务统计尚未提供，请刷新概览后查看';
const fail=()=>{throw Error('任务统计响应不完整，请重新读取概览');};
const list=value=>Array.isArray(value)?value:fail();
const id=value=>typeof value==='string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value);
const validDate=value=>typeof value==='string' && Number.isFinite(Date.parse(value));
const date=value=>value?dateLabel(value):'未登记';
const missingCount=()=>({value:null,known_count:0,coverage:'unavailable',missing_reasons:[unavailable]});

function countView(item,key,label,basis) {
  if(!item || !['complete','partial','unavailable'].includes(item.coverage) || countText(item.known_count)==='—' ||
    (item.coverage==='complete'?(countText(item.value)==='—' || item.value!==item.known_count):item.value!==null) ||
    (item.coverage==='unavailable' && item.known_count!==0))fail();
  const reasons=list(item.missing_reasons);
  if(item.coverage!=='complete' && !reasons.length)fail();
  return {id:key,label,unit:'项',value:item.value,reasons,note:item.coverage==='complete'?basis:
    item.coverage==='partial'?`已确认 ${item.known_count} 项 · 统计未完整`:'尚未统计'};
}

// The task view is a sibling of frozen overview facts. Never add it to a
// snapshot, signed generation binding, report body, or model request.
export function taskActivityView(activity,{memberId,dataAsOf}={}) {
  const lightweight=activity==null || activity.contract_version==='work_report.task_activity.v2';
  const metrics=lightweight?lightweightMetrics:legacyMetrics;
  if(activity==null)return {counts:metrics.map(([key,,label,basis])=>countView(missingCount(),key,label,basis)),
    taskActivity:{completed:[],progressing:[]},notices:[unavailable]};
  if(!['work_report.task_activity.v1','work_report.task_activity.v2'].includes(activity.contract_version) || activity.attribution_basis!=='current_task_owner' ||
    !validDate(activity.data_as_of) || activity.data_as_of!==dataAsOf)fail();
  const members=list(activity.members),selected=memberId?members.find(row=>row.member_id===memberId):null;
  if(new Set(members.map(row=>row.member_id)).size!==members.length || members.some(row=>!id(row.member_id)))fail();
  const sourceIds=new Set(list(activity.sources).map(row=>row.source_id));
  const taskActivity={},counts=[];
  for(const [key,kind,label,basis] of metrics) {
    const rows=list(activity[key]),rowIds=new Set(rows.map(row=>row.id));
    if(rowIds.size!==rows.length)fail();
    const memberKey=key==='new_tasks'?'new_task_ids':key==='progressing_tasks'?'progressing_task_ids':'completed_task_ids';
    for(const member of members) {
      const refs=list(member[memberKey]);
      if(new Set(refs).size!==refs.length || refs.some(ref=>!rowIds.has(ref)))fail();
    }
    const shownCount=memberId?(selected?.counts?.[key] || missingCount()):activity.counts?.[key];
    counts.push(countView(shownCount,key,label,basis));
    const selectedIds=memberId?new Set(selected?.[memberKey] || []):null;
    taskActivity[kind]=rows.map(row=>{
      if(!id(row.id) || row.task_id!==row.id || typeof row.name!=='string' || !row.name || !validDate(row.created_at) ||
        (row.completed_at!==null && !validDate(row.completed_at)) || typeof row.status_label!=='string' || !row.status_label)fail();
      if(lightweight && (!validDate(row.updated_at) || (row.processing_reason!==null && !Object.hasOwn(processingLabels,row.processing_reason)) || ['processing_detail','latest_feedback','next_step','completion_result'].some(key=>row[key]!==null && typeof row[key]!=='string')))fail();
      const owners=list(row.owners),memberIds=list(row.member_ids);
      if(memberIds.some(value=>!id(value)) || owners.some(owner=>!id(owner.member_id) || (owner.name!==null && typeof owner.name!=='string')) ||
        list(row.source_refs).some(ref=>!sourceIds.has(ref)))fail();
      for(const member of members)if(member[memberKey].includes(row.id) && !memberIds.includes(member.member_id))fail();
      return {id:row.id,taskId:row.task_id,name:row.name,ownerLabel:owners.map(owner=>owner.name || '姓名未取得').join('、') || '负责人未登记',
        customerId:row.customer_id,customerName:row.customer_name,opportunityId:row.opportunity_id,opportunityName:row.opportunity_name,
        createdLabel:date(row.created_at),completedLabel:date(row.completed_at),statusLabel:row.status_label,
        ...(lightweight?{updatedLabel:date(row.updated_at),processingLabel:processingLabels[row.processing_reason] || '',processingDetail:row.processing_detail || '',latestFeedback:row.latest_feedback || '',nextStep:row.next_step || '',completionResult:row.completion_result || ''}:{})};
    }).filter(row=>!selectedIds || selectedIds.has(row.id));
  }
  return {counts,taskActivity,notices:[...new Set([...list(activity.missing_reasons),...counts.flatMap(row=>row.reasons)])]};
}

export function withTaskActivity(model,activity,options) {
  const tasks=taskActivityView(activity,options);
  return {...model,counts:[...model.counts,...tasks.counts],taskActivity:tasks.taskActivity,
    notices:[...new Set([...model.notices,...tasks.notices])],
    definitions:[...model.definitions,activity?.contract_version==='work_report.task_activity.v1'?'历史任务统计按创建与完成分别计数，团队按任务去重。':'任务按当前负责人归属，团队按任务去重。完成数仅含本期正常完成，不含停止、历史取消、拒绝或结果待核对；待验收仍计入推进。']};
}
