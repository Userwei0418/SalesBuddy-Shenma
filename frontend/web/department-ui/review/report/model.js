import {calendarHint,stable,clone} from '../../work-report/state.js';
import {todayKey} from '../../pc/period.js';
import {composeSchemes} from '../../work-report/default-schemes.js';
import {REVIEW_METRICS,formatDecimal,percentage,isV2ReviewSelection,normalizeReviewSelection,matchingReviewScope} from '../overview-model.js';

export const DAILY='work_report.review_daily.v1',WEEKLY='work_report.review_weekly.v1';
const TOKEN=/\{\{metric:([a-z][a-z0-9_.-]{0,95})\}\}/g;
const DECIMAL=/^-?\d{1,24}(?:\.\d{1,8})?$/;
const strings=value=>Array.isArray(value) && value.every(item=>typeof item==='string') && new Set(value).size===value.length;
const MISSING_LABELS={
  no_baseline:'无可信历史基线，不能计算变化',no_effective_target:'尚无生效目标',
  source_scope_not_fully_authorized:'来源权限不覆盖完整范围',source_unavailable:'相关来源暂不可用',
  task_alignment_missing:'部分任务尚未关联目标、关键结果或填写其他原因',task_key_result_restricted:'部分任务关联的关键结果不在可查看范围内',
  no_confirmed_progress:'尚无已确认的进展',department_projection_not_defined:'尚不支持按部门计算此项进展',
  forecast_requires_quarter_period:'预测指标需要按季度统计',forecast_source_permission_denied:'没有完整的预测来源读取权限',
  effective_target_identity_mismatch:'关联目标与当前生效目标不一致',actual_source_permission_denied:'没有完整的实际业绩来源读取权限',
  automatic_metric_projection_not_defined:'此指标尚不支持自动统计',no_actual_evidence:'尚无可核实的实际业绩材料',
  incomplete_actual_period:'实际业绩的期间数据不完整',missing_material:'缺少核实材料',tax_basis_unresolved:'税务口径尚未确认',
  source_confirmation_required:'来源数据存在冲突，需要核实',period_actual_not_allocated_to_selected_dates:'部分实际业绩尚未分配到所选日期',
  unknown_recorded_team_where_needed:'部分记录未登记发生时的团队归属',unknown_attribution:'部分记录归属尚未明确',
  creation_provenance_permission_denied:'没有商机创建来源的读取权限',unproven_import_creation_provenance:'导入商机的原始创建日期尚未核实',
  unknown_creation_date:'部分商机缺少可信的创建日期',unknown_interaction_at:'部分跟进缺少实际发生日期',
  unknown_eligibility:'部分商机的回款信心或赢单概率尚未明确',currency_unavailable:'部分金额的币种不支持当前汇总口径',missing_forecast_amount:'部分商机缺少预测金额',
  history_field_coverage_unverified:'历史变更字段的覆盖范围尚未核实',
};
export const missingLabel=reason=>MISSING_LABELS[reason] || (typeof reason==='string' && /[\u3400-\u9fff]/u.test(reason)?reason:'部分材料暂不可用，请查看依据并核实');
export const roleLabel=role=>({current:'当前值',forecast:'预测值',effective_target:'生效目标',gap:'距目标差额',rate:'预计达成率',day_change:'较昨日变化',week_change:'较周初变化',kr_progress:'KR 进展'})[role] || role;
export const stateLabel=state=>({occurred:'已发生事实',current:'当前状态',planned:'已登记计划',declaration:'贡献声明',fact:'事实',plan:'已登记计划',suggestion:'建议 · 待人工确认'})[state] || state;
const REPORT_METRIC_LABELS={collection:'实际回款',recognized:'实际确收',opportunity_count:'新增商机数',visit_count:'跟进记录数'};
export const metricLabel=(code,facts)=>REVIEW_METRICS.find(row=>row.code===code)?.label || REPORT_METRIC_LABELS[code] ||
  facts?.groups?.find(row=>row.metric_code===code && !row.key_result_id && /[\u3400-\u9fff]/u.test(row.title || ''))?.title || '业务指标';

