import {SbTextarea} from '../ImeInput.jsx';
import {PersonPickerDialog,TaskCancellationDialog} from '../NativeDialogs.jsx';
import React, {useState} from 'react';
import {Button} from 'antd';
import {SbBottomBar, SbField, SbLabeledSelect, SbStatePanel, SbStatusTag} from '@shandiant/ui-react';
import '../task-detail.css';
import TaskSyncStatus from './TaskSyncStatus.jsx';
import TaskAlignmentDetail from '../review/tasks/TaskAlignmentDetail.jsx';
import {ContributionFields} from '../review/tasks/TaskAlignmentFields.jsx';
import {contributionError, contributionPayload} from '../review/tasks/model.js';
import {completeAlignedTask} from '../review/tasks/api.js';
import {useTaskAlignmentDirectory} from '../review/tasks/useTaskAlignmentDirectory.js';
import {runtime} from '../pc/data.js';

// 任务详情（原 pages/task-detail/index）。左栏任务身份与信息，右栏当前要做的事；底栏是动作。业务动作都走原 Page。
export function supportsTaskDetail(page, data = {}) {
  return Boolean(page && page.route === 'pages/task-detail/index' && data.task);
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const STAGE = t => t.isEnded ? (t.isSuccessful ? '任务正常完成，结果已保留' : '任务已结束，原因和历史记录已保留') : t.handover_required ? '接收资格已变化，等待协调处理' : t.status === 'completed' ? '任务已完成' : t.status === 'pending_confirm' ? '等待接收方接受或拒绝' : t.status === 'pending_review' ? '完成说明已提交，等待发起人验收' : t.status === 'cancelled' ? (t.wasCancelled ? '任务已取消，原因和协调记录已保留' : '任务已被拒绝，原因已推送发起人') : '任务执行中';

export default function LegacyTaskPanel({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const t = d.task;
  const alignmentDirectory = useTaskAlignmentDirectory(Boolean(t?.alignment || t?.can_coordinate));
  const actualKey = `${alignmentDirectory.owner}:${t.id}:${t.version_no}`;
  const [actualState, setActualState] = useState({key: '', value: null, error: ''});
  const actual = actualState.key === actualKey ? actualState : {value: null, error: ''};
  const setActual = value => setActualState({key: actualKey, value, error: ''});
  const complete = () => {
    if (actual.value == null) return call('markCompleted');
    const error = contributionError(actual.value, alignmentDirectory.metrics, t.alignment?.metric_codes || [], true);
    if (error) {setActualState({key: actualKey, value: actual.value, error}); return;}
    if (!String(d.completionNote || '').trim()) return call('markCompleted');
    const contribution = contributionPayload(actual.value), note = String(d.completionNote).trim();
    page.confirmTaskMutation({capability: 'canComplete', permission: 'task.complete',
      modal: {title: t.selfAssigned ? '确认任务已完成？' : '提交完成申请？', content: t.selfAssigned ? '自建自领任务将直接完成，贡献量保留为申报记录。' : `提交后由 ${t.creator} 确认，贡献量保留为申报记录。`, confirmText: t.selfAssigned ? '确认完成' : '提交完成'},
      submit: () => completeAlignedTask(t, note, contribution),
      onSuccess: (completed, current) => {
        if (completed.status === 'completed') runtime().wx.setStorageSync('lastCompletedTaskId', t.id);
        runtime().wx.showToast({title: completed.status === 'completed' ? '任务已完成' : '已提交，等待确认', icon: 'success'});
        setTimeout(() => {if (current()) runtime().wx.navigateBack();}, 700);
      }, errorMessage: '更新失败，请稍后重试'});
  };
  const busy = Boolean(d.submitting);
  const info = [['任务类别', t.associationLabel], ...(t.opportunityLabel ? [['关联商机', t.opportunityLabel]] : []), ['执行人', t.owner], [t.task_type === 'visit_follow_up' ? '记录人' : '下发人', t.creator], ['任务来源', t.sourceLabel], ['截止时间', t.dueLabel, 'due'], ['创建时间', t.createdLabel], ['相关方', t.relatedText]];
  const coordinating = Boolean(d.coordinateOpen);
  const members = d.coordinateMembers || [];
  let panel = null, bar = null;
  if (coordinating) {
    panel = <section className="ds-td-card">
      <h2>任务协调 <small className="ds-muted">转交执行人</small></h2>
      <SbField label="接手人员" required error={d.coordinateDirectoryError}><Button disabled={busy} loading={d.coordinateLoading} onClick={()=>call('openCoordinatePicker')}>{d.coordinateSelectedName || '选择接手人员'}</Button>{d.coordinateDirectoryError && <Button type="link" onClick={()=>call('loadCoordinationOptions')}>重试人员目录</Button>}</SbField>
      {!d.coordinateLoading && !members.length && <p className="ds-muted ds-td-help">当前没有可接手人员，可联系运营协调处理。</p>}
      <SbField label="协调原因" required error={d.coordinateError}><SbTextarea value={d.coordinateNote || ''} maxLength={500} autoSize={{ minRows: 4, maxRows: 8 }} placeholder="请说明转交原因" onChange={v => call('coordinateNote', { detail: { value: v } })} /></SbField>
    </section>;
    bar = <SbBottomBar reason={<span><Button type="link" size="small" disabled={busy} onClick={() => call('closeCoordination')}>返回</Button>转交后保留原执行人和协调历史</span>}
      secondary={{ label: '返回任务', disabled: busy, onClick: () => call('closeCoordination') }}
      primary={{ label: '转交任务', loading: busy, disabled: !d.coordinateSelectedId || !!d.coordinateDirectoryError, disabledReason: d.coordinateSelectedId ? '' : '没有可接手人员', onClick: () => call('coordinate', { dataset: { event: 'reassign' } }) }} />;
  } else if (t.status === 'completed') {
    panel = <section className="ds-td-card ds-td-done"><h2>{t.completedBy} 已完成 <small className="ds-muted">{t.completedLabel}</small></h2><p>{t.completionNote}</p></section>;
  } else if (t.status === 'cancelled') {
    panel = <section className="ds-td-card ds-td-rejected"><h2>{t.statusLabel} <small className="ds-muted">{t.endReason === 'cancelled' ? '取消原因' : t.endReason === 'rejected' ? '拒绝说明' : '结束记录'}</small></h2><p>{t.endReason === 'cancelled' ? t.cancellationNote : t.endReason === 'rejected' ? (t.rejectionComment || t.endNote) : t.endNote}</p></section>;
    if (t.canRetry) bar = <SbBottomBar reason="原任务和拒绝说明会被保留，新任务可重新修改" primary={{ label: '重新发起新任务', onClick: () => call('retryTask') }} />;
  } else if (t.canRespond) {
    panel = <section className="ds-td-card">
      <h2>接受或拒绝</h2>
      <p className="ds-muted ds-td-help">确认后进入执行；岗位任务只需一人领取，拒绝仅表示本人不领取。拒绝时必须填写原因，系统会立即通知发起人。</p>
      <SbField label="拒绝原因" help="拒绝时必填，将立即推送给发起人"><SbTextarea value={d.responseComment || ''} maxLength={2000} autoSize={{ minRows: 4, maxRows: 8 }} placeholder="说明无法接受的原因或建议调整方式" onChange={v => call('inputResponseComment', { detail: { value: v } })} /></SbField>
    </section>;
    bar = <SbBottomBar reason={`拒绝原因将立即推送给 ${t.creator}`} secondary={{ label: '拒绝', disabled: busy || !t.canDecline, onClick: () => call('rejectTask') }} primary={{ label: t.responseLabel || '接受任务', disabled:!t.canAccept, loading: busy, onClick: () => call('acceptTask') }} />;
  } else if (t.status === 'pending_review') {
    panel = <section className="ds-td-card">
      <h2>{t.canReview ? '待你验收' : '等待发起人确认'} <small className="ds-muted">{t.owner} 提交的完成说明</small></h2>
      <blockquote className="ds-td-quote">{t.completionNote}</blockquote>
      {t.canReview && <SbField label="验收意见" help="驳回时必填，说明需要补充或改进的内容"><SbTextarea value={d.reviewNote || ''} maxLength={2000} autoSize={{ minRows: 4, maxRows: 8 }} placeholder="验收意见" onChange={v => call('inputReviewNote', { detail: { value: v } })} /></SbField>}
    </section>;
    if (t.canReview) bar = <SbBottomBar reason="确认完成后任务结束" secondary={{ label: '驳回', disabled: busy, onClick: () => call('reviewCompletion', { dataset: { event: 'reject_completion' } }) }} primary={{ label: '确认完成', loading: busy, onClick: () => call('reviewCompletion', { dataset: { event: 'approve_completion' } }) }} />;
  } else if (t.canComplete) {
    panel = <section className="ds-td-card">
      <h2>完成反馈</h2>
      {t.reviewRejected && <p className="ds-td-error" role="alert">驳回原因：{t.reviewNote}</p>}
      <p className="ds-muted ds-td-help">{t.selfAssigned ? '填写完成说明后可直接完成。' : '请填写完成说明，提交后由发起人验收，通过才算完成。'}</p>
      <SbField label="完成说明" required><SbTextarea value={d.completionNote || ''} maxLength={2000} autoSize={{ minRows: 5, maxRows: 9 }} placeholder="说明交付结果、附件位置或后续安排" onChange={v => call('inputCompletionNote', { detail: { value: v } })} /></SbField>
      {alignmentDirectory.status === 'ready' && t.alignment && <ContributionFields actual value={actual.value} onChange={setActual} metrics={alignmentDirectory.metrics} codes={t.alignment.metric_codes || []} disabled={busy} error={actual.error}/>}
    </section>;
    bar = <SbBottomBar reason={t.selfAssigned ? '填写说明后直接完成' : `提交后由 ${t.creator} 验收`} primary={{ label: t.selfAssigned ? '确认完成' : '提交完成', loading: busy, loadingLabel: '正在同步…', onClick: complete }} />;
  } else {
    panel = <SbStatePanel state="empty" title={`该任务由 ${t.owner} 负责完成`} description="你可以查看最新状态。" />;
  }
  return <section className="ds-tdetail review-task-detail" aria-label="任务详情">
    <aside className="ds-panel ds-td-side">
      <div className="ds-td-tags"><SbStatusTag tone={toneOf(t.signal?.tone)} label={`${t.signal?.label || ''} · ${t.statusLabel}`} /><span className="ds-td-chip">{t.priority}优先级</span></div>
      <h1>{t.title}</h1>
      <p className="ds-muted">{[t.customer, t.team].filter(Boolean).join(' · ')}</p>
      <p className="ds-muted ds-td-stage">{STAGE(t)}</p>
      {t.handover_required && <p className="ds-td-note">原接收人账号或岗位资格已变化，任务待交接；不自动完成或删除。</p>}
      <dl className="ds-td-info">{info.map(([k, v, kind]) => <div key={k}><dt>{k}</dt><dd className={kind === 'due' ? 'ds-td-due' : ''}>{v}</dd></div>)}</dl>
      {t.canCancel && <Button danger disabled={busy} onClick={()=>call('openCancellation')}>取消任务</Button>}
       {t.can_coordinate && t.status !== 'pending_review' && !coordinating && <Button onClick={() => call('openCoordination')}>协调任务</Button>}
    </aside>
    <section className="ds-panel ds-td-main">
      <div className="ds-td-refresh"><TaskSyncStatus sync={d.webTaskSync}/><Button loading={d.executionLoading} disabled={busy || coordinating || d.cancelOpen || d.migrationOpen} onClick={()=>call('refreshWebTasks')}>刷新任务</Button></div>
      <section className="ds-td-card"><h2>任务要求</h2><p className="ds-td-desc">{t.description}</p></section>
      <TaskAlignmentDetail task={t} page={page} directory={alignmentDirectory} busy={busy}/>
      {panel}
      {(t.history || []).length > 0 && <section className="ds-td-card"><h2>提交与验收记录</h2><ol className="ds-td-history">{t.history.map(h => <li key={h.id}><b>{h.label} · {h.actor_name}</b><span className="ds-muted">{h.time}</span>{h.transferSummary && <p>{h.transferSummary}</p>}<p>{h.note}</p></li>)}</ol></section>}
      {bar && <div className="ds-td-bar">{bar}</div>}
    </section>
    <PersonPickerDialog instance={select('#personPicker')} invokeOn={invokeOn} />
    <TaskCancellationDialog instance={select('task-cancel-dialog')} invokeOn={invokeOn} />
  </section>;
}

LegacyTaskPanel.subcomponents=(page,d)=>[
 {selector:'#personPicker',required:!!d.task,props:{open:d.coordinatePickerOpen,members:d.coordinateMembers,teams:d.coordinateTeams,defaultTeamId:d.coordinateDefaultTeamId,selected:d.coordinateSelectedIds,multiple:false,allowAll:false,maxSelected:1,title:'选择接手人员',loading:d.coordinateLoading,error:d.coordinateDirectoryError}},
 {selector:'task-cancel-dialog',required:!!d.task,props:{open:d.cancelOpen,taskId:d.taskId,taskVersion:d.task?.version_no || 0}}
];
