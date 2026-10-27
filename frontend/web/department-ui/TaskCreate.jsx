import {Input, SbTextarea} from './ImeInput.jsx';
import {PersonPickerDialog} from './NativeDialogs.jsx';
import React, { useMemo, useState } from 'react';
import {Alert, Button, DatePicker, Form, Modal, Radio} from 'antd';
import dayjs from 'dayjs';
import {SbBottomBar, SbField, SbSelect, SbStatePanel} from '@shandiant/ui-react';
import './tasks.css';
import {runtime, session} from './pc/data.js';
import TaskAlignmentFields from './review/tasks/TaskAlignmentFields.jsx';
import {alignmentError, alignmentPayload, emptyAlignment} from './review/tasks/model.js';
import {useTaskAlignmentDirectory} from './review/tasks/useTaskAlignmentDirectory.js';
import {submitAlignedTask} from './review/tasks/create-adapter.js';

const handlers = ['changeTaskType', 'inputDescription', 'openRecipients', 'confirmRecipients', 'selectPriority', 'changeDueDate', 'changeDueTime', 'submitTask', 'toggleTaskVoice', 'openTaskSelector', 'closeTaskSelector', 'searchTaskChoices', 'chooseTaskLink', 'loadTaskChoices', 'moreTaskChoices', 'hydrateTaskLink', 'retryRecipients'];

/** A newer upstream shape should use the existing renderer instead of a partial form. */
export function supportsTaskCreate(page, data = page?.data) {
  return Boolean(page && data && handlers.every(name => typeof page[name] === 'function') && Array.isArray(data.priorities) && Array.isArray(data.members));
}

