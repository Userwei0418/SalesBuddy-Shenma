const scores=require('./profileScores');
const access=require('./access');
const {visitDetailUrl}=require('./visitNavigation');
const states={assessed:'已评估',partially_assessed:'部分维度已评估',insufficient_data:'材料不足，尚未评估',legacy_unknown:'历史评估，材料与规则口径待核'};
const text=value=>typeof value==='string'?value:'';
const count=value=>Number.isInteger(value)&&value>=0?String(value):'未知';
const emptyReview=()=>({canReview:false,busy:false,status:'missing',error:'',currentReview:null,reviewId:'',reviewedAt:'',evaluationState:'legacy_unknown',evidenceCoverage:null,scoreBasis:null,groupCoverage:null,comparisonStatus:null});
const emptyEvidence=()=>({visible:false,loading:false,error:'',reviewId:'',result:null});
function basisLabel(basis){
  if(!basis||basis.snapshot_status!=='frozen')return '历史规则未完整保存，不与新规则直接比较';
  return basis.framework_version==null?`事实比例规则 · ${text(basis.calculation_version)}`:`采用框架版本 ${basis.framework_version} · 评分规则版本 ${basis.score_policy?basis.score_policy.version:'未知'}`;
}
function coverageText(coverage){
  if(!coverage||coverage.status!=='known')return '历史材料覆盖未完整保存，不能确认是否为全量';
  const range=coverage.window_start&&coverage.window_end_exclusive?`${coverage.window_start} 至 ${coverage.window_end_exclusive}（不含） · ${coverage.business_timezone||'时间区未提供'}；`:'';
  return `${range}采用 ${count(coverage.used_visits)} / ${count(coverage.total_eligible_visits)} 条有效拜访${coverage.truncated===true?`；超出 ${count(coverage.limit)} 条上限，已截取最近材料`:coverage.truncated===false?'；未截断':'；截断状态未知'}`;
}
function fdeCoverageText(coverage){
  if(!coverage)return '历史事实覆盖未完整保存';
  return `归档记录 ${count(coverage.archived_visits)} 条，当前原文可读 ${count(coverage.readable_archived_visits)} 条；协助项目 ${count(coverage.assigned_projects)} 个、客户 ${count(coverage.assigned_customers)} 家；本人任务 ${count(coverage.owned_tasks)} 个`;
}
function coachingCoverageText(coverage){
  if(!coverage||coverage.status!=='known')return '历史建议素材覆盖未完整保存';
  return `建议采用拜访 ${count(coverage.selected_visits)} 条（上限 ${count(coverage.visit_limit)}）、任务 ${count(coverage.selected_tasks)} 条（上限 ${count(coverage.task_limit)}）；正文截短 ${count(coverage.truncated_text_sources)} 处。建议素材范围不改变事实指标分母。`;
}
function scoreInfo(review={},modern=false){
  const frozen=modern||(review.score_basis||{}).snapshot_status==='frozen';
  const source=review.score_summary||(!frozen?{value:review.overall_score,text:review.overall_score==null?'--':String(review.overall_score)}:{});
  const value=review.evaluation_state==='insufficient_data'?null:source.value;
  const display=scores.displayScore({...source,value});
  return {...source,...display,count:source.count,total:source.total,explanation:source.explanation||display.explanation};
}
function historyPoints(history,code='overall',fde=false){
  return (history||[]).map(row=>({id:row.id,run_id:row.run_id,review_date:row.review_date,reviewed_at:row.reviewed_at,generation_no:row.generation_no,
    date:text(row.reviewed_at||row.review_date||row.date),score_basis:row.score_basis||null,evaluation_state:row.evaluation_state||'legacy_unknown',basisLabel:basisLabel(row.score_basis),
    score:fde?scores.numeric(((row.dimensions||[]).find(x=>x.code===code)||{}).score):code==='overall'?scoreInfo(row).value:scores.numeric(((row.dimension_scores||{})[code]||{}).score)}));
}
function salesDisplay(payload){
  const latest=payload.latest||{},basis=latest.score_basis||null,modern=payload.contract_version==='sales.growth.v2';
  const values=latest.dimension_scores||{},frozen=basis&&basis.framework_snapshot;
  const definitions=frozen&&Array.isArray(frozen.dimensions)?frozen.dimensions:Object.keys(values).length?Object.keys(values).map(code=>({code,...values[code]})):(payload.framework||{}).dimensions||[];
  const dimensions=definitions.map(definition=>{const value=values[definition.code]||{},state=value.evaluation_state||'legacy_unknown';
    const score=state==='insufficient_data'?null:scores.numeric(value.score),display=scores.displayScore({value:score});
    return {code:definition.code,name:value.name||definition.name,shortName:value.short_name||definition.short_name||definition.name,score:score===null?'--':score,signal:display.signal,evaluation_state:state,assessment:value.assessment||states[state]||'暂无判断',coachingAction:value.coaching_action||'补充正式跟进材料后再评估'};
  });
  const group=payload.projection==='current_group',mixed=group&&['mixed_basis','legacy_unknown','no_data'].includes(payload.comparison_status);
  const profileScoreInfo=mixed?scores.displayScore(null):scoreInfo(latest,modern),coverage=payload.coverage||{};
  const groupText=`已完成 ${count(coverage.assessed_members)} 人，部分评估 ${count(coverage.partially_assessed_members)} 人，未评估 ${count(coverage.unassessed_members)} 人，历史口径未知 ${count(coverage.legacy_unknown_members)} 人；范围内 ${count(coverage.eligible_members)} 人`;
  const status=payload.current_review?payload.current_review.status:payload.today_status||'missing',busy=['queued','running'].includes(status);
  const statusText=group?(mixed?'范围内规则不一致或材料不足，暂不汇总总分；':'当前同口径汇总；')+groupText:busy?'正在更新评估；下方如有结果，仍为上次保存的评估':status==='failed'?'本次更新失败；已有结果保留原评估日期':payload.latest?states[latest.evaluation_state]||'已保存的历史评估':'尚无已保存评估';
  return {growthLoading:false,growthReady:!!payload.latest||group,growthStatusText:statusText,overallScore:profileScoreInfo.text,profileScoreInfo,dimensions,
    reviewDate:text(latest.reviewed_at||latest.review_date),reviewSummary:text(latest.summary),latestReviewId:latest.id||'',history:group?[]:(payload.history||[]).map(row=>({...row,basisLabel:basisLabel(row.score_basis)})),
    growthOptions:[{code:'overall',name:'综合'}].concat(dimensions.map(d=>({code:d.code,name:d.shortName}))),
    visitCount:(latest.evidence_coverage||{}).used_visits==null?null:latest.evidence_coverage.used_visits,
    aiAdvice:Array.isArray(latest.improvements)?latest.improvements.filter(x=>typeof x==='string'):[],
    growthCoverageText:group?groupText:coverageText(latest.evidence_coverage),growthBasisText:basisLabel(basis),
    growthReview:{...emptyReview(),status,busy,currentReview:payload.current_review||null,reviewId:latest.id||'',reviewedAt:text(latest.reviewed_at),evaluationState:latest.evaluation_state||'legacy_unknown',evidenceCoverage:latest.evidence_coverage||null,scoreBasis:basis,groupCoverage:payload.coverage||null,comparisonStatus:payload.comparison_status||null}};
}
function safeEvidence(payload,fde){
  const evidence=(payload.evidence||[]).map(row=>row.readable===true?(fde?{source_type:row.source_type,source_id:row.source_id,source_version:row.source_version,source_ref:row.source_ref,detail:row.detail,readable:true}:{visit_id:row.visit_id,visit_version:row.visit_version,dimension_codes:row.dimension_codes,detail:row.detail,readable:true}):(fde?{source_type:null,source_id:null,source_version:null,source_ref:null,detail:null,readable:false}:{visit_id:null,visit_version:null,dimension_codes:[],detail:null,readable:false}));
  const review={};['id','run_id','review_run_id','review_date','reviewed_at','generation_no','evaluation_state','overall_score','score_summary','dimension_scores','dimensions','summary','strengths','improvements','advice','score_basis','evidence_coverage','coaching_coverage'].forEach(key=>{if(Object.prototype.hasOwnProperty.call(payload.review||{},key))review[key]=payload.review[key];});
  return {contract_version:payload.contract_version,assessment_kind:payload.assessment_kind,subject:payload.subject,review,evidence,evidence_summary:payload.evidence_summary||{},summary:payload.evidence_summary||{},
    coverageText:(fde?fdeCoverageText:coverageText)((payload.review||{}).evidence_coverage),basisLabel:basisLabel((payload.review||{}).score_basis),
    items:evidence.map((row,index)=>row.readable===true?{key:String(index),readable:true,type:fde?row.source_type:'visit',id:fde?row.source_id:row.visit_id,version:fde?row.source_version:row.visit_version,detail:text(row.detail),dimensionCodes:row.dimension_codes||[]}:{key:String(index),readable:false,type:'',id:null,version:null,detail:'当前无法查看该评估依据',dimensionCodes:[]})};
}
function evidenceMethods(api,session,fde=false,navigate=url=>wx.navigateTo({url})){
  return {
    closeGrowthEvidence(){this.growthEvidenceOwner=null;this.growthEvidenceSeq=(this.growthEvidenceSeq||0)+1;this.setData({growthEvidence:emptyEvidence()});},
    async openGrowthEvidence(e){
      const id=e.currentTarget.dataset.id,action=fde?'profile.fde_read':'profile.sales_read';
      if(!id||this.closed||this.visible===false||!access.can(session(),action))return;
      if(this.growthReadOwner!==this.growthEvidenceContext())return;
      const known=id===this.data.latestReviewId||(this.data.history||[]).some(row=>(fde?row.run_id:row.id)===id);if(!known)return;
      const key=this.growthEvidenceContext(),identity=access.identity(session()),serial=this.growthEvidenceSeq=(this.growthEvidenceSeq||0)+1;
      const current=()=>!this.closed&&this.visible!==false&&serial===this.growthEvidenceSeq&&identity===access.identity(session())&&key===this.growthEvidenceContext();
      this.setData({growthEvidence:{visible:true,loading:true,error:'',reviewId:id,result:null}});
      try{const payload=await (fde?api.getFdeProfileReview(id):api.getSalesGrowthReview(id));if(!current())return;
        if(!payload||payload.assessment_kind!==(fde?'fde_factual_portrait':'sales_competency')||!payload.review||!Array.isArray(payload.evidence))throw Error('评估依据响应不完整');
        const returnedId=payload.review.id||payload.review.run_id||payload.review.review_run_id;if(returnedId&&returnedId!==id)throw Error('评估依据已变化，请重新查看');
        this.growthEvidenceOwner={key,identity};this.setData({growthEvidence:{visible:true,loading:false,error:'',reviewId:id,result:safeEvidence(payload,fde)}});
      }catch(error){if(current()){this.setData({growthEvidence:{visible:true,loading:false,error:error.message||'评估依据暂不可用',reviewId:id,result:null}});if([403,404].includes(error.statusCode))this.clearGrowthAccess();}}
    },
    openGrowthSource(e){
      const owner=this.growthEvidenceOwner;if(!owner||owner.identity!==access.identity(session())||owner.key!==this.growthEvidenceContext()||this.closed||this.visible===false)return;
      const dataset=e.currentTarget.dataset,item=((this.data.growthEvidence.result||{}).items||[]).find(row=>dataset.key!=null?row.key===String(dataset.key):row.type===dataset.kind&&row.id===dataset.id);
      if(!item||!item.readable||!item.id||!['visit','task'].includes(item.type)||!access.can(session(),item.type+'.read'))return;
      navigate(item.type==='visit'?visitDetailUrl({id:item.id}):'/pages/task-detail/index?id='+encodeURIComponent(item.id));
    }
  };
}
module.exports={emptyReview,emptyEvidence,basisLabel,coverageText,fdeCoverageText,coachingCoverageText,scoreInfo,historyPoints,salesDisplay,evidenceMethods};
