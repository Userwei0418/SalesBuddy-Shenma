import React,{forwardRef,useEffect,useImperativeHandle,useRef,useState} from 'react';
import {Alert,Button,Checkbox,Select,Spin} from 'antd';
import {identity} from '../pc/data.js';
import FilterBar from '../pc/FilterBar.jsx';
import {todayKey} from '../pc/period.js';
import {useAsync} from '../pc/ui.jsx';
import MaterialReview,{ReportFilters,ReportMetrics,ReportSupplement} from './MaterialReview.jsx';
import {ReportBlank,ReportStatus} from './ReportFrame.jsx';
import {RunProgress} from './DocumentEditor.jsx';
import ReportSources from './ReportSources.jsx';
import {allSchemes,fromPeriod,toPeriod,materialView,metricViews,scopeOptions} from './presentation.js';
import {snapshotSelection,reportSupplements,accessLost} from './composition.js';
import {PERIOD_CHOICES,stable} from './state.js';
import {useLeaveGuard} from './useLeaveGuard.js';
import {usePollingResource} from './usePollingResource.js';
import {workReportApi} from './api.js';
import {SALES_WEEK_FACTS,WEEK_SECTION_LABELS} from './sales-week.js';
import {composeSchemes,chooseComposeScheme,compatibleScheme} from './default-schemes.js';
import {boundSnapshotSelection,overviewStale,OVERVIEW_REASONS} from './overview.js';

