import React from 'react';
import { Button, Checkbox, Progress } from 'antd';
import { SbBottomBar, SbListRow, SbSearch, SbSegmented, SbStatePanel, SbTextarea } from '@shandiant/ui-react';
import { FdeVisitOpportunity } from './FdeVisitOpportunity.jsx';
import './visit-entry.css';

// 记录客户拜访（原 pages/visit-entry/index）。左栏选客户，右栏写原文；语音、文件、草稿都走原页面逻辑。
// FDE 视角：选客户后还要选本人参与的商机（原子组件 fde-visit-opportunity，#fdeVisitOpportunity），没有首次拜访。
export function supportsVisitEntry(page) {
  return Boolean(page && page.route === 'pages/visit-entry/index');
}
VisitEntry.subcomponents = (page, d) => d.isFde ? [{ selector: '#fdeVisitOpportunity', required: Boolean(d.customerConfirmed), props: { customerId: d.customerId || '', selectedId: d.opportunityId || '', disabled: Boolean(d.isProcessing || d.isRecording) } }] : [];

export default function VisitEntry({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const busy = d.isRecording || d.isStarting || d.isStopping || d.isProcessing;
  const voice = d.entryMode !== 'file';
  const fde = Boolean(d.isFde);
  const picker = fde && d.customerConfirmed ? select('#fdeVisitOpportunity') : null;
  const submitLabel = !d.customerConfirmed ? '请先选择客户' : fde && !d.fdeOpportunityVerified ? '请先选择本人参与的商机' : !d.transcript ? '请先录入拜访内容' : '提交结构化';
  const importText = d.importStatus === 'uploading' ? `${d.uploadProgress}%` : d.importStatus === 'succeeded' ? '已提取' : d.importStatus === 'failed' ? '处理失败' : d.importStatus === 'save_failed' ? '本地保存失败，尚未上传' : d.importStatus === 'saving' ? '正在保存' : '处理中';
  return <section className="ds-ventry" aria-label="记录客户拜访">
    <aside className="ds-panel ds-ve-side">
      <h2 className="ds-ve-h">选择客户 <small className="ds-ve-required">必填</small></h2>
      {d.customerConfirmed ? <div className="ds-ve-selected">
        <span className="ds-ve-mark" aria-hidden="true">{d.customerInitial}</span>
        <div className="ds-ve-selected-main"><b>{d.customerName}</b><span className="ds-muted">本次关联客户</span></div>
        <Button type="link" size="small" disabled={busy} onClick={() => call('changeCustomer')}>更换</Button>
      </div> : <>
        <SbSearch value={d.customerQuery || ''} placeholder="输入客户名称关键词" loading={Boolean(d.searching)} onChange={value => call('inputCustomerQuery', { detail: { value } })} onSearch={() => call('searchDepartmentCustomers')} />
        <p className="ds-muted ds-ve-hint">{d.searching ? '正在匹配客户…' : fde ? '选择客户后，仅可录入本人参与的商机' : '可搜索公司全部客户，选中后再填写拜访内容'}</p>
        <div className="ds-ve-results">
          {(d.customerResults || []).length ? (d.customerResults || []).map(c => <SbListRow key={c.id} name={c.name} summary={c.team_name || c.team || '本部门'} onClick={() => call('chooseCustomer', { dataset: { id: c.id } })} />)
            : d.customerSearchError ? <SbStatePanel state="error" title={d.customerSearchError} onRetry={() => call('searchDepartmentCustomers')} />
            : !d.searching && <SbStatePanel state="empty" title="没有匹配到客户" description="换个关键词再试。" />}
        </div>
      </>}
      {fde && d.customerConfirmed && <FdeVisitOpportunity picker={picker} invokeOn={invokeOn} selectedId={d.opportunityId} />}
      {!fde && d.canFirstVisit && <label className="ds-ve-first"><Checkbox checked={Boolean(d.isFirstVisit)} disabled={Boolean(d.isProcessing)} onChange={e => call('toggleFirstVisit', { detail: { value: e.target.checked ? ['first'] : [] } })} /><span><b>首次拜访</b><small className="ds-muted">会多出主营业务、需求、预算、联系人角色四项，确认页提示补充</small></span></label>}
    </aside>
    <section className="ds-panel ds-ve-main">
      <div className="ds-ve-note-head"><h2 className="ds-ve-h">拜访原始记录 <small className="ds-muted">键盘输入或语音转写</small></h2><span className="ds-muted ds-ve-count">{(d.transcript || '').length} / 50000</span>{(d.transcript || '').length > 0 && <Button type="link" size="small" disabled={d.isRecording || d.isStarting || d.isStopping} onClick={() => call('clearTranscript')}>一键清空</Button>}</div>
      <SbTextarea showCount={false} className="ds-ve-textarea" value={d.transcript || ''} disabled={d.isRecording || d.isProcessing} maxLength={50000} placeholder="可以说说：这次为什么拜访、客户反馈了什么、接下来准备何时做什么……" onChange={v => call('inputTranscript', { detail: { value: v } })} />
      <div className="ds-ve-status">
        {d.draftSaveError ? <span className="ds-ve-error" role="alert">{d.draftSaveError}</span> : d.undoAvailable ? <span>已清空本次内容 <Button type="link" size="small" onClick={() => call('undoClear')}>撤销</Button></span> : d.draftNotice ? <span className="ds-muted">{d.draftNotice}</span> : null}
        <span className={`ds-ve-state ${d.errorText ? 'is-error' : ''}`}>{d.statusText}</span>
      </div>
      <div className={`ds-ve-capture ${d.isRecording ? 'is-recording' : ''}`}>
        <SbSegmented size="middle" value={voice ? 'voice' : 'file'} disabled={busy} options={[{ value: 'voice', label: '语音录入' }, { value: 'file', label: '文件录入' }]} onChange={mode => call('switchInputMode', { dataset: { mode } })} />
        <div className="ds-ve-capture-copy"><b>{voice ? (d.isRecording ? `正在录音 ${d.recordingTime}` : '说说这次拜访') : '导入已有材料'}</b><small className="ds-muted">{voice ? '每次最长 10 分钟，结束后自动转写，转写后可以修改' : '音频 MP3、WAV、M4A 等最长 60 分钟；文档 PDF、DOCX、PPTX、TXT 最大 20MB，扫描件暂不支持'}</small></div>
        {voice ? <Button type={d.isRecording ? 'default' : 'primary'} danger={d.isRecording} disabled={!d.canTranscribe || d.isProcessing || d.isStarting || d.isStopping} onClick={() => call('toggleRecording')}>{d.isStarting ? '启动中…' : d.isStopping ? '结束中…' : d.isRecording ? '结束录音' : '开始录音'}</Button>
          : <Button type="primary" disabled={!d.canUploadVisit || d.isProcessing} onClick={() => call('chooseMaterial')}>{d.isProcessing ? '处理中…' : '选择文件'}</Button>}
      </div>
      <div className="ds-sync-recovery"><Button type="link" size="small" onClick={()=>call('toggleRecordingGuide')}>{d.recordingGuideVisible ? '收起录音说明' : '录音与草稿说明'}</Button>{d.recordingGuideVisible && <p>请允许浏览器麦克风权限，并保持当前标签页。录音结束后等待转写；本地保存失败时可重试保存。临时文件尚未持久保存，关闭页面可能丢失，请先保留原文件。</p>}{d.errorText && <p role="alert">{d.errorText.replaceAll('微信','浏览器')}</p>}</div>
      {d.fileName && <div className="ds-ve-file">
        <div className="ds-ve-file-line"><b>{d.fileName}</b><span className={d.importStatus === 'failed' ? 'ds-ve-error' : 'ds-muted'}>{importText}</span></div>
        {d.importStatus === 'uploading' && <Progress percent={Number(d.uploadProgress) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />}
        {!d.isProcessing && <div className="ds-ve-file-actions">{d.importStatus !== 'succeeded' && (d.canRetryImport || !d.importId && d.canUploadVisit) && <Button type="link" size="small" onClick={() => call('retryImport')}>{d.importStatus === 'save_failed' ? '重试保存' : '重试'}</Button>}<Button type="link" size="small" onClick={() => call('removeFile')}>移除附件</Button></div>}
      </div>}
      <div className="ds-ve-bar"><SbBottomBar reason="语音识别失败不影响手工录入；点「提交结构化」后才进入字段确认页" primary={{ label: submitLabel, disabled: !d.canSubmit || busy, disabledReason: busy ? '先等这一步完成' : '', onClick: () => call('submitTranscript') }} /></div>
    </section>
  </section>;
}
