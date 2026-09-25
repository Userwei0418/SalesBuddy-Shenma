import React, { useEffect, useState } from 'react';
import { Input, Progress, Select } from 'antd';
import { SbAiBadge, SbBottomBar } from '@shandiant/ui-react';
import './customer-create.css';

// Web 只改变录入方式；选项取运行时目录，必填计数、草稿、语音和提交沿用原 Page。
export function supportsCustomerCreate(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-create/index' && Array.isArray(data.fields));
}

const OPTION_KEYS = { industry: 'industry', customer_type: 'customer_type', level_code: 'level_code', lead_source: 'source', contact_role: 'contact_role' };
const CONTACT_KEYS = new Set(['contact_name', 'contact_title', 'contact_role']);
const PLACEHOLDERS = { customer_name: '请输入客户全称', partner_name: '选填，可留空', contact_name: '请输入首要联系人姓名', contact_title: '请输入联系人职位' };

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
  const notice = d.draftRestored ? '已恢复你上次保存的建档草稿，可继续编辑。' : d.voiceParsed ? '语音草案已填入，请重点核对客户名称、客户类型和联系人信息。' : '';
  const catalog = globalThis.SalesRuntime?.businessOptions()?.customer || {};
  const fields = d.fields || [];
  const groups = [
    { key: 'customer', title: '客户资料', fields: fields.filter(f => !CONTACT_KEYS.has(f.key)) },
    { key: 'contact', title: '联系人信息', fields: fields.filter(f => CONTACT_KEYS.has(f.key)) },
  ];
  const editable = () => page === globalThis.SalesRuntime?.current && !page._destroyed && !['isRecording', 'isStarting', 'isStopping', 'isParsing'].some(key => page.data[key]);
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
  return <section className="ds-ccreate" aria-label="创建客户">
    <div className="ds-panel ds-cc-main">
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
          <div className="ds-cc-fields">{group.fields.map(field => <CustomerField key={field.key} field={field} options={optionsFor(field)} disabled={Boolean(voiceBusy)} onChange={(value, teamId) => updateField(field.key, value, teamId)} onBlur={() => trimField(field.key)} />)}</div>
        </section>)}
      </div>
      <div className="ds-cc-bar"><SbBottomBar reason={d.role === 'sales' ? '创建后客户负责人自动设为本人' : '创建后进入待分配客户池'} secondary={{ label: '保存草稿', disabled: voiceBusy, onClick: () => call('saveDraft') }} primary={{ label: '创建客户', disabled: voiceBusy || Boolean(d.missingCount), disabledReason: voiceBusy ? '先录完这段语音' : d.missingCount ? `还有 ${d.missingCount} 个必填项` : '', onClick: () => call('submitCustomer') }} /></div>
    </div>
  </section>;
}
