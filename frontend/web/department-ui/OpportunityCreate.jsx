import {Input, SbSearch} from './ImeInput.jsx';
import React from 'react';
import {Button, Checkbox, Steps} from 'antd';
import dayjs from 'dayjs';
import {SbBottomBar, SbDatePicker, SbField, SbLabeledSelect, SbListRow, SbSegmented, SbStatePanel} from '@shandiant/ui-react';
import {PeopleDialog} from './WorkflowOverlays.jsx';
import './opportunity-create.css';
import OpportunityMaterials from './OpportunityMaterials.jsx';
import OpportunityRecognizedActual from './OpportunityRecognizedActual.jsx';
import OpportunityExtras from './OpportunityExtras.jsx';
import {can, open} from './pc/data.js';
import ForecastAmountInput from './ForecastAmountInput.jsx';

// 新建／修改商机（原 pages/opportunity-create/index）。页面本身只管选客户和提交；表单的数据与校验都在原子组件 opportunity-form 里，
// 这里通过 select('#opportunityForm') 拿到它的实例，读 form.data、用 invokeOn 调它的方法，协助 FDE 选择器是它的子组件 #opportunityFdePicker。
export function supportsOpportunityCreate(page) {
  return Boolean(page && page.route === 'pages/opportunity-create/index');
}
const day = v => (v ? dayjs(v) : null);
const num = v => (v === '' || v === null || v === undefined ? null : Number(v));
const draftLocked = d => Boolean(d.busy || ['saving', 'unknown', 'checking', 'succeeded'].includes(d.saveStatus));

// Sales keeps one current forecast per quarter. The shared form owns its draft,
// validation and save; PC does not derive another amount from stage probability.
export function QuarterForecast({form, invokeOn, disabled}) {
  const f = form.data || {};
  const on = (name, payload) => invokeOn(form, name, payload);
  return <>
    <button type="button" className="ds-oc-fold" onClick={() => on('toggleForecast')} aria-expanded={Boolean(f.showForecast)}>
      <span>季度预测 <small className={f.forecastRequired ? 'ds-oc-required' : 'ds-muted'}>{f.forecastRequired ? '必填' : '选填'}</small></span><span className="ds-oc-link">{f.showForecast ? '收起' : '填写'}</span>
    </button>
    {f.showForecast && <div className="ds-oc-forecast">
      <div className="ds-oc-forecast-head">
        <SbLabeledSelect label="季度" allowClear={false} disabled={disabled} value={f.quarterIndex} options={(f.quarterOptions || []).map((q, i) => ({value: i, label: q.label}))} onChange={i => on('quarter', {detail: {value: i}})} width={180} />
        <span className="ds-muted">销售填写 · 保存后作为当前预测</span>
      </div>
      <div className="ds-oc-grid">
        <SbField label="预计回款（万元）" required={Boolean(f.forecastRequired)}>
          <ForecastAmountInput value={num(f.collection)} placeholder="未填写" disabled={disabled} width="100%" onChange={value => on('forecast', {dataset: {key: 'collection'}, detail: {value: value === null ? '' : String(value)}})} />
        </SbField>
        <SbField label="预计确收（万元，含税）" required={Boolean(f.forecastRequired)}>
          <ForecastAmountInput value={num(f.recognized)} placeholder="未填写" disabled={disabled} width="100%" onChange={value => on('forecast', {dataset: {key: 'recognized'}, detail: {value: value === null ? '' : String(value)}})} />
        </SbField>
        <SbField label="回款信心度" required={Boolean(f.forecastRequired)}><SbLabeledSelect allowClear={false} disabled={disabled} value={f.collectionConfidence || ''} options={(f.confidenceOptions || []).map(row => ({value: row.value || '', label: row.label}))} onChange={value => on('forecast', {dataset: {key: 'collection_confidence'}, detail: {value: value || null}})} width="100%" /></SbField>
      </div>
      {f.forecastError && <p className="ds-oc-error" role="alert">{f.forecastError}</p>}
      <p className="ds-muted ds-oc-note">预测按本次填写金额保存，后续可更新；历史变化保留。阶段和回款信心度不改变预测金额。实际确收和回款可在修改商机时直接登记；运营保留修改功能，修改预测不会改变实际。{f.forecastRequired ? '30% 及以上阶段至少填一个季度；新填或修改金额的季度须填写回款、确收与回款信心度，金额为零请填 0。' : '当前阶段可选填；未填写与 0 不同。'}</p>
    </div>}
  </>;
}

