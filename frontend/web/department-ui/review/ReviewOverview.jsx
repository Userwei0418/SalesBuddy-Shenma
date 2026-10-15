import React,{useMemo,useState} from 'react';
import {Alert,Button,Drawer,Empty,Input,Pagination,Progress,Select,Spin,Table,Tag,Tooltip} from 'antd';
import {open,stageLabel,identity,runtime,INTERACTION_MODE} from '../pc/data.js';
import {useAsync,Panel} from '../pc/ui.jsx';
import {useListPageSize,PAGE_SIZE_OPTIONS} from '../pc/list-pagination.js';
import ReviewMeetingDeck from './ReviewMeetingDeck.jsx';
import {reviewOverviewApi} from './overview-api.js';
import {REVIEW_METRICS,REVIEW_PRIMARY_METRICS,REVIEW_SECONDARY_METRICS,REVIEW_ACTIVITIES,REVIEW_POOL_FILTERS,selectionKey,detailSelectionKey,bindReviewScope,money,exactMoney,percentage,timestamp,metricState,changeRows,changeValue,changeLabel,formatDecimal,coverageText,reasonText} from './overview-model.js';
import './review-overview.css';

const text=value=>value===null || value===undefined || value===''?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
const moneyCell=value=><span className="rv-money" title={exactMoney(value)}>{money(value)}</span>;
const businessLabel=(group,code)=>{
  if(!code)return '未记录';
  if(group==='interactionModes')return INTERACTION_MODE[code] || code;
  try{return runtime().require('utils/businessOptions').customer.source.find(row=>row.code===code)?.label || code;}catch{return code;}
};
const link=(label,action)=>action?<Button type="link" className="rv-link" onClick={action}>{label || '查看详情'}</Button>:text(label);
const company=row=><div>{row.customer_name || '客户名称未记录'}</div>;
const opportunity=row=><div>{link(row.opportunity_name || row.name,row.opportunity_id || row.id?()=>open.opportunity(row.customer_id,row.opportunity_id || row.id):null)}<small>{row.customer_name || '客户名称未记录'}</small></div>;
const memberNames=(row,members)=>row.owner_name || (row.owners || []).map(owner=>owner.name).filter(Boolean).join('、') || (row.member_ids || [row.owner_id]).map(id=>members.find(member=>member.id===id)?.name).filter(Boolean).join('、') || '未记录';

export function ReviewMetricCards({value,onDrill,definitions=REVIEW_PRIMARY_METRICS}) {
  const progress=Math.max(0,Math.min(100,Number(value.quarter.time_progress)*100));
  return <div className="rv-metric-grid" style={{'--rv-metric-columns':definitions.length}} data-testid="review-metric-cards">
    {definitions.map(definition=>{
      const metric=value.metrics.find(row=>row.metric_code===definition.code),state=metricState(metric);
      const target=metric.target.status==='effective',incompleteTarget=metric.target.coverage && metric.target.coverage!=='complete';
      return <section className="rv-metric" key={definition.code} aria-label={definition.label}>
        <header><h2>Q{value.quarter.quarter} {definition.label}</h2><Tag color={state.color}>{state.label}</Tag></header>
        <div className="rv-metric-main"><span>{metric.coverage==='partial'?'已知小计':'当前预测'}</span><div title={exactMoney(metric.coverage==='partial'?metric.known_subtotal:metric.value)}><strong>{money(metric.coverage==='partial'?metric.known_subtotal:metric.value)}</strong><span>万元</span></div></div>
        <div className="rv-metric-secondary"><div><span>有效目标</span><b title={exactMoney(metric.target.value)}>{target?money(metric.target.value):incompleteTarget?'目标未齐':metric.target.status==='permission_denied'?'不可查看':'未设置'}{target && <small> 万元</small>}</b></div><div><span>预计达成率</span><b>{percentage(metric.rate)}</b></div></div>
        <dl className="rv-metric-details"><div><dt>距目标差额</dt><dd title={exactMoney(metric.gap)}>{money(metric.gap)}{metric.gap!=null?' 万元':''}</dd></div><div><dt>较昨日变化</dt><dd title={metric.baseline?.reason || exactMoney(metric.day_change)}>{metric.day_change==null?'无可信基线':formatDecimal(metric.day_change,{shift:4,signed:true})+' 万元'}</dd></div></dl>
        <div className="rv-time-progress"><span>季度时间进度</span><b>{percentage(value.quarter.time_progress)}</b><Progress percent={Number.isFinite(progress)?progress:0} showInfo={false} size="small" strokeColor="#8CAAF6"/></div>
        <footer><Tooltip title={<div>{definition.definition}<br/>金额单位为人民币；差额为有效目标减预测，负值表示预计超出。<br/>季度时间进度按所选自然日（含当日）计算。{metric.missing_reasons?.length>0 && <><br/>{coverageText(metric)}</>}</div>}><button className="rv-help" type="button" aria-label={`${definition.label}口径说明`}>ⓘ 口径</button></Tooltip>{onDrill && <Button type="link" size="small" onClick={()=>onDrill(definition.code)}>查看明细 →</Button>}</footer>
        {incompleteTarget && <p className="rv-card-note">目标覆盖不完整；已知目标小计 {money(metric.target.known_subtotal)} 万元，暂不计算达成率和差额。</p>}
        {metric.target.components?.length>1 && <details className="rv-target-components"><summary>查看各范围目标</summary>{metric.target.components.map(component=><div key={component.subject_id}><span>{value.scope.subjects?.find(row=>row.id===component.subject_id)?.label || '所选范围'}</span><b>{component.status==='effective'?`${money(component.value)} 万元`:component.status==='permission_denied'?'目标不可查看':'未设目标'}</b></div>)}</details>}
        {metric.coverage!=='complete'  && <p className="rv-card-note">{coverageText(metric) || '数据尚不完整，暂不计算达成率'}</p>}
      </section>;
    })}
  </div>;
}

