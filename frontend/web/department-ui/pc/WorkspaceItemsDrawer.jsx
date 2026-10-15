import React, {useState} from 'react';
import {Drawer, Table} from 'antd';
import {LoadState, useAsync} from './ui.jsx';
import {identity, open, scopeKey} from './data.js';
import {loadWorkspaceRisks, loadWorkspaceTasks} from './workspace-data.js';

export default function WorkspaceItemsDrawer({selection, scope, label, onClose}) {
  const [page, setPage] = useState(1);
  const isRisk = selection === 'risks';
  const title = {risks: '当前未关闭风险', all_pending: '当前待办任务', today_pending: '今日待办', today_completed: '今日已完成'}[selection];
  const data = useAsync(() => isRisk ? loadWorkspaceRisks(scope, {offset: (page - 1) * 50})
    : loadWorkspaceTasks(scope, {overview: selection, offset: (page - 1) * 50}), [selection, scopeKey(scope), page, identity()]);
  const columns = [
    {title: isRisk ? '风险' : '任务', key: 'title', render: (_, row) => <button type="button" className="pc-link" onClick={() => isRisk ? open.risk(row.id) : open.task(row.id)}>{row.title}</button>},
    {title: '客户', dataIndex: 'customerName', width: 180},
    {title: '负责人', dataIndex: 'owner', width: 100},
    ...(isRisk ? [{title: '商机', dataIndex: 'opportunityName', width: 160}] : [{title: selection === 'today_completed' ? '完成日期' : '截止日期', key: 'date', width: 110,
      render: (_, row) => (selection === 'today_completed' ? row.completedDay : row.dueDay) || '未设截止'}]),
  ];
  return <Drawer open title={`${title} · ${label}`} onClose={onClose} size={820} classNames={{root: 'department-ui pc-fu-drawer'}}>
    <LoadState loading={data.loading} error={data.error} onRetry={data.reload}>
      <Table rowKey="id" size="small" dataSource={data.value?.items || []} columns={columns} scroll={{x: 700}}
        locale={{emptyText: '当前范围没有记录'}} pagination={{current: page, pageSize: 50, total: data.value?.total || 0,
          showSizeChanger: false, showTotal: total => `共 ${total} 条`, onChange: setPage}} />
    </LoadState>
  </Drawer>;
}
