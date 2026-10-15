import {Input} from './ImeInput.jsx';
import React from 'react';
import {Alert, Button, Checkbox, Modal, Select, Space, Tag} from 'antd';
import {SbStatePanel} from '@shandiant/ui-react';

// Presentation bridge only. Selection, permissions, versions and writes remain in 1.1.0 components.
export function PersonPickerDialog({instance, invokeOn}) {
  if (!instance) return null;
  const d=instance.data, p=instance.properties, call=(name,payload={})=>invokeOn(instance,name,payload);
  return <Modal rootClassName="department-ui" title={p.title} open={Boolean(p.open)} onCancel={()=>call('close')} width={640}
    footer={<Space><span>已选 {d.draft?.length || 0} 人</span><Button onClick={()=>call('close')}>返回</Button><Button type="primary" disabled={!d.canConfirm} onClick={()=>call('confirm')}>确认选择</Button></Space>}>
    <div className="ds-person-toolbar"><Select aria-label="筛选团队" value={d.teamId || ''} options={[{value:'',label:'全部团队'},...(d.teamRows || []).map(t=>({value:t.id,label:t.name}))]} onChange={id=>call('chooseTeam',{dataset:{id}})} />
    <Input aria-label="搜索人员" placeholder="搜索姓名或账号" value={d.query || ''} allowClear onChange={e=>call('search',{detail:{value:e.target.value}})} /></div>
    {!!d.selectedRows?.length && <div className="ds-person-selected">{d.selectedRows.map(r=><Tag key={r.id} closable={!p.loading} onClose={e=>{e.preventDefault();call('toggle',{dataset:{id:r.id}});}}>{r.name}</Tag>)}</div>}
    {d.selectionError && <Alert type="warning" showIcon title={d.selectionError} />}
    <SbStatePanel state={p.loading?'loading':p.error?'error':!d.rows?.length?'empty':'normal'} title={p.error || (!d.rows?.length?'没有匹配的人员':undefined)} onRetry={()=>call('retry')} />
    {!p.loading && !p.error && <div className="ds-person-rows">{d.rows?.map(r=><div className="ds-person-row" key={r.id}><Checkbox checked={r.checked} onChange={()=>call('toggle',{dataset:{id:r.id}})}><span><strong>{r.name}</strong><small>{r.account_code || r.account || ''} · {r.teamLabel || r.team_name || r.team || '未填写团队'}</small></span></Checkbox></div>)}</div>}
    {p.allowAll && <Button type="link" disabled={p.loading || !!p.error} onClick={()=>call('chooseAll')}>{p.allLabel}</Button>}
  </Modal>;
}
export function TaskCancellationDialog({instance, invokeOn}) {
  if (!instance) return null;
  const d=instance.data,p=instance.properties,call=(name,payload={})=>invokeOn(instance,name,payload);
  const busy=d.loading || d.submitting || d.confirming;
  return <Modal rootClassName="department-ui" title="取消任务" focusable={{trap:!d.confirming}} keyboard={!d.confirming} open={Boolean(p.open)} onCancel={()=>call('close')} width={560}
    footer={<Space><Button onClick={()=>call('close')}>返回</Button>{d.needsRefresh && <Button disabled={busy} onClick={()=>call('refresh')}>刷新任务状态</Button>}<Button danger type="primary" loading={d.submitting || d.confirming} disabled={busy || !d.canSubmit || d.needsRefresh} onClick={()=>call('submit')}>核对并取消任务</Button></Space>}>
    {d.loading ? <SbStatePanel state="loading" title="正在核对任务和权限" /> : <><p>{d.task?.description || '当前任务'}</p><p className="ds-muted">取消不会计为完成，任务及原因会在已结束中保留。</p></>}
    <label htmlFor="task-cancel-note">取消原因 · 必填</label><Input.TextArea id="task-cancel-note" aria-label="取消原因" value={d.note || ''} maxLength={500} showCount autoSize={{minRows:4,maxRows:8}} disabled={busy} onChange={e=>call('inputNote',{detail:{value:e.target.value}})} />
    {d.error && <Alert className="ds-dialog-feedback" type="error" showIcon title={d.error} />}
  </Modal>;
}
