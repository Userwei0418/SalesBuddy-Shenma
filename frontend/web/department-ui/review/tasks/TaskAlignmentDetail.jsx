import React, {useState} from 'react';
import {Alert, Button} from 'antd';
import {identity, runtime} from '../../pc/data.js';
import TaskAlignmentFields, {TaskAlignmentSummary} from './TaskAlignmentFields.jsx';
import {alignmentDraft, alignmentError, alignmentPayload, effectiveMetricCodes} from './model.js';
import {saveTaskAlignment} from './api.js';

export default function TaskAlignmentDetail({task, page, directory, busy}) {
  const key = `${directory.owner}:${task.id}`;
  const [state, setState] = useState({key: '', editing: false, draft: null, error: '', attempted: false});
  const local = state.key === key ? state : {editing: false, draft: null, error: '', attempted: false};
  const update = values => setState({...local, key, ...values});
  const hidden = task.alignment?.restricted_key_result_count > 0 || task.alignment?.restricted_target_count > 0;
  const canEdit = Boolean(task.can_coordinate && !['completed','cancelled','pending_review'].includes(task.status) && !hidden);
  const targets = [...(directory.targets || [])];
  for(const row of task.alignment?.targets || [])if(!targets.some(item=>item.id===row.id))targets.push(row);
  const editDirectory = {...directory,targets};
  const save = () => {
    const declared = task.alignment?.actual_contribution?.metric_code;
    const error = !canEdit ? '当前不能替换任务对齐关系，请保留原关系' : directory.status !== 'ready' ? '请先确认服务支持任务目标关联' : alignmentError(local.draft, directory.metrics, directory.keyResults, targets) || (declared && !effectiveMetricCodes(local.draft,directory.keyResults,targets).includes(declared) ? '已有实际贡献申报，请保留对应指标的目标或 KR' : '');
    update({attempted: true, error});
    if (error || busy) return;
    const alignment = alignmentPayload(local.draft, directory.metrics, directory.keyResults, targets), owner = identity();
    page.confirmTaskMutation({capability: 'can_coordinate', permission: 'task.coordinate', modal: {title: '保存任务对齐关系？', content: '保留原任务派发与验收流程；贡献声明不会自动增加业绩。', confirmText: '保存'},
      submit: () => saveTaskAlignment(task, alignment).catch(failure => {
        if (owner === identity() && page.data.task?.id === task.id) update({error: failure.statusCode === 409 ? '任务或指标已更新。当前编辑内容已保留，请核对最新任务后重新编辑。' : failure.message || '保存失败，当前编辑内容已保留'});
        throw failure;
      }),
      onSuccess: () => {if (owner === identity() && page.data.task?.id === task.id) update({editing: false, error: ''}); runtime().wx.showToast({title: '任务对齐已保存', icon: 'success'});},
      errorMessage: '任务对齐保存失败'});
  };
  if (!task.alignment && !canEdit) return null;
  return <section className="ds-td-card" data-testid="task-alignment-detail">
    {!local.editing && <>
      <TaskAlignmentSummary alignment={task.alignment} metrics={directory.metrics} keyResults={directory.keyResults}/>
      {hidden && <p className="review-task-help">部分关联对象受查看权限限制，当前保留完整关系，不能替换编辑。</p>}
      {!task.alignment && <p className="review-task-help">此历史任务尚未关联指标或 KR。</p>}
      {canEdit && directory.status === 'ready' && <Button onClick={() => update({editing: true, draft: alignmentDraft(task.alignment), error: '', attempted: false})}>编辑指标与目标</Button>}
    </>}
    {local.editing && <>
      <TaskAlignmentFields draft={local.draft} directory={editDirectory} onChange={draft=>update({draft,error:''})} disabled={busy || !canEdit || directory.status!=='ready'} error={local.error}/>
      <div className="review-task-editor-actions"><Button disabled={busy} onClick={() => update({editing: false})}>取消</Button><Button type="primary" loading={busy} disabled={!canEdit || directory.status!=='ready'} onClick={save}>保存对齐关系</Button></div>
    </>}
    {directory.status === 'unavailable' && <Alert type="info" showIcon message="当前服务尚不支持任务目标关联，请完成配套升级后再编辑；原任务处理流程仍可继续。"/>}
    {directory.status === 'error' && <Alert type="warning" showIcon message={directory.error} action={<Button onClick={directory.reload}>重试指标目录</Button>}/>}
  </section>;
}
