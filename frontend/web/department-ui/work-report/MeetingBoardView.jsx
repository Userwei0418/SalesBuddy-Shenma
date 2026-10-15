import React,{useId,useRef,useState} from 'react';
import {Alert,Button,Empty,Segmented,Table,Tag} from 'antd';
import {NavIcon} from '../pc/icons.jsx';
import {MEETING_MONEY,meetingAmount,changeTone,countText} from './meeting-display.js';
import ReportPresentation from './ReportPresentation.jsx';
import {rowPages} from './presentation-pages.js';
import TaskActivity,{TaskActivityTable} from './TaskActivity.jsx';
import './meeting-board.css';

const empty=text=><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={text}/>;
const person=row=>row.ownerLabel || '未登记负责人';
const money=(value,signed=false)=><span className={`wr-change-money ${signed?`is-${changeTone(value)}`:''}`}>{meetingAmount(value,{signed})}</span>;
const Missing=({reasons=[]})=>reasons.length?<details className="wr-change-notes"><summary>统计说明 · {reasons.length} 项</summary><ul>{reasons.map((reason,index)=><li key={index}>{reason}</li>)}</ul></details>:null;

export function MeetingAmounts({items=[],quarterLabel}) {
  return <section className="wr-change-amounts" aria-label="业绩期初、变化与当前值">
    {MEETING_MONEY.map(metric=>{
      const item=items.find(row=>row.id===metric.id) || {};
      return <article className="wr-change-amount" key={metric.id}>
        <header><h3>{metric.id==='acv'?'ACV':`${quarterLabel || '当季'} ${metric.label}`}</h3><span>万元</span></header>
        <div className="wr-change-current">{money(item.closing)}<span>期末／当前</span></div>
        <div className="wr-change-equation"><div><span>期初</span>{money(item.opening)}</div><i aria-hidden="true">→</i><div><span>本期变动</span>{money(item.change,true)}</div></div>
        {(item.opening==null || item.change==null || item.closing==null) && <p className="wr-change-incomplete">{item.opening==null && item.closing!=null?'历史基准未完整取得':'统计尚未完整取得'}</p>}
        <Missing reasons={item.reasons}/>
      </article>;
    })}
  </section>;
}

function MovementDetails({row}) {
  return <div className="wr-change-detail">
    {row.reason && <p><b>变动原因</b>{row.reason}</p>}
    <p><b>本期进展</b>{row.progress || '未登记本期新进展'}</p>
    <p><b>下一步</b>{row.nextStep || '尚未登记下一步计划'}</p>
    <Missing reasons={row.reasons}/>
    {row.history?.length>0 && <details><summary>查看本期变更过程</summary><ol>{row.history.map((item,index)=><li key={item.id || index}><span className="wr-muted">{item.atLabel} · {item.actorLabel}</span><p>{item.text}</p></li>)}</ol></details>}
  </div>;
}

function ChangesTable({rows=[],onOpen,metric='collection',onMetric,fixed=false}) {
  return <>
    <div className="wr-change-subtoolbar">{fixed?<b>{MEETING_MONEY.find(row=>row.id===metric)?.label}</b>:<Segmented aria-label="变动金额指标" value={metric} onChange={onMetric} options={MEETING_MONEY.map(row=>({value:row.id,label:row.label}))}/>}<span className="wr-muted">金额单位：万元</span></div>
    <Table rowKey="id" size="small" dataSource={rows} pagination={rows.length>10?{pageSize:10,showSizeChanger:false}:false} scroll={{x:780}}
      locale={{emptyText:empty('本期没有可展示的业绩变动')}} expandable={{expandedRowRender:row=><MovementDetails row={row}/>}}
      columns={[
        {title:'商机 / 客户',key:'name',width:260,render:(_,row)=><div className="wr-change-object">{onOpen && row.canOpen?<Button type="link" onClick={()=>onOpen(row)}>{row.name}</Button>:<b>{row.name}</b>}<span>{row.customerName} · {person(row)}</span></div>},
        {title:'期初',key:'opening',align:'right',width:130,render:(_,row)=>money(row.money?.[metric]?.opening)},
        {title:'本期变化',key:'change',align:'right',width:130,render:(_,row)=>money(row.money?.[metric]?.change,true)},
        {title:'期末／当前',key:'closing',align:'right',width:130,render:(_,row)=>money(row.money?.[metric]?.closing)},
        {title:'原因',key:'reason',width:180,render:(_,row)=><><Tag color={row.kind==='lost'?'red':undefined}>{row.kindLabel || '金额调整'}</Tag>{row.reason && <p className="wr-change-reason">{row.reason}</p>}</>},
      ]}/>
  </>;
}

