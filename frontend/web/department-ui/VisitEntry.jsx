import {SbSearch, SbTextarea} from './ImeInput.jsx';
import React from 'react';
import {Button, Checkbox, Progress} from 'antd';
import {SbBottomBar, SbListRow, SbSegmented, SbStatePanel} from '@shandiant/ui-react';
import {VisitOpportunitySelector} from './VisitOpportunitySelector.jsx';
import './visit-entry.css';
import {RecordingGuide} from './WorkflowCompletion.jsx';
import {accessModule, can} from './pc/data.js';
import {VisitDraftStatus} from './VisitDraftStatus.jsx';

// 记录客户拜访（原 pages/visit-entry/index）。左栏选客户，右栏写原文；语音、文件、草稿都走原页面逻辑。
// Sales and FDE share an optional opportunity reference; server write grants remain authoritative.
export function supportsVisitEntry(page) {
  return Boolean(page && page.route === 'pages/visit-entry/index');
}
VisitEntry.subcomponents = (page, d) => [{selector: '#visitEntryOpportunity', required: Boolean(d.customerConfirmed), props: {customerId: d.customerId || '', selectedId: d.opportunityId || '', disabled: Boolean(d.isProcessing || d.isRecording)}}];

export default function VisitEntry({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const preview = globalThis.SALES_MODE === 'preview';
  const captureHint = voiceMode => voiceMode
    ? d.canTranscribe ? '每次最长 10 分钟，结束后自动转写，转写后可以修改' : preview ? '演示环境支持文字录入，语音转写请在正式工作区使用' : '当前账号未开通语音转写，可使用文字录入'
    : d.canUploadVisit ? '音频 MP3、WAV、M4A 等最长 60 分钟；文档 PDF、DOCX、PPTX、TXT 最大 20MB，扫描件暂不支持' : preview ? '演示环境支持文字录入，文件上传请在正式工作区使用' : '当前账号未开通文件上传，可使用文字录入';
  const unresolved = !d.importId && ['sending', 'processing', 'uncertain', 'legacy_unknown', 'conflict'].includes(d.uploadIntentState);
  const needsCheck = ['legacy_unknown', 'conflict'].includes(d.uploadIntentState);
  const recoveryAction = accessModule().visitImportAction(d);
  const canRecover = recoveryAction.permission === null || can(recoveryAction.permission);
  const statusMessage = preview && d.uploadIntentState === 'retryable_failure' ? '演示模式未接收文件，请保留原件。可移除附件后使用文字录入。'
    : d.errorText || d.statusText;
  const retryLabel = recoveryAction.kind === 'local_reason' ? '查看原因' : recoveryAction.kind === 'read' ? '继续查看' : recoveryAction.kind === 'retry' ? '重试处理' : unresolved ? '继续查回' : d.localFileTemporary ? '重试保存' : '重试上传';
  const busy = d.isRecording || d.isStarting || d.isStopping || d.isProcessing;
  const voice = d.entryMode !== 'file';
  const picker = d.customerConfirmed ? select('#visitEntryOpportunity') : null;
  const submitLabel = !d.customerConfirmed ? '请先选择客户' : d.opportunityId && !d.opportunityResolved ? '请核对已选商机或清除关联' : !d.transcript ? '请先录入拜访内容' : '提交结构化';
  const importText = needsCheck ? '需要核对' : ['uncertain', 'processing'].includes(d.uploadIntentState) ? '待确认接收' : preview && d.uploadIntentState === 'retryable_failure' ? '未接收' : d.localFileTemporary && !d.isProcessing && !d.importId ? '未保存，待处理' : d.importStatus === 'saving' ? '保存中' : d.importStatus === 'save_failed' ? '保存失败' : d.importStatus === 'uploading' ? `${d.uploadProgress}%` : d.importStatus === 'succeeded' ? '已提取' : d.importStatus === 'failed' ? (d.importId ? '处理失败' : '上传失败') : '处理中';
  return <section className="ds-ventry" aria-label="记录客户拜访">
    <aside className="ds-panel ds-ve-side">
      <h2 className="ds-ve-h">选择客户 <small className="ds-ve-required">必填</small></h2>
      {d.customerConfirmed ? <div className="ds-ve-selected">
        <span className="ds-ve-mark" aria-hidden="true">{d.customerInitial}</span>
        <div className="ds-ve-selected-main"><b>{d.customerName}</b><span className="ds-muted">本次关联客户</span></div>
        <Button type="link" size="small" disabled={busy} onClick={() => call('changeCustomer')}>更换</Button>
      </div> : <>
        <SbSearch value={d.customerQuery || ''} placeholder="输入客户名称关键词" disabled={Boolean(busy)} loading={Boolean(d.searching)} onChange={value => call('inputCustomerQuery', { detail: { value } })} onSearch={() => call('retryCustomerSearch')} />
        <p className="ds-muted ds-ve-hint">{d.searching ? '正在匹配客户…' : '选择本次跟进的客户，可继续关联已有商机'}</p>
        <div className="ds-ve-results">
          {d.searching ? <SbStatePanel state="loading" title="正在匹配客户…" />
            : d.customerSearchError ? <SbStatePanel state="error" title={d.customerSearchError} onRetry={() => call('retryCustomerSearch')} />
            : (d.customerResults || []).length ? (d.customerResults || []).map(c => <SbListRow key={c.id} name={c.name} summary={c.team_name || c.team || ''} onClick={busy ? undefined : () => call('chooseCustomer', { dataset: { id: c.id } })} />)
            : d.customerSearchDone && <SbStatePanel state="empty" title="没有匹配到客户" description="换个关键词再试。" />}
        </div>
      </>}
      {d.customerConfirmed && <VisitOpportunitySelector picker={picker} invokeOn={invokeOn} selectedId={d.opportunityId} />}
      {d.canFirstVisit && <label className="ds-ve-first"><Checkbox checked={Boolean(d.isFirstVisit)} disabled={Boolean(busy)} onChange={e => call('toggleFirstVisit', { detail: { value: e.target.checked ? ['first'] : [] } })} /><span><b>首次拜访</b><small className="ds-muted">会多出主营业务、需求、预算、联系人角色四项，确认页提示补充</small></span></label>}
    </aside>
    <section className="ds-panel ds-ve-main">
      {d.legacyDraftAvailable && <div style={{padding: '12px 20px', background: '#fffbe6'}} role="status">发现旧版草稿，来源环境未标记。恢复文字后，请重新确认客户与商机。<Button type="link" onClick={() => call('restoreLegacyDraft')}>恢复旧稿文字</Button></div>}
      <div className="ds-ve-note-head"><h2 className="ds-ve-h">拜访原始记录 <small className="ds-muted">键盘输入或语音转写</small></h2><span className="ds-muted ds-ve-count">{(d.transcript || '').length} / 50000</span>{(d.transcript || '').length > 0 && <Button type="link" size="small" disabled={d.isRecording || d.isStarting || d.isStopping || unresolved} onClick={() => call('clearTranscript')}>一键清空</Button>}</div>
      <SbTextarea showCount={false} className="ds-ve-textarea" value={d.transcript || ''} disabled={d.isRecording || d.isProcessing} maxLength={50000} placeholder="可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……" onChange={v => call('inputTranscript', { detail: { value: v } })} />
      <div className="ds-ve-status">
        <VisitDraftStatus data={d} />
        {d.undoAvailable && <span>已清空本次内容 <Button type="link" size="small" onClick={() => call('undoClear')}>撤销</Button></span>}
        <span className={`ds-ve-state ${d.errorText ? 'is-error' : ''}`}>{statusMessage}</span>
      </div>
      <div className={`ds-ve-capture ${d.isRecording ? 'is-recording' : ''}`}>
        <SbSegmented size="middle" value={voice ? 'voice' : 'file'} disabled={busy || unresolved} options={[{ value: 'voice', label: '语音录入' }, { value: 'file', label: '文件录入' }]} onChange={mode => call('switchInputMode', { dataset: { mode } })} />
        <div className="ds-ve-capture-copy"><b>{voice ? (d.isRecording ? `正在录音 ${d.recordingTime}` : '说说这次拜访') : '导入已有材料'}</b><small className="ds-muted">{captureHint(voice)}</small></div>
        {voice ? <Button type={d.isRecording ? 'default' : 'primary'} danger={d.isRecording} disabled={d.isProcessing || d.isStarting || d.isStopping || unresolved || (!d.isRecording && !d.canTranscribe)} onClick={() => call('toggleRecording')}>{d.isStarting ? '启动中…' : d.isStopping ? '结束中…' : d.isRecording ? '结束录音' : '开始录音'}</Button>
          : <Button type="primary" disabled={d.isProcessing || unresolved || !d.canUploadVisit} onClick={() => call('chooseMaterial')}>{d.isProcessing ? '处理中…' : '选择文件'}</Button>}
      </div>
      {/* 需求清单第 3 条：录音前就看得到要说清哪些字段，说全了确认页才好通过 */}
      <div className="ds-ve-checklist" aria-label="录入要点">
        <b>说清这几项，确认页更容易通过</b>
        <ul>
          <li><span>客户与对接人</span>客户全称、本次对接人姓名</li>
          <li><span>跟进日期</span>实际沟通是哪天（不说默认今天）</li>
          <li><span>沟通内容</span>讨论了什么、客户怎么反馈、达成了什么</li>
          <li><span>下一步</span>哪天、由谁、做什么或达成什么目标</li>
          {(d.canFirstVisit || d.isFirstVisit) && <li><span>首次拜访</span>主营业务、需求、预算、联系人角色</li>}
          {d.canCreateOpportunity && <li><span>新商机</span>项目名称、阶段、金额、预计关单、直销或伙伴</li>}
        </ul>
      </div>
      <RecordingGuide data={d} invoke={invoke} />
      {d.fileName && <div className="ds-ve-file">
        <div className="ds-ve-file-line"><b>{d.fileName}</b><span className={d.importStatus === 'failed' ? 'ds-ve-error' : 'ds-muted'}>{importText}</span></div>
        {d.importStatus === 'uploading' && <Progress percent={Number(d.uploadProgress) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />}
        {!d.isProcessing && <div className="ds-ve-file-actions">{d.importStatus !== 'succeeded' && <Button type="link" size="small" disabled={!canRecover} onClick={() => call('retryImport')}>{retryLabel}</Button>}{d.localFileTemporary && !d.importId && d.uploadIntentState === 'new' && <Button type="link" size="small" disabled={!d.canUploadVisit} onClick={() => call('uploadTemporaryFile')}>直接上传临时文件</Button>}<Button type="link" size="small" disabled={unresolved} onClick={() => call('removeFile')}>移除附件</Button></div>}
      </div>}
      <div className="ds-ve-bar"><SbBottomBar reason="语音识别失败不影响手工录入；点「提交结构化」后才进入字段确认页" primary={{ label: submitLabel, disabled: !d.canSubmit || busy, disabledReason: busy ? '先等这一步完成' : '', onClick: () => call('submitTranscript') }} /></div>
    </section>
  </section>;
}
