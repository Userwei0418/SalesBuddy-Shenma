import {TaskCancellationDialog} from './NativeDialogs.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {Kpi, ScopePicker, useAsync} from './pc/ui.jsx';
import {identity, loadDirectory} from './pc/data.js';
import React, { useEffect, useState } from 'react';
import {Button, Pagination} from 'antd';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {SbLabeledSelect, SbStatePanel, SbStatusTag, SbTable, SbTabs} from '@shandiant/ui-react';
import {PageHead} from './pc/ui.jsx';
import './tasks.css';
import './pc/pc-ui.css';
import TaskSyncStatus, {taskSyncUnavailable} from './task-execution/TaskSyncStatus.jsx';

const toneFor = signal => ({green: 'good', yellow: 'watch', red: 'bad', gray: 'pending'})[signal?.tone] || 'pending';
// B-02：任务状态是对象状态，用文字；只有业务判断（逾期、转差）才用红黄绿灰标签，且带依据
const TONE_LABEL = {good: '向好', watch: '需关注', bad: '转差', pending: '待评估'};
const TASK_PERIODS = [{value: 'all', label: '全部'}, {value: 'day', label: '日'}, {value: 'week', label: '周'}, {value: 'month', label: '月'}, {value: 'quarter', label: '季'}];
function feedbackText(row) {
  for (const value of [row.latestFeedback, row.latest_feedback]) {
    const text = typeof value === 'string' ? value : value && typeof value.text === 'string' ? value.text : '';
    if (text.trim()) return text;
  }
  return '暂无反馈';
}

