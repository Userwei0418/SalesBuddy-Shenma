import React from 'react';
import PresentationSwitch from './PresentationSwitch.jsx';
import { Button, Progress } from 'antd';
import { SbAmountInput, SbField, SbLabeledSelect, SbSegmented, SbSheet, SbTextarea } from '@shandiant/ui-react';
import { SbAiBadge, SbMetricStrip, SbStatePanel, SbStatusTag, SbTable, SbTabs } from '@shandiant/ui-react';
import './profile.css';
// 金额控件收数字，页面里的字符串先转一下
const toAmount = v => (v === '' || v == null || Number.isNaN(Number(v))) ? null : Number(v);

// 我的（原 pages/profile/index）。销售、主管、总经理三种视角；主管与总经理多一行「看谁的」。
// FDE 视角：项目成效与 FDE 画像来自原子组件 fde-profile（内含季度目标 #fdeQuarterTarget），协作效率与排名是页面自己的数据。
export function supportsProfile(page) {
  return Boolean(page && page.route === 'pages/profile/index');
}
Profile.subcomponents = (page, d) => {
  if (!d.isFde) return [{selector:'#salesQuarterTarget',required:Boolean(d.canReadTargets && d.organizationReady && d.activeProfileTab==='maturity'),props:{scope:d.targetQueryScope || 'self',subjectId:d.targetQueryMemberId || '',teamId:d.profileTeamId || '',subjectLabel:d.profileScopeLabel || '',year:d.targetYear || 0,quarter:d.targetQuarter || 0,editable:Boolean(d.canEditSalesTarget),contextKey:d.targetContextKey || ''}}];
  const fp = page.selectComponent('fde-profile'), f = fp?.data || {};
  const person = d.fdeProfileScope === 'self';
  return [
    { selector: 'fde-profile', required: d.activeProfileTab !== 'efficiency', props: { chartsHidden: Boolean(d.scopePickerOpen), embedded: true, identityKey: d.viewerIdentity || '', profileScope: d.fdeProfileScope || 'self', selectedTeamId: d.fdeTeamId || '', selectedMemberId: person ? d.fdeMemberId || '' : '', selectedMemberName: person ? d.fdeMemberName || '' : '', section: d.activeProfileTab || 'maturity', userName: d.userName, roleName: d.roleName, team: d.team, account: d.account, initial: d.initial } },
    { selector: '#fdeQuarterTarget', parent: 'fde-profile', required: Boolean(fp && d.canReadTargets && d.activeProfileTab === 'maturity' && f.metricsLoading === false && !f.metricsError), props: { scope: d.fdeProfileScope === 'team' ? 'team' : (person && d.fdeMemberId) ? 'person' : 'self', teamId: d.fdeTeamId || '', subjectId: person ? d.fdeMemberId || '' : '', subjectLabel: (person && d.fdeMemberName) || d.userName || '', year: f.metricYear || 0, quarter: f.metricQuarter || 0, editable: Boolean(f.canEditOwnTargets), contextKey: f.targetContextKey || '' } },
  ];
};

/* 六维雷达：缺样本的维度按 0 画，文字标未评估 */
function Radar({ dimensions }) {
  const n = dimensions.length || 6, cx = 150, cy = 130, r = 95;
  const point = (i, k) => { const a = -Math.PI / 2 + i * 2 * Math.PI / n; return [cx + Math.cos(a) * r * k, cy + Math.sin(a) * r * k]; };
  const ring = k => dimensions.map((_, i) => point(i, k).map(v => v.toFixed(1)).join(',')).join(' ');
  const shape = dimensions.map((dim, i) => point(i, Math.max(0, Math.min(100, Number(dim.score) || 0)) / 100).map(v => v.toFixed(1)).join(',')).join(' ');
  return <svg className="ds-pf-radar" viewBox="0 0 300 260" role="img" aria-label="六维能力画像，缺少样本的维度显示未评估">
    {[0.25, 0.5, 0.75, 1].map(k => <polygon key={k} points={ring(k)} className="ds-pf-radar-grid" />)}
    {dimensions.map((_, i) => { const [x, y] = point(i, 1); return <line key={i} x1={cx} y1={cy} x2={x} y2={y} className="ds-pf-radar-grid" />; })}
    <polygon points={shape} className="ds-pf-radar-shape" />
    {dimensions.map((dim, i) => { const [x, y] = point(i, 1.22); return <text key={dim.code} x={x} y={y} textAnchor="middle" dominantBaseline="middle" className="ds-pf-radar-label">{dim.shortName || dim.name} {dim.scoreText}</text>; })}
  </svg>;
}

