import React, {forwardRef, useImperativeHandle, useRef, useState} from 'react';
import {Alert, Button, Input, Modal, Select, Table} from 'antd';
import {SbField} from '@shandiant/ui-react';
import {BUSINESS_RECORDS, FOLLOW_UP_TYPES, normalizeDemoDraft, searchBusiness, validIds, validateDemoDraft} from './shenzhou-demo.mjs';
import './followup-business-demo.css';

const STORAGE_ERROR = '演示草稿未能保存，当前选择仍保留在页面。请重试保存后再离开。';
export default forwardRef(function FollowupBusinessDemo({draftKey, disabled=false, readOnly=false}, ref) {
  const preview = globalThis.SALES_MODE === 'preview';
  const [initial] = useState(() => {
    try {return {draft:normalizeDemoDraft(JSON.parse(localStorage.getItem(draftKey) || 'null')), error:''};}
    catch {return {draft:normalizeDemoDraft(null), error:'演示草稿读取失败，请重新选择并保存。'};}
  });
  const [draft,setDraft] = useState(initial.draft);
  const [storageError,setStorageError] = useState(initial.error);
  const [saved,setSaved] = useState(false);
  const [errors,setErrors] = useState({});
  const [open,setOpen] = useState(false);
  const [pending,setPending] = useState([]);
  const [query,setQuery] = useState('');
  const typeRef=useRef(null), triggerRef=useRef(null);
  const blocked = disabled || !preview;
  const selected = draft.relatedBusinessIds.map(id=>BUSINESS_RECORDS.find(r=>r.id===id));
  function save(next=draft) {
    if (!preview) return true;
    try {localStorage.setItem(draftKey,JSON.stringify(normalizeDemoDraft(next)));setStorageError('');setSaved(true);return true;}
    catch {setStorageError(STORAGE_ERROR);setSaved(false);return false;}
  }
  function update(patch) {
    const next=normalizeDemoDraft({...draft,...patch});setDraft(next);save(next);
    setErrors(previous=>Object.fromEntries(Object.entries(previous).map(([key,value])=>[key,key in patch ? '' : value])));
  }
  useImperativeHandle(ref,()=>({
    save,
    validate() {
      if (!preview) return true;
      const next=validateDemoDraft(draft);setErrors(next);
      if (next.followUpType) typeRef.current?.focus();
      else if (next.relatedBusinessIds) triggerRef.current?.focus();
      return !Object.values(next).some(Boolean) && save();
    },
  }));
  function showPicker() {setPending([...draft.relatedBusinessIds]);setQuery('');setOpen(true);}
  if (readOnly) return <section className="ds-vc-business-summary" aria-label="跟进演示信息">
    <b>跟进类型：{draft.followUpType || '未填写'}</b>
    <span>关联业务数据：{selected.length ? selected.map(r=>r.name).join('；') : '未填写'}</span>
    <small className="ds-muted">这两项仅保存在本浏览器的演示草稿中。</small>
  </section>;
  return <div className="ds-vc-business" aria-label="跟进类型与关联业务数据">
    <SbField label="跟进类型" required>
      <Select ref={typeRef} aria-label="跟进类型" placeholder="请选择跟进类型" className="ds-vc-followup-select" value={draft.followUpType || undefined}
        options={FOLLOW_UP_TYPES.map(value=>({value,label:value}))} disabled={blocked} status={errors.followUpType ? 'error' : undefined}
        onChange={followUpType=>update({followUpType})} />
      {errors.followUpType && <p className="ds-vc-error" role="alert">{errors.followUpType}</p>}
    </SbField>
    <section className="ds-vc-business-panel" aria-labelledby="related-business-heading">
      <div className="ds-vc-business-head"><div><h3 id="related-business-heading">关联业务数据 <span className="ds-vc-required">*</span></h3><p className="ds-muted">可关联多条业务数据</p></div>
        <Button ref={triggerRef} disabled={blocked} onClick={showPicker}>添加业务数据</Button></div>
      {selected.length ? <ul className="ds-vc-business-chips">{selected.map(r=><li key={r.id}><div><b>{r.name}</b><small className="ds-muted">{r.code}</small></div><Button type="text" size="small" disabled={blocked} aria-label={`移除 ${r.code}`} onClick={()=>update({relatedBusinessIds:draft.relatedBusinessIds.filter(id=>id!==r.id)})}>移除</Button></li>)}</ul>
        : <p className="ds-muted ds-vc-business-empty">尚未关联业务数据，点击右上方添加</p>}
      {errors.relatedBusinessIds && <p className="ds-vc-error" role="alert">{errors.relatedBusinessIds}</p>}
    </section>
    <div className="ds-vc-business-status" role="status"><span className="ds-muted">演示数据 · 仅保存到本浏览器</span>{saved && !storageError && <span className="ds-muted">已保存演示草稿</span>}</div>
    {!preview && <Alert type="info" title="新增字段当前仅支持演示模式" />}
    {storageError && <Alert type="error" title={storageError} action={<Button size="small" onClick={()=>save()}>重试保存</Button>} />}
    <Modal title="选择关联业务数据" open={open} width={820} className="ds-vc-business-modal" centered
      onCancel={()=>setOpen(false)} maskClosable={false} afterClose={()=>triggerRef.current?.focus()}
      footer={<div className="ds-vc-business-footer"><span className="ds-muted">已选 {pending.length} 条</span><Button onClick={()=>setOpen(false)}>取消</Button><Button type="primary" disabled={blocked} onClick={()=>{update({relatedBusinessIds:pending});setOpen(false);}}>确认关联</Button></div>}>
      <p className="ds-muted">选择本次跟进涉及的业务数据，确认后添加到表单。</p>
      <Input.Search aria-label="搜索业务数据" placeholder="搜索业务名称、编号或说明" allowClear value={query} onChange={e=>setQuery(e.target.value)} />
      <Table rowKey="id" size="small" pagination={false} dataSource={searchBusiness(query)} scroll={{y:320}}
        rowSelection={{selectedRowKeys:pending,preserveSelectedRowKeys:true,onChange:ids=>setPending(validIds(ids)),getCheckboxProps:r=>({'aria-label':`选择 ${r.code}`})}}
        columns={[{title:'业务数据',key:'name',render:(_,r)=><div className="ds-vc-business-cell"><b>{r.name}</b><small className="ds-muted">{r.code} · {r.description}</small></div>}]}
        locale={{emptyText:'没有匹配的业务数据，请更换关键词'}} />
    </Modal>
  </div>;
});
