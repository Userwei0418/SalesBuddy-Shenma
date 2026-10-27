import React from 'react';
import {Alert, Button, Modal} from 'antd';
import {SbField} from '@shandiant/ui-react';
import {Input, SbTextarea} from '../ImeInput.jsx';

export default function ChecklistEditor({data:d,invoke}){
 const input=(key,value,id)=>invoke('inputChecklist',{dataset:{key,id},detail:{value}}),busy=Boolean(d.executionBusy);
 return <Modal title="维护推进清单" open={Array.isArray(d.checklistDraft)} width={760} maskClosable={false} closable={!busy} onCancel={()=>invoke('closeChecklist')} destroyOnHidden footer={<><Button disabled={busy} onClick={()=>invoke('closeChecklist')}>取消</Button><Button type="primary" loading={busy} disabled={d.executionLoading || !!d.executionError} onClick={()=>invoke('saveChecklist')}>保存清单</Button></>}>
  <p className="ds-muted">{d.checklistImpact}</p>
  <p className="ds-muted">保存后直接生效，并按新清单重算进度。整体交付要求保持不变。</p>
  {d.execution?.pause?.active && <Alert type="info" message="暂停期间可以调整计划；恢复后才能更新完成成果。"/>}
  {(d.checklistDraft||[]).map((item,index)=><div className="task-checklist-editor" key={item.item_id}>
   <SbField label={`推进项 ${index+1}`} required><Input aria-label={`清单标题 ${index+1}`} value={item.title} maxLength={500} disabled={busy} onChange={e=>input('title',e.target.value,item.item_id)}/></SbField>
   <SbField label="完成标准" required><SbTextarea aria-label={`清单完成标准 ${index+1}`} value={item.acceptance_criteria} maxLength={2000} autoSize={{minRows:2,maxRows:4}} disabled={busy} onChange={v=>input('acceptance_criteria',v,item.item_id)}/></SbField>
   <Button type="text" disabled={busy||index===0} onClick={()=>invoke('moveChecklist',{dataset:{id:item.item_id,delta:-1}})}>上移</Button><Button type="text" disabled={busy||index===(d.checklistDraft||[]).length-1} onClick={()=>invoke('moveChecklist',{dataset:{id:item.item_id,delta:1}})}>下移</Button><Button danger type="text" disabled={busy} onClick={()=>invoke('removeChecklist',{dataset:{id:item.item_id}})}>移除此项</Button>
  </div>)}
  <Button disabled={busy||(d.checklistDraft||[]).length>=100} onClick={()=>invoke('addChecklist')}>添加推进项</Button>
  <SbField label="调整原因" required><SbTextarea aria-label="清单调整原因" value={d.checklistReason||''} maxLength={1000} autoSize={{minRows:2,maxRows:4}} disabled={busy} onChange={v=>input('reason',v)}/></SbField>
  {d.checklistNeedsReview&&<Alert type="warning" message="请核对当前已保存清单，草稿中的增删会在下次保存时生效" description={<><ul>{(d.execution?.items||[]).map(item=><li key={item.item_id}>{item.title}：{item.acceptance_criteria}</li>)}</ul><Button disabled={busy||d.executionLoading} onClick={()=>invoke('confirmChecklistLatest')}>已核对，保留草稿继续编辑</Button></>}/>}
  {d.checklistError && <Alert type="error" message={d.checklistError}/>}
 </Modal>;
}
