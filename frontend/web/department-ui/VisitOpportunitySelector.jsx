import {SbSearch} from './ImeInput.jsx';
import React from 'react';
import {Button} from 'antd';
import {SbField, SbListRow, SbStatusTag} from '@shandiant/ui-react';
import './fde-visit-opportunity.css';

// View adapter only: the shared component owns candidates, restoration and events.
export function VisitOpportunitySelector({picker, invokeOn, selectedId}) {
  if (!picker) return <SbField label="关联已有商机" help="选填，可只记录客户跟进"><p className="ds-muted">正在加载商机…</p></SbField>;
  const d = picker.data || {}, disabled = Boolean(picker.properties?.disabled);
  const on = (name, payload) => invokeOn(picker, name, payload);
  return <SbField label="关联已有商机" help="选填，可只记录客户跟进">
    {(d.selected || selectedId) && <div className="ds-fvo-selected"><b>{d.selected?.name || '正在核对已选商机'}</b>{d.selected && <SbStatusTag tone="good" label="已选择" />}<Button type="link" size="small" disabled={disabled} onClick={() => on('clear')}>清除关联</Button></div>}
    {!selectedId && !d.selected && <>
      <SbSearch value={d.query || ''} placeholder="搜索该客户下已有商机" loading={Boolean(d.loading)} disabled={disabled} onChange={value => on('search', {detail: {value}})} />
      <div className="ds-fvo-list">{(d.items || []).map(row => <SbListRow key={row.id} name={row.name} summary={row.stage_label || row.stage_code || ''} disabled={disabled} onClick={() => on('choose', {dataset: {id: row.id}})} />)}
        {d.hasMore && <Button size="small" disabled={disabled || d.loading} onClick={() => on('more')}>加载更多</Button>}
      </div>
    </>}
    {d.loading ? <p className="ds-muted ds-fvo-state">正在加载商机…</p> : d.error ? <p className="ds-fvo-error" role="alert">{d.error} <Button type="link" size="small" disabled={disabled} onClick={() => on('retry')}>重新加载</Button></p>
      : !d.selected && !(d.items || []).length ? <p className="ds-muted ds-fvo-state">{d.query ? '没有匹配的商机，请调整关键词。' : '暂无可选商机，可直接记录客户跟进。'}</p> : null}
  </SbField>;
}
