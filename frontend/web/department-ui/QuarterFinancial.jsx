import React from 'react';
import {SbMetricStrip, SbTable} from '@shandiant/ui-react';

// Already-resolved periods come from the shared financial-overview reader.
// Do not sum confirmations, fall back to entries/history, or assign daily dates.
export default function QuarterFinancial({data: d}) {
  const periods = d.quarterFinancial || {};
  const status = p => p?.statusLabel || ({missing: '未登记', withdrawn: '已撤回', partial: '部分登记', conflict: '待核对'}[p?.status]) || (p?.amount == null ? '未登记' : '已确认');
  const columns = [
    {title: '登记项', key: 'material', render: (_, row) => row.materialLabel || (row.material_line_id ? '物料名称未提供' : '商机回款')},
    {title: '确认金额', key: 'amount', render: (_, row) => row.amountText ?? '—'},
    {title: '状态', key: 'status', render: (_, row) => row.statusLabel || (row.status === 'withdrawn' ? '已撤回' : '已确认')},
    {title: '确认人 / 依据', key: 'source', render: (_, row) => <span>{row.confirmed_by_name || '未标注'} · {row.source_ref || '未标注'}</span>},
  ];
  return <>
    <SbMetricStrip columns={2} items={['collection', 'recognized'].map(kind => {
      const p = periods[kind];
      return {key: kind, label: kind === 'recognized' ? '季度实际确收（不含税）' : '季度实际回款', value: p?.amount == null ? null : p.amountText, missingText: status(p), note: p?.sourceLabel || ''};
    })} />
    <p className="ds-muted ds-od-note">{d.quarterActualHint || '金额按归属季度展示，查询日期不代表到账日期。实际由运营维护，销售预测独立保存。'}</p>
    {['recognized', 'collection'].map(kind => {
      const p = periods[kind];
      if (!p) return null;
      return <div key={kind} className="ds-od-finance-period">
        <div className="ds-od-actual-head"><b>{kind === 'recognized' ? '确收' : '回款'} · {status(p)}</b><span className="ds-muted">{p.sourceLabel || '尚无采用来源'}</span></div>
        {p.status === 'partial' && p.confirmed_subtotal != null && <p className="ds-muted">已登记小计：{p.confirmed_subtotal} 元{kind === 'recognized' ? '（不含税）' : ''}，尚缺 {p.missing_material_count ?? '待核对'} 项，未作为完整总额。</p>}
        {p.tax_unknown && <p className="ds-muted">所采用资料的税口径待核对，暂不显示正式确收总额。</p>}
        {(p.qualityHints || []).map((hint, i) => <p className="ds-muted" key={i}>{hint}</p>)}
        {!!p.confirmations?.length && <details><summary>查看登记明细（{p.confirmations.length} 项）</summary><SbTable rowKey="id" density="compact" columns={columns} rows={p.confirmations} state="normal" /></details>}
      </div>;
    })}
  </>;
}