function CustomersTable({rows=[],onOpen}) {
  return <Table rowKey="id" size="small" dataSource={rows} pagination={rows.length>10?{pageSize:10,showSizeChanger:false}:false} scroll={{x:620}}
    locale={{emptyText:empty('本期没有新认领的客户')}} columns={[
      {title:'新认领客户',key:'name',render:(_,row)=>onOpen && row.canOpen?<Button type="link" onClick={()=>onOpen(row)}>{row.name}</Button>:row.name},
      {title:'认领人',key:'owner',render:(_,row)=>person(row)},
      {title:'认领生效时间',dataIndex:'atLabel'},
    ]}/>;
}

function OpportunitiesTable({rows=[],onOpen,kind}) {
  const visible=kind?rows.filter(row=>row.kind===kind):rows;
  return <Table rowKey="id" size="small" dataSource={visible} pagination={visible.length>10?{pageSize:10,showSizeChanger:false}:false} scroll={{x:680}}
    expandable={{expandedRowRender:row=><MovementDetails row={row}/>}} locale={{emptyText:empty('本期没有对应的商机变动')}} columns={[
      {title:'商机 / 客户',key:'name',render:(_,row)=><div className="wr-change-object">{onOpen && row.canOpen?<Button type="link" onClick={()=>onOpen(row)}>{row.name}</Button>:<b>{row.name}</b>}<span>{row.customerName}</span></div>},
      {title:'变化',key:'kind',render:(_,row)=><Tag color={row.kind==='lost'?'red':'blue'}>{row.kindLabel}</Tag>},
      {title:'负责人',key:'owner',render:(_,row)=>person(row)},
      {title:'时间',dataIndex:'atLabel'},
    ]}/>;
}

function Followups({rows=[]}) {
  const [expanded,setExpanded]=useState(new Set());
  return <div className="wr-change-followups">{rows.length?rows.map(row=><article key={row.id}>
    <header><div><b>{row.customerName || row.name}</b>{row.opportunityName && <span> · {row.opportunityName}</span>}</div><span>{person(row)} · {row.atLabel}</span></header>
    <p className={!expanded.has(row.id) && (row.summary?.length>120 || row.summary?.includes('\n'))?'wr-change-summary':undefined}>{row.summary || '未登记跟进摘要'}</p>
    {(row.summary?.length>120 || row.summary?.includes('\n')) && <Button type="link" size="small" aria-expanded={expanded.has(row.id)} onClick={()=>setExpanded(previous=>{const next=new Set(previous);next.has(row.id)?next.delete(row.id):next.add(row.id);return next;})}>{expanded.has(row.id)?'收起记录':'展开完整记录'}</Button>}
    {row.nextStep && <p className="wr-change-next"><Tag color="blue">下一步</Tag>{row.nextStep}</p>}
  </article>):empty('本期没有实际跟进日期落在此范围内的正式记录')}</div>;
}

function MeetingCounts({items,onSelect}) {
  return <section className={`wr-change-counts${items.length>4?' has-task-counts':''}`} aria-label="本期活动数量">{items.map(item=>{
    const content=<><span>{item.label}{onSelect && <i aria-hidden="true">↗</i>}</span><strong>{countText(item.value)}<small>{item.unit}</small></strong>{item.note && <small>{item.note}</small>}</>;
    return onSelect?<button type="button" key={item.id} onClick={()=>onSelect(item.id)} aria-label={`查看${item.label}`}>{content}</button>:<div className="wr-change-count-item" key={item.id}>{content}</div>;
  })}</section>;
}

function Plans({rows=[]}) {
  return rows.length?<ol>{rows.map((row,index)=><li key={row.id || index}><span className="wr-change-plan-number">{String(index+1).padStart(2,'0')}</span><div><b>{row.title}</b><p>{row.text}</p><small>{[row.ownerLabel,row.dateLabel].filter(Boolean).join(' · ')}</small></div></li>)}</ol>:<p className="wr-muted">本期材料中尚未登记下一步计划</p>;
}

