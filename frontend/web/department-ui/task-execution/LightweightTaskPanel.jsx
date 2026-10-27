import React, {useMemo} from 'react';
import {Alert, Button, DatePicker, Modal, Select} from 'antd';
import dayjs from 'dayjs';
import {SbField, SbStatusTag} from '@shandiant/ui-react';
import {SbTextarea} from '../ImeInput.jsx';
import TaskSyncStatus, {taskSyncUnavailable} from './TaskSyncStatus.jsx';

const titles = {append_feedback:'更新进展', defer:'延期', request_adjustment:'申请调整', pause:'暂停任务',
  resume:'恢复推进', submit_completion:'提交完成', review_approve:'验收通过', review_reject:'退回继续',
  revise:'修改任务安排', continue_original:'按原安排继续', close:'结束任务', adopt:'接入新任务流程'};
const noteLabels = {defer:'延期原因', request_adjustment:'实际情况', pause:'暂停说明', resume:'恢复说明',
  submit_completion:'完成结果', review_approve:'验收意见（选填）', review_reject:'退回原因',
  continue_original:'继续推进说明', close:'结束原因', adopt:'接入说明'};

/** All actions and immutable retry/version bindings belong to the shared page. */
export function LightweightTaskDialog({data:d, invoke}) {
  const dialog=d.lightweightDialog, action=dialog?.action;
  const busy=Boolean(d.executionBusy);
  const field=(key,value)=>invoke('inputLightweight',{dataset:{key},detail:{value}});
  const date=useMemo(()=>dialog?.dueDate?dayjs(`${dialog.dueDate}T${dialog.dueTime || '17:00'}`):null,[dialog?.dueDate,dialog?.dueTime]);
  const actions=d.lightweightActionOptions || [];
  const recipients=d.lightweightRecipients || [], executorId=dialog?.executor_user_id;
  const executorName=recipients.find(row=>row.user_id===executorId)?.name ||
    (executorId===(d.execution?.raw?.executor_user_id || d.execution?.raw?.executor?.user_id) ? d.execution?.executorLabel : '') || '姓名暂不可用';
  const disabled=busy || d.executionLoading || !!d.executionError || taskSyncUnavailable(d.webTaskSync);
  return <Modal rootClassName="department-ui" width={620} title={titles[action] || '更新任务'} open={Boolean(dialog)}
    maskClosable={false} closable={!busy} onCancel={()=>invoke('closeLightweightUpdate')} destroyOnHidden
    footer={<><Button disabled={busy} onClick={()=>invoke('closeLightweightUpdate')}>取消</Button><Button type="primary" loading={busy} disabled={disabled} onClick={()=>invoke('submitLightweight')}>确认保存</Button></>}>
    {action!=='adopt' && <SbField label="本次更新"><Select aria-label="本次更新" value={action} disabled={busy} options={actions}
      onChange={value=>invoke('chooseLightweightAction',{detail:{value}})}/></SbField>}
    {action==='append_feedback' && <><SbField label="最新反馈" required><SbTextarea aria-label="最新反馈" value={dialog?.text || ''} maxLength={4000} autoSize={{minRows:4,maxRows:10}} disabled={busy} onChange={value=>field('text',value)}/></SbField>
      <SbField label="下一步" required={d.execution?.raw?.status==='in_progress'}><SbTextarea aria-label="下一步" value={dialog?.next_step || ''} maxLength={2000} autoSize={{minRows:2,maxRows:5}} disabled={busy} onChange={value=>field('next_step',value)}/></SbField>
      <p className="ds-muted">保存反馈后保持当前状态及已有安排。</p></>}
    {action==='revise' && <><SbField label="任务内容" required><SbTextarea aria-label="任务内容" value={dialog?.description || ''} maxLength={500} autoSize={{minRows:4,maxRows:8}} disabled={busy} onChange={value=>field('description',value)}/></SbField>
      <SbField label="执行人" required><Select aria-label="执行人" showSearch optionFilterProp="label" labelInValue value={executorId?{value:executorId,label:executorName}:undefined}
        loading={d.lightweightRecipientsLoading} disabled={busy} placeholder="选择执行人"
        options={recipients.map(row=>({value:row.user_id,label:row.name}))}
        onChange={option=>invoke('chooseLightweightExecutor',{detail:{value:recipients.findIndex(row=>row.user_id===option.value)}})}/></SbField>
      {d.lightweightRecipientsError && <Alert type="error" message={d.lightweightRecipientsError} action={<Button onClick={()=>invoke('loadLightweightRecipients')}>重试加载</Button>}/>}
      <p className="ds-muted">实际修改内容、日期或执行人后自动进行中；原反馈与提交结果保留在历史中。</p></>}
    {['defer','revise'].includes(action) && <SbField label={action==='defer'?'新的截止时间':'截止时间'} required><DatePicker aria-label="截止时间" showTime={{format:'HH:mm'}} format="YYYY-MM-DD HH:mm" allowClear={false} value={date?.isValid()?date:null} disabled={busy}
      onChange={value=>{field('dueDate',value?.format('YYYY-MM-DD') || '');field('dueTime',value?.format('HH:mm') || '17:00');}}/></SbField>}
    {['review_approve','review_reject'].includes(action) && <section aria-label="本轮完成结果"><h3>本轮完成结果</h3><p className="ds-td-desc">{d.execution?.currentSubmissionNote || '本轮结果暂不可用，请刷新后核对'}</p></section>}
    {action && !['append_feedback','revise'].includes(action) && <SbField label={noteLabels[action] || '说明'} required={!['review_approve','continue_original','adopt'].includes(action)}>
      <SbTextarea aria-label={noteLabels[action] || '说明'} value={dialog?.note || dialog?.reason || ''} maxLength={['submit_completion','review_approve','review_reject'].includes(action)?4000:1000} autoSize={{minRows:4,maxRows:10}} disabled={busy} onChange={value=>{field('note',value);field('reason',value);}}/>
    </SbField>}
    {action==='submit_completion' && <p className="ds-muted">{d.execution?.selfManaged?'保存结果后直接完成。':'保存结果后进入待验收，由任务下发人确认。'}</p>}
    {action==='close' && <Alert type="warning" message="结束后将不再推进" description="结束原因会保留；此操作不代表目标已经达成。"/>}
    {action==='adopt' && <p>仅接入当前任务，保留原始反馈、清单和提交历史，不自动通过旧验收。</p>}
    {d.lightweightDialogError && <Alert type="error" showIcon message={d.lightweightDialogError}/>}
  </Modal>;
}

