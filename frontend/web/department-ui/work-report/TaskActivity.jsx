import React from 'react';
import {Button,Empty,Segmented,Table} from 'antd';
import {countText} from './meeting-display.js';

// Counts and membership come from the same authorized overview response.
// A filtered/paginated list must never be used to calculate either headline.
export function TaskActivityTable({rows=[],count,onOpenTask,onOpenCustomer,onOpenOpportunity}) {
  const object=(row,name,open)=>name?(open?<Button type="link" onClick={()=>open(row)}>{name}</Button>:<span>{name}</span>):null;
  return <Table rowKey="id" size="small" dataSource={rows} pagination={rows.length>10?{pageSize:10,showSizeChanger:false}:false} scroll={{x:980}}
    locale={{emptyText:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={count?.value===0?'本期没有此类任务':'暂无可展示的任务明细，请查看统计说明'}/>}}
    columns={[
      {title:'任务名称',key:'name',width:260,render:(_,row)=><div className="wr-change-object">{onOpenTask && row.taskId?<Button type="link" onClick={()=>onOpenTask(row)}>{row.name}</Button>:<b>{row.name}</b>}</div>},
      {title:'负责人',dataIndex:'ownerLabel',width:110},
      {title:'关联客户 / 商机',key:'related',width:240,render:(_,row)=><div className="wr-change-object">{object(row,row.customerName,row.customerId?onOpenCustomer:undefined)}{object(row,row.opportunityName,row.customerId && row.opportunityId?onOpenOpportunity:undefined)}{!row.customerName && !row.opportunityName && <span>关联信息未提供</span>}</div>},
      {title:'创建时间',dataIndex:'createdLabel',width:160},
      {title:'完成时间',dataIndex:'completedLabel',width:160},
      {title:'当前状态',dataIndex:'statusLabel',width:110},
    ]}/>;
}

export default function TaskActivity({value,counts,kind='created',onKind,...links}) {
  const id=kind==='completed'?'completed_tasks':'new_tasks',count=counts.find(row=>row.id===id);
  return <>
    <div className="wr-change-subtoolbar"><Segmented aria-label="任务变化类型" value={kind} onChange={onKind}
      options={[{value:'created',label:'本期新增'},{value:'completed',label:'本期完成'}]}/>
      <span className="wr-muted">{count?.label} {countText(count?.value)} 项</span></div>
    {count?.reasons?.length>0 && <p className="wr-change-task-notice" role="status">{count.reasons.join('；')}</p>}
    <TaskActivityTable rows={value[kind]} count={count} {...links}/>
    <p className="wr-muted wr-change-task-basis">新增按创建时间，完成按实际完成时间；以前创建、本期完成的任务也计入完成数。</p>
  </>;
}