export default function TaskCreate({ page, data, invoke, invokeOn, select }) {
  const [attempted, setAttempted] = useState(false);
  const alignmentDirectory = useTaskAlignmentDirectory(!data.accessBlocked);
  // A creation-rollout change preserves the draft; identity and other grants still isolate it.
  const actor = session();
  const formOwner = runtime().require('utils/access').identity({...actor, capabilities: {...actor?.capabilities, task_execution: null}});
  const [alignmentState, setAlignmentState] = useState(() => {
    const retained = page._webTaskAlignmentDraft;
    const state = {owner: formOwner, draft: retained?.owner === formOwner ? retained.draft : emptyAlignment()};
    page._webTaskAlignmentDraft = state;
    return state;
  });
  const alignment = alignmentState.owner === formOwner ? alignmentState.draft : emptyAlignment();
  const changeAlignment = draft => {
    const state = {owner: formOwner, draft};
    page._webTaskAlignmentDraft = state;
    setAlignmentState(state);
  };
  const alignmentIssue = alignmentDirectory.status === 'ready' ? alignmentError(alignment, alignmentDirectory.metrics, alignmentDirectory.keyResults, alignmentDirectory.targets) : '';
  const call = (name, payload = {}) => invoke(name, payload);
  const voiceBusy = Boolean(data.isStarting || data.isRecording || data.isStopping || data.isParsing);
  const locked = Boolean(data.submitting || data.submissionPending);
  const support = session()?.capabilities?.task_execution;
  const creation = runtime().require('utils/taskExecutionMode').creationSupport(support);
  const executionReady = (data.executionMode==='execution_v3'?creation.v3:data.executionMode==='execution_v2'?creation.v2:creation.v1) && typeof page.chooseExecutionMode === 'function';
  const executionMode = data.executionMode || 'legacy';
  const lightweightMode = executionMode==='execution_v3';
  const checklistMode = ['execution_v1','execution_v2'].includes(executionMode);
  const selfManagedMode=executionMode==='execution_v2';
  const modeBlocked = (checklistMode || lightweightMode) && !executionReady;
  const customerTask = data.taskType === 'customer';
  // Directory/voice updates must not reset an in-progress date picker edit.
  const due = useMemo(() => data.customDueDate && data.customDueTime ? dayjs(`${data.customDueDate}T${data.customDueTime}`) : null, [data.customDueDate, data.customDueTime]);
  const badDue = !due?.isValid() || due.valueOf() <= Date.now();
  const descriptionLength = [...String(data.description || '').trim()].length;
  const errors = {
    description: descriptionLength > 500 ? '任务描述最多填写 500 字，请修改后提交' : attempted && descriptionLength < 5 ? '任务描述要写清做什么，至少 5 个字' : '',
    member: attempted && !data.selectedMembers?.length ? '请选择任务执行人' : '',
    customer: attempted && customerTask && !data.customerId ? '先选一个客户' : '',
    opportunity: attempted && customerTask && data.customerId && !data.opportunityId ? '先选这个客户的商机' : '',
    due: (attempted || due?.isValid()) && badDue ? '截止时间要晚于现在' : '',
  };
  const voiceTitle = data.isStarting ? '正在启动录音' : data.isStopping ? '正在结束录音' : data.isParsing ? '正在识别任务内容' : data.isRecording ? `正在录音 ${data.recordingTime}` : data.voiceFilled ? '继续语音录入' : '语音录入';
  const send = () => {
    setAttempted(true);
    if (data.submissionPending) return submitAlignedTask(page, null, call);
    if (modeBlocked || alignmentDirectory.status !== 'ready' || alignmentIssue) return;
    const payload = alignmentPayload(alignment, alignmentDirectory.metrics, alignmentDirectory.keyResults, alignmentDirectory.targets);
    return submitAlignedTask(page, payload, call);
  };
  const changeDue = value => {
    if (!value?.isValid()) return;
    call('changeDueDate', {detail: {value: value.format('YYYY-MM-DD')}});
    call('changeDueTime', {detail: {value: value.format('HH:mm')}});
  };
  if (data.accessBlocked) return <SbStatePanel state="forbidden" title="此功能暂不可用" description={data.accessMessage} />;
  if (data.adviceLoading) return <SbStatePanel state="loading" title="正在读取 AI 建议" />;
  if (data.adviceError) return <section><SbStatePanel state="error" title="当前建议暂不可用" description={data.adviceError}/><Button onClick={() => globalThis.SalesRuntime.wx.navigateBack()}>返回建议所在页面</Button></section>;
  return (
    <section className="department-task-create" aria-label="创建任务">
      <Form layout="vertical" className="department-task-form" onFinish={send}>
        <div className="department-task-create-grid">
          <section className="department-task-form-main department-task-form-panel" aria-label="任务内容">
            <h2>任务内容</h2>{lightweightMode && <p className="ds-muted">保存后直接进行中；自办任务直接完成，他人任务提交后由下发人验收。</p>}
            <SbField label="任务类型" required>
              <Radio.Group aria-label="任务类型" value={data.taskType} disabled={locked || Boolean(data.adviceSource || data.adviceLoading)} onChange={event => call('changeTaskType', {dataset: {type: event.target.value}})} options={[{value: 'daily', label: '日常工作任务'}, {value: 'customer', label: '客户任务'}]} optionType="button" />
            </SbField>
            {(data.adviceSource || data.adviceLoading || data.adviceError) && <div className={`department-task-advice${data.adviceError ? ' department-task-advice-error' : ''}`} role={data.adviceError ? 'alert' : 'status'}><strong>{data.adviceLoading ? '正在读取 AI 建议…' : data.adviceError || `来自 AI 建议：${data.adviceSource.title}`}</strong></div>}
            {customerTask && <div className="department-task-link-fields">
              <SbField label="客户" required error={errors.customer} help={data.adviceSource ? 'AI 建议关联 · 已固定' : undefined}><Button className="department-task-choice" disabled={locked || Boolean(data.adviceLoading || data.adviceSource)} onClick={() => call('openTaskSelector', {dataset: {kind: 'customer'}})}>{data.customerName || (data.customerId ? '正在核对客户…' : '选择客户')}<span aria-hidden>⌄</span></Button></SbField>
              <SbField label="商机" required error={errors.opportunity} help={data.adviceSource?.opportunityFixed ? 'AI 建议关联 · 已固定' : undefined}><Button className="department-task-choice" disabled={locked || !data.customerId || Boolean(data.adviceLoading || (data.adviceSource && page.adviceOpportunityId))} onClick={() => call('openTaskSelector', {dataset: {kind: 'opportunity'}})}>{data.opportunityName || (data.opportunityId ? '正在核对商机…' : '选择商机')}<span aria-hidden>⌄</span></Button></SbField>
              {(data.linkError || data.linkLoading) && <div className="department-task-link-feedback" role={data.linkError ? 'alert' : 'status'}>{data.linkError || '正在核对关联信息…'}{data.linkError && <Button type="link" onClick={() => call('hydrateTaskLink')}>重试关联信息</Button>}</div>}
            </div>}
            <SbField label="任务描述" required error={errors.description}>
              <SbTextarea id="department-task-description" aria-label="任务描述" aria-invalid={Boolean(errors.description)} value={data.description || ''} maxLength={500} autoSize={{minRows: 7, maxRows: 12}} disabled={voiceBusy || locked} placeholder="输入任务描述" onChange={value => call('inputDescription', {detail: {value}})} />
            </SbField>
            {!lightweightMode && !selfManagedMode && support?.protocol_version!==2 && (executionReady || checklistMode) && <SbField label="执行方式" help={checklistMode ? '按推进清单记录进度，完成后单独提交验收。' : '按原有流程接收、反馈完成并验收。'}>
              <Radio.Group aria-label="任务执行方式" value={executionMode} disabled={locked || voiceBusy} onChange={e=>call('chooseExecutionMode',{dataset:{mode:e.target.value}})} options={[{value:'legacy',label:'标准任务'},{value:'execution_v1',label:'清单任务',disabled:!executionReady}]} optionType="button"/>
            </SbField>}
            {data.executionUpgradeAvailable&&<Alert type="info" message="新版任务流程已可用" description="已有填写内容会保留。切换后直接进行中，通过文字反馈更新；自办任务填写结果直接完成。" action={<Button disabled={locked} onClick={()=>call('chooseExecutionMode',{dataset:{mode:creation.v3?'execution_v3':'execution_v2'}})}>保留填写并切换</Button>}/>}
            {selfManagedMode && <><SbField label="整体交付要求" required><SbTextarea aria-label="整体交付要求" value={data.deliverable || ''} maxLength={4000} autoSize={{minRows:2,maxRows:5}} disabled={locked} onChange={value=>call('inputTaskRequirements',{dataset:{key:'deliverable'},detail:{value}})} placeholder="明确这项任务最终需要交付什么"/></SbField><SbField label="整体完成标准" required><SbTextarea aria-label="整体完成标准" value={data.acceptanceCriteria || ''} maxLength={4000} autoSize={{minRows:2,maxRows:5}} disabled={locked} onChange={value=>call('inputTaskRequirements',{dataset:{key:'acceptanceCriteria'},detail:{value}})} placeholder="明确怎样判断任务已经完成"/></SbField></>}
            {checklistMode && <section className="task-execution-create" aria-label="推进清单">
              <h3>推进清单</h3><p className="ds-muted">{selfManagedMode?'可以先不填写，执行人接收后自主维护；本人负责的任务创建后直接待执行。':'把任务拆成可验收的具体事项。至少一项，每项写明成果标准。'}</p>
              {(data.checklist || []).map((item, index) => <div className="task-checklist-editor" key={item.id}>
                <SbField label={`第 ${index + 1} 项`} required><Input aria-label={`推进项 ${index + 1}`} value={item.title} maxLength={500} disabled={locked} onChange={e => call('changeChecklistItem', {dataset:{id:item.id,key:'title'},detail:{value:e.target.value}})} placeholder="例如：提交客户方案初稿"/></SbField>
                <SbField label="验收标准" required><SbTextarea aria-label={`验收标准 ${index + 1}`} value={item.acceptance_criteria} maxLength={2000} autoSize={{minRows:2,maxRows:5}} disabled={locked} onChange={value=>call('changeChecklistItem',{dataset:{id:item.id,key:'acceptance_criteria'},detail:{value}})} placeholder="例如：包含需求、实施范围和报价，完成内部评审"/></SbField>
                <Button disabled={locked} onClick={()=>call('removeChecklistItem',{dataset:{id:item.id}})}>移除第 {index + 1} 项</Button>
              </div>)}
              <Button disabled={locked || !executionReady || data.checklist?.length >= 100} onClick={()=>call('addChecklistItem')}>添加推进项</Button>
              {data.checklistError && <Alert type="error" showIcon message={data.checklistError}/>}
              {!executionReady && <Alert type="info" showIcon message="任务创建暂不可用，填写内容已保留，请稍后重试。"/>}
            </section>}
            <div className="department-task-voice">
              <Button disabled={locked || Boolean(data.isStarting || data.isStopping || data.isParsing)} danger={data.isRecording} onClick={() => call('toggleTaskVoice')}>{data.isRecording ? '结束录音' : voiceTitle}</Button>
              {(data.isRecording || data.voiceFilled) && <span aria-live="polite">{data.isRecording ? data.recordingTime : '语音已转成文字，核对一下'}</span>}
            </div>
            {alignmentDirectory.status === 'ready' && <TaskAlignmentFields draft={alignment} onChange={changeAlignment} directory={alignmentDirectory} disabled={locked || data.submissionPending} error={attempted ? alignmentIssue : ''}/>}
            {alignmentDirectory.status === 'loading' && <div className="review-task-load-state" role="status">正在核对任务目标关联能力…</div>}
            {alignmentDirectory.status === 'error' && <Alert className="review-task-load-state" type="error" showIcon message={alignmentDirectory.error} action={<Button onClick={alignmentDirectory.reload}>重试指标目录</Button>}/>}
            {alignmentDirectory.status === 'unavailable' && <Alert className="review-task-load-state" type="info" message="当前服务尚不支持任务目标关联，请完成配套升级后再下发任务。"/>}
          </section>
          <aside className="department-task-form-panel department-task-settings" aria-label="执行人和执行设置">
            <h2>执行设置</h2>
            <SbField label="执行人" required error={errors.member || data.recipientError}>
              <Button className="department-task-choice" disabled={locked} loading={data.recipientLoading} onClick={()=>call('openRecipients')}>{data.selectedMembers?.length ? `已选 ${data.selectedMembers.length} 人：${data.selectedNames}` : '选择执行人，可多选'}</Button>
            </SbField>
            {data.recipientError && <Button type="link" onClick={() => call('retryRecipients')}>重新加载执行人</Button>}
            <SbField label="优先级" required><SbSelect aria-label="任务优先级" value={data.selectedPriority} disabled={locked} options={data.priorities.map(value => ({value, label: value}))} onChange={value => call('selectPriority', {dataset: {value}})} /></SbField>
            <SbField label="截止时间" required error={errors.due}>
              {/* 截止时间要到时分，SbDatePicker 只出日期，这里仍用 antd DatePicker */}
              <DatePicker aria-label="任务截止时间" placeholder="选择截止时间" value={due?.isValid() ? due : null} format="YYYY-MM-DD HH:mm" showTime={{format: 'HH:mm'}} allowClear={false} disabled={locked} status={errors.due ? 'error' : undefined} onChange={changeDue} disabledDate={value => Boolean((data.minDueDate && value.format('YYYY-MM-DD') < data.minDueDate) || (data.maxDueDate && value.format('YYYY-MM-DD') > data.maxDueDate))} />
            </SbField>
            {data.selectedMembers?.length > 0 && <div className="department-task-delivery"><strong>将发送给 {data.selectedNames}</strong><p>{data.selectedMembers.length} 条独立任务 · {data.selectedPriority}优先级</p><p>截止 {data.selectedDue}</p></div>}
          </aside>
        </div>
        <SbBottomBar primary={{label: data.submissionPending ? '重试确认发送结果' : '下发任务', loading: Boolean(data.submitting), loadingLabel: '下发中…', disabled: (!data.submissionPending && modeBlocked) || voiceBusy || Boolean(data.adviceLoading || data.adviceError) || (!data.submissionPending && alignmentDirectory.status !== 'ready'), disabledReason: !data.submissionPending && modeBlocked ? '任务创建当前不可用' : voiceBusy ? '先录完这段语音' : data.adviceLoading ? '正在读取建议' : data.adviceError || (alignmentDirectory.status === 'loading' ? '正在加载指标目录' : alignmentDirectory.status === 'error' ? '请重新加载指标目录' : alignmentDirectory.status === 'unavailable' ? '需配套升级后下发任务' : undefined), onClick: send}} reason={data.adviceError || undefined} />
      </Form>
      <PersonPickerDialog instance={select('#personPicker')} invokeOn={invokeOn} />
      {data.submissionPending && !data.submitting && <p role="status">发送结果尚未确认；重试将核对原提交，不会重新派发。</p>}
      <Modal rootClassName="department-ui department-task-choice-modal" title={data.selectorKind === 'customer' ? '选择客户' : '选择商机'} open={Boolean(data.selectorOpen)} onCancel={() => call('closeTaskSelector')} footer={null} destroyOnHidden width={600}>
        <Input aria-label={data.selectorKind === 'customer' ? '搜索客户名称' : '搜索当前客户的商机'} value={data.selectorQuery || ''} allowClear maxLength={100} placeholder={data.selectorKind === 'customer' ? '搜索客户名称' : '搜索当前客户的商机'} onChange={event => call('searchTaskChoices', {detail: {value: event.target.value}})} />
        <div className="department-task-choice-results">
          {(data.selectorRows || []).map(row => <Button key={row.id} className="department-task-selector-row" onClick={() => call('chooseTaskLink', {dataset: {id: row.id}})}><span>{row.name}</span><span>选择</span></Button>)}
          <SbStatePanel state={data.selectorError ? 'error' : data.selectorLoading ? 'loading' : !data.selectorRows?.length ? 'empty' : 'normal'} description={data.selectorError || (data.selectorKind === 'customer' ? '没有匹配的客户。换个关键词，或先登记商机。' : '没有匹配的商机。换个关键词。')} onRetry={() => call('loadTaskChoices')} />
          {data.selectorMore && !data.selectorLoading && !data.selectorError && <Button className="department-task-selector-more" onClick={() => call('moreTaskChoices')}>加载更多</Button>}
        </div>
      </Modal>
    </section>
  );
}

TaskCreate.subcomponents = (page,d) => [{selector:'#personPicker',required:!d.accessBlocked,props:{open:d.recipientOpen,members:d.members,teams:d.recipientTeams,defaultTeamId:d.recipientDefaultTeamId,selected:d.selectedRecipientIds,multiple:true,allowAll:false,maxSelected:100,title:'选择执行人',loading:d.recipientLoading,error:d.recipientError}}];
