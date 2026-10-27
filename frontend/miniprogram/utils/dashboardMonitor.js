const {beijingDateParts} = require('./opportunityQuarter');

const DEFINITIONS = [
  {code:'forecast_collection',label:'预测回款',basis:'latest_sales_forecast',definition:'所选季度回款预测中，回款信心为高的金额；不按概率加权。'},
  {code:'collection_ppl',label:'回款 PPL 储备',basis:'latest_sales_forecast',definition:'所选季度回款预测中，赢单概率 ≥30% 的商机金额；排除丢单，不按概率加权。'},
  {code:'forecast_recognized',label:'预测确收（含税）',basis:'latest_sales_forecast',definition:'所选季度含税确收预测中，赢单概率 ≥70% 的商机金额；排除丢单，目标同为含税。'},
  {code:'actual_recognized_exclusive',label:'实际不含税确收',basis:'adopted_actuals',kind:'recognized',taxBasis:'tax_exclusive',definition:'所选季度已采用的实际不含税确收；未登记或税口径未确认的金额不补为零。'},
  {code:'actual_collection',label:'实际回款',basis:'adopted_actuals',kind:'collection',taxBasis:'cash_collection_not_applicable',definition:'所选季度已采用的实际回款；明确登记为零与尚未登记分开显示。'},
];
const REASONS = {
  missing_forecast_amount:'本季度预测金额未填写',missing_amount:'预测金额未填写',missing_forecast:'尚未填写本季度预测',
  unknown_eligibility:'阶段概率或回款信心未齐，暂不能确定是否纳入',currency_unavailable:'币种缺失或非人民币，暂不能纳入人民币汇总',
  source_scope_not_fully_authorized:'部分来源不在当前可见范围',not_eligible:'不符合本指标纳入条件',
  actual_not_registered:'本季度存在未登记金额，未将缺失值计为零',actual_incomplete:'部分实际金额未登记完整，仅展示已知小计',
  actual_attribution_unresolved:'部分历史实绩归属待核对，暂不计入个人或部门业绩',
  actual_tax_basis_unresolved:'部分记录的不含税口径待确认',actual_source_conflict:'部分实际记录来源待核对',actual_withdrawn:'部分原登记已撤回，不计入当前采用金额',
};
const SOURCE_LABELS={entries:'历史逐笔采用值',historical:'历史期间采用值',operations:'实绩登记'};
const STATUS_LABELS={missing:'未登记',single_source:'已登记',confirmed:'已确认',needs_confirmation:'来源待确认',stale_confirmation:'原确认待复核',partial:'部分登记',withdrawn:'已撤回'};
const COVERAGES=['complete','partial','unavailable'];
const sorted=values=>[...new Set(values||[])].sort();
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const decimal=value=>typeof value==='string' && /^-?\d+(?:\.\d+)?$/.test(value);
const reasonText=reason=>REASONS[reason]||'暂不可计算';
const actual=code=>DEFINITIONS.some(row=>row.code===code&&row.basis==='adopted_actuals');
const invalid=message=>{throw Object.assign(Error(message),{monitorSafe:true});};

