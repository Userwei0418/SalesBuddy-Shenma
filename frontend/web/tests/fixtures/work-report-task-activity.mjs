export const taskId=n=>`90000000-0000-0000-0000-${String(n).padStart(12,'0')}`;
export const taskAsOf='2026-10-12T11:00:00+08:00';
export const taskCount=value=>({value,known_count:value,coverage:'complete',missing_reasons:[]});
export function taskActivityFixture() {
  const owner={member_id:taskId(1),name:'成员甲'},other={member_id:taskId(2),name:'成员乙'};
  const row=(n,name,owners,created,completed,status,status_label)=>({id:taskId(n),task_id:taskId(n),name,owners,
    member_ids:owners.map(person=>person.member_id),created_at:created,completed_at:completed,status,status_label,
    customer_id:taskId(100),customer_name:'示例客户',opportunity_id:taskId(101),opportunity_name:'示例商机',source_refs:[`task:${n}`]});
  const shared=row(10,'共同核对交付计划',[owner,other],'2026-10-12T08:00:00+08:00',null,'in_progress','执行中');
  const recent=row(11,'确认本周回款日期',[owner],'2026-10-12T08:30:00+08:00','2026-10-12T09:00:00+08:00','completed','已完成');
  const old=row(12,'上周创建、本期完成后重新打开',[other],'2026-10-08T09:00:00+08:00','2026-10-12T10:00:00+08:00','in_progress','执行中');
  return {contract_version:'work_report.task_activity.v1',data_as_of:taskAsOf,attribution_basis:'current_task_owner',
    counts:{new_tasks:taskCount(2),completed_tasks:taskCount(2)},new_tasks:[shared,recent],completed_tasks:[recent,old],
    members:[{member_id:owner.member_id,counts:{new_tasks:taskCount(2),completed_tasks:taskCount(1)},new_task_ids:[shared.id,recent.id],completed_task_ids:[recent.id]},
      {member_id:other.member_id,counts:{new_tasks:taskCount(1),completed_tasks:taskCount(1)},new_task_ids:[shared.id],completed_task_ids:[old.id]}],
    sources:[10,11,12].map(n=>({source_id:`task:${n}`,kind:'task',record_id:taskId(n),version:'1',observed_at:taskAsOf,business_at:taskAsOf,source_hash:'0'.repeat(64)})),missing_reasons:[]};
}
