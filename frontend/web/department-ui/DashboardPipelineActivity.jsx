import React,{useState} from 'react';
import {Button,Pagination,Table} from 'antd';
import {SbChartCard,SbSegmented,SbSelect,SbStatePanel} from '@shandiant/ui-react';
import {identity,open} from './pc/data.js';
import {useAsync} from './pc/ui.jsx';
import {timestamp,money,exactMoney} from './review/overview-model.js';
import {DashboardAnalysisChart,funnelOption,stageBarsOption,activityDonutOption} from './DashboardAnalysisCharts.jsx';
import {PIPELINE_METRICS,analysisSelection,analysisKey,analysisWarnings,pipelineTable,activityPresentation,matchingActivitySnapshot,dashboardAnalysisApi} from './dashboard-pipeline-activity.js';
import './dashboard-pipeline-activity.css';

const ACTIVITY_PAGE_SIZE=7;

export function PipelinePanel({result,scopeLabel}) {
  const [choice,setChoice]=useState(null),value=result.value;
  const selected=choice&&choice.value===value?choice.stage:'';
  const rows=value?pipelineTable(value,selected):[];
  const state=result.loading?'loading':result.error?'error':'normal';
  const table=value?{columns:['阶段','商机数','预测回款（万元）','预测确收（含税，万元）','完整性说明'],rows:rows.map(row=>[row.label,row.count,
    row.amounts.forecast_collection.text,row.amounts.forecast_recognized.text,
    PIPELINE_METRICS.map(metric=>row.amounts[metric.code].note?`${metric.label}：${row.amounts[metric.code].note}`:'').filter(Boolean).join('；') || '完整'])}:undefined;
  const select=stage=>{if(value?.stages.some(row=>row.code===stage))setChoice({value,stage:selected===stage?'':stage});};
  return <SbChartCard title="季度 PPL 阶段分布" scope={scopeLabel} state={state} onRetry={result.reload}
    caliber="仅看当前在推或赢单且阶段 ≥30% 的商机子集，数量不以季度金额已填为前提。各层高度固定，仅宽度随相对数量变化；少量数据保留最小可见宽度，0 为空心斜线。各层基准不同，准确数量以标签为准，不表示转化率。预测回款只纳入高回款信心；预测含税确收只纳入阶段 ≥70%。金额取本季度填报，不按概率加权。上方预测回款卡还可能包含低于30%的高信心商机，金额不一定相同。"
    summary={value?`PPL ${value.eligible_count} 个，${value.excluded_count} 个不符合条件，${value.unknown_eligibility_count} 个资格待核对`:''} data={table}>
    {value && <>
      <div className="ds-analysis-stage-controls" role="group" aria-label="筛选阶段，仅影响本卡金额和数据表">
        <Button size="small" type={!selected?'primary':'default'} aria-pressed={!selected} onClick={()=>setChoice(null)}>全部阶段</Button>
        {value.stages.map(row=><Button key={row.code} size="small" type={selected===row.code?'primary':'default'} aria-pressed={selected===row.code} onClick={()=>select(row.code)}>{row.label} · {row.count}</Button>)}
        {selected && <Button type="link" size="small" onClick={()=>setChoice(null)}>清除筛选</Button>}
      </div>
      <p className="ds-muted ds-analysis-note">PPL {value.eligible_count} 个 · 未纳入 {value.excluded_count} 个{value.unknown_eligibility_count?` · 资格待补充 ${value.unknown_eligibility_count} 个`:''}。各层高度固定，宽度随数量变化，斜线空心表示 0；准确数量以文字为准，点击阶段只筛选本卡。</p>
      {analysisWarnings(value)&&<p role="status" className="ds-analysis-warning">{analysisWarnings(value)}</p>}
      <div className="ds-analysis-pipeline-charts">
        <div><h3>阶段分布</h3>{value.stages.length>0?<DashboardAnalysisChart option={funnelOption(value.stages,selected)} height={Math.max(280,value.stages.length*54)} label="PPL阶段数量图，高度固定，仅宽度随相对数量变化；斜线空心表示0，文字为准确数量，可用上方阶段按钮筛选" onSelect={select}/>:<SbStatePanel state="empty" title="当前范围暂无可确认的 PPL 商机" description={value.unknown_eligibility_count?'请补充待核对商机的阶段后查看':'明确为零，未将其他阶段或丢单商机加入漏斗'}/>}</div>
        <div><h3>{selected?'所选阶段':'各阶段'}季度预测金额 <small>万元</small></h3><DashboardAnalysisChart option={stageBarsOption(rows)} height={Math.max(280,rows.length*54)} label="阶段预测回款与含税确收金额；缺失金额不作为零绘制"/>
          <p className="ds-muted ds-analysis-note">回款：信心高；含税确收：阶段 ≥70%。空白条请查看图表数据中的“未纳入／待补充／未填写”，部分数据只展示已知小计。</p>
        </div>
      </div>
    </>}
  </SbChartCard>;
}