export function ReviewActionTiles({value,selected,onChange}) {
  return <div className="rv-action-tiles" aria-label="本期动作" data-testid="review-action-tiles">{REVIEW_ACTIVITIES.map(definition=>{
    const row=value.activities.find(item=>item.code===definition.code);
    return <button key={definition.code} type="button" className={selected===definition.code?'is-selected':''} aria-pressed={selected===definition.code} onClick={()=>onChange?.(definition.code)}>
      <span>{definition.label}</span><b>{row.count??'—'}<small>{definition.unit}</small></b>{row.coverage!=='complete' && <em title={coverageText(row)}>{row.coverage==='partial'?`已知 ${row.known_count} ${definition.unit}`:'数据不可用'}</em>}
    </button>;
  })}</div>;
}

function columnsFor(activity,members=[]) {
  const owner={title:'负责人',key:'owner',width:110,render:(_,row)=>memberNames(row,members)};
  const time=(title,key)=>({title,key,width:138,render:(_,row)=>timestamp(row[key])});
  const field=(title,key,width=170)=>({title,dataIndex:key,key,width,render:text});
  const amount={title:'登记金额（万原币）',key:'amount',width:150,render:(_,row)=><span className="rv-money" title={`${formatDecimal(row.amount)} ${row.currency || '币种未记录'}`}>{money(row.amount)}<small>{row.currency || '币种未记录'}</small></span>};
  const task={title:'任务',key:'task',width:290,render:(_,row)=>link(row.name,()=>open.task(row.task_id || row.id))};
  if(activity==='new_claimed_customers')return [{title:'客户名称',key:'customer',width:280,render:(_,row)=>link(row.customer_name || row.name,()=>open.customer(row.customer_id))},{...owner,title:'认领人'},{title:'客户来源',key:'source',width:150,render:(_,row)=>businessLabel('customerSources',row.source_code)},time('认领时间','occurred_at')];
  if(activity==='formal_visits')return [{title:'客户 / 商机',key:'customer',width:230,render:(_,row)=><div>{company(row)}<small>{(row.opportunities || []).map(item=>item.name).join('、') || row.opportunity_name || '未关联商机'}</small></div>},{title:'方式',key:'method',width:90,render:(_,row)=>businessLabel('interactionModes',row.interaction_mode_code)},field('跟进摘要','summary',310),field('下一步','next_action',250),time('实际跟进','interaction_at'),time('录入时间','recorded_at'),{title:'操作',key:'open',width:70,render:(_,row)=>link('查看',()=>open.visit(row.id,row.customer_id))}];
  if(activity==='new_opportunities')return [{title:'商机 / 客户',key:'opportunity',width:280,render:(_,row)=>opportunity(row)},owner,amount,{title:'阶段',key:'stage',width:120,render:(_,row)=>stageLabel(row) || '未记录'},time('创建时间','occurred_at')];
  if(activity==='lost_opportunities')return [{title:'商机 / 客户',key:'opportunity',width:280,render:(_,row)=>opportunity(row)},amount,field('丢单原因','lost_reason',270),owner,time('丢单时间','occurred_at')];
  if(activity==='updated_opportunities')return [{title:'商机 / 客户',key:'opportunity',width:300,render:(_,row)=>opportunity(row)},owner,{title:'变动',key:'changes',width:220,render:(_,row)=>`${row.event_count || row.events?.length || 0} 次变更，展开查看前后值`},time('最近变更','occurred_at')];
  if(activity==='completed_tasks')return [task,{title:'派发人',dataIndex:'creator_name',width:110,render:text},time('截止时间','due_at'),{title:'状态',dataIndex:'status_label',width:100,render:text},field('完成说明','completion_note',280),time('完成时间','completed_at')];
  return [task,owner,time('截止时间','due_at'),{title:'状态',dataIndex:'status_label',width:110,render:text},{title:'逾期',key:'overdue',width:110,render:(_,row)=>row.overdue?<Tag color="red">已逾期</Tag>:<Tag>未逾期</Tag>}];
}

