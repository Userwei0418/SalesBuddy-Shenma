import React from 'react';
import { Button } from 'antd';
import { SbField, SbListRow, SbSearch, SbStatusTag } from '@shandiant/ui-react';
import './fde-visit-opportunity.css';

// FDE 选本人参与的商机（原子组件 fde-visit-opportunity）：picker 是组件实例，只能选到本人在协助名单里的商机。
export function FdeVisitOpportunity({ picker, invokeOn, selectedId }) {
  if (!picker) return <SbField label="选择商机" required><p className="ds-muted">正在核对商机协助关系…</p></SbField>;
  const d = picker.data || {}, disabled = Boolean(picker.properties?.disabled);
  const on = (name, payload) => invokeOn(picker, name, payload);
  return <SbField label="选择商机" required help="仅可选择本人参与的商机">
    {d.selected ? <div className="ds-fvo-selected"><b>{d.selected.name}</b><SbStatusTag tone="good" label="已选择" /><Button type="link" size="small" disabled={disabled} onClick={() => on('change')}>更换</Button></div>
      : !selectedId && <>
        <SbSearch value={d.query || ''} placeholder="搜索该客户下本人参与的商机" loading={Boolean(d.loading)} disabled={disabled} onChange={value => on('search', { detail: { value } })} />
        <div className="ds-fvo-list">
          {(d.items || []).map(o => <SbListRow key={o.id} name={o.name} summary={o.stage_label || o.stage_code || ''} disabled={disabled} onClick={() => on('choose', { dataset: { id: o.id } })} />)}
          {d.hasMore && <Button size="small" disabled={disabled || Boolean(d.loading)} onClick={() => on('more')}>加载更多</Button>}
        </div>
      </>}
    {d.loading ? <p className="ds-muted ds-fvo-state">正在核对商机协助关系…</p>
      : d.error ? <p className="ds-fvo-error">{d.error} <Button type="link" size="small" disabled={disabled} onClick={() => on('retry')}>重新加载</Button></p>
        : !d.selected && !(d.items || []).length ? <p className="ds-muted ds-fvo-state">{d.query ? '没有匹配的本人商机，请调整关键词。' : '该客户暂无你参与的商机，请先由销售添加协助 FDE。'}</p> : null}
  </SbField>;
}