export function QuarterCollectionActual({form, invokeOn, disabled}) {
  const f = form.data || {};
  if (!form.properties?.existing || form.properties?.visitContext || !f.collectionActualVisible) return null;
  const loaded = f.collectionActualLoaded === true;
  const locked = Boolean(disabled || f.collectionActualEditable !== true || f.collectionActualLoading || !loaded);
  const input = (key, value) => {
    if (!locked) invokeOn(form, 'collectionActualInput', {dataset: {key}, detail: {value}});
  };
  return <section className="ds-oc-collection" aria-label="季度实际回款" aria-busy={Boolean(f.collectionActualLoading)}>
    <div className="ds-oc-material-head"><h3>季度实际回款 <small className="ds-muted">选填</small></h3><div><span className="ds-muted">随商机一起保存</span><Button size="small" type="link" disabled={disabled || Boolean(f.collectionActualLoading)} onClick={() => { if (!disabled && !f.collectionActualLoading) invokeOn(form, 'retryCollectionActual'); }}>刷新状态</Button></div></div>
    <div className="ds-oc-forecast-head">
      <SbLabeledSelect label="季度" allowClear={false} disabled={disabled || Boolean(f.collectionActualLoading)} value={f.collectionActualQuarterIndex}
        options={(f.collectionActualQuarterOptions || []).map((row, index) => ({value: index, label: row.label}))} width={180}
        onChange={value => { if (!disabled && !f.collectionActualLoading) invokeOn(form, 'collectionActualQuarter', {detail: {value}}); }} />
      <span className="ds-muted">{f.collectionActualLoading ? '正在读取实际回款…' : !loaded ? '实际回款尚未读取' : ''}</span>
    </div>
    {loaded && <><div className="ds-oc-collection-summary">
      <div><span>当前实际回款</span><strong>{f.collectionActualLoading ? '读取中' : f.collectionActualCurrentText || '未登记'}</strong></div>
    </div>
    <div className="ds-oc-grid">
      <SbField label="本季度累计回款（万元）" help={f.collectionActualAmountPreview ? `${f.collectionActualAmountPreview}。填写本季度累计到账金额；为零请填 0。` : '填写本季度累计到账金额；为零请填 0。'}>
        <Input aria-label="本季度累计回款（万元）" inputMode="decimal" value={f.collectionActualAmount ?? ''} disabled={locked} placeholder="未填写"
          onChange={event => input('amount', event.target.value)} />
      </SbField>
      <SbField label="回款依据" required={Boolean(f.collectionActualDirty)} help="登记或调整累计回款时必填。">
        <Input value={f.collectionActualEvidence || ''} disabled={locked} maxLength={160} placeholder="到账凭据、收款单号等"
          onChange={event => input('evidence', event.target.value)} />
      </SbField>
      <div className="ds-oc-extra-wide"><SbField label="备注" help="选填">
        <Input.TextArea value={f.collectionActualNote || ''} disabled={locked} maxLength={1000} autoSize={{minRows: 2, maxRows: 4}}
          onChange={event => input('note', event.target.value)} />
      </SbField></div>
    </div>
    {f.collectionActualReplaceRequired && <Checkbox checked={Boolean(f.collectionActualReplaceSource)} disabled={locked}
      onChange={event => { if (!locked) invokeOn(form, 'collectionActualReplace', {detail: {value: event.target.checked}}); }}>
      以本次季度累计金额替换现有回款来源
    </Checkbox>}</>}
    {f.collectionActualError && <p className="ds-oc-error" role="alert">{f.collectionActualError}</p>}
    <p className="ds-muted ds-oc-note">保存后直接更新本季度实际回款，按累计额替换，不重复累加；未修改金额时保持原值，依据和备注随金额修改一并保存。</p>
  </section>;
}

