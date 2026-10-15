import FilterBar from './pc/FilterBar.jsx';
import {useLocalTablePagination} from './pc/list-pagination.js';
import {Input} from 'antd';
import React, {useMemo, useState} from 'react';
import {Alert, App, Avatar, Button, DatePicker, Pagination, Segmented, Select, Table, Tooltip} from 'antd';
import dayjs from 'dayjs';
import {NavIcon} from './pc/icons.jsx';
import {FollowupDrawer, FollowupStream, VisitOpportunityLinks, initials} from './pc/followups.jsx';
import {Empty2, Kpi, LoadState, PageHead, Panel, ProgressNote, ScopePicker, SourceTag, Tag2, downloadText, fuzzyMatch, toCsv, useAsync, useSessionState} from './pc/ui.jsx';
import {PERIODS, customRange} from './pc/period.js';
import {SOURCE_LABEL, byMember, can, clearCache, clientExportAllowed, collectFollowups, dateLabel, defaultScope, groupBy, identity, isFdeRole, loadDirectory, open, periodRange, relativeDay,
  scopeKey, scopeLabel, scopeMembers, session, summarize, todayKey} from './pc/data.js';
import './pc/visit-records.css';
import {facetValue, filterRecordRows, visitFacets} from './pc/visit-records.js';
import {recordSelection} from './pc/workspace-selection.js';

// 拜访与跟进 · 跟进记录：本人与有权限查看的团队成员的跟进，按时间 / 人员 / 客户 / 商机四个视角看，表格视图可导出（替代多维表格）。
// 同一客户点进去是客户档案里的完整跟进时间轴；保留服务端提供的历史来源快照。
const VIEWS = [{value: 'time', label: '按时间'}, {value: 'person', label: '按人员'}, {value: 'customer', label: '按客户'}, {value: 'opportunity', label: '按商机'}, {value: 'table', label: '表格'}];
const SOURCES = [{value: 'all', label: '全部来源'}, {value: 'sales', label: SOURCE_LABEL.sales}, {value: 'fde', label: SOURCE_LABEL.fde}];

function PaginatedFollowups({visits, name, ...props}) {
  const pagination = useLocalTablePagination(name, visits.map(row => row.id).join(','));
  return <><FollowupStream visits={visits.slice((pagination.current - 1) * pagination.pageSize, pagination.current * pagination.pageSize)} {...props} />
    {visits.length > 0 && <div className="pc-list-pagination"><Pagination {...pagination} total={visits.length} /></div>}</>;
}

function PersonView({visits, members, onSelect, scope}) {
  const rows = byMember(visits, []).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'));
  const [active, setActive] = useState(null);
  const current = rows.find(row => row.id === active) || rows.find(row => row.count) || rows[0];
  if (!rows.length) return <Empty2 title="本期还没有跟进记录" />;
  return <div className="pc-vr-split">
    <ul className="pc-vr-people" aria-label="按人员">
      {rows.map(row => <li key={row.id}><button type="button" className={row.id === current?.id ? 'is-active' : ''} onClick={() => setActive(row.id)}>
        <Avatar size={28}>{initials(row.name)}</Avatar>
        <span className="pc-vr-people-main"><b>{row.name}{row.role && isFdeRole(row.role) && <Tag2 tone="teal">FDE</Tag2>}</b><small>{row.count ? `客户 ${row.customerCount} · 商机 ${row.opportunityCount} · 最近 ${relativeDay(row.lastDate)}` : '本期未跟进'}</small></span>
        <span className={`pc-vr-count${row.count ? '' : ' is-zero'}`}>{row.count}</span>
      </button></li>)}
    </ul>
    <div className="pc-vr-detail">
      {current && <header><b>{current.name}</b><span>{current.count} 条跟进 · {current.customerCount} 家客户 · {current.opportunityCount} 个商机</span></header>}
      <PaginatedFollowups name="visits-person" visits={current?.visits || []} onSelect={onSelect} showPerson={false} emptyText="这位成员本期还没有跟进记录" />
    </div>
  </div>;
}

