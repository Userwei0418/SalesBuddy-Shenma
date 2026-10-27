import React,{useEffect,useState} from 'react';
import {Button,Drawer,Pagination,Table,Tooltip} from 'antd';
import {SbSelect,SbStatePanel} from '@shandiant/ui-react';
import {identity} from './pc/data.js';
import {useAsync} from './pc/ui.jsx';
import {useListPageSize,PAGE_SIZE_OPTIONS} from './pc/list-pagination.js';
import {DashboardActualCards,dashboardActualColumns,actualCoverageText} from './DashboardActuals.jsx';
import DashboardPipelineActivity from './DashboardPipelineActivity.jsx';
import DashboardPerformanceRanking from './DashboardPerformanceRanking.jsx';
import {ReviewMetricCards} from './review/ReviewOverview.jsx';
import {money,exactMoney,timestamp,coverageText} from './review/overview-model.js';
import {DASHBOARD_METRICS,DASHBOARD_FORECAST_METRICS,isDashboardActual,currentMonitorQuarter,dashboardMonitorSelection,dashboardMonitorKey,dashboardMonitorApi,dashboardMetricReason} from './dashboard-monitor.js';

const targetSettings={
  forecast_collection:'配置目标 → 预测回款，单位万元。',
  collection_ppl:'配置目标 → 回款 PPL 储备，单位万元。',
  forecast_recognized:'配置目标 → 预测确收（含税），单位万元。',
};

function DashboardTargetHelp({definition,quarter}) {
  return <Tooltip trigger={['hover','focus']} styles={{root:{maxWidth:360}}} title={<div>
    <div>{quarter.year} Q{quarter.quarter} 当前范围读取已生效目标；“未设置”表示没有匹配的生效目标。</div>
    <div>管理后台 → 组织与目标 → 目标管理。</div>
    <div>{targetSettings[definition.code]}</div>
    <div>按同一范围、季度填写目标及依据，保存并生效后刷新。部门／团队／个人及年度／季度目标独立，待审批不计入；无维护权限请联系运营。</div>
  </div>}><button type="button" className="rv-help" style={{display:'inline-flex',alignItems:'center',justifyContent:'center',flex:'0 0 16px',width:16,minWidth:16,height:16,minHeight:16,margin:0,padding:0,lineHeight:1}} aria-label={`${definition.label}有效目标设置说明`}>ⓘ</button></Tooltip>;
}

export function DashboardMonitorCards({result,onDrill}) {
  if (result.loading) return <SbStatePanel state="loading" title="正在读取季度监控…"/>;
  if (result.error) return <SbStatePanel state="error" title={result.error} onRetry={result.reload}/>;
  if (!result.value) return <SbStatePanel state="empty" title="季度监控暂不可用"/>;
  return <><ReviewMetricCards value={result.value} definitions={DASHBOARD_FORECAST_METRICS} onDrill={onDrill} renderTargetHelp={definition=><DashboardTargetHelp definition={definition} quarter={result.value.quarter}/>}/><DashboardActualCards value={result.value} onDrill={onDrill}/></>;
}

function DashboardMetricDetails({selection,metric,service}) {
  const [pageSize,setPageSize]=useListPageSize('dashboard-metric-detail');
  const [position,setPosition]=useState(0),key=dashboardMonitorKey(selection);
  const actual=isDashboardActual(metric);
  const result=useAsync(()=>service[actual?'actuals':'opportunities'](selection,{metric_code:metric,limit:pageSize,offset:position*pageSize}),[key,metric,pageSize,position,service]);
  const value=result.value,definition=DASHBOARD_METRICS.find(row=>row.code===metric);
  const columns=actual?dashboardActualColumns(metric):[
    {title:'商机 / 客户',key:'name',width:270,render:(_,row)=><div><b>{row.name || '商机名称未记录'}</b><small>{row.customer_name || '客户名称未记录'}</small></div>},
    {title:'负责人',dataIndex:'owner_name',width:120,render:name=>name || '—'},
    {title:'阶段概率',dataIndex:'probability',width:110,render:value=>value==null?'未填写':`${value}%`},
    {title:'回款信心',dataIndex:'collection_confidence',width:110,render:value=>({high:'高',medium:'中',low:'低'})[value] || '未填写'},
    {title:'纳入金额（万元）',key:'amount',width:180,render:(_,row)=>{const amount=row.metric_contributions?.[metric];return <span title={amount?.reason?dashboardMetricReason(amount.reason):exactMoney(amount?.value)}>{money(amount?.value)}</span>;}},
  ];
  return <div className="ds-dashboard-detail">
    <p className="ds-muted">{definition.definition}</p>
    {actual && <p className="ds-muted">按已采用的财务事实展示，含客户级记录；记录可能为季度汇总，并非逐笔交易流水。</p>}
    {result.error && <SbStatePanel state="error" title={result.error} onRetry={result.reload}/>}
    {value && value.coverage!=='complete' && <p role="status" className="ds-muted">{(actual?actualCoverageText(value):coverageText(value)) || '部分数据尚不可用，仅展示已知明细'}</p>}
    <Table rowKey="id" size="middle" loading={result.loading} dataSource={value?.items || []} columns={columns} pagination={false} scroll={{x:800}}
      locale={{emptyText:result.error?'明细读取失败':value?.coverage==='unavailable'?'明细暂不可用':actual?'本季度尚无已登记的采用记录':'本季度没有符合口径的商机'}}/>
    {value && <div className="rv-page-footer"><span className="rv-subtle">明细读取于 {timestamp(value.data_as_of)}</span><Pagination current={position+1} pageSize={pageSize} total={value.total??value.known_total} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS} showTotal={total=>`${value.total==null?'已知':'共'} ${total} 条`} onChange={(page,size)=>{setPageSize(size);setPosition(size!==pageSize?0:page-1);}}/></div>}
  </div>;
}