/** Presentation only. The shared page owns scope, state, sorting and cursor paging. */
export default function Tasks({ page, data, invoke, invokeOn, select }) {
  const directory = useAsync(() => loadDirectory('task.read'), [identity()]);
  const scope = data.fdeTaskMemberId ? {kind: 'member', memberId: data.fdeTaskMemberId} : data.taskView === 'team' ? {kind: 'team', teamId: data.webTaskTeamId || 'all'} : {kind: 'self'};
  const rows = data.webTaskRows || [];
  const query = JSON.stringify([data.activeTab, data.taskSummaryGroup, data.overviewFilter, data.taskPeriod, data.sortIndex, data.taskView, data.fdeTaskMemberId, data.webTaskTeamId, data.completedYear, data.completedQuarters, data.opportunityOnly, page.teamFilter, page.memberFilter]);
  const [pagination, setPagination] = useState(() => ({query: page._departmentTaskQuery || query, current: page._departmentTaskPage || 1}));
  const [pageSize, setPageSize] = useListPageSize('tasks');
  const knownTotal = Number.isInteger(data.filteredTotal) && data.filteredTotal >= 0 ? data.filteredTotal : null;
  const total = knownTotal ?? rows.length;
  const current = Math.min(pagination.query === query ? pagination.current : 1, Math.max(1, Math.ceil(total / pageSize)));
  const visibleRows = rows.slice((current - 1) * pageSize, current * pageSize);
  useEffect(() => {page._webTaskVisibleEnd = current * pageSize;}, [page, current, pageSize]);
  const changePage = next => { page._departmentTaskQuery = query; page._departmentTaskPage = next; setPagination({query, current: next}); };
  useEffect(() => {if (pagination.query !== query) changePage(1);}, [query]);
  const call = (name, payload = {}) => invoke(name, payload);
  const tab = key => call('selectTab', {dataset: {key}});
  const busy = Boolean(data.loading);
  const pagePending = !busy && !data.loadError && data.hasMore && rows.length < Math.min(current * pageSize, total);
  // The shared task contract supplies 20-row cursor pages. Fill only the requested
  // visible page; preserve its real total and keep failures explicitly retryable.
  useEffect(() => {
    if (pagePending && !data.loadingMore && !data.moreError) call('loadMore');
  }, [query, current, pageSize, pagePending, rows.length, data.nextOffset, data.loadingMore, data.moreError]);
  const kpis = data.webTaskKpis || {pending: data.pendingCount, completed: data.completedCount, overdue: null, teamMembers: null};
  const displayKpi = value => busy || data.loadError || value === null || value === undefined ? '—' : value;
  const missingKpis = !busy && !data.loadError ? [['completed', '正常完成'], ['overdue', '逾期任务'], ['teamMembers', '涉及执行人']].filter(([key]) => kpis[key] == null).map(([, label]) => label) : [];
  const periodOptions = data.taskPeriodOptions || TASK_PERIODS;
  const columns = [
    {title: '任务 / 关联对象', key: 'task', width: 340, render: (_, row) => (
      <div className="department-task-object">
        <Button type="link" className="department-task-title" onClick={() => call('openTask', {dataset: {id: row.id}})}>{row.webTitle}</Button>
        <div className="department-task-meta">{row.customer || '协作任务'}</div>
        {row.opportunityId && <Button type="link" className="department-task-related" onClick={() => call('openOpportunity', {dataset: {customerId: row.customerId, opportunityId: row.opportunityId}})}>{row.opportunityName || '查看商机'} ↗</Button>}
      </div>
    )},
    {title: '任务状态', key: 'status', width: 155, render: (_, row) => { const t = toneFor(row.signal); return <div className="department-task-status"><span>{row.statusLabel}</span>{row.processingLabel && <small className="ds-muted">{row.processingLabel}</small>}{row.responsibilityLabel&&<small className="ds-muted">{row.responsibilityLabel}</small>}{row.showProgress && <span aria-label="任务清单进度">{row.progressView?.label || '进度暂不可用'} · {row.progressView?.countLabel || '—'}</span>}{(t === 'watch' || t === 'bad') && <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={row.signal?.reason} />}</div>; }},
    {title: '负责人', key: 'owner', width: 125, render: (_, row) => <div>{row.owner || '待分配'}{row.team && <div className="department-task-meta">{row.team}</div>}</div>},
    {title: '时间', key: 'time', width: 170, render: (_, row) => <div className={`department-task-time department-task-time-${row.webTime?.tone || 'undated'}`}><span>{row.webTime?.label || '未设截止时间'}</span><div className="department-task-meta">{row.webTime?.detail || '待安排'}</div></div>},
    {title: '最新反馈', key: 'feedback', width: 260, render: (_, row) => <span style={{whiteSpace:'pre-wrap'}}>{feedbackText(row)}</span>},
    {title: '更新时间', key: 'updated', width: 160, render: (_, row) => <span>{row.updatedLabel || row.updated_at || '未记录'}</span>},
    {title: '优先级', key: 'priority', width: 90, render: (_, row) => <span className={row.webPriority ? 'department-task-priority' : ''}>{row.priority || '未登记'}</span>},
  ];
  if (data.accessBlocked) return <SbStatePanel state="forbidden" description={data.accessMessage} />;
  return (
    <div className="pc-page pc-list-page pc-tasks">
      <PageHead title={data.opportunityOnly ? '商机待办' : '任务'} subtitle={scope.kind === 'self' ? '与我相关：我发起、负责或作为候选人的任务' : '按所选部门或成员的负责人范围查看任务'} />
      <section className="department-tasks" aria-label="任务列表">
      <div className="department-task-refresh"><TaskSyncStatus sync={data.webTaskSync} /><Button loading={busy || data.webTaskSync?.status === 'syncing'} disabled={data.loadingMore || data.cancelOpen} onClick={() => call('refreshWebTasks')}>刷新任务</Button></div>
      <div className="pc-kpis" role="group" aria-label="任务概览">
        <Kpi label="未完成任务" value={displayKpi(kpis.pending)} unit="项" />
        <Kpi label="正常完成" value={displayKpi(kpis.completed)} unit="项" />
        <Kpi label="已逾期" value={displayKpi(kpis.overdue)} unit="项" tone={kpis.overdue > 0 ? 'danger' : undefined} sub={missingKpis.includes('逾期任务') ? '接口未返回' : undefined} />
        <Kpi label="涉及执行人" value={displayKpi(kpis.teamMembers)} unit="人" sub={missingKpis.includes('涉及执行人') ? '接口未返回' : undefined} />
      </div>
      <div className="department-task-panel">
        {directory.error && <div className="pc-error" role="alert"><span>任务范围目录加载失败：{directory.error}</span><Button size="small" onClick={directory.reload}>重新加载人员目录</Button></div>}
        {Boolean(data.completedYear) && <p className="department-task-context">完成时间：{data.completedYear} 年{data.completedQuarters?.length ? `第 ${data.completedQuarters.join('、')} 季度` : '全年'}</p>}
        {data.overviewFilter && <div className="department-task-overview" role="status"><div><strong>{data.overviewTitle}</strong></div><Button type="link" onClick={() => tab(data.activeTab)}>清除总览条件</Button></div>}
        {missingKpis.length > 0 && <p className="department-task-kpi-note" role="status">{missingKpis.join('、')}数量：接口未返回，本页不根据当前分页推算。</p>}
        <div className="department-task-tabs"><SbTabs activeKey={data.activeTab} onChange={tab} items={(data.webTaskTabs || data.tabs || []).map(item => ({key: item.key, label: item.label, count: item.count}))} /></div>
        <p className="department-task-kpi-note"><strong>未完成：</strong>包含进行中、已延期、需调整及历史待处理任务，排除所有已结束任务。<strong>正常完成：</strong>不含停止、历史取消或拒绝；所有终态可在“已结束”查看。<strong>进行中：</strong>仅统计当前状态为“进行中”的任务，是未完成的一部分。</p>
        <FilterBar className="department-task-toolbar" label="任务筛选" search={
          <div className="department-task-toolbar-summary"><strong>{data.loadError ? '任务加载失败' : busy ? '正在加载任务…' : `共 ${data.filteredTotal} 项`}</strong>{data.taskPeriodUnsupported && <span className="department-task-period-note" role="status">{data.taskPeriodMessage || '当前周期暂不支持'}</span>}</div>}>
          {directory.value && <ScopePicker data-toolbar-width={scope.kind === 'member' ? 570 : scope.kind === 'team' ? 380 : 216} selfLabel="与我相关" permission="task.read" scope={scope} directory={directory.value} onChange={scope => call('changeWebTaskScope', {detail: {scope}})} />}
          <SbLabeledSelect label="周期" allowClear={false} value={data.taskPeriod || 'all'} options={periodOptions} onChange={value => call('changeTaskPeriod', {detail: {value}})} />
          <SbLabeledSelect label="排序" allowClear={false} value={data.sortIndex} options={(data.sortOptions || []).map((item, index) => ({label: item.label, value: index}))} onChange={value => call('changeSort', {detail: {value}})} />
        </FilterBar>
        <div className="pc-results-scroll" role="region" aria-label="任务明细" tabIndex={0}>
        <SbTable className="department-task-table" rowKey="id" density="compact" columns={columns} rows={visibleRows} state={data.loadError ? 'error' : busy || pagePending && !data.moreError ? 'loading' : !rows.length ? 'empty' : 'normal'}
          emptyTitle={data.loadError ? '加载失败' : data.overviewEmptyTitle || (data.activeTab === 'pending' ? '没有待处理的任务' : '没有匹配的任务')} emptyDescription={data.loadError || undefined} onRetry={() => call('loadTasks')} onClear={data.overviewFilter ? () => tab(data.activeTab) : undefined}
          actions={row => <><Button type="link" onClick={() => call('openTask', {dataset: {id: row.id}})}>{row.webAction || '查看'}</Button>{row.canCancel && <Button type="link" danger disabled={taskSyncUnavailable(data.webTaskSync)} onClick={()=>call('openCancellation',{dataset:{id:row.id}})}>取消</Button>}</>} actionsWidth={112} scrollX={992}
          expandable={{rowExpandable: row => Boolean(row.webDescription), expandedRowRender: row => <div className="department-task-expanded"><strong>完整任务描述</strong><p>{row.webDescription}</p><span>{row.sourceLabel}</span></div>, columnTitle: <span className="department-task-sr-only">展开说明</span>, columnWidth: 32}} />
        {!busy && !data.loadError && <div className="department-task-pagination">
          {knownTotal === null && <span>已加载 {rows.length} 项{data.hasMore ? '，完整数量暂不可用' : ''}</span>}
          <Pagination current={current} pageSize={pageSize} total={total} showSizeChanger pageSizeOptions={PAGE_SIZE_OPTIONS}
            showTotal={number => knownTotal === null ? `已加载 ${number} 项` : `共 ${number} 项`} onChange={(number, size) => {setPageSize(size); changePage(size !== pageSize ? 1 : number);}} />
        </div>}
        {!busy && !data.loadError && (data.moreError || knownTotal === null && data.hasMore) && <div className="department-task-pagination" aria-live="polite">
          {data.moreError && <span role="alert">{data.moreError}</span>}<Button loading={data.loadingMore} onClick={() => call('loadMore')}>{data.moreError ? '重试加载本页' : '加载更多'}</Button>
        </div>}
        </div>
      </div>
      <TaskCancellationDialog instance={select('#taskCancellation')} invokeOn={invokeOn} />
      </section>
    </div>
  );
}

Tasks.subcomponents=(page,d)=>[{selector:'#taskCancellation',required:!d.accessBlocked,props:{open:d.cancelOpen,taskId:d.cancelTaskId,taskVersion:d.cancelTaskVersion}}];
