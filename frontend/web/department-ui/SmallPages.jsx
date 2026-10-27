import {WorkflowDialog} from './WorkflowOverlays.jsx';
import {Input, SbSearch, SbTextarea} from './ImeInput.jsx';
import React from 'react';
import CustomerIdentityFields, {BusinessIdentityTags} from './CustomerIdentityFields.jsx';
import {Alert, Button, Progress} from 'antd';
import {SbAiBadge, SbBottomBar, SbField, SbLabeledSelect, SbSegmented,  SbStatePanel, SbStatusTag, SbTable, SbTabs} from '@shandiant/ui-react';
import {LabeledSearchSelect} from './pc/ui.jsx';
import { Sparkline } from './Profile.jsx';
import './small-pages.css';
import {AdviceActions} from './WorkflowCompletion.jsx';
import {adviceEntries} from './advice-bridge.js';
import FilterBar from './pc/FilterBar.jsx';
import {can, open} from './pc/data.js';
import {opportunityGroupsInServerOrder} from './Opportunities.jsx';
import VisitAudio from './pc/VisitAudio.jsx';

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
      {d.creationOptionsError && <Alert type="error" title={d.creationOptionsError} showIcon />}
      <CustomerIdentityFields data={d} invoke={invoke} disabled={Boolean(d.submitting)} />
      <div className="ds-sp-fields">
        {(d.fields || []).map((f, i) => f.hidden ? null : <button key={f.key} type="button" className={`ds-sp-field ${f.missing || f.validationError ? 'is-missing' : ''} ${f.edited ? 'is-edited' : ''} ${f.readonly ? 'is-readonly' : ''}`} onClick={() => call('openEditor', { dataset: { index: i } })}>
          <span className="ds-sp-field-label">{f.label}<small>{f.required ? '必填' : f.system ? '系统生成' : '选填'}{f.edited ? ' · 已修改' : ''}</small></span>
          <span className={`ds-sp-field-value ${f.value ? '' : 'is-empty'}`}>{f.value || '待补充'}{f.validationError && <small role="alert" className="ds-sp-error">{f.validationError}</small>}</span>
          <span className="ds-sp-field-action">{f.readonly ? '自动匹配' : f.missing ? '补充' : '编辑'}</span>
        </button>)}
      </div>
      <div className="ds-sp-bar"><SbBottomBar reason="确认后写入正式客户数据并通知负责销售" secondary={{ label: '保存草稿', disabled: d.submitting, onClick: () => call('saveDraft') }} primary={{ label: '确认建档并下发', loading: d.submitting, disabled: Boolean(d.missingCount || d.invalidFieldCount) || !d.creationOptionsReady || !d.identityOptionsReady, disabledReason: d.creationOptionsError || (d.invalidFieldCount ? '请修改标出的选项' : d.missingCount ? `还有 ${d.missingCount} 个必填项` : ''), onClick: () => call('confirmArchive') }} /></div>
    </div>
    <WorkflowDialog width={640} open={Boolean(d.editorVisible)} title={d.editorTitle} onClose={() => call('closeEditor')} footer={<><Button onClick={() => call('closeEditor')}>取消</Button><Button type="primary" onClick={() => call('saveEditor')}>保存修改</Button></>}>
      {d.editorType === 'text' && <Input autoFocus value={d.editorValue || ''} placeholder={d.editorPlaceholder} onChange={e => call('inputEditor', { detail: { value: e.target.value } })} onPressEnter={() => call('saveEditor')} />}
      {d.editorType === 'textarea' && <SbTextarea autoFocus value={d.editorValue || ''} placeholder={d.editorPlaceholder} maxLength={500} autoSize={{ minRows: 5, maxRows: 10 }} onChange={v => call('inputEditor', { detail: { value: v } })} />}
      {d.editorType === 'select' && <div className="ds-cc-options" role="listbox">{(d.editorOptions || []).map((o, i) => <button key={o.label} type="button" role="option" aria-selected={Boolean(o.selected)} className={`ds-cc-option ${o.selected ? 'is-selected' : ''}`} onClick={() => call('selectOption', { dataset: { index: i } })}>{o.label}</button>)}</div>}
      <p className="ds-cc-helper">修改先更新当前草案，点「确认建档并下发」后才写入正式客户数据。</p>
    </WorkflowDialog>
  </section>;
}

