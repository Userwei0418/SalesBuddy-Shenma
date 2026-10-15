const access=require('./access');
const materials=require('./opportunityMaterials');
const productLines=require('./opportunityProductLines');
const fieldLabels={quantity:'交易数量',unit_price:'成交单价（元）',usage:'用途',trade_relation:'交易关系',expected_delivery_on:'预计交付日期',expected_start_on:'预计服务开始日期',expected_oa_on:'预计 OA 日期',service_months:'服务月数',expected_account_count:'预计账号数',expected_consumption_amount:'预计消耗含税额（元）',expected_consumption_year:'消耗年度',expected_consumption_quarter:'消耗季度',manual_sales_input:'需销售人工填写',material_lines:'尚无适用物料',all_active_lines_excluded:'所有物料均不在本商机计算确收',distributor_resale:'总代转售，在下游商机登记确收',recognition_rule:'确收规则', 'rule_parameters.monthly_allocation':'按月分摊规则','rule_parameters.oa_cutoff_day':'OA 截止日','rule_parameters.oa_deferral_policy':'OA 延后规则',before_effective_start:'OA 延后排除',outside_requested_quarter:'不在所选季度'};
const valueLabels={manual:'人工填写',delivery:'交付确收',monthly:'按月分摊',consumption:'消耗确收',equal_calendar_months:'按日历月均摊',keep_original_end:'保留原结束月份，排除月份不重分配',shift_full_term:'顺延整个服务期',line:'本物料行',catalog_default:'采用时的目录默认值',ROUND_HALF_UP:'四舍五入到分',direct:'直销',distributor:'总代',downstream:'下游',self_use:'自用',resale:'转售'};
Object.assign(fieldLabels,{material_id:'目录物料',catalog_version_no:'物料目录版本',catalog_snapshot:'物料规则尚未读取',upstream_opportunity_id:'上游经销商商机'});
const asText=value=>typeof value==='string'?value:'';
const decimalText=value=>typeof value==='string'&&/^-?\d+(\.\d+)?$/.test(value)?value:null;
const moneyText=value=>decimalText(value)===null?'未提供':`${value} 元`;
const displayValue=value=>value===true?'是':value===false?'否':value==null||value===''?'未提供':valueLabels[value]||String(value);
const reasonsFor=values=>(Array.isArray(values)?values:[]).map(value=>{
  const key=String(value).split(':').pop();return fieldLabels[key]||`待补充：${key}`;
});
function fields(source,spec){return spec.map(([key,label])=>({label,value:displayValue(source&&source[key])}));}
function amounts(inclusive,exclusive){return `含税参考：${moneyText(inclusive)}；不含税参考：${moneyText(exclusive)}`;}
// Read-only projection. All money/intermediate values remain server decimal strings.
// Never read the live catalog to fill an adopted snapshot's missing explanation.
function previewDisplay(result){
  const trace=result.calculation_trace||{};
  const lines=(Array.isArray(result.lines)?result.lines:[]).map((line,index)=>{
    const t=line.calculation_trace,identity=t&&t.identity||{},definition=t&&t.business_definition;
    const maintained=definition&&definition.status==='maintained'&&definition.value;
    const manual=(line.missing_fields||[]).includes('manual_sales_input');
    const item={key:line.client_key||line.line_id||String(index),name:asText(t&&t.catalog_snapshot&&t.catalog_snapshot.name)||`物料 ${index+1}`,expanded:false,
      statusText:line.status==='excluded'?'本商机不计入确收':manual?'待人工填写':line.status==='complete'?'参考已计算':'信息不足，未计算',
      amountText:line.status==='complete'?amounts(line.amount_inclusive,line.amount_exclusive):'暂无计算金额（不代表 0）',
      reasons:[...reasonsFor(line.missing_fields),...(line.reason?[line.reason]:[])],
      versionText:t?`${line.snapshot_source==='current_catalog'?'未保存物料':`物料行版本 ${displayValue(identity.line_version)}`} · 采用目录版本 ${displayValue(line.catalog_version_no??identity.catalog_version)} · 计算版本 ${displayValue(t.calculator_version)}`:'当前响应未提供计算过程',
      definition:{statusText:maintained?'采用时的规则说明（仅作说明，不参与计算）':definition&&definition.status==='historical_description_not_maintained'?'历史说明未维护':'当前响应未提供规则说明',
        description:maintained?asText(definition.value.description):'',applicability:maintained?asText(definition.value.applicability):'',exceptions:maintained?asText(definition.value.exceptions):'',
        examples:maintained&&Array.isArray(definition.value.examples)?definition.value.examples.map(x=>({scenario:asText(x.scenario),explanation:asText(x.explanation)})):[]},sections:[],months:[]};
    if(!t)return item;
    const section=(title,source,spec,note)=>item.sections.push({title,rows:fields(source,spec),note:asText(note)});
    section('采用的计算规则',t.rule,[['recognition_rule','确收方式'],['tax_rate','冻结税率'],['rounding','金额舍入方式']]);
    const parameters=t.rule&&t.rule.parameters;
    if(parameters)section('采用的规则参数',parameters,[['default_service_months','默认服务月数'],['monthly_allocation','按月分摊方式'],['oa_required','是否需要 OA'],['oa_cutoff_day','OA 截止日'],['oa_deferral_policy','OA 延后方式']].filter(([key])=>key in parameters));
    section('计算输入',t.inputs,Object.entries(fieldLabels).filter(([key])=>Object.prototype.hasOwnProperty.call(t.inputs||{},key)));
    if(t.pricing&&t.pricing.status==='calculated')section('成交金额',t.pricing,[['unrounded_total','数量 × 单价原值（元）'],['total_inclusive','舍入后含税金额（元）'],['quantum','舍入单位（元）'],['rounding','舍入方式']],t.pricing.explanation);
    const allocation=t.monthly_allocation;
    if(allocation){
      section('服务期与分摊',allocation,[['service_months','服务月数'],['service_months_source','月数来源'],['requested_start_on','原开始日期'],['requested_start_month','原开始月份'],['requested_end_month_exclusive','原结束边界月份（不含）'],['effective_start_month','生效开始月份'],['effective_end_month_exclusive','生效结束边界月份（不含）'],['empty_effective_window','生效服务期为空'],['allocation_start_month','分配起始月份'],['allocation_end_month_exclusive','分配结束边界月份（不含）'],['total_inclusive','完整服务期含税金额（元）'],['base_cents_per_month','每月基础分值（分）'],['tail_extra_cents','分配至尾部月份的余分（分）'],['amount_inclusive','本季度计入含税金额（元）']],allocation.explanation);
      if(allocation.oa&&allocation.oa.required)section('OA 生效规则',allocation.oa,[['expected_oa_on','预计 OA 日期'],['configured_cutoff_day','配置截止日'],['effective_cutoff_day','实际截止日'],['cutoff_on','实际截止日期'],['clamped_to_month_end','截止日已按月末调整'],['after_cutoff','已超过截止日'],['permitted_start_month','允许开始月份'],['deferral_policy','延后方式'],['shifted_months','延后月数']]);
      item.months=(allocation.months||[]).map(month=>({month:asText(month.month),amountText:moneyText(month.allocated_inclusive),base:asText(month.base_cents),tail:asText(month.tail_extra_cents),
        statusText:month.included?'计入本季度':reasonsFor(month.exclusion_reasons).join('；')||'本季度不计入'}));
    }
    (t.steps||[]).filter(step=>['delivery','consumption','manual','eligibility','required_inputs'].includes(step.step)).forEach(step=>{
      section('确收过程',step,[['delivery_on','交付日期'],['consumption_amount','消耗含税额（元）'],['consumption_year','消耗年度'],['consumption_quarter','消耗季度'],['included','计入所选季度'],['amount_inclusive','季度含税额（元）']].filter(([key])=>key in step),step.explanation||reasonsFor(step.missing_fields).join('；'));
    });
    if(t.tax&&t.tax.status==='calculated')section('本行税额换算',t.tax,[['amount_inclusive','本季度含税金额（元）'],['rate','采用税率'],['divisor','除数'],['unrounded_exclusive','舍入前不含税金额（元）'],['amount_exclusive','舍入后不含税金额（元）'],['rounding','舍入方式']],t.tax.explanation);
    return item;
  });
  return {summary:result.status==='complete'?amounts(result.recognized_inclusive,result.recognized_exclusive):'暂无完整确收参考（不代表 0）',
    reasons:reasonsFor(trace.not_calculated_reasons||result.missing_fields),explanation:asText(trace.explanation),
    sourceLabel:result.persisted===false?'当前输入试算（尚未保存）':'已保存物料参考',
    periodLabel:result.period?`${result.period.year}年 Q${result.period.quarter}`:'',
    versionText:result.opportunity_version==null?'':`商机版本 ${result.opportunity_version}`,lines};
}
function validateDraftPreview(result,request){
  const period=result&&result.period,lines=result&&result.lines;
  const mismatch=!result||!['complete','incomplete'].includes(result.status)||result.persisted!==false||
    result.reference_kind!=='sales_forecast_draft_reference'||result.draft_revision!==request.draft_revision||
    !period||period.year!==request.year||period.quarter!==request.quarter||
    (result.opportunity_id||null)!==(request.opportunity_id||null)||
    (result.opportunity_version??null)!==(request.expected_opportunity_version??null)||
    !Array.isArray(lines)||lines.length!==request.material_lines.length||
    (Array.isArray(lines)&&new Set(lines.map(line=>line.client_key)).size!==lines.length)||
    request.material_lines.some(input=>{
      const line=Array.isArray(lines)&&lines.find(item=>item.client_key===input.client_key);
      return !line||(line.catalog_version_no??null)!==input.catalog_version_no||
        (input.id&&line.line_id!==input.id)||!['complete','incomplete','excluded'].includes(line.status)||
        (line.status==='complete'&&(decimalText(line.amount_inclusive)===null||decimalText(line.amount_exclusive)===null));
    })||
    (result.status==='complete'&&(decimalText(result.recognized_inclusive)===null||decimalText(result.recognized_exclusive)===null));
  if(mismatch)throw Error('试算结果与当前输入、季度或物料版本不一致，请重试');
}
const emptyPreview=()=>({pending:false,loading:false,error:'',result:null,display:null,draftRevision:null});
function validatePreview(result,row,q,lines,savedVersions){
  const trace=result&&result.calculation_trace;
  const mismatch=!result||!['complete','incomplete'].includes(result.status)||
    (result.opportunity_id!=null&&result.opportunity_id!==row.id)||
    (result.opportunity_version!=null&&row.version_no!=null&&String(result.opportunity_version)!==String(row.version_no))||
    (result.period&&(String(result.period.year)!==String(q.year)||String(result.period.quarter)!==String(q.quarter)))||
    (result.reference_kind!=null&&result.reference_kind!=='sales_forecast_reference')||
    (trace&&(!result.period||result.opportunity_id!==row.id||result.opportunity_version==null||!result.material_versions))||
    (result.material_versions&&(lines||[]).some(line=>line.id&&line.version_no!=null&&String(result.material_versions[line.id])!==String(line.version_no)))||
    (result.material_versions&&savedVersions&&(Object.keys(result.material_versions).length!==Object.keys(savedVersions).length||Object.keys(savedVersions).some(id=>String(result.material_versions[id])!==String(savedVersions[id]))));
  if(mismatch)throw Error('参考的商机、季度或物料版本已变化，请返回刷新后重试');
}
const data={tradeRelations:materials.RELATIONS,materialUsages:materials.USAGES,materialCatalog:[],materialTotal:0,materialQuery:'',materialProductLines:[],materialProductLineId:'',materialsLoading:false,materialsError:'',showMaterialSearch:false,
  opportunityProductLines:[],productLineOptions:[],productLinesLoading:false,productLinesError:'',productLineReadonly:false,productLineSource:'none',legacyProductLine:'',productLineMissingCount:0,
  upstreamOptions:[],upstreamTotal:0,upstreamQuery:'',upstreamLoading:false,upstreamError:'',showUpstreamSearch:false,retiredMaterialLines:[],materialLoadError:'',materialLinesLoading:false,materialPreview:emptyPreview()};
