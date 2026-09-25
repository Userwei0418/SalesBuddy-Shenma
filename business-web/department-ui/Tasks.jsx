import React, { useEffect, useState } from 'react';
import { Button, Segmented } from 'antd';
import { SbLabeledSelect, SbStatePanel, SbStatusTag, SbTable, SbTabs } from '@shandiant/ui-react';
import './tasks.css';

const toneFor = signal => ({green: 'good', yellow: 'watch', red: 'bad', gray: 'pending'})[signal?.tone] || 'pending';
// B-02：任务状态是对象状态，用文字；只有业务判断（逾期、转差）才用红黄绿灰标签，且带依据
const TONE_LABEL = {good: '向好', watch: '需关注', bad: '转差', pending: '待评估'};

/** Presentation only. The shared page owns scope, state, sorting and cursor paging. */
export default function Tasks({ page, data, invoke }) {
  const rows = data.webTaskRows || [];
  const query = JSON.stringify([data.activeTab, data.overviewFilter, data.sortIndex, data.taskView, data.fdeTaskMemberId, data.completedYear, data.completedQuarters, data.opportunityOnly, page.teamFilter, page.memberFilter]);
  const [pagination, setPagination] = useState(() => ({query: page._departmentTaskQuery || query, current: page._departmentTaskPage || 1}));
  const sizeForViewport = () => globalThis.innerWidth <= 600 ? 6 : globalThis.innerHeight < 800 ? 4 : 6;
  const [pageSize, setPageSize] = useState(sizeForViewport);
  useEffect(() => {const resize = () => setPageSize(sizeForViewport()); globalThis.addEventListener('resize', resize); return () => globalThis.removeEventListener('resize', resize);}, []);
  const current = Math.min(pagination.query === query ? pagination.current : 1, Math.max(1, Math.ceil(rows.length / pageSize)));
  const visibleRows = rows.slice((current - 1) * pageSize, current * pageSize);
  const changePage = next => { page._departmentTaskQuery = query; page._departmentTaskPage = next; setPagination({query, current: next}); };
  useEffect(() => {if (pagination.query !== query) changePage(1);}, [query]);
  const call = (name, payload = {}) => invoke(name, payload);
  const tab = key => call('selectTab', {dataset: {key}});
  const busy = Boolean(data.loading);
  const columns = [
    {title: '任务 / 关联对象', key: 'task', width: 340, render: (_, row) => (
      <div className="department-task-object">
        <Button type="link" className="department-task-title" onClick={() => call('openTask', {dataset: {id: row.id}})}>{row.webTitle}</Button>
        <div className="department-task-meta">{row.customer || '协作任务'}</div>
        {row.opportunityId && <Button type="link" className="department-task-related" onClick={() => call('openOpportunity', {dataset: {customerId: row.customerId, opportunityId: row.opportunityId}})}>{row.opportunityName || '查看商机'} ↗</Button>}
      </div>
    )},
    {title: '任务状态', key: 'status', width: 155, render: (_, row) => { const t = toneFor(row.signal); return <div className="department-task-status"><span>{row.statusLabel}</span>{(t === 'watch' || t === 'bad') && <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={row.signal?.reason} />}</div>; }},
    {title: '负责人', key: 'owner', width: 125, render: (_, row) => <div>{row.owner || '待分配'}{row.team && <div className="department-task-meta">{row.team}</div>}</div>},
    {title: '时间', key: 'time', width: 170, render: (_, row) => <div className={`department-task-time department-task-time-${row.webTime?.tone || 'undated'}`}><span>{row.webTime?.label || '未设截止时间'}</span><div className="department-task-meta">{row.webTime?.detail || '待安排'}</div></div>},
    {title: '优先级', key: 'priority', width: 90, render: (_, row) => <span className={row.webPriority ? 'department-task-priority' : ''}>{row.priority || '未登记'}</span>},
  ];
  if (data.accessBlocked) return <SbStatePanel state="forbidden" description={data.accessMessage} />;
  return (
    <section className="department-tasks" aria-label="任务列表">
      <div className="department-task-panel">
        {data.isFde && data.canViewTeam && <div className="department-task-scope"><Segmented aria-label="任务范围" value={data.taskView || 'self'} options={[{label: '本人任务', value: 'self'}, {label: '团队任务', value: 'team'}]} onChange={view => call('changeTaskView', {dataset: {view}})} /></div>}
        {Boolean(data.completedYear) && <p className="department-task-context">完成时间：{data.completedYear} 年{data.completedQuarters?.length ? `第 ${data.completedQuarters.join('、')} 季度` : '全年'}</p>}
        {data.overviewFilter && <div className="department-task-overview" role="status"><div><strong>{data.overviewTitle}</strong></div><Button type="link" onClick={() => tab(data.activeTab)}>清除总览条件</Button></div>}
        <div className="department-task-tabs"><SbTabs activeKey={data.activeTab} onChange={tab} items={(data.webTaskTabs || data.tabs || []).map(item => ({key: item.key, label: item.label, count: item.count}))} /></div>
        <div className="department-task-toolbar">
          <div><strong>{busy || data.loadError ? '正在加载任务…' : `共 ${data.filteredTotal} 项`}</strong></div>
          <SbLabeledSelect label="排序" allowClear={false} value={data.sortIndex} options={(data.sortOptions || []).map((item, index) => ({label: item.label, value: index}))} onChange={value => call('changeSort', {detail: {value}})} />
        </div>
        <SbTable className="department-task-table" rowKey="id" density="compact" columns={columns} rows={visibleRows} state={data.loadError ? 'error' : busy ? 'loading' : !rows.length ? 'empty' : 'normal'}
          emptyTitle={data.loadError ? '加载失败' : data.overviewEmptyTitle || (data.activeTab === 'pending' ? '没有待处理的任务' : '没有匹配的任务')} emptyDescription={data.loadError || undefined} onRetry={() => call('loadTasks')} onClear={data.overviewFilter ? () => tab(data.activeTab) : undefined}
          actions={row => <Button type="link" onClick={() => call('openTask', {dataset: {id: row.id}})}>{row.webAction || '查看'}</Button>} actionsWidth={112} scrollX={992}
          pagination={{current, pageSize, total: rows.length, onChange: changePage}}
          expandable={{rowExpandable: row => Boolean(row.webDescription), expandedRowRender: row => <div className="department-task-expanded"><strong>完整任务描述</strong><p>{row.webDescription}</p><span>{row.sourceLabel}</span></div>, columnTitle: <span className="department-task-sr-only">展开说明</span>, columnWidth: 32}} />
        {!busy && !data.loadError && rows.length > 0 && (data.hasMore || data.moreError || data.loadingMore) && <div className="department-task-pagination" aria-live="polite">{Number.isInteger(data.filteredTotal) && data.filteredTotal > rows.length && <span>还有 {data.filteredTotal - rows.length} 项没加载</span>}{data.moreError && <span role="alert">{data.moreError}</span>}<Button loading={data.loadingMore} onClick={() => call('loadMore')}>{data.moreError ? '重试' : '加载更多'}</Button></div>}
      </div>
    </section>
  );
}
