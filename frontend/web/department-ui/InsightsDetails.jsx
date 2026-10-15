import React from 'react';
import {useLocalTablePagination} from './pc/list-pagination.js';
import {Button, Drawer, Table} from 'antd';
import {insightCell} from './insights-presentation.js';

export default function InsightsDetails({metricKey, detail, onClose, onOpenRecord}) {
  const pagination = useLocalTablePagination('insights-details', `${metricKey}:${detail.periodLabel}:${detail.scopeLabel}`);
  const columns = (detail.columns || []).map(column => ({
    key: column.key, title: column.label, dataIndex: column.key,
    render: value => insightCell(value, column.kind),
  }));
  if (onOpenRecord && (detail.rows || []).some(row => row.can_open === true)) columns.push({
    key: 'source', title: '来源', width: 100,
    render: (_, row) => row.can_open === true ? <Button size="small" type="link" onClick={() => onOpenRecord(metricKey, row.id)}>查看记录</Button> : '—',
  });
  return <Drawer open title={detail.title || '指标明细'} onClose={onClose} size={920} classNames={{root: 'department-ui ds-insight-drawer'}}>
    <div className="ds-insight-context"><span>{detail.scopeLabel}</span><span>{detail.periodLabel}</span>{detail.unit && <span>单位：{detail.unit}</span>}</div>
    <p className="ds-insight-definition">{detail.definition}</p>
    {detail.note && <p className="ds-muted">{detail.note}</p>}
    {detail.summary && <p className="ds-insight-summary">{detail.summary}</p>}
    <Table size="small" rowKey="id" columns={columns} dataSource={detail.rows || []} scroll={{x: 680}}
      pagination={pagination}
      locale={{emptyText: detail.emptyText || '当前范围没有对应记录'}} />
  </Drawer>;
}