// Attribution is display-only and comes exclusively from this measure's frozen
// metric_query evidence and the frozen subject directory, never ID namespaces,
// ordering, equal values, live account data, or another measure's references.
export function measureSubjectLabel(measure,facts){
  const unknown='归属待核对';
  if(!measure?.source_refs?.length)return unknown;
  const queries=[];
  for(const ref of measure.source_refs){
    const matches=(facts?.evidence || []).filter(row=>row.source_ref===ref);
    if(matches.length!==1)return unknown;
    if(matches[0].kind==='metric_query')queries.push(matches[0]);
  }
  if(!queries.length)return unknown;
  const subjects=facts?.scope?.subjects || [];
  let chosen;
  for(const query of queries){
    let payload;try{payload=JSON.parse(query.text);}catch{return unknown;}
    const scope=payload?.scope;
    if(payload?.metric_code!==measure.metric_code || !['team','person'].includes(scope?.scope_type) || typeof scope.record_id!=='string')return unknown;
    const matches=subjects.filter(row=>row.scope_type===scope.scope_type && row.id===scope.record_id);
    if(matches.length!==1 || typeof matches[0].label!=='string' || !matches[0].label.trim())return unknown;
    const subject=matches[0];
    if(chosen && (chosen.id!==subject.id || chosen.scope_type!==subject.scope_type))return unknown;
    chosen=subject;
  }
  return `${chosen.scope_type==='team'?'团队':'成员'} · ${chosen.label}`;
}


export function reviewContentProblem(content,facts){
  if(!content || ![DAILY,WEEKLY].includes(content.contract_version))return 'Review 正文版本暂不支持';
  if(!strings(content.missing_data))return 'Review 材料缺口响应不完整';
  let statements=[];
  if(content.contract_version===DAILY){
    if(!Array.isArray(content.items) || content.items.length<1 || content.items.length>3)return '日总结必须包含 1–3 条完整内容';
    for(const item of content.items){
      if(item?.next_step?.kind!=='suggestion' || item.next_step?.requires_confirmation!==true)return '下一步必须保留待人工确认标识';
      statements.push(item.fact,item.impact_or_risk,item.next_step);
    }
  }else{
    if(!Array.isArray(content.groups) || content.groups.some(row=>!row) || new Set(content.groups.map(row=>row.group_id)).size!==content.groups.length)return '周报分组响应不完整';
    for(const group of content.groups){
      if(typeof group.group_id!=='string' || !Array.isArray(group.progress) || group.progress.length>30 || !Array.isArray(group.risks) || group.risks.length>30 || !Array.isArray(group.next_week_tasks) || group.next_week_tasks.length>20)return '周报分组内容不完整';
      if(group.progress.some(row=>!['fact','current','plan','declaration'].includes(row?.kind)) || group.next_week_tasks.some(row=>row?.kind!=='suggestion' || row.requires_confirmation!==true))return '周报事实、计划或贡献声明标识不完整';
      statements.push(...group.progress,...group.risks,...group.next_week_tasks);
    }
  }
  for(const item of statements){
    if(!item || typeof item.text!=='string' || !item.text.trim() || item.text.length>2000 || !strings(item.source_refs) || !strings(item.measure_refs) || !(item.source_refs.length || item.measure_refs.length))return 'Review 正文缺少可回查的依据';
    const tokens=[...new Set([...item.text.matchAll(TOKEN)].map(match=>match[1]))].sort();
    if(stable(tokens)!==stable([...item.measure_refs].sort()) || item.text.replace(TOKEN,'').includes('{{metric:'))return 'Review 指标引用不完整';
  }
  if(!facts)return '';
  if(!['work_report.review_facts.v1','work_report.review_facts.v2'].includes(facts.contract_version) || !Array.isArray(facts.evidence) || !Array.isArray(facts.measures) || !Array.isArray(facts.groups) || !facts.data_as_of)return '报告的冻结 Review 事实不可用';
  if(facts.contract_version==='work_report.review_facts.v2'){
    const scope=facts.scope,anchor=facts.period?.anchor_date || String(facts.period?.start || facts.data_as_of).slice(0,10);
    const selection=scope && {scope:scope.kind,period_type:facts.period?.kind,anchor_date:anchor,expected_scope_revision:scope.scope_revision,
      ...(scope.kind==='team'?{team_ids:scope.subject_ids}:{}),...(scope.kind==='member'?{member_ids:scope.subject_ids}:{})};
    try{if(!selection || !selection.period_type)throw Error();matchingReviewScope(scope,selection);}catch{return '报告冻结范围不完整，请重新读取';}
  }
  if(facts.evidence.some(row=>!row?.source_ref || !['occurred','current','planned','declaration'].includes(row.state_kind)) || facts.measures.some(row=>!row?.measure_id) || facts.groups.some(row=>!row?.group_id))return '报告冻结引用不完整';
  const evidence=new Map(facts.evidence.map(row=>[row.source_ref,row])),measures=new Map(facts.measures.map(row=>[row.measure_id,row]));
  if(evidence.size!==facts.evidence.length || measures.size!==facts.measures.length)return '报告冻结引用重复，请重新读取';
  for(const measure of facts.measures){
    if(!['complete','partial','unavailable'].includes(measure.coverage) || !strings(measure.source_refs) || !measure.source_refs.length || measure.source_refs.some(ref=>!evidence.has(ref)))return '指标依据不属于此报告快照';
    if(!strings(measure.missing_reasons) || (measure.coverage==='complete'?(typeof measure.value!=='string' || !DECIMAL.test(measure.value) || measure.missing_reasons.length):(measure.value!==null || !measure.missing_reasons.length)))return '不完整统计不能作为完整指标';
    if(['day_change','week_change'].includes(measure.role) && measure.value!==null && (!measure.baseline || measure.baseline.boundary_at!==measure.comparison_boundary || measure.baseline.definition_version!==measure.definition_version || measure.baseline.continuous_coverage!==true || measure.baseline.same_authorized_population!==true || evidence.get(measure.baseline.source_ref)?.kind!=='baseline' || !measure.source_refs.includes(measure.baseline.source_ref)))return '变化指标缺少可信历史基线';
  }
  if(statements.some(item=>item.source_refs.some(ref=>!evidence.has(ref)) || item.measure_refs.some(ref=>!measures.has(ref))))return '正文引用不属于此报告快照';
  if(facts.groups.some(row=>!strings(row.source_refs) || !strings(row.measure_refs) || row.source_refs.some(ref=>!evidence.has(ref)) || row.measure_refs.some(ref=>!measures.has(ref))))return '周报分组引用不属于此报告快照';
  if(content.contract_version===WEEKLY && stable(content.groups.map(row=>row.group_id))!==stable(facts.groups.map(row=>row.group_id)))return '周报分组与冻结指标或 KR 不一致';
  return '';
}

