import React, { useEffect, useState } from 'react';
import { Button, Input, Progress, Select, Tag } from 'antd';
import { SbAiBadge, SbBottomBar } from '@shandiant/ui-react';
import './customer-create.css';

// Web 只改变录入方式；选项取运行时目录，必填计数、草稿、语音和提交沿用原 Page。
export function supportsCustomerCreate(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-create/index' && Array.isArray(data.fields));
}

const OPTION_KEYS = { industry: 'industry', customer_type: 'customer_type', level_code: 'level_code', lead_source: 'source', contact_role: 'contact_role' };
const CONTACT_KEYS = new Set(['contact_name', 'contact_title', 'contact_role']);
const PLACEHOLDERS = { customer_name: '请输入客户全称', company_reference: '请输入公司客户编号或审批单号', partner_name: '选填，可留空', contact_name: '请输入首要联系人姓名', contact_title: '请输入联系人职位' };

function CustomerField({ field, options, disabled, onChange, onBlur }) {
  // 输入即时回显，不等待桥接层下一帧渲染；语音或草稿恢复仍从原字段同步。
  const [value, setValue] = useState(field.value || '');
  const [touched, setTouched] = useState(false);
  useEffect(() => { setValue(field.value || ''); }, [field.value]);
  const id = `customer-create-${field.key}`;
  const invalid = touched && field.missing;
  const select = Boolean(options);
  const change = (next, option) => {
    const text = select ? (option?.label || '') : next;
    setValue(text);
    onChange(text, option?.teamId);
  };
  const blur = () => { setTouched(true); onBlur(); };
  const selected = field.key === 'target_team'
    ? (field.teamId || options?.find(o => o.label === value)?.value || value || undefined)
    : value || undefined;
  return <div className={`ds-cc-field ${field.key === 'customer_name' ? 'ds-cc-field-wide' : ''}`}>
    <label className="ds-cc-field-label" htmlFor={id}>{field.label}<span className="ds-cc-required">{field.required ? '必填' : '选填'}</span></label>
    {select ? <Select id={id} aria-label={field.label} aria-required={Boolean(field.required)} aria-invalid={Boolean(invalid)} aria-describedby={invalid ? `${id}-error` : undefined}
      value={selected} options={options} optionFilterProp="label" showSearch allowClear
      disabled={disabled || field.readonly} placeholder={`请选择${field.label}`} status={invalid ? 'error' : undefined}
      onChange={change} onBlur={blur} notFoundContent="暂无可选项" />
      : <Input id={id} aria-required={Boolean(field.required)} aria-invalid={Boolean(invalid)} aria-describedby={invalid ? `${id}-error` : undefined}
        value={value} readOnly={Boolean(field.readonly)} disabled={disabled} status={invalid ? 'error' : undefined}
        placeholder={PLACEHOLDERS[field.key] || `请输入${field.label}`}
        onChange={e => change(e.target.value)} onBlur={blur} />}
    {invalid && <span id={`${id}-error`} className="ds-cc-field-error" role="alert">请{select ? '选择' : '填写'}{field.label}</span>}
  </div>;
}

