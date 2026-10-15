require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const materials=require('../miniprogram/utils/opportunityMaterials');
const formModule=require('../miniprogram/utils/opportunityMaterialForm');
const opp=require('../miniprogram/utils/opportunity');
const finance=require('../miniprogram/utils/financialDisplay');
const catalog={id:'sku',version_no:2,name:'合同中的物料',unit:'套',catalog_price:'100',recognition_rule:'monthly',rule_parameters:{monthly_allocation:'equal_calendar_months'},is_active:true};
function line(extra={}){return {...materials.fromCatalog(catalog),quantity:'2',unit_price:'12.35',usage:'self_use',...extra};}
function setup(api={}){
  let identity={workspaceId:'w',userId:'u',permissionVersion:1};
  const page={...formModule.create(api,()=>identity,options=>options.success({confirm:true})),properties:{customerId:'c',existing:null,disabled:false},sourceKey:'one',closed:false,
    data:JSON.parse(JSON.stringify({...formModule.data,form:opp.formFor(null)})),emit(){},setData(values){for(const [key,value]of Object.entries(values)){const parts=key.split('.');let target=this.data;while(parts.length>1)target=target[parts.shift()];target[parts[0]]=value;}}};
  return {page,switchIdentity(){identity={...identity,userId:'other'};}};
}
test('同SKU多批次保留不同稳定行ID，目录价格不代填成交价',()=>{
  const a=line({id:'batch-a'}),b=line({id:'batch-b'});
  const p=materials.payload({trade_relation:'direct',material_lines:[a,b]});
  assert.deepEqual(p.material_lines.map(x=>x.id),['batch-a','batch-b']);assert.equal(p.material_lines[0].material_id,p.material_lines[1].material_id);
  assert.equal(materials.fromCatalog(catalog).unit_price,'');assert.equal(materials.fromCatalog(catalog).usage,'');
  assert.notEqual(materials.fromCatalog(catalog).client_key,materials.fromCatalog(catalog).client_key);
});
test('未加载旧草稿不发送material_lines，[]才是显式停用；不从销售渠道推断关系',()=>{
  assert.deepEqual(materials.payload({}),{});assert.deepEqual(materials.payload({material_lines:null}),{});
  assert.deepEqual(materials.payload({material_lines:[]}),{material_lines:[]});
  assert.equal(materials.formFields({sales_channel:'direct',customer_type:'dealer'}).trade_relation,'');
  assert.throws(()=>materials.payload({material_lines:[line()]}),/交易关系/);
  assert.throws(()=>materials.payload({trade_relation:'downstream',material_lines:[line()]}),/上游/);
});
test('数量只乘单价一次且按分精确四舍五入，月数/账号数不参与；不以参考覆盖ACV',()=>{
  assert.equal(materials.lineTotal('0.125','0.04'),'0.01');assert.equal(materials.lineTotal('2.5','12.35'),'30.88');
  assert.equal(materials.lineTotal(' 0001.0 ',' 0002.50 '),'2.50');
  assert.equal(materials.lineTotal('100000000000','999999.99'),'99999999000000000.00');
  const row=line({service_months:'12',expected_account_count:'30'});assert.equal(materials.decorate(row).line_amount_text,'24.70');
  const payload=opp.payload({...opp.formFor(null),name:'合成测试',amount:'80',stageIndex:0,expected_close_date:'2026-12-01',trade_relation:'direct',material_lines:[row]},null);
  assert.equal(payload.amount,800000);assert.equal(payload.material_lines[0].unit_price,'12.35');assert.equal(payload.material_lines[0].line_amount,undefined);
});
test('聚合完整50加部分20只显示小计70，单项完整性互不影响，空集合确认0不看count',()=>{
  const example=require('./helpers/finance-periods-v2.json').aggregate_complete_plus_partial;
  assert.equal(finance.aggregate(example,'recognized').amountText,'已登记小计 70元');
  const zero=finance.aggregate({...example,recognized_amount:'0',recognized_complete:true,recognized_count:0,recognized_incomplete_period_count:0,recognized_missing_material_count:0},'recognized');
  assert.equal(zero.amountText,'0元');assert.deepEqual(zero.qualityHints,[]);
  assert.equal(finance.aggregate(example,'collection').amountText,'未登记');
});
test('物料边界：数量>0，单价可0，日期有效，消耗周期配对，旧快照保留',()=>{
  for(const patch of [{quantity:'0'},{unit_price:'-1'},{quantity:'1.12345'},{service_months:'121'},{expected_account_count:'1.2'},{expected_delivery_on:'2026-02-30'},{expected_consumption_year:'2026'},{usage:''}])assert.throws(()=>materials.payload({trade_relation:'direct',material_lines:[line(patch)]}));
  const p=materials.payload({trade_relation:'direct',material_lines:[line({unit_price:'0',expected_consumption_year:'2026',expected_consumption_quarter:'4',expected_consumption_amount:'0'})]});
  assert.equal(p.material_lines[0].unit_price,'0');assert.equal(p.material_lines[0].expected_consumption_amount,'0');
  const old=materials.lineFor({id:'old',material_id:'sku',catalog_snapshot:{name:'停售前名称',is_active:false},quantity:'1',unit_price:'5',usage:'self_use'});
  assert.equal(old.materialLabel,'停售前名称');assert.equal(materials.payload({trade_relation:'direct',material_lines:[old]}).material_lines[0].id,'old');
});
test('目录空结果如实显示，失败可重试，不用内置SKU补齐',async()=>{
  let fail=true;const {page}=setup({listMaterials:async()=>{if(fail)throw Error('离线');return {items:[],total:0};},getMaterialProductLines:async()=>({items:[]})});
  await page.openMaterials();assert.match(page.data.materialsError,/离线/);fail=false;await page.loadMaterials();assert.equal(page.data.materialsError,'');assert.equal(page.data.materialCatalog.length,0);
});
test('目录搜索与身份切换的迟到响应不会覆盖当前选择',async()=>{
  const pending=[];const h=setup({listMaterials:()=>new Promise(resolve=>pending.push(resolve)),getMaterialProductLines:async()=>({items:[]})});
  const first=h.page.loadMaterials(),second=h.page.materialSearch({detail:{value:'新'}});
  pending[1]({items:[{...catalog,id:'new'}],total:1});await second;pending[0]({items:[catalog],total:1});await first;assert.equal(h.page.data.materialCatalog[0].id,'new');
  const third=h.page.loadMaterials();h.switchIdentity();pending[2]({items:[catalog],total:1});await third;assert.equal(h.page.data.materialCatalog.length,0);
});
test('物料重读保留草稿修改；版本冲突阻止覆盖，停用只移除目标行',async()=>{
  let version=4;const {page}=setup({getOpportunityMaterials:async()=>({opportunity_id:'o',version_no:version,trade_relation:'direct',items:[line({id:'a',unit_price:'1'})],retired_items:[line({id:'retired'})]})});
  page.properties.existing={id:'o',version_no:4};page.data.form={...page.data.form,trade_relation:'direct',material_lines:[line({id:'a',unit_price:'80'}),line({id:'b'})]};
  await page.retryMaterialLines();assert.equal(page.data.form.material_lines[0].unit_price,'80');assert.equal(page.data.retiredMaterialLines[0].id,'retired');
  const key=page.data.form.material_lines[0].client_key;await page.removeMaterial({currentTarget:{dataset:{key}}});assert.equal(page.data.form.material_lines.length,1);assert.equal(page.data.form.material_lines[0].id,'b');
  version=5;await page.retryMaterialLines();assert.match(page.data.materialLoadError,/版本/);assert.equal(page.data.form.material_lines.length,1);
});
test('物料参考不回填预测或实际，未保存变更不能取旧参考冒充新值',async()=>{
  let calls=0;const {page}=setup({previewOpportunityMaterials:async()=>{calls++;return {status:'incomplete',recognized_inclusive:null};}});
  page.properties.existing={id:'o'};page.data.quarterOptions=[{year:2026,quarter:4}];page.data.quarterIndex=0;page.data.form.quarters=[{year:2026,quarter:4,recognized:'80',collection:'100'}];
  await page.previewMaterials();assert.equal(page.data.materialPreview.result.status,'incomplete');assert.equal(page.data.form.quarters[0].recognized,'80');
  page.data.form.materials_dirty=true;await page.previewMaterials();assert.equal(calls,1);
});
test('不完整日期和税口径明确提示；未采用旧参考不清空运营金额',()=>{
  const ops=finance.bucket({amount:'50',source:'operations',status:'confirmed',complete:true,reference_changed:true,entry_amount:'999'});
  assert.equal(ops.amountText,'50元');assert.match(ops.qualityHints.join(''),/保留/);
  const unknown=finance.bucket({amount:null,tax_unknown:true,known_exclusive_subtotal:'20'});assert.match(unknown.qualityHints.join(''),/税口径/);
  assert.match(finance.hints({period_precision_warning:'not_allocated_to_days',period_only_collection_amount:'100'}).join(''),/100元/);
});