export default function DashboardMonitor({data,onScopeRetry,service=dashboardMonitorApi}) {
  const owner=identity();
  const [period,setPeriod]=useState(()=>currentMonitorQuarter()),[drill,setDrill]=useState(null);
  const selection=dashboardMonitorSelection(data,period),key=dashboardMonitorKey(selection),context=`${owner}:${key}`;
  useEffect(()=>{setDrill(null);},[context]);
  const ready=!data.optionsLoading && !data.optionsError;
  const result=useAsync(()=>ready?service.metrics(selection):Promise.resolve(null),[key,service,ready]);
  const current=currentMonitorQuarter(),years=[...new Set([current.year-2,current.year-1,current.year,current.year+1,current.year+2,period.year,...(data.quarterYears || []).map(Number)])].filter(year=>Number.isInteger(year)&&year>=2000&&year<=2100).sort((a,b)=>b-a);
  const metric=drill?.context===context && !result.loading && !result.error && ready?drill.metric:null;
  const scopeLabel=selection.personal?data.memberLabel || '本人':data.teamLabel || '已授权团队';
  const state=data.optionsError?{error:'监控范围读取失败，请重新读取查看范围',reload:onScopeRetry}:!ready?{loading:true}:result;
  return <section className="ds-dashboard-monitor" aria-label="季度监控">
    <header className="ds-dashboard-monitor-head"><div><h2>季度监控</h2><span className="ds-muted">{scopeLabel}{result.value && ready?` · 读取于 ${timestamp(result.value.data_as_of)}`:''}</span></div>
      <div className="ds-dashboard-monitor-controls" role="group" aria-label="监控季度，独立于下方图表周期">
        <SbSelect aria-label="监控年份" width={116} value={period.year} options={years.map(year=>({value:year,label:`${year}年`}))} onChange={year=>{setDrill(null);setPeriod(previous=>({...previous,year}));}}/>
        <SbSelect aria-label="监控季度" width={100} value={period.quarter} options={[1,2,3,4].map(quarter=>({value:quarter,label:`Q${quarter}`}))} onChange={quarter=>{setDrill(null);setPeriod(previous=>({...previous,quarter}));}}/>
        <Button disabled={!ready || result.loading} onClick={()=>{setDrill(null);result.reload();}}>刷新监控</Button>
      </div>
    </header>
    <DashboardMonitorCards result={state} onDrill={metric=>setDrill({metric,context})}/>
    <DashboardPerformanceRanking data={data} period={period} onScopeRetry={onScopeRetry}/>
    <DashboardPipelineActivity data={data} period={period} onScopeRetry={onScopeRetry}/>
    <Drawer open={Boolean(metric)} title={`${period.year} Q${period.quarter} ${DASHBOARD_METRICS.find(row=>row.code===metric)?.label || ''} · 明细`} onClose={()=>setDrill(null)} size="large" width="min(1180px,96vw)" destroyOnHidden classNames={{root:'department-ui ds-dashboard-drawer'}}>
      {metric && <DashboardMetricDetails key={`${context}:${metric}`} selection={selection} metric={metric} service={service}/>}
    </Drawer>
  </section>;
}
