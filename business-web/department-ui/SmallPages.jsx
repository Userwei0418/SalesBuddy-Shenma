import React from 'react';
import { Button, Input, Progress } from 'antd';
import { SbAiBadge, SbBottomBar, SbField, SbLabeledSelect, SbSearch, SbSegmented, SbSheet, SbStatePanel, SbStatusTag, SbTable, SbTabs, SbTextarea } from '@shandiant/ui-react';
import { Sparkline } from './Profile.jsx';
import './small-pages.css';

// 第二批穿透页：都是原 Page 的数据与动作，这里只换展示层。
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const use = (page, invoke) => (name, payload = {}) => invoke(name, payload);

/* 客户建档确认 pages/customer-assign-confirm */
export function supportsAssignConfirm(page, data = {}) { return Boolean(page && page.route === 'pages/customer-assign-confirm/index' && Array.isArray(data.fields)); }
export function AssignConfirm({ page, data: d, invoke }) {
  const call = use(page, invoke);
  const notice = d.draftRestored ? '已恢复上次保存的客户建档草稿。' : d.missingCount ? `还有 ${d.missingCount} 个关键字段待补充，完成后才能正式建档下发。` : d.saved ? '当前修改已经保存为草稿。' : '';
  return <section className="ds-sp ds-sp-fill" aria-label="客户建档与下发">
    <div className="ds-panel ds-sp-card ds-sp-col">
      <div className="ds-sp-head">
        <div className="ds-sp-progress"><div className="ds-sp-progress-head"><b>{d.customerName}</b><span className="ds-muted">必填 {d.completedCount} / {d.requiredCount} · {d.roleName} · 创建人 {d.recorderName}</span></div><Progress percent={Number(d.progressPercent) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /></div>
      </div>
      <div className="ds-sp-ai"><SbAiBadge state="pending" text="AI 生成 · 待核对" /><span>核对客户名称、联系人角色和负责销售；确认后完成客户建档、负责人绑定和销售端提醒。</span></div>
      {notice && <p className="ds-sp-notice">{notice}</p>}
      <div className="ds-sp-fields">
        {(d.fields || []).map((f, i) => <button key={f.key} type="button" className={`ds-sp-field ${f.missing ? 'is-missing' : ''} ${f.edited ? 'is-edited' : ''} ${f.readonly ? 'is-readonly' : ''}`} onClick={() => call('openEditor', { dataset: { index: i } })}>
          <span className="ds-sp-field-label">{f.label}<small>{f.required ? '必填' : f.system ? '系统生成' : '选填'}{f.edited ? ' · 已修改' : ''}</small></span>
          <span className={`ds-sp-field-value ${f.value ? '' : 'is-empty'}`}>{f.value || '待补充'}</span>
          <span className="ds-sp-field-action">{f.readonly ? '自动匹配' : f.missing ? '补充' : '编辑'}</span>
        </button>)}
      </div>
      <div className="ds-sp-bar"><SbBottomBar reason="确认后写入正式客户数据并通知负责销售" secondary={{ label: '保存草稿', disabled: d.submitting, onClick: () => call('saveDraft') }} primary={{ label: '确认建档并下发', loading: d.submitting, disabled: Boolean(d.missingCount), disabledReason: d.missingCount ? `还有 ${d.missingCount} 个必填项` : '', onClick: () => call('confirmArchive') }} /></div>
    </div>
    <SbSheet open={Boolean(d.editorVisible)} title={d.editorTitle} onClose={() => call('closeEditor')} footer={<><Button onClick={() => call('closeEditor')}>取消</Button><Button type="primary" onClick={() => call('saveEditor')}>保存修改</Button></>}>
      {d.editorType === 'text' && <Input autoFocus value={d.editorValue || ''} placeholder={d.editorPlaceholder} onChange={e => call('inputEditor', { detail: { value: e.target.value } })} onPressEnter={() => call('saveEditor')} />}
      {d.editorType === 'textarea' && <SbTextarea autoFocus value={d.editorValue || ''} placeholder={d.editorPlaceholder} maxLength={500} autoSize={{ minRows: 5, maxRows: 10 }} onChange={v => call('inputEditor', { detail: { value: v } })} />}
      {d.editorType === 'select' && <div className="ds-cc-options" role="listbox">{(d.editorOptions || []).map((o, i) => <button key={o.label} type="button" role="option" aria-selected={Boolean(o.selected)} className={`ds-cc-option ${o.selected ? 'is-selected' : ''}`} onClick={() => call('selectOption', { dataset: { index: i } })}>{o.label}</button>)}</div>}
      <p className="ds-cc-helper">修改先更新当前草案，点「确认建档并下发」后才写入正式客户数据。</p>
    </SbSheet>
  </section>;
}

/* 客户认领 pages/customer-claim */
export function supportsCustomerClaim(page) { return Boolean(page && page.route === 'pages/customer-claim/index'); }
export function CustomerClaim({ page, data: d, invoke }) {
  const call = use(page, invoke);
  const cols = [
    { title: '', key: 'pick', width: 40, render: (_, r) => <span className={`ds-sp-radio ${d.selectedCustomerId === r.id ? 'is-on' : ''} ${r.claimed || r.claim_status === 'pending' ? 'is-off' : ''}`} aria-hidden="true" /> },
    { title: '客户名称', key: 'name', render: (_, r) => <div className="ds-sp-two"><b>{r.name}</b><span className="ds-muted">{r.industry || '行业待补充'} · {r.team || '当前部门'}</span></div> },
    { title: '等级', key: 'level', width: 90, render: (_, r) => r.level || <span className="ds-muted">未分级</span> },
    { title: '负责销售', key: 'owner', width: 140, render: (_, r) => r.owner || <span className="ds-muted">未分配</span> },
    { title: '认领状态', key: 'claim', width: 130, render: (_, r) => <SbStatusTag tone={r.claimed ? 'good' : r.claim_status === 'pending' ? 'watch' : 'pending'} label={r.claimLabel} /> },
  ];
  const state = d.loading && !(d.customers || []).length ? 'loading' : d.loadError && !(d.customers || []).length ? 'error' : !(d.customers || []).length ? 'empty' : 'normal';
  return <section className="ds-sp ds-sp-fill" aria-label="客户认领">
    <div className="ds-panel ds-sp-card ds-sp-col">
      <div className="ds-sp-tools">
        <div className="ds-sp-search"><SbSearch value={d.query || ''} placeholder="客户名称 / 拼音 / 首字母" loading={Boolean(d.loading)} onChange={value => call('inputQuery', { detail: { value } })} /></div>
        <div className="ds-sync-filters">
          <SbLabeledSelect label="行业" value={d.industryIndex ?? 0} options={(d.industryOptions || []).map((o,i)=>({value:i,label:o.label}))} disabled={d.optionsLoading || d.submitting || !d.industryOptions?.length} onChange={i=>call('changeIndustry',{detail:{value:i ?? 0}})} />
          <SbLabeledSelect label="认领状态" value={d.statusIndex ?? 0} options={(d.statusOptions || []).map((o,i)=>({value:i,label:o.label}))} disabled={d.optionsLoading || d.submitting || !d.statusOptions?.length} onChange={i=>call('changeClaimStatus',{detail:{value:i ?? 0}})} />
          {d.hasFilters && <Button onClick={()=>call('clearFilters')}>清除筛选</Button>}
          {d.optionsError && <span role="alert">{d.optionsError}<Button type="link" onClick={()=>call('retryFilterOptions')}>重试筛选选项</Button></span>}
        </div>
        <span className="ds-muted">{d.loading ? '加载中…' : d.total !== null && d.total !== undefined ? `已加载 ${(d.customers || []).length} / 共 ${d.total} 家` : '暂未加载'}</span>
        <Button onClick={() => call('refreshCustomers')}>刷新名单</Button>
      </div>
      {d.resultMessage && <p className="ds-sp-notice">{d.resultMessage}</p>}
      <div className="ds-sp-body">
        <SbTable rowKey="id" density="compact" columns={cols} rows={d.customers || []} state={state} emptyTitle={d.hasFilters ? '没有匹配的客户' : '公司客户名单暂为空'} emptyDescription={d.hasFilters ? '请调整搜索词或筛选条件' : '运营完成客户建档后，可在这里申请认领'} onRetry={() => call('refreshCustomers')} onRowClick={r => call('selectCustomer', { dataset: { id: r.id } })} />
        {(d.customers || []).length > 0 && (d.hasMore ? <div className="ds-sp-more">{d.loadMoreError && <span className="ds-sp-error">{d.loadMoreError}</span>}<Button size="small" loading={d.loadingMore} disabled={d.loadingMore} onClick={() => call('retryMore')}>{d.refreshRequired ? '刷新名单' : d.loadMoreError ? '重试加载' : '加载更多客户'}</Button></div> : !d.loading && <p className="ds-muted ds-sp-end">已显示全部匹配客户</p>)}
      </div>
      <div className="ds-sp-bar"><SbBottomBar reason="提交申请后由运营审批，通过后加入你的作战地图" primary={{ label: '提交认领申请', loading: d.submitting, disabled: !d.selectedCustomerId, disabledReason: d.selectedCustomerId ? '' : '先选一个可申请的客户', onClick: () => call('confirmClaim') }} /></div>
    </div>
  </section>;
}

/* 风险中心 pages/risks */
export function supportsRisks(page) { return Boolean(page && page.route === 'pages/risks/index'); }
export function Risks({ page, data: d, invoke }) {
  const call = use(page, invoke);
  const cols = [
    { title: '风险', key: 'title', render: (_, r) => <div className="ds-sp-two"><b>{r.title}</b><span className="ds-muted">{r.customerName}{r.team ? ` · ${r.team}` : ''}</span></div> },
    { title: '状态', key: 'status', width: 150, render: (_, r) => <SbStatusTag tone={toneOf(r.signal?.tone)} label={`${r.signal?.label || ''} · ${r.statusLabel}`} reason={r.description} /> },
    { title: '等级', key: 'severity', width: 90, render: (_, r) => `${r.severity}风险` },
    { title: '负责人', key: 'owner', width: 110, render: (_, r) => r.owner },
    { title: '时间', key: 'time', width: 150, render: (_, r) => <span className="ds-muted">{r.status === 'resolved' ? `解除 ${r.resolvedLabel}` : `识别 ${r.openedLabel}`}</span> },
    { title: '关联商机', key: 'opp', width: 160, render: (_, r) => r.opportunityId ? <Button type="link" size="small" onClick={e => { e.stopPropagation(); call('openOpportunity', { dataset: { customerId: r.customerId, opportunityId: r.opportunityId } }); }}>{r.opportunityName || '查看商机'}</Button> : <span className="ds-muted">无</span> },
  ];
  return <section className="ds-sp ds-sp-fill" aria-label={d.opportunityOnly ? '商机风险' : '风险中心'}>
    <div className="ds-panel ds-sp-card ds-sp-col">
      <SbTabs activeKey={d.activeTab} onChange={key => call('selectTab', { dataset: { key } })} items={(d.tabs || []).map(t => ({ key: t.key, label: t.label, count: t.key === 'open' ? d.openCount : t.key === 'resolved' ? d.resolvedCount : (d.risks || []).length }))} />
      <div className="ds-sp-body ds-sp-body-pad">
        <SbTable rowKey="id" density="compact" columns={cols} rows={d.filteredRisks || []} state={d.loading ? 'loading' : (d.filteredRisks || []).length ? 'normal' : 'empty'} emptyTitle={d.activeTab === 'open' ? '当前没有待解除风险' : '暂无对应风险记录'} emptyDescription="新识别的风险会自动进入这里" onRowClick={r => call('openRisk', { dataset: { id: r.id } })} />
      </div>
    </div>
  </section>;
}

/* 风险详情 pages/risk-detail */
export function supportsRiskDetail(page, data = {}) { return Boolean(page && page.route === 'pages/risk-detail/index' && data.risk); }
export function RiskDetail({ page, data: d, invoke }) {
  const call = use(page, invoke); const r = d.risk;
  const resolved = r.status === 'resolved';
  return <section className="ds-sp ds-sp-two-col" aria-label="风险详情">
    <aside className="ds-panel ds-sp-side">
      <div className="ds-sp-tags"><SbStatusTag tone={toneOf(r.signal?.tone)} label={`${r.signal?.label || ''} · ${r.statusLabel}`} /><span className="ds-sp-chip">{r.severity}风险</span></div>
      <p className="ds-muted ds-sp-eyebrow">{r.customerName}</p>
      <h1>{r.title}</h1>
      <p className="ds-muted">{[r.owner, r.team].filter(Boolean).join(' · ')}</p>
      <dl className="ds-sp-info"><div><dt>识别时间</dt><dd>{r.openedLabel}</dd></div>{resolved && <div><dt>解除时间</dt><dd>{r.resolvedLabel}</dd></div>}</dl>
    </aside>
    <section className="ds-panel ds-sp-main">
      <section className="ds-sp-block"><h2>风险说明</h2><p className="ds-sp-pre">{r.description}</p></section>
      <section className="ds-sp-block"><h2>判断依据</h2><ol className="ds-sp-evidence">{(r.evidence || []).map((e, i) => <li key={i}>{e}</li>)}</ol></section>
      <section className="ds-sp-block ds-sp-next"><b>建议下一步</b><p>{r.nextAction}</p></section>
      {resolved ? <section className="ds-sp-block ds-sp-block-good"><h2>{r.resolvedBy} 已解除风险 <small className="ds-muted">{r.resolvedLabel}</small></h2><p className="ds-sp-pre">{r.resolutionNote}</p></section>
        : <section className="ds-sp-block"><h2>解除风险</h2><p className="ds-muted ds-sp-help">请填写已消除风险的事实依据，便于管理者和后续跟进人员追溯。</p><SbField label="解除依据" required help="至少 5 个字"><SbTextarea value={d.resolutionNote || ''} maxLength={500} autoSize={{ minRows: 4, maxRows: 8 }} placeholder="例如：客户已确认下一步方案和时间，原风险事项已消除" onChange={v => call('inputResolutionNote', { detail: { value: v } })} /></SbField></section>}
      {!resolved && d.canResolveRisk && <div className="ds-sp-bar"><SbBottomBar reason="解除记录会保留处理人、时间和依据" primary={{ label: '确认解除风险', loading: d.submitting, loadingLabel: '正在同步…', disabled: (d.noteCount || 0) < 5, disabledReason: (d.noteCount || 0) < 5 ? '依据至少 5 个字' : '', onClick: () => call('confirmResolve') }} /></div>}
    </section>
  </section>;
}

/* 报告详情 pages/report-detail */
export function supportsReportDetail(page) { return Boolean(page && page.route === 'pages/report-detail/index'); }
export function ReportDetail({ page, data: d, invoke }) {
  const call = use(page, invoke); const r = d.detail;
  if (d.loading && !r) return <SbStatePanel state="loading" title="正在读取报告…" />;
  if (d.error && !r) { const missing = /缺少报告标识/.test(d.error); return <SbStatePanel state={missing ? 'empty' : 'error'} title={missing ? '缺少报告标识' : d.error} description={missing ? '报告条目只能从总览的即时总结进入' : '网络或服务暂时不可用'} onRetry={missing ? undefined : () => call('loadReport')} />; }
  if (!r) return <SbStatePanel state="empty" title="缺少报告标识" description="请从总览重新打开" />;
  return <section className="ds-sp" aria-label="即时总结">
    <div className="ds-panel ds-sp-card ds-sp-report">
      <div className="ds-sp-report-head"><SbAiBadge state="pending" text="AI 生成 · 即时总结" /><span className="ds-muted">{r.scope} · {r.period} · 生成于 {r.generatedAt}</span></div>
      <h1>{r.reportTitle}</h1>
      <p className="ds-muted ds-sp-eyebrow">{r.sectionTitle}{r.sectionSubtitle ? ` · ${r.sectionSubtitle}` : ''}</p>
      <h2>{r.title}</h2>
      <p className="ds-sp-pre">{r.detail}</p>
      {r.isAttention && <div className="ds-sp-next"><b>推荐行动 · {r.recommendationTitle || '针对该客户的下一步建议'}</b><p>{r.recommendationDetail || '当前尚未形成可执行建议，请重新生成即时总结。'}</p></div>}
      <p className="ds-muted ds-sp-help">AI 基于权限范围内的业务数据分析，结论供参考。</p>
    </div>
  </section>;
}

/* 拜访详情 pages/visit-detail */
export function supportsVisitDetail(page, data = {}) { return Boolean(page && page.route === 'pages/visit-detail/index' && data.visit && !data.loading); }
export function VisitDetail({ page, data: d, invoke }) {
  const call = use(page, invoke); const v = d.visit; const a = d.visitAdvice || {};
  const facts = [['客户类型', v.customerType], ['客户名称', v.customerName], ['商机名称', v.opportunityName], ['伙伴名称', v.partnerName], ['跟进日期', v.date], ['创建时间', v.createdAt], ['对接人', v.contactNames], ['跟进人', v.owner], ...(v.originalRecorderName ? [['原记录人', v.originalRecorderName]] : []), ...(v.managerName ? [['负责人', v.managerName]] : []), ['协同人', v.collaborators], ['是否七天内', v.sevenDaysText], ['拜访方式', v.mode], ['拜访时长', v.duration], ['拜访地点', v.location], ['是否首次拜访', v.firstVisitText]];
  return <section className="ds-sp ds-sp-two-col" aria-label="拜访记录">
    <aside className="ds-panel ds-sp-side">
      <div className="ds-sp-tags"><SbStatusTag tone={toneOf(v.signal?.tone)} label={v.signal?.badgeText || v.signal?.label} reason={v.signal?.reason} /><span className="ds-sp-chip">只读</span></div>
      <h1>{v.title}</h1>
      <p className="ds-muted">{[v.date, v.mode, v.firstVisitText === '是' ? '首次拜访' : '客户跟进'].filter(Boolean).join(' · ')}</p>
      {v.fdeParticipantNames && <p className="ds-sp-notice">本次实际协助 FDE：{v.fdeParticipantNames}{v.isParticipant ? ' · 本人参与' : ''}</p>}
      <dl className="ds-sp-info">{facts.map(([k, val]) => <div key={k}><dt>{k}</dt><dd>{val}</dd></div>)}</dl>
      {(v.linkedOpportunities || []).map(link => <Button key={link.id} onClick={() => call('openOpportunity',{dataset:{id:link.id}})}>{link.name || '查看关联商机'}</Button>)}
    </aside>
    <section className="ds-panel ds-sp-main">
      {v.isFirstVisit && <section className="ds-sp-block"><h2>首次拜访信息</h2><dl className="ds-sp-rows">{[['客户主营业务', v.customerMainBusiness], ['客户需求', v.customerNeeds], ['客户预算', v.customerBudget], ['联系人角色', v.contactRole]].map(([k, val]) => <div key={k}><dt>{k}</dt><dd>{val}</dd></div>)}</dl></section>}
      <section className="ds-sp-block"><h2>拜访内容</h2><dl className="ds-sp-rows">{[['拜访目标', v.visitGoal], ['沟通内容', v.followUpRecord], ['达成结果', v.expectation]].map(([k, val]) => <div key={k}><dt>{k}</dt><dd className="ds-sp-pre">{val}</dd></div>)}</dl><div className="ds-sp-next"><b>下一步计划</b><p>{v.nextAction}</p></div></section>
      <section className="ds-sp-block">
        <div className="ds-sp-block-head"><SbAiBadge state={a.status === 'loading' ? 'generating' : 'pending'} text={a.status === 'loading' ? 'AI 生成中' : 'AI 生成 · 本次拜访建议'} />{a.status !== 'loading' && <Button type="link" size="small" onClick={() => call('loadVisitAdvice')}>{a.status === 'ready' ? '检查建议更新' : '生成建议'}</Button>}</div>
        {a.status === 'loading' && <p className="ds-muted">正在分析这次拜访…</p>}
        {a.status === 'error' && <p className="ds-sp-error">{a.error}</p>}
        {a.status === 'ready' && <><p className="ds-sp-pre">{a.summary}</p><ol className="ds-sp-evidence">{(a.rows || []).map((row, i) => <li key={row.id || i}><b>{row.title}</b><span className="ds-muted">{row.detail}</span></li>)}</ol></>}
        <p className="ds-muted ds-sp-help">已归档正文只读；建议由你确认后形成待办。</p>
      </section>
    </section>
  </section>;
}

/* 维护客户信息 pages/customer-edit */
export function supportsCustomerEdit(page, data = {}) { return Boolean(page && page.route === 'pages/customer-edit/index' && !data.loading); }
export function CustomerEdit({ page, data: d, invoke }) {
  const call = use(page, invoke); const f = d.form || {}; const a = d.agentView || {};
  // 客户类型在接口里是代码，选项是中文名；显示时按名字找下标，找不到才用页面的下标
  const TYPE_NAMES = { prospect: '潜在客户', opportunity: '商机客户', won: '已成单客户' };
  const typeIndex = (() => { const i = (d.customerTypeOptions || []).indexOf(TYPE_NAMES[f.customer_type] || f.customer_type); return i >= 0 ? i : d.customerTypeIndex; })();
  const sel = (label, options, index, handler, width = 260) => <SbLabeledSelect label={label} allowClear={false} value={index} options={(options || []).map((o, i) => ({ value: i, label: o }))} onChange={i => call(handler, { detail: { value: i } })} width={width} />;
  const input = (key, placeholder) => <Input value={f[key] || ''} placeholder={placeholder} onChange={e => call('inputField', { dataset: { key }, detail: { value: e.target.value } })} />;
  return <section className="ds-sp ds-sp-two-col" aria-label="维护客户信息">
    <aside className="ds-panel ds-sp-side">
      <div className="ds-sp-tags"><SbAiBadge state="confirmed" text="Agent 计算 · 只读" /></div>
      <h1>{f.name}</h1>
      <p className="ds-muted">保存后 Agent 会重新评估客户画像与作战地图位置</p>
      <dl className="ds-sp-info"><div><dt>作战象限</dt><dd>{a.quadrant}</dd></div><div><dt>客户潜力</dt><dd>{a.potential}</dd></div><div><dt>关系深度</dt><dd>{a.relationship}</dd></div><div><dt>当前风险</dt><dd>{a.risk}</dd></div></dl>
    </aside>
    <section className="ds-panel ds-sp-main">
      <section className="ds-sp-block"><h2>人工维护字段 <small className="ds-muted">修改后成为 Agent 的新分析依据</small></h2>
        <div className="ds-sp-grid">
          <SbField label="客户名称" help="不可修改"><div className="ds-sp-readonly">{f.name}</div></SbField>
          <SbField label="所属行业" required>{sel('行业', d.industryOptions, d.industryIndex, 'changeIndustry')}</SbField>
          <SbField label="客户类型" required>{sel('类型', d.customerTypeOptions, typeIndex, 'changeCustomerType')}</SbField>
          <SbField label="客户优先级" required>{sel('优先级', d.customerLevelOptions, d.customerLevelIndex, 'changeCustomerLevel')}</SbField>
          <SbField label="客户 / 线索来源" required>{sel('来源', d.sourceOptions, d.sourceIndex, 'changeSource')}</SbField>
          <SbField label="合作伙伴" required>{input('partner_name', '合作伙伴名称')}</SbField>
        </div>
      </section>
      <section className="ds-sp-block"><h2>首要联系人 <small className="ds-muted">角色仅限决策者、影响者、使用者</small></h2>
        <div className="ds-sp-grid">
          <SbField label="联系人姓名">{input('contact_name', '姓名')}</SbField>
          <SbField label="联系人职位">{input('contact_title', '职位')}</SbField>
          <SbField label="联系人角色">{sel('角色', d.contactRoleOptions, d.contactRoleIndex, 'changeContactRole')}</SbField>
        </div>
      </section>
      <div className="ds-sp-bar"><SbBottomBar reason="保存后触发 Agent 重评" primary={{ label: '保存修改', loading: d.saving, onClick: () => call('submit') }} /></div>
    </section>
  </section>;
}

/* 成员成长 pages/member-growth（非 FDE） */
export function supportsMemberGrowth(page, data = {}) { return Boolean(page && page.route === 'pages/member-growth/index' && !data.isFde); }
export function MemberGrowth({ page, data: d, invoke }) {
  const call = use(page, invoke); const s = d.subject || {};
  const cols = [
    { title: '能力', key: 'name', width: 140, render: (_, r) => <b>{r.name}</b> },
    { title: '得分', key: 'score', width: 220, render: (_, r) => <div className="ds-sp-dim"><Progress percent={r.score === '--' ? 0 : Number(r.score)} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" /><b>{r.score}</b></div> },
    { title: '提升方法', key: 'coach', render: (_, r) => r.coachingAction },
  ];
  return <section className="ds-sp" aria-label="成员成长">
    <div className="ds-panel ds-sp-card ds-sp-person"><span className="ds-sp-avatar" aria-hidden="true">{s.initial}</span><div className="ds-sp-two"><b>{s.name}</b><span className="ds-muted">{d.subjectRoleName} · {s.team}</span></div><span className="ds-sp-chip">每日复盘</span><div className="ds-sp-score"><b>{d.overallScore}</b><span className="ds-muted">综合得分</span></div></div>
    <div className="ds-panel ds-sp-card ds-sp-block-col">
      {d.loading ? <SbStatePanel state="loading" title={d.statusText} /> : !d.ready ? <SbStatePanel state="empty" title="暂无能力画像" description={d.statusText} /> : <>
        <h2 className="ds-sp-h">六维能力画像 <small className="ds-muted">基于近 30 天拜访记录 · {d.visitCount} 次拜访 · {d.reviewDate}</small></h2>
        <p className="ds-sp-summary">{d.reviewSummary}</p>
        <SbTable rowKey="code" density="compact" columns={cols} rows={d.dimensions || []} />
        <div className="ds-sp-block ds-sp-advice"><div className="ds-sp-block-head"><SbAiBadge state="pending" text="AI 生成 · 销售教练建议" /><span className="ds-muted">围绕六项能力给出提升方法，只读</span></div><ol className="ds-sp-evidence">{(d.aiAdvice || []).map((t, i) => <li key={i}>{t}</li>)}</ol></div>
        <div className="ds-sp-growth-head"><h2 className="ds-sp-h">成长趋势 <small className="ds-muted">{(d.history || []).length} 个快照</small></h2><SbSegmented value={d.selectedGrowthCode} options={(d.growthOptions || []).map(g => ({ value: g.code, label: g.name }))} onChange={v => call('selectGrowthDimension', { dataset: { code: v } })} /></div>
        <Sparkline history={d.history || []} code={d.selectedGrowthCode || 'overall'} />
      </>}
    </div>
  </section>;
}

/* 商机看板 pages/opportunities（非 FDE） */
export function supportsOpportunityBoard(page, data = {}) { return Boolean(page && page.route === 'pages/opportunities/index' && !data.isFde); }
export function OpportunityBoard({ page, data: d, invoke }) {
  const call = use(page, invoke);
  const changeStages = next => { const prev = d.selectedStages || []; for (const v of new Set([...(next || []), ...prev])) if ((next || []).includes(v) !== prev.includes(v)) call('toggleStage', { dataset: { value: v } }); };
  const cols = [
    { title: '商机 / 客户', key: 'name', width: '32%', render: (_, r) => <div className="ds-sp-two"><b>{r.name}</b><span className="ds-muted">{r.customer_name}</span></div> },
    { title: '阶段', key: 'stage', width: 170, render: (_, r) => <span>{r.stageName}<span className="ds-muted"> · {r.probabilityText}</span></span> },
    { title: '状态', key: 'signal', width: 120, render: (_, r) => <SbStatusTag tone={toneOf(r.signal?.tone)} label={r.signal?.label} reason={r.signal?.detail} /> },
    { title: '确收 / 回款', key: 'money', width: 160, align: 'right', render: (_, r) => <span className="ds-sp-nums">{r.recognizedLabel}<span className="ds-muted"> / </span>{r.collectionLabel}</span> },
    { title: '预计关单', key: 'close', width: 120, render: (_, r) => r.closeLabel },
    { title: '产品线', key: 'line', width: 120, render: (_, r) => r.productLineLabel },
    { title: '负责人', key: 'owner', width: 130, render: (_, r) => <div className="ds-sp-two"><span>{r.owner}</span><span className="ds-muted">{r.team}</span></div> },
  ];
  const groups = [...(d.opportunityGroups || [])].reverse();
  const listState = d.loading ? 'loading' : d.loadError ? 'error' : !(d.filtered || []).length ? 'empty' : 'normal';
  return <section className="ds-sp ds-sp-fill" aria-label="全部商机">
    <div className="ds-panel ds-sp-card ds-sp-col">
      <div className="ds-sp-tools">
        {d.canViewTeam && <SbLabeledSelect label="团队" value={d.teamIndex > 0 ? d.teamIndex : undefined} options={(d.teamOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeTeam', { detail: { value: i ?? 0 } })} />}
        {d.canViewTeam && <SbLabeledSelect label={d.role === 'supervisor' ? '直属成员' : '人员'} value={d.ownerIndex > 0 ? d.ownerIndex : undefined} options={(d.ownerOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeFilter', { dataset: { key: 'owner' }, detail: { value: i ?? 0 } })} />}
        <SbLabeledSelect label="阶段" mode="multiple" value={d.selectedStages || []} options={(d.stageOptions || []).map(o => ({ value: o.value, label: o.label }))} onChange={changeStages} />
        <SbLabeledSelect label="关单日期" value={d.closeIndex > 0 ? d.closeIndex : undefined} options={(d.closeOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeFilter', { dataset: { key: 'close' }, detail: { value: i ?? 0 } })} />
        <SbLabeledSelect label="等级" value={d.gradeIndex > 0 ? d.gradeIndex : undefined} options={(d.gradeOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeFilter', { dataset: { key: 'grade' }, detail: { value: i ?? 0 } })} />
        {d.filterActive && <Button type="link" size="small" onClick={() => call('resetFilters')}>清除筛选</Button>}
        <span className="ds-muted ds-sp-count">共 {d.total ?? '—'} 条</span>
        {d.canCreate && <Button type="primary" className="ds-sp-right" onClick={() => call('createOpportunity')}>新增商机</Button>}
      </div>
      <div className="ds-sp-body ds-sp-body-pad">
        {listState !== 'normal' ? <SbTable columns={cols} rows={[]} state={listState} emptyTitle={listState === 'error' ? '加载失败' : '当前筛选条件下没有商机'} emptyDescription={listState === 'error' ? d.loadError : undefined} onRetry={() => call('loadPage')} onClear={d.filterActive ? () => call('resetFilters') : undefined} />
          : groups.map((g, gi) => <div key={g.key} className="ds-sp-group"><h3>{g.label} <small>{g.items.length} 条</small></h3><SbTable rowKey="id" density="compact" columns={cols} rows={g.items} showHeader={gi === 0} scrollX={900} onRowClick={r => call('openCustomer', { dataset: { customerId: r.customer_id, opportunityId: r.id } })} /></div>)}
        {listState === 'normal' && (d.hasMore || d.loadingMore || d.moreError) && <div className="ds-sp-more"><span className="ds-muted">已显示 {(d.filtered || []).length} / {d.total} 个商机</span>{d.moreError && <span className="ds-sp-error">{d.moreError}</span>}<Button size="small" loading={d.loadingMore} onClick={() => call('loadMore')}>{d.moreError ? '重试' : '加载更多'}</Button></div>}
      </div>
    </div>
  </section>;
}