/* 协助 FDE 多选：桌面居中对话框；草稿、搜索和确认仍由原 fde-picker 管理。 */
export function FdePicker({ picker, selected = [], disabled, invokeOn, title = '协助 FDE', hint }) {
  if (!picker) return null;
  const p = picker.data || {};
  const on = (name, payload) => { if (!disabled) return invokeOn(picker, name, payload); };
  const rows = p.rows || [];
  return <SbField label={<span>{title} <small className="ds-muted">选填</small></span>} help={hint || p.hint}>
    <div className="ds-oc-fde">
      <div className="ds-oc-tags">{selected.length ? selected.map(m => <span key={m.id} className="ds-oc-tag">{m.name}</span>) : <span className="ds-muted">暂未选择</span>}</div>
      {!disabled && <Button size="small" onClick={() => on('open')}>选择</Button>}
    </div>
    <PeopleDialog open={Boolean(p.open && !disabled)} title={title} onClose={() => on('cancel')} footer={<><span className="ds-muted">已选 {(p.draft || []).length} 人</span><Button onClick={() => on('cancel')}>取消</Button><Button type="primary" onClick={() => on('confirm')}>确认选择</Button></>}>
      <div className="ds-oc-picker">
        <SbSearch value={p.query || ''} placeholder="搜索姓名，支持多选" loading={Boolean(p.loading)} onChange={value => on('search', { detail: { value } })} />
        {!!(p.draft || []).length && <div className="ds-oc-tags">{p.draft.map(m => <button key={m.id} type="button" disabled={Boolean(m.locked)} className="ds-oc-tag is-removable" onClick={() => on('toggle', { dataset: { id: m.id } })}>{m.name}{m.locked ? ' · 只读' : ' ×'}</button>)}</div>}
        <div className="ds-oc-picker-list">
          {rows.map(r => <div key={r.id} className={`ds-oc-person ${r.excluded || r.locked ? 'is-off' : ''}`}>
            <Checkbox aria-label={r.name} checked={Boolean(r.selected)} disabled={Boolean(r.excluded || r.locked)} onChange={() => on('toggle', { dataset: { id: r.id } })} />
            <span className="ds-oc-person-main"><b>{r.name}</b><span className="ds-muted">{r.team || '未标注部门'} · {r.role === 'fde_lead' ? 'FDE 主管' : 'FDE'}</span></span>
            <span className="ds-muted">{r.locked ? '其他部门，只读' : r.excluded ? '已作普通协同人' : ''}</span>
          </div>)}
          {p.loading ? <p className="ds-muted">正在读取成员…</p> : p.error ? <p className="ds-oc-error">{p.error} <Button type="link" size="small" onClick={() => on('load')}>重试</Button></p> : !rows.length ? <p className="ds-muted">没有匹配的 FDE 成员</p> : p.offset < p.total ? <Button size="small" onClick={() => on('load', { dataset: { more: true } })}>加载更多</Button> : null}
        </div>
      </div>
    </PeopleDialog>
  </SbField>;
}

