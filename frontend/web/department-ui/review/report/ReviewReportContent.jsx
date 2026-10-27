import React,{useState} from 'react';
import {Alert,Button,Drawer,Empty,Tag} from 'antd';
import {timestamp} from '../overview-model.js';
import {DAILY,reviewContentProblem,displayStatement,statementEvidence,measureText,roleLabel,stateLabel,missingLabel,metricLabel,measureSubjectLabel} from './model.js';
import './review-report.css';
import {evidenceView,evidenceMeasureText} from './evidence.js';

function Evidence({statement,facts,onClose}){
  const selected=statementEvidence(statement,facts);
  return <Drawer open title="本报告的冻结依据" onClose={onClose} width="min(760px,96vw)" destroyOnHidden>
    <p className="rr-muted">取数于 {timestamp(facts.data_as_of)}。以下只展示本报告已授权的冻结证据，不读取当前概览来替换历史数据。</p>
    {selected.measures.map(row=><section className="rr-evidence" key={row.measure_id}><h3>{roleLabel(row.role)} · {metricLabel(row.metric_code,facts)}</h3><p className="rr-measure-subject">{measureSubjectLabel(row,facts)}</p><strong>{evidenceMeasureText(row)}</strong><p className="rr-muted">口径版本 {row.definition_version}{row.comparison_boundary?` · 比较边界 ${timestamp(row.comparison_boundary)}`:''}</p>
      {row.missing_reasons?.length>0 && <p>{row.missing_reasons.map(missingLabel).join('；')}</p>}
      {row.baseline && <p className="rr-muted">基线边界 {timestamp(row.baseline.boundary_at)} · 采集于 {timestamp(row.baseline.captured_at)}</p>}
    </section>)}
    {selected.evidence.map(row=><FrozenEvidence key={row.source_ref} row={row} facts={facts}/>)}
  </Drawer>;
}

export function FrozenEvidence({row,facts}){
  const view=evidenceView(row,facts);
  return <section className="rr-evidence"><h3>{view.title}</h3><div><Tag>{stateLabel(row.state_kind)}</Tag>{row.target_status && <Tag color={row.target_status==='effective'?'green':'orange'}>{row.target_status==='effective'?'生效目标':'待审目标 · 不作为正式目标'}</Tag>}</div>
    <p className="rr-muted">观察于 {timestamp(row.observed_at)}</p>
    {view.fields.length>0 && <dl className="rr-evidence-fields">{view.fields.map((field,index)=><div key={index}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>}
    {view.text && <p className="rr-evidence-text">{view.text}</p>}{view.notice && <p className="rr-muted">{view.notice}</p>}
  </section>;
}

function Statement({label,statement,facts,onEvidence,suggestion=false}){
  return <div className="rr-statement"><div className="rr-statement-label"><b>{label}</b>{suggestion && <Tag color="purple">待人工确认</Tag>}</div><p>{displayStatement(statement,facts)}</p><Button type="link" size="small" onClick={()=>onEvidence(statement)}>查看依据</Button></div>;
}

// Read only: ordinary sections remain owned by the existing reader/editor.
// The typed token text and references are never modified by this renderer.
export default function ReviewReportContent({content,facts,compact=false,itemIndex,groupIndex}){
  const [selected,setSelected]=useState(null);
  const choose=statement=>setSelected({statement,facts,content});
  if(!content)return null;
  const problem=reviewContentProblem(content,facts);
  if(problem || !facts)return <Alert type="error" message={problem || '本报告的冻结依据不可用，请重新读取'}/>;
  const daily=content.contract_version===DAILY;
  const index=daily?itemIndex:groupIndex,rows=daily?content.items:content.groups;
  if(index!==undefined && (!Number.isInteger(index) || index<0 || index>=rows.length))return <Alert type="error" message="所选报告条目不存在，请重新打开"/>;
  // Presentation slices never bypass full typed-content / frozen-fact checks.
  const displayed=rows.map((row,originalIndex)=>({row,index:originalIndex})).filter(entry=>index===undefined || entry.index===index);
  return <section className={`rr-content${compact?' is-compact':''}`} aria-label={daily?'AI 日总结内容':'指标与 KR 周报回顾'}>
    <p className="rr-muted">报告取数时点 {timestamp(facts.data_as_of)} · {facts.scope?.label}。当前库存与预测为该取数时点的状态，不代表历史期末值。</p>
    {daily?<ol className="rr-daily-items">{displayed.map(({row:item,index})=><li key={index} className="rr-card"><h3>{String(index+1).padStart(2,'0')}</h3><Statement label="事实" statement={item.fact} facts={facts} onEvidence={choose}/><Statement label="影响或风险" statement={item.impact_or_risk} facts={facts} onEvidence={choose}/><Statement label="下一步建议" statement={item.next_step} facts={facts} onEvidence={choose} suggestion/></li>)}</ol>
      :content.groups.length?displayed.map(({row:group,index})=>{
        const frozen=facts.groups[index];
        return <section className="rr-card" key={group.group_id}><h3>{frozen.key_result_id?frozen.title:metricLabel(frozen.metric_code,facts)}</h3><p className="rr-muted">{frozen.key_result_id?'关联 KR':'按指标归组'} · {metricLabel(frozen.metric_code,facts)}</p>
          {frozen.measure_refs?.length>0 && <dl className="rr-measures">{frozen.measure_refs.map(id=>{const measure=facts.measures.find(row=>row.measure_id===id);return <div key={id}><dt><span className="rr-measure-subject">{measureSubjectLabel(measure,facts)}</span><span>{roleLabel(measure?.role || id)}</span></dt><dd>{measureText(measure,{withUnit:true})}</dd></div>;})}</dl>}
          {!group.progress.length && !group.risks.length && !group.next_week_tasks.length && <p className="rr-muted">本组尚未形成回顾内容。</p>}
          {group.progress.map((row,i)=><Statement key={`p${i}`} label={stateLabel(row.kind)} statement={row} facts={facts} onEvidence={choose}/>)}
          {group.risks.map((row,i)=><Statement key={`r${i}`} label="风险" statement={row} facts={facts} onEvidence={choose}/>)}
          {group.next_week_tasks.map((row,i)=><Statement key={`n${i}`} label="下周任务建议" statement={row} facts={facts} onEvidence={choose} suggestion/>)}
        </section>;
      }):<Empty description="未形成可展示的指标或 KR 分组"/>}
    {content.missing_data.length>0 && <details className="rr-missing"><summary>材料缺口 · {content.missing_data.length} 项</summary><ul>{content.missing_data.map(reason=><li key={reason}>{missingLabel(reason)}</li>)}</ul></details>}
    <p className="rr-muted">建议需要人工确认；任务完成或贡献声明不会自动增加回款、确收或 KR 进度。</p>
    {selected?.facts===facts && selected?.content===content && <Evidence statement={selected.statement} facts={facts} onClose={()=>setSelected(null)}/>}
  </section>;
}