function create(api,session,showModal) {
function guard(component,key) {
  const seq=component[key]=(component[key]||0)+1,source=component.sourceKey,identity=access.identity(session());
  return ()=>!component.closed&&component[key]===seq&&component.sourceKey===source&&access.identity(session())===identity;
}
function editable(c){return !c.properties.disabled&&!c.closed&&!c.data.materialLinesLoading&&!c.data.materialLoadError;}
function changed(c,fields){c.setData({...fields,'form.materials_dirty':true});c.updateProductLines();c.scheduleMaterialPreview();c.emit();}
const methods={
  resetMaterials(){
    clearTimeout(this.materialPreviewTimer);
    this.materialSavedVersions=null;
    ['materialCatalogSeq','materialLineSeq','upstreamSeq','materialPreviewSeq','productLinesSeq'].forEach(key=>{this[key]=(this[key]||0)+1;});
    this.setData({...data,materialCatalog:[],retiredMaterialLines:[],upstreamOptions:[]});
    if(Array.isArray(this.data.form.material_lines))this.setData({'form.material_lines':this.data.form.material_lines.map(materials.lineFor)});
    this.updateProductLines();this.loadProductLines();
    if(this.properties.existing)this.retryMaterialLines();
  },
  updateProductLines(){this.setData(productLines.projection(this.data.form,this.properties.existing,this.data.materialProductLines));},
  async loadProductLines(){
    if(typeof api.getMaterialProductLines!=='function')return;
    const current=guard(this,'productLinesSeq');this.setData({productLinesLoading:true,productLinesError:''});
    try{
      const result=await api.getMaterialProductLines();if(!current())return;
      if(!Array.isArray(result.items)||result.items.some(item=>!item.id||typeof item.name!=='string'))throw Error('产品线目录响应不完整');
      this.setData({materialProductLines:result.items});this.updateProductLines();
    }catch(error){if(current())this.setData({productLinesError:error.message||'产品线目录读取失败，请重试'});}
    finally{if(current())this.setData({productLinesLoading:false});}
  },
  intentProductLine(e){
    if(!editable(this)||this.data.productLineReadonly||this.data.productLinesLoading||this.data.productLinesError)return;
    const id=e.currentTarget.dataset.id,option=this.data.productLineOptions.find(item=>item.id===id);if(!option)return;
    const selected=this.data.form.intent_product_line_ids||[];
    if(!selected.includes(id)&&option.is_active===false)return;
    if(!selected.includes(id)&&selected.length>=productLines.MAX_INTENT_PRODUCT_LINES){
      showModal({title:'已达选择上限',content:`意向产品线最多选择 ${productLines.MAX_INTENT_PRODUCT_LINES} 条，请先取消部分选择。`,showCancel:false,success(){},fail(){}});return;
    }
    this.setData({'form.intent_product_line_ids':selected.includes(id)?selected.filter(value=>value!==id):selected.concat(id),'form.intent_product_lines_dirty':true});
    this.updateProductLines();this.emit();
  },
  async retryMaterialLines(){
    const row=this.properties.existing;if(!row||!row.id)return;
    const current=guard(this,'materialLineSeq');this.setData({materialLinesLoading:true,materialLoadError:''});
    try{
      const result=await api.getOpportunityMaterials(row.id);if(!current())return;
      if(result.opportunity_id!==row.id||result.version_no!==row.version_no||!Array.isArray(result.items))throw Error('商机物料版本已变化，请返回刷新商机再编辑');
      this.materialSavedVersions=Object.fromEntries(result.items.filter(line=>line.version_no!=null).map(line=>[line.id,line.version_no]));
      this.setData({...(!Array.isArray(this.data.form.material_lines)?{'form.material_lines':result.items.map(materials.lineFor),'form.trade_relation':result.trade_relation||'','form.upstream_opportunity_id':result.upstream_opportunity_id||''}:{}),retiredMaterialLines:(result.retired_items||[]).map(materials.lineFor)});this.updateProductLines();this.emit();
    }catch(error){if(current())this.setData({materialLoadError:error.message||'物料读取失败，请重试'});}
    finally{if(current()){this.setData({materialLinesLoading:false});this.scheduleMaterialPreview();}}
  },
  openMaterials(){if(!editable(this))return;this.setData({showMaterialSearch:true});return this.loadMaterials();},
  closeMaterials(){this.materialCatalogSeq=(this.materialCatalogSeq||0)+1;this.setData({showMaterialSearch:false,materialsLoading:false});},
  materialSearch(e){this.setData({materialQuery:String(e.detail.value||'').slice(0,100)});return this.loadMaterials();},
  materialProductLine(e){this.setData({materialProductLineId:e.detail.value||''});return this.loadMaterials();},
  async loadMaterials(e){
    const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more);if(more&&this.data.materialsLoading)return;
    const current=guard(this,'materialCatalogSeq');this.setData({materialsLoading:true,materialsError:'',...(!more?{materialCatalog:[],materialTotal:0}:{})});
    try{
      const [result]=await Promise.all([api.listMaterials({q:this.data.materialQuery.trim(),product_line_id:this.data.materialProductLineId,status:'active',limit:30,offset:more?this.data.materialCatalog.length:0}),this.loadProductLines()]);
      if(!current())return;
      if(!Array.isArray(result.items)||!Number.isInteger(result.total))throw Error('物料目录响应不完整');
      this.setData({materialCatalog:more?this.data.materialCatalog.concat(result.items):result.items,materialTotal:result.total});
      this.updateProductLines();
    }catch(error){if(current())this.setData({materialsError:error.message||'物料目录加载失败，请重试'});}
    finally{if(current())this.setData({materialsLoading:false});}
  },
  chooseMaterial(e){
    if(!editable(this))return;const item=this.data.materialCatalog.find(x=>x.id===e.currentTarget.dataset.id);if(!item||item.is_active===false)return;
    const lines=this.data.form.material_lines||[];if(lines.length>=200){this.setData({materialsError:'物料行最多200条'});return;}
    changed(this,{'form.material_lines':lines.concat(materials.fromCatalog(item)),showMaterialSearch:false});
  },
  materialInput(e){
    if(!editable(this))return;const {key,field}=e.currentTarget.dataset;if(!materials.FIELDS.includes(field))return;
    changed(this,{'form.material_lines':(this.data.form.material_lines||[]).map(row=>row.client_key===key?materials.decorate({...row,[field]:e.detail.value}):row)});
  },
  materialUsage(e){
    if(!editable(this)||!materials.USAGES.some(x=>x.value===e.detail.value))return;
    changed(this,{'form.material_lines':(this.data.form.material_lines||[]).map(row=>row.client_key===e.currentTarget.dataset.key?{...row,usage:e.detail.value}:row)});
  },
  async removeMaterial(e){
    if(!editable(this))return;const key=e.currentTarget.dataset.key,row=(this.data.form.material_lines||[]).find(x=>x.client_key===key);if(!row)return;
    const source=this.sourceKey,identity=access.identity(session());
    const result=await new Promise(resolve=>showModal({title:row.id?'停用此物料行？':'移除此物料行？',content:'保存后移出当前销售计划，已确认实际和历史记录保留。',success:resolve,fail:()=>resolve({confirm:false})}));
    if(!result.confirm||source!==this.sourceKey||identity!==access.identity(session())||!editable(this))return;
    changed(this,{'form.material_lines':this.data.form.material_lines.filter(x=>x.client_key!==key)});
  },
  tradeRelation(e){if(!editable(this)||!materials.RELATIONS.some(x=>x.value===e.detail.value))return;changed(this,{'form.trade_relation':e.detail.value,...(e.detail.value!=='downstream'?{'form.upstream_opportunity_id':'','form.upstream_opportunity_name':''}:{})});},
  openUpstreams(){if(!editable(this))return;this.setData({showUpstreamSearch:true});return this.loadUpstreams();},
  closeUpstreams(){this.upstreamSeq=(this.upstreamSeq||0)+1;this.setData({showUpstreamSearch:false,upstreamLoading:false});},
  upstreamSearch(e){this.setData({upstreamQuery:String(e.detail.value||'').slice(0,100)});return this.loadUpstreams();},
  async loadUpstreams(e){
    const more=!!(e&&e.currentTarget&&e.currentTarget.dataset.more);if(more&&this.data.upstreamLoading)return;
    const current=guard(this,'upstreamSeq');this.setData({upstreamLoading:true,upstreamError:'',...(!more?{upstreamOptions:[],upstreamTotal:0}:{})});
    try{
      const result=await api.listUpstreamOpportunities({q:this.data.upstreamQuery.trim(),customer_id:this.properties.customerId,opportunity_id:this.properties.existing&&this.properties.existing.id,offset:more?this.data.upstreamOptions.length:0,limit:30});
      if(!current())return;
      if(!Array.isArray(result.items)||!Number.isInteger(result.total))throw Error('上游商机目录响应不完整');
      this.setData({upstreamOptions:more?this.data.upstreamOptions.concat(result.items):result.items,upstreamTotal:result.total});
    }catch(error){if(current())this.setData({upstreamError:error.message||'上游商机读取失败，请重试'});}
    finally{if(current())this.setData({upstreamLoading:false});}
  },
  chooseUpstream(e){if(!editable(this))return;const row=this.data.upstreamOptions.find(x=>x.id===e.currentTarget.dataset.id);if(!row)return;changed(this,{'form.upstream_opportunity_id':row.id,'form.upstream_opportunity_name':row.name,showUpstreamSearch:false});},
  toggleMaterialTrace(e){
    const preview=this.data.materialPreview;if(!preview.display)return;
    this.setData({'materialPreview.display.lines':preview.display.lines.map(line=>line.key===e.currentTarget.dataset.key?{...line,expanded:!line.expanded}:line)});
  },
  scheduleMaterialPreview(){
    clearTimeout(this.materialPreviewTimer);
    const current=guard(this,'materialPreviewSeq');
    const ready=typeof api.previewMaterialDraft==='function'&&!this.closed&&!this.data.materialLinesLoading&&!this.data.materialLoadError&&
      !!(this.data.form.material_lines||[]).length&&!!(this.data.quarterOptions||[])[this.data.quarterIndex];
    this.setData({materialPreview:{...emptyPreview(),pending:ready}});
    if(ready)this.materialPreviewTimer=setTimeout(()=>{if(current())this.previewMaterials();},350);
  },
  async previewMaterials(){
    clearTimeout(this.materialPreviewTimer);
    const row=this.properties.existing,q=(this.data.quarterOptions||[])[this.data.quarterIndex];
    const draft=typeof api.previewMaterialDraft==='function';
    if(!q||this.closed||this.data.materialLinesLoading||this.data.materialLoadError||(!draft&&(!row||this.data.form.materials_dirty)))return;
    const guarded=guard(this,'materialPreviewSeq');
    const context=()=>JSON.stringify([this.properties.customerId,this.properties.existing,this.data.quarterOptions[this.data.quarterIndex],this.data.form.material_lines,this.data.form.trade_relation,this.data.form.upstream_opportunity_id,this.data.form.owner_team_id]);
    const snapshot=context(),current=()=>guarded()&&(draft||!this.data.form.materials_dirty)&&context()===snapshot;
    const revision=this.materialDraftRevision=(this.materialDraftRevision||0)+1;
    this.setData({materialPreview:{...emptyPreview(),loading:true,draftRevision:revision}});
    try{
      const request=draft?{...materials.draftPayload(this.data.form),year:q.year,quarter:q.quarter,draft_revision:revision,
        customer_id:this.properties.customerId,...(row?{opportunity_id:row.id,expected_opportunity_version:row.version_no}:{})}:null;
      const result=draft?await api.previewMaterialDraft(request):await api.previewOpportunityMaterials(row.id,{year:q.year,quarter:q.quarter});
      if(!current())return;
      if(draft)validateDraftPreview(result,request);else validatePreview(result,row,q,this.data.form.material_lines,this.materialSavedVersions);
      this.setData({materialPreview:{...emptyPreview(),draftRevision:revision,result,display:previewDisplay(result)}});
    }
    catch(error){if(current())this.setData({materialPreview:{...emptyPreview(),draftRevision:revision,error:error.statusCode===409?`${error.message||'商机或目录版本已变化'}。输入已保留，请核对最新商机和物料目录后重试。`:error.message||'参考暂不可用，请重试'}});}
  }
};
return methods;
}
module.exports={data,create,previewDisplay};
