import MetricStrip from './pc/MetricStrip.jsx';
import FilterBar from './pc/FilterBar.jsx';
import {SbSearch} from './ImeInput.jsx';
import React, {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {Popover, Button, Select} from 'antd';
import {SbLabeledSelect, SbSegmented, SbStatePanel, SbStatusTag, SbTable} from '@shandiant/ui-react';
import {LabeledSearchSelect, PageHead} from './pc/ui.jsx';
import {identity} from './pc/data.js';
import './opportunities.css';
import './pc/pc-ui.css';

// 商机页（原 pages/workbench/index）。原则：总览一行、每格一行、点整行进详情，其余进展开行。业务数据、筛选、分页都还是原 Page 的。
export function supportsOpportunities(page, data = {}) {
  return Boolean(page && page.route === 'pages/workbench/index' && !data.isFde && data.dataReady);
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const TONE_LABEL = { good: '向好', watch: '需关注', bad: '转差', pending: '待评估' };
const ACTIVITY_WINDOWS = [
  { value: 'today', label: '今天' },
  { value: '3d', label: '近3天' },
  { value: 'week', label: '本周' },
  { value: 'month', label: '本月' },
  { value: 'all', label: '全部' },
];
const SCOPE_OPTIONS = [{ value: 'self', label: '我的' }, { value: 'team', label: '授权范围' }];
export const DEFAULT_OPPORTUNITY_COLUMNS = ['name', 'stage', 'owner', 'amount', 'signing', 'signal', 'money', 'close'];
export const OPPORTUNITY_COLUMN_OPTIONS = [
  { value: 'name', label: '商机 / 客户' },
  { value: 'stage', label: '阶段' },
  { value: 'owner', label: '负责人' },
  { value: 'amount', label: 'ACV / 金额' },
  { value: 'signing', label: '签约方式' },
  { value: 'signal', label: '状态' },
  { value: 'money', label: '确收 / 回款' },
  { value: 'close', label: '预计关单' },
  { value: 'product', label: '产品线' },
];
const DEFAULT_VIEW_ID = '__default__';
const LOCAL_VIEW_PREFIX = 'sales-web:pc-local:opportunity-views';
export const opportunityViewStorageKey = value => `${LOCAL_VIEW_PREFIX}:${value || ''}`;
const METRIC_HELP = <div className="ds-opp-help">列表筛选同时作用于总览和列表。<br />总商机：完整筛选结果，包含在推、已成单和已丢单，不受分页影响。<br />统计时间只影响已成单、活跃和新增数量，不改变商机范围。<br />已成单按实际成单日期；活跃按正式跟进日期去重并排除已丢单；新增按原始建单日期。<br />全年等于所选年份四季全选；全部年份才是跨年。缺少实际日期的记录不计入年、季度统计。</div>;
const money = v => (v == null || v === '' || v === '—' || v === '未登记') ? <span className="ds-muted">未登记</span> : v;
const signingLabel = row => row?.sales_channel === 'direct' ? '客户直签'
  : row?.sales_channel === 'partner' ? (row.partner_name ? `伙伴转售 · ${row.partner_name}` : '伙伴转售')
  : row?.partner_name ? row.partner_name : '未填写';
const activeOpportunity = row => ['open', 'active', 'in_progress'].includes(String(row?.status || '').toLowerCase());

// The bridge contract tests call the presentation function without a React
// renderer. Keep that read-only contract usable while the browser render still
// gets the real hook implementations.
const hooksReady = () => Boolean(React.__SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED?.ReactCurrentDispatcher?.current);
const safeState = initial => hooksReady() ? useState(initial) : [typeof initial === 'function' ? initial() : initial, () => {}];
const safeMemo = (factory, deps) => hooksReady() ? useMemo(factory, deps) : factory();
const safeEffect = (effect, deps) => { if (hooksReady()) useEffect(effect, deps); };
const safeCallback = (callback, deps) => hooksReady() ? useCallback(callback, deps) : callback;

function usePagePreference(page, owner, name, initial) {
  const read = () => page?._pcOpportunityPreferences?.owner === owner ? page._pcOpportunityPreferences[name] ?? initial : initial;
  const [saved, setSaved] = safeState(() => ({owner, value: read()}));
  const value = saved.owner === owner ? saved.value : read();
  return [value, next => {
    const resolved = typeof next === 'function' ? next(value) : next;
    if (page) {
      if (page._pcOpportunityPreferences?.owner !== owner) page._pcOpportunityPreferences = {owner};
      page._pcOpportunityPreferences[name] = resolved;
    }
    setSaved({owner, value: resolved});
  }];
}

function readLocalViews(key) {
  try {
    const storage = globalThis.window?.localStorage || globalThis.localStorage;
    if (!storage) return [];
    const value = JSON.parse(storage.getItem(key) || '[]');
    return Array.isArray(value) ? value.filter(view => view && typeof view.id === 'string' && typeof view.name === 'string' && Array.isArray(view.columns)) : [];
  } catch (_) { return []; }
}

function useLocalViews() {
  // Older bridge tests and preview shells may not expose the optional identity
  // helper. Keep the view state local to that shell instead of crashing the page;
  // the live runtime always supplies the workspace/user/session identity.
  const key = opportunityViewStorageKey(typeof identity === 'function' ? identity() : 'anonymous');
  const [views, setViews] = safeState(() => readLocalViews(key));
  const [error, setError] = safeState('');
  safeEffect(() => { setViews(readLocalViews(key)); setError(''); }, [key]);
  const update = safeCallback(next => {
    const resolved = typeof next === 'function' ? next(views) : next;
    try {
      const storage = globalThis.window?.localStorage || globalThis.localStorage;
      if (!storage) throw Error('storage unavailable');
      storage.setItem(key, JSON.stringify(resolved));
      setViews(resolved); setError(''); return true;
    } catch (_) { setError('浏览器未能保存列布局，请检查存储空间或浏览器设置后重试。'); return false; }
  }, [key, views]);
  return [views, update, key, error];
}

export function opportunityGroupsInServerOrder(groups = [], rows = []) {
  // The API orders the complete authorized result before pagination. Only add
  // contiguous headings here; regrouping/sorting loaded rows changes that order.
  const labels = new Map(groups.flatMap(group => (group.items || []).map(row => [row.id, group])));
  return rows.reduce((result, row) => {
    const source = labels.get(row.id) || {key: 'undated', label: '日期待完善'};
    const phase = activeOpportunity(row) ? '进行中' : '已结束';
    const bucket = `${source.key}:${phase}`;
    let group = result.at(-1);
    if (group?.bucket !== bucket) {
      group = {key: `${bucket}:${result.length}`, bucket, label: `${source.label} · ${phase}`, items: []};
      result.push(group);
    }
    group.items.push(row);
    return result;
  }, []);
}

export function OpportunityCloseFilter({data: d, invoke}) {
  const selected = d.listQuarter || {isAllYears: true, quarters: []};
  const years = d.summaryYearOptions || [];
  const year = selected.isAllYears ? 'all' : selected.year;
  const quick = d.opportunityCloseOptions || [];
  const label = d.opportunityCloseIndex > 0 ? quick[d.opportunityCloseIndex]?.label : selected.isAllYears ? '不限' : selected.label;
  const setQuarter = (nextYear, quarters) => invoke('setOpportunityQuarterSelection', {dataset: {scope: 'list'}, detail: {year: nextYear, quarters}});
  return <Popover trigger="click" placement="bottomRight" classNames={{root: 'department-ui pc-view-popover'}} content={
    <div className="ds-opp-close-filter" role="group" aria-label="预计关单筛选">
      <SbLabeledSelect label="快捷时间" value={d.opportunityCloseIndex || 0} options={quick.map((option, index) => ({value: index, label: option.label}))} onChange={index => invoke('selectOpportunityClosePeriod', {dataset: {index: index ?? 0}})} />
      <SbLabeledSelect label="关单年份" value={year} options={years} onChange={value => { const next = value ?? 'all'; setQuarter(next, next === 'all' ? [] : [1, 2, 3, 4]); }} />
      {year !== 'all' && <SbLabeledSelect label="关单季度" mode="multiple" value={selected.quarters} options={[1, 2, 3, 4].map(value => ({value, label: `Q${value}`}))} onChange={values => setQuarter(year, values.length ? values : [1, 2, 3, 4])} />}
      <span className="ds-muted">按预计关单时间筛选商机；不限制跟进发生时间。</span>
    </div>
  }><Button className="ds-opp-close-trigger" aria-label={`预计关单：${label || '不限'}`}><span>预计关单</span><b>{label || '不限'}</b><span>⌄</span></Button></Popover>;
}

export function normalizeOpportunityColumns(columns) {
  const allowed = new Set(OPPORTUNITY_COLUMN_OPTIONS.map(option => option.value));
  const values = [...new Set((Array.isArray(columns) ? columns : DEFAULT_OPPORTUNITY_COLUMNS).filter(value => allowed.has(value)))];
  return values.includes('name') ? values : ['name', ...values];
}

export default function Opportunities({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const [views, updateViews, viewIdentity, viewError] = useLocalViews();
  const [activeViewId, setActiveViewId] = usePagePreference(page, viewIdentity, 'viewId', DEFAULT_VIEW_ID);
  const [visibleColumns, setVisibleColumns] = usePagePreference(page, viewIdentity, 'columns', DEFAULT_OPPORTUNITY_COLUMNS);
  const [viewName, setViewName] = usePagePreference(page, viewIdentity, 'viewName', '');
  const groups = safeMemo(() => opportunityGroupsInServerOrder(d.opportunityGroups || [], d.opportunityItems || []), [d.opportunityGroups, d.opportunityItems]);
  const listState = groups.length ? 'normal' : d.opportunityListError ? 'error' : d.opportunityListLoading ? 'loading' : 'empty';
  const results = hooksReady() ? useRef(null) : {current: null};
  const scrollKey = JSON.stringify([viewIdentity, d.executionScope, d.executionTeamKey, d.opportunityOwnerIndex, d.opportunityQuery, d.opportunitySelectedStages, d.opportunityCloseIndex, d.opportunityGradeIndex, d.activityWindow, d.listQuarter]);
  safeEffect(() => {
    if (listState === 'normal' && results.current) {
      const saved = page?._pcOpportunityScroll;
      results.current.scrollTop = saved?.key === scrollKey ? saved.top : 0;
    }
  }, [scrollKey, listState]);
  const board = d.opportunityDataReady ? d.opportunityBoard : {};
  const sq = d.summaryQuarter || { year: '', quarters: [], options: [] };
  const scopeOptions = d.executionScopeOptions || SCOPE_OPTIONS;
  const periodLabel = sq.isAllYears ? '全部年份' : `${sq.year}年${sq.quarters.length === 4 ? '全年' : sq.quarters.map(value => `Q${value}`).join('、')}`;
  const savedView = views.find(view => view.id === activeViewId);
  const selectView = id => {
    setActiveViewId(id);
    const view = views.find(item => item.id === id);
    setVisibleColumns(normalizeOpportunityColumns(view?.columns || DEFAULT_OPPORTUNITY_COLUMNS));
    setViewName(view?.name || '');
  };
  const saveView = () => {
    const name = viewName.trim() || '未命名列布局';
    const columns = normalizeOpportunityColumns(visibleColumns);
    if (activeViewId !== DEFAULT_VIEW_ID && savedView) {
      if (!updateViews(items => items.map(item => item.id === activeViewId ? {...item, name, columns} : item))) return;
      setViewName(name);
      return;
    }
    const id = globalThis.crypto?.randomUUID?.() || `view-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    if (!updateViews(items => [...items, {id, name, columns}])) return;
    setActiveViewId(id);
    setViewName(name);
  };
  const renameView = () => {
    if (activeViewId === DEFAULT_VIEW_ID || !savedView || !viewName.trim()) return;
    const name = viewName.trim();
    if (!updateViews(items => items.map(item => item.id === activeViewId ? {...item, name} : item))) return;
    setViewName(name);
  };
  const deleteView = () => {
    if (activeViewId === DEFAULT_VIEW_ID) return;
    if (!updateViews(items => items.filter(item => item.id !== activeViewId))) return;
    setActiveViewId(DEFAULT_VIEW_ID);
    setVisibleColumns(DEFAULT_OPPORTUNITY_COLUMNS);
    setViewName('');
  };
  const pickQuarter = value => call('setOpportunityQuarterSelection', {dataset: {scope: 'summary'}, detail: {year: sq.year, quarters: value === 'all' ? [1, 2, 3, 4] : [value]}});
  const changeStages = values => call('setOpportunityStages', {detail: {value: values || []}});
  const columnDefinitions = [
    { title: '商机 / 客户', key: 'name', width: 280, render: (_, r) => <div className="ds-opp-name"><b>{r.name}{r.gradeLabel && <span className={`ds-opp-grade ds-opp-grade-${r.gradeCode}`}>{r.gradeLabel}</span>}</b><span className="ds-muted">{r.customer_name}</span></div> },
    { title: '阶段', key: 'stage', width: 150, render: (_, r) => <span className="ds-opp-stage">{r.stageName}{r.probabilityText && <span className="ds-muted"> · {r.probabilityText}</span>}</span> },
    { title: '负责人', key: 'owner', width: 145, render: (_, r) => <div className="ds-opp-owner"><b>{r.owner || '待分配'}</b><span className="ds-muted">{r.team || '待分配团队'}</span></div> },
    { title: 'ACV / 金额', key: 'amount', width: 125, align: 'right', render: (_, r) => money(r.amountLabel || r.amountText || r.amount) },
    { title: '签约方式', key: 'signing', width: 150, render: (_, r) => signingLabel(r) },
    { title: '状态', key: 'signal', width: 110, render: (_, r) => { const t = toneOf(r.signal?.tone); return <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={r.signal?.detail} />; } },
    { title: '确收 / 回款', key: 'money', width: 160, align: 'right', render: (_, r) => <span className="ds-opp-money">{money(r.recognizedLabel)}<span className="ds-muted"> / </span>{money(r.collectionLabel)}</span> },
    { title: '预计关单', key: 'close', width: 120, render: (_, r) => r.closeLabel || <span className="ds-muted">未登记</span> },
    { title: '产品线', key: 'product', width: 130, render: (_, r) => r.productLineLabel || '未填写' },
  ];
  const columns = normalizeOpportunityColumns(visibleColumns).map(key => columnDefinitions.find(column => column.key === key));
  const expanded = r => <div className="ds-opp-expanded"><span>产品线：{r.productLineLabel || '未登记'}</span><span>负责人：{r.team} · {r.owner}</span><span>依据：{r.signal?.label} · {r.signal?.detail}</span></div>;
  const moveColumn = (index, offset) => {
    const next = [...visibleColumns];
    if (!next[index + offset]) return;
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    setVisibleColumns(next);
  };
  const viewSettings = <div className="pc-view-settings"><div className="ds-opp-views" aria-label="本机列布局">
          <select aria-label="列布局" value={activeViewId} onChange={event => selectView(event.target.value)}>
            <option value={DEFAULT_VIEW_ID}>默认列布局</option>
            {views.map(view => <option key={view.id} value={view.id}>{view.name}</option>)}
          </select>
          <input aria-label="列布局名称" value={viewName} onChange={event => setViewName(event.target.value)} placeholder="本机列布局名称" maxLength={40} />
          <Button size="small" onClick={saveView}>保存列布局</Button>
          {activeViewId !== DEFAULT_VIEW_ID && <Button size="small" onClick={renameView}>重命名</Button>}
          {activeViewId !== DEFAULT_VIEW_ID && <Button size="small" danger onClick={deleteView}>删除</Button>}
          <span className="ds-opp-local-note">仅保存本机可见列和顺序，不保存筛选条件</span>
        </div>
        {viewError && <span role="alert" className="ds-opp-note">{viewError}</span>}
        <label className="pc-column-filter"><span>可见列</span><Select aria-label="可见列" mode="multiple" maxTagCount={1} value={visibleColumns} options={OPPORTUNITY_COLUMN_OPTIONS.map(option => ({value: option.value, label: option.label}))} onChange={value => setVisibleColumns(normalizeOpportunityColumns(value))} style={{width: 210}} /></label>
        <div className="ds-opp-column-order" aria-label="列顺序">{visibleColumns.map((key, index) => {
          const label = OPPORTUNITY_COLUMN_OPTIONS.find(option => option.value === key)?.label;
          return <div key={key}><span>{label}</span><Button size="small" disabled={index === 0} aria-label={`${label}前移`} onClick={() => moveColumn(index, -1)}>↑</Button><Button size="small" disabled={index === visibleColumns.length - 1} aria-label={`${label}后移`} onClick={() => moveColumn(index, 1)}>↓</Button></div>;
        })}</div></div>;
  if (d.accessBlocked) return <SbStatePanel state="forbidden" description={d.accessMessage} />;
  return <div className="pc-page pc-list-page pc-opportunities">
    <PageHead title="商机管理" subtitle="统一查看商机、经营阶段与跟进状态" />
    <section className="ds-opps" aria-label="商机">
    <section className="ds-panel ds-opp-board" aria-label="商机总览">
      <MetricStrip loading={d.opportunityOverviewLoading}
        extra={(d.summaryYearOptions || []).length > 1 && <SbSegmented aria-label="年份" value={d.summaryYearIndex} options={(d.summaryYearOptions || []).map((o, i) => ({ value: i, label: o.label }))} onChange={i => call('changeQuarterYear', { dataset: { scope: 'summary' }, detail: { value: i } })} />}
        periods={sq.isAllYears ? [] : [{ value: 'all', label: '全年' }, ...(sq.options || []).map(q => ({ value: q.value, label: q.label }))]}
        period={sq.quarters.length === 1 ? sq.quarters[0] : 'all'}
        onPeriodChange={pickQuarter}
        caliber={METRIC_HELP}
        items={[
          { key: 'total', label: '总商机', value: board.total ?? null, note: '筛选范围内全部状态' },
          { key: 'active', label: '活跃商机', value: board.active ?? null, note: `${periodLabel}有正式跟进` },
          { key: 'won', label: '已成单', value: board.won ?? null, note: `${periodLabel}实际成单` },
          { key: 'new', label: '新增商机', value: board.newCount ?? null, note: `${periodLabel}新建` },
        ]} />
      {d.opportunityDataError && <p className="ds-opp-note" role="alert">{d.opportunityDataError} <Button type="link" size="small" onClick={() => call('applyOpportunitySummary')}>重试</Button></p>}
    </section>
    <section className="ds-panel ds-opp-list" aria-label="商机列表">
      <FilterBar className="ds-opp-tools" label="商机筛选" search={<SbSearch value={d.opportunityQuery || ''} onChange={value => call('searchOpportunities', { detail: { value } })} onSearch={() => call('applyOpportunityFilters')} placeholder="搜索客户、商机或产品线" loading={d.opportunityListLoading} />} actions={<>{d.opportunityFilterActive && <Button type="link" size="small" onClick={() => call('resetOpportunityFilters')}>清除筛选</Button>}<Popover trigger="click" placement="bottomRight" classNames={{root: 'department-ui pc-view-popover'}} content={viewSettings}><Button>列设置</Button></Popover>{d.canCreateOpportunity && <Button type="primary" onClick={() => call('createOpportunity')}>新增商机</Button>}</>}>
        <SbLabeledSelect label="跟进时间" data-toolbar-width={176} value={d.activityWindow || 'all'} options={(d.activityWindowOptions || ACTIVITY_WINDOWS).map(option => ({ value: option.value, label: option.label }))} onChange={value => { const options = d.activityWindowOptions || ACTIVITY_WINDOWS; const index = options.findIndex(option => option.value === value); if (index >= 0) call('changeActivityWindow', { detail: { value: index } }); }} width={130} />
        {d.canViewTeam && scopeOptions.length > 1 && <SbLabeledSelect label="范围" value={d.executionScope || 'self'} options={scopeOptions} onChange={value => { const index = scopeOptions.findIndex(option => option.value === value); if (index >= 0) call('changeExecutionScope', { detail: { value: index } }); }} width={130} />}
        {d.canViewTeam && d.executionScope !== 'self' && <LabeledSearchSelect label="团队" value={d.executionTeamIndex > 0 ? d.executionTeamIndex : undefined} options={(d.executionTeamOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeExecutionTeam', { detail: { value: i ?? 0 } })} />}
        {d.canViewTeam && <LabeledSearchSelect label="负责人" value={d.opportunityOwnerIndex > 0 ? d.opportunityOwnerIndex : undefined} options={(d.opportunityOwnerOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Owner' }, detail: { value: i ?? 0 } })} />}
        <SbLabeledSelect label="阶段" mode="multiple" maxTagCount="responsive" value={d.opportunitySelectedStages || []} options={(d.opportunityStageOptions || []).filter(o => o.value !== 'all').map(o => ({ value: o.value, label: o.label }))} onChange={changeStages} />
        <OpportunityCloseFilter data-toolbar-width={200} data={d} invoke={invoke} />
        <SbLabeledSelect label="等级" value={d.opportunityGradeIndex > 0 ? d.opportunityGradeIndex : undefined} options={(d.opportunityGradeOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Grade' }, detail: { value: i ?? 0 } })} />
      </FilterBar>
      <div className="pc-results-scroll" ref={results} role="region" aria-label="商机明细" tabIndex={0} onScroll={event => { if (page && listState === 'normal') { const host=event.currentTarget; page._pcOpportunityScroll = {key: scrollKey, top: host.scrollTop}; if(host.scrollHeight-host.scrollTop-host.clientHeight<=480)call('onReachBottom'); } }}>
      <div className="ds-opp-count" aria-live="polite"><b>{listState === 'loading' ? '正在加载商机…' : listState === 'error' ? '商机加载失败' : `共 ${d.opportunityTotal ?? '—'} 条`}</b>{listState === 'normal' && <span className="ds-muted">已加载 {(d.filteredOpportunities || []).length} 条</span>}</div>
      {listState === 'normal' && <p className="ds-opp-note" role="status">{d.opportunityListLoading ? '正在更新列表，已加载内容仍可查看' : d.opportunityListError || ''}{!d.opportunityListLoading && <Button type="link" size="small" onClick={() => call('loadAllOpportunities')}>刷新列表</Button>}</p>}
      {listState !== 'normal'
        ? <SbTable columns={columns} rows={[]} state={listState} emptyTitle={listState === 'error' ? '加载失败' : '没有匹配的商机'} emptyDescription={listState === 'error' ? d.opportunityListError : undefined} onRetry={() => call('loadAllOpportunities')} onClear={d.opportunityFilterActive ? () => call('resetOpportunityFilters') : undefined} />
        : <div className="ds-opp-groups">{groups.map((g, gi) => <div key={g.key} className="ds-opp-group">
          <h3>{g.label} <small>已加载 {g.items.length} 条</small></h3>
          <SbTable rowKey="id" density="compact" columns={columns} rows={g.items} showHeader={gi === 0} scrollX={columns.reduce((width, column) => width + column.width, 100)}
            onRowClick={r => call('openOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } })}
            actions={r => r.canEdit ? <Button type="link" size="small" onClick={e => { e.stopPropagation(); call('editOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } }); }}>编辑</Button> : null} actionsWidth={72}
            expandable={{ columnWidth: 28, expandedRowRender: expanded, expandRowByClick: false }} />
        </div>)}</div>}
      {listState === 'normal' && (d.opportunityHasMore || d.opportunityMoreError || d.opportunityLoadingMore) && <div className="ds-opp-more">{d.opportunityTotal > (d.filteredOpportunities || []).length && <span className="ds-muted">还有 {d.opportunityTotal - (d.filteredOpportunities || []).length} 条没加载</span>}{d.opportunityMoreError && <span role="alert">{d.opportunityMoreError}</span>}<Button loading={d.opportunityLoadingMore} onClick={() => call('loadMoreOpportunities')}>{d.opportunityMoreError ? '重试' : '加载更多'}</Button></div>}
      </div>
    </section>
    </section>
  </div>;
}