// String-only display scaling keeps large financial values exact on Mini engines
// without BigInt. Aggregates, rates and gaps always come from the API.
function formatDecimal(value,places=2,shift=0,group=true) {
  if(!decimal(value))return '—';
  const negative=value[0]==='-',parts=value.replace(/^-/,'').split('.'),fraction=parts[1]||'';
  const raw=(parts[0]+fraction).replace(/^0+(?=\d)/,''),exponent=places-fraction.length-shift;
  let rounded;
  if(exponent>=0)rounded=raw+'0'.repeat(exponent);
  else {
    const padded=raw.padStart(-exponent+1,'0'),cut=padded.length+exponent;
    rounded=padded.slice(0,cut);
    if(padded[cut]>='5') {
      const digits=rounded.split('');let carry=1;
      for(let i=digits.length-1;i>=0&&carry;i--){const next=Number(digits[i])+carry;digits[i]=String(next%10);carry=next>9?1:0;}
      rounded=(carry?'1':'')+digits.join('');
    }
  }
  rounded=rounded.replace(/^0+(?=\d)/,'');
  if(!/^0+$/.test(raw)&&/^0+$/.test(rounded))return `${negative?'−':''}<${places?'0.'+'0'.repeat(places-1)+'1':'1'}`;
  const padded=rounded.padStart(places+1,'0');let head=places?padded.slice(0,-places):padded;
  if(group)head=head.replace(/\B(?=(\d{3})+(?!\d))/g,',');
  return (negative&&!/^0+$/.test(rounded)?'−':'')+head+(places?'.'+padded.slice(-places):'');
}
const money=value=>formatDecimal(value,2,4);
const percentage=value=>decimal(value)?formatDecimal(value,1,-2,false)+'%':'—';
function initialData(now=new Date()) {
  const period=beijingDateParts(now);
  return {monitorYear:period.year,monitorQuarter:period.quarter,
    monitorYears:Array.from({length:101},(_,i)=>({value:2000+i,label:(2000+i)+'年'})),
    monitorQuarters:[1,2,3,4].map(value=>({value,label:'Q'+value})),
    monitorLoading:false,monitorError:'',monitorReady:false,monitorForecasts:[],monitorActuals:[],monitorScope:'',monitorAsOf:'',monitorProgress:'—',
    monitorDetail:null};
}
function selection(page) {
  const scope=page.factsQuery();
  return {personal:scope.personal,member_id:scope.personal?scope.member_id||null:null,team_groups:scope.personal?[]:sorted(scope.team_groups),year:page.data.monitorYear,quarter:page.data.monitorQuarter};
}
function matchingEnvelope(value,query,selfId) {
  const returned=value&&value.selection,period=value&&value.quarter,scope=value&&value.scope;
  const target=query.member_id||selfId,kinds=query.personal?[target===selfId?'self':'member']:['team','authorized'];
  if(!returned||returned.personal!==query.personal||(query.personal?returned.member_id!==target:returned.member_id!=null)||
      !scope||!kinds.includes(scope.kind)||typeof scope.label!=='string'||!Array.isArray(returned.team_groups)||!same(sorted(returned.team_groups),sorted(query.team_groups))||
      !period||period.year!==query.year||period.quarter!==query.quarter||!decimal(period.time_progress)||Number(period.time_progress)<0||Number(period.time_progress)>1||
      typeof value.data_as_of!=='string'||!Number.isFinite(Date.parse(value.data_as_of)))invalid('监控范围或季度已变化，请重新读取');
}
function matchingMetrics(value,query,selfId) {
  matchingEnvelope(value,query,selfId);
  if(value.contract_version!=='dashboard.metrics.v1'||value.values_basis!=='current_authorized_quarter_snapshot'||!Array.isArray(value.metrics))invalid('季度监控数据不完整，请重新读取');
  for(const definition of DEFINITIONS) {
    const matches=value.metrics.filter(row=>row&&row.metric_code===definition.code),row=matches[0],target=row&&row.target;
    if(matches.length!==1||row.basis!==definition.basis||(definition.taxBasis&&row.tax_basis!==definition.taxBasis)||!COVERAGES.includes(row.coverage)||!target||!['effective','no_target','permission_denied'].includes(target.status))invalid('季度监控指标不完整，请重新读取');
    for(const field of ['value','known_subtotal','rate','gap','day_change'])if(row[field]!=null&&!decimal(row[field]))invalid('季度监控金额格式异常，请重新读取');
    for(const field of ['value','known_subtotal'])if(target[field]!=null&&!decimal(target[field]))invalid('季度目标金额格式异常，请重新读取');
    if((target.status==='effective'&&!decimal(target.value))||(target.status!=='effective'&&target.value!=null)||(target.status==='permission_denied'&&(row.rate!=null||row.gap!=null)))invalid('季度目标可见范围已变化，请重新读取');
    const provisional=row.coverage==='partial'&&decimal(row.known_subtotal)&&['known_subtotals','known_subtotals_and_targets'].includes(row.rate_basis);
    if(row.coverage!=='complete'&&((row.value!=null&&!provisional)||((row.rate!=null||row.gap!=null)&&!provisional)))invalid('部分数据不能作为完整监控指标，请重新读取');
    const counts=['missing_amount_count','unknown_eligibility_count'];
    if(actual(definition.code))counts.push('registered_fact_count','registered_period_count','incomplete_period_count','missing_material_count','tax_unknown_period_count','conflict_period_count');
    if(counts.some(key=>!Number.isInteger(row[key])||row[key]<0)||!Array.isArray(row.missing_reasons))invalid('季度监控缺项信息不完整，请重新读取');
  }
  return value;
}
function coverageText(metric) {
  const reasons=new Set(metric.missing_reasons||[]),notes=[];
  const add=(field,reason,label)=>{if(metric[field]>0){notes.push(`${metric[field]} ${label}`);if(reason)reasons.delete(reason);}};
  if(actual(metric.metric_code)) {
    add('incomplete_period_count','actual_incomplete','项季度记录尚未完整登记');
    add('missing_material_count',null,'项物料待补齐');
    add('tax_unknown_period_count','actual_tax_basis_unresolved','项季度记录的不含税口径待确认');
    add('conflict_period_count','actual_source_conflict','项季度记录来源待核对');
    add('attribution_unknown_period_count','actual_attribution_unresolved','项历史季度记录归属待核对，暂不计入个人或部门业绩');
  } else {
    add('unknown_eligibility_count','unknown_eligibility',metric.metric_code==='forecast_collection'?'条记录缺少本季度预测或回款信心，待确认是否纳入':'条记录的纳入条件待补充');
    if(metric.missing_amount_count>0){notes.push(`${metric.missing_amount_count} 条符合条件的记录，预测金额或币种待核对（未填、币种缺失或非人民币）`);reasons.delete('missing_forecast_amount');reasons.delete('currency_unavailable');}
  }
  // Counts describe separate, potentially overlapping dimensions; never sum them.
  return [...new Set([...notes,...[...reasons].map(reasonText)])].join('；');
}
function cardView(metric) {
  const definition=DEFINITIONS.find(row=>row.code===metric.metric_code),isActual=actual(metric.metric_code),target=metric.target;
  const amount=metric.coverage==='complete'?metric.value:metric.coverage==='partial'?metric.known_subtotal:null;
  const targetPartial=target.status!=='permission_denied'&&target.coverage==='partial';
  const reasons=metric.missing_reasons||[];
  const result={...definition,amount:money(amount),exactAmount:decimal(amount)?formatDecimal(amount)+' 元':'金额暂不可计算',
    amountLabel:metric.coverage==='partial'?'已知小计':isActual?'已采用金额':'当前预测',
    state:metric.coverage==='partial'?'部分数据':metric.coverage==='complete'?'数据完整':'暂不可计算',
    note:coverageText(metric),registeredCount:metric.registered_fact_count||0};
  if(isActual) {
    result.amountLabel=metric.coverage==='partial'?'已登记小计':'已采用金额';
    result.state=amount==null&&reasons.includes('actual_attribution_unresolved')?'归属待核对':metric.coverage==='partial'?'部分登记':amount!=null?'已登记':reasons.includes('source_scope_not_fully_authorized')?'当前范围数据未齐':reasons.includes('actual_withdrawn')?'已撤回':reasons.includes('actual_attribution_unresolved')?'归属待核对':reasons.includes('actual_not_registered')?'未登记':'金额待核对';
    return result;
  }
  result.target=target.status==='permission_denied'?'不可查看':target.status==='effective'?money(target.value)+' 万元':targetPartial?target.known_subtotal!=null?'已知 '+money(target.known_subtotal)+' 万元':'目标未齐':'未设置';
  result.targetNote=targetPartial?'目标未齐，仅按可用目标口径展示；未设置部分不计为零。':target.status==='effective'&&/^0+(\.0+)?$/.test(target.value)?'目标明确为 0，不计算达成率。':'';
  result.rateLabel=metric.rate_basis&&metric.rate_basis!=='effective_target'?'已知部分达成率':'预计达成率';
  result.rate=percentage(metric.rate);result.gap=decimal(metric.gap)?money(metric.gap)+' 万元':'—';
  return result;
}
function matchingDetail(value,query,page,selfId) {
  matchingEnvelope(value,query,selfId);
  const isActual=actual(page.metric_code),definition=DEFINITIONS.find(row=>row.code===page.metric_code);
  if(!definition||value.contract_version!==(isActual?'dashboard.actual_page.v1':'dashboard.metric_page.v1')||value.metric_code!==page.metric_code||value.limit!==page.limit||value.offset!==page.offset||
      !Array.isArray(value.items)||value.items.length>page.limit||typeof value.has_more!=='boolean'||!COVERAGES.includes(value.coverage)||!Number.isInteger(value.known_total)||value.known_total<0||
      (value.total!=null&&(!Number.isInteger(value.total)||value.total<0))||(!isActual&&value.coverage!=='complete'&&value.total!=null))invalid('监控明细或分页已变化，请重新读取');
  if(isActual&&(value.values_basis!=='current_authorized_quarter_snapshot'||value.basis!=='adopted_actuals'))invalid('实际金额明细口径已变化，请重新读取');
  for(const row of value.items) {
    if(!row||typeof row.id!=='string')invalid('监控明细数据不完整，请重新读取');
    if(isActual&&(typeof row.customer_id!=='string'||row.year!==query.year||row.quarter!==query.quarter||row.kind!==definition.kind||
        (row.opportunity_id!=null&&typeof row.opportunity_id!=='string')||typeof row.complete!=='boolean'||typeof row.tax_unresolved!=='boolean'))invalid('实际金额明细口径不完整，请重新读取');
    const contribution=row.metric_contributions&&row.metric_contributions[page.metric_code];
    const amount=isActual?row.amount:contribution&&contribution.value;
    if(amount!=null&&!decimal(amount))invalid('监控明细金额格式异常，请重新读取');
  }
  return value;
}
function detailRow(row,code) {
  const isActual=actual(code),contribution=row.metric_contributions&&row.metric_contributions[code]||{},amount=isActual?row.amount:contribution.value;
  return {id:row.id,title:isActual?row.customer_name||'客户名称未记录':row.name||'商机名称未记录',
    subtitle:isActual?row.opportunity_id?row.opportunity_name||'商机名称未记录':'客户级记录 · 未关联商机':row.customer_name||'客户名称未记录',
    owner:isActual&&row.attribution_status==='unresolved'?'历史归属待核对':row.owner_name||'—',amount:money(amount),amountLabel:isActual?row.complete?'采用金额':'已登记小计':'纳入金额',
    period:isActual?`${row.year} Q${row.quarter}`:'',date:isActual?(row.period_precision==='day'&&row.occurred_on?row.occurred_on:'季度汇总记录'):'',
    source:isActual?SOURCE_LABELS[row.source]||'来源未记录':'',status:isActual?STATUS_LABELS[row.status]||'状态待核对':'',
    note:isActual?[row.attribution_status==='unresolved'?'历史归属待核对，不按当前认领人推算历史业绩':'',row.tax_unresolved?'不含税口径待确认':'',row.missing_material_count>0?`${row.missing_material_count} 项物料待补齐`:''].filter(Boolean).join('；'):contribution.reason?reasonText(contribution.reason):'',
    probability:decimal(row.probability)?row.probability+'%':'未填写',confidence:{high:'高',medium:'中',low:'低'}[row.collection_confidence]||'未填写'};
}
function errorText(error) {
  if(Number(error&&error.statusCode)===403)return '当前账号无权查看所选监控范围，请重新选择';
  if(Number(error&&error.statusCode)===401)return '登录状态已失效，请重新登录';
  return error&&error.monitorSafe?error.message:'季度监控暂时加载失败，请重试';
}
function methods(api) {
  return {
    monitorQuery(){return selection(this);},
    monitorContext(){return JSON.stringify([this.identityKey(),this.monitorQuery(),this.data.presentation]);},
    clearMonitor(){this._monitorSerial=(this._monitorSerial||0)+1;this._monitorOwner=null;this.closeMonitorDetail();this.setData({monitorReady:false,monitorLoading:false,monitorError:'',monitorForecasts:[],monitorActuals:[],monitorScope:'',monitorAsOf:'',monitorProgress:'—'});},
    closeMonitorDetail(){this._monitorDetailSerial=(this._monitorDetailSerial||0)+1;if(this.data.monitorDetail&&this.monitorDetailVisibility)this.monitorDetailVisibility(false);this.setData({monitorDetail:null});},
    monitorNoop(){},
    changeMonitorYear(e){const year=Number(e.detail.value);if(Number.isInteger(year)&&year>=2000&&year<=2100&&year!==this.data.monitorYear){this.setData({monitorYear:year});return this.loadMonitor();}},
    changeMonitorQuarter(e){const quarter=Number(e.detail.value);if(Number.isInteger(quarter)&&quarter>=1&&quarter<=4&&quarter!==this.data.monitorQuarter){this.setData({monitorQuarter:quarter});return this.loadMonitor();}},
    retryMonitor(){if(this.data.canViewTeam&&this.data.optionsError)return this.loadOptions();return this.loadMonitor();},
    loadMonitor(){
      this.clearMonitor();
      if(!this.monitorAllowed())return Promise.resolve();
      if(this.data.canViewTeam&&(this.data.optionsLoading||this.data.optionsError)){this.setData({monitorError:'查看范围尚未加载，请重试'});return Promise.resolve();}
      const query=this.monitorQuery(),key=this.monitorContext(),serial=this._monitorSerial,selfId=this.monitorSelfId();
      this.setData({monitorLoading:true});
      const current=()=>serial===this._monitorSerial&&key===this.monitorContext()&&this.monitorAllowed();
      return Promise.resolve().then(()=>current()?api.getDashboardMetrics(query):null).then(value=>{
        if(!current())return;
        matchingMetrics(value,query,selfId);this._monitorOwner=key;
        const views=DEFINITIONS.map(definition=>cardView(value.metrics.find(row=>row.metric_code===definition.code)));
        const asOf=new Date(Date.parse(value.data_as_of)+8*3600000).toISOString().slice(0,16).replace('T',' ');
        this.setData({monitorLoading:false,monitorReady:true,monitorForecasts:views.slice(0,3),monitorActuals:views.slice(3),monitorScope:value.scope.label,
          monitorAsOf:asOf,monitorProgress:percentage(value.quarter.time_progress)});
      }).catch(error=>{if(current())this.setData({monitorLoading:false,monitorError:errorText(error)});});
    },
    openMonitorDetail(e){
      const code=e.currentTarget.dataset.code,definition=DEFINITIONS.find(row=>row.code===code);
      if(!definition||!this.data.monitorReady||this._monitorOwner!==this.monitorContext()||!this.monitorAllowed())return;
      this.closeMonitorDetail();
      this.setData({monitorDetail:{code,title:definition.label,definition:definition.definition,isActual:actual(code),rows:[],offset:0,loading:false,error:'',hasMore:false,totalText:'',note:''}});
      if(this.monitorDetailVisibility)this.monitorDetailVisibility(true);
      return this.loadMonitorDetail(0);
    },
    loadMonitorDetail(offset=0){
      const detail=this.data.monitorDetail;if(!detail||this._monitorOwner!==this.monitorContext()||!this.monitorAllowed())return Promise.resolve();
      const key=this.monitorContext(),query=this.monitorQuery(),selfId=this.monitorSelfId(),page={metric_code:detail.code,limit:20,offset};
      const serial=this._monitorDetailSerial=(this._monitorDetailSerial||0)+1;
      this.setData({monitorDetail:{...detail,offset,loading:true,error:'',rows:[],hasMore:false,totalText:'',note:''}});
      const current=()=>serial===this._monitorDetailSerial&&key===this.monitorContext()&&this._monitorOwner===key&&this.monitorAllowed()&&this.data.monitorDetail&&this.data.monitorDetail.code===detail.code;
      return Promise.resolve().then(()=>current()?(actual(detail.code)?api.getDashboardMetricActuals({...query,...page}):api.getDashboardMetricOpportunities({...query,...page})):null).then(value=>{
        if(!current())return;matchingDetail(value,query,page,selfId);
        this.setData({monitorDetail:{...this.data.monitorDetail,loading:false,rows:value.items.map(row=>detailRow(row,detail.code)),hasMore:value.has_more,
          totalText:value.total==null?`当前可见 ${value.known_total} 条`:`共 ${value.total} 条`,
          note:[...new Set((value.missing_reasons||[]).map(reasonText))].join('；')}});
      }).catch(error=>{if(current())this.setData({monitorDetail:{...this.data.monitorDetail,loading:false,error:errorText(error)}});});
    },
    retryMonitorDetail(){return this.loadMonitorDetail(this.data.monitorDetail?this.data.monitorDetail.offset:0);},
    previousMonitorDetail(){const d=this.data.monitorDetail;if(d&&!d.loading&&d.offset>=20)return this.loadMonitorDetail(d.offset-20);},
    nextMonitorDetail(){const d=this.data.monitorDetail;if(d&&!d.loading&&d.hasMore)return this.loadMonitorDetail(d.offset+20);},
  };
}
module.exports={DEFINITIONS,initialData,methods,selection,matchingMetrics,matchingDetail,formatDecimal,money,percentage,cardView,coverageText,detailRow};
