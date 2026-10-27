import React,{useCallback,useEffect,useRef,useState} from 'react';
import {Alert,Button,Empty,Spin,Tag} from 'antd';
import ReportPresentation from '../work-report/ReportPresentation.jsx';
import {identity,INTERACTION_MODE,stageLabel} from '../pc/data.js';
import {reviewOverviewApi} from './overview-api.js';
import {taskDetailFields,taskProcessingLabel} from './task-details.js';
import {REVIEW_PRIMARY_METRICS,REVIEW_SECONDARY_METRICS,REVIEW_ACTIVITIES,matchingPage,money,percentage,timestamp,metricState,changeRows,changeValue,changeLabel,coverageText,reasonText,dailyChangeView} from './overview-model.js';

const PAGE_ROWS=5,READ_LIMIT=100;
const copy=value=>JSON.parse(JSON.stringify(value));
const text=value=>value==null || value===''?'未记录':typeof value==='object'?JSON.stringify(value):String(value);
const amount=(value,currency='CNY')=>value==null?'未记录':`${money(value)} 万${currency==='CNY'?'元':` ${currency}`}`;
const names=(row,members)=>row.owner_name || (row.owners || []).map(item=>item.name).filter(Boolean).join('、') || (row.member_ids || [row.owner_id]).map(id=>members.find(item=>item.id===id)?.name).filter(Boolean).join('、') || '未记录';
const definitions=[...REVIEW_ACTIVITIES.map(item=>({...item,key:item.code,kind:'activities',query:{activity:item.code},group:item.code==='pending_tasks'?'当前任务存量':'本期动作明细'})),
  {key:'opportunity_pool',label:'当前商机池',kind:'opportunities',query:{filter:'all',sort:'last_follow_up_desc'},group:'当前商机与季度回款'},
  {key:'key_collection',label:'季度重点回款',kind:'opportunities',query:{filter:'key_collection',sort:'last_follow_up_desc'},group:'当前商机与季度回款'}];

const styles=`.rv-meeting-deck{display:flex;flex-direction:column;gap:16px;max-width:1480px;margin:auto;font-size:16px;line-height:1.65}.rv-meeting-note{margin:0;color:var(--ui-secondary,#667085);font-size:14px}.rv-meeting-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:16px}.rv-meeting-card,.rv-meeting-row{background:var(--ui-surface,#fff);border:1px solid var(--ui-line,#e5e7eb);border-radius:10px;padding:18px 22px;min-width:0}.rv-meeting-card h3{font-size:17px;margin:0 0 12px}.rv-meeting-card strong{display:block;font-size:34px;color:var(--ui-primary,#3370ff);font-variant-numeric:tabular-nums;overflow-wrap:anywhere}.rv-meeting-card strong small{font-size:15px;font-weight:400;margin-left:6px}.rv-meeting-card dl{display:flex;flex-wrap:wrap;gap:16px;margin:16px 0 0}.rv-meeting-card dt{font-size:13px;color:var(--ui-secondary,#667085)}.rv-meeting-card dd{margin:0;font-weight:600}.rv-meeting-card p{margin:12px 0 0;font-size:13px;color:var(--ui-secondary,#667085)}.rv-meeting-records{display:flex;flex-direction:column;gap:10px;margin:0;padding:0;list-style:none}.rv-meeting-row header{display:flex;justify-content:space-between;gap:16px;align-items:baseline}.rv-meeting-row h3{font-size:18px;margin:0;overflow-wrap:anywhere}.rv-meeting-row header>span{flex:none;font-size:13px;color:var(--ui-muted,#737b89)}.rv-meeting-row .rv-meeting-meta{font-size:13px;color:var(--ui-secondary,#667085);margin:4px 0 8px}.rv-meeting-row dl{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 22px;margin:0}.rv-meeting-row dl>div{display:flex;gap:10px;min-width:0}.rv-meeting-row dt{color:var(--ui-secondary,#667085);flex:none;font-size:14px}.rv-meeting-row dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;font-size:15px}.rv-meeting-row .rv-meeting-wide{grid-column:1/-1}.rv-meeting-reading{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;border-bottom:1px solid var(--ui-line,#e5e7eb);padding-bottom:12px;font-size:14px}.rv-meeting-reading b{font-variant-numeric:tabular-nums}.rv-meeting-loading{min-height:240px;display:flex;align-items:center;justify-content:center;gap:12px}.rv-meeting-changes{display:flex;flex-direction:column;gap:4px;margin:0;padding-left:18px;font-size:14px}.rv-meeting-changes span{color:var(--ui-secondary,#667085)}@media(max-width:720px){.rv-meeting-row dl{grid-template-columns:minmax(0,1fr)}.rv-meeting-row header{align-items:flex-start}.rv-meeting-row,.rv-meeting-card{padding:14px 16px}.rv-meeting-card strong{font-size:28px}}`;
function Frame({children}){return <div className="rv-meeting-deck"><style>{styles}</style>{children}</div>;}

