import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {Alert,Button,Spin} from 'antd';
import {identity,runtime} from '../pc/data.js';
import {businessErrorMessage} from '../pc/error-message.js';
import {RunProgress} from './DocumentEditor.jsx';
import DocumentView from './DocumentView.jsx';
import FeishuPublish from './FeishuPublish.jsx';
import {ReportStatus} from './ReportFrame.jsx';
import GenerationNotice from './GenerationNotice.jsx';
import ReportSources from './ReportSources.jsx';
import {DocumentHistory,DocumentExport,DocumentShare} from './ReportDocumentDialogs.jsx';
import {clone,stable} from './state.js';
import {accessLost} from './composition.js';
import {contentProblem} from './content.js';
import {useLeaveGuard} from './useLeaveGuard.js';
import {usePollingResource} from './usePollingResource.js';
import {workReportApi} from './api.js';
import {matchesSavedRevision,uncertainSave} from './save-recovery.js';
import {shareErrorText,shareSelectionFailure,shareResultText} from './sharing.js';

// Inspect the shared transport receipt without creating/replacing a key. The
// shared client will synchronously reuse this receipt for the same frozen body.
function pendingRevisionReceipt(documentId,intent) {
  try {
    const bridge=runtime(),client=bridge.require('utils/apiClient'),receipts=bridge.require('utils/requestIdentity');
    const actor=client.getAuth()?.actor;if(!actor)return null;
    const scope=[client.getBaseUrl(),actor.workspace_id,actor.user_id];
    const fingerprint=receipts.fingerprint(scope,{method:'POST',path:`/work-reports/documents/${encodeURIComponent(documentId)}/revisions`,data:intent});
    const key=bridge.wx.getStorageSync(receipts.STORAGE_KEY)?.[fingerprint];
    return typeof key==='string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(key)?{key,fingerprint}:null;
  } catch{return null;}
}

