import {WorkflowDialog} from '../WorkflowOverlays.jsx';
import React,{useEffect,useMemo,useState} from 'react';
import {Alert,Button,Select} from 'antd';
import {SbField} from '@shandiant/ui-react';
import {Input,SbTextarea} from '../ImeInput.jsx';
import {identity} from '../pc/data.js';
import {createOpportunityAccessApi} from './api.js';
import {createTransferController} from './transfer-controller.js';
import './collaboration.css';
const syncLabels={pending:'同步请求已排队，尚未确认外部系统更新',disabled:'外部同步未启用',not_configured:'外部同步尚未配置',unchanged:'未产生新的同步请求'};
function Transfer({id,onReadback}){
 const c=useMemo(()=>createTransferController(id,createOpportunityAccessApi()),[id]);
 const [s,set]=useState(c.getState()),[open,setOpen]=useState(false),[query,setQuery]=useState('');
 useEffect(()=>{const off=c.subscribe(set);c.load();return()=>{off();c.dispose();};},[c]);
 const can=s.access?.capabilities.can_transfer===true,locked=!can||!['ready','conflict'].includes(s.phase),busy=['saving','uncertain'].includes(s.phase);
 const close=()=>{if(busy)return;setOpen(false);if(s.phase==='done')onReadback();};
 const team=s.person?.teams.find(t=>t.team_id===s.teamId);
 return <>
  {can && s.phase!=='done' && <Button onClick={()=>{setOpen(true);c.load(true);}}>转交商机主责</Button>}
  <WorkflowDialog width={640} open={open} title="转交商机主责" onClose={busy?undefined:close} footer={<><Button disabled={busy} onClick={close}>{s.phase==='done'?'关闭并刷新详情':'取消'}</Button>{can&&s.phase!=='done'&&(s.phase==='conflict'?<Button type="primary" onClick={()=>c.acknowledge()}>已核对最新主责</Button>:<Button type="primary" loading={s.phase==='saving'} disabled={s.phase!=='ready'&&s.phase!=='uncertain'} onClick={()=>c.save()}>{s.phase==='uncertain'?'重试本次转交':'确认转交'}</Button>)}</>}>
   <div className="ds-oa-panel">
    <p>仅转交当前商机的主责与所属团队。客户归属、任务责任和已有历史财务归属分别保留。</p>
    {s.message&&<Alert showIcon type={s.phase==='done'?'success':'warning'} message={s.message}/>}
    {s.phase==='loading'&&<p role="status">正在核对商机转交权限…</p>}
    {s.phase==='denied'&&<Button onClick={()=>c.load(true)}>重新核对转交权限</Button>}
    {s.access&&<div className="ds-oa-owner"><b>当前主责</b><span>{s.access.owner.display_name || '待核对'} · {s.access.owner.team_name || '团队待核对'}</span></div>}
    {s.result?<><h3>{s.result.owner.display_name} · {s.result.owner.team_name}</h3><p>{syncLabels[s.result.sync_status]}</p></>:<>
     <form className="ds-oa-search" onSubmit={e=>{e.preventDefault();c.search(query);}}><Input aria-label="搜索接手销售" placeholder="输入姓名查找可接手销售" value={query} maxLength={100} disabled={locked} onChange={e=>setQuery(e.target.value)}/><Button htmlType="submit" disabled={locked} loading={s.directoryLoading}>查找销售</Button></form>
     {s.directoryError&&<Alert type="warning" message={s.directoryError}/>}
     <div className="ds-oa-candidates">{s.people.map(p=><Button key={p.user_id} disabled={locked} type={s.person?.user_id===p.user_id?'primary':'default'} onClick={()=>c.choose(p.user_id)}>{p.display_name}</Button>)}</div>
     {s.offset<s.total&&<Button loading={s.directoryLoading} disabled={locked} onClick={()=>c.search(s.query,true)}>加载更多销售</Button>}
     {s.person&&<><SbField label="接手销售"><strong>{s.person.display_name}</strong></SbField><SbField label="接手团队" required><Select aria-label="接手团队" placeholder="请选择此次转交的明确团队" value={s.teamId||undefined} options={s.person.teams.map(t=>({value:t.team_id,label:t.name}))} disabled={locked} onChange={value=>c.edit('teamId',value)}/></SbField></>}
     <SbField label="转交原因" required><SbTextarea aria-label="转交原因" maxLength={1000} autoSize={{minRows:3,maxRows:7}} value={s.reason} disabled={locked} onChange={value=>c.edit('reason',value)}/></SbField>
     {s.person&&team&&<Alert type="info" message={`确认后转交给 ${s.person.display_name} · ${team.name}`} description="转交后是否还能查看，由当前权限决定。"/>}
    </>}
   </div>
  </WorkflowDialog>
 </>;
}
export default function OpportunityTransfer({opportunityId,onReadback}){return <Transfer key={`${identity()}:${opportunityId}`} id={opportunityId} onReadback={onReadback}/>;}