function GroupView({visits, kind, onSelect, linkedOnly = false}) {
  const pagination = useLocalTablePagination(`visits-${kind}`, visits.map(row => row.id).join(','));
  const groups = groupBy(visits, kind).filter(group => !linkedOnly || group.key !== 'none').sort((a, b) => b.lastDate.localeCompare(a.lastDate) || b.count - a.count);
  const canOpen = row => row.key !== 'none' && row.visits.some(visit => kind === 'customer' ? visit.customerAccess?.[row.key] === true : (visit.opportunityAccess?.[row.key] ?? (visit.opportunityId === row.key && visit.canOpenOpportunity)));
  const columns = [
    {title: kind === 'customer' ? '客户' : '商机', key: 'title', render: (_, row) => <div className="pc-vr-group-title">
      {kind === 'customer'
        ? (canOpen(row) ? <button type="button" className="pc-link" onClick={() => open.customer(row.key)}>{row.title}</button> : <span>{row.title}</span>)
        : (canOpen(row) ? <button type="button" className="pc-link" onClick={() => open.opportunity(row.customerId, row.key)}>{row.title}</button> : <span>{row.title}</span>)}
      {kind === 'opportunity' && <small>{row.customerName}</small>}
    </div>},
    {title: '跟进次数', dataIndex: 'count', key: 'count', width: 96, align: 'right', sorter: (a, b) => a.count - b.count},
    {title: '最近跟进', dataIndex: 'lastDate', key: 'last', width: 120, render: value => relativeDay(value), sorter: (a, b) => a.lastDate.localeCompare(b.lastDate)},
    {title: '跟进人', key: 'people', width: 220, render: (_, row) => <span className="pc-vr-names" title={row.people.join('、')}>{row.people.join('、')}</span>},
    {title: '', key: 'go', width: 120, render: (_, row) => !canOpen(row) ? null : kind === 'customer'
      ? <Button type="link" size="small" onClick={() => open.customer(row.key)}>客户时间轴 →</Button>
      : row.key !== 'none' ? <Button type="link" size="small" onClick={() => open.opportunity(row.customerId, row.key)}>商机详情 →</Button> : null},
  ];
  if (!groups.length) return <Empty2 title="本期还没有跟进记录" />;
  return <Table size="small" rowKey="key" columns={columns} dataSource={groups} pagination={pagination}
    expandable={{expandedRowRender: row => <div className="pc-vr-expanded"><FollowupStream visits={row.visits} onSelect={onSelect} /></div>}} />;
}

const CSV_COLUMNS = [
  {title: '跟进日期', value: v => v.date}, {title: '记录人', value: v => v.recorderName}, {title: '来源', value: v => SOURCE_LABEL[v.source]},
  {title: '客户', value: v => v.customerName}, {title: '商机', value: v => v.opportunityName}, {title: '沟通方式', value: v => v.mode},
  {title: '对接人', value: v => v.contacts}, {title: '沟通内容摘要', value: v => v.content}, {title: '下一步计划摘要', value: v => v.nextAction},
  {title: '协助 FDE', value: v => v.fdeNames.join('、')}, {title: '状态', value: v => v.statusLabel}, {title: '录入时间', value: v => v.createdAt},
];

