import {stable} from '../work-report/state.js';
export const REVIEW_METRICS = [
  {code:'forecast_collection',label:'预测回款',definition:'本季度回款预测中，回款信心为高的金额；不按概率加权。'},
  {code:'collection_ppl',label:'回款 PPL 储备',definition:'本季度回款预测中，赢单概率 ≥30% 的商机金额；排除丢单，不按概率加权。'},
  {code:'forecast_recognized',label:'预测确收',definition:'本季度含税确收预测中，赢单概率 ≥70% 的商机金额；排除丢单，目标同为含税。'},
];
export const REVIEW_PRIMARY_METRICS=REVIEW_METRICS.filter(row=>row.code!=='forecast_recognized');
export const REVIEW_SECONDARY_METRICS=REVIEW_METRICS.filter(row=>row.code==='forecast_recognized');
export const REVIEW_ACTIVITIES = [
  {code:'new_claimed_customers',label:'新认领客户',unit:'家'},
  {code:'formal_visits',label:'参与跟进',unit:'条'},
  {code:'new_opportunities',label:'新增商机',unit:'个'},
  {code:'lost_opportunities',label:'丢单商机',unit:'个'},
  {code:'updated_opportunities',label:'商机变动',unit:'个'},
  {code:'completed_tasks',label:'完成任务',unit:'项'},
  {code:'pending_tasks',label:'当前待完成',unit:'项'},
];
export const REVIEW_POOL_FILTERS = [
  {value:'all',label:'全部'}, {value:'not_updated',label:'当日未更新'},
  {value:'stale',label:'7天未跟进'}, {value:'high_risk',label:'高风险'}, {value:'key_collection',label:'季度重点回款'},
];
const DECIMAL=/^-?\d+(?:\.\d+)?$/;
export const isDecimal=value=>typeof value==='string' && DECIMAL.test(value);

