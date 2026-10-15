import {Input, SbTextarea} from './ImeInput.jsx';
import {PersonPickerDialog} from './NativeDialogs.jsx';
import React, { useMemo, useState } from 'react';
import {Alert, Button, DatePicker, Form, Modal, Radio} from 'antd';
import dayjs from 'dayjs';
import {SbBottomBar, SbField, SbSelect, SbStatePanel} from '@shandiant/ui-react';
import './tasks.css';
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
  const [alignmentState, setAlignmentState] = useState(() => ({owner: alignmentDirectory.owner, draft: emptyAlignment()}));
  const alignment = alignmentState.owner === alignmentDirectory.owner ? alignmentState.draft : emptyAlignment();
  const changeAlignment = draft => setAlignmentState({owner: alignmentDirectory.owner, draft});
  const alignmentIssue = alignmentDirectory.status === 'ready' ? alignmentError(alignment, alignmentDirectory.metrics, alignmentDirectory.keyResults, alignmentDirectory.targets) : '';
  const call = (name, payload = {}) => invoke(name, payload);
  const voiceBusy = Boolean(data.isStarting || data.isRecording || data.isStopping || data.isParsing);
  const locked = Boolean(data.submitting);
  const customerTask = data.taskType === 'customer';
  // Directory/voice updates must not reset an in-progress date picker edit.
  const due = useMemo(() => data.customDueDate && data.customDueTime ? dayjs(`${data.customDueDate}T${data.customDueTime}`) : null, [data.customDueDate, data.customDueTime]);
  const badDue = !due?.isValid() || due.valueOf() <= Date.now();
  const descriptionLength = [...String(data.description || '').trim()].length;
  const errors = {
    description: descriptionLength > 500 ? '任务描述最多填写 500 字，请修改后提交' : attempted && descriptionLength < 5 ? '任务描述要写清做什么，至少 5 个字' : '',
    member: attempted && !data.selectedMembers?.length ? '任务要有负责人' : '',
    customer: attempted && customerTask && !data.customerId ? '先选一个客户' : '',
    opportunity: attempted && customerTask && data.customerId && !data.opportunityId ? '先选这个客户的商机' : '',
    due: (attempted || due?.isValid()) && badDue ? '截止时间要晚于现在' : '',
  };
  const voiceTitle = data.isStarting ? '正在启动录音' : data.isStopping ? '正在结束录音' : data.isParsing ? '正在识别任务内容' : data.isRecording ? `正在录音 ${data.recordingTime}` : data.voiceFilled ? '继续语音录入' : '语音录入';
  const send = () => {
    setAttempted(true);
    if (data.submissionPending) return submitAlignedTask(page, null, call);
    if (alignmentDirectory.status !== 'ready' || alignmentIssue) return;
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
            <h2>任务内容</h2>
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
            <div className="department-task-voice">
              <Button disabled={locked || Boolean(data.isStarting || data.isStopping || data.isParsing)} danger={data.isRecording} onClick={() => call('toggleTaskVoice')}>{data.isRecording ? '结束录音' : voiceTitle}</Button>
              {(data.isRecording || data.voiceFilled) && <span aria-live="polite">{data.isRecording ? data.recordingTime : '语音已转成文字，核对一下'}</span>}
            </div>
            {alignmentDirectory.status === 'ready' && <TaskAlignmentFields draft={alignment} onChange={changeAlignment} directory={alignmentDirectory} disabled={locked || data.submissionPending} error={attempted ? alignmentIssue : ''}/>}
            {alignmentDirectory.status === 'loading' && <div className="review-task-load-state" role="status">正在核对任务目标关联能力…</div>}
            {alignmentDirectory.status === 'error' && <Alert className="review-task-load-state" type="error" showIcon message={alignmentDirectory.error} action={<Button onClick={alignmentDirectory.reload}>重试指标目录</Button>}/>}
            {alignmentDirectory.status === 'unavailable' && <Alert className="review-task-load-state" type="info" message="当前服务尚不支持任务目标关联，请完成配套升级后再下发任务。"/>}
          </section>
          <aside className="department-task-form-panel department-task-settings" aria-label="负责人和执行设置">
            <h2>执行设置</h2>
            <SbField label="任务负责人" required error={errors.member || data.recipientError}>
              <Button className="department-task-choice" disabled={locked} loading={data.recipientLoading} onClick={()=>call('openRecipients')}>{data.selectedMembers?.length ? `已选 ${data.selectedMembers.length} 人：${data.selectedNames}` : '选择任务负责人，可多选'}</Button>
            </SbField>
            {data.recipientError && <Button type="link" onClick={() => call('retryRecipients')}>重新加载负责人</Button>}
            <SbField label="优先级" required><SbSelect aria-label="任务优先级" value={data.selectedPriority} disabled={locked} options={data.priorities.map(value => ({value, label: value}))} onChange={value => call('selectPriority', {dataset: {value}})} /></SbField>
            <SbField label="截止时间" required error={errors.due}>
              {/* 截止时间要到时分，SbDatePicker 只出日期，这里仍用 antd DatePicker */}
              <DatePicker aria-label="任务截止时间" placeholder="选择截止时间" value={due?.isValid() ? due : null} format="YYYY-MM-DD HH:mm" showTime={{format: 'HH:mm'}} allowClear={false} disabled={locked} status={errors.due ? 'error' : undefined} onChange={changeDue} disabledDate={value => Boolean((data.minDueDate && value.format('YYYY-MM-DD') < data.minDueDate) || (data.maxDueDate && value.format('YYYY-MM-DD') > data.maxDueDate))} />
            </SbField>
            {data.selectedMembers?.length > 0 && <div className="department-task-delivery"><strong>将发送给 {data.selectedNames}</strong><p>{data.selectedMembers.length} 条独立任务 · {data.selectedPriority}优先级</p><p>截止 {data.selectedDue}</p></div>}
          </aside>
        </div>
        <SbBottomBar primary={{label: data.submissionPending ? '重试确认发送结果' : '下发任务', loading: locked, loadingLabel: '下发中…', disabled: voiceBusy || Boolean(data.adviceLoading || data.adviceError) || (!data.submissionPending && alignmentDirectory.status !== 'ready'), disabledReason: voiceBusy ? '先录完这段语音' : data.adviceLoading ? '正在读取建议' : data.adviceError || (alignmentDirectory.status === 'loading' ? '正在加载指标目录' : alignmentDirectory.status === 'error' ? '请重新加载指标目录' : alignmentDirectory.status === 'unavailable' ? '需配套升级后下发任务' : undefined), onClick: send}} reason={data.adviceError || undefined} />
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

TaskCreate.subcomponents = (page,d) => [{selector:'#personPicker',required:!d.accessBlocked,props:{open:d.recipientOpen,members:d.members,teams:d.recipientTeams,defaultTeamId:d.recipientDefaultTeamId,selected:d.selectedRecipientIds,multiple:true,allowAll:false,maxSelected:100,title:'选择任务负责人',loading:d.recipientLoading,error:d.recipientError}}];
