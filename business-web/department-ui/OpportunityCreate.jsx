import React from 'react';
import { Button, Checkbox, Input, Steps } from 'antd';
import dayjs from 'dayjs';
import { SbAmountInput, SbBottomBar, SbDatePicker, SbField, SbLabeledSelect, SbListRow, SbSearch, SbSegmented, SbSheet, SbStatePanel } from '@shandiant/ui-react';
import './opportunity-create.css';
// 金额控件收数字，页面里的字符串先转一下
const toAmount = v => (v === '' || v == null || Number.isNaN(Number(v))) ? null : Number(v);

// 新建／修改商机（原 pages/opportunity-create/index）。页面本身只管选客户和提交；表单的数据与校验都在原子组件 opportunity-form 里，
// 这里通过 select('#opportunityForm') 拿到它的实例，读 form.data、用 invokeOn 调它的方法，协助 FDE 选择器是它的子组件 #opportunityFdePicker。
export function supportsOpportunityCreate(page) {
  return Boolean(page && page.route === 'pages/opportunity-create/index');
}
const day = v => (v ? dayjs(v) : null);
const num = v => (v === '' || v === null || v === undefined ? null : Number(v));

/* 协助 FDE 多选：原 fde-picker 子组件的抽屉版。picker 是组件实例，selected 是当前已选（来自表单数据） */
export function FdePicker({ picker, selected = [], disabled, invokeOn, title = '协助 FDE', hint }) {
  if (!picker) return null;
  const p = picker.data || {};
  const on = (name, payload) => invokeOn(picker, name, payload);
  const rows = p.rows || [];
  return <SbField label={<span>{title} <small className="ds-muted">选填</small></span>} help={hint || p.hint}>
    <div className="ds-oc-fde">
      <div className="ds-oc-tags">{selected.length ? selected.map(m => <span key={m.id} className="ds-oc-tag">{m.name}</span>) : <span className="ds-muted">暂未选择</span>}</div>
      {!disabled && <Button size="small" onClick={() => on('open')}>选择</Button>}
    </div>
    <SbSheet open={Boolean(p.open)} title={title} onClose={() => on('cancel')} footer={<><span className="ds-muted">已选 {(p.draft || []).length} 人</span><Button onClick={() => on('cancel')}>取消</Button><Button type="primary" onClick={() => on('confirm')}>确认选择</Button></>}>
      <div className="ds-oc-picker">
        <SbSearch value={p.query || ''} placeholder="搜索姓名，支持多选" loading={Boolean(p.loading)} onChange={value => on('search', { detail: { value } })} />
        {!!(p.draft || []).length && <div className="ds-oc-tags">{p.draft.map(m => <button key={m.id} type="button" className="ds-oc-tag is-removable" onClick={() => on('toggle', { dataset: { id: m.id } })}>{m.name}{m.locked ? ' · 只读' : ' ×'}</button>)}</div>}
        <div className="ds-oc-picker-list">
          {rows.map(r => <label key={r.id} className={`ds-oc-person ${r.excluded || r.locked ? 'is-off' : ''}`}>
            <Checkbox checked={Boolean(r.selected)} disabled={Boolean(r.excluded || r.locked)} onChange={() => on('toggle', { dataset: { id: r.id } })} />
            <span className="ds-oc-person-main"><b>{r.name}</b><span className="ds-muted">{r.team || '未标注部门'} · {r.role === 'fde_lead' ? 'FDE 主管' : 'FDE'}</span></span>
            <span className="ds-muted">{r.locked ? '其他部门，只读' : r.excluded ? '已作普通协同人' : ''}</span>
          </label>)}
          {p.loading ? <p className="ds-muted">正在读取成员…</p> : p.error ? <p className="ds-oc-error">{p.error} <Button type="link" size="small" onClick={() => on('load')}>重试</Button></p> : !rows.length ? <p className="ds-muted">没有匹配的 FDE 成员</p> : p.offset < p.total ? <Button size="small" onClick={() => on('load', { dataset: { more: true } })}>加载更多</Button> : null}
        </div>
      </div>
    </SbSheet>
  </SbField>;
}

