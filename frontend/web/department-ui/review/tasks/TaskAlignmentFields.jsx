import React, {useState} from 'react';
import {Alert, Button, DatePicker, Input, Radio, Select, Tag} from 'antd';
import dayjs from 'dayjs';
import {SbField} from '@shandiant/ui-react';
import {effectiveMetricCodes, objectPeriod, objectScope, targetMetricCode, targetSelectable} from './model.js';
import './tasks.css';

export function ContributionFields({value, onChange, metrics, codes, disabled, actual = false, error}) {
  const options = metrics.filter(metric => codes.includes(metric.code) && metric.active === true && metric.source_kind !== 'category').map(metric => ({value: metric.code, label: `${metric.name}（${metric.unit}）`}));
  const label = actual ? '实际贡献' : '期望贡献';
  if (!value) return options.length > 0 && <Button type="link" size="small" disabled={disabled} onClick={() => onChange({metric_code: '', value: '', note: ''})}>填写{label}（选填）</Button>;
  const metric = metrics.find(row => row.code === value.metric_code);
  return <div className="review-task-contribution" data-testid={actual ? 'task-actual-contribution' : 'task-expected-contribution'}>
    <div className="review-task-section-heading"><strong>{label} <small>选填</small></strong><Button type="text" size="small" disabled={disabled} onClick={() => onChange(null)}>移除</Button></div>
    <div className="review-task-contribution-grid"><Select aria-label={`${label}指标`} placeholder="选择指标" value={value.metric_code || undefined} options={options} disabled={disabled} onChange={metric_code => onChange({...value, metric_code})}/><Input aria-label={`${label}量`} inputMode="decimal" placeholder="填写数值" suffix={metric?.unit} value={value.value} disabled={disabled} onChange={event => onChange({...value, value: event.target.value})}/></div>
    <Input.TextArea aria-label={`${label}说明`} placeholder={actual ? '说明实际完成的量、依据或证据位置（必填）' : '说明期望达到的结果（选填）'} maxLength={2000} autoSize={{minRows: 2, maxRows: 5}} disabled={disabled} value={value.note} onChange={event => onChange({...value, note: event.target.value})}/>
    {error && <div className="review-task-error" role="alert">{error}</div>}
    <p className="review-task-help">贡献量仅作任务说明，不自动计入业绩或修改 KR 进展。</p>
  </div>;
}

export default function TaskAlignmentFields({draft, onChange, directory = {}, disabled, error}) {
  const {metrics=[],keyResults=[],targets=[],targetRows=targets,targetQuery, setTargetQuery, targetStatus='ready',targetError='',krStatus='ready',krError='',reload} = directory;
  const [scope,setScope] = useState('all');
  const codes = effectiveMetricCodes(draft,keyResults,targets), name = code => metrics.find(row=>row.code===code)?.name || code;
  const targetLabel = row => `${name(targetMetricCode(row))} · ${objectScope(row)} · ${objectPeriod(row)}${row.state==='pending_new'?' · 待首次审批（不可选）':row.state==='pending_change'?' · 调整待审批，关联原生效目标':''}`;
  const options = new Map();
  for(const row of [...targetRows,...targets.filter(row=>draft.target_ids.includes(row.id))]) {
    if(scope !== 'all' && (row.scope?.scope_type || row.scope_type) !== scope && !draft.target_ids.includes(row.id))continue;
    const value = row.id || row.row_key;
    if(value)options.set(value,{value,label:targetLabel(row),disabled:!targetSelectable(row,metrics)});
  }
  return <section className="review-task-alignment" aria-label="任务指标与目标" data-testid="task-alignment-fields">
    <div className="review-task-section-heading"><h3>任务支持的目标</h3><span>选择实际目标，明确任务用途</span></div>
    <Radio.Group aria-label="任务对齐方式" value={draft.basis} disabled={disabled} onChange={event=>onChange({basis:event.target.value,target_ids:[],key_result_ids:[],other_reason:'',expected_contribution:null})} options={[{value:'objects',label:'关联目标 / KR'},{value:'other',label:'其他原因'}]}/>
    {draft.basis==='other' ? <SbField label="其他原因" required help="说明此任务为什么暂不关联业绩目标或 KR。"><Input.TextArea aria-label="任务其他原因" placeholder="例如：开展团队培训，为后续业务推进做准备" value={draft.other_reason} disabled={disabled} autoSize={{minRows:3,maxRows:7}} onChange={event=>onChange({...draft,other_reason:event.target.value})}/><span className="review-task-help">{[...String(draft.other_reason || '').trim()].length} / 2000 字</span></SbField> : <>
      <SbField label="业绩目标" help="选择已生效的目标，可跨周期多选。浏览周期不会改变截止时间或清除已选目标。">
        {targetQuery && <div className="review-task-target-filter"><Select aria-label="目标周期类型" value={targetQuery.period_type} disabled={disabled} options={[{value:'week',label:'周目标'},{value:'month',label:'月目标'},{value:'quarter',label:'季度目标'},{value:'year',label:'年目标'}]} onChange={period_type=>setTargetQuery({...targetQuery,period_type})}/><DatePicker aria-label="目标周期日期" allowClear={false} value={dayjs(targetQuery.anchor_date)} disabled={disabled} onChange={value=>{if(value?.isValid())setTargetQuery({...targetQuery,anchor_date:value.format('YYYY-MM-DD')});}}/><Select aria-label="目标归属类型" value={scope} disabled={disabled} options={[{value:'all',label:'全部归属'},{value:'person',label:'个人目标'},{value:'team',label:'团队目标'},{value:'department',label:'部门目标'}]} onChange={setScope}/></div>}
        <Select mode="multiple" aria-label="任务关联业绩目标" placeholder="按指标、所属人员或团队搜索目标（可多选）" value={draft.target_ids} options={[...options.values()]} optionFilterProp="label" disabled={disabled || targetStatus!=='ready'} loading={targetStatus==='loading'} maxCount={20} onChange={target_ids=>onChange({...draft,target_ids})}/>
        {targetStatus==='loading' && <p className="review-task-help" role="status">正在读取所选周期目标{directory.targetTotal==null?'…':`（${directory.targetLoaded} / ${directory.targetTotal}）`}</p>}
        {targetStatus==='ready' && !targetRows.length && <p className="review-task-help">此周期暂无可查看的业绩目标。可切换周期、选择 KR，或填写其他原因。</p>}
        {targetError && <Alert type="warning" showIcon message={targetError} action={reload && <Button disabled={disabled} onClick={reload}>重新读取目标</Button>}/>}
      </SbField>
      <SbField label="支持的 KR" help="按真实周期与归属选择；只列出当前允许关联的 KR。"><Select mode="multiple" aria-label="任务关联 KR" placeholder="选择 KR（可多选）" value={draft.key_result_ids} options={keyResults.filter(row=>row.linkable===true || draft.key_result_ids.includes(row.id)).map(row=>({value:row.id,label:`${row.title || '已关联 KR'} · ${objectScope(row)} · ${objectPeriod(row)}`,disabled:row.linkable!==true}))} optionFilterProp="label" disabled={disabled || krStatus!=='ready'} loading={krStatus==='loading'} maxCount={20} onChange={key_result_ids=>onChange({...draft,key_result_ids})}/>
        {krStatus==='loading' && <p className="review-task-help" role="status">正在读取 KR{directory.krTotal==null?'…':`（${directory.krLoaded} / ${directory.krTotal}）`}</p>}
        {krError && <Alert type="warning" showIcon message={krError} action={reload && <Button disabled={disabled} onClick={reload}>重新读取 KR</Button>}/>}
      </SbField>
      {codes.length>0 && <div className="review-task-derived"><span>关联对象对应指标：</span>{codes.map(code=><Tag key={code}>{name(code)}</Tag>)}</div>}
      <ContributionFields value={draft.expected_contribution} onChange={expected_contribution=>onChange({...draft,expected_contribution})} metrics={metrics} codes={codes} disabled={disabled}/>
    </>}
    {error && <div className="review-task-error" role="alert">{error}</div>}
  </section>;
}

