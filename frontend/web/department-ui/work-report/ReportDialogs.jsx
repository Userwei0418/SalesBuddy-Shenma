import React from 'react';
import {Alert, Button, Drawer, Empty, Modal, Select, Spin, Tag} from 'antd';
import {SourceList} from './DocumentEditor.jsx';

export function SourcesDrawer({open, onClose, ...props}) {
  return <Drawer title="报告材料来源" open={open} onClose={onClose} size={620} destroyOnHidden><SourceList {...props} /></Drawer>;
}

export function HistoryDrawer({open, onClose, items, loading, error, onRetry, selectedId, onSelect, detail, detailLoading, detailError, hasMore, onMore}) {
  return <Drawer title="历史版本" open={open} onClose={onClose} size={760} destroyOnHidden>
    <p className="wr-muted">查看当时保存的报告内容与材料。当前编辑稿不会被历史版本覆盖。</p>
    {error ? <Alert type="error" title={error} action={<Button onClick={onRetry}>重试</Button>} /> : <>
      <div className="wr-versions">{items.map(item=><Button key={item.id} type={item.id===selectedId?'primary':'default'} onClick={()=>onSelect(item.id)}>{item.label}</Button>)}</div>
      {loading && <Spin size="small" />}
      {!loading && !items.length && <Empty description="暂无历史版本" />}
      {hasMore && <Button onClick={onMore} loading={loading}>更多版本</Button>}
      {detailError ? <Alert type="error" title={detailError} /> : detailLoading ? <Spin size="small" /> : detail && <section className="wr-paper wr-history-paper"><header className="wr-paper-header"><b>{detail.title}</b><Tag>{detail.label}</Tag></header><div className="wr-report-preview">{detail.text}</div></section>}
    </>}
  </Drawer>;
}

export function ShareDialog({open, onClose, options, selected, onChange, loading, saving, error, onSave, onRevoke, alreadyShared, selector, canSubmit=Boolean(selected?.length)}) {
  return <Modal title={alreadyShared?'管理报告共享':'确认并共享报告'} open={open} onCancel={onClose} destroyOnHidden footer={<div className="wr-share-actions">
    {alreadyShared && onRevoke && <Button danger onClick={onRevoke} disabled={loading || saving || Boolean(error)}>取消全部共享</Button>}
    <Button onClick={onClose} disabled={saving}>暂不共享</Button><Button type="primary" onClick={onSave} loading={saving} disabled={loading || !canSubmit || Boolean(error)}>确认共享</Button>
  </div>}>
    <p className="wr-dialog-note">请先核对报告内容，再选择接收范围。共享范围以当前数据权限为准。</p>
    {error && <Alert type="error" title={error} showIcon />}
    {selector || <Select aria-label="共享给成员" mode="multiple" options={options} value={selected} onChange={onChange} loading={loading} disabled={loading || saving || Boolean(error)} placeholder="选择成员" style={{width:'100%'}} optionFilterProp="label" />}
  </Modal>;
}

export function ExportDialog({open, onClose, value, loading, error, onRetry, onCopy, onDownload}) {
  return <Modal title="复制与下载报告" open={open} onCancel={onClose} destroyOnHidden footer={<>
    <Button onClick={onClose}>关闭</Button><Button onClick={onCopy} disabled={loading || Boolean(error) || !value}>复制正文</Button><Button type="primary" onClick={onDownload} disabled={loading || Boolean(error) || !value}>下载 Markdown</Button>
  </>}>
    {error ? <Alert type="error" title={error} action={onRetry && <Button onClick={onRetry}>重试</Button>} /> : loading ? <Spin size="small" /> : value && <div className="wr-export-summary"><b>{value.title}</b><p className="wr-muted">{value.description}</p></div>}
  </Modal>;
}
