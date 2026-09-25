import React from 'react';
import { Button } from 'antd';
import { SbLabeledSelect, SbMetricStrip, SbSearch, SbSegmented, SbStatePanel, SbStatusTag, SbTable } from '@shandiant/ui-react';
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
  // 分组倒序：最近的季度在前（Q4 → Q1），组内顺序沿用原页面
  const groups = [...(d.opportunityGroups || [])].reverse();
  const listState = d.opportunityListError ? 'error' : d.opportunityListLoading ? 'loading' : !groups.length ? 'empty' : 'normal';
  const board = d.opportunityDataReady ? d.opportunityBoard : {};
  const sq = d.summaryQuarter || { year: '', quarters: [], options: [] };
  const lq = d.listQuarter || { quarters: [] };
  // 总览季度：原来可多选，这里改成单选一个季度或全年，少一层理解
  const pickQuarter = v => { if (v === 'all') return call('toggleQuarter', { dataset: { scope: 'summary', value: 'all' } }); for (const q of sq.quarters) if (q !== v) call('toggleQuarter', { dataset: { scope: 'summary', value: q } }); if (!sq.quarters.includes(v)) call('toggleQuarter', { dataset: { scope: 'summary', value: v } }); };
  // 阶段多选：上游一次只切一个，这里把差异逐个切
  const changeStages = next => { const prev = d.opportunitySelectedStages || []; for (const v of new Set([...(next || []), ...prev])) if ((next || []).includes(v) !== prev.includes(v)) call('toggleOpportunityStage', { dataset: { value: v } }); };
  const columns = [
    { title: '商机 / 客户', key: 'name', width: '36%', render: (_, r) => <div className="ds-opp-name"><b>{r.name}{r.gradeLabel && <span className={`ds-opp-grade ds-opp-grade-${r.gradeCode}`}>{r.gradeLabel}</span>}</b><span className="ds-muted">{r.customer_name}</span></div> },
    { title: '阶段', key: 'stage', width: 150, render: (_, r) => <span className="ds-opp-stage">{r.stageName}{r.probabilityText && <span className="ds-muted"> · {r.probabilityText}</span>}</span> },
    { title: '状态', key: 'signal', width: 110, render: (_, r) => { const t = toneOf(r.signal?.tone); return <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={r.signal?.detail} />; } },
    { title: '确收 / 回款', key: 'money', width: 160, align: 'right', render: (_, r) => <span className="ds-opp-money">{money(r.recognizedLabel)}<span className="ds-muted"> / </span>{money(r.collectionLabel)}</span> },
    { title: '预计关单', key: 'close', width: 120, render: (_, r) => r.closeLabel || <span className="ds-muted">未登记</span> },
  ];
  const expanded = r => <div className="ds-opp-expanded"><span>产品线：{r.productLineLabel || '未登记'}</span><span>负责人：{r.team} · {r.owner}</span><span>依据：{r.signal?.label} · {r.signal?.detail}</span></div>;
  if (d.accessBlocked) return <SbStatePanel state="forbidden" description={d.accessMessage} />;
  return <section className="ds-opps" aria-label="商机">
    <section className="ds-panel ds-opp-board" aria-label="商机总览">
      <SbMetricStrip columns={4} loading={d.opportunityOverviewLoading}
        extra={(d.quarterYearOptions || []).length > 1 && <SbSegmented aria-label="年份" value={d.summaryYearIndex} options={(d.quarterYearOptions || []).map((o, i) => ({ value: i, label: o.label }))} onChange={i => call('changeQuarterYear', { dataset: { scope: 'summary' }, detail: { value: i } })} />}
        periods={[{ value: 'all', label: '全年' }, ...(sq.options || []).map(q => ({ value: q.value, label: q.label }))]}
        period={sq.quarters.length === 1 ? sq.quarters[0] : 'all'}
        onPeriodChange={pickQuarter}
        caliber={METRIC_HELP}
        items={[
          { key: 'total', label: '总商机', value: board.total ?? null, missingText: '—' },
          { key: 'active', label: '活跃商机', value: board.active ?? null, missingText: '—' },
          { key: 'won', label: '已成单', value: board.won ?? null, missingText: '—' },
          { key: 'new', label: '新增商机', value: board.newCount ?? null, missingText: '—' },
        ]} />
      {d.opportunityDataError && <p className="ds-opp-note" role="alert">{d.opportunityDataError} <Button type="link" size="small" onClick={() => call('applyOpportunitySummary')}>重试</Button></p>}
    </section>
    <section className="ds-panel ds-opp-list" aria-label="商机列表">
      <div className="ds-opp-tools">
        <div className="ds-opp-search"><SbSearch value={d.opportunityQuery || ''} onChange={value => call('searchOpportunities', { detail: { value } })} onSearch={() => call('applyOpportunityFilters')} placeholder="搜索客户、商机或产品线" loading={d.opportunityListLoading} /></div>
        {d.canViewTeam && <SbLabeledSelect label="团队" value={d.executionTeamIndex > 0 ? d.executionTeamIndex : undefined} options={(d.executionTeamOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeExecutionTeam', { detail: { value: i ?? 0 } })} />}
        {d.canViewTeam && <SbLabeledSelect label="负责人" value={d.opportunityOwnerIndex > 0 ? d.opportunityOwnerIndex : undefined} options={(d.opportunityOwnerOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Owner' }, detail: { value: i ?? 0 } })} />}
        <SbLabeledSelect label="阶段" mode="multiple" value={d.opportunitySelectedStages || []} options={(d.opportunityStageOptions || []).filter(o => o.value !== 'all').map(o => ({ value: o.value, label: o.label }))} onChange={changeStages} />
        <SbLabeledSelect label="预计关单" value={d.opportunityCloseIndex > 0 ? d.opportunityCloseIndex : lq.quarters?.length ? 'quarters' : undefined} options={[...(d.opportunityCloseOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0), ...(lq.quarters?.length ? [{ value: 'quarters', label: lq.label }] : [])]} onChange={i => call('selectOpportunityClosePeriod', { dataset: { index: i ?? 0 } })} />
        <SbLabeledSelect label="等级" value={d.opportunityGradeIndex > 0 ? d.opportunityGradeIndex : undefined} options={(d.opportunityGradeOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeOpportunityFilter', { dataset: { key: 'Grade' }, detail: { value: i ?? 0 } })} />
        {d.opportunityFilterActive && <Button type="link" size="small" onClick={() => call('resetOpportunityFilters')}>清除筛选</Button>}

      </div>
      <div className="ds-opp-count"><b>共 {d.opportunityTotal ?? '—'} 条</b></div>
      {listState !== 'normal'
        ? <SbTable columns={columns} rows={[]} state={listState} emptyTitle={listState === 'error' ? '加载失败' : '没有匹配的商机'} emptyDescription={listState === 'error' ? d.opportunityListError : undefined} onRetry={() => call('loadAllOpportunities')} onClear={d.opportunityFilterActive ? () => call('resetOpportunityFilters') : undefined} />
        : <div className="ds-opp-groups">{groups.map((g, gi) => <div key={g.key} className="ds-opp-group">
          <h3>{g.label} <small>{g.items.length} 条</small></h3>
          <SbTable rowKey="id" density="compact" columns={columns} rows={g.items} showHeader={gi === 0} scrollX={760}
            onRowClick={r => call('openOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } })}
            actions={r => r.canEdit ? <Button type="link" size="small" onClick={e => { e.stopPropagation(); call('editOpportunity', { dataset: { customerId: r.customer_id, opportunityId: r.id } }); }}>编辑</Button> : null} actionsWidth={72}
            expandable={{ columnWidth: 28, expandedRowRender: expanded, expandRowByClick: false }} />
        </div>)}</div>}
      {listState === 'normal' && (d.opportunityHasMore || d.opportunityMoreError || d.opportunityLoadingMore) && <div className="ds-opp-more">{d.opportunityTotal > (d.filteredOpportunities || []).length && <span className="ds-muted">还有 {d.opportunityTotal - (d.filteredOpportunities || []).length} 条没加载</span>}{d.opportunityMoreError && <span role="alert">{d.opportunityMoreError}</span>}<Button loading={d.opportunityLoadingMore} onClick={() => call('loadMoreOpportunities')}>{d.opportunityMoreError ? '重试' : '加载更多'}</Button></div>}
    </section>
  </section>;
}