test('MINI-R01 采用来源状态与金额完整性独立：税未知和冲突保留小计，撤回不复活',()=>{
  for(const status of ['single_source','conflict','resolved','partial']){
    const row=finance.bucket({status,source:'entries',amount:null,confirmed_subtotal:'20',known_exclusive_subtotal:'20',tax_unknown:true,complete:false,entry_amount:'500'});
    assert.equal(row.amountText,'已登记小计 20元');assert.equal(row.displayAmount,'20');assert.equal(row.isSubtotal,true);
    assert.match(row.qualityHints.join(''),/税口径.*不含税/);assert.match(row.qualityHints.join(''),/实际尚未完整/);
    if(status==='conflict')assert.match(row.qualityHints.join(''),/来源待运营核对/);
  }
  assert.equal(finance.bucket({status:'single_source',amount:null,known_exclusive_subtotal:'20',tax_unknown:true}).amountText,'已登记小计 20元');
  assert.equal(finance.bucket({status:'single_source',amount:null,confirmed_subtotal:'0',complete:false}).amountText,'已登记小计 0元');
  const withdrawn=finance.bucket({status:'withdrawn',amount:'50',confirmed_subtotal:'20',known_exclusive_subtotal:'20'});
  assert.equal(withdrawn.amountText,'已撤回');assert.equal(withdrawn.displayAmount,null);assert.equal(withdrawn.isSubtotal,false);
  assert.equal(finance.bucket({amount:null,entry_amount:'50',history:[{amount:'50'}]}).amountText,'未登记');
});

