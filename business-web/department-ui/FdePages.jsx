import PresentationSwitch from './PresentationSwitch.jsx';
import React, { useState } from 'react';
import { Button, Checkbox, Progress } from 'antd';
import { SbBarChart, SbLabeledSelect, SbListRow, SbMetricStrip, SbSearch, SbSegmented, SbSheet, SbStatePanel, SbStatusTag, SbTable } from '@shandiant/ui-react';
import './fde-pages.css';

// FDE 视角的几页都只是原页面包一层子组件：协作看板是 fde-dashboard，商机是 fde-projects。
// 这里从子组件实例读 data、用 invokeOn 调它的方法；页面本身几乎没有数据。
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const TONE_LABEL = { good: '向好', watch: '需关注', bad: '转差', pending: '待评估' };
const YEAR_LABEL = y => `${y}年`;

/* 拜访节奏：竖向柱状，日或周；数量缺失不画成 0 */
function Bars({ rows, mode }) {
  if (!rows.length) return null;
  return <SbBarChart orientation="vertical" categories={rows.map(r => mode === 'week' && r.endLabel ? `${r.dateLabel}～${r.endLabel}` : r.dateLabel)} series={[{ name: '拜访', data: rows.map(r => r.visits === null || r.visits === undefined ? null : Number(r.visits)) }]} unit="次" height={220} />;
}

