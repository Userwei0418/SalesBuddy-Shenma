require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const materials=require('../miniprogram/utils/opportunityMaterials');
const behavior=require('../miniprogram/utils/opportunityMaterialForm');
const opp=require('../miniprogram/utils/opportunity');
const productLines=require('../miniprogram/utils/opportunityProductLines');
const catalog={id:'sku',version_no:3,name:'按月服务',product_line_id:'cloud',product_line_name:'云服务',recognition_rule:'monthly',rule_parameters:{oa_required:true},is_active:true};
const line=patch=>({...materials.fromCatalog(catalog),client_key:'draft-a',quantity:'2',unit_price:'10.00',usage:'self_use',...patch});
function setup(api={}){
  let identity={workspaceId:'w',userId:'u',permissionVersion:1};
  const page={...behavior.create(api,()=>identity,options=>options.success({confirm:true})),properties:{customerId:'c',existing:null,disabled:false},sourceKey:'c-new',closed:false,
    data:{...structuredClone(behavior.data),form:{...opp.formFor(null),trade_relation:'direct',material_lines:[line()],quarters:[{year:2026,quarter:4,recognized:'100',collection:'70'}]},quarterOptions:[{year:2026,quarter:4,label:'2026 Q4'},{year:2027,quarter:1,label:'2027 Q1'}],quarterIndex:0},
    emit(){},setData(values){for(const [key,value]of Object.entries(values)){const keys=key.split('.');let target=this.data;while(keys.length>1)target=target[keys.shift()];target[keys[0]]=value;}}};
  return {page,switchIdentity(){identity={...identity,userId:'other'};}};
}
function result(request,patch={}){
  return {status:'complete',recognized_inclusive:'20.00',recognized_exclusive:'17.70',draft_revision:request.draft_revision,period:{year:request.year,quarter:request.quarter},
    opportunity_id:request.opportunity_id||null,opportunity_version:request.expected_opportunity_version??null,reference_kind:'sales_forecast_draft_reference',persisted:false,missing_fields:[],
    lines:request.material_lines.map(row=>({client_key:row.client_key,line_id:row.id||row.client_key,catalog_version_no:row.catalog_version_no,snapshot_source:row.id?'saved_line':'current_catalog',
      status:'complete',amount_inclusive:'20.00',amount_exclusive:'17.70',missing_fields:[],calculation_trace:{catalog_snapshot:{name:'按月服务'},identity:{catalog_version:row.catalog_version_no},calculator_version:'v1'}})),...patch};
}
function input(page,value){page.materialInput({currentTarget:{dataset:{key:'draft-a',field:'unit_price'}},detail:{value}});}
function timers(t){
  const pending=new Map();let serial=0;
  t.mock.method(global,'setTimeout',(fn,ms)=>{assert.equal(ms,350);pending.set(++serial,fn);return serial;});
  t.mock.method(global,'clearTimeout',id=>pending.delete(id));
  return {pending,async tick(){const fns=[...pending.values()];pending.clear();for(const fn of fns)await fn();await Promise.resolve();await Promise.resolve();}};
}
function component(){
  let definition;const file=path.resolve(__dirname,'../miniprogram/components/opportunity-form/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Component:v=>definition=v,require:n=>n.endsWith('apiClient')?{}:require(path.resolve(path.dirname(file),n)),getApp:()=>({globalData:{session:{}}}),wx:{},setTimeout,clearTimeout});return definition;
}
test('FIN-R 新建未保存也试算，同SKU两批次各带key和目录版本，不提交预测或实际',async()=>{
  const requests=[];const {page}=setup({previewMaterialDraft:async body=>{requests.push(body);return result(body);}});
  page.data.form.material_lines.push(line({client_key:'draft-b',unit_price:'0'}));page.data.form.materials_dirty=true;
  const before=structuredClone(page.data.form);await page.previewMaterials();
  const body=requests[0];assert.equal(body.opportunity_id,undefined);assert.equal(body.customer_id,'c');assert.deepEqual(body.material_lines.map(x=>x.client_key),['draft-a','draft-b']);
  assert.equal(body.material_lines[1].unit_price,'0');assert.equal(body.material_lines[0].catalog_version_no,3);assert.equal(body.quarterly_forecasts,undefined);assert.equal(body.actuals,undefined);
  assert.deepEqual(page.data.form,before);assert.match(page.data.materialPreview.display.sourceLabel,/尚未保存/);assert.equal(page.data.materialPreview.display.periodLabel,'2026年 Q4');
});
test('FIN-R 编辑使用冻结目录版本而非当前目录，带商机版本；从未回填销售预测',async()=>{
  let request;const {page}=setup({previewMaterialDraft:async body=>{request=body;return result(body);}});
  page.properties.existing={id:'o',version_no:4};page.data.materialCatalog=[{...catalog,version_no:9}];page.data.form.material_lines=[line({id:'saved-a',version_no:7})];
  await page.previewMaterials();assert.equal(request.expected_opportunity_version,4);assert.equal(request.material_lines[0].catalog_version_no,3);assert.equal(request.material_lines[0].id,'saved-a');
  assert.equal(page.data.form.quarters[0].recognized,'100');assert.equal(materials.lineFor(page.data.form.material_lines[0]).version_no,7);
});
test('FIN-R 草稿缺数量/价格/用途/半填期间保留null，保存仍严格校验',()=>{
  const form={trade_relation:'direct',material_lines:[line({quantity:'',unit_price:'',usage:'',expected_consumption_year:'2026'})]};
  const row=materials.draftPayload(form).material_lines[0];assert.equal(row.quantity,null);assert.equal(row.unit_price,null);assert.equal(row.usage,null);assert.equal(row.expected_consumption_year,2026);assert.equal(row.expected_consumption_quarter,null);
  assert.throws(()=>materials.payload(form));
  for(const patch of [{quantity:'0'},{unit_price:'-1'},{expected_start_on:'2026-02-30'},{catalog_snapshot:{name:'旧数据'}},{service_months:'121'}])assert.throws(()=>materials.draftPayload({trade_relation:'direct',material_lines:[line(patch)]}));
  assert.throws(()=>materials.draftPayload({trade_relation:'direct',material_lines:[line(),line()]}),/标识重复/);
});
test('MINI121 日期清除事件传入草稿空值并重算缺项，不保留旧日期或改写预测',async t=>{
  const clock=timers(t),pickerFile=path.resolve(__dirname,'../miniprogram/ui/sb/sb-date-picker/index.js');
  let pickerDefinition;
  vm.runInNewContext(fs.readFileSync(pickerFile,'utf8'),{Component:value=>pickerDefinition=value,Date});
  for(const field of ['expected_delivery_on','expected_start_on','expected_oa_on']){
    const requests=[],events=[];
    const {page}=setup({previewMaterialDraft:async request=>{
      requests.push(request);
      if(request.material_lines[0][field]!==null)return result(request);
      return result(request,{status:'incomplete',recognized_inclusive:null,recognized_exclusive:null,
        lines:request.material_lines.map(row=>({client_key:row.client_key,catalog_version_no:row.catalog_version_no,
          status:'incomplete',amount_inclusive:null,amount_exclusive:null,missing_fields:[field]}))});
    }});
    page.data.form.material_lines[0][field]='2026-11-01';
    const forecasts=structuredClone(page.data.form.quarters);
    await page.previewMaterials();
    assert.equal(requests[0].material_lines[0][field],'2026-11-01');
    assert.equal(page.data.materialPreview.result.status,'complete');
    const picker={...pickerDefinition.methods,
      data:{value:'2026-11-01',text:'2026-11-01',disabled:false,open:true},_pickerSession:{epoch:1},
      setData(values){Object.assign(this.data,values);},
      triggerEvent(name,detail){
        events.push({name,value:detail.value});
        page.materialInput({currentTarget:{dataset:{key:'draft-a',field}},detail});
      }};
    picker.onClear();
    assert.deepEqual(events,[{name:'change',value:''}]);
    assert.equal(picker.data.open,false);assert.equal(picker._pickerSession,null);
    assert.equal(page.data.form.material_lines[0][field],'');
    assert.equal(page.data.form.materials_dirty,true);
    assert.equal(page.data.materialPreview.result,null);
    assert.equal(page.data.materialPreview.display,null);
    assert.equal(page.data.materialPreview.pending,true);
    await clock.tick();
    assert.equal(requests.length,2);
    assert.equal(requests[1].material_lines[0][field],null);
    assert.equal(page.data.materialPreview.result.status,'incomplete');
    assert.match(page.data.materialPreview.display.summary,/不代表 0/);
    assert.deepEqual(page.data.form.quarters,forecasts);
    assert.equal(requests[1].quarterly_forecasts,undefined);
    assert.equal(requests[1].actuals,undefined);
    picker.data.disabled=true;picker.onClear();
    assert.equal(events.length,1,'禁用日期控件不能继续清空草稿');
    assert.equal(clock.pending.size,0);
  }
});
test('FIN-R 自动试算合并快速输入，清除旧参考；选择物料后缺上下文明确提示',async t=>{
  const clock=timers(t),requests=[];const {page}=setup({previewMaterialDraft:async body=>{requests.push(body);return result(body);}});
  await page.previewMaterials();input(page,'30');input(page,'40');input(page,'50');
  assert.equal(clock.pending.size,1);assert.equal(page.data.materialPreview.pending,true);assert.equal(page.data.materialPreview.result,null);await clock.tick();
  assert.equal(requests.length,2);assert.equal(requests[1].material_lines[0].unit_price,'50');assert.equal(page.data.materialPreview.loading,false);
  page.data.form.trade_relation='';await page.previewMaterials();assert.match(page.data.materialPreview.error,/交易关系/);assert.equal(requests.length,2);
  page.data.form.trade_relation='downstream';await page.previewMaterials();assert.match(page.data.materialPreview.error,/上游/);assert.equal(requests.length,2);
});
test('FIN-R 输入、身份、页面、关闭、季度和客户改变后迟到结果不展示',async t=>{
  timers(t);
  for(const change of [h=>input(h.page,'80'),h=>h.switchIdentity(),h=>{h.page.sourceKey='another';},h=>{h.page.closed=true;},h=>{h.page.data.quarterIndex=1;},h=>{h.page.properties.customerId='other';}]){
    let resolve,request;const h=setup({previewMaterialDraft:body=>{request=body;return new Promise(r=>resolve=r);}});const pending=h.page.previewMaterials();change(h);resolve(result(request));await pending;
    assert.equal(h.page.data.materialPreview.result,null);assert.equal(h.page.data.materialPreview.display,null);clearTimeout(h.page.materialPreviewTimer);
  }
});
test('FIN-R 已排队的自动请求在身份改变或离开后不发出',async t=>{
  const clock=timers(t);let calls=0;const h=setup({previewMaterialDraft:async body=>{calls++;return result(body);}});
  input(h.page,'9');h.switchIdentity();await clock.tick();assert.equal(calls,0);
  input(h.page,'10');h.page.closed=true;await clock.tick();assert.equal(calls,0);
});
test('FIN-R 重复试算只有最新响应有效；旧失败也不覆盖最新成功',async()=>{
  const calls=[];const {page}=setup({previewMaterialDraft:body=>new Promise((resolve,reject)=>calls.push({body,resolve,reject}))});
  const first=page.previewMaterials(),second=page.previewMaterials();calls[1].resolve(result(calls[1].body));await second;calls[0].reject(Error('旧请求失败'));await first;
  assert.equal(page.data.materialPreview.error,'');assert.equal(page.data.materialPreview.draftRevision,2);
});
test('FIN-R 错季度、草稿序号、商机版本、重复行与数值类型不符均拒绝，修正后可重试',async()=>{
  const corruptions=[r=>({...r,draft_revision:99}),r=>({...r,period:{year:2027,quarter:4}}),r=>({...r,opportunity_version:5}),r=>({...r,persisted:true}),r=>({...r,lines:[]}),r=>({...r,lines:[{...r.lines[0],catalog_version_no:9}]}),r=>({...r,recognized_inclusive:20}),r=>({...r,lines:[{...r.lines[0],client_key:'wrong'}]})];
  for(const corrupt of corruptions){let bad=true;const {page}=setup({previewMaterialDraft:async body=>bad?corrupt(result(body)):result(body)});await page.previewMaterials();assert.match(page.data.materialPreview.error,/不一致/);assert.equal(page.data.materialPreview.display,null);bad=false;await page.previewMaterials();assert.equal(page.data.materialPreview.error,'');}
});
test('FIN-R 409和网络错误保留草稿；未完整参考不是0，明确0按后端值显示',async()=>{
  let failure=true;const {page}=setup({previewMaterialDraft:async body=>{if(failure)throw Object.assign(Error('目录版本已变化'),{statusCode:409});return result(body,{status:'incomplete',recognized_inclusive:null,recognized_exclusive:null,missing_fields:['draft-a:expected_start_on']});}});
  const before=structuredClone(page.data.form);await page.previewMaterials();assert.deepEqual(page.data.form,before);assert.match(page.data.materialPreview.error,/版本/);
  failure=false;await page.previewMaterials();assert.match(page.data.materialPreview.display.summary,/不代表 0/);assert.match(page.data.materialPreview.display.reasons.join(),/预计服务开始日期/);
  const zero=behavior.previewDisplay(result({draft_revision:1,year:2026,quarter:4,material_lines:[]},{recognized_inclusive:'0.00',recognized_exclusive:'0.00'}));assert.match(zero.summary,/含税参考：0.00 元/);
});
test('FIN-R 季度处理器自动按新季度重算，已有季度预测各自保留',async t=>{
  const clock=timers(t),requests=[];const {page}=setup({previewMaterialDraft:async body=>{requests.push(body);return result(body);}}),c=component();
  page.quarter=c.methods.quarter;page.loadQuarter=c.methods.loadQuarter;page.updatePredictions=()=>{};
  page.data.form.quarters.push({year:2027,quarter:1,recognized:'80',collection:'31'});
  page.quarter({detail:{value:1}});assert.equal(page.data.recognized,'80');await clock.tick();assert.equal(requests[0].quarter,1);assert.equal(requests[0].year,2027);
  page.quarter({detail:{value:0}});assert.equal(page.data.recognized,'100');await clock.tick();assert.equal(requests[1].quarter,4);assert.equal(page.data.form.quarters.length,2);
});
test('FIN-R 规则输入由冻结物料决定：OA仅必需时显示，交付/消耗各自独立',()=>{
  assert.equal(materials.decorate(line()).inputVisibility.oa,true);
  for(const rule of ['delivery','monthly','consumption','manual']){
    const v=materials.decorate(line({catalog_snapshot:{recognition_rule:rule,rule_parameters:{oa_required:false}}})).inputVisibility;
    assert.equal(v.oa,false);assert.equal(v.delivery,rule==='delivery');assert.equal(v.consumption,rule==='consumption');assert.equal(v.monthly,rule==='monthly');
  }
});

test('FIN-R 跨产品线物料按稳定ID去重，同名不同ID不合并；目录改名不修改冻结规则',()=>{
  const a=line(),b=line({client_key:'b'}),c=line({client_key:'c',catalog_snapshot:{...catalog,product_line_id:'software',product_line_name:'云服务'}});
  const form={...opp.formFor(null),material_lines:[a,b,c]},catalogLines=[{id:'cloud',name:'新云产品',code:'C',is_active:true},{id:'software',name:'云服务',code:'S',is_active:true}];
  const p=productLines.projection(form,null,catalogLines);assert.equal(p.productLineReadonly,true);assert.equal(p.productLineSource,'materials');assert.deepEqual(p.opportunityProductLines.map(x=>x.id),['cloud','software']);
  assert.equal(p.opportunityProductLines[0].name,'新云产品');assert.equal(a.catalog_snapshot.product_line_name,'云服务');
  assert.deepEqual(productLines.payload({...form,intent_product_line_ids:['other'],intent_product_lines_dirty:true},null),{});
});
test('FIN-R 无物料意向多选、明确清空和未编辑omit；旧自由文本永不作为新值保存',async()=>{
  const {page}=setup({getMaterialProductLines:async()=>({items:[{id:'cloud',name:'云服务',is_active:true},{id:'soft',name:'软件',is_active:true}]})});
  page.data.form.material_lines=[];await page.loadProductLines();
  const toggle=id=>page.intentProductLine({currentTarget:{dataset:{id}}});
  toggle('cloud');toggle('soft');assert.deepEqual(productLines.payload(page.data.form,null),{intent_product_line_ids:['cloud','soft']});
  toggle('cloud');toggle('soft');assert.deepEqual(productLines.payload(page.data.form,null),{intent_product_line_ids:[]});
  assert.deepEqual(productLines.payload(opp.formFor(null),null),{});
  const legacy={id:'old',product_line:'历史手填云',intent_product_line_ids:[],legacy_product_line:'历史手填云'};
  const p=productLines.projection(opp.formFor(legacy),legacy,[{id:'cloud',name:'历史手填云'}]);assert.equal(p.legacyProductLine,'历史手填云');assert.equal(p.productLineSource,'legacy');assert.deepEqual(p.opportunityProductLines,[]);
  const payload=opp.payload({...opp.formFor(null),name:'新商机',amount:'80',stageIndex:0,expected_close_date:'2026-12-01',product_line:'任意新文本'},null);assert.equal(payload.product_line,undefined);
});
test('FIN-R 已停用意向保留可移除不可新增，添加物料后只读，删光恢复已选意向',async()=>{
  const {page}=setup({getMaterialProductLines:async()=>({items:[{id:'cloud',name:'云服务',is_active:true}]})});
  page.properties.existing={id:'old',intent_product_line_ids:['retired'],intent_product_lines:[{id:'retired',name:'旧线',is_active:false}],legacy_product_line:'旧文本'};
  page.data.form={...opp.formFor(page.properties.existing),material_lines:[]};await page.loadProductLines();
  assert.equal(page.data.productLineOptions.find(x=>x.id==='retired').selected,true);
  page.data.materialCatalog=[catalog];page.chooseMaterial({currentTarget:{dataset:{id:'sku'}}});assert.equal(page.data.productLineReadonly,true);assert.deepEqual(page.data.opportunityProductLines.map(x=>x.id),['cloud']);
  page.intentProductLine({currentTarget:{dataset:{id:'cloud'}}});assert.deepEqual(page.data.form.intent_product_line_ids,['retired']);
  await page.removeMaterial({currentTarget:{dataset:{key:page.data.form.material_lines[0].client_key}}});assert.equal(page.data.productLineReadonly,false);assert.deepEqual(page.data.opportunityProductLines.map(x=>x.id),['retired']);
  page.intentProductLine({currentTarget:{dataset:{id:'retired'}}});assert.deepEqual(page.data.form.intent_product_line_ids,[]);page.intentProductLine({currentTarget:{dataset:{id:'retired'}}});assert.deepEqual(page.data.form.intent_product_line_ids,[]);
});
test('FIN-R 产品线目录失败保留意向，重试和迟到/身份切换不覆盖当前目录',async()=>{
  const calls=[];const h=setup({getMaterialProductLines:()=>new Promise((resolve,reject)=>calls.push({resolve,reject}))});h.page.data.form.material_lines=[];h.page.data.form.intent_product_line_ids=['cloud'];
  const first=h.page.loadProductLines();calls[0].reject(Error('目录暂不可用'));await first;assert.match(h.page.data.productLinesError,/暂不可用/);assert.deepEqual(h.page.data.form.intent_product_line_ids,['cloud']);
  const old=h.page.loadProductLines(),recent=h.page.loadProductLines();calls[2].resolve({items:[{id:'cloud',name:'当前云服务'}]});await recent;calls[1].resolve({items:[{id:'old',name:'过时结果'}]});await old;assert.equal(h.page.data.productLineOptions[0].name,'当前云服务');
  const other=h.page.loadProductLines();h.switchIdentity();calls[3].resolve({items:[{id:'other',name:'另一公司'}]});await other;assert.equal(h.page.data.productLineOptions[0].name,'当前云服务');
});
test('FIN-R 拜访草稿沿用同一意向目录字段且不推断历史映射',()=>{
  const row={id:'o',legacy_product_line:'旧文本',product_line:'当前投影',product_line_source:'materials',product_lines:[{id:'cloud',name:'云服务'}],intent_product_line_ids:['intent'],intent_product_lines:[{id:'intent',name:'原意向'}]};
  const f=opp.formForVisit(row,null);assert.deepEqual(f.intent_product_line_ids,['intent']);assert.equal(f.intent_product_lines_dirty,false);
  const p=productLines.projection(f,row,[]);assert.equal(p.productLineReadonly,true);assert.equal(p.legacyProductLine,'旧文本');assert.equal(p.opportunityProductLines[0].id,'cloud');
  assert.deepEqual(productLines.payload({...f,intent_product_line_ids:['other'],intent_product_lines_dirty:true},row),{});
});

test('FIN-R-MINI-001 新行与已有行正式保存均携带试算采用的冻结目录版本',()=>{
  for(const id of [undefined,'saved-a']){
    const form={trade_relation:'direct',material_lines:[line({id})]};
    const preview=materials.draftPayload(form).material_lines[0],saved=materials.payload(form).material_lines[0];
    assert.equal(saved.catalog_version_no,3);assert.equal(saved.catalog_version_no,preview.catalog_version_no);assert.equal(saved.id,id);
    assert.equal(saved.catalog_snapshot,undefined);assert.equal(saved.client_key,undefined);
  }
});
test('FIN-R-MINI-001 新行缺版本和所有坏版本禁止提交；真正历史无版本不伪造当前版本',()=>{
  for(const version of [undefined,null,'',0,-1,1.5,'3'])assert.throws(()=>materials.payload({trade_relation:'direct',material_lines:[line({catalog_snapshot:{version_no:version}})]}),/目录版本/);
  for(const version of ['',0,-1,1.5,'3'])assert.throws(()=>materials.payload({trade_relation:'direct',material_lines:[line({id:'saved',catalog_snapshot:{version_no:version}})]}),/目录版本/);
  for(const version of [undefined,null]){
    const saved=materials.payload({trade_relation:'direct',material_lines:[line({id:'historical',catalog_snapshot:{name:'历史物料',version_no:version}})]}).material_lines[0];
    assert.equal(Object.hasOwn(saved,'catalog_version_no'),false);assert.equal(saved.id,'historical');
  }
});

test('FIN-R 意向产品线100条可保存，101条在选择时提示并保留前100条，取消后可重新选择',async()=>{
  const items=Array.from({length:101},(_,i)=>({id:`line-${i}`,name:`产品线${i}`,is_active:true}));
  const {page}=setup({getMaterialProductLines:async()=>({items})});page.data.form.material_lines=[];await page.loadProductLines();
  const modals=[];Object.assign(page,behavior.create({getMaterialProductLines:async()=>({items})},()=>({workspaceId:'w',userId:'u',permissionVersion:1}),options=>modals.push(options)));
  const toggle=i=>page.intentProductLine({currentTarget:{dataset:{id:items[i].id}}});
  for(let i=0;i<100;i++)toggle(i);
  assert.equal(productLines.payload(page.data.form,null).intent_product_line_ids.length,100);
  toggle(100);assert.equal(modals.length,1);assert.match(modals[0].content,/100 条/);assert.equal(page.data.form.intent_product_line_ids.length,100);assert.equal(page.data.form.intent_product_line_ids.includes('line-100'),false);
  toggle(0);toggle(100);assert.equal(page.data.form.intent_product_line_ids.length,100);assert.equal(page.data.form.intent_product_line_ids.includes('line-100'),true);assert.equal(modals.length,1);
});
test('FIN-R 101条历史或恢复草稿本地阻止提交，不静默截断；修正到100条恢复保存',()=>{
  const ids=Array.from({length:101},(_,i)=>`line-${i}`),form={intent_product_lines_dirty:true,intent_product_line_ids:ids,material_lines:[]};
  assert.throws(()=>productLines.payload(form,null),/最多选择 100 条/);assert.equal(form.intent_product_line_ids.length,101);
  const fixed={...form,intent_product_line_ids:ids.slice(1)};assert.equal(productLines.payload(fixed,null).intent_product_line_ids.length,100);
});
