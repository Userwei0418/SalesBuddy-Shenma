import {WorkflowDrawer} from '../WorkflowOverlays.jsx';
import React, {useEffect, useMemo, useState} from 'react';
import {Button, Checkbox} from 'antd';
import {Input} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {createOpportunityAccessApi} from './api.js';
import {createCollaborationController} from './controller.js';
import './collaboration.css';

function CollaborationSheet({id, open, close}) {
  const controller = useMemo(() => createCollaborationController(id, createOpportunityAccessApi()), [id]);
  const [s, set] = useState(controller.getState()), [query, setQuery] = useState('');
  useEffect(() => {const off=controller.subscribe(set);controller.load();return () => {off();controller.dispose();};}, [controller]);
  const access=s.access, manage=access?.capabilities.can_manage_collaborators === true;
  const locked=!manage || !['ready','conflict'].includes(s.phase), saving=s.phase==='saving', uncertain=s.phase==='uncertain';
  const names=s.draft.map(p=>p.display_name).join('、');
  return <WorkflowDrawer width={640} open={open} title="商机协作" onClose={saving ? undefined : close} footer={<>
    <Button disabled={saving} onClick={close}>关闭</Button>
    {s.phase==='conflict' && manage ? <Button type="primary" onClick={()=>controller.acknowledge()}>已核对最新名单</Button>
      : manage && <Button type="primary" loading={saving} disabled={saving || (!uncertain && (s.phase!=='ready' || !controller.dirty()))} onClick={()=>controller.save()}>{uncertain ? '重试本次保存' : '保存协作名单'}</Button>}
  </>}>
    <div className="ds-oa-panel">
      <p className="ds-muted">协作人可跟进本商机。客户归属、其他商机和普通编辑权限分别管理。</p>
      {s.message && <p role="status" className={`ds-oa-message ${['denied','error','uncertain','conflict'].includes(s.phase)?'ds-oa-warning':''}`}>{s.message}</p>}
      {s.phase==='loading' && <p role="status">正在核对商机和协作权限…</p>}
      {['error','denied'].includes(s.phase) && <Button onClick={()=>controller.load(true)}>重新读取商机</Button>}
      {access && <>
        <h3>{access.name}</h3>
        <div className="ds-oa-owner"><b>商机主责</b><span>{access.owner.display_name || '历史主责待核对'} · {access.owner.team_name || '团队未确认'}</span></div>
        <div><b>当前协作人</b><p>{access.collaborators.length ? access.collaborators.map(p=>p.display_name).join('、') : '暂未设置协作人'}</p></div>
        {!manage && <p className="ds-muted">当前账号无权维护名单，由商机主责管理。</p>}
      </>}
      {!!s.draft.length && <div className="ds-oa-selected"><b>本次选择（{s.draft.length}/30）</b><ul>{s.draft.map(p=><li key={p.user_id}><span>{p.display_name}</span><Button type="link" size="small" disabled={locked} aria-label={`移除 ${p.display_name}`} onClick={()=>controller.toggle(p,false)}>移除</Button></li>)}</ul></div>}
      {manage && <>
        {controller.dirty() && <p className="ds-oa-change">保存后名单：{names || '无协作人（将移除全部协作人）'}</p>}
        <form className="ds-oa-search" onSubmit={e=>{e.preventDefault();controller.search(query);}}><Input aria-label="搜索协作人" placeholder="输入姓名查找可添加成员" value={query} maxLength={100} disabled={locked} onChange={e=>setQuery(e.target.value)}/><Button htmlType="submit" disabled={locked} loading={s.directoryLoading}>查找</Button></form>
        {s.directoryError && <p role="alert">{s.directoryError} <Button type="link" onClick={()=>controller.search(query)}>重试</Button></p>}
        <div className="ds-oa-candidates">{s.candidates.map(p=><div key={p.user_id}><Checkbox checked={s.draft.some(d=>d.user_id===p.user_id)} disabled={locked || (s.draft.length>=30&&!s.draft.some(d=>d.user_id===p.user_id))} onChange={e=>controller.toggle(p,e.target.checked)}>{p.display_name}</Checkbox></div>)}</div>
        {!s.directoryLoading && !s.directoryError && s.query && !s.candidates.length && <p className="ds-muted">没有匹配的可添加成员</p>}
        {s.offset<s.total && <Button disabled={locked} loading={s.directoryLoading} onClick={()=>controller.search(s.query,true)}>加载更多成员</Button>}
        {!s.candidates.length && !s.directoryError && !s.directoryLoading && !s.query && <Button disabled={locked} onClick={()=>controller.search('')}>查看可添加成员</Button>}
      </>}
    </div>
  </WorkflowDrawer>;
}
export default function OpportunityCollaboration({opportunityId}) {
  const [open,setOpen]=useState(false), actor=identity();
  useEffect(()=>setOpen(false),[opportunityId,actor]);
  return <><Button onClick={()=>setOpen(true)}>商机协作</Button>{open && <CollaborationSheet key={`${actor}:${opportunityId}`} id={opportunityId} open close={()=>setOpen(false)}/>}</>;
}