function TableView({visits, onSelect}) {
  const pagination = useLocalTablePagination('visits-table', visits.map(row => row.id).join(','));
  const columns = [
    {title: '跟进日期', dataIndex: 'date', key: 'date', width: 116, fixed: 'left', sorter: (a, b) => a.date.localeCompare(b.date), defaultSortOrder: 'descend', render: value => <span className="pc-num pc-nowrap">{value}</span>},
    {title: '记录人', key: 'recorder', width: 120, render: (_, v) => <span className="pc-vr-recorder">{v.recorderName}<SourceTag source={v.source} /></span>, sorter: (a, b) => a.recorderName.localeCompare(b.recorderName, 'zh-CN')},
    {title: '客户', key: 'customer', width: 190, render: (_, v) => v.customerId && v.canOpenCustomer !== false ? <button type="button" className="pc-link" onClick={() => open.customer(v.customerId)}>{v.customerName}</button> : v.customerName, sorter: (a, b) => a.customerName.localeCompare(b.customerName, 'zh-CN')},
    {title: '商机', dataIndex: 'opportunityName', key: 'opportunity', width: 150, render: (_, v) => v.opportunityIds.length ? <VisitOpportunityLinks visit={v} /> : <span className="pc-muted">未关联商机</span>},
    {title: '方式', dataIndex: 'mode', key: 'mode', width: 88},
    {title: '沟通内容', dataIndex: 'content', key: 'content', render: value => <Tooltip title={value} placement="topLeft"><span className="pc-clamp-2">{value || '—'}</span></Tooltip>},
    {title: '下一步', dataIndex: 'nextAction', key: 'next', width: 200, render: value => <Tooltip title={value} placement="topLeft"><span className="pc-clamp-2">{value || <span className="pc-muted">未填写</span>}</span></Tooltip>},
    {title: '', key: 'open', width: 64, fixed: 'right', render: (_, v) => <Button type="link" size="small" onClick={() => onSelect(v)}>详情</Button>},
  ];
  if (!visits.length) return <Empty2 title="本期还没有跟进记录" />;
  return <Table size="small" rowKey="id" columns={columns} dataSource={visits} scroll={{x: 1120}} sticky
    pagination={pagination} />;
}