export function meetingSlides(value,{members=[],compact=false}={}) {
  const summary=model=><div className="wr-presentation-summary"><MeetingCounts items={model.counts}/><MeetingAmounts items={model.amounts} quarterLabel={model.quarterLabel}/>
    {model.notices?.length>0 && <Alert type="warning" showIcon title="部分数据需核对" description={<Missing reasons={model.notices}/>}/>}<Missing reasons={model.detailNotes}/></div>;
  const slides=[{key:'overview',title:'本期汇总',group:'本期汇总',content:summary(value)}];
  if(compact)return slides;
  const append=(rows,size,key,title,group,render)=>{
    const pages=rowPages(rows || [],size);
    pages.forEach((page,index)=>slides.push({key:`${key}:${index}`,group,title:`${title}${pages.length>1?`（${index+1}/${pages.length}）`:''}`,content:<div className="wr-paper">{render(page)}</div>}));
  };
  const changes=(model,prefix,group)=>MEETING_MONEY.forEach(metric=>append(model.changes,5,`${prefix}:changes:${metric.id}`,`${metric.id==='acv'?'':`${model.quarterLabel} `}${metric.label}变动`,group,page=><ChangesTable rows={page} metric={metric.id} fixed/>));
  changes(value,'team','业绩变动');
  append(value.customers,8,'customers','新认领客户','客户与商机',page=><CustomersTable rows={page}/>);
  append(value.opportunities,6,'opportunities','商机新增与丢单','客户与商机',page=><OpportunitiesTable rows={page}/>);
  append(value.followups,2,'followups','关键跟进','跟进进展',page=><Followups rows={page}/>);
  const tasks=(model,prefix,group)=>{
    if(!model.taskActivity)return;
    for(const [kind,id,title] of [['created','new_tasks','本期新增任务'],['completed','completed_tasks','本期完成任务']]) {
      const count=model.counts.find(row=>row.id===id);
      append(model.taskActivity[kind],5,`${prefix}:tasks:${kind}`,title,group,page=><><Missing reasons={count?.reasons}/><TaskActivityTable rows={page} count={count}/></>);
    }
  };
  tasks(value,'team','任务情况');
  members.forEach(member=>{
    const group=`成员 · ${member.name}`;
    if(member.error){slides.push({key:`member:${member.id}`,group,title:member.name,content:<div><Alert type="error" title={member.error}/></div>});return;}
    slides.push({key:`member:${member.id}`,group,title:`${member.name} · 本期汇总`,content:summary(member.value)});
    if(member.value.changes.length)changes(member.value,`member:${member.id}`,group);
    append(member.value.followups,2,`member:${member.id}:followups`,`${member.name} · 跟进进展`,group,page=><Followups rows={page}/>);
    tasks(member.value,`member:${member.id}`,group);
    append(member.value.nextSteps,4,`member:${member.id}:plans`,`${member.name} · 下一步`,group,page=><div className="wr-change-plan"><Plans rows={page}/></div>);
  });
  append(value.nextSteps,4,'plans','下一步与待协调','下一步',page=><div className="wr-change-plan"><Plans rows={page}/></div>);
  return slides;
}