function ChangeDetails({row,members}) {
  const items=changeRows(row.events,members);
  return <Table size="small" className="rv-change-detail" rowKey="key" pagination={false} dataSource={items} locale={{emptyText:'历史记录未提供可核实的字段差异，暂不能还原'}} columns={[{title:'变更时间',dataIndex:'at',width:140,render:timestamp},{title:'修改人',dataIndex:'actor',width:130},{title:'变更字段',key:'label',width:170,render:(_,change)=>changeLabel(change)},{title:'变更前',key:'before',render:(_,change)=>changeValue(change,'before')},{title:'变更后',key:'after',render:(_,change)=>changeValue(change,'after')}]}/>;
}

function PageReadout({value,position,setPosition,pageSize,setPageSize}) {
  return <div className="rv-page-footer"><span className="rv-subtle">明细读取于 {timestamp(value.data_as_of)}{value.coverage!=='complete'?' · 已知明细':''}</span><Pagination current={position+1} pageSize={pageSize} total={value.total??value.known_total} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} showTotal={total=>`${value.total==null?'已知':'共'} ${total} 条`} onChange={(page,size)=>{setPageSize(size);setPosition(size!==pageSize?0:page-1);}}/></div>;
}
function PageMessage({result}) {
  return result.error?<Alert type="error" title={result.error} action={<Button onClick={result.reload}>重新读取</Button>}/>:result.value?.coverage!=='complete' && result.value?<Alert type="warning" title={coverageText(result.value) || '部分数据尚不可用'}/>:null;
}
function ReviewActivityTable({value,selection,activity,service,refresh}) {
  const [pageSize,setPageSize]=useListPageSize('review-actions');
  const [position,setPosition]=useState(0),query=useMemo(()=>({activity,limit:pageSize,offset:position*pageSize}),[activity,pageSize,position]);
  const result=useAsync(()=>service.activities(selection,query),[detailSelectionKey(selection),query,service,refresh]);
  return <div data-testid="review-activity-table">
    {activity==='pending_tasks' && <p className="rv-subtle rv-table-note">截至当前的待完成任务，包含待验收及未来到期；不是所选历史日期的待办快照。</p>}
    {activity==='formal_visits' && <p className="rv-subtle rv-table-note">按实际跟进日期统计正式记录，包含录入及参与的跟进；团队内同一记录只计一次。</p>}
    <PageMessage result={result}/>
    <Table rowKey="id" size="middle" pagination={false} loading={result.loading} dataSource={result.value?.items || []} columns={columnsFor(activity,value.members)} scroll={{x:'max-content'}}
      expandable={activity==='updated_opportunities'?{expandedRowRender:row=><ChangeDetails row={row} members={value.members}/>,rowExpandable:row=>Boolean(row.events?.length)}:undefined}
      locale={{emptyText:result.error?'明细读取失败':result.value?.coverage==='unavailable'?'明细暂不可用':'本期暂无记录'}}/>
    {result.value && <PageReadout {...{value:result.value,position,setPosition,pageSize,setPageSize}}/>}
  </div>;
}

