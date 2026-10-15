import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Alert,Button,Spin} from 'antd';
import {identity,runtime,session,can} from '../pc/data.js';
import {useAsync} from '../pc/ui.jsx';
import {ReportFrame} from './ReportFrame.jsx';
import ReportCompose from './ReportCompose.jsx';
import ReportDocument from './ReportDocument.jsx';
import ReportDocuments from './ReportDocuments.jsx';
import ReportSchemes from './ReportSchemes.jsx';
import ReportOverview from './ReportOverview.jsx';
import ReviewTargets from '../review/targets/ReviewTargets.jsx';
import ReviewOkr from '../review/targets/okr/ReviewOkr.jsx';
import {fromPeriod} from './presentation.js';
import {reportRoute,reportQuery,initialReportScope} from './route.js';
import {scopeQuery} from '../review/scope-selection.js';
import {workReportApi} from './api.js';
import {readRunReference,saveRunReference} from './run-recovery.js';

function WorkspaceState({page,owner,service}) {
  const [route,setRoute]=useState(()=>{const entry=reportRoute(page?.options);return {...entry,activeRun:entry.activeRun || readRunReference(owner)};});
  const [settledRun,setSettledRun]=useState(null);
  const [composePreset,setComposePreset]=useState(null);
  const [overviewFilters,setOverviewFilters]=useState(null),[overviewValue,setOverviewValue]=useState(null);
  const editor=useRef(null);
  const needsReportOptions=!['meeting','targets','okr'].includes(route.tab);
  const options=useAsync(()=>needsReportOptions?service.options():Promise.resolve(null),[owner,service,needsReportOptions]);
  const activeRun=route.activeRun;
  const settled=Boolean(activeRun && settledRun?.id===activeRun.id);
  useEffect(()=>{if(owner===identity())saveRunReference(owner,settled?null:activeRun);},[owner,activeRun,settled]);
  const selectOverview=useCallback(filters=>{
    if(owner!==identity())return;
    setOverviewFilters(filters);
    runtime().replacePageQuery(page,scopeQuery(filters));
  },[owner,page]);
  const observed=useCallback(value=>{
    if(owner!==identity() || value.id!==activeRun?.id || (!value.unavailable && !['succeeded','failed','cancelled'].includes(value.status)))return;
    setSettledRun(previous=>previous?.id===value.id && previous.unavailable===value.unavailable?previous:value);
  },[owner,activeRun?.id]);
  const update=patch=>{
    if(owner!==identity())return;
    const next={...route,...patch};setRoute(next);
    // Reuses the runtime's in-page query update: no new history entry, no
    // remount, and no draft text, private example or credential in storage.
    runtime().replacePageQuery(page,reportQuery(next));
  };
  const navigate=(patch,before)=>{
    const apply=()=>{before?.();update(settled && patch.tab && patch.tab!==route.tab?{activeRun:null,...patch}:patch);};
    return editor.current?.leave?editor.current.leave(apply):apply();
  };
  const openDocument=documentId=>update({tab:'documents',documentId,activeRun:null});
  const newReport=()=>navigate({tab:'meeting',documentId:null,schemeId:null});
  const resume=()=>navigate({tab:activeRun.documentId?'documents':'compose',documentId:activeRun.documentId});
  const initialPeriod=page?.options?.period || (page?.route==='pages/weekly-report/index'?'week':undefined);
  const composeFromOverview=overview=>{
    if(!overview || owner!==identity())return;
    if(activeRun && !settled){resume();return;}
    navigate({tab:'compose',documentId:null,schemeId:null,activeRun:null},()=>setComposePreset(overview.kind==='scope_v2'?overview:{overview,period:fromPeriod(overview.selection.period.kind),anchor:overview.selection.period.anchor_date,
      scope:overview.generation_binding?.snapshot_selection.scope || overview.selection.scope}));
  };
  const config=options.value;
  return <ReportFrame tab={route.tab} disabledTabs={route.tab==='meeting' && !activeRun && !overviewValue?.capabilities.can_generate?['compose']:[]}
    onTabChange={tab=>tab==='compose' && activeRun && !settled?resume():tab==='compose' && route.tab==='meeting'?(overviewValue?.capabilities.can_generate && composeFromOverview(overviewValue)):navigate({tab,documentId:tab==='documents'?route.documentId:null})}
    actions={<>{route.tab==='documents' && route.documentId && <Button onClick={()=>navigate({documentId:null})}>返回报告列表</Button>}{can('target.read') && <>{route.tab!=='targets' && <Button onClick={()=>navigate({tab:'targets',documentId:null})}>目标值管理</Button>}{route.tab!=='okr' && <Button onClick={()=>navigate({tab:'okr',documentId:null})}>目标与关键结果</Button>}</>}</>}
    error={needsReportOptions?options.error:null} onRetry={options.reload}>
    {activeRun && settledRun?.id===activeRun.id && settledRun.unavailable?
      <Alert type="warning" title="这份生成记录已无法访问" description="记录可能已移除或查看权限已变化，可返回概览重新选择。" action={<Button onClick={()=>navigate({tab:'meeting',documentId:null,activeRun:null})}>返回本期概览</Button>}/>:
      activeRun && !settled && ((activeRun.documentId || route.tab!=='compose') && (!activeRun.documentId || route.documentId!==activeRun.documentId || route.tab!=='documents')) &&
      <Alert type="info" title="有一份报告生成记录可继续查看" action={<Button onClick={resume}>查看生成状态</Button>}/>}
    {route.tab==='okr'?<ReviewOkr ref={editor} onBack={()=>navigate({tab:'meeting'})} onTargets={()=>navigate({tab:'targets'})}/>:route.tab==='targets'?<ReviewTargets ref={editor} onBack={()=>navigate({tab:'meeting'})}/>:route.tab==='meeting'?<ReportOverview owner={owner} service={service} initialPeriod={initialPeriod?fromPeriod(initialPeriod):undefined} initialFilters={overviewFilters} initialQuery={page?.options}
      onSelection={selectOverview} onReady={setOverviewValue} onCompose={composeFromOverview} onOpenDocument={openDocument}/>:options.loading?<Spin/>:config?<>
      {route.tab==='schemes'?<ReportSchemes ref={editor} page={page} options={config} owner={owner} service={service}
        onUse={scheme=>{if(activeRun && !settled){resume();return;}setComposePreset(null);update({tab:'compose',documentId:null,schemeId:scheme.id,activeRun:null});}}/>:route.tab==='documents'?route.documentId?
        <ReportDocument ref={editor} page={page} documentId={route.documentId} actorId={session()?.userId} owner={owner} service={service}
          canGenerate={config.can_create && config.generation_available && (!activeRun || settled || activeRun.documentId===route.documentId)} initialRunId={activeRun?.documentId===route.documentId?activeRun.id:undefined} onRunObserved={observed}
          onRunChange={id=>update({activeRun:id?{id,documentId:route.documentId}:null})}/>:
        <ReportDocuments owner={owner} service={service} onOpen={id=>navigate({documentId:id})} onCreate={config.can_create?newReport:undefined}/>:
        <ReportCompose ref={editor} page={page} options={config} owner={owner} service={service} initialSchemeId={route.schemeId} initialAnchor={composePreset?.anchor || page?.options?.review_anchor} initialPeriod={composePreset?.period || (initialPeriod?fromPeriod(initialPeriod):undefined)} initialScope={composePreset?{scope:composePreset.scope}:initialReportScope(page?.options,config)}
          initialProfile={composePreset?.profile} initialOverview={composePreset?.overview} onOverview={()=>navigate({tab:'meeting',documentId:null})}
          initialRunId={activeRun && !activeRun.documentId?activeRun.id:undefined} onRunObserved={observed} onRunChange={id=>update({activeRun:id?{id,documentId:null}:null})}
          onDocument={openDocument} onConfigure={()=>navigate({tab:'schemes',documentId:null})}/>}
    </>:null}
  </ReportFrame>;
}
export default function ReportWorkspace({page,service=workReportApi}) {
  const owner=identity();
  return <WorkspaceState key={owner} page={page} owner={owner} service={service}/>;
}
