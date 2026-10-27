import React from 'react';
import {Button, Progress} from 'antd';
import {SbAiBadge, SbLabeledSelect, SbMetricStrip, SbSegmented, SbStatePanel, SbStatusTag, SbTable, SbTabs} from '@shandiant/ui-react';
import './opportunity-detail.css';
import {AdviceActions} from './WorkflowCompletion.jsx';
import {AdviceDrawer} from './WorkflowOverlays.jsx';
import {adviceEntries} from './advice-bridge.js';
import QuarterFinancial from './QuarterFinancial.jsx';
import OpportunityTransfer from './opportunity-access/OpportunityTransfer.jsx';
import OpportunityChangeHistory from './OpportunityChangeHistory.jsx';

// 商机详情（原 pages/customer-assets/index 带 opportunity_id 的形态）。FDE 视角多出录入拜访、创建 Demo 两个入口，Demo 场景页签，跟进记录里可切到本人归档记录。
export function supportsOpportunityDetail(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-assets/index' && data.opportunityId && data.opportunity && !data.opportunityLoading && !data.opportunityError);
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';

function PageMoreButton({ onClick }) { return <div className="ds-od-more"><Button size="small" onClick={onClick}>加载更多</Button></div>; }
function PageMore({ state, section, call }) {
  if (!state) return null;
  if (state.error) return <div className="ds-od-more"><span role="alert">{state.error}</span><Button size="small" onClick={() => call('retryOpportunitySection', { dataset: { section } })}>重试</Button></div>;
  if (state.loading) return <div className="ds-od-more ds-muted">正在加载…</div>;
  if (state.hasMore) return <div className="ds-od-more"><Button size="small" onClick={() => call('moreOpportunitySection', { dataset: { section } })}>加载更多</Button></div>;
  return null;
}

OpportunityDetail.subcomponents = (_, data) => adviceEntries(data.opportunityAdvice?.[data.opportunityTab], Boolean(!data.readOnly && data.canOpportunityAdvice && data.adviceExpanded?.[data.opportunityTab]));
export default function OpportunityDetail({ page, data: d, invoke, invokeOn }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const o = d.opportunity;
  const profileCustomerId = typeof o.customer_id === 'string' ? o.customer_id.trim() : '';
  const tab = d.opportunityTab || 'overview';
  const advice = (d.opportunityAdvice || {})[tab] || {};
  const advicePending = ['queued', 'running'].includes(advice.status);
  const adviceBusy = advice.status === 'loading' || advice.checking;
  const pages = d.detailPages || {};
  const summary = d.detailSummary || {};
  const tabs = d.isFde ? (d.opportunityTabs || []) : (d.opportunityTabs || []).filter(t => t.key !== 'demo');
  const fde = Boolean(d.isFde);
  const facts = [['商机名称', o.name], ['商机阶段', o.stageText], ['阶段概率', `${o.probability ?? '—'}%`], ['ACV（商机金额）', o.amount], ['预计签约', o.expectedDate], ['所属伙伴', o.partner_name || '未填写'], ['产品线', o.product_line || '未填写'], ['负责人', o.ownerLabel], ['所属团队', o.teamLabel], ['创建时间', o.createdLabel], ['更新时间', o.updatedLabel]];
  const taskCols = [
    { title: '任务', key: 'title', render: (_, r) => <div className="ds-od-two"><b>{r.title}</b>{r.description && <span className="ds-muted">{r.description}</span>}</div> },
    {title:'清单进度',key:'executionProgress',width:170,render:(_,r)=>r.showProgress?<><div>{r.progressView.label} · {r.progressView.countLabel}</div><small>{r.responsibilityLabel}</small></>:'—'},
    { title: '状态', key: 'status', width: 130, render: (_, r) => <SbStatusTag tone={toneOf(r.signal?.tone)} label={`${r.signal?.label || ''} · ${r.statusLabel}`} /> },
    { title: '负责人 / 时间', key: 'meta', width: 200, render: (_, r) => <span className="ds-muted">{r.assigneeName} · {r.timelineAt}</span> },
  ];
  const visitCols = [
    { title: '录入', key: 'recorded', width: 150, render: (_, r) => r.recordedAt },
    { title: '拜访 · 记录人', key: 'visit', width: 190, render: (_, r) => <span>{r.date} · {r.owner}</span> },
    { title: '沟通内容', key: 'copy', render: (_, r) => <span className="ds-od-clamp">{r.followUpRecord}</span> },
    { title: '状态', key: 'signal', width: 110, render: (_, r) => <SbStatusTag tone={toneOf(r.signal?.tone)} label={r.signal?.badgeText || r.signal?.label} /> },
  ];
  const events = d.progressEvents || [];
  const shownEvents = d.progressEventsExpanded ? events : events.slice(0, 3);
  return <section className="ds-odetail" aria-label="商机详情">
    <aside className="ds-panel ds-od-side">
      <p className="ds-od-eyebrow ds-muted">{o.customerName}</p>
      <h1>{o.name}</h1>
      <div className="ds-od-tags">
        <span className={`ds-od-grade ds-od-grade-${o.grade?.code || 'none'}`}>商机等级 {o.grade?.code || '未分级'}</span>
        <SbStatusTag tone={toneOf(o.signal?.tone)} label={`${o.signal?.label || '待评估'} · ${o.statusLabel}`} reason={o.signal?.reason} />
      </div>
      <div className="ds-od-actions">
        {!d.sharedReadOnly && <OpportunityTransfer opportunityId={d.opportunityId} onReadback={()=>call('loadOpportunity')} />}
        {d.webCanEditOpportunity && <Button type="primary" onClick={() => call('webEditOpportunity')}>编辑商机</Button>}
        {(d.canFollowUpThisOpportunity || fde && d.canRecordThisOpportunity) && <Button type="primary" onClick={() => call('recordFdeVisit')}>录入拜访</Button>}
        {fde && d.canRecordThisOpportunity && <Button onClick={() => call('openDemoScenes')}>创建 Demo</Button>}
        <Button disabled={!profileCustomerId || !d.canReadProfile} onClick={() => globalThis.SalesRuntime?.wx?.navigateTo({url: `/pages/customer-detail/index?id=${encodeURIComponent(profileCustomerId)}`})}>客户档案</Button>
        {!d.sharedReadOnly && <Button onClick={() => call('showAllOpportunities')}>客户全部实绩</Button>}
      </div>
      {!profileCustomerId && <p className="ds-muted ds-od-note">关联客户信息缺失，暂无法打开档案。</p>}
      <dl className="ds-od-facts">
        <div><dt>ACV（商机金额）</dt><dd className="ds-od-amount">{o.amount}</dd></div>
        <div><dt>当前阶段</dt><dd>{o.stageText}</dd></div>
        <div><dt>预计签约</dt><dd>{o.expectedDate || '未登记'}</dd></div>
        <div><dt>负责人</dt><dd>{o.ownerLabel} · {o.teamLabel}</dd></div>
      </dl>
      {fde && !d.canFollowUpThisOpportunity && !d.canRecordThisOpportunity && <p className="ds-muted ds-od-note">当前商机仅可查看，不在本人协助名单内</p>}
    </aside>
    <section className="ds-panel ds-od-main">
      <SbTabs activeKey={tab} onChange={key => call('selectOpportunityTab', { dataset: { tab: key } })} items={tabs.map(t => ({ key: t.key, label: t.label }))} />
      <div className="ds-od-body">
        {tab !== 'demo' && !summary.loaded && <p className="ds-od-summary ds-muted">{summary.loading ? '经营汇总加载中，其他资料可继续查看' : summary.error ? '经营汇总暂不可用，已加载资料不受影响' : '经营汇总按需加载'}{!summary.loading && <Button type="link" size="small" onClick={() => call('retryOpportunitySummary')}>{summary.error ? '重试' : '加载'}</Button>}</p>}
        {!d.sharedReadOnly && d.canReadOpportunityAdvice && tab !== 'demo' && <div className="ds-od-ai">
          <div className="ds-od-ai-head"><SbAiBadge state={advice.status === 'running' ? 'generating' : 'pending'} text={advice.checking ? '读取已有建议' : advice.status === 'loading' ? '正在提交生成请求' : advice.status === 'queued' ? '已有建议任务排队中' : advice.status === 'running' ? '已有建议任务处理中' : fde ? 'AI 建议 · FDE 专业建议' : 'AI 建议 · 销售专家建议'} /><span className="ds-muted">{o.name} · {tabs.find(t => t.key === tab)?.label}</span>
            <Button type="link" size="small" disabled={adviceBusy} onClick={() => call('loadOpportunityAdvice')}>{advice.checking ? '读取中' : '刷新已有建议'}</Button>
            {d.canOpportunityAdvice && !adviceBusy && !advicePending && <Button type="link" size="small" onClick={() => call('requestOpportunityAdvice', {dataset: {adviceAction: 'generate', opportunityId: d.opportunityId, section: tab}})}>{advice.id ? '重新生成建议' : '生成建议'}</Button>}
          </div>
          {advice.status === 'loading' && <p className="ds-muted">正在提交本次生成请求…</p>}
          {advicePending && <p className="ds-muted">已有任务{advice.status === 'queued' ? '等待后台处理' : '正在处理'}，可刷新已有建议查看进度；查看资料不会再创建任务。</p>}
          {advice.checking && <p className="ds-muted">正在读取已有建议，核验当前查看权限…</p>}
          {advice.status === 'error' && <p role="alert" className="ds-od-error">{advice.error}</p>}
          {advice.status === 'stale' && <p className="ds-muted">{advice.error || '原建议已失效。'}{d.canOpportunityAdvice ? '需要时可重新生成。' : ''}</p>}
          {advice.status === 'ready' && <>
            <p className="ds-od-ai-summary">{advice.summary}</p>
            <Button type="link" size="small" aria-expanded={Boolean(d.adviceExpanded?.[tab])} onClick={() => call('toggleAdvice')}>{d.adviceExpanded?.[tab] ? '收起建议' : '查看完整建议'}</Button>
            <AdviceDrawer open={Boolean(d.adviceExpanded?.[tab])} title={`${tabs.find(t => t.key === tab)?.label || '商机'} · 推进建议`} onClose={() => call('toggleAdvice')} footer={<Button onClick={() => call('toggleAdvice')}>完成，稍后再看</Button>}>
              <p className="ds-muted">{o.name}</p><p className="ds-od-ai-summary">{advice.summary}</p>
              <ol className="ds-od-ai-rows">{(advice.rows || []).map((row, i) => <li key={row.id || i}><b>{row.title}</b><span className="ds-muted">{row.detail}</span>{!d.readOnly && <AdviceActions page={page} advice={advice} suggestion={row} invokeOn={invokeOn}/>}</li>)}</ol>
              {!advice.rows?.length && <p className="ds-muted">本次没有需要补充的待办建议</p>}
            </AdviceDrawer>
            <p className="ds-muted ds-od-ai-source">基于当前商机关联资料生成 · 建议供参考{advice.updatedAt ? ` · ${advice.updatedAt}` : ''}</p>
          </>}
          {(!advice.status || advice.status === 'idle') && <p className="ds-muted">还没有生成建议。{d.canOpportunityAdvice ? '需要时点击“生成建议”。' : ''}</p>}
        </div>}
        {tab === 'demo' && fde && <>
          <div className="ds-od-demo-head"><h3 className="ds-od-h">Demo 场景 <small>{d.demoTotal} 个场景 · 关联当前商机，登记人和修改记录可追溯</small></h3>{d.canRecordThisOpportunity && <Button size="small" onClick={() => call('openDemoScenes')}>创建 Demo</Button>}</div>
          {d.demoError ? <p role="alert" className="ds-od-error">{d.demoError} <Button type="link" size="small" onClick={() => call('loadDemoScenes')}>重试</Button></p>
            : d.demosLoading && !(d.demos || []).length ? <p className="ds-muted">正在读取场景…</p>
              : !(d.demos || []).length ? <SbStatePanel state="empty" title="还没有 Demo 场景" description="点「创建 Demo」添加该商机的演示场景" />
                : <ol className="ds-od-demos">{(d.demos || []).map((s, i) => <li key={s.id}><button type="button" onClick={() => call('viewDemoScene', { dataset: { id: s.id } })}><span className="ds-od-demo-no">{i + 1}</span><b>{s.name}</b><span className="ds-muted">{s.creator_name || ''}</span></button></li>)}</ol>}
          {d.demosMore && !d.demosLoading && <Button size="small" onClick={() => call('moreDemoScenes')}>加载更多场景</Button>}
        </>}
        {tab === 'overview' && <>
          <h3 className="ds-od-h">商机完整信息</h3>
          <dl className="ds-od-grid">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
          <h3 className="ds-od-h">协助 FDE</h3>
          <div className="ds-od-fde">{(d.fdeMembers || []).length ? (d.fdeMembers || []).map(m => <span key={m.id || m.user_id || m.name} className="ds-od-chip">{m.name || m.display_name}</span>) : <span className="ds-muted">未指定</span>}<span className="ds-muted ds-od-fde-note">协助名单由有权人员维护</span></div>
          <h3 className="ds-od-h">季度预测</h3>
          {(o.quarterDetails || []).length ? <dl className="ds-od-grid">{o.quarterDetails.map(q => <div key={q.label}><dt>{q.label}</dt><dd>含税确收 {q.recognized} · 回款 {q.collection}</dd></div>)}</dl> : <p className="ds-muted">未填写季度预测</p>}
          {!d.sharedReadOnly && <div className="ds-od-actual">
            <div className="ds-od-actual-head"><h3 className="ds-od-h">季度实绩</h3>{(d.quarterActualOptions || []).length > 0 && <SbLabeledSelect label="季度" allowClear={false} value={d.quarterActualIndex} options={(d.quarterActualOptions || []).map((q, i) => ({ value: i, label: q.label }))} onChange={i => call('changeActualQuarter', { detail: { value: i } })} width={150} />}</div>
            {d.quarterActualLoading ? <p className="ds-muted">正在汇总季度实绩…</p> : d.quarterActualError ? <p role="alert" className="ds-od-error">{d.quarterActualError} <Button type="link" size="small" onClick={() => call('loadQuarterActuals')}>重新加载</Button></p> : <>
              <QuarterFinancial data={d} />
            </>}
          </div>}
        </>}
        {tab === 'tasks' && <>
          {pages.tasks?.syncError&&<p role="status" className="ds-muted">{pages.tasks.syncError}</p>}
          <div className="ds-od-strip"><SbMetricStrip columns={3} items={[{ key: 'total', label: '全部事项', value: d.taskStats?.total, missingText: '—' }, { key: 'done', label: '已完成', value: d.taskStats?.completed, missingText: '—' }, { key: 'pending', label: '未完成', value: d.taskStats?.pending, missingText: '—' }]} /></div>
          <SbTable rowKey="id" density="compact" columns={taskCols} rows={d.relatedTasks || []} state={pages.tasks?.loaded && !pages.tasks?.loading && !(d.relatedTasks || []).length ? 'empty' : 'normal'} emptyTitle="当前商机暂无已关联待办" emptyDescription="仅展示明确关联此商机的任务" onRowClick={r => call('openRelatedTask', { dataset: { id: r.id } })} />
          <PageMore state={pages.tasks} section="tasks" call={call} />
        </>}
        {tab === 'visits' && fde && <div className="ds-od-visit-mode"><SbSegmented value={d.fdeVisitMode || 'self'} options={[{ value: 'self', label: '我的拜访记录' }, { value: 'all', label: '全部跟进' }]} onChange={mode => call('changeFdeVisitMode', { dataset: { mode } })} /></div>}
        {tab === 'visits' && fde && (d.fdeVisitMode || 'self') === 'self' && <>
          <p className="ds-muted ds-od-caption">{d.fdeOwnTotal ?? 0} 条本人填写并确认归档的记录 · 新老客户均计入</p>
          <SbTable rowKey="id" density="compact" rows={d.fdeOwnVisits || []} state={d.fdeOwnLoading && !(d.fdeOwnVisits || []).length ? 'loading' : d.fdeOwnError ? 'error' : !(d.fdeOwnVisits || []).length ? 'empty' : 'normal'} emptyTitle={d.fdeOwnError ? '本人记录读取失败' : '你还没有在这条商机下确认归档拜访记录'} emptyDescription={d.fdeOwnError || undefined} onRetry={() => call('retryFdeOwnVisits')} onRowClick={r => call('openFdeOwnVisit', { dataset: { id: r.id } })} columns={[
            { title: '拜访日期', key: 'date', width: 170, render: (_, r) => r.dateLabel },
            { title: '商机', key: 'opp', render: (_, r) => r.opportunity_name },
            { title: '记录人', key: 'rec', width: 130, render: (_, r) => r.recorder_name },
            { title: '', key: 'link', width: 130, render: (_, r) => <span className="ds-muted">{r.can_read_detail ? '查看我的记录' : '本人归档摘要'}</span> },
          ]} />
          {d.fdeOwnMore && !d.fdeOwnLoading && <PageMoreButton onClick={() => call('moreFdeOwnVisits')} />}
        </>}
        {tab === 'visits' && (!fde || (d.fdeVisitMode || 'self') === 'all') && <>
          <p className="ds-muted ds-od-caption">{d.relatedVisitCount ?? (d.relatedVisits || []).length} 条已关联跟进记录 · 最新录入在前</p>
          <SbTable rowKey="id" density="compact" columns={visitCols} rows={d.relatedVisits || []} state={pages.visits?.loaded && !pages.visits?.loading && !(d.relatedVisits || []).length ? 'empty' : 'normal'} emptyTitle="当前商机暂无已关联跟进" emptyDescription="未关联商机的客户跟进不会计入" onRowClick={r => call('openRelatedVisit', { dataset: { id: r.id } })} />
          <PageMore state={pages.visits} section="visits" call={call} />
        </>}
        {tab === 'opportunity' && <>
          <OpportunityChangeHistory history={d.changeHistory} call={call} />
          <div className="ds-od-stage">
            <div className="ds-od-stage-head"><span>当前商机阶段</span><SbStatusTag tone={toneOf(o.signal?.tone)} label={`${o.signal?.label || ''} · ${o.statusLabel}`} /></div>
            <b className="ds-od-stage-name">{o.stageText}</b>
            <Progress percent={Number(o.probability) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
            <p className="ds-muted">{o.signal?.reason}</p>
            <p className="ds-muted">预计签约 {o.expectedDate}</p>
          </div>
          <div className="ds-od-events-head"><h3 className="ds-od-h">已记录动态 <small>{d.progressEventsExpanded ? '按时间倒序' : '最新 3 条'}</small></h3>{events.length > 3 && <Button type="link" size="small" onClick={() => call('toggleProgressEvents')}>{d.progressEventsExpanded ? '收起' : '展开全部'}</Button>}</div>
          {events.length ? <ol className="ds-od-events">{shownEvents.map(ev => <li key={ev.key}><button type="button" onClick={() => call('openProgressEvent', { dataset: { key: ev.key } })}><b>{ev.title}</b><span className="ds-muted">{ev.date}</span><span>{ev.detail}</span></button></li>)}</ol> : <SbStatePanel state="empty" title="还没有记录动态" />}
          {(d.progressEventsExpanded || events.length < 3) && <PageMore state={pages.timeline} section="timeline" call={call} />}
        </>}
      </div>
    </section>
  </section>;
}