/* 季度目标：qt 是 quarter-target 实例，条 + 抽屉 */
function QuarterTarget({ qt, invokeOn }) {
  if (!qt) return null;
  const q = qt.data || {};
  const on = (name, payload) => invokeOn(qt, name, payload);
  return <div className="ds-pf-target">
    <div className="ds-pf-target-bar"><b>{q.quarterLabel} · 季度目标</b>{q.canEdit && <Button size="small" disabled={Boolean(q.loading) || (q.pending || []).length > 0} onClick={() => on('show')}>{(q.pending || []).length ? '变更待审批' : '设置目标'}</Button>}</div>
    {q.notice && <p className="ds-muted ds-pf-target-note">{q.notice}{(q.rows || []).filter(r => r.pending).map(r => <span key={r.kind}> · {r.name} {r.currentText} → {r.proposedText}</span>)}</p>}
    {q.decision && !(q.pending || []).length && <p className="ds-muted ds-pf-target-note">最近目标调整 · {q.decision.label} · {q.decision.reviewer}{q.decision.date ? ` · ${q.decision.date}` : ''}{q.decision.reason ? ` · ${q.decision.reason}` : ''}</p>}
    {q.error && !q.open && <p className="ds-pf-error">{q.error} <Button type="link" size="small" onClick={() => on('load')}>重试</Button></p>}
    <SbSheet open={Boolean(q.open)} title={<span>季度目标 <small className="ds-muted">{qt.properties?.subjectLabel || '本人'} · {q.quarterLabel}</small></span>} onClose={() => on('close')} footer={<><Button disabled={Boolean(q.saving)} onClick={() => on('close')}>取消</Button><Button type="primary" loading={Boolean(q.saving)} onClick={() => on('save')}>{q.isChange ? '提交目标调整' : '确认并生效'}</Button></>}>
      <div className="ds-pf-target-form">
        <p className="ds-muted">记录已与负责人商定的目标。首次填写直接生效，后续调整由运营审核。</p>
        {(q.rows || []).map(r => <SbField key={r.kind} label={`${r.name}目标（万元）`} required help={`当前生效：${r.currentText}`}>
          <SbAmountInput value={toAmount(q[r.kind])} placeholder={`填写${r.name}目标`} width="100%" onChange={value => on('input', { dataset: { field: r.kind }, detail: { value: value === null ? '' : String(value) } })} />
        </SbField>)}
        <SbField label={q.isChange ? '调整原因' : '商定依据'} required>
          <SbTextarea value={q.reason || ''} maxLength={2000} autoSize={{ minRows: 3, maxRows: 6 }} placeholder="说明与负责人的约定，或本次目标调整的原因" onChange={v => on('input', { dataset: { field: 'reason' }, detail: { value: v } })} />
        </SbField>
        {q.error && <p className="ds-pf-error" role="alert">{q.error}</p>}
      </div>
    </SbSheet>
  </div>;
}

