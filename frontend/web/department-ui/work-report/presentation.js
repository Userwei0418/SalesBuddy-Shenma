import {clone} from './state.js';
import {sourceDisplayText} from './source-display.js';
import {formatDecimal} from '../review/overview-model.js';
import {displayItem} from './content.js';
import {salesWeekView,actualDate,WEEK_SECTION_LABELS} from './sales-week.js';

export const SOURCE_LABELS={visit:'拜访跟进',opportunity:'商机进展',task:'任务完成',risk:'风险情况',actual:'正式回款'};
export const PERIOD_BASIS_LABELS={occurred_in_period:'所选周期内已发生',current_at_cutoff:'截至统计截止时间',current_at_snapshot:'取数时点的当前状态',planned_in_period:'周期内计划，尚未发生'};
export const fromPeriod=value=>value==='two_weeks'?'biweek':value;
export const toPeriod=value=>value==='biweek'?'two_weeks':value;
export const dateLabel=value=>value?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value)):'—';
const options=values=>Object.entries(values).map(([value,label])=>({value,label}));
export const STYLE_CHOICES={
  groupBy:options({topic:'按主题',customer:'按客户',opportunity:'按商机',member:'按成员'}),
  tone:options({concise:'简洁',formal:'正式',narrative:'叙述'}),
  length:options({short:'精简',medium:'适中',long:'详细'}),
  voice:options({first_person:'本人',team:'团队'}),language:options({'zh-CN':'中文'}),
};
export const scopeOptions=value=>value.available_scope_kinds.map(kind=>({value:kind,label:{self:'本人',member:'按成员',team:'按部门',workspace:'全部可见范围'}[kind]}));
export function schemeEditor(view) {
  const config=clone(view.config);
  return {name:view.name,sources:config.default_sources,period:fromPeriod(config.default_period),sections:config.sections,groupBy:config.group_by,
    style:{...config.style,focus:config.style.focus.join('\n')},references:config.reference_texts.map(ref=>({...ref,originalText:ref.text})),baseConfig:config};
}
export function schemeConfig(editor) {
  const focus=editor.style.focus.split('\n').map(text=>text.trim()).filter(Boolean);
  if(focus.length>20 || focus.some(text=>text.length>240))throw Error('写作要求最多20条，每行最多240字');
  if(editor.references.length>5)throw Error('参考范文最多5篇');
  const references=editor.references.filter(ref=>ref.text.trim()).map(({originalText,...ref})=>({...ref,version_no:ref.version_no+(originalText!==undefined && originalText!==ref.text?1:0)}));
  if(references.some(ref=>ref.text.length>20000) || references.reduce((sum,ref)=>sum+ref.text.length,0)>60000)throw Error('单篇范文最多20000字，合计最多60000字');
  return {...editor.baseConfig,default_period:toPeriod(editor.period),default_sources:[...editor.sources],sections:clone(editor.sections),group_by:editor.groupBy,style:{...editor.style,focus},reference_texts:references};
}
export function materialView(snapshot,config,enabled) {
  const selected=snapshot.coverage.filter(item=>item.selected);
  const partial=selected.some(item=>item.status!=='complete');
  const weekly=salesWeekView(snapshot.sales_week_facts,config.sections);
  return {periodLabel:snapshot.period.label,scopeLabel:snapshot.scope.label,asOfLabel:`材料准备于 ${dateLabel(snapshot.data_as_of)}`,
    cutoffLabel:snapshot.period.cutoff?`本期已发生事实统计截至 ${dateLabel(snapshot.period.cutoff)}`:'',
    coverageState:snapshot.status==='failed'?'failed':partial?'partial':'complete',coverageMessage:selected.flatMap(item=>item.missing_reasons).join('；'),
    sources:selected.map(item=>({id:item.source_type,label:SOURCE_LABELS[item.source_type],description:item.missing_reasons.join('；') || '按已授权范围和业务时间准备',complete:item.status==='complete',canRead:item.included>0,
      countLabel:item.total==null?`已纳入 ${item.included} 条 · 总量未知`:`已纳入 ${item.included} / ${item.total} 条`})),
    sections:config.sections.filter(item=>item.enabled),styleLabel:[STYLE_CHOICES.tone.find(item=>item.value===config.style.tone)?.label,STYLE_CHOICES.length.find(item=>item.value===config.style.length)?.label,config.style.audience].filter(Boolean).join(' · '),
    weekly,canGenerate:enabled && snapshot.status==='ready' && weekly?.supported!==false,notices:snapshot.selection.supplements.length?['已纳入用户补充材料，未计入系统统计。']:[],
  };
}
export const metricViews=metrics=>metrics.map(metric=>({id:metric.metric_id,label:metric.label,display:metric.display_value,complete:metric.complete,note:[PERIOD_BASIS_LABELS[metric.period_basis],metric.definition].filter(Boolean).join(' · ')}));
// Read-only amount presentation; original frozen values and prose remain intact.
export function materialMetricViews(metrics) {
  return metricViews(metrics).map((view,index)=>{
    const metric=metrics[index];
    if (!metric.complete) return {...view,missingText:'数据未齐',note:[metric.display_value,view.note].filter(Boolean).join(' · ')};
    const exact=typeof metric.value==='string' && /^-?\d{1,24}(?:\.\d{1,8})?$/.test(metric.value);
    if (metric.kind!=='amount' || !exact) return view;
    if (metric.currency!=='CNY' || !['元','原币'].includes(metric.unit)) {
      return {...view,unit:metric.display_value===metric.value?(['元','原币'].includes(metric.unit)?metric.currency:metric.unit):undefined};
    }
    const places=Math.max(2,(metric.value.split('.')[1] || '').length);
    return {...view,display:formatDecimal(metric.value,{shift:4,places:2}),unit:'万元',
      originalAmount:formatDecimal(metric.value,{places})+' 元'};
  });
}
export function sourceViews(items) {
  return items.map(source=>{
    const basis={occurred:'已发生',current:'当前状态',planned:'计划，尚未发生'}[source.state_kind];
    const date=actualDate(source.actual_lineage) || (source.state_kind==='current'?`取数时点 ${dateLabel(source.observed_at)}`
      :source.business_at?`${source.state_kind==='planned'?'计划日期':'业务日期'} ${dateLabel(source.business_at)}`
      :`${source.state_kind==='planned'?'计划日期':'业务日期'}未登记 · 材料准备于 ${dateLabel(source.observed_at)}`);
    const label=source.source_type==='task' && source.state_kind==='planned'?'任务计划':SOURCE_LABELS[source.source_type] || source.source_type;
    return {ref:source.source_id,title:source.title,text:sourceDisplayText(source.text,source.source_type==='opportunity'?['opportunity:display']:[]),kindLabel:[label,basis].filter(Boolean).join(' · '),owner:source.contributors.map(p=>p.name).join('、'),date,canOpen:false};
  });
}
export const reportRow=document=>({id:document.id,title:document.title,periodLabel:document.period.label,scopeLabel:document.scope.label,schemeLabel:document.scheme.name,status:document.status,updatedLabel:dateLabel(document.updated_at)});
export const supplementView=item=>({ref:`user:${item.id}`,title:item.section_id?`用户补充 · ${WEEK_SECTION_LABELS[item.section_id] || '所选章节'}`:'用户补充材料',
  kindLabel:item.declared_kind==='plan'?'用户补充计划，未核实':'用户补充',text:item.text,canOpen:false});