const lastFollowup=value=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
export function ActivityRows({result,onRefresh,position,setPosition}) {
  const value=result.value;
  const amount=(value,row)=>row.amount_reason==='currency_unavailable'?<span title="币种缺失或非人民币">待核对</span>:value==null?'未填写':<span title={exactMoney(value)}>{money(value)}</span>;
  const columns=[
    {title:'商机 / 客户',key:'name',width:220,render:(_,row)=><div><b title={row.name}>{row.name}</b><small title={row.customer_name || undefined}>{row.customer_name || '客户名称未记录'}</small></div>},
    {title:'负责人 / 阶段',key:'owner',width:140,render:(_,row)=><div>{row.owner_name || '—'}<small>{row.probability==null?'阶段待核对':`${row.probability}%`}{row.status==='won'?' · 已赢单':row.status==='lost'?' · 已丢单':''}</small></div>},
    {title:'最近跟进',key:'followup',width:170,render:(_,row)=>row.follow_up_status==='never'?<span>尚无正式跟进</span>:<div>{lastFollowup(row.last_follow_up_at)}<small>{row.days_since_follow_up} 天未跟进</small></div>},
    {title:'本季已填回款计划（万元）',dataIndex:'collection_amount',width:155,render:amount},
    {title:'本季已填确收计划（含税，万元）',dataIndex:'recognized_amount',width:165,render:amount},
    {title:'操作',key:'action',width:96,render:(_,row)=>row.can_read===true?<Button type="link" size="small" onClick={()=>open.opportunity(row.customer_id,row.id)}>查看商机</Button>:null},
  ];
  return <div className="ds-analysis-table">
    {result.error&&<SbStatePanel state="error" title={result.error} onRetry={onRefresh}/>}
    {!result.error&&<Table rowKey="id" size="small" tableLayout="fixed" columns={columns} dataSource={value?.items || []} loading={result.loading} pagination={false} scroll={{x:920}} locale={{emptyText:result.loading?'正在读取…':'当前范围没有超期或尚未跟进的活跃商机'}}/>}
    {value&&!result.loading&&!result.error&&<div className="rv-page-footer"><span className="rv-subtle">{value.total==null?'已知可见':'共'} {value.known_total} 个 · 每页 7 个 · 读取于 {timestamp(value.data_as_of)}</span>
      <Pagination current={position+1} pageSize={ACTIVITY_PAGE_SIZE} total={value.known_total} showSizeChanger={false} onChange={page=>setPosition(page-1)}/>
    </div>}
  </div>;
}

function ActivityDetails({summary,selection,service,onRefresh}) {
  const [position,setPosition]=useState(0);
  const key=analysisKey(selection);
  const result=useAsync(async()=>matchingActivitySnapshot(await service.overdue(selection,{limit:ACTIVITY_PAGE_SIZE,offset:position*ACTIVITY_PAGE_SIZE}),summary),[key,service,position,summary]);
  return <ActivityRows result={result} onRefresh={onRefresh} position={position} setPosition={setPosition}/>;
}

export function ActivityPanel({result,selection,scopeLabel,service,onRefresh}) {
  const value=result.value,view=value?activityPresentation(value):null;
  const state=result.loading?'loading':result.error?'error':'normal';
  return <div className="ds-analysis-activity-grid">
    <SbChartCard title="季度活跃商机跟进占比" scope={scopeLabel} state={state} onRetry={onRefresh}
      caliber="活跃商机与商机经营页一致：本季度已填回款或含税确收计划任一大于零，不额外排除赢单或丢单。近7天按当前北京时间的7个自然日（含今天）统计正式跟进，不随所选历史季度回拨。权限或日期不完整时标为待核对。"
      summary={value?`${value.active_count} 个活跃商机，近7天已跟进 ${value.recent_count} 个，超7天或尚未跟进 ${value.overdue_count} 个，待核对 ${value.unknown_count} 个`:''}
      data={view?{columns:['跟进情况','商机数'],rows:view.parts.map(row=>[row.label,row.count])}:undefined}>
      {value&&<>
        <p className="ds-muted ds-analysis-note">本季已填回款或含税确收计划任一大于 0 · {view.windowLabel} · 读取于 {timestamp(value.data_as_of)}</p>
        {value.active_count>0?<div className="ds-analysis-donut"><DashboardAnalysisChart option={activityDonutOption(view.parts)} height={260} label="活跃商机近7天跟进占比"/>
          <div className="ds-analysis-donut-center"><b>{view.rate}</b><span>{view.rateAvailable?'近 7 天已跟进':'跟进比例待核对'}</span><span>已确认 {value.recent_count} / 可见 {value.active_count} 个</span></div>
        </div>:<SbStatePanel state="empty" title="本季度暂无活跃商机" description="没有符合季度计划金额口径的商机，比例不计算"/>}
        <dl className="ds-analysis-legend">{view.parts.map(row=><div key={row.label}><dt>{row.label}</dt><dd>{row.count} 个</dd></div>)}</dl>
        {value.never_count>0&&<p className="ds-muted ds-analysis-note">超期或未跟进中，{value.never_count} 个尚无正式跟进。</p>}
        {(analysisWarnings(value)||value.unknown_count>0)&&<p role="status" className="ds-analysis-warning">{analysisWarnings(value)}{value.unknown_count>0?` ${value.unknown_count} 个跟进情况待核对，未当作未跟进。`:''}</p>}
      </>}
    </SbChartCard>
    <SbChartCard title="超 7 天或尚未跟进的活跃商机" scope={scopeLabel} state={state} onRetry={onRefresh}
      caliber="只列出当前范围内可确认超7天或尚无正式跟进的季度活跃商机。与左侧占比使用相同的范围和窗口；表中金额是季度已填计划，不筛高回款信心或70%阶段。按最早跟进时间排序。">
      {value&&<><p className="ds-muted ds-analysis-note">共 {value.overdue_count} 个{value.never_count?`，含 ${value.never_count} 个尚无正式跟进`:''}；待核对记录不列入本表。</p><ActivityDetails key={`${analysisKey(selection)}:${value.data_as_of}`} summary={value} selection={selection} service={service} onRefresh={onRefresh}/></>}
    </SbChartCard>
  </div>;
}