/* 商机表单：form 是原 opportunity-form 组件实例；两列布局，季度预测与更多信息折叠 */
export function OpportunityForm({ form, picker, invokeOn, disabled }) {
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
      {f.requiresOwnerTeam && <SbField label="所属团队" required error={f.ownerTeamsError}><SbLabeledSelect label="团队" allowClear={false} value={v.owner_team_id ? f.ownerTeamIndex : undefined} options={(f.ownerTeams || []).map((t,i)=>({value:i,label:t.name}))} disabled={disabled || f.ownerTeamsLoading} onChange={value=>on('changeOwnerTeam',{detail:{value}})}/>{f.ownerTeamsError && <Button type="link" onClick={()=>on('loadOwnerTeams')}>重试</Button>}</SbField>}
      <SbField label="商机名称" required error={f.error ? <button type="button" className="ds-oc-errlink" onClick={() => on('retryCatalog')}>{f.error}</button> : undefined} help={f.checking ? '正在检查名称…' : undefined}>
        <Input value={v.name || ''} maxLength={200} placeholder="填写项目名，可用部门、场景区分" disabled={disabled} onChange={e => input('name', e.target.value)} />
      </SbField>
      <SbField label="商机阶段" required>
        <SbLabeledSelect label="阶段" allowClear={false} placeholder="请选择阶段" width="100%" disabled={disabled || !stages.length} value={stageIndex >= 0 ? stageIndex : undefined} options={stages.map((s, i) => ({ value: i, label: s.text }))} onChange={i => on('stage', { detail: { value: i } })} />
        {stages.length > 0 && <ol className={`ds-oc-track ${lost ? 'is-lost' : ''}`} aria-hidden="true">{stages.map((s, i) => <li key={s.code} className={stageIndex >= i && !lost && s.status !== 'lost' ? 'is-active' : ''} />)}</ol>}
      </SbField>
      <SbField label="ACV（万元）" required>
        <SbAmountInput value={toAmount(num(v.amount))} placeholder="请输入金额" disabled={disabled} width="100%" onChange={value => input('amount', value === null ? '' : String(value))} />
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
      <FdePicker picker={picker} selected={visit ? v.visit_fde_members : v.fde_members} disabled={disabled} invokeOn={invokeOn} hint={visit ? '仅选择本次拜访参与的 FDE；保存后追加到商机协助名单' : '商机协助人员，可搜索并选择多位 FDE'} />
    </div>

    <button type="button" className="ds-oc-fold" onClick={() => on('toggleForecast')} aria-expanded={Boolean(f.showForecast)}>
      <span>季度回款与确收 <small className={f.forecastRequired ? 'ds-oc-required' : 'ds-muted'}>{f.forecastRequired ? '必填' : '选填'}</small></span><span className="ds-oc-link">{f.showForecast ? '收起' : '填写'}</span>
    </button>
    {f.showForecast && <div className="ds-oc-forecast">
      <div className="ds-oc-forecast-head">
        <SbLabeledSelect label="季度" allowClear={false} value={f.quarterIndex} options={(f.quarterOptions || []).map((q, i) => ({ value: i, label: q.label }))} onChange={i => on('quarter', { detail: { value: i } })} width={180} />
        <span className="ds-muted">阶段概率 {f.forecastRate}</span>
      </div>
      <div className="ds-oc-grid">
        <SbField label="回款（万元）" required={Boolean(f.forecastRequired)}>
          <SbAmountInput value={toAmount(num(f.collection))} placeholder="未填写" disabled={disabled} width="100%" onChange={value => on('forecast', { dataset: { key: 'collection' }, detail: { value: value === null ? '' : String(value) } })} />
        </SbField>
        <SbField label="确收（万元）" required={Boolean(f.forecastRequired)}>
          <SbAmountInput value={toAmount(num(f.recognized))} placeholder="未填写" disabled={disabled} width="100%" onChange={value => on('forecast', { dataset: { key: 'recognized' }, detail: { value: value === null ? '' : String(value) } })} />
        </SbField>
        <div className="ds-oc-calc"><span className="ds-muted">预测回款（万元）</span><b>{f.predictedCollection}</b></div>
        <div className="ds-oc-calc"><span className="ds-muted">预测确收（万元）</span><b>{f.predictedRecognized}</b></div>
      </div>
      {f.forecastError && <p className="ds-oc-error" role="alert">{f.forecastError}</p>}
      <p className="ds-muted ds-oc-note">预测金额＝填写金额 × 阶段百分比。{f.forecastRequired ? '30% 及以上阶段至少填一个季度；已填季度须同时填回款和确收，金额为零请填 0。' : '当前阶段可选填；未填写与 0 不同。'}填写的是季度计划，实际以已确认实绩为准。</p>
    </div>}

    <button type="button" className="ds-oc-fold" onClick={() => on('toggleMore')} aria-expanded={Boolean(f.showMore)}>
      <span>更多信息 <small className="ds-muted">选填</small></span><span className="ds-oc-link">{f.showMore ? '收起' : '补充'}</span>
    </button>
    {f.showMore && <div className="ds-oc-grid"><SbField label="产品线">
      <Input value={v.product_line || ''} maxLength={200} placeholder="填写产品线" disabled={disabled} onChange={e => input('product_line', e.target.value)} />
    </SbField></div>}
  </div>;
}