function MetricSummary({value,metrics=REVIEW_PRIMARY_METRICS}){
  return <div className="rv-meeting-grid">{metrics.map(definition=>{
    const row=value.metrics.find(item=>item.metric_code===definition.code),state=metricState(row),daily=dailyChangeView(row);
    return <section className="rv-meeting-card" key={definition.code} aria-label={definition.label}>
      <h3>Q{value.quarter.quarter} {definition.label}</h3><Tag color={state.color}>{state.label}</Tag>
      <p>{row.coverage==='partial'?'已知小计':'当前预测'}</p><strong>{money(row.coverage==='partial'?row.known_subtotal:row.value)}<small>万元</small></strong>
      <dl><div><dt>有效目标</dt><dd>{row.target.status==='effective'?amount(row.target.value):row.target.status==='permission_denied'?'不可查看':'未设置'}</dd></div><div><dt>预计达成率</dt><dd>{percentage(row.rate)}</dd></div><div><dt>距目标差额</dt><dd>{row.gap==null?'—':amount(row.gap)}</dd></div><div><dt>{daily.label}</dt><dd title={daily.title}>{daily.value}</dd></div></dl>
      <p data-testid="review-daily-baseline">{daily.note}</p>
      {row.target.coverage && row.target.coverage!=='complete' && <p>目标未齐 · 已知目标小计 {money(row.target.known_subtotal)} 万元，暂不计算达成率和差额。</p>}<p>{definition.definition}</p>{row.coverage!=='complete' && <p>{coverageText(row) || '数据不完整，不作为完整总量。'}</p>}
    </section>;
  })}</div>;
}
function ActionsSummary({value}){
  return <Frame><p className="rv-meeting-note">选定周期：{value.period.label}。前六项按本期发生记录统计；“当前待完成”为读取时的任务存量。</p>
    <div className="rv-meeting-grid">{REVIEW_ACTIVITIES.map(definition=>{
      const row=value.activities.find(item=>item.code===definition.code);
      return <section className="rv-meeting-card" key={definition.code} aria-label={definition.label}>
        <h3>{definition.label}</h3><strong>{row.count??'—'}<small>{definition.unit}</small></strong>
        {row.coverage!=='complete' && <p>{row.coverage==='unavailable'?'暂不可用':`已知 ${row.known_count} ${definition.unit}，完整数量暂不可确认`}</p>}
        {row.missing_reasons?.length>0 && <p>{coverageText(row)}</p>}
        <p>{definition.code==='pending_tasks'?'当前存量 · 含待验收及未来到期':definition.code==='formal_visits'?'含录入及参与 · 按实际跟进日期 · 团队内同条只计一次':'选定周期内的业务动作'}</p>
      </section>;
    })}</div><p className="rv-meeting-note">继续翻页查看各项明细；明细按章节首次打开时读取，并在本次会议内保留。</p>
  </Frame>;
}
function scopeNote(definition,value){
  if(definition.key==='completed_tasks')return '按本期正常完成时间统计，不含停止、历史取消、拒绝或结果待核对的任务。';
  if(definition.key==='pending_tasks')return '当前任务存量，包含待验收及未来到期；不是所选历史日期的待办快照。';
  if(definition.key==='key_collection')return `Q${value.quarter.quarter} 当前高信心、正数回款预测；包含进行中及已赢单商机，不代表实际到账。`;
  if(definition.key==='opportunity_pool')return '按当前授权范围读取的商机存量；不是所选历史日期的商机池快照。';
  if(definition.key==='formal_visits')return `选定周期 ${value.period.label} · 按实际跟进日期统计正式记录，含录入及参与的跟进，同一记录只计一次。`;
  return `选定周期 ${value.period.label} · ${value.scope.label} · 按本期实际发生的业务动作展示。`;
}
function Record({row,definition,members,index}){
  const code=definition.key,owner=names(row,members);
  let title=row.opportunity_name || row.name || '名称未记录',meta=[],fields=[];
  if(code==='new_claimed_customers'){
    title=row.customer_name || row.name || '客户名称未记录';meta=[`认领人：${owner}`,`认领于 ${timestamp(row.occurred_at)}`];fields=[['客户来源',text(row.source_code)]];
  }else if(code==='formal_visits'){
    title=row.customer_name || '客户名称未记录';meta=[INTERACTION_MODE[row.interaction_mode_code] || text(row.interaction_mode_code),`实际跟进 ${timestamp(row.interaction_at)}`,`录入 ${timestamp(row.recorded_at)}`];
    fields=[['关联商机',(row.opportunities || []).map(item=>item.name).filter(Boolean).join('、') || row.opportunity_name || '未关联商机',true],['跟进摘要',text(row.summary),true],['下一步',text(row.next_action),true]];
  }else if(code==='completed_tasks' || code==='pending_tasks'){
    meta=[`执行人：${owner}`,`截止 ${timestamp(row.due_at)}`,...new Set([text(row.status_label),code==='pending_tasks'?taskProcessingLabel(row):''].filter(Boolean))];
    fields=code==='completed_tasks'?[['派发人',text(row.creator_name)],['完成时间',timestamp(row.completed_at)]]:[['逾期情况',row.overdue===true?'已逾期':row.overdue===false?'未逾期':'未记录'],['更新时间',timestamp(row.updated_at)]];
    fields.push(...taskDetailFields(row,{completed:code==='completed_tasks'}).map(([label,value])=>[label,value,true]));
  }else{
    meta=[row.customer_name || '客户名称未记录',`负责人：${owner}`,stageLabel(row) || '阶段未记录'];
    if(code==='opportunity_pool' || code==='key_collection')fields=[['预计回款',amount(row.collection_amount)],['预计确收（含税）',amount(row.recognized_amount)],['最近正式跟进',timestamp(row.last_follow_up_at)]];
    else if(code==='lost_opportunities')fields=[['原登记金额',amount(row.amount,row.currency)],['丢单时间',timestamp(row.occurred_at)],['丢单原因',text(row.lost_reason),true]];
    else if(code==='new_opportunities')fields=[['登记金额',amount(row.amount,row.currency)],['创建时间',timestamp(row.occurred_at)]];
    else fields=[['最近变更',timestamp(row.occurred_at)]];
  }
  const changes=code==='updated_opportunities'?changeRows(row.events,members):[];
  return <li className="rv-meeting-row" data-testid="review-meeting-record"><header><h3>{title}</h3><span>{index+1}</span></header><p className="rv-meeting-meta">{meta.join(' · ')}</p>
    <dl>{fields.map(([label,value,wide])=><div key={label} className={wide?'rv-meeting-wide':undefined}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    {code==='updated_opportunities' && (changes.length?<ul className="rv-meeting-changes">{changes.map(change=><li key={change.key}><span>{timestamp(change.at)} · {change.actor} · {changeLabel(change)}</span>：{changeValue(change,'before')} → {changeValue(change,'after')}</li>)}</ul>:<p className="rv-meeting-note">历史记录未提供可核实的字段差异，暂不能还原。</p>)}
  </li>;
}

function DetailSlide({definition,entry,read,reveal,page,value}){
  useEffect(()=>{if(!entry)read(definition);},[entry,read,definition]);
  useEffect(()=>{if(entry?.status==='ready')reveal(definition.key,Math.max(1,Math.ceil(entry.value.items.length/PAGE_ROWS)));},[entry,reveal,definition]);
  const result=entry?.value,start=page*PAGE_ROWS,items=result?.items.slice(start,start+PAGE_ROWS) || [];
  const unavailable=result?.coverage==='unavailable' || result?.available===false;
  const quantity=result?.total!=null?`总量 ${result.total} 条`:result?.coverage==='partial'?`已知量 ${result.known_total} 条 · 完整总量未知`:'总量暂不可确认';
  return <Frame><p className="rv-meeting-note">{scopeNote(definition,value)}</p>
    {!entry || entry.status==='loading'?<div className="rv-meeting-loading" role="status"><Spin/><span>首次读取本章节，完成后保留本次结果…</span></div>:entry.status==='error'?<Alert type="error" showIcon message="本章节读取失败" description={entry.error} action={<Button onClick={()=>read(definition)}>重试本章节</Button>}/>:<>
      <div className="rv-meeting-reading"><b>{unavailable?'本章节暂不可用':`已展示 ${items.length?`${start+1}–${start+items.length}`:'0'} / 本次读取 ${result.items.length} 条 · ${quantity}`}</b><span>读取于 {timestamp(result.data_as_of)} · 本次会议内不自动刷新</span></div>
      {result.coverage!=='complete' && <Alert type="warning" showIcon message={unavailable?'数据暂不可用，不视为没有记录':'仅展示已知明细，不代表完整范围'} description={coverageText(result) || (typeof result.reason==='string'?reasonText(result.reason):undefined)}/>}
      {!unavailable && (result.has_more || result.known_total>result.items.length || (result.total!=null && result.total>result.items.length)) && <Alert type="info" showIcon message={`本次会议最多读取前 ${READ_LIMIT} 条，本章节已读取 ${result.items.length} 条。其余明细请退出会议后在概览中查看。`}/>}
      {items.length?<ol className="rv-meeting-records">{items.map((row,index)=><Record key={`${row.id}:${start+index}`} row={row} definition={definition} members={value.members || []} index={start+index}/>)}</ol>:<Empty description={unavailable?'当前无法读取本章节':result.coverage==='partial'?'暂无已知明细，完整数量尚不能确认':'本章节没有符合条件的记录'}/>}
    </>}
  </Frame>;
}

function MeetingSession({session,onClose}){
  const {value,selection,service,owner,metricsContent}=session;
  const [cache,setCache]=useState({}),[pageCounts,setPageCounts]=useState({}),records=useRef({}),active=useRef(true);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  const close=()=>{active.current=false;onClose();};
  // Expand only the chapter currently mounted by the shell. A slow, earlier
  // chapter finishing in the background must never shift the selected index.
  const reveal=useCallback((key,pages)=>{if(active.current)setPageCounts(current=>current[key]===pages?current:{...current,[key]:pages});},[]);
  const read=useCallback(async definition=>{
    if(!active.current || identity()!==owner || ['ready','loading'].includes(records.current[definition.key]?.status))return;
    const update=entry=>{if(active.current && identity()===owner){records.current={...records.current,[definition.key]:entry};setCache(records.current);}};
    update({status:'loading'});
    try{
      const query={...definition.query,limit:READ_LIMIT,offset:0};
      const result=matchingPage(await service[definition.kind](selection,query),selection,query);
      update({status:'ready',value:copy(result)});
    }catch(error){update({status:'error',error:error.message || '请稍后重试'});}
  },[owner,selection,service]);
  const slides=[
    {key:'metrics',title:'季度目标与当前预测',group:'业务概览',content:<Frame><p className="rv-meeting-note">选定周期：{value.period.label} · 当前 Q{value.quarter.quarter} 预测，非历史期末快照。概览读取于 {timestamp(value.data_as_of)}。</p>{metricsContent || <MetricSummary value={value}/>}<p className="rv-meeting-note">概览保留进入会议时的版本；明细按章节首次打开时读取，不保证各章节处于同一取数时点。</p></Frame>},
    {key:'actions',title:'本期行动汇总',group:'业务概览',content:<ActionsSummary value={value}/>},
  ];
  for(const definition of definitions){
    const entry=cache[definition.key],pages=pageCounts[definition.key] || 1;
    for(let page=0;page<pages;page++)slides.push({key:`${definition.key}:${page}`,title:`${definition.label}${pages>1?` · ${page+1}/${pages}`:''}`,group:definition.group,content:<DetailSlide {...{definition,entry,read,reveal,page,value}}/>});
  }
  slides.push({key:'more-metrics',title:'更多指标 · 预测确收',group:'补充指标',content:<Frame><MetricSummary value={value} metrics={REVIEW_SECONDARY_METRICS}/></Frame>});
  return <ReportPresentation open title="业务 Review · 会议模式" subtitle={`${value.period.label} · ${value.scope.label} · 仅供会议阅读`} slides={slides} onClose={close}/>;
}

// Every open captures its own overview/selection and lazily read chapter cache.
// This component deliberately has no business mutation or background refresh.
export default function ReviewMeetingDeck({open,onClose,value,selection,service=reviewOverviewApi,metricsContent}){
  const [session,setSession]=useState(null),wasOpen=useRef(false),serial=useRef(0),owner=identity();
  useEffect(()=>{
    if(open && !wasOpen.current && value && selection)setSession({id:++serial.current,owner,value:copy(value),selection:copy(selection),service,metricsContent});
    if(!open)setSession(null);
    wasOpen.current=open;
  },[open,value,selection,service,metricsContent,owner]);
  useEffect(()=>{if(open && session && session.owner!==owner)onClose();},[open,session,owner,onClose]);
  return open && session && session.owner===owner?<MeetingSession key={session.id} session={session} onClose={onClose}/>:null;
}
