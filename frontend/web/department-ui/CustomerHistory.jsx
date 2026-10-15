import FilterBar from './pc/FilterBar.jsx';
import React from 'react';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {Button, Input, Select, Table, Tabs} from 'antd';
import {can, identity, loadDirectory, open, scopeLabel} from './pc/data.js';
import {Kpi, LoadState, PageHead, Panel, ScopePicker, Tag2, useAsync, useSessionState} from './pc/ui.jsx';
import {initialHistoryFilters, loadRequestHistory, REQUEST_STATUSES as STATUSES} from './pc/customer-flow.js';
import './pc/pc-ui.css';

const moment = value => value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '—';

export default function CustomerHistory({page}) {
  const owner = identity(), allowed = can('customer.read');
  const [pageSize, setPageSize] = useListPageSize('customer-history');
  const [filters, setFilters] = useSessionState('customer-history', initialHistoryFilters(), page);
  const [draft, setDraft] = useSessionState('customer-history-search', filters.q, page);
  const directory = useAsync(() => allowed ? loadDirectory('customer.read') : Promise.resolve(null), [owner, allowed]);
  const result = useAsync(() => allowed ? loadRequestHistory({...filters, pageSize}) : Promise.resolve(null), [owner, allowed, JSON.stringify(filters), pageSize]);
  const change = patch => setFilters(value => ({...value, ...patch, offset: 0}));
  const summary = !result.loading && !result.error ? result.value?.summary : null;
  const refresh = () => { directory.reload(); result.reload(); };
  const columns = [
    {title: '客户 / 申请单', dataIndex: 'customer_name', width: 230, render: (name, row) => <div>
      {row.customer_id && row.can_read_customer ? <Button type="link" onClick={() => open.customer(row.customer_id)}>{name}</Button> : <span>{name || '客户资料未授权'}</span>}
      <div className="pc-muted" style={{fontSize: 12, overflowWrap: 'anywhere'}} title={row.id}>记录号 {row.id}</div>
      {row.kind === 'creation' && row.status === 'approved' && can('customer.claim') && can('customer.claim_directory') && name && <Button type="link" size="small" onClick={() => open.route(`pages/customer-claim/index?q=${encodeURIComponent(name)}`)}>查询认领状态</Button>}
    </div>},
    {title: '申请类型', dataIndex: 'kind', width: 100, render: value => value === 'creation' ? '新建客户' : '认领客户'},
    {title: '申请人', dataIndex: 'applicant_name', width: 110},
    {title: '状态', dataIndex: 'status', width: 100, render: value => <Tag2 tone={value === 'approved' ? 'green' : value === 'rejected' ? 'red' : 'neutral'}>{STATUSES[value]}</Tag2>},
    {title: '申请时间', dataIndex: 'requested_at', width: 165, render: moment},
    {title: '审核人', dataIndex: 'reviewer_name', width: 110, render: value => value || '—'},
    {title: '处理时间', dataIndex: 'reviewed_at', width: 165, render: moment},
    {title: '审核意见', dataIndex: 'decision_reason', width: 220, render: value => value || '—'},
  ];
  return <div className="pc-page pc-list-page pc-history">
    <PageHead title="申请与认领记录" subtitle="新建通过后建立正式客户；认领通过后成为该客户负责人。查看范围按申请人筛选。" extra={<>
      {can('customer.claim') && <Button onClick={() => open.route('pages/customer-claim/index')}>认领客户</Button>}
      {(can('customer.create_request') || can('customer.create')) && <Button onClick={() => open.route('pages/customer-create/index')}>客户建档</Button>}
      <Button onClick={refresh}>刷新</Button>
    </>} />
    {!allowed ? <Panel title="暂无访问权限"><p>当前身份未获客户历史读取权限。</p></Panel> : <>
      <div className="pc-kpis" role="group" aria-label="申请状态统计">
        {Object.entries(STATUSES).map(([key, label]) => <Kpi key={key} label={label} value={summary?.[key]} unit="项" loading={result.loading} onClick={summary ? () => change({status: key}) : undefined} />)}
      </div>
      <Panel title="申请历史" note="统计按当前范围、类型与搜索条件计算；状态切换只筛选下方记录">
        <FilterBar className="pc-history-filters" label="申请筛选" search={<Input.Search aria-label="搜索申请" placeholder="客户、申请人或申请记录号" maxLength={100} value={draft} onChange={event => setDraft(event.target.value)} onSearch={q => change({q: q.trim()})} />} actions={<Button onClick={() => {setDraft(''); setFilters(initialHistoryFilters());}}>清除筛选</Button>}>
          <div data-toolbar-width={filters.scope.kind === 'self' ? 180 : filters.scope.kind === 'member' && directory.value?.teams.length > 1 ? 510 : 360}>
            {directory.loading ? <span>正在读取可选范围…</span> : directory.error ? <Button onClick={directory.reload}>重试读取范围</Button> : <ScopePicker scope={filters.scope} onChange={scope => change({scope})} directory={directory.value} permission="customer.read" />}
          </div>
          <Select aria-label="申请类型" value={filters.kind} style={{width: '100%'}} options={[{value: '', label: '全部申请'}, {value: 'creation', label: '新建客户'}, {value: 'claim', label: '认领客户'}]} onChange={kind => change({kind})} />
        </FilterBar>
        {directory.error && <p role="alert">人员目录读取失败：{directory.error}。当前筛选保持不变。</p>}
        {directory.value && <p className="pc-muted">当前范围：{scopeLabel(filters.scope, directory.value)}</p>}
        <Tabs activeKey={filters.status} onChange={status => change({status})} items={[{key: '', label: '全部状态'}, ...Object.entries(STATUSES).map(([key, label]) => ({key, label}))]} />
        <div className="pc-results-scroll" role="region" aria-label="申请明细" tabIndex={0}>
          <LoadState loading={result.loading} error={result.error} onRetry={result.reload}>
            <Table size="small" rowKey={row => `${row.kind}:${row.id}`} columns={columns} dataSource={result.value?.items || []} scroll={{x: 1200}}
              locale={{emptyText: '当前条件下没有申请记录'}} pagination={{current: Math.floor(filters.offset / pageSize) + 1, pageSize, total: result.value?.total || 0, showTotal: total => `共 ${total} 条`, showSizeChanger: true, pageSizeOptions: PAGE_SIZE_OPTIONS, onChange: (number, size) => {setPageSize(size); setFilters(value => ({...value, offset: size !== pageSize ? 0 : (number - 1) * size}));}}} />
          </LoadState>
        </div>
      </Panel>
    </>}
  </div>;
}
