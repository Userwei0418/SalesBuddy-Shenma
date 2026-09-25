import React, {useRef} from 'react';
import { Button, Checkbox, Input, Steps } from 'antd';
import dayjs from 'dayjs';
import { SbAiBadge, SbBottomBar, SbDatePicker, SbField, SbLabeledSelect, SbListRow, SbMetricStrip, SbSearch, SbSheet, SbStatePanel, SbStatusTag, SbTextarea } from '@shandiant/ui-react';
import { OpportunityForm } from './OpportunityCreate.jsx';
import { FdeVisitOpportunity } from './FdeVisitOpportunity.jsx';
import './visit-confirm.css';
import AdviceActions from './AdviceActions.jsx';
import FollowupBusinessDemo from './FollowupBusinessDemo.jsx';
import {demoDraftKey} from './shenzhou-demo.mjs';

// 核对拜访记录（原 pages/visit-confirm/index）。三步：核对内容、AI 质检、确认保存；补充跟进信息也走这页。
// FDE 要选本人参与的商机（原子组件 #fdeVisitOpportunity）；选中已有商机后内嵌的商机表单是原子组件 #visitOpportunityForm，与新建商机页共用一套表单。
export function supportsVisitConfirm(page, data = {}) {
  return Boolean(page && page.route === 'pages/visit-confirm/index');
}
VisitConfirm.subcomponents = (page, d) => {
  if(d.archived) return (d.advice?.suggestions || []).map(s=>({selector:'advice-actions',suggestionId:s.id,required:Boolean(d.showAdvice),props:{analysisId:d.advice.id,suggestion:s}}));
  if (d.isFde) return [{ selector: '#fdeVisitOpportunity', required: Boolean(d.customerConfirmed && !d.editing), props: { customerId: d.customerId || '', selectedId: d.opportunityId || '', disabled: Boolean(d.busy) } }];
  const form = page.selectComponent('#visitOpportunityForm');
  return [
    { selector: '#visitOpportunityForm', required: Boolean(!d.editing && d.customerConfirmed && d.opportunityEditing), props: { visitContext: true, customerId: d.customerId || '', existing: d.selectedOpportunity || null, savedDraft: d.opportunityDraft || null, disabled: Boolean(d.busy) } },
    { selector: '#opportunityFdePicker', parent: '#visitOpportunityForm', required: false, props: { selected: form?.data?.form?.visit_fde_members || [], disabled: Boolean(d.busy) } },
  ];
};
const toneOf = t => ({ green: 'good', yellow: 'watch', red: 'bad', gray: 'pending' })[t] || 'pending';
const day = v => (v ? dayjs(v) : null);

