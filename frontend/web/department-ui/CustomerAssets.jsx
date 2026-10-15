import React from 'react';
import {actualPeriodLabel, actualSourceLabel} from './pc/finance-display.js';
import {Button} from 'antd';
import {SbMetricStrip, SbSegmented, SbTable} from '@shandiant/ui-react';
import './customer-assets.css';

// 客户资产只读取服务端当前采用的实际；运营登记和更正统一在管理后台完成。
export function supportsCustomerAssets(page, data = {}) {
  return Boolean(page && page.route === 'pages/customer-assets/index' && !data.opportunityId);
}

export default function CustomerAssets({ page, data: d, invoke }) {
  const call = (name, payload = {}) => invoke(name, payload);
  const kindLabel = d.kind === 'collection' ? '回款' : '确收';
  const periodLabel = d.period === 'year' ? '本年' : '历年累计';
  const byCustomer = !d.customerId;
  const items = d.items || [];
  const state = d.error ? 'error' : d.loading && !items.length ? 'loading' : !items.length ? 'empty' : 'normal';
  const entryCols = [
    { title: '归属期间', key: 'date', width: 130, render: (_, r) => actualPeriodLabel(r) },
    { title: '金额（万元）', key: 'amount', width: 140, align: 'right', render: (_, r) => <b className="ds-ca-num">{r.amountText}</b> },
    { title: '关联商机', key: 'opp', render: (_, r) => r.opportunity_id ? <Button type="link" size="small" onClick={() => call('openProfile', { dataset: { id: r.opportunity_id } })}>{r.opportunity_name}</Button> : <span className="ds-muted">暂未关联商机</span> },
    { title: '来源编号', key: 'ref', width: 200, render: (_, r) => <span className="ds-muted">{r.source_ref}</span> },
    { title: '采用来源 / 状态', key: 'by', width: 180, render: (_, r) => <span className="ds-muted">{actualSourceLabel(r)}</span> },
  ];
  const customerCols = [
    { title: '客户', key: 'name', render: (_, r) => <div className="ds-ca-two"><b>{r.customer_name}</b><span className="ds-muted">{r.team_name || '未分组'} · {r.owner_name || '待分配'}</span></div> },
    { title: `${periodLabel}${kindLabel}（万元）`, key: 'total', width: 180, align: 'right', render: (_, r) => <b className="ds-ca-num">{r.totalText}</b> },
    { title: '记录', key: 'count', width: 100, render: (_, r) => <span className="ds-muted">{r.entry_count} 条</span> },
  ];
  return <section className="ds-cassets" aria-label={byCustomer ? '客户资产明细' : `${d.customerName} 的经营实绩`}>
    <div className="ds-panel ds-ca-summary">
      <SbMetricStrip columns={1} items={[{ key: 'total', label: `${periodLabel}${kindLabel}${d.kind === 'recognized' ? '（不含税）' : ''} · 万元`, value: d.loading && !items.length ? '…' : d.totalText === '—' ? null : d.totalText, note: `当前采用金额 · 查询日期 ${d.asOf || '—'}`, missingText: '未登记' }]}
        extra={<SbSegmented value={d.kind} options={[{ value: 'recognized', label: '确收' }, { value: 'collection', label: '回款' }]} onChange={kind => call('changeKind', { dataset: { kind } })} />}
        periods={[{ value: 'year', label: '本年' }, { value: 'all', label: '历年累计' }]} period={d.period} onPeriodChange={period => call('changePeriod', { dataset: { period } })}
        caliber="按当前采用的有效实绩汇总，ACV 和销售预测不计入。季度确认显示归属季度，逐笔登记保留真实发生日期；旧来源不重复相加。查询日期不代表到账日期；季度金额不能细分到日。实际由运营在管理后台登记或更正。" />
    </div>
    {!!d.financialHints?.length && <div className="ds-panel ds-ca-financial-hints">{d.financialHints.map((hint, i) => <p key={i}>{hint}</p>)}</div>}
    <div className="ds-panel ds-ca-list">
      <div className="ds-ca-tools">
        <h2>{byCustomer ? '贡献客户' : `${d.customerName} · ${kindLabel}明细`}<small className="ds-muted">当前采用的有效实绩</small></h2>
        {!byCustomer && !d.readOnly && <div className="ds-ca-actions">{d.customerId && <Button type="link" size="small" onClick={() => call('openProfile')}>客户档案</Button>}{d.originCustomerId ? null : <Button type="link" size="small" onClick={() => call('backToCustomers')}>返回客户汇总</Button>}</div>}
      </div>
      <div className="ds-ca-body">
        <SbTable rowKey={byCustomer ? 'customer_id' : 'id'} density="compact" columns={byCustomer ? customerCols : entryCols} rows={items} state={state} emptyTitle={state === 'error' ? '数据加载失败' : `暂无${kindLabel}记录`} emptyDescription={state === 'error' ? d.error : '实际由运营在管理后台登记或更正，当前采用金额会显示在这里。'} onRetry={() => call('retry')}
          onRowClick={byCustomer ? r => call('openCustomer', { dataset: { id: r.customer_id } }) : undefined}
          expandable={!byCustomer ? { columnWidth: 28, rowExpandable: r => Boolean(r.note), expandedRowRender: r => <p className="ds-ca-note">{r.note}</p> } : undefined} />
        {state === 'normal' && (d.loading ? <p className="ds-muted ds-ca-more">正在加载…</p> : d.hasMore && <div className="ds-ca-more"><Button size="small" onClick={() => call('more')}>加载更多</Button></div>)}
      </div>
    </div>
  </section>;
}
