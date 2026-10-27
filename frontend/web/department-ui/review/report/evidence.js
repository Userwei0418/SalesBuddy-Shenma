import {formatDecimal,timestamp,REVIEW_ACTIVITIES} from '../overview-model.js';
import {metricLabel,missingLabel,reportText,measureText} from './model.js';

const decimal=value=>typeof value==='string' && /^-?\d{1,24}(?:\.\d{1,8})?$/.test(value);
const object=value=>value && typeof value==='object' && !Array.isArray(value);
function exactWan(value){
  // Divide the frozen decimal string without binary floating point or rounding
  // away sub-yuan precision; suppress trailing zeros beyond two decimals.
  return formatDecimal(value,{shift:4,places:(value.split('.')[1] || '').length+4}).replace(/(\.\d{2}\d*?)0+$/,'$1');
}
export function evidenceMeasureText(measure){
  if(measure?.coverage==='complete' && measure.role!=='rate' && measure.unit==='元' && (!measure.currency || measure.currency==='CNY') && decimal(measure.value))return `${exactWan(measure.value)} 万元${measure.tax_basis==='tax_inclusive'?' · 含税':measure.tax_basis==='tax_exclusive'?' · 不含税':''}`;
  return measureText(measure,{withUnit:true});
}
const coverageLabel=value=>({complete:'数据完整',partial:'数据未齐',unavailable:'暂不可用'})[value];
export const evidenceKindLabel=kind=>({metric_query:'指标统计',target:'目标依据',objective:'目标',key_result:'关键结果',task:'任务记录',activity:'业务活动',opportunity:'商机依据',baseline:'历史基线'})[kind] || '冻结材料';
const statusLabel=value=>({open:'推进中',won:'已赢单',lost:'已丢单',pending:'待处理',in_progress:'进行中',completed:'已完成',cancelled:'已取消',effective:'已生效',no_target:'未设置目标',permission_denied:'无目标查看权限',confirmed:'已确认',withdrawn:'已撤回'})[value];
function periodLabel(value){
  if(!object(value))return null;
  if(Number.isInteger(value.year) && [1,2,3,4].includes(value.quarter))return `${value.year}年 Q${value.quarter}`;
  const date=value=>typeof value==='string' && /^\d{4}-\d{2}-\d{2}/.test(value)?value.slice(0,10):null;
  if(date(value.start) && date(value.end))return `${date(value.start)} 至 ${date(value.end)}`;
  return date(value.anchor_date);
}

// Only allowlisted business fields from the immutable evidence are rendered.
// IDs, hashes, internal references and unrecognized JSON never become UI text.
export function evidenceView(row,facts){
  let payload;
  try{payload=JSON.parse(row.text);}catch{
    return {title:evidenceKindLabel(row.kind),fields:[],text:/^\s*[\[{]/.test(row.text || '')?null:reportText(row.text),notice:/^\s*[\[{]/.test(row.text || '')?'此项冻结材料的可读摘要暂不可用。':null};
  }
  if(!object(payload))return {title:evidenceKindLabel(row.kind),fields:[],notice:'此项冻结材料的可读摘要暂不可用。'};
  const fields=[],add=(label,value)=>{if((typeof value==='string' && value!=='') || (typeof value==='number' && Number.isFinite(value)))fields.push({label,value:String(value)});};
  const related=(facts?.measures || []).filter(item=>item.role!=='rate' && item.source_refs?.includes(row.source_ref) && (!payload.metric_code || item.metric_code===payload.metric_code));
  const measure=related.length && related.every(item=>item.unit===related[0].unit && item.currency===related[0].currency)?related[0]:null;
  const financial=['forecast_collection','collection_ppl','forecast_recognized','collection','recognized'].includes(payload.metric_code);
  const unit=measure?.unit || (typeof payload.unit==='string'?payload.unit:null) || (financial?'元':payload.metric_code==='opportunity_count'?'个':payload.metric_code==='visit_count'?'条':null);
  const currency=measure?.currency || payload.currency;
  const money=(label,value)=>add(label,!decimal(value)?'未填写或不可用':unit==='元' && (!currency || currency==='CNY')?`${exactWan(value)} 万元`:`${formatDecimal(value,{places:(value.split('.')[1] || '').length})}${unit?` ${unit}`:'（单位未注明）'}${typeof currency==='string' && currency!=='CNY'?` · ${currency}`:''}`);
  if(payload.metric_code)add('指标',metricLabel(payload.metric_code,facts));
  if(payload.activity_code)add('活动类型',REVIEW_ACTIVITIES.find(item=>item.code===payload.activity_code)?.label || '业务活动');
  add('统计周期',periodLabel(payload.period) || periodLabel(payload));
  if(payload.scope){
    const selected=facts?.scope?.subjects?.filter(item=>item.scope_type===payload.scope.scope_type && item.id===payload.scope.record_id) || [];
    add('归属',selected.length===1?selected[0].label:'归属待核对');
  }
  add('数据状态',coverageLabel(payload.coverage));
  if(Object.hasOwn(payload,'value'))money(payload.coverage && payload.coverage!=='complete'?'完整统计值':'冻结值',payload.value);
  if(payload.value==null && payload.known_subtotal!=null)money('已知小计（非完整总额）',payload.known_subtotal);
  if(Array.isArray(payload.missing_reasons) && payload.missing_reasons.length)add('待补充或核对',payload.missing_reasons.map(missingLabel).join('；'));
  for(const [key,label] of [['missing_amount_count','缺金额数量'],['unknown_eligibility_count','纳入条件待核对数量']])if(Number.isSafeInteger(payload[key]) && payload[key]>=0)add(label,`${payload[key]} 条`);
  for(const [key,label] of [['title','名称'],['name','名称'],['customer_name','客户'],['opportunity_name','商机'],['task_title','任务'],['summary_excerpt','内容摘要'],['note','备注'],['source_ref_text','依据说明']])if(typeof payload[key]==='string')add(label,reportText(payload[key]));
  add('状态',statusLabel(payload.status));
  for(const [key,label] of [['occurred_at','发生时间'],['interaction_at','跟进时间'],['completed_at','完成时间'],['due_at','截止时间'],['forecast_updated_at','预测填报时间']])if(typeof payload[key]==='string')add(label,timestamp(payload[key]));
  // Some older financial evidence embeds the adopted actual record.
  const actual=object(payload.actual)?payload.actual:null;
  if(actual){
    add('实际业绩周期',periodLabel(actual));
    for(const [key,label] of [['amount','已登记金额'],['collection_amount','已登记回款'],['recognized_amount','已登记确收']])if(Object.hasOwn(actual,key))money(label,actual[key]);
    add('登记状态',statusLabel(actual.status));
  }
  if(row.kind==='opportunity' && !fields.some(field=>field.label==='商机' || field.label==='名称'))add('材料说明','此冻结记录用于核对商机和季度预测来源；快照未提供可显示的商机名称。');
  if(row.state_kind==='declaration' || payload.counts_toward_financial_actuals===false)add('说明','贡献声明或计划不会自动计入实际回款、确收。');
  if(payload.summary_truncated)add('摘要说明','原冻结摘要有截取，仅展示当时保留的内容。');
  return {title:evidenceKindLabel(row.kind),fields,notice:fields.length?null:'此项冻结材料未提供可读的业务摘要；关联指标和材料状态仍以上方冻结值为准。'};
}
