import React, {useEffect, useMemo, useState} from 'react';
import {Alert, Button, Modal, Segmented, Tooltip} from 'antd';
import {SbLabeledSelect, SbStatePanel, SbTable} from '@shandiant/ui-react';
import CustomerBattleMap from './CustomerBattleMap.jsx';
import {SbSearch} from './ImeInput.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {PAGE_SIZE_OPTIONS, useListPageSize} from './pc/list-pagination.js';
import MetricStrip from './pc/MetricStrip.jsx';
import {Kpi, LoadState, PageHead, ScopePicker, Tag2, useAsync, useSessionState} from './pc/ui.jsx';
import {api, amountWan, can, dayKey, loadDirectory, open, readScopeFor, scopeLabel, session, todayKey} from './pc/data.js';
import {CUSTOMER_PERIODS, CUSTOMER_QUADRANTS, CUSTOMER_SEGMENTS, customerRange, initialCustomerFilters, loadCustomerPortfolio, mapPoints, portfolioParams, selectionLabel} from './pc/customer-portfolio.js';
import './customers.css';
import EnterpriseDirectory from './EnterpriseDirectory.jsx';
import {BusinessIdentityTags} from './CustomerIdentityFields.jsx';
import './pc/pc-ui.css';

const QUADRANTS = Object.fromEntries(CUSTOMER_QUADRANTS.map(item => [item.value, item.label]));
const QUADRANT_KEYS = {main_attack: 'attack', customer_asset: 'asset', order_driven: 'spot', customer_resource: 'resource'};
const defaultScope = () => readScopeFor('customer.read').scopes.some(scope => ['teams', 'workspace'].includes(scope)) ? {kind: 'team', teamId: 'all'} : {kind: 'self'};

function FinancialAssets({scope, onClose}) {
  const [period, setPeriod] = useState('year');
  const allowed = can('actual.read');
  const permission = readScopeFor('actual.read');
  const teamAllowed = permission.scopes.some(value => ['teams', 'workspace'].includes(value));
  const params = {scope: teamAllowed && scope.kind !== 'self' ? 'team' : 'self', basis: 'auto', period};
  if (params.scope === 'team') {
    if (scope.kind === 'team' && scope.teamId !== 'all') params.team_id = scope.teamId;
    if (scope.kind === 'member') params.owner_id = scope.memberId;
  }
  const result = useAsync(() => allowed ? api().getCustomerAssets(params) : null, [JSON.stringify(params), allowed]);
  const summary = result.value?.summary;
  const detail = kind => open.route('pages/customer-assets/index?' + new URLSearchParams({...params, kind, basis: result.value?.basis || 'auto'}));
  return <Modal title="财务资产" open width={760} footer={null} onCancel={onClose}>
    <div className="department-ui pc-customer-finance">
      <p className="pc-muted">{params.scope === 'self' ? '本人财务授权范围' : '所选部门／成员的财务授权范围'} · 客户范围、搜索、活跃周期和商机阶段筛选不改变此处财务统计。</p>
      <Segmented value={period} onChange={setPeriod} options={[{value: 'year', label: '本年'}, {value: 'all', label: '历年'}]} />
      {!allowed ? <p>当前没有财务资产查看权限。</p> : <LoadState loading={result.loading} error={result.error} onRetry={result.reload}>
        <MetricStrip items={[
          {key: 'acv', label: '年度合同额 · 万元', value: summary?.acv_amount == null ? null : amountWan(summary.acv_amount), note: '当前范围在推商机金额'},
          {key: 'recognized', label: '确收 · 万元', value: summary?.recognized_amount == null ? null : amountWan(summary.recognized_amount), onClick: () => detail('recognized')},
          {key: 'collection', label: '回款 · 万元', value: summary?.collection_amount == null ? null : amountWan(summary.collection_amount), onClick: () => detail('collection')},
        ]} />
        <p className="pc-muted">确收、回款按已登记实绩统计，不含预测金额；点击数字查看财务明细。</p>
      </LoadState>}
    </div>
  </Modal>;
}

