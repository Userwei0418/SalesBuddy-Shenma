import React from 'react';
import {Button} from 'antd';
// Preserve the imported component's permission checks, confirmations and versioned decision receipt.
export default function AdviceActions({page,suggestion:s,invokeOn}) {
  const instance=page.selectAllComponents('advice-actions').find(c=>c.data.suggestion?.id===s.id);
  if(!instance)return null;
  const on=(name,id)=>invokeOn(instance,name,{dataset:{id}});
  const tasks=s.tasks?.length?s.tasks:s.task_id?[{id:s.task_id}]:[];
  return <div className="ds-sync-members">{s.decision==='pending'&&instance.data.canDecide&&<><Button size="small" type="primary" disabled={instance.data.saving} onClick={()=>on('adopt')}>采纳并创建待办</Button><Button size="small" disabled={instance.data.saving} onClick={()=>on('dismiss')}>无需待办</Button></>}{tasks.map((t,i)=><Button key={t.id} size="small" onClick={()=>on('openTask',t.id)}>查看待办 {t.assignee_name || i+1}</Button>)}{s.decision==='no_task'&&<span>已确认无需待办</span>}</div>;
}
