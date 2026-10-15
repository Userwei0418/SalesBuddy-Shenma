import {Input} from './ImeInput.jsx';
import React, { useEffect, useState } from 'react';
import {Button, Progress, Select} from 'antd';
import {SbAiBadge, SbBottomBar, SbStatusTag} from '@shandiant/ui-react';
import {can, open} from './pc/data.js';
import {useAsync} from './pc/ui.jsx';
import {creationSearchBlock, exactCustomerName, loadExistingCustomers} from './pc/customer-flow.js';
import './customer-create.css';
import CustomerIdentityFields from './CustomerIdentityFields.jsx';

// Web 只改变录入方式；选项取运行时目录，必填计数、草稿、语音和提交沿用原 Page。
export function supportsCustomerCreate(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-create/index' && Array.isArray(data.fields));
}

const OPTION_KEYS = { industry: 'industry', customer_type: 'customer_type', level_code: 'level_code', lead_source: 'source', contact_role: 'contact_role' };
const CONTACT_KEYS = new Set(['contact_name', 'contact_title', 'contact_role']);
const PLACEHOLDERS = { customer_name: '请输入客户全称', partner_name: '选填，可留空', contact_name: '请输入首要联系人姓名', contact_title: '请输入联系人职位' };
const REQUEST_STATUS = {
  pending: {label: '待运营审批', tone: 'watch'},
  approved: {label: '已通过', tone: 'good'},
  rejected: {label: '已驳回', tone: 'bad'},
  cancelled: {label: '已撤回', tone: 'pending'},
};

function requestTitle(request) {
  return request?.payload?.name || request?.payload?.customer_name || '未命名客户';
}

function requestTime(request) {
  const value = request?.requested_at || request?.created_at;
  return value ? new Date(value).toLocaleString('zh-CN', {timeZone: 'Asia/Shanghai', hour12: false}) : '提交时间待同步';
}

function CustomerField({ field, options, disabled, onChange, onBlur }) {
  // 输入即时回显，不等待桥接层下一帧渲染；语音或草稿恢复仍从原字段同步。
  const [value, setValue] = useState(field.value || '');
  const [touched, setTouched] = useState(false);
  useEffect(() => { setValue(field.value || ''); }, [field.value]);
  const id = `customer-create-${field.key}`;
  const error = field.validationError || (touched && field.missing ? `请${options ? '选择' : '填写'}${field.label}` : '');
  const invalid = Boolean(error);
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
        placeholder={PLACEHOLDERS[field.key] || `请输入${field.label}`} maxLength={field.key === 'customer_name' ? 200 : undefined}
        onChange={e => change(e.target.value)} onBlur={blur} />}
    {invalid && <span id={`${id}-error`} className="ds-cc-field-error" role="alert">{error}</span>}
  </div>;
}