function CustomerOperations({page, viewControl}) {
  const allowed = can('customer.read');
  const [filters, setFilters] = useSessionState('customer-portfolio-v1', initialCustomerFilters(defaultScope()), page);
  const [draft, setDraft] = useState(filters.q);
  const [financeOpen, setFinanceOpen] = useState(false);
  const [cluster, setCluster] = useState(null);
  const [pageSize, setPageSize] = useListPageSize('customers');
  const now = todayKey();
  const range = useMemo(() => customerRange(filters.period), [filters.period, now]);
  const params = useMemo(() => portfolioParams(filters, {limit: pageSize}), [filters, pageSize, now]);
  const directory = useAsync(() => allowed ? loadDirectory('customer.read') : null, [allowed]);
  const metadata = useAsync(() => allowed ? api().getBusinessOptions() : null, [allowed]);
  const result = useAsync(() => allowed ? loadCustomerPortfolio(params) : null, [JSON.stringify(params), allowed]);
  const response = result.value;
  const summary = response?.summary;
  const availability = response?.availability;
  const rows = response?.items || [];
  const points = useMemo(() => mapPoints(response?.map_points || []), [response]);
  const label = scopeLabel(filters.scope, directory.value, session());
  const change = patch => {setCluster(null); setFilters(current => ({...current, ...patch, offset: 0}));};
  useEffect(() => {
    if (draft === filters.q) return;
    const timer = setTimeout(() => setFilters(current => ({...current, q: draft, offset: 0})), 350);
    return () => clearTimeout(timer);
  }, [draft, filters.q, setFilters]);
  useEffect(() => {
    if (response && filters.offset > 0 && filters.offset >= response.total) {
      setFilters(current => ({...current, offset: Math.max(0, Math.ceil(response.total / pageSize) - 1) * pageSize}));
    }
  }, [response, filters.offset, pageSize, setFilters]);
  const clear = () => {setDraft(''); setCluster(null); setFilters(initialCustomerFilters(defaultScope()));};
  const title = selectionLabel(filters.selection, range);
  const openCustomer = row => {if (row.can_open === true || row.canOpen === true) open.customer(row.id);};
  const metric = (key, name, selection, note, permission) => {
    const unavailable = permission ? summary?.[`${permission}_unavailable_count`] : 0;
    const sub = summary?.[key] === null ? '未获该项查看权限' : unavailable ? `${unavailable} 家${permission === 'activity' ? '跟进' : '商机'}未授权，未计入` : note;
    return <Kpi key={key} label={name} unit="家" value={summary?.[key]} loading={result.loading} sub={sub}
      onClick={allowed && !result.error && !result.loading && (!permission || availability?.[permission]) ? () => change({selection}) : undefined}
      tone={filters.selection === selection ? 'primary' : undefined} />;
  };
  const columns = [
    {title: '客户 / 负责人', key: 'customer', width: '43%', render: (_, row) => <div className="ds-customer-identity">
      <button className="ds-text-action ds-customer-name" title={row.name} disabled={row.can_open !== true} onClick={() => openCustomer(row)}>{row.name}</button>
      <BusinessIdentityTags values={row.business_identities} />
      <div className="ds-muted">{row.team_name || '未分组'} · {row.owner_name || '未登记'}{row.level_code ? ` · ${row.level_code}` : ''}</div>
    </div>},
    {title: '经营进展', key: 'progress', render: (_, row) => <div className="pc-customer-progress">
      <div>{row.max_open_probability != null ? <Tag2 tone="primary">最高在推 {row.max_open_probability}%</Tag2> : <span className="pc-muted">{row.opportunity_readable ? '暂无在推商机' : '商机未授权'}</span>}
        {row.has_won && <Tag2 tone="success">已成交</Tag2>}</div>
      <span className="pc-muted">{row.is_active === true ? `${range.name}有跟进` : row.is_active === false ? `${range.name}无跟进` : '跟进未授权'}</span>
    </div>},
    {title: '最近跟进', key: 'visit', width: 110, render: (_, row) => <span className="pc-muted">{dayKey(row.latest_visit_at) || (row.activity_readable ? '暂无正式跟进' : '未授权')}</span>},
  ];
  if (!allowed) return <div className="pc-page pc-customers"><PageHead title="客户经营" /><SbStatePanel state="empty" title="暂无客户查看权限" description="当前身份没有客户读取授权。" /></div>;
  return <div className="pc-page pc-list-page pc-customers">
    <PageHead title="客户经营" subtitle={`${label} · 活跃统计 ${range.start} 至 ${range.today < range.end ? range.today : range.end}`}
      extra={<><Button onClick={() => setFinanceOpen(true)}>财务资产</Button>{can('customer.claim') && <Button onClick={() => open.route('pages/customer-claim/index')}>客户认领</Button>}</>} />
    {viewControl}
    <section className="pc-kpis pc-customer-scope-metrics" aria-label="客户范围统计">
      <Kpi label="客户总库" value={summary?.pool_total} unit="家" loading={result.loading} sub={summary?.pool_total == null ? '按认领目录权限开放' : '可见认领目录 · 独立范围'} />
      {metric('claimed', '已认领客户', 'claimed', '当前授权及筛选范围')}
      {metric('active', `${range.name}活跃客户`, 'active', '所选周期有正式跟进', 'activity')}
      {metric('pipeline_ge_10', '有商机客户', 'pipeline:10', '在推商机 ≥10%', 'opportunity')}
      {metric('pipeline_ge_30', '商机 ≥30% 客户', 'pipeline:30', '按最高在推阶段累计', 'opportunity')}
      {metric('won', '已成交客户', 'won', '有赢单记录 · 可另有在推', 'opportunity')}
    </section>
    <section className="ds-panel ds-customer-workbench" aria-label="客户经营明细">
      <section className="ds-customer-tools">
        <FilterBar label="客户筛选条件" search={<SbSearch value={draft} onChange={setDraft} placeholder="搜索客户或负责人" loading={result.loading} />}
          actions={<><Button type="link" size="small" onClick={clear}>重置</Button><Button size="small" onClick={result.reload}>刷新</Button></>}>
          <SbLabeledSelect label="客户" data-toolbar-width={184} value={filters.selection}
            options={CUSTOMER_SEGMENTS.map(option => ({...option, disabled: Boolean(option.requires && availability && !availability[option.requires])}))}
            onChange={selection => change({selection: selection || 'claimed'})} />
          <SbLabeledSelect label="活跃周期" data-toolbar-width={148} value={filters.period} options={CUSTOMER_PERIODS} onChange={period => change({period: period || 'year'})} />
          <div data-toolbar-width={filters.scope.kind === 'member' ? 480 : 310}>
            {directory.value ? <ScopePicker permission="customer.read" scope={filters.scope} directory={directory.value} onChange={scope => change({scope})} /> : <span className="pc-muted">{directory.error ? '人员范围暂不可用' : '正在读取人员范围…'}</span>}
          </div>
          <SbLabeledSelect label="业务身份" value={filters.businessIdentity || 'all'} options={[{value: 'all', label: '全部客户'}, {value: 'partner', label: '同时为合作伙伴'}]} onChange={businessIdentity => change({businessIdentity: businessIdentity || 'all'})} />
          <SbLabeledSelect label="象限" value={filters.quadrant} disabled={availability?.map === false} options={CUSTOMER_QUADRANTS} onChange={quadrant => change({quadrant: quadrant || '', unscored: false})} />
          <SbLabeledSelect label="优先级" value={filters.level} options={[{value: '', label: '全部'}, ...(metadata.value?.customer?.level_code || []).map(value => ({value, label: value}))]} onChange={level => change({level: level || ''})} />
          <SbLabeledSelect label="计划" value={filters.plan} disabled={availability?.opportunity === false} options={[{value: '', label: '全部'}, {value: 'current_year', label: '当年计划成单'}, {value: 'long_term', label: '计划长期运作'}]} onChange={plan => change({plan: plan || ''})} />
          <SbLabeledSelect label="金额" value={filters.amount} disabled={availability?.opportunity === false} options={[{value: '', label: '全部'}, ...(metadata.value?.map_amount_ranges || []).map(item => ({value: `${item.min ?? ''}:${item.max ?? ''}`, label: item.label}))]} onChange={amount => change({amount: amount || ''})} />
          <SbLabeledSelect label="评分" value={filters.unscored ? 'unscored' : ''} disabled={availability?.map === false} options={[{value: '', label: '全部'}, {value: 'unscored', label: '仅未评分'}]} onChange={value => change({unscored: value === 'unscored', quadrant: ''})} />
        </FilterBar>
        {directory.error && <Alert type="warning" title="人员筛选读取失败" description={directory.error} action={<Button size="small" onClick={directory.reload}>重试</Button>} />}
        {metadata.error && <Alert type="warning" title="筛选选项读取失败" description={metadata.error} action={<Button size="small" onClick={metadata.reload}>重试</Button>} />}
      </section>
      <div className="pc-customer-caliber"><span>按当前授权可见的正式跟进与商机统计；活跃与商机分别判断。</span>
        <Tooltip title="统计按客户去重，只统计有权读取的跟进与商机事实。阶段取当前在推商机最高概率，≥30%包含50%、70%、90%；赢单另计已成交，丢单不计在推。数字卡保留共同筛选，点击切换对应客户范围。客户总库使用独立认领目录授权，不随此处筛选变化。"><button className="pc-overview-help" aria-label="客户统计口径">ⓘ</button></Tooltip>
      </div>
      {result.error ? <div className="pc-customer-load-state"><SbStatePanel state="error" title="客户范围加载失败" description={result.error} onRetry={result.reload} /></div> :
        <div className="ds-customer-workspace-wrap"><div className="ds-customer-workspace">
          <aside className="ds-customer-map-panel" aria-label="作战地图">
              <header><h2>作战地图</h2><span className="ds-muted ds-customer-map-note">{result.loading ? '正在读取…' : availability?.map ? `${points.length} 家已评分 · ${response?.unscored_count} 家未评分${response?.map_unavailable_count ? ` · ${response.map_unavailable_count} 家地图未授权` : ''}` : '地图未授权'}</span></header>
            {availability?.map === false ? <SbStatePanel state="empty" title="暂无地图查看权限" description="客户列表仍按客户读取授权展示。" /> :
              <CustomerBattleMap points={points} thresholds={{potential: 7, relationship: 7}} unrated={response?.unscored_count || 0} loading={result.loading}
                zoomQuadrant={filters.quadrant || null} onZoomChange={quadrant => change({quadrant: quadrant || '', unscored: false})}
                onPointClick={openCustomer} onClusterClick={setCluster} onUnratedClick={() => change({unscored: true, quadrant: ''})}
                emptyText={response?.map_unavailable_count === response?.total && response?.total ? '此范围客户的地图未授权，可在右侧查看客户列表。' : response?.total ? '此范围客户尚未评分，可在右侧查看完整列表。' : '当前范围暂无客户'} emptyAction={{label: '重置筛选', onClick: clear}} />}
          </aside>
          <section className="ds-customer-list" aria-label="客户列表">
            <header><h2>{title} <span className="ds-customer-count">{result.loading ? '—' : response?.total ?? '—'}</span></h2>{filters.unscored && <Button type="link" size="small" onClick={() => change({unscored: false})}>显示全部评分</Button>}</header>
            <div className="ds-customer-table-viewport">
              <SbTable rowKey="id" density="compact" columns={columns} rows={rows} state={result.loading ? 'loading' : !rows.length ? 'empty' : 'normal'}
                emptyTitle="当前条件下没有客户" onClear={clear} scrollX={590}
                pagination={{current: Math.floor(filters.offset / pageSize) + 1, pageSize, total: response?.total || 0, pageSizeOptions: PAGE_SIZE_OPTIONS, onPageSizeChange: size => {setPageSize(size); setFilters(current => ({...current, offset: 0}));},
                  onChange: (number, size = pageSize) => {if (size !== pageSize) {setPageSize(size); setFilters(current => ({...current, offset: 0}));} else setFilters(current => ({...current, offset: (number - 1) * pageSize}));}}}
                actions={row => <Button type="link" size="small" disabled={row.can_open !== true} aria-label={`查看${row.name}`} onClick={() => openCustomer(row)}>查看</Button>} actionsWidth={64}
                expandable={{columnWidth: 28, expandedRowRender: row => <div className="ds-customer-expanded"><strong>{row.name}</strong>
                  <p>在推商机：{row.open_count ?? '未授权'}{row.open_count != null ? ' 个' : ''} · 所属象限：{row.map_readable === false || availability?.map === false ? '地图未授权' : QUADRANTS[QUADRANT_KEYS[row.quadrant_code] || row.quadrant_code] || '未评分'}</p>
                  <p>在推金额：{row.acv_amount == null ? row.opportunity_readable === false ? '未授权' : '金额未完整登记' : `${amountWan(row.acv_amount)} 万元`}</p>
                  <p>{row.is_active === true ? `${range.name}有正式跟进` : row.is_active === false ? `${range.name}没有正式跟进` : '跟进情况未授权'}{row.has_won ? ' · 已有赢单记录' : ''}</p>
                </div>}} />
            </div>
          </section>
        </div></div>}
    </section>
    {financeOpen && <FinancialAssets scope={filters.scope} onClose={() => setFinanceOpen(false)} />}
    <Modal title="此处的客户" open={Boolean(cluster?.length)} footer={null} onCancel={() => setCluster(null)}>
      {(cluster || []).map(point => <Button className="ds-map-candidate" key={point.id} block disabled={point.canOpen === false} onClick={() => openCustomer(point)}>{point.name}<span className="ds-muted"> · {point.summary}</span></Button>)}
    </Modal>
  </div>;
}

export default function Customers({page}) {
  const [view, setView] = useSessionState('customer-directory-view-v1', 'operations', page);
  const viewControl = <div className="pc-customer-view-switch"><Segmented aria-label="客户经营视图" value={view} onChange={setView} options={[{value:'operations',label:'客户经营'}, {value:'directory',label:'企业档案'}]} />{view === 'directory' && <span className="ds-muted">包含客户与合作伙伴；完整档案按你的权限开放</span>}</div>;
  return view === 'directory' ? <EnterpriseDirectory page={page} viewControl={viewControl} /> : <CustomerOperations page={page} viewControl={viewControl} />;
}
