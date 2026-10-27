import React, {useRef, useState} from 'react';
import {Alert, Button, Checkbox, DatePicker, Modal, Progress, Select, Tag} from 'antd';
import dayjs from 'dayjs';
import {SbField} from '@shandiant/ui-react';
import {SbTextarea} from '../ImeInput.jsx';
import {identity, runtime, session} from '../pc/data.js';
import './execution.css';
import GovernancePanel from './GovernancePanel.jsx';
import ChecklistEditor from './ChecklistEditor.jsx';
import {ContributionFields} from '../review/tasks/TaskAlignmentFields.jsx';
import {contributionError,contributionPayload} from '../review/tasks/model.js';
import TaskSyncStatus, {taskSyncUnavailable} from './TaskSyncStatus.jsx';

const titles={complete_self:'完成本人任务',cancel_task:'取消任务',set_item_progress:'更新推进项',pause:'暂停任务',resume:'恢复任务',recheck_pause:'复查暂停任务',withdraw_completion:'撤回验收申请',accept_task:'接受任务',decline_task:'拒绝任务',submit_completion:'提交验收',review_completion:'验收任务',respond_executor_handover:'确认执行交接',respond_reviewer_handover:'确认验收交接',respond_requirements:'确认要求变更',withdraw_requirements:'撤回要求变更'};
export default function ExecutionPanel({page, data:d, invoke, directory={}}) {
  const x=d.execution, [draft,setDraft]=useState({text:'',versions:null}), [feedbackError,setFeedbackError]=useState('');
  const [reworkIds,setReworkIds]=useState([]);
  const [contribution,setContribution]=useState(null),[contributionIssue,setContributionIssue]=useState('');
  const pending=useRef(false), [saving,setSaving]=useState(false);
  const context=JSON.stringify([identity(),d.taskId]), version=JSON.stringify(x?.versions);
  const stale=Boolean(draft.versions && draft.versions!==version);
  const busy=Boolean(d.executionBusy || d.executionLoading || saving), blocked=busy || Boolean(d.executionError) || taskSyncUnavailable(d.webTaskSync);
  const action=(name,itemId)=>{if(name==='reject')setReworkIds([]);if(name==='complete'){setContribution(null);setContributionIssue('');}return invoke('executionAction',{detail:{action:name,itemId}});};
  const contributionCodes=d.task?.alignment?.metric_codes || [];
  const submitDialog=()=>{
    if(!['submit_completion','complete_self'].includes(d.executionDialog?.action))return invoke('submitExecutionDialog');
    const error=contribution && directory.status!=='ready'?'请先读取可用的指标目录':contributionError(contribution,directory.metrics || [],contributionCodes,true);
    setContributionIssue(error);if(error)return;
    return invoke('submitExecutionDialog',{detail:contribution?{actual_contribution:contributionPayload(contribution)}:{}});
  };
  const input=(key,value)=>invoke('executionInput',{dataset:{key},detail:{value}});
  async function feedback(){
    if(pending.current || blocked || !x?.caps.append_feedback || stale)return;
    let command;try{command=runtime().require('utils/taskFeedback').prepare(draft,x.versions);}catch(error){setFeedbackError(error.message);return;}
    pending.current=true;setSaving(true);setFeedbackError('');
    try{
      const success=await page.sendExecution('append_feedback',command);
      if(context!==JSON.stringify([identity(),page.data.taskId]) || page._destroyed)return;
      if(success){setDraft({text:'',versions:null});await page.loadExecution();}
      else setFeedbackError(page.data.executionError || '反馈尚未保存，草稿已保留');
    }finally{pending.current=false;setSaving(false);}
  }
  const p=x ? runtime().require('utils/taskExecutionPresentation').progress(x.progress) : null;
  const dialog=d.executionDialog;
  const reviewReject=dialog?.action==='review_completion' && dialog.decision==='reject';
  const noteLabel=dialog?.action==='set_item_progress'?'成果依据或调整原因':['submit_completion','complete_self'].includes(dialog?.action)?'完成说明':dialog?.action==='review_completion'?'验收意见':'操作原因';
  const feedbackField=(key,value)=>{setDraft({...draft,[key]:value,versions:draft.versions||version});setFeedbackError('');};
  const referenceFeedback=(row,correct)=>{setDraft({...draft,text:draft.text||(correct?row.text:''),feedback_kind:draft.feedback_kind||(correct?row.kind:'comment'),item_id:draft.item_id||row.itemId||'',next_step:draft.next_step||(correct?row.nextStep:''),blocker:draft.blocker||(correct?row.blocker:''),evidence_urls:draft.evidence_urls||(correct?(row.evidenceUrls||[]).join('\n'):''),reply_to_event_id:correct?null:row.id,corrects_event_id:correct?row.id:null,reference:row.text,versions:draft.versions||version});setFeedbackError('');};
  return <section className="task-execution" aria-label="任务执行">
    <div className="task-execution-heading"><h2>任务执行</h2><Button onClick={()=>invoke('refreshWebTasks')} disabled={d.executionBusy || saving || Boolean(d.executionDialog || d.checklistDraft || d.requirementsDraft || d.handoverPurpose || d.migrationOpen)} loading={d.executionLoading}>刷新执行信息</Button></div>
    <TaskSyncStatus sync={d.webTaskSync} lastSyncedLabel={d.executionSynced} />
    {d.executionError && <Alert type="warning" showIcon message={d.executionError} description="填写内容会保留；请刷新核对当前要求和责任人后，再确认提交。"/>}
    {!x && <p role="status">{d.executionLoading?'正在读取推进清单和当前责任…':d.executionError?'任务信息仅供查看，执行操作暂不可用。':'新版任务执行信息尚未就绪，暂不可操作。'}</p>}
    {x && <>
      <div className="task-execution-summary"><div><span className="ds-muted">清单进度</span><strong>{p.label}</strong><span>{p.countLabel}</span></div><div><span className="ds-muted">当前执行人</span><strong>{x.executorLabel}</strong></div><div><span className="ds-muted">当前验收人</span><strong>{x.reviewerLabel}</strong></div></div>
      {p.percent!==null && <Progress percent={p.percent} showInfo={false} status="normal"/>}<p className="ds-muted">{p.hint}</p>
      {x.coordinationRequired&&<Alert type="warning" showIcon message="任务责任或权限发生变化，需要协调处理" description="已有成果保留。请根据可用交接入口调整责任，或联系任务发起人。"/>}
      {x.pause?.active && <Alert type="warning" showIcon message="任务已暂停" description={<><p>{x.pause.reason}</p><p>复查时间：{x.pause.recheckLabel} · 到期提醒不会自动恢复任务</p></>}/>}
      <div className="task-execution-actions">
        {x.caps.accept_task===true && <Button type="primary" disabled={blocked} onClick={()=>action('accept')}>接受任务</Button>}
        {x.caps.decline_task===true && <Button danger disabled={blocked} onClick={()=>action('decline')}>拒绝任务</Button>}
        {x.caps.pause===true && <Button disabled={blocked} onClick={()=>action('pause')}>暂停任务</Button>}
        {x.caps.recheck_pause===true && <Button disabled={blocked} onClick={()=>action('recheck')}>复查暂停情况</Button>}
        {x.caps.resume===true && <Button disabled={blocked} onClick={()=>action('resume')}>恢复任务</Button>}
      </div>
      <section aria-label="推进清单"><div className="task-execution-heading"><h3>推进清单 <small>{x.items.length} 项</small></h3>{x.caps.save_checklist===true&&<Button disabled={blocked} onClick={()=>invoke('openChecklist')}>维护清单</Button>}</div>
        {!x.items.length && <p>尚未设置推进清单，等待执行人补充。</p>}
        <ol className="task-execution-items">{x.items.map((item,index)=><li key={item.item_id}>
          <div className="task-execution-heading"><h4>{index+1}. {item.title}</h4><Tag color={item.state==='completed'?'green':item.state==='rework'?'orange':'default'}>{item.stateLabel}</Tag></div>
          <p><b>验收标准：</b>{item.acceptance_criteria}</p>{item.note && <p><b>成果依据：</b>{item.note}</p>}
          {item.requires_revalidation && <p className="ds-td-error">要求已变化，此项需重新核对。</p>}
          {x.caps.set_item_progress===true && <Button disabled={blocked} onClick={()=>action('progress',item.item_id)} aria-label={`更新推进项 ${index+1}`}>更新进度</Button>}
        </li>)}</ol>
      </section>
      <section aria-label="过程反馈"><h3>过程反馈</h3>
        {x.caps.append_feedback===true && <>
          <SbTextarea aria-label="过程反馈内容" value={draft.text} maxLength={4000} autoSize={{minRows:3,maxRows:8}} disabled={busy} onChange={text=>feedbackField('text',text)} placeholder="记录实际进展、遇到的问题或需要协助的事项"/>
          {draft.reference&&<Alert type="info" message={draft.corrects_event_id?'更正原反馈（保留原文）':'回复反馈'} description={draft.reference} action={<Button onClick={()=>setDraft({...draft,reference:'',reply_to_event_id:null,corrects_event_id:null})}>取消关联</Button>}/>}
          {x.raw.protocol_version===2&&<div className="task-feedback-fields">
            <SbField label="反馈类型"><Select aria-label="反馈类型" value={draft.feedback_kind||'progress'} options={[{value:'progress',label:'执行进展'},{value:'comment',label:'评论 / 协作说明'}]} disabled={busy} onChange={v=>feedbackField('feedback_kind',v)}/></SbField>
            <SbField label="关联推进项"><Select aria-label="关联推进项" allowClear value={draft.item_id||undefined} options={x.items.map(item=>({value:item.item_id,label:item.title}))} disabled={busy} onChange={v=>feedbackField('item_id',v||'')}/></SbField>
            <SbField label="下一步"><SbTextarea aria-label="反馈下一步" value={draft.next_step||''} maxLength={2000} autoSize={{minRows:2,maxRows:4}} disabled={busy} onChange={v=>feedbackField('next_step',v)}/></SbField>
            <SbField label="问题与阻碍"><SbTextarea aria-label="反馈问题与阻碍" value={draft.blocker||''} maxLength={2000} autoSize={{minRows:2,maxRows:4}} disabled={busy} onChange={v=>feedbackField('blocker',v)}/></SbField>
            <SbField label="成果链接"><SbTextarea aria-label="反馈成果链接" value={draft.evidence_urls||''} autoSize={{minRows:2,maxRows:4}} disabled={busy} onChange={v=>feedbackField('evidence_urls',v)} placeholder="每行一个网址，选填"/></SbField>
          </div>}
          {stale && <Alert type="warning" message="任务已更新，反馈草稿已保留" action={<Button disabled={blocked} onClick={()=>setDraft({...draft,versions:version})}>已核对最新要求</Button>}/>}
          {feedbackError && <p role="alert" className="ds-td-error">{feedbackError}</p>}
          <Button type="primary" disabled={blocked || stale} loading={saving} onClick={feedback}>发布过程反馈</Button>
        </>}
        <ol className="task-execution-feedback">{x.feedback.map(row=><li key={row.id}><strong>{row.authorLabel}</strong><span className="ds-muted"> {row.timeLabel} {row.relationLabel}</span><p>{row.text}</p>{row.nextStep&&<p><b>下一步：</b>{row.nextStep}</p>}{row.blocker&&<p><b>问题：</b>{row.blocker}</p>}{row.itemId&&<p className="ds-muted">关联：{x.items.find(i=>i.item_id===row.itemId)?.title||'历史推进项'}</p>}{row.evidenceUrls?.filter(url=>/^https?:\/\//i.test(url)).map(url=><p key={url}><a href={url} target="_blank" rel="noopener noreferrer">查看成果链接</a></p>)}{x.caps.append_feedback&&<div><Button size="small" type="link" disabled={blocked} onClick={()=>referenceFeedback(row,false)}>回复</Button>{row.actorUserId===session()?.userId&&<Button size="small" type="link" disabled={blocked} onClick={()=>referenceFeedback(row,true)}>更正</Button>}</div>}</li>)}</ol>
        {!x.feedback.length && <p className="ds-muted">暂无过程反馈</p>}
        {x.raw.protocol_version===2&&d.executionHistoryCursors?.events!==null&&<Button loading={d.executionHistoryLoading} disabled={blocked} onClick={()=>invoke('loadExecutionHistory',{dataset:{kind:'events'}})}>加载更早反馈与记录</Button>}{d.executionHistoryError&&<p role="alert">{d.executionHistoryError}</p>}
      </section>
      {x.raw.requirements_incomplete&&<Alert type="warning" showIcon message="整体交付物或完成标准尚未补齐，请先修改任务要求。"/>}<section><h3>整体任务要求</h3><p><b>交付物：</b>{x.raw.requirements?.deliverable||'待补充'}</p><p><b>完成标准：</b>{x.raw.requirements?.acceptance_criteria||'待补充'}</p></section><GovernancePanel data={d} invoke={invoke}/>
      <section className="task-execution-submit"><h3>提交与验收</h3><p>{x.raw.status==='completed'?(x.selfManaged?'任务已完成。':'任务已通过验收。'):x.raw.status==='pending_review'?'完成说明已提交，等待当前验收人确认。':x.selfManaged?'清单完成后由本人确认完成，无需验收。':'完成清单后，填写交付结果并提交整体验收。'}</p>
        {['pending_review','completed'].includes(x.raw.status) && <>
          <p><b>完成说明</b></p><blockquote>{x.completionNote}</blockquote>
          {x.raw.status==='completed' && <p><b>{x.selfManaged?'完成时间：':'验收通过时间：'}</b>{x.completedAt?<time dateTime={x.completedAt}>{dayjs(x.completedAt).format('YYYY-MM-DD HH:mm')}</time>:'完成时间待核对'}</p>}
        </>}
        <div className="task-execution-actions">
          {x.caps.submit_completion===true && <Button type="primary" disabled={blocked} onClick={()=>action('complete')}>{x.selfManaged?'完成任务':'提交验收'}</Button>}
          {x.caps.complete_self===true&&<Button type="primary" disabled={blocked} onClick={()=>action('completeSelf')}>确认完成本人任务</Button>}
          {x.caps.withdraw_completion===true && <Button disabled={blocked} onClick={()=>action('withdrawCompletion')}>撤回验收申请</Button>}
          {x.caps.review_completion===true && <><Button type="primary" disabled={blocked} onClick={()=>action('approve')}>确认验收</Button><Button danger disabled={blocked} onClick={()=>action('reject')}>选择返工项并驳回</Button></>}
        </div>
        {x.raw.protocol_version===2&&<><Button disabled={blocked||d.executionHistoryCursors?.submissions===null} loading={d.executionHistoryLoading} onClick={()=>invoke('loadExecutionHistory',{dataset:{kind:'submissions'}})}>查看历史成果提交</Button><ol className="task-execution-feedback">{(d.executionSubmissions||[]).map(item=><li key={item.id}><b>{item.timeLabel}</b><p>{item.note}</p><details><summary>提交时保留 {item.itemCount} 项清单 · 版本 {item.snapshot?.checklist_revision||'—'}</summary><p>交付物：{item.snapshot?.requirements?.deliverable||'未记录'}</p><p>完成标准：{item.snapshot?.requirements?.acceptance_criteria||'未记录'}</p>{(item.checklist||[]).map(row=><div key={row.item_id}><b>{row.title} · {row.stateLabel}</b><p>{row.acceptance_criteria}</p><p>{row.note||'未填写成果依据'}</p></div>)}</details></li>)}</ol></>}
      </section>
    </>}
    <Modal title={dialog?.action==='submit_completion'&&x?.selfManaged?'完成任务':titles[dialog?.action] || '确认操作'} open={Boolean(dialog)} onCancel={()=>invoke('closeExecutionDialog')} closable={!d.executionBusy} maskClosable={false} destroyOnHidden footer={<><Button disabled={d.executionBusy} onClick={()=>invoke('closeExecutionDialog')}>取消</Button><Button type="primary" loading={d.executionBusy} disabled={blocked} onClick={submitDialog}>确认保存</Button></>}>
      {dialog?.action==='set_item_progress' && <SbField label="推进状态"><Select aria-label="推进状态" value={Number(d.executionState)} options={(d.executionStates || []).map((label,value)=>({label,value}))} disabled={d.executionBusy} onChange={v=>input('executionState',v)}/></SbField>}
      {['pause','recheck_pause'].includes(dialog?.action) && <SbField label="复查时间" required><DatePicker aria-label="暂停复查时间" showTime={{format:'HH:mm'}} format="YYYY-MM-DD HH:mm" value={d.executionDate?dayjs(`${d.executionDate}T${d.executionTime}`):null} disabled={d.executionBusy} onChange={v=>{input('executionDate',v?.format('YYYY-MM-DD') || '');input('executionTime',v?.format('HH:mm') || '09:00');}}/></SbField>}
      <SbField label={noteLabel}><SbTextarea aria-label={noteLabel} value={d.executionNote || ''} autoSize={{minRows:4,maxRows:8}} maxLength={['set_item_progress','submit_completion','complete_self','review_completion'].includes(dialog?.action)?4000:1000} disabled={d.executionBusy} onChange={v=>input('executionNote',v)}/></SbField>
      {dialog?.action==='submit_completion'&&x?.raw.missing_deliverables&&<SbField label="遗漏交付处理说明" required><Alert type="warning" message={x.raw.missing_deliverables}/><SbTextarea aria-label="遗漏交付处理说明" value={d.executionMissingResolution||''} maxLength={4000} autoSize={{minRows:3,maxRows:6}} disabled={d.executionBusy} onChange={v=>input('executionMissingResolution',v)}/></SbField>}
      {dialog?.action==='submit_completion' && <><ContributionFields actual value={contribution} metrics={directory.metrics || []} codes={directory.status==='ready'?contributionCodes:[]} onChange={value=>{setContribution(value);setContributionIssue('');}} disabled={d.executionBusy} error={contributionIssue}/>{d.task?.alignment?.actual_contribution && <p className="ds-muted">已有实际贡献申报；本次不填写则保留原申报，不会清空。</p>}{directory.status==='error' && <Alert type="warning" message="指标目录读取失败，暂不能填写新的贡献申报；可重试读取或仅提交完成说明。" action={<Button onClick={directory.reload}>重试指标目录</Button>}/>}</>}
      {reviewReject && <><SbField label="遗漏的整体交付"><SbTextarea aria-label="遗漏的整体交付" value={d.executionMissingDeliverables||''} maxLength={4000} autoSize={{minRows:2,maxRows:4}} disabled={d.executionBusy} onChange={v=>input('executionMissingDeliverables',v)}/></SbField><SbField label="需要返工的推进项"><Checkbox.Group value={reworkIds} disabled={d.executionBusy} onChange={value=>{setReworkIds(value);invoke('executionRework',{detail:{value}});}}>{x?.items.map(item=><Checkbox className="task-rework-option" key={item.item_id} value={item.item_id}>{item.title}</Checkbox>)}</Checkbox.Group></SbField></>}
      {d.executionDialogError && <Alert type="error" showIcon message={d.executionDialogError}/>}
    </Modal>
    <ChecklistEditor data={d} invoke={invoke}/>
  </section>;
}