const ComposeState=forwardRef(function ComposeState({page,options,owner,initialSchemeId,initialPeriod,initialAnchor,initialScope,initialProfile,initialRunId,initialOverview,onOverview,onRunChange,onRunObserved,onDocument,onConfigure,service=workReportApi,pollDelay=1500},ref) {
  const [filters,setFilters]=useState({schemeId:initialSchemeId,period:initialPeriod,anchor:initialAnchor || todayKey()});
  const [scope,setScope]=useState(initialScope?.scope || {kind:options.available_scope_kinds.includes('self')?'self':options.available_scope_kinds[0]});
  const [sources,setSources]=useState(null),[supplement,setSupplement]=useState('');
  const [supplementSection,setSupplementSection]=useState('');
  const [prepared,setPrepared]=useState(null),[runId,setRunId]=useState(initialRunId || null),[refresh,setRefresh]=useState(0);
  const [submittedSupplement,setSubmittedSupplement]=useState(null);
  const [busy,setBusy]=useState(null),[error,setError]=useState(initialScope?.error || null),[sourceType,setSourceType]=useState(null);
  const [bindingInvalid,setBindingInvalid]=useState(false);
  const [supplementId]=useState(()=>crypto.randomUUID());
  const active=useRef(false),writing=useRef(false),serial=useRef(0),opened=useRef(null);
  useEffect(()=>{active.current=true;return()=>{active.current=false;serial.current++;};},[]);
  const current=()=>active.current && owner===identity();
  const schemes=useAsync(async signal=>composeSchemes(await allSchemes(service,signal),options),[owner,service,options]);
  const explicitlySelected=schemes.value?.find(item=>item.id===filters.schemeId);
  const period=filters.period || fromPeriod(explicitlySelected?.config.default_period || options.default_scheme.default_period);
  const schemeChoices=(schemes.value || []).filter(row=>!(initialOverview || initialProfile) || compatibleScheme(row,toPeriod(period)) && (row.config.facts_contract===SALES_WEEK_FACTS)===((initialOverview?.selection.generation_profile || initialProfile)==='sales_week_six'));
  const factsContract=initialOverview?(initialOverview.selection.generation_profile==='sales_week_six'?SALES_WEEK_FACTS:null):(initialProfile==='general'?null:initialProfile==='sales_week_six'?SALES_WEEK_FACTS:options.default_scheme.facts_contract ?? null);
  const scheme=chooseComposeScheme(schemeChoices,{schemeId:filters.schemeId,period:toPeriod(period),factsContract});
  const selectedSources=initialOverview?.generation_binding?.snapshot_selection.sources ?? sources ?? scheme?.config.default_sources ?? options.default_scheme.default_sources;
  const salesWeekly=scheme?.config.facts_contract===SALES_WEEK_FACTS;
  const sectionId=salesWeekly?supplementSection:'';
  const weeklyPeriodMismatch=salesWeekly && period!=='week';
  const draft={...filters,period,scope,sources:selectedSources,supplement,supplementId,supplementSection:sectionId,...(scheme?.config.facts_contract?{facts_contract:scheme.config.facts_contract}:{})};
  const draftKey=stable({...draft,overviewHash:initialOverview?.overview_hash,scheme:scheme?{id:scheme.id,version:scheme.version_no}:null});
  const matching=prepared?.draftKey===draftKey;
  const snapshot=usePollingResource({id:matching?prepared.id:null,owner,load:service.snapshot,pending:value=>value.status==='preparing',delay:pollDelay,refresh});
  const run=usePollingResource({id:runId,owner,load:service.run,pending:value=>['queued','running'].includes(value.status),delay:pollDelay,refresh});
  useEffect(()=>{if(runId && current())onRunObserved?.({id:runId,status:run.value?.status,unavailable:[403,404].includes(run.error?.statusCode || run.error?.status)});},[runId,run.value,run.error,onRunObserved]);
  const pendingRun=Boolean(runId && (run.loading || run.error || ['queued','running'].includes(run.value?.status)));
  const locked=Boolean(busy || pendingRun);
  // Only the exact supplement submitted to this run is recoverable there.
  // A succeeded/insufficient_data run has no saved report to return to; later
  // edits are also local even when a previous run produced a document.
  const supplementSaved=submittedSupplement?.runId===runId && submittedSupplement?.text===supplement && submittedSupplement?.sectionId===sectionId &&
    (['queued','running'].includes(run.value?.status) || (run.value?.status==='succeeded' && run.value.document_id && run.value.result_status!=='insufficient_data'));
  const leave=useLeaveGuard({page,owner,dirty:Boolean(supplement.trim()) && !supplementSaved,saving:Boolean(busy)});
  useImperativeHandle(ref,()=>({leave}),[leave]);
  useEffect(()=>{
    const value=run.value;
    if(!busy && value?.status==='succeeded' && value.document_id && current() && opened.current!==value.id) {
      opened.current=value.id;onDocument?.(value.document_id);
    }
  },[run.value,busy,onDocument]);
  useEffect(()=>{if(overviewStale(snapshot.value?.error) || overviewStale(run.value?.error))setBindingInvalid(true);},[snapshot.value,run.value]);
  const change=action=>{
    if(writing.current || pendingRun || !current())return;
    serial.current++;setPrepared(null);setSourceType(null);setError(null);action();
  };
  const mutate=async(kind,action,accept)=>{
    if(writing.current || !current())return;
    writing.current=true;setBusy(kind);setError(null);const request=++serial.current;
    try {const value=await action();if(current() && request===serial.current)accept(value);}
    catch(failure){if(current() && request===serial.current){
      setError(failure.message || '操作失败，请重试');
      if(accessLost(failure)){setPrepared(null);setSourceType(null);}
      if(overviewStale(failure)){setBindingInvalid(true);setPrepared(null);}
      if(kind==='cancel' && (failure.statusCode || failure.status)===409)setRefresh(value=>value+1);
    }}
    finally{writing.current=false;if(current() && request===serial.current)setBusy(null);}
  };
  const prepare=()=>{
    if(pendingRun || !options.can_create || !scheme || bindingInvalid)return;
    let selection;try{selection=initialOverview?boundSnapshotSelection(initialOverview,reportSupplements(draft),scheme):snapshotSelection(draft,options);}catch(failure){setError(failure.message);if(overviewStale(failure))setBindingInvalid(true);return;}
    setPrepared(null);setSourceType(null);
    mutate('prepare',()=>service.prepare(selection),value=>{setRunId(null);setSubmittedSupplement(null);onRunChange?.(null);setPrepared({id:value.id,draftKey});});
  };
  const generate=()=>{
    if(!matching || snapshot.value?.status!=='ready' || pendingRun || !options.can_create || !options.generation_available || !scheme || bindingInvalid)return;
    if(!materialView(snapshot.value,scheme.config,true).canGenerate)return;
    const payload={snapshot_id:snapshot.value.id,snapshot_hash:snapshot.value.snapshot_hash,scheme:{scheme_id:scheme.id,version_no:scheme.version_no}};
    mutate('generate',()=>service.generate(payload),value=>{setRunId(value.id);setSubmittedSupplement({runId:value.id,text:supplement,sectionId});onRunChange?.(value.id);});
  };
  const cancel=()=>{
    if(!run.value || !['queued','running'].includes(run.value.status))return;
    mutate('cancel',()=>service.cancel(runId,{version_no:run.value.version_no}),()=>setRefresh(value=>value+1));
  };
  if(owner!==identity())return null;
  const readError=snapshot.error || run.error;
  const denied=accessLost(readError);
  const ready=!denied && matching && snapshot.value?.status==='ready';
  const canPrepare=Boolean(options.can_create && scheme && !locked && !weeklyPeriodMismatch && !bindingInvalid && (!initialOverview || initialOverview.capabilities.can_generate));
  const review=ready?materialView(snapshot.value,scheme.config,options.can_create && options.generation_available && !locked && !bindingInvalid):null;
  const generating=busy==='generate' || pendingRun;
  const primaryAction=<Button type="primary" onClick={ready?generate:prepare} disabled={generating || (ready?!review.canGenerate:!canPrepare)} loading={Boolean(busy==='prepare' || generating)}>
    {generating?'正在生成报告':ready?'生成报告':'准备材料'}
  </Button>;
  const scopeControl=<div className="wr-scope-controls"><Select aria-label="报告范围" value={scope.kind} options={scopeOptions(options)} disabled={locked} onChange={kind=>change(()=>setScope({kind}))}/>
    {['member','team'].includes(scope.kind) && <Select mode="multiple" aria-label={scope.kind==='member'?'报告成员':'报告部门'} placeholder={scope.kind==='member'?'选择成员（可多选）':'选择部门（可多选）'} value={scope.kind==='member'?(scope.member_ids || (scope.member_id?[scope.member_id]:[])):(scope.team_ids || (scope.team_id?[scope.team_id]:[]))}
      options={(scope.kind==='member'?options.members:options.teams).map(item=>({value:item.id,label:item.name}))} showSearch optionFilterProp="label" disabled={locked} maxTagCount="responsive" maxCount={100}
      onChange={ids=>change(()=>setScope({kind:scope.kind,[scope.kind==='member'?'member_ids':'team_ids']:ids}))}/>}</div>;
  return <div className="wr-compose-flow">
    <div className="wr-compose-controls">{initialOverview?<FilterBar label="当前概览对应报告" actions={primaryAction}>
      <span data-toolbar-width={280}>{initialOverview.period.label} · {initialOverview.scope.label}</span>
      <Select aria-label="使用报告方案" value={scheme?.id} options={schemeChoices.map(row=>({value:row.id,label:row.name}))} disabled={locked} placeholder="选择报告方案"
        onChange={schemeId=>change(()=>setFilters(previous=>({...previous,schemeId})))} data-toolbar-width={240}/>
      <Button onClick={onOverview} disabled={locked} data-toolbar-width={130}>返回本期概览</Button>
    </FilterBar>:<ReportFilters value={{...filters,period,schemeId:scheme?.id}} onChange={value=>change(()=>{
      if(value.schemeId!==scheme?.id){setSources(null);setSupplementSection('');value.period=undefined;}
      else if(value.period!==period){
        value.schemeId=filters.schemeId && compatibleScheme(scheme,toPeriod(value.period))?filters.schemeId:undefined;
        const next=chooseComposeScheme(schemes.value || [],{schemeId:value.schemeId,period:toPeriod(value.period),factsContract});
        if(next?.id!==scheme?.id){setSources(null);setSupplementSection('');}
      }
      else value.schemeId=filters.schemeId;
      setFilters(value);
    })}
      scopeControl={scopeControl} periods={PERIOD_CHOICES.filter(item=>options.periods.includes(toPeriod(item.value)))} schemes={schemeChoices.map(item=>({value:item.id,label:item.name}))} disabled={locked} actions={primaryAction}/>}</div>
    {initialOverview && !initialOverview.capabilities.can_generate && <Alert type="info" title={OVERVIEW_REASONS[initialOverview.capabilities.generation_unavailable_reason]}/>}
    {bindingInvalid && <Alert type="warning" title="概览材料已变化或过期，请刷新后再生成" action={<Button onClick={onOverview} disabled={locked}>返回本期概览</Button>}/>}
    {!options.can_create && <Alert type="info" title="当前账号可查看已有报告，暂不可生成新报告"/>}
    {!options.generation_available && <Alert type="info" title="报告生成暂不可用，可先准备和查看材料"/>}
    {weeklyPeriodMismatch && <Alert type="info" title="当前销售周报方案仅适用于自然周" description="月报、季报等周期请在上方选择通用方案，或到报告方案中新建通用方案。"
      action={options.general_scheme && onConfigure && <Button onClick={onConfigure} disabled={locked}>配置通用方案</Button>}/>}
    {schemes.loading?<Spin/>:schemes.error?<Alert type="error" title={schemes.error} action={<Button onClick={schemes.reload}>重新读取方案</Button>}/>:!scheme && <Alert type="info" title="请先选择或创建报告方案" action={onConfigure && <Button onClick={onConfigure}>配置报告方案</Button>}/>}
    {error && <Alert type="error" title={error}/>}
    {readError && <Alert type="error" title={readError.message || '状态暂时无法读取'} action={<Button onClick={()=>setRefresh(value=>value+1)}>重新读取状态</Button>}/>}
    {snapshot.value?.status==='failed' && <Alert type="error" title={snapshot.value.error?.detail || '材料准备失败，请重试'}/>}
    {run.value && !pendingRun && <div className="wr-panel"><ReportStatus status={run.value.result_status || run.value.status}/>
      {run.value.error && <p>{run.value.error.detail}</p>}
      {run.value.result_status==='insufficient_data' && <p>本次材料不足，未生成正文。请调整来源或补充说明后重新准备。</p>}
      {run.value.status==='succeeded' && run.value.document_id && <Button onClick={()=>onDocument?.(run.value.document_id)}>打开报告草稿</Button>}
    </div>}
    {pendingRun && !run.error && <RunProgress label={run.loading?'正在读取生成状态':run.value.status==='queued'?'等待生成':'正在生成报告'} onCancel={!run.loading?cancel:undefined} cancelling={busy==='cancel'}/>}
    {snapshot.loading || snapshot.value?.status==='preparing'?<RunProgress label="正在准备报告材料" description="按当前授权范围收集材料与统计，可调整条件重新准备。"/>:null}
    {ready && <ReportMetrics items={metricViews(snapshot.value.metrics)}/>}
    <div className="wr-compose"><div>{ready?<MaterialReview value={review} busy={locked}
      onSources={setSourceType} onGenerate={generate} onRefresh={prepare}/>:!pendingRun && !snapshot.loading && snapshot.value?.status!=='preparing' && <ReportBlank disabled={!canPrepare} onPrepare={prepare}/>}</div>
      <aside className="wr-aside"><section className="wr-panel" aria-label="选择报告材料"><h2>材料来源</h2><Checkbox.Group className="wr-source-choices" value={selectedSources} disabled={locked || Boolean(initialOverview)}
        options={options.sources.filter(item=>salesWeekly || item.id!=='actual' || selectedSources.includes(item.id)).map(item=>({value:item.id,label:item.label,disabled:!item.available && !selectedSources.includes(item.id)}))} onChange={value=>change(()=>setSources(value))}/>
        {options.sources.filter(item=>!item.available).map(item=><p className="wr-muted" key={item.id}>{item.label}：{item.unavailable_reason || '暂不可用'}</p>)}</section>
        <ReportSupplement text={supplement} onChange={value=>change(()=>setSupplement(value))} disabled={locked} maxLength={16000}
          sections={salesWeekly?scheme.config.sections.filter(row=>row.enabled && Object.hasOwn(WEEK_SECTION_LABELS,row.id)):undefined}
          sectionId={sectionId} onSectionChange={value=>change(()=>setSupplementSection(value))}/>
      </aside></div>
    <ReportSources open={sourceType!==null && ready} sourceType={sourceType} snapshotId={snapshot.value?.id} owner={owner} service={service} onClose={()=>setSourceType(null)}
      onAccessLost={failure=>{setPrepared(null);setSourceType(null);setError(failure.message);}}/>
  </div>;
});
export default forwardRef(function ReportCompose(props,ref){return <ComposeState key={props.owner} {...props} ref={ref}/>;});
