import React from 'react';
import { Button, Input } from 'antd';
import dayjs from 'dayjs';
import { SbBottomBar, SbDatePicker, SbField, SbListRow, SbMetricStrip, SbSegmented, SbSheet, SbStatePanel, SbTable, SbTextarea } from '@shandiant/ui-react';
import './customer-assets.css';

// 客户资产明细（原 pages/customer-assets/index 不带 opportunity_id 的形态）：经营总览的贡献客户，或某个客户的确收、回款明细与登记。
export function supportsCustomerAssets(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-assets/index' && !data.opportunityId);
}

export default function CustomerAssets({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const kindLabel = d.kind === 'collection' ? '回款' : '确收';
  const periodLabel = d.period === 'year' ? '本年' : '历年累计';
  const byCustomer = !d.customerId;
  const historical = d.basis === 'historical';
  const items = d.items || [];
  const state = d.error ? 'error' : d.loading && !items.length ? 'loading' : !items.length ? 'empty' : 'normal';
  const entryCols = [
    { title: '发生日期', key: 'date', width: 130, render: (_, r) => r.period_label || r.occurred_on || '未填写' },
    { title: '金额（万元）', key: 'amount', width: 140, align: 'right', render: (_, r) => <b className="ds-ca-num">{r.amountText}</b> },
    { title: '关联商机', key: 'opp', render: (_, r) => r.opportunity_id ? <Button type="link" size="small" onClick={() => call('openProfile', { dataset: { id: r.opportunity_id } })}>{r.opportunity_name}</Button> : <span className="ds-muted">暂未关联商机</span> },
    { title: '来源编号', key: 'ref', width: 200, render: (_, r) => <span className="ds-muted">{r.source_ref}</span> },
    { title: '确认', key: 'by', width: 140, render: (_, r) => <span className="ds-muted">{historical ? (r.taxBasisText || r.tax_basis || '历史原始口径') : `${r.confirmed_by || '管理人员'} 已确认`}</span> },
  ];
  const customerCols = [
    { title: '客户', key: 'name', render: (_, r) => <div className="ds-ca-two"><b>{r.customer_name}</b><span className="ds-muted">{r.team_name || '未分组'} · {r.owner_name || '待分配'}</span></div> },
    { title: `${periodLabel}${kindLabel}（万元）`, key: 'total', width: 180, align: 'right', render: (_, r) => <b className="ds-ca-num">{r.totalText}</b> },
    { title: '记录', key: 'count', width: 100, render: (_, r) => <span className="ds-muted">{r.entry_count} 条</span> },
  ];
  return <section className="ds-cassets" aria-label={byCustomer ? '客户资产明细' : `${d.customerName} 的经营实绩`}>
    <div className="ds-panel ds-ca-summary">
      <SbMetricStrip columns={1} items={[{ key: 'total', label: `${periodLabel}${kindLabel} · 万元`, value: d.loading && !items.length ? '…' : d.totalText === '—' ? null : d.totalText, note: `${d.summary?.entry_count || 0} 条已确认记录 · 截至 ${d.asOf}${d.summary?.first_date && d.period === 'all' ? ` · 记录始于 ${d.summary.first_date}` : ''}`, missingText: '—' }]}
        extra={<SbSegmented value={d.kind} options={[{ value: 'recognized', label: '确收' }, { value: 'collection', label: '回款' }]} onChange={kind => call('changeKind', { dataset: { kind } })} />}
        periods={[{ value: 'year', label: '本年' }, { value: 'all', label: '历年累计' }]} period={d.period} onPeriodChange={period => call('changePeriod', { dataset: { period } })}
        caliber={historical ? '历史原始数据只读展示，保留来源与税务口径，不转换或覆盖财务登记。' : '只展示经人工确认的确收、回款记录，ACV 和季度预测不计入。本年截至今天，历年累计包含本年。'} />
    </div>
    <div className="ds-panel ds-ca-list">
      <div className="ds-ca-tools">
        <h2>{byCustomer ? '贡献客户' : `${d.customerName} · ${kindLabel}明细`}<small className="ds-muted">{byCustomer ? '按已登记记录统计' : historical ? '历史原始口径 · 只读' : '仅人工确认的记录'}</small></h2>
        {!byCustomer && !d.readOnly && <div className="ds-ca-actions">{d.customerId && <Button type="link" size="small" onClick={() => call('openProfile')}>客户档案</Button>}{d.originCustomerId ? null : <Button type="link" size="small" onClick={() => call('backToCustomers')}>返回客户汇总</Button>}{!historical && d.canManage && <Button type="primary" onClick={() => call('openForm')}>登记{kindLabel}</Button>}</div>}
      </div>
      <div className="ds-ca-body">
        <SbTable rowKey={byCustomer ? 'customer_id' : 'id'} density="compact" columns={byCustomer ? customerCols : entryCols} rows={items} state={state} emptyTitle={state === 'error' ? '数据加载失败' : `暂无${kindLabel}记录`} emptyDescription={state === 'error' ? d.error : (d.customerId && d.canManage ? '确认已有实绩后，可从上方登记。' : '已确认的经营实绩会显示在这里。')} onRetry={() => call('retry')}
          onRowClick={byCustomer ? r => call('openCustomer', { dataset: { id: r.customer_id } }) : undefined}
          actions={!historical && !byCustomer && d.canVoidActual && !d.readOnly ? r => r.can_void === true ? <Button type="link" size="small" danger onClick={() => call('voidEntry', { dataset: { id: r.id } })}>作废</Button> : null : undefined} actionsWidth={72}
          expandable={!byCustomer ? { columnWidth: 28, rowExpandable: r => Boolean(r.note), expandedRowRender: r => <p className="ds-ca-note">{r.note}</p> } : undefined} />
        {state === 'normal' && (d.loading ? <p className="ds-muted ds-ca-more">正在加载…</p> : d.hasMore && <div className="ds-ca-more"><Button size="small" onClick={() => call('more')}>加载更多</Button></div>)}
      </div>
    </div>
    <SbSheet open={Boolean(d.formOpen)} title={`登记${kindLabel} · ${d.customerName}`} onClose={() => call('closeForm')} footer={<><Button disabled={d.saving} onClick={() => call('closeForm')}>取消</Button><Button type="primary" loading={d.saving} disabled={d.saving || d.formTargetLoading || d.formSelectionRequired} onClick={() => call('submit')}>确认登记</Button></>}>
      <div className="ds-ca-form">
        <SbField label={<span>关联商机 <small className="ds-muted">选填</small></span>} help={d.formTargetLoading ? '正在确认原关联商机…' : `已选：${d.formOpportunity?.name || '暂不关联商机'}`}>
          <Input placeholder="搜索商机名称" value={d.formQuery || ''} disabled={d.saving} onChange={e => call('searchFormOpportunity', { detail: { value: e.target.value } })} />
          <div className="ds-ca-choices">
            <SbListRow name="暂不关联商机" selected={!d.formOpportunity?.id} onClick={() => call('selectFormOpportunity', { dataset: { id: '' } })} />
            {(d.formChoices || []).map(c => <SbListRow key={c.id} name={c.name} selected={d.formOpportunity?.id === c.id} onClick={() => call('selectFormOpportunity', { dataset: { id: c.id } })} />)}
            {d.formChoiceState?.loading && <p className="ds-muted ds-ca-hint">正在读取…</p>}
            {d.formChoiceState?.error && <p className="ds-ca-error">{d.formChoiceState.error} <Button type="link" size="small" onClick={() => call('retryFormChoices')}>重试</Button></p>}
            {d.formChoiceState?.hasMore && <Button type="link" size="small" onClick={() => call('moreFormChoices')}>加载更多</Button>}
          </div>
        </SbField>
        <div className="ds-ca-form-grid">
          <SbField label="金额（万元）" required><Input value={d.amount || ''} disabled={d.saving} inputMode="decimal" placeholder="请输入已确认金额" onChange={e => call('input', { dataset: { field: 'amount' }, detail: { value: e.target.value } })} /></SbField>
          <SbField label={`${kindLabel}日期`} required><SbDatePicker value={d.occurredOn || null} allowClear={false} disabledDate={c => c && c.isAfter(dayjs(d.asOf), 'day')} onChange={s => call('pickDate', { detail: { value: s } })} /></SbField>
          <SbField label="来源编号" required><Input value={d.sourceRef || ''} disabled={d.saving} maxLength={160} placeholder="如：财务台账2026-09·第12行" onChange={e => call('input', { dataset: { field: 'sourceRef' }, detail: { value: e.target.value } })} /></SbField>
        </div>
        <SbField label={<span>备注 <small className="ds-muted">选填</small></span>}><SbTextarea value={d.note || ''} disabled={d.saving} maxLength={500} onChange={v => call('input', { dataset: { field: 'note' }, detail: { value: v } })} /></SbField>
        {d.formError && <p className="ds-ca-error" role="alert">{d.formError}</p>}
        <p className="ds-muted ds-ca-hint">确认后计入客户资产；录错可作废后重新登记。</p>
      </div>
    </SbSheet>
  </section>;
}
