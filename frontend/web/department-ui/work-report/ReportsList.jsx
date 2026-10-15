import React from 'react';
import {PAGE_SIZE_OPTIONS} from '../pc/list-pagination.js';
import {Alert, Button, Input, Select, Table} from 'antd';
import FilterBar from '../pc/FilterBar.jsx';
import {EmptyReports, ReportStatus} from './ReportFrame.jsx';

export default function ReportsList({items, loading, error, onRetry, onOpen, onCreate, query, onQuery, onSearch, statuses, pagination, onPage}) {
  return <div className="wr-report-list">
    <FilterBar label="查找报告" search={<Input.Search aria-label="搜索报告名称" placeholder="搜索报告名称" value={query.text} maxLength={120} onChange={event=>onQuery({...query,text:event.target.value})} onSearch={onSearch} allowClear />} actions={onCreate && <Button onClick={onCreate}>生成新报告</Button>}>
      <Select aria-label="报告状态" value={query.status} options={statuses} onChange={status=>onQuery({...query,status})} data-toolbar-width={140} />
    </FilterBar>
    {error ? <Alert type="error" title={error} action={onRetry && <Button onClick={onRetry}>重试</Button>} /> : !loading && !items.length && !query.text && !query.status ? <EmptyReports onCreate={onCreate} /> :
      <div className="wr-table"><Table size="small" rowKey="id" loading={loading} dataSource={items} scroll={{x:740}}
        pagination={pagination ? {current:pagination.page,pageSize:pagination.pageSize,total:pagination.total,showSizeChanger:true,pageSizeOptions:PAGE_SIZE_OPTIONS,showTotal:total=>`共 ${total} 条`,onChange:onPage} : false}
        locale={{emptyText:'没有符合条件的报告'}} columns={[
          {title:'报告名称',key:'title',width:280,render:(_,row)=><Button type="link" className="wr-report-title" onClick={()=>onOpen(row.id)}>{row.title}</Button>},
          {title:'周期与范围',key:'range',render:(_,row)=><div>{row.periodLabel}<p className="wr-muted">{row.scopeLabel}</p></div>},
          {title:'报告方案',dataIndex:'schemeLabel',key:'scheme',width:150},
          {title:'状态',key:'status',width:112,render:(_,row)=><ReportStatus status={row.status} />},
          {title:'更新时间',dataIndex:'updatedLabel',key:'updated',width:156},
        ]} /></div>}
  </div>;
}
