import React from 'react';
import {Alert,Button} from 'antd';
import {SbStatusTag} from '@shandiant/ui-react';
import {TaskCancellationDialog} from './NativeDialogs.jsx';
import TaskAlignmentDetail from './review/tasks/TaskAlignmentDetail.jsx';
import {useTaskAlignmentDirectory} from './review/tasks/useTaskAlignmentDirectory.js';
import LightweightTaskPanel from './task-execution/LightweightTaskPanel.jsx';
import ExecutionPanel from './task-execution/ExecutionPanel.jsx';
import './task-detail.css';
import LegacyTaskPanel from './task-execution/LegacyTaskPanel.jsx';
import TaskMigration from './task-execution/TaskMigration.jsx';
import {runtime,session} from './pc/data.js';
function taskMode(task){try{return runtime().require('utils/taskExecutionMode').mode(task,session()?.capabilities?.task_execution);}catch{return 'invalid';}}
export function supportsTaskDetail(page,data={}) {return Boolean(page && page.route==='pages/task-detail/index' && data.task);}
function ExecutionTaskDetail({page,data:d,invoke,invokeOn,select}) {
  const t=d.task, x=d.execution, busy=Boolean(d.executionBusy || d.executionLoading || d.submitting);
  const directory=useTaskAlignmentDirectory(Boolean(t.alignment));
  const info=[['执行人',x?.executorLabel || t.owner],['验收人',x?.reviewerLabel || '待核对'],['下发人',t.creator],['截止时间',t.dueLabel],['关联商机',t.opportunityLabel],['创建时间',t.createdLabel]];
  return <section className="ds-tdetail task-execution-detail" aria-label="任务详情">
    <aside className="ds-panel ds-td-side"><div className="ds-td-tags"><SbStatusTag tone={t.isSuccessful?'good':'pending'} label={t.statusLabel}/><span>{t.priority}优先级</span></div><h1>{t.title}</h1><p className="ds-muted">{[t.customer,t.team].filter(Boolean).join(' · ')}</p>
      <dl className="ds-td-info">{info.filter(([,v])=>v).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      {x && !d.executionError && (x.raw.protocol_version===2?x.caps.cancel_task:t.canCancel) && <Button danger disabled={busy} onClick={()=>x.raw.protocol_version===2?invoke('executionAction',{detail:{action:'cancel'}}):invoke('openCancellation')}>取消任务</Button>}
    </aside>
    <section className="ds-panel ds-td-main">
      <section className="ds-td-card"><h2>任务要求</h2><p className="ds-td-desc">{t.description}</p></section>
      <TaskAlignmentDetail task={{...t,can_coordinate:Boolean(x && !d.executionError && t.can_coordinate)}} page={page} directory={directory} busy={busy}/>
      <ExecutionPanel key={`${d.executionContext || 'loading'}:${t.id}`} page={page} data={d} invoke={invoke} directory={directory}/>
      {(t.history || []).length>0 && <section className="ds-td-card"><h2>任务记录</h2><ol className="ds-td-history">{t.history.map(h=><li key={h.id}><b>{h.label} · {h.actor_name}</b><span>{h.time}</span><p>{h.note}</p>{h.nextStep && <p><b>下一步：</b>{h.nextStep}</p>}{(h.changes || []).map(change=><p key={change.field}><b>{change.label}：</b>{change.before} → {change.after}</p>)}</li>)}</ol></section>}
    </section>
    <TaskCancellationDialog instance={select('task-cancel-dialog')} invokeOn={invokeOn}/>
  </section>;
}
function LightweightTaskDetail({page,data:d,invoke}) {
  const t=d.task, x=d.execution;
  const directory=useTaskAlignmentDirectory(Boolean(t.alignment));
  const info=[['执行人',x?.executorLabel || t.owner],['下发人',x?.creatorLabel || t.creator],['截止时间',t.dueLabel],['关联商机',t.opportunityLabel],['创建时间',t.createdLabel]];
  return <section className="ds-tdetail task-lightweight-detail" aria-label="任务详情">
    <aside className="ds-panel ds-td-side"><div className="ds-td-tags"><SbStatusTag tone={t.isSuccessful?'good':'pending'} label={x?.statusLabel || t.statusLabel}/><span>{t.priority}优先级</span></div><h1>{t.title}</h1><p className="ds-muted">{[t.customer,t.team].filter(Boolean).join(' · ')}</p>
      <dl className="ds-td-info">{info.filter(([,v])=>v).map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    </aside>
    <section className="ds-panel ds-td-main">
      <section className="ds-td-card"><h2>任务内容</h2><p className="ds-td-desc">{t.description}</p></section>
      <TaskAlignmentDetail task={{...t,can_coordinate:Boolean(x && !d.executionError && t.can_coordinate)}} page={page} directory={directory} busy={Boolean(d.executionBusy)}/>
      <LightweightTaskPanel data={d} invoke={invoke}/>
      <section className="ds-td-card"><h2>任务记录</h2><ol className="ds-td-history">{(t.history || []).map(h=><li key={h.id}><b>{h.label} · {h.actor_name}</b><span>{h.time}</span><p>{h.note}</p>{h.nextStep && <p><b>下一步：</b>{h.nextStep}</p>}{(h.changes || []).map(change=><p key={change.field}><b>{change.label}：</b>{change.before} → {change.after}</p>)}</li>)}</ol>
        {d.executionHistoryCursors?.events!==null && <Button disabled={d.executionBusy} loading={d.executionHistoryLoading} onClick={()=>invoke('loadExecutionHistory',{dataset:{kind:'events'}})}>加载更早记录</Button>}
        {d.executionHistoryError && <p role="alert">{d.executionHistoryError}</p>}
      </section>
    </section>
  </section>;
}
export default function TaskDetail(props){
  const mode=taskMode(props.data.task);
  if(mode==='execution_v3')return <LightweightTaskDetail {...props}/>;
  if(mode==='legacy')return <><TaskMigration data={props.data} invoke={props.invoke}/><LegacyTaskPanel {...props}/>{([2,3].includes(props.data.taskMigration?.raw.supported_protocol_version)||[2,3].includes(props.data.taskMigration?.raw.protocol_version))&&<div><Button loading={props.data.executionHistoryLoading} disabled={props.data.executionHistoryCursors?.events===null} onClick={()=>props.invoke('loadExecutionHistory',{dataset:{kind:'events'}})}>加载更早任务记录</Button>{props.data.executionHistoryError&&<p role="alert">{props.data.executionHistoryError}</p>}</div>}</>;
  if(['execution_v1','execution_v2'].includes(mode))return <><TaskMigration data={props.data} invoke={props.invoke}/><ExecutionTaskDetail {...props}/></>;
  return <section className="ds-panel" aria-label="任务模式待核对"><Alert type="warning" showIcon message="任务响应不完整，暂不能操作" description="任务模式与当前服务协议不匹配，请刷新核对。" action={<Button onClick={()=>props.invoke('loadTask')}>重新读取任务</Button>}/></section>;
}
TaskDetail.subcomponents=(page,d)=>{
  const mode=taskMode(d.task);
  if(mode==='legacy')return LegacyTaskPanel.subcomponents(page,d);
  if(!['execution_v1','execution_v2'].includes(mode))return [];
  return [{selector:'task-cancel-dialog',required:!!d.task,props:{open:d.cancelOpen,taskId:d.taskId,taskVersion:d.task?.version_no || 0}}];
};