// 需要的子组件：客户确定且加载完成后模板才会放表单；FDE 选择器挂在表单下面，selected 跟随表单数据
OpportunityCreate.subcomponents = (page, d) => {
  const form = page.selectComponent('#opportunityForm');
  const visit = Boolean(form?.properties?.visitContext);
  const v = form?.data?.form || {};
  return [
    { selector: '#opportunityForm', required: Boolean(d.customerName && !d.loading), props: { customerId: d.customerId, existing: d.existing, disabled: Boolean(d.busy) } },
    { selector: '#opportunityFdePicker', parent: '#opportunityForm', required: false, props: { selected: (visit ? v.visit_fde_members : v.fde_members) || [], disabled: Boolean(d.busy) } },
  ];
};

export default function OpportunityCreate({ page, data: d, invoke, invokeOn, select }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const form = d.customerName && !d.loading ? select('#opportunityForm') : null;
  const picker = form ? select('#opportunityFdePicker', '#opportunityForm') : null;
  const editing = Boolean(d.existing);
  const step = !d.customerId ? 0 : 1;
  return <section className="ds-ocreate" aria-label={editing ? '修改商机' : '新增商机'}>
    <aside className="ds-panel ds-oc-side">
      <Steps direction="vertical" size="small" current={step} items={[{ title: '选择所属客户', description: '商机归档到所选客户名下' }, { title: '完善商机信息', description: '名称、阶段、金额、关单日期必填' }]} />
      {d.customerId ? <div className="ds-oc-chosen">
        <span className="ds-oc-mark" aria-hidden="true">客</span>
        <div className="ds-oc-chosen-main"><span className="ds-muted">关联客户</span><b>{d.customerName || (d.loading ? '正在加载…' : '客户信息未取到')}</b></div>
      </div> : <>
        <SbSearch value={d.query || ''} placeholder="搜索客户名称" onChange={value => call('inputCustomer', { detail: { value } })} />
        <div className="ds-oc-results">
          {(d.customers || []).map(c => <SbListRow key={c.id} name={c.name} summary={c.team_name || c.team || ''} onClick={() => call('selectCustomer', { dataset: { id: c.id } })} />)}
          {d.query && !(d.customers || []).length && <p className="ds-muted">输入客户名称后，从搜索结果中选择</p>}
        </div>
      </>}
    </aside>
    <section className="ds-panel ds-oc-main">
      <div className="ds-oc-head"><h2>{editing ? '修改商机' : '新增商机'}</h2>{d.customerName && <span className="ds-muted">{d.customerName}</span>}</div>
      <div className="ds-oc-body">
        {d.error && <p className="ds-oc-error ds-oc-banner" role="alert">{d.error}</p>}
        {!d.customerId ? <SbStatePanel state="empty" title="先在左侧选择客户" description="选好客户后再填写商机信息。" />
          : d.loading ? <SbStatePanel state="loading" title="正在加载商机信息" />
            : d.customerName ? <OpportunityForm form={form} picker={picker} invokeOn={invokeOn} disabled={Boolean(d.busy)} /> : null}
      </div>
      {d.customerName && !d.loading && <div className="ds-oc-bar"><SbBottomBar reason="请确认商机名称、阶段、金额和预计关单日期" primary={{ label: '确认保存商机', loading: Boolean(d.busy), loadingLabel: '正在保存', onClick: () => call('submit') }} /></div>}
    </section>
  </section>;
}
