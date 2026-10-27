import React from 'react';
import {LightweightTaskDialog} from './LightweightTaskPanel.jsx';
import {Alert,Button,Modal,Select} from 'antd';
import {SbField} from '@shandiant/ui-react';
import {SbTextarea} from '../ImeInput.jsx';
export default function TaskMigration({data:d,invoke}){
 const x=d.taskMigration||d.execution;
 if(x?.caps.adopt_v3)return <><Alert type="info" showIcon message="这条任务沿用原流程" description="可单独接入四状态任务流程，原反馈、清单与提交历史保留，不会自动通过验收。" action={<Button disabled={d.executionBusy||d.executionLoading||!!d.executionError} onClick={()=>invoke('openLightweightAdopt')}>接入轻量流程</Button>}/><LightweightTaskDialog data={d} invoke={invoke}/></>;
 if(x?.raw.requires_single_owner)return <Alert type="warning" showIcon message="历史任务有多名主责" description="请先在任务协调中确认唯一主执行人，再开启进度管理。原成员和历史记录不会自动改写。"/>;
 if(!x?.caps.initialize_execution)return null;
 return <><Alert type="info" showIcon message="这条任务沿用原流程" description="可为本任务开启自主清单和自办流程，原状态、成果及历史记录会保留。" action={<Button disabled={d.executionBusy||d.executionLoading||!!d.executionError} onClick={()=>invoke('openTaskMigration')}>开启任务进度管理</Button>}/>
  <Modal title="开启本任务的进度管理" open={Boolean(d.migrationOpen)} maskClosable={false} closable={!d.executionBusy} onCancel={()=>invoke('closeTaskMigration')} footer={<><Button disabled={d.executionBusy} onClick={()=>invoke('closeTaskMigration')}>取消</Button><Button type="primary" loading={d.executionBusy} onClick={()=>invoke('initializeTask')}>确认开启</Button></>}>
   <p>仅接入当前任务。已有完成记录保持原样，推进清单由执行人补充，系统不会补造完成成果。</p>
   {d.migrationResumeOptions?.length>0&&<SbField label="历史延期任务继续阶段" required><p>只展示服务端核实允许的阶段；没有原接收依据时需要执行人重新接受。</p><Select aria-label="历史延期任务继续阶段" placeholder="请选择继续阶段" value={d.migrationResumeIndex>=0?d.migrationResumeIndex:undefined} options={d.migrationResumeOptions.map((option,index)=>({value:index,label:option.label}))} disabled={d.executionBusy} onChange={value=>invoke('chooseMigrationResume',{detail:{value}})}/></SbField>}
   <SbField label="接入原因" required><SbTextarea aria-label="接入原因" value={d.migrationNote||''} maxLength={1000} autoSize={{minRows:3,maxRows:5}} disabled={d.executionBusy} onChange={value=>invoke('inputMigrationNote',{detail:{value}})}/></SbField>
   {d.migrationError&&<Alert type="error" message={d.migrationError}/>}</Modal></>;
}