export function revisionText(content,metrics) {
  const body=content.sections.map(section=>`${section.title}\n${section.items.map(item=>{
    const text=`${item.kind==='manual'?'[人工编辑] ':item.kind==='user_supplement'?'[用户补充] ':''}${displayItem(item,metrics).text}`;
    return [text,...(item.source_notes || []).map(note=>`[来源说明 · ${note.title}]\n${note.notice_text}`)].join('\n');
  }).join('\n\n')}`).join('\n\n');
  const notes=content.sections.flatMap(section=>section.items.flatMap(item=>(item.source_notes || []).map(note=>`[${section.title} · ${note.title}]\n${note.text}`)));
  return notes.length?`${body}\n\n事实与来源（冻结材料附录）\n${notes.join('\n\n')}`:body;
}

export async function allSchemes(service,signal) {
  const items=[],seen=new Set();let offset=0;
  do {
    if(signal?.aborted)throw Object.assign(Error('读取已取消'),{name:'AbortError'});
    const page=await service.schemes({limit:100,offset});
    if(signal?.aborted)throw Object.assign(Error('读取已取消'),{name:'AbortError'});
    for(const item of page.items){if(!item.id || seen.has(item.id))throw Error('方案列表已变化，请重新读取');seen.add(item.id);items.push(item);}
    if(!page.has_more)return items;
    if(!Number.isInteger(page.next_offset) || page.next_offset<=offset)throw Error('方案列表响应不完整');
    offset=page.next_offset;
  }while(true);
}