// This component consumes a presentation model only. The adapter must preserve
// server amounts, complete/partial flags and snapshot membership exactly.
export function MeetingBoardBody({value,onOpenOpportunity,onOpenCustomer,onOpenTask,onMember,onBack,actions,presenting=false,selection,onSelection}) {
  const prefix=useId(),records=useRef(null),[local,setLocal]=useState({tab:'changes',eventKind:null,metric:'collection'});
  const {tab,eventKind,metric,taskKind='created'}=selection || local;
  const change=patch=>onSelection?onSelection(patch):setLocal(previous=>({...previous,...patch}));
  const select=key=>{
    change({taskKind:key==='completed_tasks'?'completed':'created',eventKind:key==='lost'?'lost':key==='new_opportunities'?'created':null,tab:['new_tasks','completed_tasks'].includes(key)?'tasks':key==='customers'?'customers':key==='followups'?'followups':['lost','new_opportunities'].includes(key)?'opportunities':'changes'});
    records.current?.scrollIntoView({block:'start'});
  };
  const tabs=[{id:'changes',label:'业绩变动'},{id:'customers',label:'新认领客户'},{id:'opportunities',label:'商机新增与丢单'},{id:'followups',label:'跟进与计划'}];
  if(value.taskActivity)tabs.push({id:'tasks',label:'任务情况'});
  return <div className={`wr-change-board${presenting?' is-presenting':''}`}>
    <header className="wr-change-heading"><div><div className="wr-change-eyebrow">{value.scopeLabel}<span> · {value.periodLabel}</span></div><h2>{value.title || '本期经营变化'}</h2><p>{value.asOfLabel}</p></div><div className="wr-toolbar">{onBack && <Button onClick={onBack}>返回团队汇总</Button>}{actions}</div></header>
    <MeetingCounts items={value.counts} onSelect={select}/>
    <MeetingAmounts items={value.amounts} quarterLabel={value.quarterLabel}/>
    {value.notices?.length>0 && <Alert type="warning" showIcon title="部分数据需核对，已保留可确认的数值" description={<Missing reasons={value.notices}/>}/>}
    {value.members?.length>0 && <section className="wr-paper wr-change-members"><header><h3>团队成员</h3><span className="wr-muted">选择成员查看其本期汇报</span></header><div>{value.members.map(row=><button type="button" key={row.id} disabled={!onMember} onClick={()=>onMember?.(row)}><span className="wr-change-avatar">{row.name?.slice(0,1)}</span><span><b>{row.name}</b><small>{row.summary}</small></span><i aria-hidden="true">→</i></button>)}</div></section>}
    <section className="wr-paper wr-change-records" aria-label="本期变化明细" ref={records}>
      <div className="wr-change-tabs" role="tablist" aria-label="变化明细类型">{tabs.map((item,index)=><button type="button" role="tab" id={`${prefix}-tab-${item.id}`} aria-controls={`${prefix}-details`} aria-selected={tab===item.id} tabIndex={tab===item.id?0:-1} key={item.id}
        onKeyDown={event=>{if(['ArrowRight','ArrowLeft','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:tabs.length-1))%tabs.length;change({tab:tabs[next].id,eventKind:null});event.currentTarget.parentElement.children[next]?.focus();}}}
        onClick={()=>change({tab:item.id,eventKind:null})}>{item.label}</button>)}</div>
      <div role="tabpanel" id={`${prefix}-details`} aria-labelledby={`${prefix}-tab-${tab}`} className="wr-change-record-body">
        {tab==='tasks' && value.taskActivity?<TaskActivity value={value.taskActivity} counts={value.counts} kind={taskKind} onKind={taskKind=>change({taskKind})} onOpenTask={onOpenTask} onOpenCustomer={onOpenCustomer} onOpenOpportunity={onOpenOpportunity}/>:tab==='changes'?<ChangesTable rows={value.changes} onOpen={onOpenOpportunity} metric={metric} onMetric={metric=>change({metric})}/>:tab==='customers'?<CustomersTable rows={value.customers} onOpen={onOpenCustomer}/>:tab==='opportunities'?<><div className="wr-change-subtoolbar"><Segmented aria-label="商机变化类型" value={eventKind || 'all'} options={[{value:'all',label:'全部'},{value:'created',label:'新增'},{value:'lost',label:'丢单'}]} onChange={kind=>change({eventKind:kind==='all'?null:kind})}/></div><OpportunitiesTable rows={value.opportunities} onOpen={onOpenOpportunity} kind={eventKind}/></>:<Followups rows={value.followups}/>}
      </div>
      <Missing reasons={value.detailNotes}/>
    </section>
    <section className="wr-paper wr-change-plan"><header><h3>下一步计划</h3><span className="wr-muted">已登记计划 · 尚未完成</span></header><Plans rows={value.nextSteps}/></section>
    <Missing reasons={value.definitions}/>
  </div>;
}

export default function MeetingBoardView(props) {
  const [deck,setDeck]=useState(null);
  const [selection,setSelection]=useState({tab:'changes',eventKind:null,metric:'collection'});
  const onSelection=patch=>setSelection(previous=>({...previous,...patch}));
  if(props.presenting)return <MeetingBoardBody {...props} selection={selection} onSelection={onSelection} onOpenOpportunity={undefined} onOpenCustomer={undefined} onOpenTask={undefined} actions={undefined}/>;
  return <>
    <MeetingBoardBody {...props} selection={selection} onSelection={onSelection} actions={<>{props.actions}{!props.hideMeetingButton && <Button icon={<NavIcon name="present"/>} onClick={()=>setDeck({slides:meetingSlides(props.value,{members:props.meetingMembers?.() || []}),title:props.value.title,subtitle:`${props.value.scopeLabel} · ${props.value.periodLabel} · ${props.value.asOfLabel}`})}>会议模式</Button>}</>}/>
    <ReportPresentation open={Boolean(deck)} {...deck} onClose={()=>setDeck(null)}/>
  </>;
}