export function ReviewOpportunityTable({selection,service,filter='all',queryText='',sort='last_follow_up_desc',metric,refresh=0}) {
  const [pageSize,setPageSize]=useListPageSize(metric?'review-metric-detail':'review-opportunities');
  const [position,setPosition]=useState(0);
  const query=useMemo(()=>({filter,q:queryText,sort,metric_code:metric,limit:pageSize,offset:position*pageSize}),[filter,queryText,sort,metric,pageSize,position]);
  const result=useAsync(()=>service.opportunities(selection,query),[detailSelectionKey(selection),query,service,refresh]);
  const columns=[{title:'商机 / 客户',key:'name',width:280,render:(_,row)=>opportunity(row)},{title:'负责人',dataIndex:'owner_name',width:110,render:text},{title:'阶段',key:'stage',width:125,render:(_,row)=>stageLabel(row) || '未记录'},
    ...(metric?[{title:'纳入金额（万元）',key:'contribution',width:180,render:(_,row)=>{const amount=row.metric_contributions?.[metric];return <span title={amount?.reason || exactMoney(amount?.value)}>{money(amount?.value)}</span>;}}]:[{title:'预计回款（万元）',dataIndex:'collection_amount',width:160,render:moneyCell},{title:'预计确收（含税·万元）',dataIndex:'recognized_amount',width:180,render:moneyCell}]),
    {title:'最近正式跟进',dataIndex:'last_follow_up_at',width:145,render:timestamp},{title:'操作',key:'actions',width:220,render:(_,row)=><div className="rv-row-actions">{row.actions?.can_view && link('查看',()=>open.opportunity(row.customer_id,row.id))}{row.actions?.can_follow_up && link('补跟进',()=>open.route(`pages/visit-entry/index?customer_id=${encodeURIComponent(row.customer_id)}&opportunity_id=${encodeURIComponent(row.id)}`))}{row.actions?.can_create_task && link('建任务',()=>open.presales(row.customer_id,row.id))}</div>}];
  return <div data-testid={metric?'review-metric-detail':'review-opportunity-table'}><PageMessage result={result}/><Table rowKey="id" size="middle" columns={columns} dataSource={result.value?.items || []} loading={result.loading} pagination={false} scroll={{x:'max-content'}} locale={{emptyText:result.error?'商机读取失败':result.value?.available===false?'此筛选暂不可用':'没有符合条件的商机'}}/>
    {result.value && <PageReadout {...{value:result.value,position,setPosition,pageSize,setPageSize}}/>}
  </div>;
}