export function measureText(measure,{withUnit=false}={}){
  if(!measure)return '统计暂不可用';
  if(measure.value==null || measure.coverage!=='complete')return measure.missing_reasons?.includes('no_baseline')?'无可信历史基线':measure.coverage==='partial'?'材料不完整':'暂不可用';
  if(typeof measure.value!=='string' || !DECIMAL.test(measure.value))return '统计格式不正确';
  if(!withUnit)return measure.value;
  if(measure.role==='rate')return `${percentage(measure.value)}（按比例换算）`;
  return `${formatDecimal(measure.value,{places:(measure.value.split('.')[1] || '').length})} ${measure.unit}${measure.currency && measure.unit!=='元'?` · ${measure.currency}`:''}${measure.tax_basis==='tax_inclusive'?' · 含税':measure.tax_basis==='tax_exclusive'?' · 不含税':''}`;
}
// Display aliases only; the saved prose and metric references remain untouched.
export function reportText(text){
  return String(text ?? '').replace(/(?<![a-zA-Z0-9_.])(?:forecast_recognized_exclusive|recognized_pipeline_exclusive|actual_recognized_exclusive|forecast_collection|forecast_recognized|collection_ppl|actual_collection|opportunity_count|visit_count)(?![a-zA-Z0-9_.])/g,code=>({forecast_recognized_exclusive:'预测不含税确收',recognized_pipeline_exclusive:'不含税确收 Pipeline',actual_recognized_exclusive:'实际不含税确收',actual_collection:'实际回款'}[code] || metricLabel(code)));
}
export function displayStatement(statement,facts){
  const measures=new Map((facts?.measures || []).map(row=>[row.measure_id,row]));
  return reportText(statement.text.replace(TOKEN,(_,id)=>measureText(measures.get(id))));
}
export function statementEvidence(statement,facts){
  const measures=(facts.measures || []).filter(row=>(statement.measure_refs || []).includes(row.measure_id));
  const refs=new Set([...(statement.source_refs || []),...measures.flatMap(row=>row.source_refs)]);
  return {measures,evidence:facts.evidence.filter(row=>refs.has(row.source_ref))};
}