export function TaskAlignmentSummary({alignment, metrics=[], keyResults=[]}) {
  if(!alignment)return null;
  const name=code=>metrics.find(row=>row.code===code)?.name || code;
  const v2=alignment.contract_version===2, targets=alignment.targets || [], krs=alignment.key_results || keyResults.filter(row=>(alignment.key_result_ids || []).includes(row.id));
  return <section className="review-task-alignment" aria-label="任务对齐关系" data-testid="task-alignment-summary"><h3>任务支持的目标</h3>
    {!v2 && <p className="review-task-help">历史任务按原指标分类保留。编辑时可明确选择目标、KR 或填写其他原因。</p>}
    {v2 && alignment.basis==='other' && <div className="review-task-other-reason"><strong>其他原因</strong><p>{alignment.other_reason}</p></div>}
    {targets.length>0 && <ul className="review-task-kr-list">{targets.map(row=><li key={row.id}><strong>{name(targetMetricCode(row))}</strong><div>{objectScope(row)} · {objectPeriod(row)}{row.version_no!=null?` · 第 ${row.version_no} 版`:''}</div></li>)}</ul>}
    {krs.length>0 && <ul className="review-task-kr-list">{krs.map(row=><li key={row.id}><strong>{row.title || '已关联 KR'}</strong><div>{objectScope(row)} · {objectPeriod(row)}{row.version_no!=null?` · 第 ${row.version_no} 版`:''}</div></li>)}</ul>}
    {alignment.restricted_target_count>0 && <p className="review-task-help">另有 {alignment.restricted_target_count} 项业绩目标受查看权限限制。</p>}
    {alignment.restricted_key_result_count>0 && <p className="review-task-help">另有 {alignment.restricted_key_result_count} 项 KR 受查看权限限制。</p>}
    {!!alignment.metric_codes?.length && <div className="review-task-derived"><span>{v2?'关联对象对应指标：':'历史指标分类：'}</span>{alignment.metric_codes.map(code=><Tag key={code}>{name(code)}</Tag>)}</div>}
    {[['期望贡献',alignment.expected_contribution],['实际贡献 · 已申报',alignment.actual_contribution]].filter(([,value])=>value).map(([label,value])=><div className="review-task-summary-contribution" key={label}><strong>{label}</strong><span>{name(value.metric_code)} · {value.value} {value.unit || ''}</span>{value.note && <p>{value.note}</p>}</div>)}
    {(alignment.expected_contribution || alignment.actual_contribution) && <p className="review-task-help">贡献声明不自动计入业绩或修改 KR 进展。</p>}
  </section>;
}