export default function LightweightTaskPanel({data:d, invoke}) {
  const task=d.task, x=d.execution, blocked=Boolean(d.executionBusy || d.executionLoading || d.executionError || taskSyncUnavailable(d.webTaskSync));
  const update=action=>invoke('openLightweightUpdate',{dataset:action?{action}:{}});
  return <section className="task-lightweight-panel" aria-label="任务进展">
    <div className="task-execution-heading"><h2>任务进展</h2><Button loading={d.executionLoading} disabled={d.executionBusy || Boolean(d.lightweightDialog)} onClick={()=>invoke('refreshWebTasks')}>刷新</Button></div>
    <TaskSyncStatus sync={d.webTaskSync} lastSyncedLabel={d.executionSynced}/>
    {d.executionError && <Alert type="warning" showIcon message={d.executionError} description="填写内容已保留，请刷新核对后继续。"/>}
    {!x && <p role="status">{d.executionLoading?'正在读取任务进展…':'任务进展暂不可用，请重试。'}</p>}
    {x && <>
      <div className="task-lightweight-state"><SbStatusTag tone={task.isSuccessful?'good':'pending'} label={x.statusLabel}/>{x.processingLabel && <strong>{x.processingLabel}</strong>}</div>
      {x.processingNote && <p className="ds-td-desc">{x.processingNote}</p>}
      {x.latestFeedback && <section className="ds-td-card"><h3>最新反馈</h3><p className="ds-td-desc">{x.latestFeedback}</p>{x.nextStep && <p className="ds-td-desc"><b>下一步：</b>{x.nextStep}</p>}</section>}
      {x.currentSubmissionNote && <section className="ds-td-card"><h3>本轮完成结果</h3><p className="ds-td-desc">{x.currentSubmissionNote}</p></section>}
      {x.completionResult && task.status==='completed' && <section className="ds-td-card"><h3>{x.completionLabel || '完成结果'}</h3><p className="ds-td-desc">{x.completionResult}</p></section>}
      <div className="task-execution-actions">
        {x.canUpdate && <Button type="primary" disabled={blocked} onClick={()=>update()}>更新任务</Button>}
        {x.caps.review_completion && <><Button disabled={blocked} onClick={()=>update('review_approve')}>验收通过</Button><Button disabled={blocked} onClick={()=>update('review_reject')}>退回继续</Button></>}
      </div>
      <section className="ds-td-card"><h3>提交与验收历史</h3>
        {(d.executionSubmissions || []).map(row=><div className="task-lightweight-submission" key={row.id}><b>{row.timeLabel}</b><p className="ds-td-desc">{row.note}</p></div>)}
        {!d.executionSubmissions?.length && <p className="ds-muted">暂无已加载的历史提交</p>}
        {d.executionHistoryCursors?.submissions!==null && <Button disabled={blocked || d.executionHistoryLoading} loading={d.executionHistoryLoading} onClick={()=>invoke('loadExecutionHistory',{dataset:{kind:'submissions'}})}>加载历史提交</Button>}
      </section>
    </>}
    <LightweightTaskDialog data={d} invoke={invoke}/>
  </section>;
}