export function reportSelection(selection,mode='daily'){
  const period=mode==='weekly'?'week':'day';
  if(!['daily','weekly'].includes(mode) || selection?.period_type!==period)throw Error(mode==='weekly'?'请切换到自然周后生成增强周报':'请切换到日报后生成 AI 日总结');
  if(!calendarHint(period,selection.anchor_date) || selection.anchor_date>todayKey())throw Error('请选择今天或过去的有效日期');
  const v2=isV2ReviewSelection(selection);
  const normalized=v2?normalizeReviewSelection(selection):selection;
  const scope=v2?{contract_version:'work_report.scope_selection.v2',kind:normalized.scope,
    ...(normalized.scope==='team'?{team_ids:normalized.team_ids}:{}),
    ...(normalized.scope==='member'?{member_ids:normalized.member_ids}:{}),
    ...(normalized.expected_scope_revision?{expected_scope_revision:normalized.expected_scope_revision}:{})}:{kind:normalized.scope};
  if(!v2 && scope.kind==='team')scope.team_id=normalized.team_id;
  if(!v2 && scope.kind==='member')scope.member_id=normalized.member_id;
  return {review_mode:mode,period:{kind:period,anchor_date:selection.anchor_date,timezone:'Asia/Shanghai'},scope,sources:[],supplements:[]};
}
export function generationPlan(selection,mode,options,actorId){
  const request=reportSelection(selection,mode);
  if(!options?.can_create)throw Error('当前账号没有报告生成权限');
  if(!options.generation_available)throw Error('AI 报告生成暂不可用');
  const v2=request.scope.contract_version==='work_report.scope_selection.v2';
  if(v2 && !/^[a-f0-9]{64}$/i.test(request.scope.expected_scope_revision || ''))throw Error('范围版本无效，请重新读取概览');
  if(!v2 && request.scope.kind==='member'){
    if(!actorId || request.scope.member_id!==actorId)throw Error('只能生成本人或实际管理团队的报告');
    request.scope={kind:'self'};
  }
  if(!options.available_scope_kinds?.includes(request.scope.kind))throw Error('只能生成本人或实际管理团队的报告');
  if(!v2 && !['self','team'].includes(request.scope.kind))throw Error('只能生成本人或实际管理团队的报告');
  if(v2 && request.scope.kind==='self' && (request.scope.team_ids || request.scope.member_ids))throw Error('本人范围不能同时选择成员或团队');
  if(v2 && request.scope.kind==='team' && (!request.scope.team_ids?.length || request.scope.member_ids))throw Error('请选择 1 至 100 个当前可管理的团队');
  if(v2 && request.scope.kind==='member' && (!request.scope.member_ids?.length || request.scope.team_ids))throw Error('请选择 1 至 100 个当前可查看的成员');
  const selectedIds=request.scope.kind==='team'?request.scope.team_ids:request.scope.kind==='member'?request.scope.member_ids:[];
  const directory=request.scope.kind==='team'?options.teams:options.members;
  if(v2 && selectedIds.some(id=>!directory?.some(row=>row.id===id)))throw Error('所选报告范围已不可用，请重新读取概览');
  if(!v2 && request.scope.kind==='team' && (!request.scope.team_id || !options.teams?.some(row=>row.id===request.scope.team_id)))throw Error('只能为实际管理的团队生成报告');
  if(!options.periods?.includes(request.period.kind))throw Error('此报告周期暂不可用');
  const scheme=composeSchemes([],options).find(row=>row.config.default_period===request.period.kind && !row.config.facts_contract);
  if(!scheme)throw Error('未找到可用的默认通用报告方案');
  if(mode==='weekly' && options.sources?.some(row=>row.id==='visit' && row.available))request.sources=['visit'];
  return {selection:request,scheme:{scheme_id:scheme.id,version_no:scheme.version_no}};
}
// API models fill omitted array defaults. Compare semantic selectors, retaining
// strict kind, revision and mixed-scope validation instead of raw JSON shape.
function snapshotScopeKey(scope,period){
  if(scope?.contract_version!=='work_report.scope_selection.v2')throw Error('报告快照范围版本不一致');
  const selected={scope:scope.kind,period_type:period.kind,anchor_date:period.anchor_date};
  for(const key of ['team_ids','member_ids','team_id','member_id','expected_scope_revision'])if(scope[key]!=null)selected[key]=scope[key];
  return stable(normalizeReviewSelection(selected));
}
export function matchingSnapshot(snapshot,plan){
  const selected=snapshot?.selection,wanted=plan?.selection;
  if(!wanted)throw Error('报告范围已变化，请重新打开');
  const v2=wanted.scope?.contract_version==='work_report.scope_selection.v2';
  if(!snapshot?.id || !['preparing','ready','failed'].includes(snapshot.status) || selected?.review_mode!==wanted.review_mode || selected.period?.kind!==wanted.period.kind || selected.period?.anchor_date!==wanted.period.anchor_date || !selected.scope || (v2 ? snapshotScopeKey(selected.scope,selected.period)!==snapshotScopeKey(wanted.scope,wanted.period) : selected.scope.kind!==wanted.scope.kind || (wanted.scope.team_id && selected.scope.team_id!==wanted.scope.team_id)))throw Error('报告快照范围已变化，请重新核对');
  if(snapshot.status==='ready'){
    if(!/^[a-f0-9]{64}$/i.test(snapshot.snapshot_hash || '') || snapshot.review_facts?.contract_version!==(v2?'work_report.review_facts.v2':'work_report.review_facts.v1'))throw Error('报告快照尚未完整准备，暂不能生成');
    if(v2){
      const selection={scope:wanted.scope.kind,period_type:wanted.period.kind,anchor_date:wanted.period.anchor_date,
        ...(wanted.scope.team_ids?{team_ids:wanted.scope.team_ids}:{}),
        ...(wanted.scope.member_ids?{member_ids:wanted.scope.member_ids}:{}),
        ...(wanted.scope.expected_scope_revision?{expected_scope_revision:wanted.scope.expected_scope_revision}:{})};
      try{matchingReviewScope(snapshot.scope,selection);}catch{throw Error('报告快照冻结范围不可用，请重新准备');}
      if(!snapshot.review_facts?.scope || stable(snapshot.review_facts.scope)!==stable(snapshot.scope))throw Error('报告快照冻结事实与范围不一致，请重新准备');
    }
  }
  return snapshot;
}
export function matchingDocument(document,reference){
  if(!reference || document?.id!==reference.documentId || document.snapshot_id!==reference.snapshotId || !document.revision?.content || !['private','shared'].includes(document.status))throw Error('生成结果与本次报告不一致');
  const wanted=reference.plan?.selection;
  if(wanted?.scope?.contract_version==='work_report.scope_selection.v2'){
    const selection={scope:wanted.scope.kind,period_type:wanted.period.kind,anchor_date:wanted.period.anchor_date,
      ...(wanted.scope.team_ids?{team_ids:wanted.scope.team_ids}:{}),
      ...(wanted.scope.member_ids?{member_ids:wanted.scope.member_ids}:{}),
      ...(wanted.scope.expected_scope_revision?{expected_scope_revision:wanted.scope.expected_scope_revision}:{})};
    try{matchingReviewScope(document.scope,selection);}catch{throw Error('报告冻结范围不可用，请重新读取');}
    if(document.review_facts?.contract_version!=='work_report.review_facts.v2' || !document.review_facts.scope || stable(document.review_facts.scope)!==stable(document.scope))throw Error('报告冻结事实与范围不一致，请重新读取');
  }
  const typed=document.revision.content.review_content;
  if(typed && !document.review_facts)throw Error('本报告的冻结依据不可用，请重新读取');
  if(typed && typed.contract_version!==(reference.mode==='weekly'?WEEKLY:DAILY))throw Error('报告内容类型与本次选择不一致');
  if(typed){const error=reviewContentProblem(typed,document.review_facts);if(error)throw Error(error);}
  return document;
}
export const exactRevision=(document,title)=>({version_no:document.version_no,content:{...clone(document.revision.content),title:title.trim()}});
