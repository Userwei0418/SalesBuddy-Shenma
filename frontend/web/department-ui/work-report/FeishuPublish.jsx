import React,{useEffect,useRef,useState} from 'react';
import {Alert,Button,Modal,Space,Tag} from 'antd';
import {identity} from '../pc/data.js';
import {accessLost} from './composition.js';

const blockedText={
  source_excluded:'这份报告包含演示、测试或其他不可对外同步的来源，不能同步飞书。',
  aggregate_provenance_not_supported:'经营概览、会议纪要和 Review 聚合报告暂不支持飞书同步，请使用有可核对来源的普通工作报告。',
  already_published:'本报告已冻结发布，后续编辑不会自动更新飞书或再次推送。',
};
export function feishuReceipt(value){
  if(!value || typeof value.can_publish!=='boolean' || !['not_published','pending','sent','unknown','failed'].includes(value.status) || (value.can_publish && (value.status!=='not_published' || value.event_id)))throw Error('飞书同步状态不完整，请重新读取');
  return value;
}
export default function FeishuPublish({document:doc,actorId,owner,dirty,locked,service,onAccessLost,onReload}){
  const [state,setState]=useState({receipt:null,error:'',reading:true}),[busy,setBusy]=useState(false),[confirm,setConfirm]=useState(false),[uncertain,setUncertain]=useState(false),[conflict,setConflict]=useState(false);
  const active=useRef(false),serial=useRef(0),sending=useRef(false);
  const current=()=>active.current && owner===identity();
  const own=doc.owner_id===actorId && doc.can_share;
  const read=async()=>{
    if(!current())return;const request=++serial.current;setState(previous=>({...previous,reading:true,error:''}));
    try{const receipt=feishuReceipt(await service.feishu(doc.id));if(current() && request===serial.current){setState({receipt,reading:false,error:''});if(receipt.event_id)setUncertain(false);}return receipt;}
    catch(error){if(current() && request===serial.current){setState({receipt:null,reading:false,error:error.message || '飞书同步状态暂不可用'});if(accessLost(error))onAccessLost?.(error);}}
  };
  useEffect(()=>{active.current=true;read();return()=>{active.current=false;serial.current++;};},[doc.id,doc.version_no,doc.latest_revision_id]);
  const receipt=state.receipt;
  const eligible=own && receipt?.can_publish===true && !receipt.event_id && doc.revision.id===doc.latest_revision_id;
  const enabled=eligible && !dirty && !locked && !busy && !state.reading && !state.error && !uncertain && !conflict;
  useEffect(()=>{if(!enabled)setConfirm(false);},[enabled]);
  const publish=async()=>{
    if(!current() || !enabled || sending.current)return;
    sending.current=true;setBusy(true);setConfirm(false);setUncertain(true);
    const payload={expected_version:doc.version_no,revision_id:doc.latest_revision_id,confirm:true};
    try{
      const accepted=await service.publishFeishu(doc.id,payload);
      if(!current())return;
      if(accepted?.document_id!==doc.id || accepted?.revision_id!==doc.latest_revision_id || !accepted?.event_id || accepted.status!=='queued')throw Error('发布回执不完整，请查询飞书状态，不要重复提交');
      setState({receipt:{status:'pending',can_publish:false,event_id:accepted.event_id,revision_id:accepted.revision_id},reading:false,error:''});
      await read();
    }catch(error){if(current()){
      if(accessLost(error)){onAccessLost?.(error);return;}
      // A lost POST response may already have queued a notification. Recovery is GET-only.
      const checked=await read();if(current()){
        const status=error.statusCode || error.status;
        if([400,409,422].includes(status) && !checked?.event_id){setUncertain(false);setConflict(true);setState(previous=>({...previous,error:status===409?'报告版本或发布状态已变化，请重新读取报告后核对。':'当前报告暂不可发布，请重新读取报告并核对来源与企业配置。'}));}
        else setState(previous=>({...previous,error:'提交结果需核对，请刷新飞书状态；未核对前不会重复提交。'}));
      }
    }}finally{sending.current=false;if(current())setBusy(false);}
  };
  if(owner!==identity() || !own)return null;
  const published=Boolean(receipt?.event_id),failed=receipt?.status==='failed';
  const table=!receipt?(state.reading?'读取中':'待核对'):receipt.record_id?'已入表':published?(failed?'未完成':'等待同步'):uncertain?'待核对':'未同步';
  const group=!receipt?(state.reading?'读取中':'待核对'):receipt.message_id?'已推送':published?(receipt.status==='unknown'?'结果未知':failed?'未完成':'等待推送'):uncertain?'待核对':'未推送';
  const hint=blockedText[receipt?.blocked_reason] || (receipt && !receipt.can_publish && !published?'当前报告暂不可同步，请联系企业管理员核对配置。':'确认后将保存当前修订到飞书多维表格，并向已配置的大群推送一次通知。');
  return <section className="wr-panel" aria-label="飞书同步" style={{display:'grid',gap:12}}>
    <Space wrap><strong>飞书同步</strong><Tag color={receipt?.record_id?'green':'default'}>多维表格：{table}</Tag><Tag color={receipt?.message_id?'green':'default'}>群通知：{group}</Tag></Space>
    <div style={{color:'var(--pc-text-secondary)',lineHeight:1.6}}>{hint}</div>
    {dirty && <Alert type="info" title="请先保存当前修改，再同步飞书。"/>}
    {state.error && <Alert type="warning" title={state.error}/>}
    {uncertain && !receipt?.event_id && <Alert type="warning" title="发布结果待核对" description="只查询状态，不会自动重新提交或重复推送。"/>}
    {conflict && onReload && <Button onClick={onReload}>重新读取报告</Button>}
    {receipt?.status==='unknown' && <Alert type="warning" title="群推送结果未知" description="请查询状态或联系企业管理员核对，不会自动重复推送。"/>}
    {failed && <Alert type="warning" title="飞书同步尚未完成" description="已完成的入表或推送会分别显示；请刷新状态或联系企业管理员检查同步任务。"/>}
    <Space wrap><Button aria-label="同步飞书并推送群" type="primary" disabled={!enabled} loading={busy} onClick={()=>setConfirm(true)}>同步飞书并推送群</Button><Button aria-label="刷新飞书状态" onClick={read} loading={state.reading} disabled={busy}>刷新飞书状态</Button></Space>
    {published && <div style={{fontSize:12,color:'var(--pc-text-secondary)'}}>发布修订：{receipt.revision_id || '正在读取'}。入队后仍需等待实际入表和群推送完成。</div>}
    <Modal title="同步飞书并推送群" open={confirm} onCancel={()=>setConfirm(false)} onOk={publish} okText="确认同步并推送" cancelText="取消" confirmLoading={busy} okButtonProps={{disabled:!enabled}}>
      <p>报告：{doc.title || doc.revision.content.title}</p><p>第 {doc.revision.revision_no} 版（{doc.latest_revision_id}）</p>
      <p>将当前已保存版本同步至企业飞书多维表格，并向配置的大群发送通知。每份报告仅冻结发布一次，后续编辑不会自动再次推送。</p>
    </Modal>
  </section>;
}
