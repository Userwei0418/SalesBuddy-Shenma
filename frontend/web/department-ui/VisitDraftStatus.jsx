import React from 'react';
import {Button} from 'antd';

export function VisitDraftStatus({data: d}) {
  const error = d.draftSaveError || d.draftConflict;
  const time = d.draftSavedAt ? new Date(d.draftSavedAt).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}) : '';
  return <div className="ds-visit-draft-status" style={{display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '4px 12px', fontSize: 13, margin: '8px 0'}}>
    {error ? <><span role="alert" style={{color: 'var(--ant-color-error, #c63838)'}}>{typeof error === 'string' ? error : '原记录已更新，请核对草稿后再保存'}</span>
      {!d.draftConflict && <Button size="small" onClick={() => window.dispatchEvent(new CustomEvent('sales:retry-visit-draft'))}>重试保存草稿</Button>}</>
      : <span className="ds-muted" role="status">{time ? `已自动保存到本机 · ${time}` : '输入后自动保存到本机'}</span>}
    {d.draftNotice && <span className="ds-muted">{d.draftNotice}</span>}
  </div>;
}