export default function DashboardPipelineActivity({data,period,onScopeRetry,service=dashboardAnalysisApi}) {
  const owner=identity();
  const [saved,setSaved]=useState(null),[revision,setRevision]=useState(0);
  const choice=saved?.owner===owner?saved:null;
  const selection=analysisSelection(data,choice,period),key=analysisKey(selection);
  const ready=!data.optionsLoading&&!data.optionsError;
  const pipeline=useAsync(()=>ready?service.pipeline(selection):Promise.resolve(null),[key,service,ready,revision]);
  const activity=useAsync(()=>ready?service.activity(selection):Promise.resolve(null),[key,service,ready,revision]);
  const teams=data.teamOptions||[],members=data.memberOptions||[];
  const selectedTeam=teams.some(row=>row.id===choice?.teamId)?choice.teamId:'all';
  const scopeName=selection.personal?(selection.member_id?members.find(row=>row.id===selection.member_id)?.name || '所选成员':'本人'):(selectedTeam==='all'?'全部授权团队':teams.find(row=>row.id===selectedTeam)?.name || '所选团队');
  const scopeLabel=`${scopeName} · ${period.year} Q${period.quarter}`;
  const update=patch=>setSaved({...choice,...patch,owner});
  const refresh=()=>setRevision(value=>value+1);
  const state=result=>data.optionsError?{error:'图表可选范围读取失败',reload:onScopeRetry}:!ready?{loading:true}:result;
  return <section className="ds-dashboard-analysis" aria-label="季度商机阶段与跟进分析">
    <header className="ds-dashboard-monitor-head"><div><h2>季度商机与跟进</h2><span className="ds-muted">当前范围：{scopeLabel}；以下图表共用此范围，季度随上方监控</span></div>
      <div className="ds-dashboard-monitor-controls" role="group" aria-label="商机与跟进分析范围">
        {data.canViewTeam&&<SbSegmented aria-label="图表团队或个人" value={selection.personal?'personal':'team'} options={[{value:'team',label:'团队'},{value:'personal',label:'个人'}]} onChange={mode=>update({mode})}/>}
        {data.canViewTeam&&!selection.personal&&<SbSelect aria-label="图表团队" width={210} value={selectedTeam} options={teams.map(row=>({value:row.id,label:row.id==='all'?'全部授权团队':row.name}))} disabled={!ready} onChange={teamId=>update({teamId})}/>}
        {data.canViewTeam&&selection.personal&&<SbSelect aria-label="图表成员" width={210} showSearch optionFilterProp="label" value={selection.member_id || ''} options={[{value:'',label:'本人'},...members.map(row=>({value:row.id,label:row.name}))]} disabled={!ready} onChange={memberId=>update({memberId})}/>}
        {!data.canViewTeam&&<span className="ds-muted">仅本人</span>}
        <Button disabled={!ready||pipeline.loading||activity.loading} onClick={refresh}>刷新图表</Button>
      </div>
    </header>
    <PipelinePanel result={state(pipeline)} scopeLabel={scopeLabel}/>
    <ActivityPanel key={`${owner}:${key}:${revision}`} result={state(activity)} selection={selection} scopeLabel={scopeLabel} service={service} onRefresh={refresh}/>
  </section>;
}