// Display scaling only. Financial aggregation, rates and gaps remain server values.
export function formatDecimal(value,{places=2,shift=0,group=true,signed=false}={}) {
  if(!isDecimal(value))return '—';
  const negative=value.startsWith('-'),[whole,fraction='']=value.replace(/^-/,'').split('.');
  const raw=BigInt(whole+fraction),exponent=places-fraction.length-shift;
  const rounded=exponent>=0?raw*10n**BigInt(exponent):(raw+10n**BigInt(-exponent)/2n)/10n**BigInt(-exponent);
  const padded=rounded.toString().padStart(places+1,'0');
  let head=places?padded.slice(0,-places):padded;
  if(group)head=head.replace(/\B(?=(\d{3})+(?!\d))/g,',');
  const number=head+(places?'.'+padded.slice(-places):'');
  if(raw!==0n && rounded===0n)return `${negative?'−':signed?'+':''}<${places?'0.'+'0'.repeat(places-1)+'1':'1'}`;
  return `${rounded===0n?'':negative?'−':signed?'+':''}${number}`;
}
export const money=value=>formatDecimal(value,{shift:4});
export const exactMoney=value=>formatDecimal(value,{places:2})+' 元';
export const percentage=value=>isDecimal(value)?formatDecimal(value,{places:1,shift:-2,group:false})+'%':'—';
const REASONS={source_scope_not_fully_authorized:'来源读取权限不覆盖所选范围，仅展示可见部分',currency_unavailable:'币种缺失或非人民币，暂不能纳入当前人民币汇总',unknown_eligibility:'部分商机缺少概率或回款信心，暂不能确定是否纳入',missing_forecast_amount:'部分商机缺少本季度预测金额',missing_amount:'部分预测金额未填写',missing_forecast:'部分商机缺少季度预测',history_field_coverage_unverified:'现有历史不能证明全部业务字段和操作人均完整记录',legacy_state_subset:'历史快照仅保留部分业务字段',forecast_confidence_not_captured:'历史记录未完整保留预测信心',event_actor_not_recorded:'部分变更未记录操作人'};
export const reasonText=value=>REASONS[value] || value;
export function coverageText(value) {
  const history=value?.history_coverage,message=typeof history?.message==='string'?history.message.trim():'';
  // Retain independent permission/currency warnings alongside the server's
  // history explanation; only its own reason codes are represented by message.
  const reasons=(value?.missing_reasons || []).filter(code=>!message || !(history.reason_codes || []).includes(code)).map(reasonText);
  return [...new Set([message,...reasons].filter(Boolean))].join('；');
}
export const timestamp=value=>{
  if(!value)return '未记录';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?'未记录':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
};
const SCOPE_DIGEST=/^[0-9a-f]{64}$/;
const SCOPE_KEY=/^review-scope:v2:[0-9a-f]{64}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SCOPE_FIELDS=['member_id','team_id','member_ids','team_ids'];
const SELECTION_FIELDS=new Set(['scope','anchor_date','period_type',...SCOPE_FIELDS,'expected_scope_revision']);
const present=value=>value!==undefined && value!==null;
const same=(left,right)=>JSON.stringify(left)===JSON.stringify(right);
function canonicalIds(value,{bounded=true}={}) {
  if(!Array.isArray(value) || (bounded && value.length>100) || value.some(id=>typeof id!=='string'||!UUID.test(id)))throw Error('请选择最多 100 个有效的同类成员或团队');
  return [...new Set(value.map(id=>id.toLowerCase()))].sort();
}
const hasScopeArrays=selection=>present(selection?.member_ids)||present(selection?.team_ids);
export const isMultiReviewSelection=selection=>selection?.scope!=='self'&&hasScopeArrays(selection);
export const isV2ReviewSelection=selection=>isMultiReviewSelection(selection)||(selection?.scope==='self'&&present(selection.expected_scope_revision));
// Array shape is significant even for one selected subject: it uses the v2 API.
// The returned scope identity is never created here or treated as authority.
export function normalizeReviewSelection(selection) {
  if(!selection || !['self','member','team'].includes(selection.scope) || Object.keys(selection).some(key=>!SELECTION_FIELDS.has(key)))throw Error('请选择周期和查看范围');
  const {scope,anchor_date,period_type}=selection;
  if(typeof anchor_date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(anchor_date)||typeof period_type!=='string'||!period_type)throw Error('请选择周期和查看范围');
  const multi=hasScopeArrays(selection),aliases=present(selection.member_id)||present(selection.team_id);
  if(multi&&aliases)throw Error('范围数组不能与单个成员或团队同时使用');
  const result={scope,anchor_date,period_type};
  if(multi) {
    const member_ids=present(selection.member_ids)?canonicalIds(selection.member_ids):[],team_ids=present(selection.team_ids)?canonicalIds(selection.team_ids):[];
    if((scope==='self'&&(member_ids.length||team_ids.length||!present(selection.expected_scope_revision)))||(scope==='member'&&(!member_ids.length||team_ids.length))||(scope==='team'&&(!team_ids.length||member_ids.length)))throw Error('请选择非空的同类成员或团队范围');
    if(scope!=='self'){result.member_ids=member_ids;result.team_ids=team_ids;}
  } else {
    if((scope==='self'&&aliases)||(scope==='member'&&(!selection.member_id||present(selection.team_id)))||(scope==='team'&&(!selection.team_id||present(selection.member_id))))throw Error('查看范围和成员或团队不匹配');
    if(scope==='member')result.member_id=selection.member_id;
    if(scope==='team')result.team_id=selection.team_id;
  }
  if(present(selection.expected_scope_revision)) {
    if((!multi&&scope!=='self')||!SCOPE_DIGEST.test(selection.expected_scope_revision))throw Error('范围版本无效，请重新读取概览');
    result.expected_scope_revision=selection.expected_scope_revision;
  }
  return result;
}
export function reviewSelection(selection) {
  if(!selection?.period || !selection?.scope)throw Error('请选择周期和查看范围');
  const request={scope:selection.scope.kind,anchor_date:selection.period.anchor_date,period_type:selection.period.kind};
  for(const key of [...SCOPE_FIELDS,'expected_scope_revision'])if(present(selection.scope[key]))request[key]=selection.scope[key];
  return normalizeReviewSelection(request);
}
export function selectionKey(selection) {
  const value=normalizeReviewSelection(selection);
  return JSON.stringify(['scope','member_id','team_id','member_ids','team_ids','anchor_date','period_type'].map(key=>value[key]??null));
}
// Use for effects/cache keys of details only; user selection does not change
// simply because the server resolved a newer membership/permission revision.
export function detailSelectionKey(selection) {return JSON.stringify([selectionKey(selection),selection?.expected_scope_revision??null]);}
function scopeChanged() {return Object.assign(Error('所选范围已变化，请重新读取概览'),{statusCode:409,code:'WORK_REPORT_SCOPE_CHANGED',serverCode:'WORK_REPORT_SCOPE_CHANGED'});}
function matchingScope(scope,selection) {
  if(scope?.contract_version!=='review.scope.v2'||scope.kind!==selection.scope||typeof scope.label!=='string'||!SCOPE_KEY.test(scope.scope_key)||!SCOPE_DIGEST.test(scope.scope_revision)||scope.membership_basis!=='current_authorized_at_data_as_of')throw Error('返回的范围身份不完整，请重新读取');
  const ids=selection.scope==='self'?scope.subject_ids:selection.scope==='team'?selection.team_ids:selection.member_ids;
  if(selection.scope==='self'&&(!Array.isArray(ids)||ids.length!==1))throw Error('返回的本人范围不一致，请重新读取');
  if(!same(scope.subject_ids,ids)||!same(canonicalIds(scope.subject_ids),scope.subject_ids)||!Array.isArray(scope.subjects)||scope.subjects.length!==ids.length||!same(canonicalIds(scope.resolved_member_ids,{bounded:false}),scope.resolved_member_ids))throw Error('返回的范围成员不一致，请重新读取');
  const type=selection.scope==='team'?'team':'person';
  if(scope.subjects.some((subject,index)=>!subject||subject.id!==ids[index]||subject.scope_type!==type||typeof subject.label!=='string'))throw Error('返回的范围主体不一致，请重新读取');
  if(selection.scope!=='team'&&!same(scope.resolved_member_ids,ids))throw Error('返回的成员范围不一致，请重新读取');
  if(selection.expected_scope_revision && scope.scope_revision!==selection.expected_scope_revision)throw scopeChanged();
}
// Report readers also consume the resolved scope outside the overview page.
// Keep the same strict identity checks for the frozen snapshot/document
// envelopes instead of maintaining a weaker, report-only copy.
export function matchingReviewScope(scope,selection) {
  const requested=normalizeReviewSelection(selection);
  matchingScope(scope,requested);
  return scope;
}
function matchingEnvelope(value,selection) {
  const requested=normalizeReviewSelection(selection);
  let returnedKey;try{returnedKey=selectionKey(value?.selection);}catch{throw Error('返回的范围或周期已变化，请重新读取');}
  if(!value || returnedKey!==selectionKey(requested) || value.scope?.kind!==requested.scope ||
    (requested.team_id && value.scope.team_id!==requested.team_id) || (requested.member_id && value.scope.member_id!==requested.member_id) || !value.data_as_of || !value.period?.label)
    throw Error('返回的范围或周期已变化，请重新读取');
  if(isV2ReviewSelection(requested))matchingScope(value.scope,requested);
  return requested;
}
export function bindReviewScope(selection,value) {
  const requested=matchingEnvelope(value,selection);
  return isV2ReviewSelection(requested)?{...requested,expected_scope_revision:value.scope.scope_revision}:requested;
}
const COVERAGES=['complete','partial','unavailable'];
const TARGET_STATUSES=['effective','no_target','permission_denied'];
const zeroDecimal=value=>isDecimal(value)&&/^-?0+(?:\.0+)?$/.test(value);
function matchingTarget(row,scope,multi) {
  const target=row.target;
  if(!target||!TARGET_STATUSES.includes(target.status)||(target.value!=null&&!isDecimal(target.value))||(target.status==='effective'&&!isDecimal(target.value))||(target.status!=='effective'&&target.value!=null))throw Error('目标数据不完整，请重新读取');
  if((target.status!=='effective'||target.value==null||zeroDecimal(target.value))&&(row.rate!=null||row.gap!=null))throw Error('缺少有效目标时不能计算达成率或差额，请重新读取');
  if(!multi)return;
  if(!COVERAGES.includes(target.coverage)||target.aggregation!=='sum_distinct_subject_targets'||!Array.isArray(target.components)||target.components.length!==scope.subject_ids.length||!Object.hasOwn(target,'known_subtotal')||(target.known_subtotal!==null&&!isDecimal(target.known_subtotal)))throw Error('范围目标的覆盖信息不完整，请重新读取');
  const type=scope.kind==='team'?'team':'person',ids=new Set();
  for(const component of target.components) {
    if(!component||component.scope_type!==type||!scope.subject_ids.includes(component.subject_id)||ids.has(component.subject_id)||!TARGET_STATUSES.includes(component.status)||!(component.target_id===null||(typeof component.target_id==='string'&&UUID.test(component.target_id)))||!(component.version_no===null||(Number.isInteger(component.version_no)&&component.version_no>0))||!(component.value===null||isDecimal(component.value)))throw Error('范围目标的组成不一致，请重新读取');
    ids.add(component.subject_id);
    if((component.status==='effective'&&!isDecimal(component.value))||(component.status!=='effective'&&component.value!==null)||(component.status==='permission_denied'&&(component.target_id!==null||component.version_no!==null)))throw Error('范围目标的可见值不一致，请重新读取');
  }
  const effective=target.components.filter(component=>component.status==='effective').length;
  const coverage=effective===target.components.length?'complete':effective?'partial':'unavailable';
  const status=target.components.some(component=>component.status==='permission_denied')?'permission_denied':coverage==='complete'?'effective':'no_target';
  if(target.coverage!==coverage||target.status!==status||(effective>0&&!isDecimal(target.known_subtotal))||(coverage!=='complete'&&(target.value!==null||row.rate!=null||row.gap!=null)))throw Error('部分目标不能作为完整目标，请重新读取');
  const component=target.components.length===1?target.components[0]:null;
  if(target.id!==(component?.target_id??null)||target.version_no!==(component?.version_no??null))throw Error('范围目标的标识不一致，请重新读取');
}
export function matchingReview(value,selection) {
  const requested=matchingEnvelope(value,selection);
  if(value.contract_version!==(isV2ReviewSelection(requested)?'review.overview.v2':'review.overview.v1') || !Array.isArray(value.metrics) || !Array.isArray(value.activities) || !value.quarter || !Array.isArray(value.members))throw Error('概览数据不完整，请刷新重试');
  for(const {code} of REVIEW_METRICS) {
    const rows=value.metrics.filter(row=>row.metric_code===code);
    if(rows.length!==1)throw Error('概览指标不完整，请刷新重试');
    const row=rows[0];
    for(const field of ['value','known_subtotal','rate','gap','day_change'])if(row[field]!=null && !isDecimal(row[field]))throw Error('指标数值格式不正确，请重新读取');
    if(!COVERAGES.includes(row.coverage) || !row.baseline)throw Error('指标口径不完整，请重新读取');
    matchingTarget(row,value.scope,isV2ReviewSelection(requested));
    if(row.coverage!=='complete' && (row.value!=null || row.rate!=null || row.gap!=null))throw Error('部分数据不能作为完整指标，请重新读取');
  }
  for(const {code} of REVIEW_ACTIVITIES) {
    const rows=value.activities.filter(row=>row.code===code);
    if(rows.length!==1 || (rows[0].count!=null && (!Number.isInteger(rows[0].count) || rows[0].count<0)) || !Number.isInteger(rows[0].known_count) || !['complete','partial','unavailable'].includes(rows[0].coverage))throw Error('本期动作数据不完整，请重新读取');
    if(rows[0].coverage!=='complete' && rows[0].count!=null)throw Error('部分动作不能作为完整数量，请重新读取');
  }
  return value;
}
export function matchingPage(value,selection,query) {
  const requested=matchingEnvelope(value,selection);
  if(value.contract_version!==(isV2ReviewSelection(requested)?'review.page.v2':'review.page.v1') || !Array.isArray(value.items) || value.offset!==query.offset || value.limit!==query.limit || value.items.length>query.limit || typeof value.has_more!=='boolean' || !Number.isInteger(value.known_total) || value.known_total<0)throw Error('明细分页已变化，请重新读取');
  for(const key of ['activity','filter','sort','metric_code'])if(query[key]!=null && value[key]!==query[key])throw Error('明细筛选已变化，请重新读取');
  if(value.total!=null && (!Number.isInteger(value.total) || value.total<0))throw Error('明细数量不正确，请重新读取');
  if(!['complete','partial','unavailable'].includes(value.coverage) || (value.coverage!=='complete' && value.total!=null) || (value.available===false && (value.items.length || value.coverage!=='unavailable')))throw Error('明细可见范围不完整，请重新读取');
  return value;
}
export function metricState(metric) {
  if(metric.coverage!=='complete')return {label:metric.coverage==='partial'?'数据未齐':'暂无可用数据',color:'orange'};
  if(metric.target.status==='permission_denied')return {label:'目标不可查看',color:'default'};
  return ({expected_achieved:{label:'预计达成',color:'green'},on_track:{label:'正常',color:'blue'},behind:{label:'进度落后',color:'orange'},no_target:{label:'未设目标',color:'default'},target_unavailable:{label:'目标不可查看',color:'default'}})[metric.status] || {label:'待核对',color:'default'};
}