export default function CustomerCreate({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const voiceBusy = d.isRecording || d.isStarting || d.isStopping || d.isParsing;
  const busy = Boolean(voiceBusy || d.submitting);
  const notice = d.draftRestored ? '已恢复你上次保存的建档草稿，可继续编辑。' : d.voiceParsed ? '语音草案已填入，请重点核对客户名称、客户类型和联系人信息。' : '';
  const catalog = globalThis.SalesRuntime?.businessOptions()?.customer || {};
  const fields = d.fields || [];
  const groups = [
    { key: 'customer', title: '客户资料', fields: fields.filter(f => !CONTACT_KEYS.has(f.key)) },
    { key: 'contact', title: '联系人信息', fields: fields.filter(f => CONTACT_KEYS.has(f.key)) },
  ];
  const editable = () => page === globalThis.SalesRuntime?.current && !page._destroyed && !['isRecording', 'isStarting', 'isStopping', 'isParsing', 'submitting'].some(key => page.data[key]);
  const updateField = (key, value, teamId) => {
    if (!editable()) return;
    const field = page.data.fields.find(f => f.key === key);
    if (!field || field.readonly) return;
    page.setData({ draftRestored: false });
    page.refresh(page.data.fields.map(f => f.key === key ? { ...f, value, edited: true, ...(key === 'target_team' ? { teamId: teamId || '' } : {}) } : f));
  };
  const trimField = key => {
    if (!editable()) return;
    const field = page.data.fields.find(f => f.key === key);
    if (field && field.value !== String(field.value || '').trim()) updateField(key, String(field.value || '').trim(), field.teamId);
  };
  const optionsFor = field => {
    if (field.key === 'target_team') return (page.directoryTeams || []).map(team => ({ value: team.id, label: team.name, teamId: team.id }));
    if (OPTION_KEYS[field.key]) return (catalog[OPTION_KEYS[field.key]] || []).map(label => ({ value: label, label }));
    return undefined;
  };
  return <section className="ds-ccreate" aria-label={d.requestMode ? '客户建档申请' : '创建客户'}>
    {d.canViewRequests && <nav className="ds-cc-tabs" aria-label="客户建档">
      <Button type={d.activeTab === 'form' ? 'primary' : 'default'} onClick={() => call('showForm')}>填写申请</Button>
      <Button type={d.activeTab === 'requests' ? 'primary' : 'default'} onClick={() => call('showRequests')}>我的申请</Button>
    </nav>}
    {d.activeTab === 'requests' ? <div className="ds-panel ds-cc-requests">
      <header className="ds-cc-requests-heading"><h2>我的建档申请 · {d.requestsTotal || 0} 条</h2><Button loading={d.requestsLoading} onClick={() => call('showRequests')}>刷新</Button></header>
      {d.requestsError && <div className="ds-cc-error" role="alert">{d.requestsError}<Button type="link" onClick={() => call('showRequests')}>重试</Button></div>}
      {!d.requestsLoading && !d.requestsError && !(d.requests || []).length && <p className="ds-muted">暂无建档申请，可在“填写申请”提交新客户。</p>}
      {d.requestsLoading && <p className="ds-muted" role="status">正在加载申请…</p>}
      <div className="ds-cc-request-list">{(d.requests || []).map(item => <article className="ds-cc-request" key={item.id}>
        <header><h3>{item.customerName}</h3><Tag color={item.status === 'approved' ? 'green' : item.status === 'rejected' ? 'red' : item.status === 'pending' ? 'blue' : 'default'}>{item.statusLabel}</Tag></header>
        <p className="ds-muted">编号 / 审批单号：{item.companyReference || '未填写'}</p>
        <p className="ds-muted">提交时间：{item.requestedLabel}</p>
        {item.reviewedLabel && <p className="ds-muted">处理时间：{item.reviewedLabel} {item.reviewer_name || ''}</p>}
        {item.decision_reason && <p className="ds-cc-request-reason">{item.status === 'rejected' ? '驳回原因' : '处理说明'}：{item.decision_reason}</p>}
        {item.status === 'approved' && <p>客户已建档。客户归属需另行申请认领，审批通过后生效。</p>}
        {item.status === 'approved' && d.canClaimCustomer && <Button onClick={() => call('openCustomerClaim')}>前往客户认领</Button>}
        {item.status === 'pending' && <Button loading={d.withdrawingId === item.id} disabled={Boolean(d.withdrawingId)} onClick={() => call('withdrawRequest', {dataset:{id:item.id}})}>撤回申请</Button>}
      </article>)}</div>
      {d.requestsHasMore && <Button loading={d.requestsLoading} onClick={() => call('loadMoreRequests')}>加载更多</Button>}
    </div> : <div className="ds-panel ds-cc-main">
      <div className="ds-cc-top">
        <div className="ds-cc-progress">
          <div className="ds-cc-progress-head"><b>必填 {d.completedRequiredCount} / {d.requiredCount}</b><span className="ds-muted">{d.missingCount ? `还有 ${d.missingCount} 项待补充` : '必填项已齐'}</span></div>
          <Progress percent={Number(d.progressPercent) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
        </div>
      </div>
      {notice && <p className={`ds-cc-notice ${d.voiceParsed && !d.draftRestored ? 'is-ai' : ''}`}>{d.voiceParsed && !d.draftRestored && <SbAiBadge state="pending" text="AI 生成 · 待核对" />}{notice}</p>}
      <div className="ds-cc-form">
        {groups.map(group => <section key={group.key} className={`ds-cc-group ds-cc-group-${group.key}`} aria-labelledby={`ds-cc-heading-${group.key}`}>
          <h2 id={`ds-cc-heading-${group.key}`}>{group.title}</h2>
          <div className="ds-cc-fields">{group.fields.map(field => <CustomerField key={field.key} field={field} options={optionsFor(field)} disabled={busy} onChange={(value, teamId) => updateField(field.key, value, teamId)} onBlur={() => trimField(field.key)} />)}</div>
        </section>)}
      </div>
      <div className="ds-cc-bar"><SbBottomBar reason={d.requestMode ? '运营审批通过后建档，客户归属仍需另行认领' : '创建后进入待分配客户池'} secondary={{ label: '保存草稿', disabled: busy, onClick: () => call('saveDraft') }} primary={{ label: d.submitting ? '提交中…' : d.requestMode ? '提交建档申请' : '创建客户', disabled: busy || Boolean(d.missingCount), disabledReason: voiceBusy ? '先录完这段语音' : d.missingCount ? `还有 ${d.missingCount} 个必填项` : '', onClick: () => call('submitCustomer') }} /></div>
    </div>}
  </section>;
}