/* 客户认领 pages/customer-claim */
export function supportsCustomerClaim(page) { return Boolean(page && page.route === 'pages/customer-claim/index'); }
export function CustomerClaim({ page, data: d, invoke }) {
  const call = use(page, invoke);
  const busy = Boolean(d.submitting);
  const pick = row => { if (!busy && row.claimEligible) call('selectCustomer', {dataset:{id:row.id}}); };
  const cols = [
    { title: '选择', key: 'pick', width: 60, render: (_, r) => <input type="radio" name="claim-customer" className="ds-claim-radio" aria-label={`选择 ${r.name}`} checked={d.selectedCustomerId === r.id} disabled={busy || !r.claimEligible} onClick={e => e.stopPropagation()} onChange={() => pick(r)}/> },
    { title: '客户名称', key: 'name', render: (_, r) => <div className="ds-sp-two"><b>{r.name}</b><BusinessIdentityTags values={r.businessIdentities || r.business_identities} /><span className="ds-muted">{r.industry || '行业待补充'} · {r.team || '部门待分配'}</span></div> },
    { title: '等级', key: 'level', width: 90, render: (_, r) => r.level || <span className="ds-muted">未分级</span> },
    { title: '负责销售', key: 'owner', width: 140, render: (_, r) => r.owner || <span className="ds-muted">未分配</span> },
    { title: '认领状态', key: 'claim', width: 130, render: (_, r) => <SbStatusTag tone={r.claimed ? 'good' : r.claim_status === 'pending' ? 'watch' : 'pending'} label={r.claimLabel} /> },
  ];
  const state = d.loading && !(d.customers || []).length ? 'loading' : d.loadError && !(d.customers || []).length ? 'error' : !(d.customers || []).length ? 'empty' : 'normal';
  const emptyDescription = d.claimStatus === 'unclaimed'
    ? '未认领范围内没有匹配客户，可切换“全部认领状态”查看其他客户'
    : d.hasFilters ? '请调整搜索词或筛选条件' : '运营完成客户建档后，可在这里申请认领';
  return <section className="ds-sp ds-sp-fill" aria-label="客户认领">
    <div className="ds-panel ds-sp-card ds-sp-col">
      <div className="ds-sp-tools"><span className="ds-muted">{d.loading ? '加载中…' : d.total !== null && d.total !== undefined ? `已加载 ${(d.customers || []).length} / 共 ${d.total} 家` : '暂未加载'}</span>
        {can('customer.read') && <Button disabled={busy} onClick={() => open.route('pages/customer-history/index')}>申请与认领记录</Button>}
        {(can('customer.create_request') || can('customer.create')) && <Button disabled={busy} onClick={() => open.route('pages/customer-create/index')}>未找到客户？申请建档</Button>}
      </div>
      <FilterBar className="ds-claim-toolbar" label="客户认领筛选" search={<SbSearch value={d.query || ''} disabled={busy} placeholder="客户名称 / 拼音 / 首字母" loading={Boolean(d.loading)} onChange={value => call('inputQuery', { detail: { value } })} />} actions={<>
        <Button disabled={busy} onClick={()=>call('clearFilters')}>重置为未认领</Button>
        <Button disabled={busy || d.loading} onClick={() => call('refreshCustomers')}>刷新名单</Button>
      </>}>
        <SbLabeledSelect label="业务身份" disabled={busy} value={d.identityIndex} options={(d.identityOptions || []).map((o,i)=>({value:i,label:o.label}))} onChange={value=>call('changeBusinessIdentity',{detail:{value:value ?? 0}})} />
        <SbLabeledSelect label="行业" disabled={d.optionsLoading || d.submitting} value={d.industryIndex} options={(d.industryOptions || []).map((o,i)=>({value:i,label:o.label}))} onChange={value=>call('changeIndustry',{detail:{value:value ?? 0}})} />
        <SbLabeledSelect label="认领状态" data-toolbar-width={224} disabled={d.optionsLoading || d.submitting} value={d.statusIndex} options={(d.statusOptions || []).map((o,i)=>({value:i,label:o.label}))} onChange={value=>call('changeClaimStatus',{detail:{value:value ?? 0}})} />
      </FilterBar>
      {d.optionsError && <p role="alert" className="ds-sp-error">{d.optionsError} <Button type="link" disabled={busy} onClick={()=>call('retryFilterOptions')}>重试</Button></p>}
      {d.hasFilters && <div className="ds-claim-filters" aria-label="已选条件">{d.query && <span className="ds-claim-filter">搜索：{d.query}</span>}{[['身份','identityIndex','identityOptions'],['行业','industryIndex','industryOptions'],['状态','statusIndex','statusOptions']].filter(([,index]) => d[index] > 0).map(([label,index,options]) => <span key={label} className="ds-claim-filter">{label}：{d[options]?.[d[index]]?.label}</span>)}</div>}
      {d.resultMessage && <p className="ds-sp-notice">{d.resultMessage}</p>}
      {d.loadError && (d.customers || []).length > 0 && <p role="alert" className="ds-sp-error">{d.loadError} <Button type="link" onClick={() => call('refreshCustomers')}>重试</Button></p>}
      <div className="ds-sp-body">
        <SbTable rowKey="id" density="compact" columns={cols} rows={d.customers || []} state={state} scrollX={760} emptyTitle={d.loadError ? '客户名单加载失败' : d.hasFilters ? '没有匹配的客户' : '公司客户名单暂为空'} emptyDescription={d.loadError || emptyDescription} onRetry={() => call('refreshCustomers')} onRowClick={pick} actions={row => row.can_read_customer === true ? <Button type="link" onClick={() => open.customer(row.id)}>查看档案</Button> : <span className="ds-muted">仅目录可见</span>} actionsWidth={110} />
        {(d.customers || []).length > 0 && (d.hasMore ? <div className="ds-sp-more">{d.loadMoreError && <span className="ds-sp-error">{d.loadMoreError}</span>}<Button size="small" loading={d.loadingMore} disabled={d.loadingMore} onClick={() => call('retryMore')}>{d.refreshRequired ? '刷新名单' : d.loadMoreError ? '重试加载' : '加载更多客户'}</Button></div> : !d.loading && <p className="ds-muted ds-sp-end">已显示全部匹配客户</p>)}
      </div>
      <div className="ds-sp-bar"><SbBottomBar reason="提交申请后由运营审批，通过后加入你的客户经营范围" primary={{ label: '提交认领申请', loading: d.submitting, disabled: busy || !d.selectedCustomerId, disabledReason: d.selectedCustomerId ? '' : '先选一个可申请的客户', onClick: () => call('confirmClaim') }} /></div>
    </div>
  </section>;
}