// Contract v1.5, Backend 1d6e988. Values below are the Data six-case review's
// cross-year keep-original-end example, not calculated by the UI fixture.
function traceReference(){
  const months=[['2026-11','14.28','0',true,false],['2026-12','14.28','0',true,false],['2027-01','14.29','1',true,true],['2027-02','14.29','1',true,true],['2027-03','14.29','1',false,true],['2027-04','14.29','1',false,false],['2027-05','14.29','1',false,false]];
  return {opportunity_id:'o',opportunity_version:4,period:{year:2027,quarter:1},reference_kind:'sales_forecast_reference',reference_fingerprint:'not-an-authorization',material_versions:{a:7},
    status:'complete',recognized_inclusive:'14.29',recognized_exclusive:'12.65',missing_fields:[],calculation_trace:{not_calculated_reasons:[],explanation:'累加逐行舍入金额，不统一除税'},
    lines:[{line_id:'a',status:'complete',amount_inclusive:'14.29',amount_exclusive:'12.65',missing_fields:[],calculation_trace:{
      identity:{line_id:'a',line_version:'7',catalog_id:'sku',catalog_version:'2'},calculator_version:'material_reference.v1',catalog_snapshot:{name:'采用时的物料'},catalog_snapshot_sha256:'hidden-snapshot-hash',rule_sha256:'hidden-rule-hash',
      business_definition:{status:'maintained',participates_in_calculation:false,value:{description:'原规则\n保留换行',applicability:'原范围',exceptions:'原例外',examples:[{scenario:'跨年',explanation:'尾部补分'}]}},
      rule:{recognition_rule:'monthly',tax_rate:'0.13',rounding:'ROUND_HALF_UP'},inputs:{quantity:'1.2500',unit_price:'80.01',expected_account_count:'99999'},
      pricing:{status:'calculated',unrounded_total:'100.012500',total_inclusive:'100.01',quantum:'0.01',rounding:'ROUND_HALF_UP',explanation:'账号数与月数不再乘入成交总额'},
      monthly_allocation:{service_months:'7',service_months_source:'catalog_default',requested_start_on:'2026-11-20',requested_start_month:'2026-11',requested_end_month_exclusive:'2027-06',effective_start_month:'2027-03',effective_end_month_exclusive:'2027-06',empty_effective_window:false,allocation_start_month:'2026-11',allocation_end_month_exclusive:'2027-06',total_inclusive:'100.01',base_cents_per_month:'1428',tail_extra_cents:'5',amount_inclusive:'14.29',explanation:'保留原结束月份，OA 剔除月份不重分配',
        oa:{required:true,expected_oa_on:'2027-02-16',configured_cutoff_day:'15',effective_cutoff_day:'15',cutoff_on:'2027-02-15',clamped_to_month_end:false,after_cutoff:true,permitted_start_month:'2027-03',deferral_policy:'keep_original_end',shifted_months:'4'},
        months:months.map(([month,amount,tail,oa,quarter])=>({month,allocated_inclusive:amount,base_cents:'1428',tail_extra_cents:tail,excluded_by_oa:oa,within_quarter:quarter,included:!oa&&quarter,exclusion_reasons:[...(oa?['before_effective_start']:[]),...(!quarter?['outside_requested_quarter']:[])]}))},
      steps:[],tax:{status:'calculated',amount_inclusive:'14.29',rate:'0.13',divisor:'1.13',unrounded_exclusive:'12.64601769911504424778761062',amount_exclusive:'12.65',rounding:'ROUND_HALF_UP',explanation:'逐行舍入后汇总'}
    }}]};
}
function previewSetup(api={}){
  const h=setup(api);h.page.properties.existing={id:'o',version_no:4};
  h.page.data.quarterOptions=[{year:2027,quarter:1,label:'2027 Q1'},{year:2027,quarter:2,label:'2027 Q2'}];h.page.data.quarterIndex=0;
  h.page.data.form.material_lines=[line({id:'a'})];return h;
}
function sectionValue(line,title,label){return line.sections.find(x=>x.title===title).rows.find(x=>x.label===label).value;}
test('TRACE 保存时说明和版本原样展示，目录改名/最新说明不参与，哈希不进入展示',()=>{
  const source=traceReference(),before=JSON.stringify(source),display=formModule.previewDisplay(source),item=display.lines[0];
  assert.equal(item.name,'采用时的物料');assert.equal(item.definition.description,'原规则\n保留换行');assert.equal(item.definition.applicability,'原范围');assert.equal(item.definition.exceptions,'原例外');
  assert.deepEqual(item.definition.examples,[{scenario:'跨年',explanation:'尾部补分'}]);assert.match(item.versionText,/物料行版本 7.*采用目录版本 2.*material_reference.v1/);
  assert.equal(item.expanded,false);assert.doesNotMatch(JSON.stringify(display),/hidden-|not-an-authorization/);assert.equal(JSON.stringify(source),before);
  source.lines[0].calculation_trace.business_definition={status:'historical_description_not_maintained',value:null,participates_in_calculation:false};
  source.lines[0].calculation_trace.catalog_snapshot.rule_parameters={business_definition:{description:'不应借用的说明'}};
  const old=formModule.previewDisplay(source).lines[0].definition;assert.equal(old.description,'');assert.match(old.statusText,/历史说明未维护/);
  source.lines[0].calculation_trace.business_definition={status:'maintained',value:{description:'',examples:[]}};
  assert.doesNotMatch(formModule.previewDisplay(source).lines[0].definition.statusText,/历史说明未维护/);
});
test('TRACE 跨年、OA排除及尾部分值全量显示，不重算金额或多乘账号数',()=>{
  const d=formModule.previewDisplay(traceReference()),item=d.lines[0];
  assert.equal(d.summary,'含税参考：14.29 元；不含税参考：12.65 元');assert.equal(item.months.length,7);
  assert.equal(sectionValue(item,'成交金额','数量 × 单价原值（元）'),'100.012500');
  assert.equal(sectionValue(item,'计算输入','预计账号数'),'99999');assert.equal(sectionValue(item,'服务期与分摊','月数来源'),'采用时的目录默认值');
  assert.equal(sectionValue(item,'服务期与分摊','原结束边界月份（不含）'),'2027-06');assert.equal(sectionValue(item,'服务期与分摊','生效开始月份'),'2027-03');
  assert.match(item.months[0].statusText,/OA 延后排除.*不在所选季度/);assert.equal(item.months[4].statusText,'计入本季度');assert.equal(item.months[6].tail,'1');
  assert.equal(sectionValue(item,'本行税额换算','舍入前不含税金额（元）'),'12.64601769911504424778761062');
  const changed=traceReference();changed.recognized_inclusive='99999999000000000.00';changed.lines[0].calculation_trace.pricing.total_inclusive='12345678901234567890.01';
  assert.match(formModule.previewDisplay(changed).summary,/99999999000000000\.00 元/);
  assert.equal(sectionValue(formModule.previewDisplay(changed).lines[0],'成交金额','舍入后含税金额（元）'),'12345678901234567890.01');
});
test('TRACE 整期顺延和月末截止日直接采用后端值；120月不截断',()=>{
  const source=traceReference(),a=source.lines[0].calculation_trace.monthly_allocation;
  a.oa={...a.oa,deferral_policy:'shift_full_term',configured_cutoff_day:'31',effective_cutoff_day:'28',cutoff_on:'2027-02-28',clamped_to_month_end:true,after_cutoff:false};
  a.effective_end_month_exclusive='2027-10';a.months=Array.from({length:120},(_,i)=>({...a.months[0],month:`period-${i}`}));
  const item=formModule.previewDisplay(source).lines[0];
  assert.equal(sectionValue(item,'OA 生效规则','延后方式'),'顺延整个服务期');assert.equal(sectionValue(item,'OA 生效规则','截止日已按月末调整'),'是');
  assert.equal(sectionValue(item,'OA 生效规则','实际截止日'),'28');assert.equal(sectionValue(item,'服务期与分摊','生效结束边界月份（不含）'),'2027-10');assert.equal(item.months.length,120);
});
test('TRACE 明确0、缺料null、manual、全部excluded区分，不按missing_fields空推断完整',()=>{
  const zero=traceReference();zero.recognized_inclusive=zero.recognized_exclusive='0.00';zero.lines[0].amount_inclusive=zero.lines[0].amount_exclusive='0.00';
  assert.equal(formModule.previewDisplay(zero).summary,'含税参考：0.00 元；不含税参考：0.00 元');
  for(const [status,missing,reason,expected] of [['incomplete',['expected_start_on'],'','信息不足'],['incomplete',['manual_sales_input'],'','待人工填写'],['excluded',[],'转售确收在下游商机登记','本商机不计入']]){
    const r=traceReference();r.status='incomplete';r.recognized_inclusive=r.recognized_exclusive=null;r.lines[0]={...r.lines[0],status,amount_inclusive:null,amount_exclusive:null,missing_fields:missing,reason};
    r.calculation_trace.not_calculated_reasons=status==='excluded'?['all_active_lines_excluded']:missing.map(x=>'a:'+x);
    const d=formModule.previewDisplay(r);assert.match(d.summary,/暂无完整/);assert.doesNotMatch(d.summary,/：0/);assert.match(d.lines[0].statusText,new RegExp(expected));assert.match(d.lines[0].amountText,/不代表 0/);assert.ok(d.reasons.length);
  }
  const numeric=traceReference();numeric.recognized_inclusive=0;assert.match(formModule.previewDisplay(numeric).summary,/含税参考：未提供/);
});
test('TRACE 旧响应仍可显示参考，缺过程不虚构；展开收起不改源响应或预测',async()=>{
  const r=traceReference(),{page}=previewSetup({previewOpportunityMaterials:async()=>r});const original=JSON.stringify(page.data.form);
  await page.previewMaterials();page.toggleMaterialTrace({currentTarget:{dataset:{key:'a'}}});assert.equal(page.data.materialPreview.display.lines[0].expanded,true);
  page.toggleMaterialTrace({currentTarget:{dataset:{key:'a'}}});assert.equal(page.data.materialPreview.display.lines[0].expanded,false);
  assert.equal(JSON.stringify(page.data.form),original);assert.equal(r.lines[0].expanded,undefined);
  const legacy=formModule.previewDisplay({status:'complete',recognized_inclusive:'0.00',recognized_exclusive:'0.00',lines:[{line_id:'a',status:'complete',amount_inclusive:'0.00',amount_exclusive:'0.00'}]});
  assert.match(legacy.lines[0].versionText,/未提供计算过程/);assert.match(legacy.lines[0].definition.statusText,/未提供规则说明/);assert.deepEqual(legacy.lines[0].sections,[]);
});
test('TRACE 响应商机/季度/物料版本错配清空结果并可重试',async()=>{
  for(const patch of [{opportunity_id:'other'},{opportunity_version:5},{period:{year:2027,quarter:2}},{material_versions:{a:8}},{reference_kind:'actual'},{period:null}]){
    let bad=true;const {page}=previewSetup({previewOpportunityMaterials:async()=>({...traceReference(),...(bad?patch:{})})});page.materialSavedVersions={a:7};
    await page.previewMaterials();assert.match(page.data.materialPreview.error,/版本已变化/);assert.equal(page.data.materialPreview.result,null);assert.equal(page.data.materialPreview.display,null);
    bad=false;await page.previewMaterials();assert.equal(page.data.materialPreview.error,'');assert.equal(page.data.materialPreview.display.lines.length,1);
  }
});
test('TRACE 修改物料/身份/页面/季度后到达的旧结果均丢弃，重复请求仅最新有效',async()=>{
  for(const change of [h=>h.page.materialInput({currentTarget:{dataset:{key:'a',field:'unit_price'}},detail:{value:'3'}}),h=>h.switchIdentity(),h=>{h.page.sourceKey='other';},h=>{h.page.closed=true;},h=>{h.page.data.quarterIndex=1;}]){
    let resolve;const h=previewSetup({previewOpportunityMaterials:()=>new Promise(r=>resolve=r)}),pending=h.page.previewMaterials();
    change(h);resolve(traceReference());await pending;assert.equal(h.page.data.materialPreview.result,null);assert.equal(h.page.data.materialPreview.display,null);
  }
  const calls=[],{page}=previewSetup({previewOpportunityMaterials:()=>new Promise(r=>calls.push(r))});
  const first=page.previewMaterials(),second=page.previewMaterials();calls[1](traceReference());await second;
  calls[0]({...traceReference(),recognized_inclusive:'999.00'});await first;assert.match(page.data.materialPreview.display.summary,/14.29/);
});
test('TRACE 实际季度处理器清除已显示和等待中的参考，预测值不受影响',async()=>{
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');let definition;
  const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:x=>definition=x,require:name=>name.endsWith('apiClient')?{}:require(path.resolve(path.dirname(file),name)),getApp:()=>({globalData:{session:{}}}),wx:{},setTimeout,clearTimeout});
  let resolve;const {page}=previewSetup({previewOpportunityMaterials:()=>new Promise(r=>resolve=r)});
  page.quarter=definition.methods.quarter;page.loadQuarter=definition.methods.loadQuarter;page.updatePredictions=()=>{};
  page.data.form.quarters=[{year:2027,quarter:2,recognized:'88',collection:'99'}];page.data.materialPreview={result:traceReference(),display:formModule.previewDisplay(traceReference())};
  page.quarter({detail:{value:1}});assert.equal(page.data.materialPreview.result,null);assert.equal(page.data.materialPreview.display,null);assert.equal(page.data.recognized,'88');
  page.quarter({detail:{value:0}});const pending=page.previewMaterials();page.quarter({detail:{value:1}});resolve(traceReference());await pending;
  assert.equal(page.data.materialPreview.loading,false);assert.equal(page.data.materialPreview.result,null);assert.equal(page.data.recognized,'88');
});
