import React from 'react';
import { Button, Checkbox, Input, Select } from 'antd';
import OpportunityViews from './OpportunityViews.jsx';
import { DEFAULT_COLUMNS, groupViewRows } from './opportunityViewState.mjs';
import { SbLabeledSelect, SbSearch, SbSegmented, SbStatePanel, SbStatusTag, SbTable } from '@shandiant/ui-react';
import FilterBar from './pc/FilterBar.jsx';
import {PageHead} from './pc/ui.jsx';
import ShenmaMetricStrip from './ShenmaMetricStrip.jsx';
import './opportunities.css';

// 商机页（原 pages/workbench/index）。原则：总览一行、每格一行、点整行进详情，其余进展开行。业务数据、筛选、分页都还是原 Page 的。
export function supportsOpportunities(page, data = {}) {
  return Boolean(page && page.route === 'pages/workbench/index' && !data.isFde && data.dataReady);
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const TONE_LABEL = { good: '向好', watch: '需关注', bad: '转差', pending: '待评估' };
const METRIC_HELP = <div className="ds-opp-help">已成单：当前已赢单，按实际成单日期归季。<br />总商机：在推与已赢单，排除已丢单，按预计关单日期归季。<br />活跃商机：所选期间有已确认跟进，每个商机只计一次。<br />新增商机：按创建日期归季。<br />上方只筛总览，下方只筛列表。</div>;
const money = v => (v == null || v === '' || v === '—' || v === '未登记') ? <span className="ds-muted">未登记</span> : v;

export default function Opportunities({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  // Preserve authorized server rows and ordering, including across paginated results.
  const groups = groupViewRows(d.opportunityItems || [], d.opportunityViewGroupBy || 'quarter');
  const listState = d.opportunityListError ? 'error' : d.opportunityListLoading ? 'loading' : !groups.length ? 'empty' : 'normal';
  const board = d.opportunityDataReady ? d.opportunityBoard : {};
  const sq = d.summaryQuarter || { year: '', quarters: [], options: [] };
  const lq = d.listQuarter || { quarters: [] };
  // 总览季度：原来可多选，这里改成单选一个季度或全年，少一层理解
  const pickQuarter = v => { if (v === 'all') return call('toggleQuarter', { dataset: { scope: 'summary', value: 'all' } }); for (const q of sq.quarters) if (q !== v) call('toggleQuarter', { dataset: { scope: 'summary', value: q } }); if (!sq.quarters.includes(v)) call('toggleQuarter', { dataset: { scope: 'summary', value: v } }); };
  const setFilters = patch => page.setData({...patch,...(patch.listQuarter ? {opportunityViewYear:patch.listQuarter.year}: {})}, () => page.applyOpportunityFilters());
  const changeStages = next => setFilters({opportunitySelectedStages: next || [], opportunityStageLabel: next?.length ? `已选${next.length}项` : '全部阶段'});
  const setQuarters = quarters => setFilters({opportunityCloseIndex: 0, listQuarter: {...lq, quarters, label: quarters.length ? `${lq.year}年 ${quarters.map(q => `Q${q}`).join(' + ')}` : '全部时间（跨年）'}});
  const allColumns = [
    { title: '商机 / 客户', key: 'name', width: 260, render: (_, r) => <div className="ds-opp-name"><b>{r.name}{r.gradeLabel && <span className={`ds-opp-grade ds-opp-grade-${r.gradeCode}`}>{r.gradeLabel}</span>}</b><span className="ds-muted">{r.customer_name}</span></div> },
    { title: '负责人 / 团队', key: 'owner', width: 180, render: (_, r) => <div className="ds-opp-owner"><b>{r.owner || '待分配'}</b><span className="ds-muted">{r.team || '待分配团队'}</span></div> },
    { title: '商机金额', key: 'amount', width: 130, align: 'right', render: (_, r) => money(r.amountLabel) },
    { title: '产品线', key: 'product', width: 140, render: (_, r) => r.productLineLabel || <span className="ds-muted">未登记</span> },
    { title: '阶段', key: 'stage', width: 150, render: (_, r) => <span className="ds-opp-stage">{r.stageName}{r.probabilityText && <span className="ds-muted"> · {r.probabilityText}</span>}</span> },
    { title: '状态', key: 'signal', width: 110, render: (_, r) => { const t = toneOf(r.signal?.tone); return <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={r.signal?.detail} />; } },
    { title: '确收 / 回款', key: 'money', width: 160, align: 'right', render: (_, r) => <span className="ds-opp-money">{money(r.recognizedLabel)}<span className="ds-muted"> / </span>{money(r.collectionLabel)}</span> },
    { title: '预计关单', key: 'close', width: 120, render: (_, r) => r.closeLabel || <span className="ds-muted">未登记</span> },
  ];
  const columns = (d.opportunityViewColumns || DEFAULT_COLUMNS).map(key => allColumns.find(c => c.key === key)).filter(Boolean);
  const expanded = r => <div className="ds-opp-expanded"><span>产品线：{r.productLineLabel || '未登记'}</span><span>负责人：{r.team} · {r.owner}</span><span>依据：{r.signal?.label} · {r.signal?.detail}</span></div>;
  if (d.accessBlocked) return <SbStatePanel state="forbidden" description={d.accessMessage} />;
  return <section className="pc-page pc-list-page pc-opportunities ds-opps" aria-label="商机">
    <PageHead title="商机管理" subtitle="统一查看商机、经营阶段与跟进状态"/>
    <section className="ds-panel ds-opp-board" aria-label="商机总览">
      <ShenmaMetricStrip loading={d.opportunityOverviewLoading}
        extra={(d.quarterYearOptions || []).length > 1 && <SbSegmented aria-label="年份" value={d.summaryYearIndex} options={(d.quarterYearOptions || []).map((o, i) => ({ value: i, label: o.label }))} onChange={i => call('changeQuarterYear', { dataset: { scope: 'summary' }, detail: { value: i } })} />}
        periods={[{ value: 'all', label: '全年' }, ...(sq.options || []).map(q => ({ value: q.value, label: q.label }))]}
        period={sq.quarters.length === 1 ? sq.quarters[0] : 'all'}
        onPeriodChange={pickQuarter}
        caliber={METRIC_HELP}
        items={[
          { key: 'total', label: '总商机', value: board.total ?? null, missingText: '—', note: '在推与已赢单，排除已丢单' },
          { key: 'active', label: '活跃商机', value: board.active ?? null, missingText: '—', note: '所选期间有已确认跟进' },
          { key: 'won', label: '已成单', value: board.won ?? null, missingText: '—', note: '按实际成单日期统计' },
          { key: 'new', label: '新增商机', value: board.newCount ?? null, missingText: '—', note: '按创建日期统计' },
        ]} />
      {d.opportunityDataError && <p className="ds-opp-note" role="alert">{d.opportunityDataError} <Button type="link" size="small" onClick={() => call('applyOpportunitySummary')}>重试</Button></p>}
    </section>
    <section className="ds-panel ds-opp-list" aria-label="商机列表">
      <OpportunityViews key={d.opportunityViewIdentity || page._id} page={page} data={d}/>
      <FilterBar className="ds-opp-tools" label="商机筛选" moreLabel="更多筛选"
        search={<SbSearch value={d.opportunityQuery || ''} onChange={value => call('searchOpportunities', { detail: { value } })} onSearch={() => call('applyOpportunityFilters')} placeholder="搜索客户、商机或产品线" loading={d.opportunityListLoading}/>}
        actions={d.opportunityFilterActive && <Button type="text" size="small" onClick={() => call('resetOpportunityFilters')}>清除筛选</Button>}>
        {d.canViewTeam && <SbLabeledSelect label="团队" value={d.executionTeamIndex > 0 ? d.executionTeamIndex : undefined} options={(d.executionTeamOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeExecutionTeam', { detail: { value: i ?? 0 } })} />}
        {d.canViewTeam && <SbLabeledSelect label="负责人" value={d.opportunityOwnerIndex > 0 ? d.opportunityOwnerIndex : undefined} options={(d.opportunityOwnerOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Owner' }, detail: { value: i ?? 0 } })} />}
        <SbLabeledSelect label="阶段" mode="multiple" value={d.opportunitySelectedStages || []} options={(d.opportunityStageOptions || []).filter(o => o.value !== 'all').map(o => ({ value: o.value, label: o.label }))} onChange={changeStages} />
        <SbLabeledSelect label="预计关单" value={d.opportunityCloseIndex > 0 ? d.opportunityCloseIndex : lq.quarters?.length ? 'quarters' : undefined} options={[...(d.opportunityCloseOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0), ...(lq.quarters?.length ? [{ value: 'quarters', label: lq.label }] : [])]} onChange={i => call('selectOpportunityClosePeriod', { dataset: { index: i ?? 0 } })} />
        <SbLabeledSelect label="等级" value={d.opportunityGradeIndex > 0 ? d.opportunityGradeIndex : undefined} options={(d.opportunityGradeOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Grade' }, detail: { value: i ?? 0 } })} />
        <label className="ds-opp-extra-filter" data-toolbar-width={200}>关单年份<Select aria-label="关单年份" value={lq.year} options={d.quarterYearOptions || []} onChange={year => setFilters({opportunityCloseIndex:0,listQuarter:{...lq,year,quarters:lq.quarters.length?lq.quarters:[1,2,3,4]}})}/></label>
        <label className="ds-opp-extra-filter" data-toolbar-width={250}>关单季度<Select aria-label="关单季度" mode="multiple" placeholder="跨年全部" value={lq.quarters || []} options={[1,2,3,4].map(value=>({value,label:`Q${value}`}))} onChange={setQuarters}/></label>
        <label className="ds-opp-extra-filter" data-toolbar-width={220}>产品线<Input aria-label="产品线筛选" placeholder="全部产品线" value={d.opportunityProductLine || ''} maxLength={100} onChange={e=>page.setData({opportunityProductLine:e.target.value})} onPressEnter={()=>page.applyOpportunityFilters()} onBlur={()=>page.applyOpportunityFilters()}/></label>
        <Checkbox checked={d.opportunityIncludeClosed !== false} onChange={e=>setFilters({opportunityIncludeClosed:e.target.checked})}>包含已关闭</Checkbox>
      </FilterBar>
      {((d.executionTeamOptions || [])[d.executionTeamIndex]?.label === '已保存团队' || (d.opportunityOwnerOptions || [])[d.opportunityOwnerIndex]?.label === '已保存负责人') && <p className="ds-opp-view-error" role="alert">所保存的团队或负责人不在当前可选范围，筛选仍保留。请重新选择，或清除筛选。</p>}
      <div className="ds-opp-count"><b>共 {d.opportunityTotal ?? '—'} 条商机</b><span className="ds-muted">{d.opportunityFilterActive ? '已按列表条件筛选' : '当前权限范围'}{d.opportunityViewScope ? ` · 已保存范围：${d.opportunityViewScope === 'self' ? '仅本人' : '所属团队'}` : ''}</span></div>
      {listState !== 'normal'
        ? <SbTable columns={columns} rows={[]} state={listState} emptyTitle={listState === 'error' ? '加载失败' : '没有匹配的商机'} emptyDescription={listState === 'error' ? d.opportunityListError : '可调整筛选条件，或从客户详情查看并维护商机。'} onRetry={() => call('loadAllOpportunities')} onClear={d.opportunityFilterActive ? () => call('resetOpportunityFilters') : undefined} />
        : <div className="ds-opp-groups">{groups.map((g, gi) => <div key={g.key} className="ds-opp-group">
          <h3>{g.label} <small>{g.items.length} 条</small></h3>
          <SbTable rowKey="id" density="compact" columns={columns} rows={g.items} showHeader={gi === 0} scrollX={Math.max(680, columns.length * 160)}
            onRowClick={r => call('openOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } })}
            actions={r => r.canEdit ? <Button type="link" size="small" onClick={e => { e.stopPropagation(); call('editOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } }); }}>编辑</Button> : null} actionsWidth={72}
            expandable={{ columnWidth: 28, expandedRowRender: expanded, expandRowByClick: false }} />
        </div>)}</div>}
      {listState === 'normal' && (d.opportunityHasMore || d.opportunityMoreError || d.opportunityLoadingMore) && <div className="ds-opp-more">{d.opportunityTotal > (d.filteredOpportunities || []).length && <span className="ds-muted">还有 {d.opportunityTotal - (d.filteredOpportunities || []).length} 条没加载</span>}{d.opportunityMoreError && <span role="alert">{d.opportunityMoreError}</span>}<Button loading={d.opportunityLoadingMore} onClick={() => call('loadMoreOpportunities')}>{d.opportunityMoreError ? '重试' : '加载更多'}</Button></div>}
    </section>
  </section>;
}
