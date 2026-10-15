import React, {useEffect} from 'react';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import {Button, DatePicker, Select, Table} from 'antd';
import dayjs from 'dayjs';
import {Input} from './ImeInput.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {can, identity, open} from './pc/data.js';
import {fuzzyMatch, LoadState, PageHead, Panel, useAsync, useSessionState} from './pc/ui.jsx';
import {creationScopeLabel, creationScopeOption, creationTimeLabel, initialCreationFilters, loadCreationHistory, loadCreationDirectory, validCreationOffset} from './pc/opportunity-history.js';
import './opportunity-history.css';

export default function OpportunityHistory({page}) {
  const owner = identity(), allowed = can('opportunity.read');
  const [pageSize, setPageSize] = useListPageSize('opportunity-history');
  const [filters, setFilters] = useSessionState('opportunity-creation-history', initialCreationFilters(), page);
  const [draft, setDraft] = useSessionState('opportunity-creation-search', filters.q, page);
  const directory = useAsync(signal => allowed ? loadCreationDirectory(signal) : null, [owner, allowed]);
  const filterKey = JSON.stringify(filters);
  const result = useAsync(() => allowed ? loadCreationHistory({...filters, pageSize}) : null, [owner, allowed, filterKey, pageSize]);
  const offset = result.value ? validCreationOffset(filters.offset, result.value.total, pageSize) : filters.offset;
  const correctingPage = offset !== filters.offset;
  useEffect(() => {
    if (correctingPage) setFilters(previous => JSON.stringify(previous) === filterKey ? {...previous, offset} : previous);
  }, [correctingPage, offset, filterKey, setFilters]);
  const change = patch => setFilters(previous => ({...previous, ...patch, offset: 0}));
  const rows = result.value?.items || [];
  const columns = [
    {title: '商机名称', dataIndex: 'name', width: 240, fixed: 'left', render: (name, row) => <Button type="link" className="opp-history-name" onClick={() => open.opportunity(row.customer_id, row.id)}>{name}</Button>},
    {title: '所属客户', dataIndex: 'customer_name', width: 220, render: (name, row) => row.can_read_customer ? <Button type="link" className="opp-history-name" onClick={() => open.customer(row.customer_id)}>{name}</Button> : <span>{name || '客户资料未授权'}</span>},
    {title: '创建人', dataIndex: 'creator_name', width: 140, render: value => value || <span className="pc-muted">创建人未记录</span>},
    {title: '创建时间', key: 'created', width: 190, render: (_, row) => <span className={!row.business_created_on ? 'pc-muted' : undefined}>{creationTimeLabel(row)}</span>},
    {title: '当前负责人', dataIndex: 'owner_name', width: 140, render: value => value || '未分配'},
  ];
  return <div className="pc-page pc-list-page opp-creation-history">
    <PageHead title="商机创建历史" subtitle="按创建人查看本人和部门的记录；转交商机不会改变创建人。" extra={<>
      {allowed && <Button aria-label="刷新创建历史" onClick={() => {directory.reload(); result.reload();}}>刷新</Button>}
      {can('opportunity.create') && <Button type="primary" onClick={() => open.route('pages/opportunity-create/index')}>新增商机</Button>}
    </>} />
    {!allowed ? <Panel title="暂无访问权限"><p>当前身份未获商机读取权限。</p></Panel> : <Panel title="创建记录" note={result.value ? `共 ${result.value.total} 条` : undefined}>
      <FilterBar label="创建历史筛选" className="pc-history-filters" search={<Input.Search aria-label="搜索创建历史" placeholder="商机或客户名称" maxLength={100} value={draft} onChange={event => setDraft(event.target.value)} onSearch={q => change({q: q.trim()})} />} actions={<Button onClick={() => {setDraft(''); setFilters(initialCreationFilters());}}>清除筛选</Button>}>
        <div data-toolbar-width={280}>
          {directory.loading ? <span>正在读取可选范围…</span> : directory.error ? <Button onClick={directory.reload}>重试读取范围</Button> : <Select aria-label="创建人范围" style={{width: '100%'}} showSearch popupMatchSelectWidth={320} labelInValue value={creationScopeOption(filters.scope, directory.value)} filterOption={(query, option) => fuzzyMatch(option?.label, query)}
            options={[{label: '查看范围', options: [{value: 'self', label: '本人创建'}, {value: 'team:all', label: '全部可见创建记录'}]},
              {label: '按当前部门', options: (directory.value?.teams || []).map(team => ({value: `team:${team.id}`, label: team.name}))},
              {label: '按创建人', options: (directory.value?.members || []).map(member => ({value: `member:${member.id}`, label: `${member.name}${member.is_active ? '' : '（已停用）'}`}))}]}
            onChange={({value}) => change({scope: value === 'self' ? {kind: 'self'} : value.startsWith('team:') ? {kind: 'team', teamId: value.slice(5)} : {kind: 'member', memberId: value.slice(7)}})} />}
        </div>
        <DatePicker.RangePicker data-toolbar-width={280} aria-label="创建日期范围" placeholder={['创建开始日期', '创建结束日期']} value={filters.created_from && filters.created_to ? [dayjs(filters.created_from), dayjs(filters.created_to)] : null} onChange={(_, dates) => change({created_from: dates?.[0] || '', created_to: dates?.[1] || ''})} />
      </FilterBar>
      {directory.error && <p role="alert">创建人目录读取失败：{directory.error}。当前筛选保持不变。</p>}
      <div className="opp-history-context pc-muted">
        <span>创建人范围：{creationScopeLabel(filters.scope, directory.value)}</span>
        <span>{filters.created_from ? `创建日期：${filters.created_from} — ${filters.created_to}` : '创建日期：全部'}</span>
      </div>
      <p className="pc-muted opp-history-note">部门按创建人当前有效任职筛选；历史日期不明的记录标记为“日期未记录”，选择日期范围后不计入结果。</p>
      <div className="pc-results-scroll" role="region" aria-label="商机创建明细" tabIndex={0}>
        <LoadState loading={result.loading || correctingPage} error={result.error} onRetry={result.reload}>
          <Table size="small" rowKey="id" columns={columns} dataSource={rows} scroll={{x: 930}} locale={{emptyText: '当前条件下没有商机创建记录'}}
            pagination={{current: Math.floor(filters.offset / pageSize) + 1, pageSize, total: result.value?.total || 0, showSizeChanger: true, pageSizeOptions: PAGE_SIZE_OPTIONS, showTotal: total => `共 ${total} 条`, onChange: (number, size) => {setPageSize(size); setFilters(previous => ({...previous, offset: size !== pageSize ? 0 : (number - 1) * size}));}}} />
        </LoadState>
      </div>
    </Panel>}
  </div>;
}