export default function VisitConfirm({ page, data: d, invoke, invokeOn, select }) {
  const demoRef = useRef(null);
  const draftKey = demoDraftKey(page, d);
  const call = (name, payload = {}) => {
    if (['review', 'saveSupplement'].includes(name) && demoRef.current && !demoRef.current.validate()) return;
    if (name === 'saveDraft' && demoRef.current && !demoRef.current.save()) return;
    return invoke(name, payload);
  };
  const busy = Boolean(d.busy);
  const fde = Boolean(d.isFde);
  const field = (key, value) => call('inputField', { dataset: { key }, detail: { value } });
  const values = d.values || {};
  if (d.archived) {
    return <section className="ds-vconfirm" aria-label="拜访已保存">
      <div className="ds-panel ds-vc-done">
        <SbStatusTag tone="good" label="拜访记录录入成功" />
        <h1>{d.customerName}</h1>
        <SbMetricStrip columns={3} items={[{ key: 'count', label: '字段已归档', value: d.archivedCount }, { key: 'score', label: '质量评分', value: d.score == null ? null : `${d.score} 分`, missingText: '暂未评分' }, { key: 'grade', label: '质量等级', value: d.grade, missingText: '暂未评分' }]} />
        <FollowupBusinessDemo key={draftKey} draftKey={draftKey} readOnly />
        <div className="ds-vc-next"><b>下一步行动</b><p>{values.next_action || '未记录'}</p></div>
        {d.archiveWarning && <p role="alert">{d.archiveWarning}</p>}
        <div className="ds-vc-done-actions"><Button type="primary" onClick={()=>call('openVisit')}>查看本次跟进详情</Button>
          {!fde && d.canSuggestVisitTasks && (d.advice || d.adviceBusy || d.adviceError) && <Button onClick={() => call('openAdvice')}>查看本次待办建议</Button>}
          <Button type="primary" disabled={busy} onClick={() => call('openCustomer')}>查看客户档案</Button>
        </div>
      </div>
      <SbSheet open={Boolean(d.showAdvice)} title="本次拜访待办建议" onClose={() => call('closeAdvice')} footer={<Button onClick={() => call('closeAdvice')}>完成，稍后再看</Button>}>
        <div className="ds-vc-advice">
          <div className="ds-vc-advice-head"><SbAiBadge state={d.adviceBusy ? 'generating' : 'pending'} text={d.adviceBusy ? 'AI 生成中' : 'AI 生成 · 待办建议'} /><span className="ds-muted">拜访已保存，是否创建待办由你决定</span></div>
          {d.adviceBusy ? <p className="ds-muted">正在根据本次拜访整理建议…</p>
            : d.adviceError ? <p><span className="ds-vc-error" role="alert">{d.adviceError}</span> <Button type="link" size="small" onClick={() => call('loadAdvice')}>重新获取建议</Button></p>
            : d.advice ? <>
              <p className="ds-vc-advice-summary">{d.advice.summary}</p>
              {!(d.advice.suggestions || []).length ? <p className="ds-muted">本次没有需要补充的待办建议</p> : <ol className="ds-vc-advice-list">{d.advice.suggestions.map((s, i) => <li key={s.id || i}><b>{s.title}</b><span className="ds-muted">{s.evidence}</span><span>{s.action}</span><AdviceActions page={page} suggestion={s} invokeOn={invokeOn}/></li>)}</ol>}
            </> : null}
        </div>
      </SbSheet>
    </section>;
  }
  const editing = Boolean(d.editing);
  const step = d.flowStep || 'edit';
  const current = step === 'edit' ? 0 : step === 'analyzing' ? 1 : 1;
  const opp = (d.opportunityOptions || [])[d.opportunityIndex || 0] || { name: '不关联商机' };
  const pendingOpp = d.opportunityPendingOption;
  const inlineForm = !fde && !editing && d.customerConfirmed && d.opportunityEditing ? select('#visitOpportunityForm') : null;
  const inlinePicker = inlineForm ? select('#opportunityFdePicker', '#visitOpportunityForm') : null;
  const fdePicker = fde && d.customerConfirmed && !editing ? select('#fdeVisitOpportunity') : null;
  const form = <div className="ds-vc-form">
    <div className="ds-vc-grid">
      <SbField label="客户类型"><SbLabeledSelect label="类型" allowClear={false} value={d.customerTypeIndex} options={(d.customerTypeOptions || []).map((o, i) => ({ value: i, label: o }))} disabled={busy || editing} onChange={i => call('selectCustomerType', { detail: { value: i } })} width={200} /></SbField>
      <SbField label="客户名称" required>
        {d.customerConfirmed ? <div className="ds-vc-chosen"><b>{d.customerName}</b><span className="ds-muted">已在上一页确认关联</span></div> : <>
          <SbSearch value={d.customerQuery || ''} placeholder="搜索客户名称" loading={Boolean(d.searching)} onChange={value => call('inputCustomer', { detail: { value } })} />
          <p className="ds-muted ds-vc-hint">{d.searching ? '正在搜索…' : '点击匹配结果，确认本次跟进的客户'}</p>
          <div className="ds-vc-results">{(d.customers || []).map(c => <SbListRow key={c.id} name={c.name} summary={c.team_name || c.team || ''} onClick={() => call('chooseCustomer', { dataset: { id: c.id } })} />)}</div>
        </>}
      </SbField>
      {fde && d.customerConfirmed && !editing && <div className="ds-vc-span"><FdeVisitOpportunity picker={fdePicker} invokeOn={invokeOn} selectedId={d.opportunityId} /></div>}
      {d.customerConfirmed && (!fde || editing) && <SbField label={<span>商机名称 <small className="ds-muted">选填</small>{d.opportunityAIRecognized && <SbAiBadge state="pending" text="AI 已识别 · 待确认" />}</span>} help={d.opportunityAIHint}>
        {editing ? <div className="ds-vc-readonly">{values.opportunity_name || '未关联商机'}</div> : <>
          <button type="button" className="ds-vc-picker" disabled={busy} onClick={() => call('toggleOpportunityPicker')}><span>{opp.name}</span><span className="ds-vc-picker-link">选择</span></button>
          {d.opportunityLoading && !d.opportunityPickerOpen && <p className="ds-muted ds-vc-hint">正在读取商机…</p>}
          {d.opportunityError && !d.opportunityPickerOpen && <p className="ds-vc-error">{d.opportunityError} <Button type="link" size="small" onClick={() => call('retryOpportunities')}>重新加载</Button></p>}
        </>}
      </SbField>}
      {inlineForm && <div className="ds-vc-span ds-vc-inline-form"><div className="ds-vc-inline-head"><b>在此更新商机 <small className="ds-muted">{d.selectedOpportunity?.name}</small></b><span className="ds-muted">随拜访一起保存，保存前会再确认</span></div><OpportunityForm form={inlineForm} picker={inlinePicker} invokeOn={invokeOn} disabled={busy} /></div>}
      <div className="ds-vc-span"><FollowupBusinessDemo key={draftKey} ref={demoRef} draftKey={draftKey} disabled={busy} /></div>
      <SbField label="跟进日期"><div className="ds-vc-date"><SbDatePicker value={d.visitDate || null} disabled={busy} placeholder="选择跟进日期" onChange={s => (s ? call('selectVisitDate', { detail: { value: s } }) : call('clearVisitDate'))} /><span className="ds-muted">{d.sevenLabel} · 系统按跟进日期判断最近七天，含今天</span></div></SbField>
      <SbField label="对接人"><Input value={values.contact_name || ''} maxLength={300} placeholder="填写本次沟通的客户对接人" disabled={busy} onChange={e => field('contact_name', e.target.value)} /></SbField>
    </div>
    {(d.core || []).map(f => <SbField key={f.key} label={f.label} required>{editing ? <div className="ds-vc-readonly">{f.value || '未记录'}</div> : <SbTextarea showCount={false} value={f.value || ''} placeholder={f.placeholder} maxLength={15000} autoSize={{ minRows: 3, maxRows: 10 }} disabled={busy} onChange={v => field(f.key, v)} />}</SbField>)}
    {d.isFirstVisit && <div className="ds-vc-first">
      <div className="ds-vc-first-head"><b>首次拜访补充</b><span className="ds-muted">四项必填 · AI 已尝试识别，未识别到的请人工补充</span></div>
      <div className="ds-vc-grid">{(d.firstVisitFields || []).map(f => <SbField key={f.key} label={<span>{f.label} <SbAiBadge state="pending" text="AI 生成 · 待确认" /></span>} required>
        {f.key === 'contact_role' ? <>
          {d.catalogError && <p className="ds-vc-error">{d.catalogError} <Button type="link" size="small" onClick={() => call('loadBusinessOptions')}>重试</Button></p>}
          <SbLabeledSelect label="角色" placeholder={f.placeholder} value={values.contact_role ? d.contactRoleIndex : undefined} options={(d.contactRoleOptions || []).map((o, i) => ({ value: i, label: o }))} disabled={busy || !(d.contactRoleOptions || []).length} onChange={i => call('selectContactRole', { detail: { value: i } })} width={260} />
        </> : (f.key === 'customer_main_business' || f.key === 'customer_needs') ? <SbTextarea showCount={false} value={f.value || ''} placeholder={f.placeholder} maxLength={3000} autoSize={{ minRows: 2, maxRows: 6 }} disabled={busy} onChange={v => field(f.key, v)} />
          : <Input value={f.value || ''} placeholder={f.placeholder} maxLength={300} disabled={busy} onChange={e => field(f.key, e.target.value)} />}
      </SbField>)}</div>
    </div>}
    <div className="ds-vc-grid">
      <SbField label="创建时间"><SbDatePicker value={d.createdDate || null} disabled={busy} placeholder="选择填写日期" onChange={s => (s ? call('selectCreatedDate', { detail: { value: s } }) : call('clearCreatedDate'))} /></SbField>
      <SbField label="跟进人"><div className="ds-vc-readonly">{d.recorderName || '未记录'}</div></SbField>
      {!fde && <SbField label={<span>协同人 <small className="ds-muted">选填</small></span>}>
        <button type="button" className="ds-vc-picker" onClick={() => call('toggleColleagues')}><span className={d.collaboratorNames ? '' : 'ds-muted'}>{d.collaboratorNames || '选择参与本次拜访的同事'}</span><span className="ds-vc-picker-link">{d.showColleagues ? '收起' : '选择'}</span></button>
        {d.showColleagues && <div className="ds-vc-colleagues">
          <Input placeholder="搜索同事姓名或账号" value={d.colleagueQuery || ''} onChange={e => call('inputColleagueQuery', { detail: { value: e.target.value } })} />
          <Checkbox.Group value={(d.colleagues || []).filter(c => c.selected).map(c => c.id)} disabled={busy} onChange={ids => call('selectColleagues', { detail: { value: ids } })}>{(d.colleagues || []).map(c => <Checkbox key={c.id} value={c.id}>{c.name}</Checkbox>)}</Checkbox.Group>
          {!(d.colleagues || []).length && <p className="ds-muted ds-vc-hint">没有匹配的同事</p>}
        </div>}
      </SbField>}
    </div>
  </div>;
  const review = <div className="ds-vc-review">
    <section className="ds-vc-card">
      <div className="ds-vc-card-head"><div><b>AI 质量审核</b><span className="ds-muted">评估本次拜访录入质量，并给出评分与改进建议</span></div>{d.score !== null && d.score !== undefined && <div className={`ds-vc-score ds-vc-score-${toneOf(d.scoreSignal?.tone)}`}><b>{d.score}</b><span>{d.reviewStale ? '修改前评分' : `分 · ${d.scoreSignal?.label || ''}`}</span></div>}</div>
      <p className="ds-muted ds-vc-rule">提交标准：质量评分须{d.admissionRequirement}</p>
      {d.quality && <ul className="ds-vc-suggestions">{(d.quality.suggestions || []).map(s => <li key={s}>{s}</li>)}</ul>}
      {d.score !== null && d.score !== undefined && !d.scorePassed && !d.reviewStale && <p className="ds-vc-error" role="alert">质量审核未通过，请按建议完善内容后重新审核。</p>}
    </section>
    <section className="ds-vc-card">
      <div className="ds-vc-card-head"><div><b>下一步审核</b><span className="ds-muted">硬性检查下一步计划是否可执行</span></div><SbStatusTag tone={d.nextReviewPassed ? 'good' : 'bad'} label={d.nextReviewStatus} /></div>
      <p className="ds-muted ds-vc-rule">必须同时包含具体日期，以及明确的行动计划或目标</p>
      {d.quality?.next_action && <ul className="ds-vc-suggestions">{(d.quality.next_action.suggestions || []).map(s => <li key={s}>{s}</li>)}</ul>}
      {d.quality && !d.nextReviewPassed && !d.reviewStale && <p className="ds-vc-error" role="alert">下一步审核未通过，即使质量评分通过也不能提交。</p>}
    </section>
  </div>;
  const bar = editing ? <SbBottomBar reason={d.customerName} primary={{ label: '保存补充信息', loading: busy, onClick: () => call('saveSupplement') }} />
    : step === 'edit' ? <SbBottomBar reason="下一步交给 AI 质检，评分和下一步计划都要过关才能保存" secondary={{ label: '保存草稿', disabled: busy, onClick: () => call('saveDraft') }} primary={{ label: d.pendingReviewId ? '继续查看质检' : '下一步 · AI 质检', loading: busy, onClick: () => call('review') }} />
    : step === 'result' ? <SbBottomBar reason={d.blockReason || '两项审核都通过后可以保存'} secondary={{ label: '返回完善', disabled: busy, onClick: () => call('backToEdit') }} primary={{ label: '确认保存', loading: busy, disabled: !d.canSubmit, disabledReason: d.blockReason || '', onClick: () => call('archive') }} /> : null;
  return <section className="ds-vconfirm" aria-label={editing ? '补充跟进信息' : '核对拜访记录'}>
    <div className="ds-panel ds-vc-main">
      {!editing && <div className="ds-vc-steps"><Steps size="small" current={current} items={[{ title: '核对内容' }, { title: 'AI 质检' }, { title: '确认保存' }]} /></div>}
      {editing && <div className="ds-vc-steps"><b>补充跟进信息</b><span className="ds-muted">{d.customerName}</span></div>}
      {d.errorText && <p className="ds-vc-error ds-vc-banner" role="alert">{d.errorText}</p>}
      <div className="ds-vc-body">
        {step === 'analyzing' && !editing ? <SbStatePanel state="loading" title="正在为这次拜访做质检" description={`${d.analysisPhrase || ''} · 完成后会展示评分与改进建议`} />
          : step === 'result' && !editing ? <><FollowupBusinessDemo key={draftKey} draftKey={draftKey} readOnly />{review}</> : form}
      </div>
      {bar && <div className="ds-vc-bar">{bar}</div>}
    </div>
    <SbSheet open={Boolean(d.opportunityPickerOpen) && !editing && !fde} title={<span>选择关联商机 <small className="ds-muted">{d.customerName}</small></span>} onClose={() => call('closeOpportunityPicker')} footer={<><Button onClick={() => call('closeOpportunityPicker')}>取消</Button><Button type="primary" disabled={busy || !pendingOpp || pendingOpp.unverified} onClick={() => call('confirmOpportunityPicker')}>确定</Button></>}>
      <div className="ds-vc-opp">
        <Input value={d.opportunityQuery || ''} maxLength={100} placeholder="搜索该客户下的商机" disabled={busy} onChange={e => call('searchOpportunities', { detail: { value: e.target.value } })} />
        <div className="ds-vc-opp-list">{(d.opportunityOptions || []).map(o => <button key={o.id || 'none'} type="button" className={`ds-vc-opp-row ${o.id === d.opportunityPendingId ? 'is-selected' : ''}`} onClick={() => call('chooseOpportunity', { dataset: { id: o.id } })}><span>{o.name}</span><span className="ds-muted">{o.unverified ? '待核对' : o.id === d.opportunityPendingId ? '已选' : ''}</span></button>)}</div>
        {d.opportunityLoading && <p className="ds-muted">正在读取商机…</p>}
        {!d.opportunityLoading && !(d.opportunityItems || []).length && !d.opportunityError && <p className="ds-muted">{d.opportunityQuery ? '没有匹配的已有商机，请调整关键词' : '该客户暂无可关联的已有商机'}</p>}
        {d.opportunityError && <p className="ds-vc-error">{d.opportunityError} <Button type="link" size="small" onClick={() => call('retryOpportunities')}>重新加载</Button></p>}
        {d.opportunityHasMore && <Button size="small" disabled={busy || d.opportunityLoading} onClick={() => call('moreOpportunities')}>加载更多</Button>}
      </div>
    </SbSheet>
  </section>;
}