function LoadedReview({value,selection:requestedSelection,service:sourceService,refresh,onMember,memberOptions,actions,onTargets,onReload,sidePanel}) {
  const [scopeError,setScopeError]=useState('');
  const selection=useMemo(()=>bindReviewScope(requestedSelection,value),[requestedSelection,value]);
  const service=useMemo(()=>Object.fromEntries(['activities','opportunities'].map(method=>[method,async(...args)=>{
    try{return await sourceService[method](...args);}catch(error){
      if(error.statusCode===403 || [error.code,error.serverCode,error.error_code].includes('WORK_REPORT_SCOPE_CHANGED'))setScopeError('所选范围或权限已变化，请重新读取概览后继续。');
      throw error;
    }
  }])),[sourceService]);
  const [activity,setActivity]=useState('formal_visits'),[filter,setFilter]=useState('all'),[search,setSearch]=useState(''),[query,setQuery]=useState(''),[sort,setSort]=useState('last_follow_up_desc'),[drill,setDrill]=useState(null),[presenting,setPresenting]=useState(false);
  const title=`${value.period.label} · ${value.scope.label}`;
  if(scopeError)return <Alert type="warning" title={scopeError} action={<Button onClick={onReload}>重新读取概览</Button>}/>;
  return <div className="rv-overview" data-testid="review-overview">
    <header className="rv-summary-head"><div><h2>{title}</h2><span className="rv-subtle">季度预测为当前授权数据 · 读取于 {timestamp(value.data_as_of)}</span></div><div className="rv-row-actions">{onTargets && <Button onClick={onTargets}>目标管理</Button>}<Button onClick={()=>setPresenting(true)}>会议模式</Button>{actions}</div></header>
    <ReviewMetricCards value={value} onDrill={setDrill}/>
    <div className={`rv-progress-layout${sidePanel?' has-summary':''}`}><Panel title={selection.scope==='team'?'团队本期进展':'本期进展'} className="rv-activities-panel" extra={selection.scope==='team' && value.members.some(row=>memberOptions?.includes(row.id)) && onMember?<Select aria-label="展开团队成员" placeholder="按成员展开" value={null} showSearch optionFilterProp="label" options={value.members.filter(row=>memberOptions?.includes(row.id)).map(row=>({value:row.id,label:row.name || '成员名称未记录'}))} onChange={onMember} style={{minWidth:170}}/>:null}>
      <ReviewActionTiles value={value} selected={activity} onChange={setActivity}/>
      <ReviewActivityTable key={`${selectionKey(selection)}:${activity}`} {...{value,selection,activity,service,refresh}}/>
    </Panel>{typeof sidePanel==='function'?sidePanel(selection,value,onReload):sidePanel}</div>
    <Panel title={selection.scope==='team'?'团队商机池':selection.scope==='member'?'所选成员商机池':'我的商机池'} className="rv-opportunity-panel" extra={<Input.Search aria-label="搜索活跃商机" placeholder="搜索商机、客户或负责人" value={search} allowClear onChange={event=>{setSearch(event.target.value);if(!event.target.value)setQuery('');}} onSearch={setQuery} style={{width:260}}/>}>
      <div className="rv-pool-toolbar"><nav aria-label="商机池筛选">{REVIEW_POOL_FILTERS.map(item=>{const availability=value.filter_availability?.[item.value],unavailable=availability?.available===false;return <Tooltip key={item.value} title={reasonText(availability?.reason)}><button type="button" aria-pressed={filter===item.value} className={filter===item.value?'is-selected':''} disabled={unavailable} onClick={()=>setFilter(item.value)}>{item.label} <b>{value.pool_counts?.[item.value]??(availability?.coverage==='partial'?`已知 ${availability.known_count}`:'—')}</b></button></Tooltip>;})}</nav><Select aria-label="商机排序" value={sort} onChange={setSort} options={[{value:'last_follow_up_desc',label:'最近跟进优先'},{value:'last_follow_up_asc',label:'最久未跟进优先'},{value:'updated_desc',label:'最近更新优先'}]}/></div>
      {filter==='key_collection' && <p className="rv-subtle rv-table-note">本季度高信心、正数回款预测，包含进行中及已赢单商机；不代表实际到账。</p>}
      {filter==='stale' && <p className="rv-subtle rv-table-note">距最近正式跟进满 7 个自然日；从未跟进按可信创建日期起算。</p>}
      <ReviewOpportunityTable key={`${selectionKey(selection)}:${filter}:${query}:${sort}`} {...{selection,service,filter,sort,refresh}} queryText={query}/>
    </Panel>
    <details className="rv-secondary-metrics"><summary>更多指标 · 预测确收</summary><ReviewMetricCards value={value} onDrill={setDrill} definitions={REVIEW_SECONDARY_METRICS}/></details>
    <Drawer title={`${REVIEW_METRICS.find(row=>row.code===drill)?.label || ''} · Q${value.quarter.quarter} 明细`} open={Boolean(drill)} onClose={()=>setDrill(null)} size="large" width="min(1180px,96vw)" destroyOnHidden>
      {drill && <><p className="rv-subtle">{REVIEW_METRICS.find(row=>row.code===drill).definition} 明细为打开时重新读取。</p><ReviewOpportunityTable key={drill} selection={selection} service={service} metric={drill}/></>}
    </Drawer>
    <ReviewMeetingDeck open={presenting} onClose={()=>setPresenting(false)} value={value} selection={selection} service={service} metricsContent={<ReviewMetricCards value={value}/>}/>
  </div>;
}

export default function ReviewOverview({selection,refresh=0,onMember,memberOptions,onTargets,actions,sidePanel,service=reviewOverviewApi,overviewControlled=false,overviewLoading=false,overviewError=null,overviewValue=null,onOverviewReload}) {
  const owner=identity();
  const result=useAsync(()=>overviewControlled?Promise.resolve(null):service.overview(selection),[owner,service,selectionKey(selection),refresh,overviewControlled,overviewValue]);
  const state=overviewControlled?{loading:overviewLoading,error:overviewError,value:overviewValue,reload:onOverviewReload || result.reload}:result;
  if(state.loading)return <div className="rv-loading"><Spin/><span>正在读取本期业务概览</span></div>;
  if(state.error)return <Alert type="error" title={state.error} action={<Button onClick={state.reload}>重新读取概览</Button>}/>;
  return state.value?<LoadedReview key={`${owner}:${selectionKey(selection)}:${state.value.data_as_of}`} {...{value:state.value,selection,service,refresh,onMember,memberOptions,actions,onTargets,sidePanel}} onReload={state.reload}/>:<Empty description="概览暂不可用"/>;
}