/* 公共排名卡：只显示本人／所选对象那几行，完整榜单放抽屉 */
function RankingCard({ card, cohort, period, onRetry }) {
  const [open, setOpen] = useState(false);
  const rows = card.rows || [], summary = rows.filter(r => r.isSelected);
  const Row = ({ r }) => <li className={`ds-fd-rank-row ${r.isSelected ? 'is-selected' : ''}`}><span className="ds-fd-rank-no">{r.rank}</span><span className="ds-fd-rank-main"><b>{r.name}{r.isSelected && <small className="ds-muted"> {r.isSelf ? '本人' : '当前查看'}</small>}</b><span className="ds-muted">{r.meta}</span><Progress percent={parseFloat(r.width) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /></span><b className="ds-fd-rank-value">{r.displayValue}</b></li>;
  return <section className="ds-fd-rank">
    <div className="ds-fd-rank-head"><div><b>{card.title}</b><span className="ds-muted">{cohort} · {period}</span></div><Button type="link" size="small" disabled={Boolean(card.error)} onClick={() => setOpen(true)}>查看详情</Button></div>
    {card.error ? <p className="ds-fd-error">{card.error} <Button type="link" size="small" onClick={onRetry}>重试</Button></p>
      : summary.length ? <ol className="ds-fd-rank-list">{summary.map(r => <Row key={r.id} r={r} />)}</ol>
        : <p className="ds-muted">{rows.length ? '所选对象暂未纳入当前榜单' : '当前范围暂无排名数据'}</p>}
    <SbSheet open={open} title={<span>{card.title} <small className="ds-muted">{cohort} · 共 {rows.length} 项 · {period}</small></span>} onClose={() => setOpen(false)} footer={<Button onClick={() => setOpen(false)}>完成</Button>}>
      {rows.length ? <ol className="ds-fd-rank-list">{rows.map(r => <Row key={r.id} r={r} />)}</ol> : <p className="ds-muted">当前范围暂无排名数据</p>}
    </SbSheet>
  </section>;
}

/* 跟进记录列表：记录页的正文，也是看板「查看记录」的落点 */
function ActivityList({ dash, on }) {
  const d = dash.data;
  return <div className="ds-fd-activity">
    <div className="ds-fd-section-head"><div><b>跟进记录 <small className="ds-muted">{d.activityTotal} 条</small></b><span className="ds-muted">{d.periodLabel} · {d.memberLabel || d.scopeLabel}</span></div></div>
    <div className="ds-fd-activity-list">
      {(d.activity || []).map(v => <SbListRow key={v.id} name={v.customer_name} summary={<span>{v.opportunity_name || '未关联商机'} · {v.recorder_name || '未记录填报人'}</span>} time={v.dateText} status={v.can_read_detail ? undefined : { tone: 'pending', label: '历史摘要' }} onClick={() => on('openVisit', { dataset: { id: v.id } })} />)}
    </div>
    {d.activityLoading ? <p className="ds-muted">正在加载跟进记录…</p>
      : d.activityError ? <p className="ds-fd-error">{d.activityError} <Button type="link" size="small" onClick={() => on('loadActivity')}>重新加载</Button></p>
        : d.activityMore ? <Button size="small" onClick={() => on('loadActivity')}>加载更多记录</Button>
          : !(d.activity || []).length ? <SbStatePanel state="empty" title="暂无跟进记录" description="所选周期内的已归档记录会显示在这里" /> : null}
  </div>;
}

/* 协作看板：dash 是 fde-dashboard 实例 */
export function FdeDashboard({ dash, invokeOn, compact }) {
  if (!dash) return <SbStatePanel state="loading" title="正在准备协作看板" />;
  const d = dash.data, s = d.summary || {};
  const on = (name, payload) => invokeOn(dash, name, payload);
  const props = dash.properties || {};
  if (props.recordsOnly) return <section className="ds-panel ds-fd-card"><ActivityList dash={dash} on={on} /></section>;
  const teamScope = d.canViewTeam && !props.personal && !props.memberId;
  const sortOption = (d.sortOptions || [])[d.sortIndex] || {};
  const rankCols = [
    { title: '#', key: 'rank', width: 48, render: (_, r) => <span className="ds-fd-rank-no">{r.rank}</span> },
    { title: '成员', key: 'name', render: (_, r) => <div className="ds-fd-two"><b>{r.name}{r.is_active === false ? ' · 已离组' : ''}</b><span className="ds-muted">{r.team} · {r.opportunities} 个商机</span></div> },
    { title: sortOption.name || '数值', key: 'value', width: 220, render: (_, r) => <div className="ds-fd-stage-bar"><Progress percent={Number(r.width) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /><b>{r.value}</b></div> },
  ];
  return <div className="ds-fd">
    <section className="ds-panel ds-fd-card">
      <div className="ds-fd-head">
        <div><h2>{props.memberId ? `${d.memberLabel || '成员'}的看板` : '协作看板'}</h2><span className="ds-muted">{d.memberLabel || d.scopeLabel} · {d.periodLabel}{d.asOf ? ` · ${d.asOf}` : ''}</span></div>
        <div className="ds-fd-controls">
          {teamScope && <SbSegmented value={d.scope} options={[{ value: 'team', label: '团队' }, { value: 'self', label: '个人' }]} onChange={scope => on('scope', { dataset: { scope } })} />}
          {teamScope && d.scope === 'self' && <SbLabeledSelect label="成员" allowClear={false} value={(d.pickerSelected || [])[0] ?? ''} options={(d.pickerOptions || []).map(m => ({ value: m.id, label: m.group && m.group !== '授权成员' ? `${m.name}（${m.group}）` : m.name }))} onChange={id => on('selectMember', { detail: { ids: [id] } })} width={220} />}
          <Button size="small" onClick={() => on('toggleFilter')}>{d.periodLabel} {d.filterOpen ? '收起' : '更改'}</Button>
        </div>
      </div>
      {d.filterOpen && <div className="ds-fd-filter">
        <SbLabeledSelect label="年份" allowClear={false} value={d.draftYearIndex} options={(d.yearOptions || []).map((y, i) => ({ value: i, label: YEAR_LABEL(y) }))} onChange={i => on('year', { detail: { value: i } })} width={150} />
        <Checkbox.Group value={d.draftQuarters || []} options={(d.quarterOptions || []).map(q => ({ value: q.value, label: `Q${q.value}` }))} onChange={next => { const prev = d.draftQuarters || []; for (const q of new Set([...next, ...prev])) if (next.includes(q) !== prev.includes(q)) on('quarter', { dataset: { q } }); }} />
        <Button size="small" onClick={() => on('quarter', { dataset: { q: 0 } })}>全年</Button>
        <span className="ds-muted">可选多个季度</span>
        <Button size="small" type="primary" onClick={() => on('applyPeriod')}>确定</Button>
      </div>}
      {d.loading ? <SbStatePanel state="loading" title="正在更新看板" />
        : d.error ? <SbStatePanel state="error" title={d.error} onRetry={() => on('load')} />
          : d.ready && <>
            <h3 className="ds-fd-h">经营数据 <small className="ds-muted">{d.memberLabel || d.scopeLabel} · 当前协助商机</small></h3>
            <SbMetricStrip columns={3} items={[
              { key: 'opps', label: '协助商机', value: s.opportunities, note: '个，点进项目', onClick: () => on('openProjects') },
              { key: 'customers', label: '协助客户', value: s.customers, note: '个' },
              { key: 'open', label: '在推商机', value: s.open_opportunities, note: '个' },
            ]} />
            <SbMetricStrip columns={4} variant="card" items={[
              { key: 'acv', label: '在推商机金额', value: s.open_acv === null || s.open_acv === undefined ? null : `${s.openAcvText} 万`, note: '当前参与项目', missingText: '未登记', onClick: () => on('openProjects') },
              { key: 'visits', label: '跟进记录', value: `${s.period_visits} 条`, note: `${s.active_recorders} 人填报 · 查看记录`, onClick: () => on('openActivity') },
              { key: 'recognized', label: '确收金额', value: s.recognized_amount === null || s.recognized_amount === undefined ? null : `${s.recognizedText} 万`, note: '所选周期 · 协助项目', missingText: '未登记', onClick: () => on('openProjects') },
              { key: 'demo', label: 'Demo 数量', value: `${s.demo_scene_count} 个`, note: '所选周期 · 场景登记' },
            ]} />
            <h3 className="ds-fd-h">任务进展 <small className="ds-muted">{d.periodLabel}</small></h3>
            <SbMetricStrip columns={3} items={[
              { key: 'done', label: '已完成', value: s.completed_tasks, onClick: () => on('openTasks', { dataset: { status: 'completed' } }) },
              { key: 'pending', label: '待完成', value: s.pending_tasks, onClick: () => on('openTasks', { dataset: { status: 'pending' } }) },
              { key: 'overdue', label: '已逾期', value: s.overdue_tasks, note: s.overdue_tasks ? '需处理' : undefined, onClick: () => on('openTasks', { dataset: { status: 'pending' } }) },
            ]} />
          </>}
    </section>
    {d.ready && !compact && <div className="ds-fd-grid">
      <section className="ds-panel ds-fd-card">
        <div className="ds-fd-section-head"><div><b>商机阶段</b><span className="ds-muted">当前参与商机 · 按数量</span></div><Button type="link" size="small" onClick={() => on('openProjects')}>全部</Button></div>
        {(d.stages || []).length ? <SbBarChart orientation="horizontal" categories={(d.stages || []).map(r => r.label)} series={[{ name: '商机数', data: (d.stages || []).map(r => Number(r.count) || 0) }]} unit="个" height={Math.max(160, (d.stages || []).length * 36 + 60)} onClick={() => on('openProjects')} />
          : <SbStatePanel state="empty" title="参与商机后，在这里查看项目进展" />}
      </section>
      <section className="ds-panel ds-fd-card">
        <div className="ds-fd-section-head"><div><b>拜访节奏</b><span className="ds-muted">{d.rhythmMode === 'day' ? '最近 7 天' : '最近 12 周 · 周一至周日'} · {s.period_customers} 个客户</span></div><div className="ds-fd-inline"><SbSegmented value={d.rhythmMode} options={[{ value: 'day', label: '日' }, { value: 'week', label: '周' }]} onChange={mode => on('changeRhythmMode', { dataset: { mode } })} /><Button type="link" size="small" onClick={() => on('openActivity')}>明细</Button></div></div>
        {(d.rhythm || []).length ? <Bars rows={d.rhythm} mode={d.rhythmMode} /> : <SbStatePanel state="empty" title="本人确认归档后，将形成拜访节奏" />}
      </section>
    </div>}
    {d.ready && !compact && d.scope === 'team' && <section className="ds-panel ds-fd-card">
      <div className="ds-fd-section-head"><div><b>团队工作分布</b><span className="ds-muted">{d.rankingPeriodLabel || d.periodLabel}</span></div><div className="ds-fd-inline">
        <SbSegmented value={d.rankingPeriod || ''} options={[{ value: 'week', label: '周' }, { value: 'month', label: '月' }, { value: 'quarter', label: '季度' }]} onChange={period => on('changeRankingPeriod', { dataset: { period } })} />
        <SbLabeledSelect label="按" allowClear={false} value={d.sortIndex} options={(d.sortOptions || []).map((o, i) => ({ value: i, label: o.name }))} onChange={i => on('sortRanking', { detail: { value: i } })} width={170} />
      </div></div>
      {d.rankingPeriodLoading ? <p className="ds-muted">正在加载团队统计…</p> : d.rankingPeriodError ? <p className="ds-fd-error">{d.rankingPeriodError}</p>
        : <SbTable rowKey="user_id" density="compact" columns={rankCols} rows={d.ranking || []} state={(d.ranking || []).length ? 'normal' : 'empty'} emptyTitle="当前范围暂无成员记录" onRowClick={r => on('openMember', { dataset: { id: r.user_id } })} />}
    </section>}
    {d.ready && !compact && <section className="ds-fd-public">
      <h3 className="ds-fd-h">公共排名 <small className="ds-muted">{d.rankingCohort}</small></h3>
      <div className="ds-fd-rank-grid">{(d.companyCards || []).map(card => <RankingCard key={card.key} card={card} cohort={d.rankingCohort} period={d.periodLabel} onRetry={() => on('load')} />)}</div>
    </section>}
    {d.activityOpen && <section className="ds-panel ds-fd-card"><ActivityList dash={dash} on={on} /></section>}
  </div>;
}

/* 跟进记录页 pages/fde-records：fde-dashboard 的 records-only 形态 */
export function supportsFdeRecords(page) { return Boolean(page && page.route === 'pages/fde-records/index'); }
FdeRecords.subcomponents = (page, d) => [{ selector: 'fde-dashboard', required: Boolean(d.context), props: { recordsOnly: true, recordContext: d.context, memberId: d.context?.member_id || '' } }];
export function FdeRecords({ data: d, invokeOn, select }) {
  const dash = d.context ? select('fde-dashboard') : null;
  if (!d.context) return <section className="ds-fd-page"><SbStatePanel state="error" title="记录参数无效，请返回重试" /></section>;
  return <section className="ds-fd-page" aria-label="跟进记录"><FdeDashboard dash={dash} invokeOn={invokeOn} /></section>;
}

/* 经营分析 pages/bi 的 FDE 形态：整页就是协作看板 */
export function supportsFdeBi(page, data = {}) { return Boolean(page && page.route === 'pages/bi/index' && data.isFde); }
FdeBi.subcomponents = () => [{ selector: '#fdeContent' }];
export function FdeBi({ data, invoke, invokeOn, select }) {
  return <section className="ds-fd-page" aria-label="协作看板"><PresentationSwitch data={data} invoke={invoke}/><FdeDashboard dash={select('#fdeContent')} invokeOn={invokeOn} /></section>;
}

/* 成员成长 pages/member-growth 的 FDE 形态：指定成员的协作看板 */
export function supportsFdeMemberGrowth(page, data = {}) { return Boolean(page && page.route === 'pages/member-growth/index' && data.isFde); }
FdeMemberGrowth.subcomponents = (page, d) => [{ selector: '#fdeContent', props: { memberId: d.fdeMemberId || '' } }];
export function FdeMemberGrowth({ invokeOn, select }) {
  return <section className="ds-fd-page" aria-label="成员协作看板"><FdeDashboard dash={select('#fdeContent')} invokeOn={invokeOn} /></section>;
}

/* 商机 pages/workbench 与 pages/opportunities 的 FDE 形态：fde-projects 子组件 */
export function FdeProjects({ projects, invokeOn }) {
  if (!projects) return <SbStatePanel state="loading" title="正在准备协助商机" />;
  const d = projects.data, props = projects.properties || {};
  const on = (name, payload) => invokeOn(projects, name, payload);
  const sq = d.summaryQuarter || { year: '', quarters: [], options: [] };
  const ov = d.overview || {};
  const toggleSet = (prev, next, fire) => { for (const v of new Set([...(next || []), ...(prev || [])])) if ((next || []).includes(v) !== (prev || []).includes(v)) fire(v); };
  const filterActive = (d.memberIds || []).length || d.query || (d.selectedStages || []).length || (d.quarters || []).length || d.gradeIndex || d.productIndex;
  const listState = d.loading ? 'loading' : d.error ? 'error' : !(d.filtered || []).length ? 'empty' : 'normal';
  const columns = [
    { title: '商机 / 客户', key: 'name', width: '34%', render: (_, r) => <div className="ds-fd-two"><b>{r.name}{r.gradeLabel && <span className="ds-fd-grade">{r.gradeLabel}</span>}</b><span className="ds-muted">{r.customer_name}</span></div> },
    { title: '阶段', key: 'stage', width: 150, render: (_, r) => <span>{r.stageName}{r.probabilityText && <span className="ds-muted"> · {r.probabilityText}</span>}</span> },
    { title: '状态', key: 'signal', width: 110, render: (_, r) => { const t = toneOf(r.signal?.tone); return <SbStatusTag tone={t} label={TONE_LABEL[t]} reason={r.signal?.detail} />; } },
    { title: '确收 / 回款', key: 'money', width: 150, align: 'right', render: (_, r) => <span>{r.recognizedLabel}<span className="ds-muted"> / </span>{r.collectionLabel}</span> },
    { title: '预计关单', key: 'close', width: 110, render: (_, r) => r.closeLabel || <span className="ds-muted">未登记</span> },
    { title: '协助 FDE', key: 'fde', width: 160, render: (_, r) => r.memberNames || <span className="ds-muted">未指定</span> },
  ];
  return <div className="ds-fd">
    {!props.customerId && <section className="ds-panel ds-fd-card">
      <div className="ds-fd-section-head"><div><b>商机总览</b><span className="ds-muted">{props.memberId ? d.memberContextLabel : d.scope === 'team' ? '团队' : '本人'} · 只筛总览，不影响下方列表</span></div></div>
      <SbMetricStrip columns={4} loading={Boolean(d.overviewLoading)}
        extra={<SbLabeledSelect label="年份" allowClear={false} value={d.summaryYearIndex} options={(d.years || []).map((y, i) => ({ value: i, label: YEAR_LABEL(y) }))} onChange={i => on('summaryYear', { detail: { value: i } })} width={130} />}
        periods={[{ value: 0, label: '全部' }, ...(sq.options || []).map(q => ({ value: q.value, label: q.label }))]}
        period={sq.quarters.length === 1 ? sq.quarters[0] : 0}
        onPeriodChange={v => { if (v === 0) return on('summaryQuarterChange', { dataset: { q: 0 } }); for (const q of sq.quarters) if (q !== v) on('summaryQuarterChange', { dataset: { q } }); if (!sq.quarters.includes(v)) on('summaryQuarterChange', { dataset: { q: v } }); }}
        items={[
          { key: 'won', label: '已成单商机', value: d.overviewReady ? ov.won : null, missingText: '—' },
          { key: 'total', label: '总商机', value: d.overviewReady ? ov.total : null, missingText: '—' },
          { key: 'active', label: '活跃商机', value: d.overviewReady ? ov.active : null, missingText: '—' },
          { key: 'demo', label: 'Demo 场景', value: d.overviewReady ? ov.demo_scene_count : null, missingText: '—' },
        ]} />
      {d.overviewError && <p className="ds-fd-error">商机总览暂不可用 <Button type="link" size="small" onClick={() => on('loadOverview')}>重试</Button></p>}
      {d.overviewReady && sq.quarters.length > 0 && (ov.missingCloseDates || ov.missingWonDates || ov.missingCreatedDates) ? <p className="ds-muted ds-fd-note">{[ov.missingCloseDates && `${ov.missingCloseDates} 个商机未填关单日期，未计入季度总量`, ov.missingWonDates && `${ov.missingWonDates} 个成单商机缺少成单日期`, ov.missingCreatedDates && `${ov.missingCreatedDates} 个商机缺少创建日期`].filter(Boolean).join('；')}</p> : null}
    </section>}
    <section className="ds-panel ds-fd-card">
      <div className="ds-fd-tools">
        <div className="ds-fd-search"><SbSearch value={d.query || ''} placeholder="搜索客户、商机或产品线" loading={Boolean(d.loading)} onChange={value => on('search', { detail: { value } })} /></div>
        {d.canViewTeam && !props.customerId && !props.memberId && <SbLabeledSelect label="人员" mode="multiple" placeholder="全部成员" value={d.memberIds || []} options={(d.members || []).filter(m => m.id).map(m => ({ value: m.id, label: m.name }))} disabled={Boolean(d.membersLoading)} onChange={ids => on('member', { detail: { ids: ids || [] } })} />}
        <SbLabeledSelect label="阶段" mode="multiple" value={d.selectedStages || []} options={(d.stages || []).map(s => ({ value: s.code, label: s.label }))} onChange={next => toggleSet(d.selectedStages, next, code => on('stage', { dataset: { code } }))} />
        <SbLabeledSelect label="关单年份" allowClear={false} value={d.yearIndex} options={(d.years || []).map((y, i) => ({ value: i, label: YEAR_LABEL(y) }))} onChange={i => on('year', { detail: { value: i } })} width={130} />
        <SbLabeledSelect label="关单季度" mode="multiple" value={d.quarters || []} options={(d.quarterOptions || []).map(q => ({ value: q.value, label: `Q${q.value}` }))} onChange={next => toggleSet(d.quarters, next, q => on('quarter', { dataset: { q } }))} />
        <SbLabeledSelect label="等级" value={d.gradeIndex > 0 ? d.gradeIndex : undefined} options={(d.gradeOptions || []).map((o, i) => ({ value: i, label: o })).filter(o => o.value > 0)} onChange={i => on('grade', { detail: { value: i ?? 0 } })} />
        <SbLabeledSelect label="产品线" value={d.productIndex > 0 ? d.productIndex : undefined} options={(d.productOptions || []).map((o, i) => ({ value: i, label: o })).filter(o => o.value > 0)} onChange={i => on('product', { detail: { value: i ?? 0 } })} />
        {filterActive ? <Button type="link" size="small" onClick={() => on('resetFilters')}>重置</Button> : null}
      </div>
      {d.membersLoading ? <p className="ds-muted ds-fd-note">正在加载团队成员…</p> : d.membersError ? <p className="ds-fd-error ds-fd-note">{d.membersError} <Button type="link" size="small" onClick={() => on('retryMembers')}>重试成员目录</Button></p> : null}
      <p className="ds-fd-count"><b>{props.customerId ? '当前客户业务全景，包含本人未参与项目' : '有效协助商机，按项目去重'} · {d.count} 个</b><span className="ds-muted">在推项目 ACV {d.acv} 万元</span></p>
      <SbTable rowKey="id" density="compact" columns={columns} rows={listState === 'normal' ? d.filtered : []} state={listState} scrollX={860}
        emptyTitle={listState === 'error' ? '加载失败' : d.query || (d.selectedStages || []).length || (d.quarters || []).length || d.productIndex ? '当前筛选没有匹配项目' : props.customerId ? '当前客户暂无商机' : '尚无有效协助项目'}
        emptyDescription={listState === 'error' ? d.error : listState === 'empty' && !filterActive && !props.customerId ? '由有权销售或负责人加入名单后显示' : undefined}
        onRetry={() => on('load')} onClear={filterActive ? () => on('resetFilters') : undefined} onRowClick={r => on('open', { dataset: { id: r.id } })} />
      {listState === 'normal' && (d.hasMore || d.loadingMore || d.moreError) && <div className="ds-fd-more"><span className="ds-muted">已显示 {(d.filtered || []).length} / {d.count} 个项目</span>{d.moreError && <span className="ds-fd-error">{d.moreError}</span>}<Button size="small" loading={Boolean(d.loadingMore)} onClick={() => on('loadMore')}>{d.moreError ? '重试' : '加载更多'}</Button></div>}
    </section>
  </div>;
}
const projectProps = d => ({ initialScope: d.fdeScope || '', memberId: d.fdeMemberId || '', customerId: d.fdeCustomerId || '' });
export function supportsFdeWorkbench(page, data = {}) { return Boolean(page && page.route === 'pages/workbench/index' && data.isFde); }
FdeWorkbench.subcomponents = (page, d) => [{ selector: '#fdeContent', props: projectProps(d) }];
export function FdeWorkbench({ data: d, invokeOn, select }) {
  if (d.accessBlocked) return <SbStatePanel state="forbidden" description={d.accessMessage} />;
  return <section className="ds-fd-page" aria-label="协助商机"><FdeProjects projects={select('#fdeContent')} invokeOn={invokeOn} /></section>;
}
export function supportsFdeOpportunityBoard(page, data = {}) { return Boolean(page && page.route === 'pages/opportunities/index' && data.isFde); }
FdeOpportunityBoard.subcomponents = (page, d) => [{ selector: '#fdeContent', props: projectProps(d) }];
export function FdeOpportunityBoard({ data: d, invokeOn, select }) {
  if (d.accessBlocked) return <SbStatePanel state="forbidden" description={d.accessMessage} />;
  return <section className="ds-fd-page" aria-label="协助商机"><FdeProjects projects={select('#fdeContent')} invokeOn={invokeOn} /></section>;
}
