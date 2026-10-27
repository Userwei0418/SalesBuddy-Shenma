import React,{useState} from 'react';
import {Button,Table,Tooltip} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';
import {identity} from './pc/data.js';
import {Panel,useAsync} from './pc/ui.jsx';
import {timestamp} from './review/overview-model.js';
import {PERFORMANCE_METRICS,performanceRankingQuery,performanceRankingKey,performanceRankingApi,performanceAmount,performanceTarget,performanceRate,performanceRowNote,highlightedRankingRow} from './dashboard-performance-ranking.js';
import './dashboard-performance-ranking.css';

const PAGE_SIZE=7;

export function PerformanceMetricChoices({selected,onChange}) {
  return <div className="ds-performance-metric-choices" role="group" aria-label="业绩排名指标，部门和个人同步切换">
    {PERFORMANCE_METRICS.map(item=><Button key={item.code} type={item.code===selected?'primary':'default'} aria-pressed={item.code===selected} onClick={()=>onChange(item.code)}>{item.label}</Button>)}
  </div>;
}

function RankingTable({kind,value,data}) {
  const department=kind==='departments',rows=value[kind];
  const amount=(_,row)=>{
    const display=performanceAmount(row),note=performanceRowNote(row);
    return <div><span className="ds-performance-amount" title={display.exact}>{display.text}</span>{note&&<small>{note}</small>}</div>;
  };
  const columns=[
    {title:'排名',key:'rank',width:76,render:(_,row)=>row.rank==null?'—':<span className={row.rank<=3?'ds-performance-leading':''}>{row.provisional?'暂列 ':''}{row.rank}</span>},
    {title:department?'部门':'姓名',dataIndex:'name',width:140,render:(name,row)=><div><b>{name}</b>{!department&&row.team_name&&<small>{row.team_name}</small>}</div>},
    {title:'金额（万元）',key:'amount',render:amount},
    ...(department?[
      {title:'有效目标（万元）',key:'target',width:135,render:(_,row)=>performanceTarget(row.target)},
      {title:'达成率',key:'rate',width:115,render:(_,row)=>performanceRate(row)},
    ]:[]),
  ];
  const sortNote=department?(value.department_sort_basis==='rate'?'按达成率排名；无可比目标的部门列在末尾':'无可比目标，按金额排名'):'销售及销售主管 · 按金额排名';
  const coverage=value[department?'departments_coverage':'people_coverage'];
  const incomplete=coverage!=='complete'||rows.some(row=>row.provisional);
  const excluded=value[department?'department_excluded_fact_count':'people_excluded_fact_count'];
  return <Panel title={department?'部门业绩排名':'个人业绩排名'} note={sortNote} className="ds-performance-ranking-panel">
    {(incomplete||excluded>0||value.attribution_unknown_period_count>0)&&<div className="ds-performance-ranking-note" role="status">
      {incomplete&&<p>部分数据待补充或核对，标记“暂列”的名次仅依据当前已知数据。</p>}
      {value.attribution_unknown_period_count>0&&<p>{value.attribution_unknown_period_count} 项季度实绩归属待核对，未分配到本榜。</p>}
      {excluded>0&&<p>{excluded} 条可见记录未匹配当前榜单名单，未纳入。</p>}
    </div>}
    {rows.length?<Table rowKey="id" size="small" className="ds-performance-ranking-table" columns={columns} dataSource={rows}
      rowClassName={row=>highlightedRankingRow(row,kind,data,value.own_user_id)?'ds-performance-highlight':''}
      pagination={{pageSize:PAGE_SIZE,showSizeChanger:false,hideOnSinglePage:true,showTotal:total=>`可见 ${total} ${department?'个部门':'人'}`}}
      scroll={{x:department?640:420}}/>:<SbStatePanel state="empty" title={department?'当前授权范围暂无部门排名':'当前授权范围暂无个人排名'}/>}
  </Panel>;
}

export function PerformanceRankingTables({result,data={}}) {
  if(result.loading)return <SbStatePanel state="loading" title="正在读取业绩排名…"/>;
  if(result.error)return <SbStatePanel state="error" title={result.error} onRetry={result.reload}/>;
  if(!result.value)return <SbStatePanel state="empty" title="业绩排名暂不可用"/>;
  return <div className="ds-performance-ranking-grid">
    <RankingTable kind="departments" value={result.value} data={data}/>
    <RankingTable kind="people" value={result.value} data={data}/>
  </div>;
}

export default function DashboardPerformanceRanking({data,period,onScopeRetry,service=performanceRankingApi}) {
  const owner=identity(),[saved,setSaved]=useState(null);
  const selected=saved&&saved.owner===owner?saved.metric:PERFORMANCE_METRICS[0].code;
  const metric=PERFORMANCE_METRICS.find(row=>row.code===selected)||PERFORMANCE_METRICS[0];
  const query=performanceRankingQuery(period,metric.code),key=performanceRankingKey(query);
  const ready=!data.optionsLoading&&!data.optionsError;
  const result=useAsync(()=>ready?service.rankings(query):Promise.resolve(null),[key,service,ready]);
  const state=data.optionsError?{error:'排名可见范围读取失败，请重试',reload:onScopeRetry}:!ready?{loading:true}:result;
  const value=ready&&!result.loading&&!result.error?result.value:null;
  return <section className="ds-performance-ranking" aria-label="季度业绩排名">
    <header className="ds-dashboard-monitor-head">
      <div><h2>业绩排名</h2><span className="ds-muted">{period.year} Q{period.quarter} · 当前授权范围{value?` · 读取于 ${timestamp(value.data_as_of)}`:''}</span></div>
      <div className="ds-dashboard-monitor-controls">
        {value&&<Tooltip trigger={['hover','focus']} styles={{root:{maxWidth:400}}} title={<div>{value.definitions.map((text,index)=><p key={index} className="ds-performance-caliber">{text}</p>)}</div>}><Button type="text" aria-label="查看业绩排名统计口径">ⓘ 口径</Button></Tooltip>}
        <Button disabled={!ready||result.loading} onClick={result.reload}>刷新排名</Button>
      </div>
    </header>
    <PerformanceMetricChoices selected={metric.code} onChange={code=>setSaved({owner,metric:code})}/>
    {metric.reference&&<p className="ds-muted ds-performance-reference">物料计算参考 · 阶段 ≥{metric.code==='forecast_recognized_exclusive'?'70':'30'}% · 按冻结税率及交付／服务周期计算季度不含税金额</p>}
    <PerformanceRankingTables key={`${owner}:${key}`} result={state} data={data}/>
  </section>;
}
