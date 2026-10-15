import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {identity,session} from '../../pc/data.js';
import {usePollingResource} from '../../work-report/usePollingResource.js';
import {workReportApi} from '../../work-report/api.js';
import {saveRunReference} from '../../work-report/run-recovery.js';
import {stable} from '../../work-report/state.js';
import {accessLost} from '../../work-report/composition.js';
import {generationPlan,matchingSnapshot,matchingDocument} from './model.js';
import {referenceKey,readReference,saveReference} from './recovery.js';

const rejected=error=>[400,403,404,409,422].includes(error?.statusCode || error?.status);
const scopeChanged=error=>(error?.statusCode || error?.status)===409 && [error?.code,error?.serverCode,error?.error_code].includes('WORK_REPORT_SCOPE_CHANGED');
const runPending=value=>['queued','running'].includes(value.status);
const snapshotPending=value=>value.status==='preparing';
const noPolling=()=>false;

// One explicit click authorizes preparing then generating. Re-entry only GETs
// saved references; an unfinished submission always needs an explicit retry.
export function useReviewReport({selection,mode='daily',service=workReportApi,storage,pollDelay=3000}){
  const owner=identity(),key=referenceKey(owner,selection,mode),actorId=session()?.userId;
  const [reference,setReference]=useState(()=>readReference(key,owner,storage));
  const latest=useRef(reference),active=useRef(false),writing=useRef(false),autoGenerate=useRef(false);
  const [busy,setBusy]=useState(null),[error,setError]=useState(null),[scopeStale,setScopeStale]=useState(false),[refresh,setRefresh]=useState(0),[optionsRefresh,setOptionsRefresh]=useState(0);
  const [config,setConfig]=useState({loading:true,value:null,error:null});
  useEffect(()=>{active.current=true;setScopeStale(false);return()=>{active.current=false;autoGenerate.current=false;};},[key]);
  const current=()=>active.current && identity()===owner;
  const remember=next=>{
    if(identity()!==owner)return;
    latest.current=next;saveReference(key,owner,next,storage);
    if(next?.runId)saveRunReference(owner,{id:next.runId,documentId:next.documentId},storage);
    if(active.current)setReference(next);
  };
  useEffect(()=>{
    let cancelled=false;setConfig({loading:true,value:null,error:null});
    service.options().then(value=>{if(!cancelled && current())setConfig({loading:false,value,error:null});}).catch(failure=>{if(!cancelled && current())setConfig({loading:false,value:null,error:failure});});
    return()=>{cancelled=true;};
  },[service,owner,optionsRefresh]);
  const plan=useMemo(()=>{
    if(!config.value)return {value:null,error:null};
    try{return {value:generationPlan(selection,mode,config.value,actorId),error:null};}catch(failure){return {value:null,error:failure.message};}
  },[stable(selection),mode,config.value,actorId]);
  const readSnapshot=useCallback(async id=>matchingSnapshot(await service.snapshot(id),latest.current?.plan),[service]);
  const snapshot=usePollingResource({id:reference?.runId?null:reference?.snapshotId,owner,load:readSnapshot,pending:snapshotPending,refresh,delay:pollDelay});
  const readRun=useCallback(async id=>{
    const value=await service.run(id);
    if(value.id!==id || value.snapshot_id!==latest.current?.snapshotId || !['queued','running','succeeded','failed','cancelled'].includes(value.status))throw Error('生成状态与本次报告不一致');
    return value;
  },[service]);
  const run=usePollingResource({id:reference?.runId,owner,load:readRun,pending:runPending,refresh,delay:pollDelay});
  const readDocument=useCallback(async id=>{
    const value=matchingDocument(await service.document(id),latest.current);
    return value;
  },[service]);
  const document=usePollingResource({id:reference?.documentId,owner,load:readDocument,pending:noPolling,refresh,delay:pollDelay});
  const denied=[config.error,snapshot.error,run.error,document.error].find(accessLost);
  const staleRead=[snapshot.error,run.error,document.error].find(scopeChanged);
  useEffect(()=>{if(denied && current()){autoGenerate.current=false;remember(null);setError(denied.message || '报告权限已变化，请重新读取');}},[denied]);
  useEffect(()=>{if(staleRead && current()){autoGenerate.current=false;remember(null);setScopeStale(true);setError(staleRead.message || '所选范围已变化，请重新读取概览');}},[staleRead]);
  useEffect(()=>{
    const value=run.value;
    if(!denied && latest.current?.runId===value?.id && value?.status==='succeeded' && value.document_id && value.result_status!=='insufficient_data' && current() && latest.current.documentId!==value.document_id)remember({...latest.current,documentId:value.document_id});
  },[run.value,denied]);
  const write=async(kind,body)=>{
    if(writing.current || !current() || !plan.value)return;
    writing.current=true;setBusy(kind);setError(null);
    try{
      const value=await (kind==='prepare'?service.prepare(body):service.generate(body));
      // Receipt metadata may still be remembered after leaving. It does not
      // expose content or continue generation, and prevents duplicate recovery.
      if(identity()!==owner || !latest.current?.plan)return;
      if(!value?.id)throw Error('提交回执缺少记录标识，请重试核对本次提交');
      if(kind==='prepare'){
        matchingSnapshot(value,latest.current.plan);
        remember({...latest.current,snapshotId:value.id,runId:null,documentId:null,pending:null});
      }else{
        if(value.snapshot_id!==latest.current.snapshotId)throw Error('生成回执与本次快照不一致，请核对');
        remember({...latest.current,runId:value.id,documentId:null,pending:null});
      }
    }catch(failure){
      autoGenerate.current=false;
      if(identity()===owner){
        if(rejected(failure))remember(latest.current?{...latest.current,pending:null}:null);
        if(scopeChanged(failure)){setScopeStale(true);remember(null);}
        if(accessLost(failure))remember(null);
        if(active.current)setError(failure.message || '本次提交结果待确认，请重试核对');
      }
    }finally{writing.current=false;if(current())setBusy(null);}
  };
  const generateSnapshot=value=>{
    if(!value || value.status!=='ready' || latest.current?.runId || writing.current || !plan.value)return;
    try{matchingSnapshot(value,latest.current.plan);}catch(failure){autoGenerate.current=false;if(scopeChanged(failure)){setScopeStale(true);remember(null);}if(active.current)setError(failure.message || '报告快照范围不可用，请重新准备');return;}
    autoGenerate.current=false;
    remember({...latest.current,pending:'generate'});
    write('generate',{snapshot_id:value.id,snapshot_hash:value.snapshot_hash,scheme:latest.current.plan.scheme});
  };
  useEffect(()=>{
    if(autoGenerate.current && !busy && snapshot.value?.status==='ready' && !snapshot.error && current())generateSnapshot(snapshot.value);
  },[snapshot.value,busy,plan.value]);
  const pending=Boolean(busy || (reference?.runId && (run.loading || runPending(run.value || {}))) || (reference?.snapshotId && !reference.runId && (snapshot.loading || snapshotPending(snapshot.value || {}))));
  const start=()=>{
    if(!plan.value || writing.current || pending || !current() || latest.current?.pending)return;
    autoGenerate.current=true;remember({mode,plan:plan.value,snapshotId:null,runId:null,documentId:null,pending:'prepare'});
    write('prepare',plan.value.selection);
  };
  const resume=()=>{
    if(!plan.value || writing.current || !current())return;
    // An earlier mounted instance may have received its POST receipt after
    // leaving. Adopt it before retrying, so a cleared transport key cannot
    // accidentally turn that recovery into a second successful submission.
    const stored=readReference(key,owner,storage);
    if(stored && stable([stored.snapshotId,stored.runId,stored.documentId,stored.pending])!==stable([latest.current?.snapshotId,latest.current?.runId,latest.current?.documentId,latest.current?.pending])){remember(stored);return;}
    const ref=latest.current;
    if(ref?.pending==='prepare'){autoGenerate.current=true;write('prepare',ref.plan.selection);}
    else if(snapshot.value?.status==='ready'){generateSnapshot(snapshot.value);}
  };
  const readError=snapshot.error || run.error || document.error;
  const result=document.value && !denied?document.value:null;
  return {owner,reference,busy,pending,error,scopeChanged:scopeStale,readError,config,capabilityError:plan.error,snapshot,run,document,result,
    start,resume,retryRead:()=>{setError(null);setRefresh(value=>value+1);},reloadOptions:()=>setOptionsRefresh(value=>value+1),canGenerate:Boolean(plan.value) && !scopeStale};
}