function VisitRecords({page}) {
  const s = session();
  const owner = identity();
  const {message} = App.useApp();
  const options = page?.options || {};
  const entry = recordSelection(options);
  const fromWorkspace = options.from === 'workspace';
  const [period, setPeriod] = useSessionState('records-period', entry.period, fromWorkspace ? page : null);
  const [custom, setCustom] = useSessionState('records-custom', null);
  const [savedScope, setScope] = useSessionState('records-scope', entry.scope, fromWorkspace ? page : null);
  const [view, setView] = useSessionState('records-view', entry.view, fromWorkspace ? page : null);
  const [source, setSource] = useSessionState('records-source', 'all', page);
  const [query, setQuery] = useSessionState('records-query', '', page);
  const [draft, setDraft] = useSessionState('records-draft', query, page);
  const [customer, setCustomer] = useSessionState('records-customer', '', page);
  const [opportunity, setOpportunity] = useSessionState('records-opportunity', '', page);
  const [selected, setSelected] = useState(null);
  const [progress, setProgress] = useState(null);
  const today = todayKey();
  const range = useMemo(() => period === 'custom' && custom ? customRange(custom[0], custom[1]) : periodRange(period === 'custom' ? 'week' : period), [period, custom?.[0], custom?.[1], today]);
  const directory = useAsync(() => loadDirectory(), [identity()]);
  const scope = savedScope || defaultScope(s, directory.value);
  const followups = useAsync(async signal => {
    if (directory.error) throw Error('人员目录加载失败：' + directory.error);
    if (!directory.value) return null;
    setProgress(null);
    return collectFollowups({range, scope, query, directory: directory.value, signal, onProgress: (done, total) => setProgress({done, total})});
  }, [directory.value, directory.error, range.start, range.end, scopeKey(scope), query]);
  const all = followups.value?.items || [];
  const visits = useMemo(() => filterRecordRows(all, {source, customer, opportunity}), [all, source, customer, opportunity]);
  const facets = useMemo(() => visitFacets(all, customer), [all, customer]);
  const opportunityOptions = [{value: 'none', label: '未关联商机'}, ...facets.opportunities];
  const selectionKey = JSON.stringify([owner, range.start, range.end, scopeKey(scope), query, source, customer, opportunity]);
  const selectVisit = visit => setSelected({visit, key: selectionKey});
  const members = directory.value ? scopeMembers(scope, directory.value, s) : [];
  const summary = summarize(visits, scope.kind === 'team' ? members : []);
  const loading = directory.loading || followups.loading || (!followups.value && !followups.error && !directory.error);
  const readError = directory.error || followups.error;
  const partial = Boolean(followups.value?.coverage?.partial);
  const refresh = () => {clearCache(); setSelected(null); directory.reload(); followups.reload();};
  const clearFilters = () => {setSource('all'); setQuery(''); setDraft(''); setCustomer(''); setOpportunity('');};
  const exportCsv = () => {
    if (owner !== identity() || !clientExportAllowed('visit.export') || loading || readError || partial) return;
    if (!visits.length) { message.info('当前没有可导出的记录'); return; }
    downloadText(`跟进记录摘要_${scopeLabel(scope, directory.value, s)}_${range.start}_${range.end}.csv`, toCsv(visits, CSV_COLUMNS), 'text/csv;charset=utf-8');
    message.success(`已导出 ${visits.length} 条跟进摘要`);
  };
  const periodOptions = [...PERIODS.map(p => ({value: p.key, label: p.label})), {value: 'custom', label: '自定义'}];
  return <div className="pc-page pc-list-page pc-records">
    <PageHead title={fromWorkspace && entry.linkedOnly ? (view === 'customer' ? '活跃客户明细' : view === 'opportunity' ? '活跃商机明细' : '跟进记录') : '跟进记录'} subtitle={`${range.title} · ${scopeLabel(scope, directory.value, s)} · ${readError ? '读取失败' : loading ? '读取中' : partial ? `仅加载 ${summary.visits} 条，记录不完整` : `${summary.visits} 条 · 客户 ${summary.customers} 家 · 商机 ${summary.opportunities} 个`}`}
      extra={<><Button aria-label="刷新跟进记录" onClick={refresh}>刷新</Button>{can('visit.create') && <Button type="primary" icon={<NavIcon name="mic" />} onClick={() => open.route('pages/visit-entry/index')}>记录拜访</Button>}</>}
      />
    <section className="pc-kpis pc-vr-kpis" aria-label="跟进概览">
      <Kpi label="跟进记录" value={loading || readError || partial ? null : summary.visits} unit="条" loading={loading} sub={`${range.title} · ${scopeLabel(scope, directory.value, s)}`} tone="primary" />
      <Kpi label="活跃客户" value={loading || readError || partial ? null : summary.customers} unit="家" loading={loading} sub="本期有跟进" />
      <Kpi label="覆盖商机" value={loading || readError || partial ? null : summary.opportunities} unit="个" loading={loading} sub="已关联商机" />
      {scope.kind === 'team' && <Kpi label="跟进成员" value={loading || readError || partial ? null : new Set(visits.map(v => v.recorderId).filter(Boolean)).size} unit="人" loading={loading} sub="本期已读取记录的成员" />}
    </section>
    {directory.error && <div className="pc-error" role="alert"><span>人员目录加载失败：{directory.error}</span><Button size="small" onClick={directory.reload}>重新加载人员目录</Button></div>}
    <FilterBar className="pc-page-filters" label="时间与查看范围" moreLabel="更多条件">
      <Segmented data-toolbar-width={340} value={period} onChange={value => { setPeriod(value); if (value === 'custom' && !custom) setCustom([dayjs(today).subtract(13, 'day').format('YYYY-MM-DD'), today]); }} options={periodOptions} />
      {period === 'custom' && <DatePicker.RangePicker data-toolbar-width={290} size="middle" allowClear={false} value={custom ? [dayjs(custom[0]), dayjs(custom[1])] : null}
        disabledDate={value => value.isAfter(dayjs(today), 'day')}
        onChange={values => values && setCustom([values[0].format('YYYY-MM-DD'), values[1].format('YYYY-MM-DD')])} />}
      {directory.value && <ScopePicker data-toolbar-width={scope.kind === 'member' ? 530 : scope.kind === 'team' ? 340 : 176} scope={scope} directory={directory.value} onChange={setScope} />}
      <Select data-toolbar-width={200} aria-label="筛选客户" showSearch allowClear labelInValue placeholder="全部客户" value={facetValue(customer, facets.customers, '客户')} options={facets.customers} filterOption={(q, option) => fuzzyMatch(option?.label, q)} onChange={option => {setCustomer(option?.value || ''); setOpportunity('');}} />
      <Select data-toolbar-width={200} aria-label="筛选商机" showSearch allowClear labelInValue placeholder="全部商机" value={facetValue(opportunity, opportunityOptions, '商机')} options={opportunityOptions} filterOption={(q, option) => fuzzyMatch(option?.label, q)} onChange={option => setOpportunity(option?.value || '')} />
      <Select data-toolbar-width={140} value={source} onChange={setSource} options={[...SOURCES, {value: 'unknown', label: '来源未记录'}]} aria-label="来源" />
    </FilterBar>
    {partial && <Alert type="warning" showIcon title="当前记录未读取完整" description="请缩小时间或人员范围后重试。完整统计和导出暂不可用，下面仅显示已加载的记录。" />}
    <div className="pc-vr-filter-context pc-muted" aria-live="polite">{customer && `客户：${facetValue(customer, facets.customers, '客户').label} · `}{opportunity && `商机：${facetValue(opportunity, opportunityOptions, '商机').label} · `}{query && `搜索：${query} · `}{source !== 'all' && `${SOURCE_LABEL[source]} · `}按实际跟进日期，列表显示摘要，打开详情查看全文。</div>
    <Panel title={<Segmented value={view} onChange={setView} options={VIEWS} />} bodyClassName={view === 'time' || view === 'person' ? '' : 'is-flush'}>
      <FilterBar className="pc-vr-search" label="跟进内容搜索" search={<Input.Search allowClear maxLength={100} placeholder="搜索客户、商机、人员或完整内容" value={draft} onChange={event => setDraft(event.target.value)} onSearch={value => setQuery(value.trim())} aria-label="搜索跟进记录" />} actions={<>
        <Button onClick={clearFilters}>清除条件</Button>
        <Tooltip title={clientExportAllowed('visit.export') ? '导出当前列表摘要；完整沟通内容请打开详情查看' : '当前账号未获得跟进记录导出权限'}><Button disabled={!clientExportAllowed('visit.export') || loading || !!readError || partial} icon={<NavIcon name="download" />} onClick={exportCsv}>导出摘要</Button></Tooltip>
      </>} />
      {loading && progress?.total > 1 && <ProgressNote done={progress.done} total={progress.total} label="正在读取跟进记录" />}
      {followups.error ? <div className="pc-error" role="alert"><span>{followups.error}</span><span>{scope.kind !== 'self' && <Button size="small" onClick={() => setScope({kind: 'self'})}>切回个人视角</Button>} <Button size="small" onClick={followups.reload}>重试</Button></span></div>
        : <LoadState loading={loading} rows={6}>
          {view === 'time' && <PaginatedFollowups name="visits-time" visits={visits} onSelect={selectVisit} emptyText={query || customer || opportunity || source !== 'all' ? '没有匹配的跟进记录' : '本期还没有跟进记录'} />}
          {view === 'person' && <PersonView visits={visits} members={members} scope={scope} onSelect={selectVisit} />}
          {(view === 'customer' || view === 'opportunity') && <GroupView visits={visits} kind={view} onSelect={selectVisit} linkedOnly={entry.linkedOnly} />}
          {view === 'table' && <TableView visits={visits} onSelect={selectVisit} />}
        </LoadState>}
    </Panel>
    <p className="pc-records-caliber">口径：按实际跟进日期和记录人统计；一条记录关联多个商机时，跟进条数仍只计一次。当前客户与商机选项来自所选时间、人员及搜索条件下已读取的记录；没有可靠来源信息时标为“来源未记录”。</p>
    <FollowupDrawer visit={!loading && !readError && selected?.key === selectionKey ? selected.visit : null} onClose={() => setSelected(null)} />
  </div>;
}

export default function ShenmaVisitRecords(props) {
  if(!can('visit.read')) return <Alert type="warning" title="当前身份没有跟进记录读取权限" />;
  return <VisitRecords {...props} />;
}
