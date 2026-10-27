import React from 'react';
import {Button,Empty,Segmented,Table} from 'antd';
import {countText} from './meeting-display.js';

// Counts and membership come from the same authorized overview response.
// A filtered/paginated list must never be used to calculate either headline.
export function TaskActivityTable({rows=[],count,kind,lightweight=false,onOpenTask,onOpenCustomer,onOpenOpportunity}) {
  const object=(row,name,open)=>name?(open?<Button type="link" onClick={()=>open(row)}>{name}</Button>:<span>{name}</span>):null;
  return <Table rowKey="id" size="small" dataSource={rows} pagination={rows.length>10?{pageSize:10,showSizeChanger:false}:false} scroll={{x:980}}
    locale={{emptyText:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={count?.value===0?'本期没有此类任务':'暂无可展示的任务明细，请查看统计说明'}/>}}
    columns={[
      {title:'任务名称',key:'name',width:260,render:(_,row)=><div className="wr-change-object">{onOpenTask && row.taskId?<Button type="link" onClick={()=>onOpenTask(row)}>{row.name}</Button>:<b>{row.name}</b>}</div>},
      {title:'负责人',dataIndex:'ownerLabel',width:110},
      {title:'关联客户 / 商机',key:'related',width:240,render:(_,row)=><div className="wr-change-object">{object(row,row.customerName,row.customerId?onOpenCustomer:undefined)}{object(row,row.opportunityName,row.customerId && row.opportunityId?onOpenOpportunity:undefined)}{!row.customerName && !row.opportunityName && <span>关联信息未提供</span>}</div>},
      {title:'创建时间',dataIndex:'createdLabel',width:160},
      {title:'完成时间',dataIndex:'completedLabel',width:160},
      {title:'当前状态',key:'status',width:140,render:(_,row)=><div>{row.statusLabel}{row.processingLabel && <div className="wr-muted">{row.processingLabel}</div>}</div>},
      ...(lightweight?[{title:kind==='completed'?'完成结果':'最新反馈',key:'feedback',width:300,render:(_,row)=><div style={{whiteSpace:'pre-wrap'}}>{(kind==='completed'?row.completionResult:row.latestFeedback) || '暂无记录'}{kind!=='completed' && row.nextStep && <p>下一步：{row.nextStep}</p>}{row.processingDetail && <p>{row.processingDetail}</p>}</div>},{title:'更新时间',dataIndex:'updatedLabel',width:160}]:[]),
    ]}/>;
}

export default function TaskActivity({value,counts,kind='completed',onKind,...links}) {
  const lightweight=Object.hasOwn(value,'progressing');
  const selected=lightweight?(kind==='progressing'?'progressing':'completed'):(kind==='completed'?'completed':'created');
  const id=selected==='completed'?'completed_tasks':selected==='progressing'?'progressing_tasks':'new_tasks',count=counts.find(row=>row.id===id);
  const options=lightweight?[{value:'completed',label:'完成任务'},{value:'progressing',label:'推进任务'}]:[{value:'created',label:'本期新增（历史口径）'},{value:'completed',label:'本期完成'}];
  return <>
    <div className="wr-change-subtoolbar"><Segmented aria-label="任务情况" value={selected} onChange={onKind} options={options}/>
      <span className="wr-muted">{count?.label} {countText(count?.value)} 项</span></div>
    {count?.reasons?.length>0 && <p className="wr-change-task-notice" role="status">{count.reasons.join('；')}</p>}
    <TaskActivityTable rows={value[selected]} count={count} kind={selected} lightweight={lightweight} {...links}/>
    <p className="wr-muted wr-change-task-basis">{lightweight?'完成仅统计本期正常完成，不含停止、历史取消、拒绝或结果待核对；推进为当前未结束任务，待验收也属于推进。':'历史口径：新增按创建时间，完成按实际完成时间。'}</p>
  </>;
}