/* 商机表单：form 是原 opportunity-form 组件实例；两列布局，季度预测与更多信息折叠 */
export function OpportunityForm({ form, picker, extraPicker, invokeOn, disabled }) {
  if (!form) return <SbStatePanel state="loading" title="正在准备商机表单" />;
  const f = form.data || {}, v = f.form || {};
  const on = (name, payload) => invokeOn(form, name, payload);
  const input = (key, value) => on('input', { dataset: { key }, detail: { value } });
  const visit = Boolean(form.properties?.visitContext);
  const stages = f.stages || [];
  const stageIndex = Number(v.stageIndex);
  const lost = stages[stageIndex]?.status === 'lost';
  const partnerMode = v.partner_mode === 'unknown' ? undefined : v.partner_mode === 'partner' ? 1 : 0;
  return <div className="ds-oc-form">
    <div className="ds-oc-grid">
      <SbField label="商机名称" required error={f.error ? <button type="button" className="ds-oc-errlink" onClick={() => on('retryCatalog')}>{f.error}</button> : undefined} help={f.checking ? '正在检查名称…' : undefined}>
        <Input value={v.name || ''} maxLength={200} placeholder="填写项目名，可用部门、场景区分" disabled={disabled} onChange={e => input('name', e.target.value)} />
      </SbField>
      <SbField label="商机阶段" required>
        <SbLabeledSelect label="阶段" allowClear={false} placeholder="请选择阶段" width="100%" disabled={disabled || !stages.length} value={stageIndex >= 0 ? stageIndex : undefined} options={stages.map((s, i) => ({ value: i, label: s.text }))} onChange={i => on('stage', { detail: { value: i } })} />
        {stages.length > 0 && <ol className={`ds-oc-track ${lost ? 'is-lost' : ''}`} aria-hidden="true">{stages.map((s, i) => <li key={s.code} className={stageIndex >= i && !lost && s.status !== 'lost' ? 'is-active' : ''} />)}</ol>}
      </SbField>
      <SbField label="ACV（万元）" required>
        <Input aria-label="ACV（万元）" inputMode="decimal" value={v.amount == null ? '' : String(v.amount)} placeholder="请输入万元金额" disabled={disabled} onChange={e=>input('amount', e.target.value)} /><span className="ds-amount-preview" aria-live="polite">{f.amountPreview || '输入后显示完整元金额'}</span>
      </SbField>
      <SbField label="预计关单日期" required help={f.dateHint}>
        <SbDatePicker value={v.expected_close_date || null} allowClear={false} disabled={disabled} placeholder="选择日期" width="100%" onChange={s => on('date', { detail: { value: s } })} />
      </SbField>
      <SbField label="所属伙伴" required>
        <div className="ds-oc-partner">
          <SbSegmented size="middle" value={partnerMode} disabled={disabled} options={(f.partnerModes || ['直销', '合作伙伴']).map((label, i) => ({ value: i, label }))} onChange={i => on('partnerMode', { detail: { value: i } })} />
          {v.partner_mode === 'unknown' && <span className="ds-oc-error">请确认销售渠道</span>}
          {v.partner_mode === 'partner' && <button type="button" className="ds-oc-picker-btn" disabled={disabled} onClick={() => on('openPartners')}><span className={v.partner_name ? '' : 'ds-muted'}>{v.partner_name || '选择合作伙伴'}</span><span className="ds-oc-link">{f.showPartnerSearch ? '收起' : '选择'}</span></button>}
        </div>
      </SbField>
      {v.partner_mode === 'partner' && f.showPartnerSearch && <SbField label="伙伴目录">
        <SbSearch value={f.partnerQuery || ''} placeholder="搜索伙伴名称" loading={Boolean(f.partnersLoading)} disabled={disabled} onChange={value => on('partnerSearch', { detail: { value } })} />
        <div className="ds-oc-results">
          {(f.partners || []).map(pr => <SbListRow key={pr.id} name={pr.name} selected={pr.id === v.partner_id} onClick={() => on('choosePartner', { dataset: { id: pr.id } })} />)}
          {f.partnersLoading ? <p className="ds-muted">正在加载…</p> : f.partnersError ? <p className="ds-oc-error">{f.partnersError} <Button type="link" size="small" onClick={() => on('loadPartners')}>重试</Button></p> : !(f.partners || []).length ? <p className="ds-muted">没有找到伙伴，请联系运营维护目录。</p> : (f.partners || []).length < f.partnerTotal ? <Button size="small" onClick={() => on('loadPartners', { dataset: { more: true } })}>加载更多</Button> : null}
        </div>
      </SbField>}
      <FdePicker picker={picker} selected={visit ? v.visit_fde_members : v.fde_members} disabled={disabled || !visit && form.properties?.existing?.capabilities?.can_manage_fde_members === false} invokeOn={invokeOn} hint={visit ? '本次拜访出席人员；项目长期名单在更多信息中维护' : '商机协助人员，可搜索并选择多位 FDE'} />
    </div>

    <OpportunityMaterials form={form} invokeOn={invokeOn} disabled={disabled} />
    <QuarterForecast form={form} invokeOn={invokeOn} disabled={disabled} />
    <OpportunityRecognizedActual form={form} invokeOn={invokeOn} disabled={disabled} />
    <QuarterCollectionActual form={form} invokeOn={invokeOn} disabled={disabled} />

    <button type="button" className="ds-oc-fold" onClick={() => on('toggleMore')} aria-expanded={Boolean(f.showMore)}>
      <span>更多信息 <small className="ds-muted">选填</small></span><span className="ds-oc-link">{f.showMore ? '收起' : '补充'}</span>
    </button>
    {f.showMore && <OpportunityExtras form={form} invokeOn={invokeOn} disabled={disabled} rosterControl={visit ? <FdePicker picker={extraPicker} selected={v.extra_roster || []} disabled={disabled || !f.extraCanManageFde} invokeOn={invokeOn} title="长期协助 FDE" hint="维护项目协作名单；本次拜访出席人员在上方选择" /> : null} />}
  </div>;
}