/* FDE 画像与项目成效：fp 是 fde-profile 实例 */
function FdeProfileSection({ fp, qt, invokeOn, section, scopeLabel }) {
  if (!fp) return <SbStatePanel state="loading" title="正在准备画像" />;
  const f = fp.data || {};
  const on = (name, payload) => invokeOn(fp, name, payload);
  if (section === 'maturity') return <>
    <div className="ds-pf-section-head"><h2>{scopeLabel} · 项目成效</h2><div className="ds-pf-score ds-pf-score-pending ds-pf-score-static"><b>—</b><span>分</span><small>待评估 · 总分 / 100</small></div></div>
    {f.metricsLoading ? <p className="ds-muted">正在加载项目成效…</p> : f.metricsError ? <p><span className="ds-pf-error" role="alert">{f.metricsError}</span> <Button type="link" size="small" onClick={() => on('loadMetrics')}>重试</Button></p> : <>
      {f.canReadTargets !== false && <QuarterTarget qt={qt} invokeOn={invokeOn} />}
      <div className="ds-pf-board">{(f.performanceBoard || []).map(m => <div key={m.kind} className="ds-pf-metric">
        <div className="ds-pf-metric-head"><b>{m.name}</b><span className="ds-muted">{m.rateText}</span></div>
        <div className="ds-pf-metric-value"><span className="ds-muted">当季度已登记</span><b>{m.completedText}</b></div>
        <Progress percent={m.progress || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
        <div className="ds-pf-metric-target ds-muted">{m.targetLabel} <b>{m.targetText}</b></div>
      </div>)}</div>
      {f.targetNotice && <p className="ds-muted">{f.targetNotice}</p>}
      <div className="ds-pf-facts"><SbMetricStrip columns={2} variant="card" items={(f.maturityFacts || []).map(x => ({ key: x.name, label: x.name, value: x.value, note: x.detail, missingText: '待计算' }))} /></div>
    </>}
  </>;
  if (section !== 'profile') return null;
  return <>
    <div className="ds-pf-section-head"><h2>{scopeLabel} · 六维能力画像</h2><div className="ds-pf-score ds-pf-score-pending ds-pf-score-static"><b>{f.portraitScore}</b><span>分</span><small>{f.portraitScoreLabel}</small></div></div>
    {f.loading ? <SbStatePanel state="loading" title="正在更新个人画像" /> : f.error ? <SbStatePanel state="error" title={f.error} onRetry={() => on('load')} /> : f.ready && <>
      <div className="ds-pf-portrait">
        <Radar dimensions={f.dimensions || []} />
        <div className="ds-pf-dims">
          {!f.sampleCount && <p className="ds-muted">暂无可评估的归档记录，缺少样本的维度显示未评估</p>}
          {f.summary && <p className="ds-pf-summary">{f.summary}</p>}
          <p className="ds-muted ds-pf-meta">所选人员近期记录与任务 · {f.updatedText} · 北京时间</p>
          <SbTable rowKey="code" density="compact" rows={f.dimensions || []} columns={[
            { title: '能力', key: 'name', width: 140, render: (_, r) => <b>{r.name}</b> },
            { title: '得分', key: 'score', width: 200, render: (_, r) => <div className="ds-pf-dim"><Progress percent={Number(r.width) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /><span className={r.score === null ? 'ds-muted' : ''}>{r.scoreText}</span></div> },
            { title: '评估', key: 'assessment', render: (_, r) => r.assessment },
          ]} />
        </div>
      </div>
      <div className="ds-pf-advice">
        <div className="ds-pf-advice-head"><SbAiBadge state={f.reviewBusy ? 'generating' : 'pending'} text={f.reviewBusy ? 'AI 生成中' : 'AI 生成 · FDE 专业成长建议'} /><span className="ds-muted">{f.reviewMessage}</span>{f.canReview && <Button type="link" size="small" disabled={Boolean(f.reviewBusy) || f.reviewStatus === 'empty'} onClick={() => on('generateAdvice')}>{f.reviewBusy ? '正在生成…' : '检查建议更新'}</Button>}</div>
        {f.reviewError && <p className="ds-pf-error" role="alert">{f.reviewError}</p>}
        <ol>{(f.advice || []).map((item, i) => <li key={i}><b>{item.title}</b> {item.content}</li>)}</ol>
        <p className="ds-muted">AI 建议供本人参考，结合实际情况决定下一步行动。</p>
      </div>
      <div className="ds-pf-growth-head"><h3 className="ds-pf-h">成长曲线 <small>近 30 天 · 已保存记录</small></h3><SbSegmented value={f.historyCode} options={(f.dimensions || []).map(dim => ({ value: dim.code, label: dim.name }))} onChange={code => on('selectHistory', { dataset: { code } })} /></div>
      {(f.historyPoints || []).some(pt => pt.score !== null) ? <Sparkline history={(f.historyPoints || []).map(pt => ({ overall_score: pt.score }))} code="overall" /> : <p className="ds-muted ds-pf-empty">暂无已保存的历史画像</p>}
    </>}
  </>;
}
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';

function ScoreTile({ score, info, kind, call }) {
  const tone = toneOf(info?.signal?.tone);
  return <button type="button" className={`ds-pf-score ds-pf-score-${tone}`} onClick={() => call('showScoreRule', { dataset: { kind } })} aria-label="查看评分规则">
    <b>{score ?? '--'}</b><span>分</span>
    <small>{info?.signal?.label || '待评估'} · 总分 / 100 ⓘ</small>
  </button>;
}

export function Sparkline({ history = [], code = 'overall' }) {
  const values = history.map(item => code === 'overall' ? item.overall_score : (item.dimension_scores || {})[code]?.score).map(v => (v == null ? null : Number(v)));
  const points = values.map((v, i) => [i, v]).filter(([, v]) => v != null && Number.isFinite(v));
  if (points.length < 2) return <p className="ds-muted ds-pf-empty">完成两轮以上每日复盘后开始形成成长曲线</p>;
  const w = 600, h = 140, pad = 12;
  const xs = i => pad + i / Math.max(1, history.length - 1) * (w - pad * 2);
  const ys = v => h - pad - Math.max(0, Math.min(100, v)) / 100 * (h - pad * 2);
  const path = points.map(([i, v], k) => `${k ? 'L' : 'M'}${xs(i).toFixed(1)},${ys(v).toFixed(1)}`).join(' ');
  return <svg className="ds-pf-spark" viewBox={`0 0 ${w} ${h}`} role="img" aria-label="成长曲线">
    {[0, 50, 100].map(v => <line key={v} x1={pad} x2={w - pad} y1={ys(v)} y2={ys(v)} className="ds-pf-spark-grid" />)}
    <path d={path} className="ds-pf-spark-line" />
    {points.map(([i, v]) => <circle key={i} cx={xs(i)} cy={ys(v)} r="3" className="ds-pf-spark-dot" />)}
  </svg>;
}

export default function Profile({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const tab = d.activeProfileTab || 'maturity';
  if (d.isFde) return <FdeProfile page={page} d={d} call={call} invokeOn={invokeOn} select={select} tab={tab} />;
  const dimCols = [
    { title: '能力', key: 'name', width: 140, render: (_, r) => <b>{r.name}</b> },
    { title: '得分', key: 'score', width: 220, render: (_, r) => <div className="ds-pf-dim"><Progress percent={r.score === '--' ? 0 : Number(r.score)} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /><SbStatusTag tone={toneOf(r.signal?.tone)} label={`${r.score} · ${r.signal?.label || '待评估'}`} /></div> },
    { title: '提升方法', key: 'coach', render: (_, r) => r.coachingAction },
  ];
  return <section className="ds-profile" aria-label="我的"><PresentationSwitch data={d} invoke={call}/>
    <div className="ds-panel ds-pf-head">
      <span className="ds-pf-avatar" aria-hidden="true">{d.initial}</span>
      <div className="ds-pf-id"><b>{d.userName}</b><span className="ds-muted">{d.roleName} · {d.team}</span></div>
      <SbStatusTag tone="good" label="已登录" />
      <Button className="ds-pf-logout" onClick={() => call('logout')} disabled={d.logoutBusy} loading={d.logoutNavigating}>退出登录 / 切换账号</Button>
    </div>
    <div className="ds-panel ds-pf-main">
      <SbTabs activeKey={tab} onChange={key => call('selectProfileTab', { dataset: { tab: key } })} items={(d.profileTabs || []).map(t => ({ key: t.key, label: t.label }))} />
      {!!d.profileScopeOptions?.length && <div className="ds-pf-scope">
        <SbSegmented value={d.profileScopeMode} options={(d.profileScopeOptions || []).map(o => ({ value: o.value, label: o.label }))} onChange={mode => call('selectProfileScope', { detail: { mode } })} />
        {d.profileScopeMode === 'team' && (d.scopeTeams || []).length > 1 && <SbLabeledSelect label="团队" allowClear={false} value={d.profileTeamId || undefined} options={(d.scopeTeams || []).map(t => ({ value: t.id, label: t.name }))} onChange={id => call('changeProfileSubject', { detail: { kind: 'team', id } })} width={220} />}
        {d.profileScopeMode === 'person' && <SbLabeledSelect label="成员" allowClear={false} value={d.profileSelectedMemberId || undefined} options={(d.scopeMembers || []).map(m => ({ value: m.id, label: m.team ? `${m.name}（${m.team}）` : m.name }))} onChange={id => call('changeProfileSubject', { detail: { kind: 'person', id } })} width={260} />}
        <span className="ds-muted">当前：{d.profileScopeLabel}</span>
      </div>}
      <div className="ds-pf-body">
        {d.directoryLoading && <p className="ds-muted">正在加载查看范围…</p>}
        {d.directoryError && <p><span role="alert" className="ds-pf-error">{d.directoryError}</span> <Button type="link" size="small" onClick={() => call('loadProfileDirectory')}>重试</Button></p>}
        {d.organizationError && (tab !== 'profile') && <p><span role="alert" className="ds-pf-error">{d.organizationError}</span> <Button type="link" size="small" onClick={() => call('retryEfficiency')}>重试</Button></p>}
        {d.performanceError ? <p><span role="alert" className="ds-pf-error">{d.performanceError}</span> <Button type="link" size="small" onClick={() => call('loadProfilePerformance')}>重试</Button></p> : d.performanceLoading && <p className="ds-muted">正在更新经营数据…</p>}

        {tab === 'maturity' && !d.directoryLoading && !d.directoryError && <>
          <div className="ds-pf-section-head"><h2>{d.role === 'sales' ? (d.maturityScopeLabel || '经营事实') : `${d.profileScopeLabel} · 经营事实`}</h2><ScoreTile score={d.maturityScore} info={d.maturityScoreInfo} kind="maturity" call={call} /></div>
          {d.organizationLoading && !d.organizationReady && <p className="ds-muted">正在汇总营销成熟度…</p>}
          {d.organizationReady && <>
            {d.canReadTargets && <QuarterTarget qt={select('#salesQuarterTarget')} invokeOn={invokeOn}/>}
            <div className="ds-pf-board">{(d.performanceBoard || []).map(m => <div key={m.kind} className="ds-pf-metric">
              <div className="ds-pf-metric-head"><b>{m.name}</b><span className="ds-muted">{m.rateText}</span></div>
              <div className="ds-pf-metric-value"><span className="ds-muted">当季度已登记</span><b>{m.completedText}</b></div>
              <Progress percent={m.progress || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
              <div className="ds-pf-metric-target ds-muted">{m.targetLabel || `${d.targetYear} Q${d.targetQuarter} 个人目标`} <b>{m.targetText}</b></div>
            </div>)}</div>
            <div className="ds-pf-facts"><SbMetricStrip columns={Math.min(3, (d.maturityFacts || []).length || 3)} variant="card" items={(d.maturityFacts || []).map(f => ({ key: f.key, label: f.name, value: f.value, note: f.detail, missingText: '待计算' }))} /></div>
          </>}
        </>}

        {tab === 'efficiency' && !d.directoryLoading && !d.directoryError && <>
          <div className="ds-pf-section-head"><h2>{d.role === 'sales' ? '我的营销效率' : `${d.profileScopeLabel} · 营销效率`}</h2><ScoreTile score={d.efficiencyScore} info={d.efficiencyScoreInfo} kind="efficiency" call={call} /></div>
          {d.organizationLoading && !d.organizationReady && <p className="ds-muted">正在统计跟进效率…</p>}
          {d.organizationReady && <>
            <div className="ds-pf-switches">
              <SbSegmented size="middle" value={d.efficiencyMetric} options={(d.efficiencyMetrics || []).map(m => ({ value: m.key, label: m.label }))} onChange={v => call('selectEfficiencyMetric', { dataset: { metric: v } })} />
              <SbSegmented value={d.efficiencyPeriod} options={(d.efficiencyPeriods || []).map(p => ({ value: p.key, label: p.label }))} onChange={v => call('selectEfficiencyPeriod', { dataset: { period: v } })} />
            </div>
            {d.efficiencyMetric !== 'followup' ? <div className="ds-pf-ratio">
              <span className="ds-muted">{d.efficiencyRatio?.label}</span>
              <b>{d.efficiencyRatio?.value ?? '—'}</b>
              <Progress percent={Number(d.efficiencyRatio?.progress) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
              <p className="ds-muted">{d.efficiencyRatio?.detail}{d.efficiencyRatio?.coverage ? ` · ${d.efficiencyRatio.coverage}` : ''}</p>
            </div> : <div className="ds-pf-facts"><SbMetricStrip columns={2} variant="card" items={[
              { key: 'total', label: `跟进次数 · ${d.efficiencyAggregateLabel || ''}`, value: d.efficiencyTotal, note: d.efficiencyUnit },
              { key: 'avg', label: '跟进记录平均分', value: d.efficiencySecondaryValue === '--' ? null : d.efficiencySecondaryValue, missingText: '待评分' },
            ]} /></div>}
            <p className="ds-muted ds-pf-window">{d.efficiencyWindowLabel}</p>
          </>}
        </>}

        {tab === 'profile' && !d.directoryLoading && !d.directoryError && <>
          <div className="ds-pf-section-head"><h2>{d.role === 'sales' ? '六维能力画像' : `${d.profileScopeLabel} · 六维能力画像`}</h2><ScoreTile score={d.overallScore} info={d.profileScoreInfo} kind="profile" call={call} /></div>
          {!d.growthReady && <SbStatePanel state={d.growthLoading ? 'loading' : 'empty'} title={d.growthStatusText} description={d.growthLoading ? undefined : '接入 Agent 中台并完成首轮复盘后生成'} />}
          {d.growthReady && <>
            <p className="ds-pf-summary">{d.reviewSummary}</p>
            <p className="ds-muted ds-pf-meta">{d.growthStatusText} · 近 30 天 {d.visitCount} 次拜访 · {d.reviewDate}</p>
            <SbTable rowKey="code" density="compact" columns={dimCols} rows={d.dimensions || []} />
            <div className="ds-pf-advice">
              <div className="ds-pf-advice-head"><SbAiBadge state="pending" text="AI 生成 · 销售教练建议" /><span className="ds-muted">围绕六项能力给出提升方法，只读</span></div>
              <ol>{(d.aiAdvice || []).map((item, i) => <li key={i}>{item}</li>)}</ol>
            </div>
            {!d.profileNotApplicable && (d.role === 'sales' || d.profileScopeMode === 'person') && <>
              <div className="ds-pf-growth-head"><h3 className="ds-pf-h">个人成长曲线 <small>近 30 天 · {(d.history || []).length} 个每日快照</small></h3><SbSegmented value={d.selectedGrowthCode} options={(d.growthOptions || []).map(g => ({ value: g.code, label: g.name }))} onChange={v => call('selectGrowthDimension', { dataset: { code: v } })} /></div>
              <Sparkline history={d.history || []} code={d.selectedGrowthCode || 'overall'} />
            </>}
          </>}
        </>}
      </div>
    </div>
  </section>;
}

/* FDE 视角的「我的」：项目成效、协作效率、FDE 画像 */
function FdeProfile({ d, call, invokeOn, select, tab }) {
  const fp = tab !== 'efficiency' ? select('fde-profile') : null;
  const qt = fp ? select('#fdeQuarterTarget', 'fde-profile') : null;
  const team = d.fdeProfileScope === 'team';
  const scopeLabel = team ? (d.fdeTeamLabel || '团队') : d.fdeMemberName || '我';
  return <section className="ds-profile" aria-label="我的"><PresentationSwitch data={d} invoke={call}/>
    <div className="ds-panel ds-pf-head">
      <span className="ds-pf-avatar" aria-hidden="true">{d.initial}</span>
      <div className="ds-pf-id"><b>{d.userName}</b><span className="ds-muted">{d.roleName} · {d.team}</span></div>
      <SbStatusTag tone="good" label="已登录" />
      <Button className="ds-pf-logout" onClick={() => call('logout')} disabled={d.logoutBusy} loading={d.logoutNavigating}>退出登录 / 切换账号</Button>
    </div>
    <div className="ds-panel ds-pf-main">
      <SbTabs activeKey={tab} onChange={key => call('selectProfileTab', { dataset: { tab: key } })} items={(d.profileTabs || []).map(t => ({ key: t.key, label: t.label }))} />
      {d.isFdeLead && <div className="ds-pf-scope">
        <SbSegmented value={team ? 'team' : 'person'} options={(d.fdeScopeModes || []).map(o => ({ value: o.value, label: o.label }))} onChange={mode => call('changeFdeScopeMode', { detail: { mode } })} />
        {team && (d.fdeScopeTeams || []).length > 1 && <SbLabeledSelect label="团队" allowClear={false} value={d.fdeTeamId || undefined} options={(d.fdeScopeTeams || []).map(t => ({ value: t.id, label: t.name }))} onChange={id => call('changeFdeScopeSubject', { detail: { kind: 'team', id } })} width={220} />}
        {!team && <SbLabeledSelect label="成员" allowClear={false} value={d.fdeMemberId || d.ownUserId || undefined} options={(d.fdeScopeMembers || []).map(m => ({ value: m.id, label: m.teamLabel ? `${m.name}（${m.teamLabel}）` : m.name }))} onChange={id => call('changeFdeScopeSubject', { detail: { kind: 'person', id } })} width={260} />}
        <span className="ds-muted">当前：{team ? d.fdeTeamLabel : d.fdeMemberName || `${d.userName}（本人）`}</span>
        {d.fdeMembersError && <span><span className="ds-pf-error" role="alert">{d.fdeMembersError}</span> <Button type="link" size="small" onClick={() => call('loadFdeMembers')}>重试</Button></span>}
      </div>}
      <div className="ds-pf-body">
        {tab === 'efficiency' && <>
          <div className="ds-pf-section-head"><h2>{team ? '团队协作效率' : d.fdeMemberName ? `${d.fdeMemberName} · 协作效率` : '我的协作效率'}</h2><ScoreTile score={d.efficiencyScore} info={d.efficiencyScoreInfo} kind="efficiency" call={call} /></div>
          {d.organizationError && <p><span role="alert" className="ds-pf-error">{d.organizationError}</span> <Button type="link" size="small" onClick={() => call('retryEfficiency')}>重试</Button></p>}
          {d.organizationLoading && !d.organizationReady && <p className="ds-muted">正在统计协作效率…</p>}
          {d.organizationReady && <>
            <div className="ds-pf-switches">
              <SbSegmented size="middle" value={d.efficiencyMetric} options={(d.efficiencyMetrics || []).map(m => ({ value: m.key, label: m.label }))} onChange={v => call('selectEfficiencyMetric', { dataset: { metric: v } })} />
              <SbSegmented value={d.efficiencyPeriod} options={(d.efficiencyPeriods || []).map(p => ({ value: p.key, label: p.label }))} onChange={v => call('selectEfficiencyPeriod', { dataset: { period: v } })} />
            </div>
            <div className="ds-pf-facts"><SbMetricStrip columns={2} variant="card" items={[{ key: 'total', label: `${d.efficiencyMetric === 'opportunities' ? '参与商机' : d.efficiencyMetric === 'demo' ? 'Demo 数量' : '跟进次数'} · ${d.efficiencyAggregateLabel || ''}`, value: d.efficiencyTotal, note: d.efficiencyUnit }]} /></div>
            {!!(d.efficiencyRanking || []).length && <ol className="ds-pf-ranking">{d.efficiencyRanking.map(r => <li key={r.user_id} className={r.isSelected ? 'is-selected' : ''}><span className="ds-pf-rank-no">{r.rank}</span><span className="ds-pf-rank-main"><b>{r.name}</b><span className="ds-muted">{r.meta}</span><Progress percent={Number(r.barWidth) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /></span><b className="ds-pf-rank-value">{r.value} <small className="ds-muted">{d.efficiencyUnit}</small></b></li>)}</ol>}
            <p className="ds-muted ds-pf-window">{d.fdeEfficiencyNote}</p>
          </>}
        </>}
        {tab !== 'efficiency' && <FdeProfileSection fp={fp} qt={qt} invokeOn={invokeOn} section={tab} scopeLabel={scopeLabel} />}
      </div>
    </div>
  </section>;
}