const FIELD_LABELS={amount:'商机金额',acv:'ACV',collection_amount:'预计回款',recognized_amount:'预计确收',collection_confidence:'回款信心',probability:'赢单概率',stage:'商机阶段',stage_code:'商机阶段',status:'商机状态',name:'商机名称',expected_close_date:'预计关单日期',owner_id:'负责人',owner_user_ref_id:'负责人',team_id:'所属团队',owner_team_id:'所属团队',sales_channel:'销售渠道',partner_id:'所属伙伴',partner_name:'所属伙伴',product_line:'产品线',follow_up_plan:'跟进计划',lost_reason:'丢单原因',customer_id:'所属客户',currency:'币种',trade_relation:'交易关系',material_lines:'物料',project_people:'项目成员'};
export function changeLabel(change) {
  const {year,quarter}=change;
  const period=year!=null && Number.isInteger(quarter) && quarter>=1 && quarter<=4?`${year} Q${quarter} `:'';
  return period+(change.label || '字段变更');
}
export function changeValue(change,side) {
  const value=change[side];
  if(value==null || value==='')return '未记录';
  if(change.field==='sales_channel' || change.label==='销售渠道')return ({direct:'直销',partner:'伙伴',unknown:'未确认'})[value] || String(value);
  const rendered=typeof value==='object'?JSON.stringify(value):String(value);
  return change.unit?`${rendered} ${change.unit}`:rendered;
}
// Preserve events independently, including A→B→A. Never replace an unknown
// history with current state, or infer a modifier from the current owner.
export function changeRows(events=[],members=[]) {
  const rows=[];
  for(const event of events) {
    const actor=event.actor_status && event.actor_status!=='available'?'未记录或不可查看':
      (event.actor_status==='available' && event.actor_display_name) || event.actor_name || members.find(row=>row.id===event.actor_id)?.name || '未记录或不可查看';
    const append=(field,label,before,after,suffix,metadata={})=>{
      if(stable(before)===stable(after))return;
      const key=field?.split('.').at(-1),displayLabel=(!label || label===field || label===key)?FIELD_LABELS[key] || label || field || '字段变更':label;
      rows.push({key:`${event.id}:${suffix}`,at:event.occurred_at,actor,field,label:displayLabel,before,after,...metadata});
    };
    const changes=event.changes;
    if(Array.isArray(changes))changes.forEach((change,index)=>append(change.field,change.label,change.before,change.after,index,
      Object.fromEntries(['unit','year','quarter'].filter(key=>Object.hasOwn(change,key)).map(key=>[key,change[key]]))));
    else if(changes && typeof changes==='object') {
      const flatten=(obj,prefix='')=>Object.entries(obj || {}).flatMap(([key,value])=>value && typeof value==='object' && !Array.isArray(value)?flatten(value,`${prefix}${key}.`):[[prefix+key,value]]);
      const before=Object.fromEntries(flatten(changes.before)),after=Object.fromEntries(flatten(changes.after));
      for(const key of new Set([...Object.keys(before),...Object.keys(after)]))append(key,undefined,before[key],after[key],key);
    }
  }
  return rows;
}