// 需要的子组件：客户确定且加载完成后模板才会放表单；FDE 选择器挂在表单下面，selected 跟随表单数据
OpportunityCreate.subcomponents = (page, d) => {
  const form = page.selectComponent('#opportunityForm');
  const visit = Boolean(form?.properties?.visitContext);
  const v = form?.data?.form || {};
  return [
    { selector: '#opportunityForm', required: Boolean(d.customerName && !d.loading), props: { customerId: d.customerId, existing: d.existing, disabled: draftLocked(d) } },
    { selector: '#opportunityFdePicker', parent: '#opportunityForm', required: false, props: { selected: (visit ? v.visit_fde_members : v.fde_members) || [], disabled: draftLocked(d) } },
  ];
};

export default function OpportunityCreate({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const locked = draftLocked(d);
  const changeDraft = (name, payload) => { if (!locked) return call(name, payload); };
  const changeForm = (...args) => { if (!locked) return invokeOn(...args); };
  const awaitingResult = d.saveStatus === 'unknown' || d.saveStatus === 'checking';
  const saved = d.saveStatus === 'succeeded';
  const checking = d.saveStatus === 'checking';
  const recoveryBusy = Boolean(d.busy || d.loading);
  const cancelling = checking && d.saveRecoveryAction === 'cancel';
  const saving = d.saveStatus === 'saving' || Boolean(d.busy && !awaitingResult && !saved);
  const recoveryMessage = d.saveRecoveryMessage || (saved
    ? '已确认原提交保存成功。请打开已保存的商机查看结果，继续修改请在该商机中进行。'
    : '暂未确认本次保存结果，原请求可能已保存。当前草稿已锁定，请先查询保存结果，避免重复创建。');
  const primary = awaitingResult ? {
    label: '查询保存结果', loading: checking, loadingLabel: cancelling ? '正在结束提交' : '正在查询', disabled: checking || recoveryBusy,
    onClick: () => { if (!checking && !recoveryBusy) return call('checkSaveResult'); },
  } : saved ? {
    label: '查看已保存商机', disabled: !d.saveRecoveryOpportunityId || Boolean(d.busy),
    onClick: () => { if (d.saveRecoveryOpportunityId && !d.busy) return call('openSavedOpportunity'); },
  } : {
    label: '确认保存商机', loading: saving, loadingLabel: '正在保存', disabled: locked || Boolean(d.loading) || (!page.opportunityId && !d.createAccessAllowed),
    onClick: () => { if (!locked && !d.loading && (page.opportunityId || d.createAccessAllowed)) return call('submit'); },
  };
  const form = d.customerName && !d.loading ? select('#opportunityForm') : null;
  const picker = form ? select('#opportunityFdePicker', '#opportunityForm') : null;
  const editing = Boolean(d.existing);
  const step = !d.customerId ? 0 : 1;
  const customerQuery = String(d.query || '').trim();
  const customerLoading = customerQuery ? d.customerSearchLoading : d.webCustomerLoading;
  const customerError = customerQuery ? d.customerSearchError : d.webCustomerError;
  const customerDone = customerQuery ? d.customerSearchDone : d.webCustomerDone;
  return <section className="ds-ocreate" aria-label={editing ? '修改商机' : '新增商机'}>
    <aside className="ds-panel ds-oc-side">
      <Steps direction="vertical" size="small" current={step} items={[{ title: '选择所属客户', description: '商机归档到所选客户名下' }, { title: '完善商机信息', description: '名称、阶段、金额、关单日期必填' }]} />
      {d.customerId ? <div className="ds-oc-chosen">
        <span className="ds-oc-mark" aria-hidden="true">客</span>
        <div className="ds-oc-chosen-main"><span className="ds-muted">关联客户</span><b>{d.customerName || (d.loading ? '正在加载…' : '客户信息未取到')}</b>{!page.opportunityId && !d.loading && !d.customerName && <><Button size="small" disabled={locked} onClick={() => call('load')}>重新核对</Button><Button size="small" disabled={locked} onClick={() => call('changeCustomer')}>重新选择客户</Button></>}</div>
      </div> : <>
        <SbSearch value={d.query || ''} placeholder="搜索客户名称" disabled={locked} onChange={value => changeDraft('inputCustomer', { detail: { value } })} onSearch={value => changeDraft('webSearchCustomers', { detail: { value } })} />
        <div className="ds-oc-results" aria-busy={Boolean(customerLoading)}>
          {(d.customers || []).map(c => <SbListRow key={c.id} name={c.name} summary={c.team_name || c.team || ''} onClick={locked ? undefined : () => changeDraft('selectCustomer', { dataset: { id: c.id } })} />)}
          {customerLoading && <p className="ds-muted" role="status">正在{customerQuery ? '搜索' : '加载'}客户…</p>}
          {customerError && <div className="ds-oc-search-error" role="alert"><p>{customerError}</p><Button size="small" disabled={locked || Boolean(customerLoading)} onClick={() => { if (!customerLoading) changeDraft('webSearchCustomers'); }}>重新搜索</Button></div>}
          {!customerLoading && !customerError && customerDone && !(d.customers || []).length && <p className="ds-muted" role="status">{customerQuery ? `未找到“${customerQuery}”相关客户，可调整关键词重试` : '暂无可创建商机的客户，公海客户请先认领'}</p>}
        </div>
        {!customerQuery && !customerLoading && !customerError && Boolean(d.customers?.length) && <p className="ds-muted ds-oc-note">仅显示已认领且允许创建商机的客户，输入名称可搜索更多。</p>}
      </>}
    </aside>
    <section className="ds-panel ds-oc-main">
      <div className="ds-oc-head"><h2>{editing ? '修改商机' : '新增商机'}</h2>{d.customerName && <span className="ds-muted">{d.customerName}</span>}{!page.opportunityId && !editing && can('opportunity.read') && <Button className="ds-oc-history" disabled={Boolean(d.busy)} onClick={() => open.route('pages/opportunity-history/index')}>创建历史</Button>}</div>
      <div className="ds-oc-body">
        {(awaitingResult || saved) && <div className={`ds-oc-recovery${saved ? ' is-saved' : ''}`} role="status" aria-live="polite">
          <strong>{saved ? '商机已保存' : cancelling ? '正在结束未完成的提交' : checking ? '正在查询保存结果' : '保存结果待确认'}</strong>
          <p>{recoveryMessage}</p>
          {d.saveStatus === 'unknown' && <div className="ds-oc-recovery-actions">
            {d.canRetrySave && <Button disabled={recoveryBusy} onClick={() => { if (!recoveryBusy) return call('retrySave'); }}>按原内容重试</Button>}
            <Button disabled={recoveryBusy} onClick={() => { if (!recoveryBusy) return call('cancelSave'); }}>结束未完成的提交</Button>
          </div>}
        </div>}
        {d.error && !awaitingResult && !saved && <p className="ds-oc-error ds-oc-banner" role="alert">{d.error}{!page.opportunityId && d.customerName && !d.createAccessAllowed && <Button size="small" disabled={locked} onClick={() => call('recheckCreateAccess')}>重新核对创建资格</Button>}</p>}
        {d.saveStatus === 'rejected' && !d.error && d.saveRecoveryMessage && <p className="ds-oc-notice" role="status">{d.saveRecoveryMessage}</p>}
        {!d.customerId ? (awaitingResult || saved ? null : <SbStatePanel state="empty" title="先在左侧选择客户" description="选好客户后再填写商机信息。" />)
          : d.loading ? <SbStatePanel state="loading" title="正在加载商机信息" />
            : d.customerName ? <OpportunityForm form={form} picker={picker} invokeOn={changeForm} disabled={locked} /> : null}
      </div>
      {(awaitingResult || saved || d.customerName && !d.loading) && <div className="ds-oc-bar"><SbBottomBar reason={cancelling ? '正在等待服务器确认，请稍候' : awaitingResult ? '查询仅核对原提交，不会再次保存' : saved ? '原提交已完成，请查看已保存商机' : '请确认商机名称、阶段、金额和预计关单日期'} primary={primary} /></div>}
    </section>
  </section>;
}
