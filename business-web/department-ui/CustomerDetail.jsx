import React from 'react';
import { Button, Progress } from 'antd';
import { SbAiBadge, SbListRow, SbMetricStrip, SbStatePanel, SbStatusTag, SbTable, SbTabs } from '@shandiant/ui-react';
import './customer-detail.css';

// 客户详情（原 pages/customer-detail/index）。左栏是客户身份与象限依据，右栏三个页签。业务数据与分页都是原 Page 的。
// FDE 视角：同一页，只是没有 Agent 经营建议，改成显示已记录的下一步行动。
export function supportsCustomerDetail(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-detail/index' && data.customer);
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const TONE_LABEL = { good: '向好', watch: '需关注', bad: '转差', pending: '待评估' };
const navigate = url => globalThis.SalesRuntime?.wx?.navigateTo({ url });

function PageMore({ state, section, call, more, retry }) {
  if (!state) return null;
  if (state.error) return <div className="ds-cd-more"><span role="alert">{state.error}</span><Button size="small" onClick={() => call(retry, { dataset: { section } })}>重试</Button></div>;
  if (state.loading) return <div className="ds-cd-more ds-muted">正在加载…</div>;
  if (state.hasMore) return <div className="ds-cd-more"><Button size="small" onClick={() => call(more, { dataset: { section } })}>加载更多</Button></div>;
  return null;
}

export default function CustomerDetail({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const c = d.customer;
  const pages = d.detailPages || {};
  const stageCols = [
    { title: '商机', key: 'name', width: '38%', render: (_, r) => <div className="ds-cd-two"><b>{r.name}</b><span className="ds-muted">{r.stageText}</span></div> },
    { title: '推进', key: 'progress', width: 180, render: (_, r) => <Progress percent={Number(r.probability) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /> },
    { title: '金额', key: 'amount', width: 110, align: 'right', render: (_, r) => <b>{r.amount}</b> },
    { title: '预计签约', key: 'date', width: 120, render: (_, r) => r.expectedDate || <span className="ds-muted">未登记</span> },
  ];
  const visitCols = [
    { title: '拜访', key: 'date', width: 170, render: (_, r) => <div className="ds-cd-two"><b>{r.date}</b><span className="ds-muted">{r.mode}</span></div> },
    { title: '主题', key: 'title', render: (_, r) => r.title },
    { title: '结果', key: 'conclusion', width: 110, render: (_, r) => r.conclusion },
    { title: '记录人', key: 'owner', width: 110, render: (_, r) => r.owner },
  ];
  const visitExpanded = r => {
    const st = d.visitDetailState || {};
    if (st.loading) return <p className="ds-muted">正在读取完整记录…</p>;
    if (st.error) return <p><span role="alert">{st.error}</span> <Button type="link" size="small" onClick={() => call('retryVisitDetail')}>重试</Button></p>;
    if (r.isSummary || !st.ready) return <Button type="link" size="small" onClick={() => call('retryVisitDetail')}>点击读取完整记录</Button>;
    const rows = [['客户名称', r.customerName], ['客户类型', r.customerType], ['商机名称', r.opportunityName], ['伙伴名称', r.partnerName], ['对接人', r.contactNames], ['拜访目标', r.visitGoal], ['沟通内容', r.followUpRecord], ['下一步计划', r.nextAction], ['拜访时间', r.date], ['创建时间', r.createdAt], ['创建人', r.creator], ['跟进人', r.owner], ['协同人', r.collaborators], ['拜访方式', r.mode], ['拜访时长', r.duration], ['达成结果', r.expectation], ['是否首次拜访', r.firstVisitText], ['是否七天内', r.sevenDaysText], ['地点', r.location],
      ...(r.isFirstVisit ? [['客户主营业务', r.customerMainBusiness], ['客户需求', r.customerNeeds], ['客户预算', r.customerBudget], ['联系人角色', r.contactRole]] : [])];
    return <div className="ds-cd-visit">
      <dl className="ds-cd-dl">{rows.map(([k, v]) => <div key={k} className={k === '下一步计划' ? 'is-next' : ''}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      {d.canSupplementVisit && r.recorderId && r.recorderId === d.viewerUserId && <Button size="small" onClick={() => call('supplementVisit', { dataset: { id: r.id } })}>补充信息</Button>}
    </div>;
  };
  const summary = d.detailSummary || {};
  return <section className="ds-cdetail" aria-label="客户详情">
    <aside className="ds-panel ds-cd-side">
      <div className="ds-cd-identity">
        <span className="ds-cd-mark" aria-hidden="true">{c.initial}</span>
        <div className="ds-cd-id-main"><h1>{c.name}</h1><p className="ds-muted">{[c.industry, c.customerType, c.team].filter(Boolean).join(' · ')}</p></div>
      </div>
      <div className="ds-cd-tags">
        {c.level && <span className="ds-cd-chip">{c.level}级客户</span>}
        <span className="ds-cd-chip">{c.quadrant}</span>
        <SbStatusTag tone={toneOf(c.signal?.tone)} label={TONE_LABEL[toneOf(c.signal?.tone)]} reason={c.risk} showReason />
      </div>
      <p className="ds-muted ds-cd-owner">负责人 {c.owner} · {c.updatedAt}</p>
      {d.isFde && <p className="ds-muted ds-cd-owner">客户全景资料 · 本人协助项目以商机名单为准</p>}
      <div className="ds-cd-actions">
        {d.canRecordVisit && <Button type="primary" onClick={() => call('recordVisit')}>记录拜访</Button>}
        {d.canCreateTask && <Button onClick={() => call('createTask')}>创建任务</Button>}
        <Button onClick={() => call('openAssets')}>经营实绩</Button>
      </div>
      <div className="ds-cd-quadrant">
        <h2>象限判断依据</h2>
        <p>{c.quadrantDefinition}，建议{c.quadrantAction}</p>
        {[['客户潜力', c.potential, c.potentialEvidence], ['关系深度', c.relationship, c.relationshipEvidence]].map(([label, score, evidence]) => <div key={label} className="ds-cd-score">
          <div className="ds-cd-score-head"><span>{label}</span><b>{score}</b></div>
          <Progress percent={Number(score) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
          <ul className="ds-muted">{(evidence || []).map(item => <li key={item}>{item}</li>)}</ul>
        </div>)}
        <p className="ds-muted ds-cd-rule">象限由事实数据自动计算；修改客户事实后，系统重新评估。</p>
      </div>
    </aside>
    <section className="ds-panel ds-cd-main">
      <SbTabs activeKey={d.activeTab} onChange={key => call('selectTab', { dataset: { tab: key } })} items={(d.tabs || []).map(t => ({ key: t.key, label: t.label }))} />
      {!summary.loaded && <p className="ds-cd-summary ds-muted">{summary.loading ? '经营汇总加载中，其他资料可继续查看' : summary.error ? '经营汇总暂不可用，已加载资料不受影响' : '经营汇总按需加载'}{!summary.loading && <Button type="link" size="small" onClick={() => call('retryCustomerSummary')}>{summary.error ? '重试' : '加载'}</Button>}</p>}
      {d.detailFocusError && <p role="alert" className="ds-cd-error">{d.detailFocusError}</p>}
      <div className="ds-cd-body">
        {d.activeTab === 'overview' && <>
          <div className="ds-cd-strip"><SbMetricStrip columns={3} items={[
            { key: 'acv', label: '在推商机 ACV', value: c.annualValue, missingText: '—' },
            { key: 'current', label: '当前商机 ACV', value: c.opportunity?.amount, missingText: '—' },
            { key: 'years', label: '合作时间', value: c.cooperationPending ? null : c.cooperationYears, missingText: '未登记' },
          ]} /></div>
          <div className="ds-cd-ai">
            <div className="ds-cd-ai-head">{d.isFde ? <b>已记录的下一步行动</b> : <><SbAiBadge state="pending" text="AI 生成 · 经营建议" /><span className="ds-muted">基于最新客户事实生成</span></>}</div>
            <p className="ds-cd-ai-text">{c.nextAction}</p>
            {d.canCreateTask && <Button type="link" size="small" onClick={() => call('createTask')}>创建下一步任务 →</Button>}
          </div>
          <h3 className="ds-cd-h">当前商机 <small>{c.opportunityCount} 个</small></h3>
          <SbTable rowKey="id" density="compact" columns={stageCols} rows={c.opportunities || []} state={pages.opportunities?.loaded && !c.opportunities?.length ? 'empty' : 'normal'} emptyTitle="暂无商机"
            onRowClick={r => navigate(`/pages/customer-assets/index?customer_id=${encodeURIComponent(c.id)}&opportunity_id=${encodeURIComponent(r.id)}&period=all&readonly=1`)} />
          <PageMore state={pages.opportunities} section="opportunities" call={call} more="moreSection" retry="retrySection" />
          <h3 className="ds-cd-h">关键联系人 <small>{c.contactCount} 人</small></h3>
          <div className="ds-cd-contacts">{(c.contacts || []).map(p => <SbListRow key={p.id} name={<span>{p.name} <small className="ds-muted">{p.title}</small></span>} summary={p.role} status={{ tone: p.strengthTone === 'strong' ? 'good' : 'pending', label: p.strength }} />)}</div>
          <PageMore state={pages.contacts} section="contacts" call={call} more="moreSection" retry="retrySection" />
        </>}
        {d.activeTab === 'visits' && <>
          <SbTable rowKey="id" density="compact" columns={visitCols} rows={c.visits || []} state={pages.visits?.loaded && !c.visits?.length ? 'empty' : 'normal'} emptyTitle="还没有拜访记录"
            expandable={{ columnWidth: 28, expandedRowKeys: d.expandedVisitId ? [d.expandedVisitId] : [], onExpand: (_, r) => call('toggleVisit', { dataset: { id: r.id } }), expandedRowRender: visitExpanded }} />
          <PageMore state={pages.visits} section="visits" call={call} more="moreSection" retry="retrySection" />
        </>}
        {d.activeTab === 'opportunity' && <>
          {d.canEditOpportunity && <div className="ds-cd-toolbar"><Button type="primary" onClick={() => call('createOpportunity')}>新增商机</Button></div>}
          {(c.opportunities || []).map(o => <article key={o.id} className="ds-cd-opp">
            <header><div><span className="ds-cd-eyebrow">{o.status === 'won' ? '已赢单' : o.status === 'lost' ? '已丢单' : '推进中'}</span><h4>{o.name}</h4></div><div className="ds-cd-opp-right"><b>{o.amount}</b>{d.canEditOpportunity && <Button type="link" size="small" onClick={() => call('editOpportunity', { dataset: { id: o.id } })}>编辑</Button>}</div></header>
            <ol className="ds-cd-steps">{(o.stageSteps || []).map(s => <li key={s.label} className={`is-${s.status}`}>{s.label}</li>)}</ol>
            <dl className="ds-cd-dl ds-cd-dl-inline">
              <div><dt>所属伙伴</dt><dd>{o.partner_name || '未填写'}</dd></div>
              <div><dt>产品线</dt><dd>{o.product_line || '未填写'}</dd></div>
              {(o.quarterDetails || []).map(q => <div key={q.label}><dt>{q.label} 预测</dt><dd>含税确收 {q.recognized} · 回款 {q.collection}</dd></div>)}
            </dl>
          </article>)}
          {pages.opportunities?.loaded && !c.opportunities?.length && <SbStatePanel state="empty" title="暂无商机" />}
          <PageMore state={pages.opportunities} section="opportunities" call={call} more="moreSection" retry="retrySection" />
          <div className="ds-cd-risk"><b>当前风险</b><span>{c.riskDetail}</span></div>
        </>}
      </div>
    </section>
  </section>;
}
