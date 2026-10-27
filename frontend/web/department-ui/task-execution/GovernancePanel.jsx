import React from 'react';
import {Alert, Button, DatePicker, Modal, Select, Tag} from 'antd';
import dayjs from 'dayjs';
import {SbField} from '@shandiant/ui-react';
import {Input, SbTextarea} from '../ImeInput.jsx';
import {taskSyncUnavailable} from './TaskSyncStatus.jsx';

function Diff({rows}){return <dl className="task-governance-diff">{(rows || []).map(row=><div key={row.key}><dt>{row.label}</dt><dd><span>当前</span><p>{row.before || '—'}</p><span>拟变更为</span><p>{row.after || '—'}</p></dd></div>)}</dl>;}
const statuses={pending:'待确认',accepted:'已接受',rejected:'已拒绝',invalidated:'已失效',cancelled:'已取消'};
export default function GovernancePanel({data:d,invoke}) {
 const x=d.execution, busy=Boolean(d.executionBusy), blocked=busy || d.executionLoading || Boolean(d.executionError) || taskSyncUnavailable(d.webTaskSync);
 if(!x)return null;
 const action=name=>invoke('executionPageAction',{dataset:{action:name}});
 const handover=(key,value)=>invoke('inputHandover',{dataset:{key},detail:{value}});
 const input=(key,value,id)=>invoke('inputRequirements',{dataset:{key,...(id?{id}:{})},detail:{value}});
 const draft=d.requirementsDraft,req=x.raw.pending_requirements;
 const handovers=[['执行责任',x.raw.execution_handover,'respond_executor_handover','executorAccept','executorReject'],['验收责任',x.raw.reviewer_handover,'respond_reviewer_handover','reviewerAccept','reviewerReject']];
 return <section className="task-governance" aria-label="责任与任务要求">
  <div className="task-execution-heading"><h3>责任与任务要求</h3><div className="task-execution-actions">
   {x.caps.initiate_executor_handover===true && <Button disabled={blocked} onClick={()=>invoke('openExecutionHandover',{dataset:{purpose:'executor'}})}>转交执行责任</Button>}
   {x.caps.initiate_reviewer_handover===true && <Button disabled={blocked} onClick={()=>invoke('openExecutionHandover',{dataset:{purpose:'reviewer'}})}>转交验收责任</Button>}
   {x.caps.propose_requirements===true && <Button disabled={blocked} onClick={()=>invoke('openRequirements')}>{x.selfManaged?'修改任务要求':'申请修改要求'}</Button>}
  </div></div>
  {handovers.filter(([,h])=>h).map(([label,h,cap,accept,reject])=><article className="task-governance-card" key={cap}><h4>{label}交接 <Tag>{statuses[h.status] || '待核对'}</Tag></h4><p>接手人：{h.target_name || '待确认'}</p><p>原因：{h.reason}</p><p className="ds-muted">{cap==='respond_executor_handover'?'接手前暂停执行操作，历史成果保留；拒接后需重新协调。':'候选人接受前，原有效验收人继续负责；执行责任保持不变。'}</p>{h.status==='pending' && x.caps[cap]===true && <div className="task-execution-actions"><Button disabled={blocked} onClick={()=>action(reject)}>拒绝接手{label==='执行责任'?'执行':'验收'}</Button><Button type="primary" disabled={blocked} onClick={()=>action(accept)}>确认接手{label==='执行责任'?'执行':'验收'}</Button></div>}</article>)}
  {req && <article className="task-governance-card"><h4>要求变更待确认</h4><p className="ds-muted">确认生效前继续按当前要求执行。请逐项核对本次变更。</p><p>原因：{req.reason}</p><Diff rows={x.requirementsDiff}/><div className="task-execution-actions">{x.caps.respond_requirements===true && <><Button disabled={blocked} onClick={()=>action('requirementsReject')}>拒绝变更</Button><Button type="primary" disabled={blocked} onClick={()=>action('requirementsAccept')}>确认以上变更</Button></>}{x.caps.withdraw_requirements===true && <Button disabled={blocked} onClick={()=>action('requirementsWithdraw')}>撤回／作废本次申请</Button>}</div></article>}
  <Modal title={d.handoverPurpose==='reviewer'?'转交验收责任':'转交执行责任'} open={Boolean(d.handoverPurpose)} width={640} maskClosable={false} closable={!busy} onCancel={()=>invoke('closeExecutionHandover')} destroyOnHidden footer={<><Button disabled={busy} onClick={()=>invoke('closeExecutionHandover')}>取消</Button><Button type="primary" loading={busy} disabled={blocked || d.handoverLoading || !!d.handoverError || !d.handoverTarget} onClick={()=>invoke('submitExecutionHandover')}>确认发起交接</Button></>}>
   <p>{d.handoverPurpose==='reviewer'?'新验收人接受后才切换验收责任。':'交接后由新人确认接手；待接手期间暂停推进，原有成果保留。'}</p>
   <div className="task-execution-heading"><Input aria-label="搜索接手人员" value={d.handoverQuery || ''} maxLength={100} disabled={busy} onChange={e=>handover('handoverQuery',e.target.value)} onPressEnter={()=>invoke('loadHandoverCandidates')}/><Button loading={d.handoverLoading} disabled={busy} onClick={()=>invoke('loadHandoverCandidates')}>搜索人员</Button></div>
   <div className="task-governance-candidates">{(d.handoverCandidates || []).map(row=><Button key={row.user_id} type={d.handoverTarget===row.user_id?'primary':'default'} disabled={busy || d.handoverLoading} onClick={()=>invoke('selectHandoverCandidate',{dataset:{id:row.user_id}})}>{row.display_name}{row.teams?.map(t=>` · ${t.name}`).join('')}</Button>)}</div>
   {!d.handoverLoading && !d.handoverCandidates?.length && !d.handoverError && <p>暂无符合本次交接资格的人员</p>}
   {d.handoverMore && <Button disabled={busy} loading={d.handoverLoading} onClick={()=>invoke('loadHandoverCandidates',{dataset:{more:true}})}>加载更多人员</Button>}
   <p>已选：{d.handoverTargetLabel || '尚未选择'}</p><SbField label="交接原因" required><SbTextarea aria-label="交接原因" value={d.handoverReason || ''} maxLength={1000} autoSize={{minRows:3,maxRows:6}} disabled={busy} onChange={v=>handover('handoverReason',v)}/></SbField>
   {d.handoverError && <Alert type="error" message={d.handoverError}/>}
  </Modal>
  <Modal title={x.selfManaged?'修改任务要求':'申请修改任务要求'} open={Boolean(draft)} width={820} maskClosable={false} closable={!busy} destroyOnHidden onCancel={()=>invoke('closeRequirements')} footer={<><Button disabled={busy} onClick={()=>invoke('closeRequirements')}>取消编辑</Button><Button type="primary" disabled={blocked} loading={busy} onClick={()=>invoke(d.requirementsPreview?'submitRequirements':'previewRequirements')}>{d.requirementsPreview?(x.selfManaged?'确认并保存要求':'确认并提交变更申请'):'核对变更差异'}</Button></>}>
   {draft && <><Alert type="info" message={x.selfManaged?'先核对差异，再保存本人任务要求；变更会留存记录。':'先核对差异，再提交责任人确认；申请不会立即覆盖当前要求。'}/>
    <SbField label="任务标题" required><Input aria-label="变更任务标题" maxLength={500} value={draft.title} disabled={busy} onChange={e=>input('title',e.target.value)}/></SbField>
    {[['任务描述','description',8000],['交付物','deliverable',4000],['验收标准','acceptance_criteria',4000]].map(([label,key,max])=><SbField key={key} label={label}><SbTextarea aria-label={`变更${label}`} value={draft[key] || ''} maxLength={max} autoSize={{minRows:2,maxRows:6}} disabled={busy} onChange={v=>input(key,v)}/></SbField>)}
    <div className="task-governance-fields"><SbField label="截止时间" required><DatePicker aria-label="变更截止时间" showTime={{format:'HH:mm'}} format="YYYY-MM-DD HH:mm" allowClear={false} value={d.requirementsDate?dayjs(`${d.requirementsDate}T${d.requirementsTime}`):null} disabled={busy} onChange={v=>{if(v){input('date',v.format('YYYY-MM-DD'));input('time',v.format('HH:mm'));}}}/></SbField><SbField label="优先级"><Select aria-label="变更优先级" value={d.requirementsPriority} options={(d.requirementsPriorities||[]).map((label,value)=>({label,value}))} disabled={busy} onChange={v=>input('priority_code',v)}/></SbField></div>
    {x.raw.protocol_version!==2&&<><h4>推进清单</h4>{draft.checklist.map((item,i)=><div className="task-checklist-editor" key={item.item_id}><SbField label={`推进项 ${i+1}`} required><Input aria-label={`变更推进项 ${i+1}`} value={item.title} maxLength={500} disabled={busy} onChange={e=>input('title',e.target.value,item.item_id)}/></SbField><SbField label="本项验收标准" required><SbTextarea aria-label={`变更清单标准 ${i+1}`} value={item.acceptance_criteria} maxLength={2000} disabled={busy} onChange={v=>input('acceptance_criteria',v,item.item_id)}/></SbField><Button disabled={busy} onClick={()=>invoke('removeRequirementItem',{dataset:{id:item.item_id}})}>移除推进项 {i+1}</Button></div>)}
    <Button disabled={busy || draft.checklist.length>=100} onClick={()=>invoke('addRequirementItem')}>添加变更推进项</Button></>}
    <SbField label="变更原因" required><SbTextarea aria-label="变更原因" value={d.requirementsReason || ''} maxLength={1000} disabled={busy} autoSize={{minRows:3,maxRows:6}} onChange={v=>input('reason',v)}/></SbField>
    {d.requirementsPreview && <><h4>请核对变更前后</h4><Diff rows={d.requirementsPreview}/></>}{d.requirementsError && <Alert type="error" message={d.requirementsError}/>}</>}
  </Modal>
 </section>;
}
