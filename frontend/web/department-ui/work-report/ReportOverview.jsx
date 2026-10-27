import React,{useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {Alert,Button,Input,Select,Spin} from 'antd';
import FilterBar from '../pc/FilterBar.jsx';
import {identity} from '../pc/data.js';
import {todayKey} from '../pc/period.js';
import {useAsync} from '../pc/ui.jsx';
import ReportReadStatus from './ReportReadStatus.jsx';
import MeetingFactsBoard from './MeetingFactsBoard.jsx';
import ReviewOverview from '../review/ReviewOverview.jsx';
import {reviewOverviewApi} from '../review/overview-api.js';
import {scopeFilter,reviewScopeRequest,readScopeQuery,multiComposePreset} from '../review/scope-selection.js';
import ReviewSummaryPanel from '../review/report/ReviewSummaryPanel.jsx';
import {PERIOD_CHOICES,calendarHint} from './state.js';
import {fromPeriod,toPeriod} from './presentation.js';
import {initialReportScope} from './route.js';
import {overviewGenerateHint} from './compose-entry.js';
import {overviewOptions,overviewScopes,overviewProfile,matchingOverview,OVERVIEW_REASONS} from './overview.js';

export function OverviewState({owner,service,options,initialPeriod,initialFilters,initialQuery,onSelection,onReady,onCompose,onOpenDocument}) {
  const entry=initialReportScope(initialQuery,options),saved=readScopeQuery(initialQuery,options);
  const [filters,setFilters]=useState(()=>{
    const period=initialPeriod || fromPeriod(options.period_kinds[0]),scope=entry?.scope;
    return initialFilters || saved?.filters || {period,anchor:todayKey(),profile:overviewProfile(period,options),
      scopeKey:scope?.kind==='self'?'self':scope?`${scope.kind}:${scope.member_id || scope.team_id}`:overviewScopes(options)[0]?.value};
  });
  const [refresh,setRefresh]=useState(0),[entryError,setEntryError]=useState(saved?.error || entry?.error);
  const requested=useMemo(()=>{try{return {request:reviewScopeRequest(filters,options)};}catch(error){return {error:error.message};}},[filters,options,refresh]);
  const [legacyExpanded,setLegacyExpanded]=useState(null);
  const legacyOpen=Boolean(requested.request?.legacy && legacyExpanded===requested.request.legacy.requestId);
  // The primary Review cards do not need the old complete-material / signed
  // overview. Read it only when its optional detail section is requested.
  const result=useAsync(async signal=>legacyOpen && owner===identity()?matchingOverview(await service.overview(requested.request.legacy.query,{signal}),requested.request.legacy):null,[owner,service,requested,legacyOpen]);
  // The legacy signed overview only supports one subject. Read a multi-subject
  // selection through the v2 scope contract here and hand that response to the
  // overview panel so it does not issue a second, unbound request.
  const v2Result=useAsync(async signal=>requested.request?.multiple?reviewOverviewApi.overview(requested.request.review,{signal}):null,[owner,requested.request?.multiple,requested.request?.review,refresh]);
  const value=!result.error && result.value?.request_id===requested.request?.legacy?.requestId?result.value:null;
  const generation=useAsync(()=>requested.request?.multiple && typeof service.options==='function'?service.options():null,[owner,service,requested]);
  const composeValue=useMemo(()=>{if(!requested.request?.multiple)return value;
    if(v2Result.loading || v2Result.error || generation.loading || generation.error)return null;
    try{return multiComposePreset(filters,v2Result.value,generation.value);}catch{return null;}
  },[requested,value,filters,v2Result.value,v2Result.loading,v2Result.error,generation.value,generation.loading,generation.error]);
  useEffect(()=>{onSelection?.(filters);},[filters,onSelection]);
  useEffect(()=>{onReady?.(composeValue);return()=>onReady?.(null);},[composeValue,onReady]);
  const scope=scopeFilter(filters);
  const [reviewRead,setReviewRead]=useState(null),activeRequest=useRef(requested.request);
  activeRequest.current=requested.request;
  const reviewLoading=Boolean(requested.request && (requested.request.multiple?v2Result.loading:reviewRead?.request===requested.request?reviewRead.loading:true));
  const onReviewLoading=useCallback(loading=>{
    if(owner===identity() && activeRequest.current===requested.request)setReviewRead(previous=>previous?.request===requested.request && previous.loading===loading?previous:{request:requested.request,loading});
  },[owner,requested.request]);
  const scopeLabel=scope.kind==='self'?'本人':scope.ids.map(id=>(scope.kind==='team'?options.teams:options.members).find(row=>row.id===id)?.name || '所选范围').join('、');
  const pendingTitle=`${calendarHint(filters.period,filters.anchor)} · ${scopeLabel}`;
  const change=patch=>{if(owner!==identity())return;onReady?.(null);setEntryError(null);setFilters(previous=>({...previous,...patch}));};
  const reload=()=>{if(reviewLoading || owner!==identity())return;onReady?.(null);setRefresh(previous=>previous+1);};
  if(owner!==identity())return null;
  return <div className="wr-compose-flow">
    <FilterBar label="本期概览条件" moreLabel="更多条件" actions={<Button onClick={reload} loading={reviewLoading} disabled={reviewLoading}>刷新</Button>}>
      <Select aria-label="概览周期" value={filters.period} options={PERIOD_CHOICES.filter(row=>options.period_kinds.includes(toPeriod(row.value)))}
        onChange={period=>change({period,profile:overviewProfile(period,options)})} data-toolbar-width={120}/>
      <Input aria-label="概览周期内日期" type="date" value={filters.anchor} max={todayKey()} title={calendarHint(filters.period,filters.anchor)} onChange={event=>change({anchor:event.target.value})} data-toolbar-width={156}/>
      <Select aria-label="查看范围" value={scope.kind} options={options.available_scope_kinds.filter(value=>['self','team','member'].includes(value)).map(value=>({value,label:({self:'本人',team:'团队',member:'成员'})[value]}))} onChange={scopeKind=>change({scopeKind,subjectIds:[]})} data-toolbar-width={120}/>
      {scope.kind!=='self' && <Select mode="multiple" aria-label={scope.kind==='team'?'选择团队':'选择成员'} placeholder={scope.kind==='team'?'选择一个或多个团队':'选择一个或多个成员'} value={scope.ids} maxCount={100} maxTagCount="responsive" allowClear showSearch optionFilterProp="label" options={(scope.kind==='team'?options.teams:options.members).map(row=>({value:row.id,label:row.name}))} onChange={subjectIds=>change({subjectIds})} data-toolbar-width={300}/>}
      {filters.period==='week' && options.generation.profiles.includes('sales_week_six') && <Select aria-label="报告类型" value={filters.profile} onChange={profile=>change({profile})}
        options={[{value:'sales_week_six',label:'销售六节周报'},{value:'general',label:'通用工作报告'}]} data-toolbar-width={180}/>}
    </FilterBar>
    {entryError && <Alert type="info" title={entryError}/>}
    {requested.error && <Alert type="warning" title={requested.error}/>}
    {generation.error && <Alert type="error" title={generation.error} action={<Button onClick={generation.reload}>重新读取报告权限</Button>}/>}
    {composeValue?.kind==='scope_v2' && !composeValue.capabilities.can_generate && <Alert type="info" title="当前所选范围暂不可生成报告，请检查生成权限和服务状态"/>}
    {requested.request && <ReviewOverview selection={requested.request.review} refresh={refresh} memberOptions={options.available_scope_kinds.includes('member')?options.members.map(row=>row.id):[]}
      pendingTitle={pendingTitle} onLoadingChange={onReviewLoading} overviewControlled={requested.request.multiple} overviewLoading={v2Result.loading} overviewError={v2Result.error} overviewValue={v2Result.value} onOverviewReload={v2Result.reload}
      onMember={memberId=>{const scopeKey=`member:${memberId}`;if(overviewScopes(options).some(row=>row.value===scopeKey))change({scopeKey,scopeKind:'member',subjectIds:[memberId]});}}
      sidePanel={['day','week'].includes(filters.period) && typeof service.options==='function'?(selection,_value,onReload)=><ReviewSummaryPanel selection={selection} mode={filters.period==='week'?'weekly':'daily'} service={service} onOpenDocument={onOpenDocument} onReloadOverview={onReload}/>:null}
      actions={<div className="wr-generation-entry"><Button type="primary" onClick={()=>owner===identity() && onCompose(composeValue)}>生成报告</Button><span role="status">{overviewGenerateHint({value:composeValue,loading:v2Result.loading || generation.loading,error:v2Result.error || generation.error})}</span></div>}/>}
    {requested.request?.legacy && <details key={requested.request.legacy.requestId} className="rv-previous-facts" open={legacyOpen}
      onToggle={event=>{if(owner===identity())setLegacyExpanded(event.currentTarget.open?requested.request.legacy.requestId:null);}}>
      <summary>业务变动与新增任务明细<span>展开后读取原有 ACV、期初与期末变化及任务新增情况</span></summary>
      {legacyOpen && <>
        {result.loading?<ReportReadStatus loading requestKey={result.reload} label="正在读取业务变动明细"/>:result.error?<Alert type="error" title="业务变动明细暂未读取完成" description={result.error} action={<Button onClick={result.reload}>重新读取明细</Button>}/>:value?<>
          {!value.capabilities.can_generate && value.capabilities.generation_unavailable_reason!=='SOURCE_NOT_GENERATABLE' && <p className="wr-muted">{OVERVIEW_REASONS[value.capabilities.generation_unavailable_reason]}</p>}
          <MeetingFactsBoard key={value.request_id} facts={value.facts} showTaskActivity taskActivity={value.task_activity_v2 ?? value.task_activity} title={`${PERIOD_CHOICES.find(row=>row.value===filters.period)?.label || '本期概览'} · ${value.scope.label}`}/>
        </>:null}
      </>}
    </details>}
  </div>;
}

export default function ReportOverview(props) {
  const options=useAsync(async()=>overviewOptions(await props.service.overviewOptions()),[props.owner,props.service]);
  return options.loading?<Spin/>:options.error?<Alert type="error" title={options.error} action={<Button onClick={options.reload}>重新读取概览配置</Button>}/>:
    options.value?<OverviewState key={props.owner} {...props} options={options.value}/>:null;
}