export default function CustomerCreate({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const voiceBusy = d.isRecording || d.isStarting || d.isStopping || d.isParsing;
  const busy = Boolean(voiceBusy || d.loading || d.submitting);
  const notice = d.draftRestored ? '已恢复你上次保存的建档草稿，可继续编辑。' : d.voiceParsed ? '语音草案已填入，请重点核对客户名称、客户类型和联系人信息。' : '';
  const catalog = globalThis.SalesRuntime?.businessOptions()?.customerCreation || {};
  const optionsReady = d.creationOptionsReady === true;
  const optionsBlock = optionsReady ? '' : d.creationOptionsRefreshing ? '正在更新创建选项' : d.creationOptionsError || '正在加载创建选项';
  const fields = d.fields || [];
  const invalidFields = fields.filter(field => field.validationError);
  const focusField = key => {
    if (page !== globalThis.SalesRuntime?.current || page._destroyed) return;
    const input = document.getElementById(`customer-create-${key}`);
    input?.scrollIntoView({block: 'center', behavior: 'auto'});
    input?.focus({preventScroll: true});
  };
  useEffect(() => {
    if (!d.submissionError || !invalidFields.length) return;
    focusField(invalidFields[0].key);
  }, [page, d.submissionError]);
  const name = String(fields.find(field => field.key === 'customer_name')?.value || '').trim();
  const searchAllowed = can('customer.claim_directory');
  const existing = useAsync(async signal => {
    if (!searchAllowed || !name) return null;
    await new Promise(resolve => { const timer = setTimeout(resolve, 350); signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, {once: true}); });
    if (signal.aborted) return null;
    return loadExistingCustomers(name);
  }, [searchAllowed, name]);
  const searchBlock = creationSearchBlock({enabled: searchAllowed, name, ...existing});
  const exactMatch = existing.value?.items.some(row => exactCustomerName(row.name, name));
  const goClaim = query => { if (!busy) { call('saveDraft'); open.route(`pages/customer-claim/index?q=${encodeURIComponent(query)}`); } };
  const groups = [
    { key: 'customer', title: '客户资料', fields: fields.filter(f => !f.hidden && !CONTACT_KEYS.has(f.key)) },
    { key: 'contact', title: '联系人信息', fields: fields.filter(f => !f.hidden && CONTACT_KEYS.has(f.key)) },
  ];
  const requestRows = Array.isArray(d.creationRequests) ? d.creationRequests : [];
  const editable = () => page === globalThis.SalesRuntime?.current && !page._destroyed && !['isRecording', 'isStarting', 'isStopping', 'isParsing', 'loading', 'submitting'].some(key => page.data[key]);
  const updateField = (key, value, teamId) => {
    if (!editable()) return;
    const field = page.data.fields.find(f => f.key === key);
    if (!field || field.readonly) return;
    page.setData({ draftRestored: false });
    page.refresh(page.data.fields.map(f => f.key === key ? { ...f, value, edited: true, validationError: '', ...(key === 'target_team' ? { teamId: teamId || '' } : {}) } : f));
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
          <div className="ds-cc-progress-head"><b>必填 {d.completedRequiredCount} / {d.requiredCount}</b><span className={invalidFields.length ? 'ds-cc-field-error' : 'ds-muted'}>{invalidFields.length ? `${invalidFields.length} 项内容需修正` : d.missingCount ? `还有 ${d.missingCount} 项待补充` : '必填项已齐'}</span></div>
          <Progress percent={Number(d.progressPercent) || 0} size="small" showInfo={false} strokeColor="var(--ui-primary)" trailColor="var(--ui-line)" />
        </div>
        {d.submissionError && <div className="ds-cc-submit-error" role="alert">
          <span>{d.submissionError}</span>
          {invalidFields.length > 0 && <div className="ds-cc-error-links">{invalidFields.map(field => <Button key={field.key} type="link" size="small" onClick={() => focusField(field.key)}>检查{field.label}</Button>)}</div>}
        </div>}
        {!optionsReady && <div className="ds-cc-options-status" role={d.creationOptionsError ? 'alert' : 'status'}>
          <span>{optionsBlock}{d.creationOptionsError ? '，已填内容已保留。' : '…'}</span>
          {d.creationOptionsError && <Button size="small" loading={Boolean(d.creationOptionsRefreshing)} disabled={busy || Boolean(d.creationOptionsRefreshing)} onClick={() => call('refreshCreationOptions')}>刷新创建选项</Button>}
        </div>}
      </div>
      {notice && <p className={`ds-cc-notice ${d.voiceParsed && !d.draftRestored ? 'is-ai' : ''}`}>{d.voiceParsed && !d.draftRestored && <SbAiBadge state="pending" text="AI 生成 · 待核对" />}{notice}</p>}
      <div className="ds-cc-scroll">
      {d.canRequestCreate && <section className="ds-cc-requests" aria-label="我的新建申请">
        <div className="ds-cc-requests-head"><div><h2>我的新建申请</h2><span className="ds-muted">新建通过后为未认领客户，可再申请认领</span></div>{can('customer.read') && <Button disabled={busy} onClick={() => {call('saveDraft'); open.route('pages/customer-history/index');}}>全部申请与认领记录</Button>}</div>
        {d.creationRequestsLoading && <p className="ds-cc-requests-message" role="status">正在读取申请状态…</p>}
        {!d.creationRequestsLoading && d.creationRequestsError && <p className="ds-cc-requests-message is-error" role="alert">{d.creationRequestsError}</p>}
        {!d.creationRequestsLoading && !d.creationRequestsError && !requestRows.length && <p className="ds-cc-requests-message">还没有客户新建申请</p>}
        {!d.creationRequestsLoading && !d.creationRequestsError && requestRows.length > 0 && <div className="ds-cc-request-list">
          {(can('customer.read') ? requestRows.slice(0, 3) : requestRows).map(request => {
            const state = REQUEST_STATUS[request.status] || {label: request.status || '待核对', tone: 'pending'};
            return <div className="ds-cc-request-row" key={request.id || `${request.status}-${request.requested_at}`}>
              <div className="ds-cc-request-main"><b title={requestTitle(request)}>{requestTitle(request)}</b><span className="ds-muted">{requestTime(request)}</span></div>
              <div className="ds-cc-request-result"><SbStatusTag tone={state.tone} label={state.label} />{request.decision_reason && <span className={`ds-cc-request-reason${request.status === 'rejected' ? ' is-rejected' : ''}`} title={request.decision_reason}>{request.decision_reason}</span>}{request.status === 'approved' && can('customer.claim') && <Button size="small" disabled={busy} onClick={() => goClaim(request.reviewed_payload?.name || requestTitle(request))}>查询认领状态</Button>}</div>
            </div>;
          })}
        </div>}
      </section>}
      <div className="ds-cc-identity"><CustomerIdentityFields data={d} invoke={invoke} disabled={busy} /></div>
      <div className="ds-cc-form">
        {groups.map(group => <section key={group.key} className={`ds-cc-group ds-cc-group-${group.key}`} aria-labelledby={`ds-cc-heading-${group.key}`}>
          <h2 id={`ds-cc-heading-${group.key}`}>{group.title}</h2>
          <div className="ds-cc-fields">{group.fields.map(field => <React.Fragment key={field.key}>
            <CustomerField field={field} options={optionsFor(field)} disabled={busy || (Boolean(OPTION_KEYS[field.key]) && !optionsReady)} onChange={(value, teamId) => updateField(field.key, value, teamId)} onBlur={() => trimField(field.key)} />
            {field.key === 'customer_name' && <section className="ds-cc-existing" aria-label="存量客户检索">
              <div className="ds-cc-existing-head"><b>先检索存量客户</b>{searchAllowed && <Button size="small" disabled={busy || !name || existing.loading} onClick={existing.reload}>重新检索</Button>}</div>
              {!searchAllowed ? <p>当前身份无存量目录查看权限，提交时仍会校验重复客户。</p> : !name ? <p>填写客户全称后自动检索，已有客户可直接申请认领。</p> : existing.loading ? <p role="status">正在检索…</p> : existing.error ? <p role="alert">{existing.error} <Button size="small" onClick={existing.reload}>重试</Button></p> : existing.value && <>
                <p>{existing.value.total ? `检索到 ${existing.value.total} 家匹配客户，请核对名称和负责人。` : '未检索到匹配客户，可继续填写；提交和审批时会再次校验。'}</p>
                {existing.value.items.slice(0, 5).map(row => <div className="ds-cc-existing-row" key={row.id}>
                  <div><b>{row.name}</b><span>{row.owner_name || '未分配负责人'} · {row.claim_status === 'pending' ? '认领待审批' : row.can_claim ? '可申请认领' : '已有客户'}</span></div>
                  {can('customer.claim') && <Button size="small" disabled={busy} onClick={() => goClaim(row.name)}>查看认领</Button>}
                </div>)}
                {existing.value.total > 5 && can('customer.claim') && <Button type="link" disabled={busy} onClick={() => goClaim(name)}>查看全部匹配客户</Button>}
                {exactMatch ? <p className="is-error" role="alert">已有同名客户，请使用已有客户，无需重复建档。</p> : existing.value.total > 0 && <p>如不是同一客户，可继续填写并提交申请。</p>}
              </>}
            </section>}
          </React.Fragment>)}</div>
        </section>)}
      </div>
      </div>
      <div className="ds-cc-bar"><SbBottomBar reason={d.requestMode ? '运营审核通过后建立未认领客户，认领需另行申请' : d.role === 'sales' ? '创建后客户负责人自动设为本人' : '创建后进入待分配客户池'} secondary={{ label: '保存草稿', disabled: busy, onClick: () => call('saveDraft') }} primary={{ label: d.requestMode ? '提交新建申请' : '创建客户', loading: Boolean(d.submitting), disabled: busy || !optionsReady || !d.identityOptionsReady || Boolean(d.missingCount) || Boolean(searchBlock), disabledReason: voiceBusy ? '先录完这段语音' : !d.identityOptionsReady ? '业务身份选项未就绪' : optionsBlock || searchBlock || (d.missingCount ? `还有 ${d.missingCount} 个必填项` : ''), onClick: () => { if (editable() && page.data.creationOptionsReady === true && !searchBlock && String(page.data.fields.find(field => field.key === 'customer_name')?.value || '').trim() === name) call('submitCustomer'); } }} /></div>
    </div>
  </section>;
}