const DocumentState=forwardRef(function DocumentState({page,documentId,owner,actorId,canGenerate=false,initialRunId,onRunChange,onRunObserved,service=workReportApi,pollDelay=1500},ref){
  const [state,setState]=useState({document:null,content:null,reading:true,error:null}),[busy,setBusy]=useState(null),[dialog,setDialog]=useState(null);
  const [runId,setRunId]=useState(initialRunId || null),[refresh,setRefresh]=useState(0);
  const [pendingSave,setPendingSave]=useState(null);
  const [shareResult,setShareResult]=useState(null);
  const active=useRef(false),serial=useRef(0),writing=useRef(false),handled=useRef(null);
  const current=()=>active.current && owner===identity();
  const apply=view=>{setPendingSave(null);setState({document:view,content:clone(view.revision.content),reading:false,error:null});};
  const denied=error=>{if(current()){serial.current++;setPendingSave(null);setDialog(null);setState({document:null,content:null,reading:false,error:error.message || '报告权限已变化'});}};
  const load=async()=>{
    if(!current())return;const request=++serial.current;setDialog(null);setState({document:null,content:null,reading:true,error:null});
    try {const view=await service.document(documentId);if(current() && request===serial.current)apply(view);}
    catch(error){if(current() && request===serial.current)setState({document:null,content:null,reading:false,error:error.message || '报告暂时无法读取'});}
  };
  useEffect(()=>{active.current=true;load();return()=>{active.current=false;serial.current++;};},[]);
  const dirty=Boolean(state.document && stable(state.content)!==stable(state.document.revision.content));
  const leave=useLeaveGuard({page,owner,dirty,saving:Boolean(busy)});useImperativeHandle(ref,()=>({leave}),[leave]);
  const run=usePollingResource({id:runId,owner,load:service.run,pending:value=>['queued','running'].includes(value.status),refresh,delay:pollDelay});
  useEffect(()=>{if(runId && current())onRunObserved?.({id:runId,status:run.value?.status,unavailable:[403,404].includes(run.error?.statusCode || run.error?.status)});},[runId,run.value,run.error,onRunObserved]);
  const pending=Boolean(runId && (run.loading || run.error || ['queued','running'].includes(run.value?.status)));
  useEffect(()=>{
    if(accessLost(run.error)){denied(run.error);return;}
    const value=run.value;
    if(!busy && !pendingSave && value?.status==='succeeded' && handled.current!==value.id && current()) {
      handled.current=value.id;
      if(value.result_status==='insufficient_data')return;
      // A rewrite may only reload the document it was created for.
      if(value.document_id!==documentId){setState(previous=>({...previous,error:'生成结果与当前报告不一致，请重新读取状态'}));return;}
      load();
    }
  },[run.value,run.error,busy,pendingSave]);
  const mutate=async(kind,action,accept,recover)=>{
    if(writing.current || !current())return;
    writing.current=true;setBusy(kind);setState(previous=>({...previous,error:null}));const request=++serial.current;
    try {const value=await action();if(current() && request===serial.current)accept(value);}
    catch(error){if(current() && request===serial.current){
      if(accessLost(error) && !(kind==='share' && shareSelectionFailure(error)))denied(error);
      else if(!recover || !await recover(error,request))setState(previous=>({...previous,error:kind==='share'?shareErrorText(error):error.message || '操作失败，请重试'}));
      if(kind==='cancel' && (error.statusCode || error.status)===409)setRefresh(value=>value+1);
    }}finally{writing.current=false;if(current())setBusy(null);}
  };
  const unknownSave=message=>setState(previous=>({...previous,error:message}));
  const receiptMatches=attempt=>{
    const receipt=pendingRevisionReceipt(documentId,attempt.intent);
    return Boolean(receipt && attempt.receipt && receipt.key===attempt.receipt.key && receipt.fingerprint===attempt.receipt.fingerprint);
  };
  const unresolvedSave=(attempt,message)=>{
    setPendingSave({...attempt,canRetry:false});unknownSave(message);
  };
  const acceptSaveCheck=(view,attempt)=>{
    if(matchesSavedRevision(view,documentId,attempt.intent)){apply(view);return;}
    const base=attempt.base;
    const unchanged=Boolean(view?.id===documentId && view.owner_id===actorId && view.owner_id===base.owner_id && view.can_edit===true &&
      view.version_no===attempt.intent.version_no && view.latest_revision_id===base.latest_revision_id &&
      view.revision?.document_id===documentId && view.revision.id===base.revision.id &&
      view.title===base.title && stable(view.revision.content)===stable(base.revision.content));
    const canRetry=unchanged && !attempt.retryRejected && receiptMatches(attempt);
    setPendingSave({...attempt,canRetry});
    unknownSave(canRetry?'尚未确认本次保存，服务器仍显示原版本。本地稿已保留，可核对结果或重试同一次保存。':
      '尚未确认本次保存。本地稿已保留，请继续核对结果；当前无法安全重试。');
  };
  const verifySave=()=>{
    if(!pendingSave || busy || pending)return;
    mutate('save-check',()=>service.document(documentId),view=>acceptSaveCheck(view,pendingSave),()=>{
      unresolvedSave(pendingSave,'暂时无法读取保存结果。本地稿已保留，请稍后再次核对。');return true;
    });
  };
  const recoverSave=async(error,request,attempt)=>{
    if(!uncertainSave(error)){
      if(!attempt.retrying)return false;
      unresolvedSave({...attempt,retryRejected:true},`${error.message || '本次重试未被接受'}。本地稿已保留，请核对结果或重新读取报告。`);return true;
    }
    const waiting={...attempt,canRetry:false};
    setPendingSave(waiting);
    try {
      const view=await service.document(documentId);
      if(current() && request===serial.current)acceptSaveCheck(view,waiting);
    } catch(readError) {
      if(current() && request===serial.current){
        if(accessLost(readError))denied(readError);
        else unresolvedSave(waiting,'暂时无法读取保存结果。本地稿已保留，请稍后再次核对。');
      }
    }
    return true;
  };
  const retrySave=()=>{
    const attempt=pendingSave;
    if(!attempt?.canRetry || busy || pending || !current())return;
    if(!receiptMatches(attempt)){unresolvedSave(attempt,'原保存标识已不可用。本地稿已保留，请核对保存结果，暂不再次提交。');return;}
    setPendingSave({...attempt,canRetry:false});
    mutate('save',()=>service.saveRevision(documentId,attempt.intent),apply,(error,request)=>recoverSave(error,request,{...attempt,retrying:true}));
  };
  if(owner!==identity())return null;
  const doc=accessLost(run.error)?null:state.document,locked=Boolean(busy || pending || pendingSave);
  const save=()=>{
    if(!doc?.can_edit || !dirty || locked || contentProblem(state.content))return;
    const intent={version_no:doc.version_no,content:clone(state.content)};
    const attempt={intent,base:clone(doc),receipt:null,canRetry:false};
    mutate('save',()=>{
      const result=service.saveRevision(documentId,intent);
      // begin() persists synchronously before the shared client sends. Capture
      // this first receipt now, so a later replacement cannot become our key.
      attempt.receipt=pendingRevisionReceipt(documentId,intent);
      return result;
    },apply,(error,request)=>recoverSave(error,request,attempt));
  };
  const rewrite=section_ids=>{
    if(!doc?.can_edit || doc.meeting_facts || doc.review_facts || !canGenerate || dirty || locked)return;
    mutate('rewrite',()=>service.generate({snapshot_id:doc.snapshot_id,snapshot_hash:doc.snapshot_hash,scheme:{scheme_id:doc.scheme.scheme_id,version_no:doc.scheme.version_no},document_id:documentId,version_no:doc.version_no,base_revision_id:doc.revision.id,section_ids}),value=>{setRunId(value.id);onRunChange?.(value.id);});
  };
  const share=(audience,confirmation={})=>{
    if(!doc?.can_share || dirty || locked)return;
    mutate('share',()=>service.share(documentId,{version_no:doc.version_no,confirm:true,audience,...confirmation}),value=>{apply(value);setShareResult(shareResultText(value.share_visibility));setDialog(null);});
  };
  const unshare=()=>{
    if(!doc?.can_share || dirty || locked)return;
    mutate('share',()=>service.unshare(documentId,{version_no:doc.version_no}),value=>{apply(value);setShareResult('已取消全部共享，接收方后续读取将重新校验。');setDialog(null);});
  };
  const cancel=()=>{if(run.value && ['queued','running'].includes(run.value.status))mutate('cancel',()=>service.cancel(runId,{version_no:run.value.version_no}),()=>setRefresh(value=>value+1));};
  const openDialog=name=>{if(!locked && (!dirty || ['history','sources'].includes(name)))setDialog(name);};
  return <div className="wr-compose-flow">
    {state.reading?<Spin/>:!doc?<Alert type="error" title={state.error || '报告暂不可用'} action={<Button onClick={load}>重新读取报告</Button>}/>:<>
      {pendingSave?<Alert type="warning" title="保存结果待确认" description={busy?'正在核对服务器保存结果，本地稿已保留。':state.error}
        action={<div><Button loading={Boolean(busy)} disabled={Boolean(busy || pending)} onClick={verifySave}>核对保存结果</Button>
          {pendingSave.canRetry && <Button disabled={Boolean(busy || pending)} onClick={retrySave}>重试同一次保存</Button>}
          {pendingSave.retryRejected && <Button disabled={Boolean(busy || pending)} onClick={()=>leave(load)}>重新读取报告</Button>}</div>}/>
        :state.error && <Alert type="error" title={state.error} action={<Button onClick={()=>leave(load)}>重新读取报告</Button>}/>}
      {shareResult && <Alert type="info" title={shareResult} closable onClose={()=>setShareResult(null)}/>}
      {doc.status==='shared' && doc.shared_revision_id!==doc.revision.id && doc.can_edit && <Alert type="info" title="当前修改尚未共享" description="接收者仍看到上一次确认共享的版本。再次确认共享后才会更新。"/>}
      {doc.owner_id===actorId && doc.can_share && typeof service.feishu==='function' && <FeishuPublish key={JSON.stringify([doc.id,doc.version_no,doc.latest_revision_id])} document={doc} actorId={actorId} owner={owner} dirty={dirty} locked={locked} service={service} onAccessLost={denied} onReload={()=>leave(load)}/>}
      <DocumentView generationDocument={doc} onRetryGeneration={doc.can_edit && canGenerate && !doc.meeting_facts && !doc.review_facts?()=>rewrite(state.content.sections.map(section=>section.id)):undefined} value={state.content} metrics={doc.metrics} meetingFacts={doc.meeting_facts} overviewFacts={doc.overview_facts} reviewFacts={doc.review_facts} onChange={content=>setState(previous=>({...previous,content}))} status={doc.status} dirty={dirty} busy={locked} saveBusy={busy==='save'} readOnly={!doc.can_edit}
        periodLabel={doc.period.label} scopeLabel={doc.scope.label} schemeLabel={doc.scheme.name}
        meta={`第 ${doc.revision.revision_no} 版 · ${{generated:'生成草稿',manual:'人工编辑',rewrite:'局部重写',basic_summary:'基础汇总'}[doc.revision.origin] || '已保存'}`} onSave={doc.can_edit?save:undefined} onRewrite={doc.can_edit && canGenerate && !doc.meeting_facts && !doc.review_facts?rewrite:undefined}
        onShare={doc.can_share?()=>openDialog('share'):undefined} onExport={doc.can_export?()=>openDialog('export'):undefined} onHistory={()=>openDialog('history')}
        onSources={doc.owner_id===actorId && !doc.meeting_facts?()=>openDialog('sources'):undefined}/>
    </>}
    {run.error && <Alert type="error" title={run.error.message} action={<Button onClick={()=>setRefresh(value=>value+1)}>重新读取状态</Button>}/>}
    {pending && !run.error && <RunProgress label={run.loading?'正在读取重写状态':'正在重写报告'} onCancel={!run.loading?cancel:undefined} cancelling={busy==='cancel'}/>}
    {run.value && !pending && <div className="wr-panel"><ReportStatus status={run.value.result_status || run.value.status}/><GenerationNotice run={run.value}/>{run.value.error && <p>{businessErrorMessage(run.value.error, '报告重写未完成，原报告已保留')}</p>}{run.value.result_status==='insufficient_data' && <p>材料不足，原报告保持原样。</p>}</div>}
    {doc && dialog==='sources' && <ReportSources open snapshotId={doc.snapshot_id} owner={owner} service={service} onClose={()=>setDialog(null)} onAccessLost={denied}/>}
    {doc && dialog==='history' && <DocumentHistory document={doc} owner={owner} service={service} onClose={()=>setDialog(null)} onAccessLost={denied}/>}
    {doc && dialog==='export' && <DocumentExport document={doc} owner={owner} service={service} onClose={()=>setDialog(null)} onAccessLost={denied}/>}
    {doc && dialog==='share' && <DocumentShare document={doc} owner={owner} service={service} saving={busy==='share'} error={state.error} onClose={()=>{if(!busy)setDialog(null);}} onSave={share} onRevoke={unshare} onAccessLost={denied} onRecheck={()=>setState(previous=>({...previous,error:null}))}/>}
  </div>;
});
export default forwardRef(function ReportDocument(props,ref){return <DocumentState key={JSON.stringify([props.owner,props.documentId])} {...props} ref={ref}/>;});