// 风险读写沿用共享 Page；独立展示组件统一状态、建议确认和审计。
export {supportsRisks, Risks, supportsRiskDetail, RiskDetail} from './RiskReview.jsx';

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
VisitDetail.subcomponents = (_, data) => adviceEntries(data.visitAdvice, data.visitAdvice?.status === 'ready');
export function VisitDetail({ page, data: d, invoke, invokeOn }) {
  const call = use(page, invoke); const v = d.visit; const a = d.visitAdvice || {};
  const facts = [['客户类型', v.customerType], ['客户名称', v.customerName], ['商机名称', v.opportunityName], ['伙伴名称', v.partnerName], ['跟进日期', v.date], ['创建时间', v.createdAt], ['对接人', v.contactNames], ['跟进人', v.owner], ['协同人', v.collaborators], ['是否七天内', v.sevenDaysText], ['拜访方式', v.mode], ['拜访时长', v.duration], ['拜访地点', v.location], ['是否首次拜访', v.firstVisitText]];
  return <section className="ds-sp ds-sp-two-col" aria-label="拜访记录">
    <aside className="ds-panel ds-sp-side">
      <div className="ds-sp-tags"><SbStatusTag tone={toneOf(v.signal?.tone)} label={v.signal?.badgeText || v.signal?.label} reason={v.signal?.reason} /><span className="ds-sp-chip">只读</span></div>
      <h1>{v.title}</h1>
      <p className="ds-muted">{[v.date, v.mode, v.firstVisitText === '是' ? '首次拜访' : '客户跟进'].filter(Boolean).join(' · ')}</p>
      {v.fdeParticipantNames && <p className="ds-sp-notice">本次实际协助 FDE：{v.fdeParticipantNames}{v.isParticipant ? ' · 本人参与' : ''}</p>}
      <dl className="ds-sp-info">{facts.map(([k, val]) => <div key={k}><dt>{k}</dt><dd>{val}</dd></div>)}</dl>
      {v.opportunityId && <Button onClick={() => call('openOpportunity')}>查看商机详情</Button>}
    </aside>
    <section className="ds-panel ds-sp-main">
      <VisitAudio visit={d.visitAudio ? {id:d.visitId,audio:d.visitAudio} : null}/>
      {v.isFirstVisit && <section className="ds-sp-block"><h2>首次拜访信息</h2><dl className="ds-sp-rows">{[['客户主营业务', v.customerMainBusiness], ['客户需求', v.customerNeeds], ['客户预算', v.customerBudget], ['联系人角色', v.contactRole]].map(([k, val]) => <div key={k}><dt>{k}</dt><dd>{val}</dd></div>)}</dl></section>}
      <section className="ds-sp-block"><h2>拜访内容</h2><dl className="ds-sp-rows">{[['拜访目标', v.visitGoal], ['沟通内容', v.followUpRecord], ['达成结果', v.expectation]].map(([k, val]) => <div key={k}><dt>{k}</dt><dd className="ds-sp-pre">{val}</dd></div>)}</dl><div className="ds-sp-next"><b>下一步计划</b><p>{v.nextAction}</p></div></section>
      {d.canReadVisitAdvice && <section className="ds-sp-block">
        <div className="ds-sp-block-head"><SbAiBadge state={a.status === 'loading' && !a.reading ? 'generating' : 'pending'} text="AI 建议 · 本次拜访" /><SbSegmented value={d.visitAdviceSection} options={d.visitAdviceSections || []} onChange={section => call('selectVisitAdviceSection', {dataset: {section}})} /></div>
        <div className="ds-sp-advice-actions">
          <Button type="link" size="small" disabled={a.status === 'loading'} onClick={() => call('loadVisitAdvice')}>刷新已有建议</Button>
          {d.canSuggestVisitTasks && !['loading', 'queued', 'running'].includes(a.status) && <Button type="link" size="small" onClick={() => call('requestVisitAdvice', {dataset: {adviceAction: 'generate', visitId: d.visitId, section: d.visitAdviceSection}})}>{a.id ? '重新生成建议' : '生成建议'}</Button>}
        </div>
        {a.status === 'loading' && <p className="ds-muted">{a.reading ? '正在读取已有建议…' : '正在提交生成请求…'}</p>}
        {['queued', 'running'].includes(a.status) && <p className="ds-muted">建议正在后台生成，可刷新进度或稍后回来查看。</p>}
        {a.status === 'stale' && <p className="ds-muted">原建议已失效，需要时可重新生成。</p>}
        {(!a.status || a.status === 'idle') && <p className="ds-muted">还没有生成建议。</p>}
        {a.status === 'error' && <p role="alert" className="ds-sp-error">{a.error}</p>}
        {a.status === 'ready' && <><p className="ds-sp-pre">{a.summary}</p><ol className="ds-sp-evidence">{(a.rows || []).map((row, i) => <li key={row.id || i}><b>{row.title}</b><span className="ds-muted">{row.detail}</span><AdviceActions page={page} advice={a} suggestion={row} invokeOn={invokeOn}/></li>)}</ol></>}
        <p className="ds-muted ds-sp-help">已归档正文只读；建议由你确认后形成待办。</p>
      </section>}
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
  const input = (key, placeholder, label, maxLength) => <Input aria-label={label} value={f[key] || ''} placeholder={placeholder} maxLength={maxLength} disabled={d.saving} onChange={e => call('inputField', { dataset: { key }, detail: { value: e.target.value } })} />;
  return <section className="ds-sp ds-sp-two-col" aria-label="维护客户信息">
    <aside className="ds-panel ds-sp-side">
      <div className="ds-sp-tags"><BusinessIdentityTags values={d.businessIdentities} /></div>
      <h1>{f.name}</h1>
      <p className="ds-muted">{d.isCustomerIdentity === false ? '纯伙伴档案不适用客户评分' : '保存后重新评估客户画像与作战地图位置'}</p>
      {d.isCustomerIdentity !== false && <dl className="ds-sp-info"><div><dt>作战象限</dt><dd>{a.quadrant}</dd></div><div><dt>客户潜力</dt><dd>{a.potential}</dd></div><div><dt>关系深度</dt><dd>{a.relationship}</dd></div><div><dt>当前风险</dt><dd>{a.risk}</dd></div></dl>}
    </aside>
    <section className="ds-panel ds-sp-main">
      {d.submissionError && <Alert type="error" showIcon title={d.submissionError} />}
      <section className="ds-sp-block"><h2>企业资料</h2>
        <CustomerIdentityFields data={d} invoke={invoke} disabled={d.saving} />
        <div className="ds-sp-grid">
          <SbField label="客户名称" help="不可修改"><div className="ds-sp-readonly">{f.name}</div></SbField>
          <SbField label="所属行业" required>{sel('行业', d.industryOptions, d.industryIndex, 'changeIndustry')}</SbField>
          {d.isCustomerIdentity !== false && <SbField label="客户类型" required>{sel('类型', d.customerTypeOptions, typeIndex, 'changeCustomerType')}</SbField>}
          <SbField label="客户优先级" required>{sel('优先级', d.customerLevelOptions, d.customerLevelIndex, 'changeCustomerLevel')}</SbField>
          <SbField label="客户 / 线索来源" required>{sel('来源', d.sourceOptions, d.sourceIndex, 'changeSource')}</SbField>
        </div>
      </section>
      <section className="ds-sp-block"><h2>首要联系人 <small className="ds-muted">角色仅限决策者、影响者、使用者</small></h2>
        <div className="ds-sp-grid">
          <SbField label="联系人姓名">{input('contact_name', '姓名', '联系人姓名', 100)}</SbField>
          <SbField label="联系人职位">{input('contact_title', '职位', '联系人职位', 100)}</SbField>
          <SbField label="联系人角色">{sel('角色', d.contactRoleOptions, d.contactRoleIndex, 'changeContactRole')}</SbField>
        </div>
      </section>
      <div className="ds-sp-bar"><SbBottomBar reason={d.isCustomerIdentity === false ? '保存伙伴企业资料' : '保存后更新客户画像'} primary={{ label: '保存修改', loading: d.saving, disabled: !d.identityOptionsReady, onClick: () => call('submit') }} /></div>
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
  const groups = opportunityGroupsInServerOrder(d.opportunityGroups || [], d.opportunities || []);
  const listState = d.loading ? 'loading' : d.loadError ? 'error' : !(d.filtered || []).length ? 'empty' : 'normal';
  return <section className="ds-sp ds-sp-fill" aria-label="全部商机">
    <div className="ds-panel ds-sp-card ds-sp-col">
      <div className="ds-sp-tools">
        {d.canViewTeam && <LabeledSearchSelect label="团队" value={d.teamIndex > 0 ? d.teamIndex : undefined} options={(d.teamOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeTeam', { detail: { value: i ?? 0 } })} />}
        {d.canViewTeam && <LabeledSearchSelect label="负责人" value={d.ownerIndex > 0 ? d.ownerIndex : undefined} options={(d.ownerOptions || []).map((o, i) => ({ value: i, label: o.label })).filter(o => o.value > 0)} onChange={i => call('changeFilter', { dataset: { key: 'owner' }, detail: { value: i ?? 0 } })} />}
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
