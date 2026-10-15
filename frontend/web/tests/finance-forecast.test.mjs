import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import {actualPeriodLabel, actualSourceLabel} from '../department-ui/pc/finance-display.js';

const module = {exports: {}};
vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/OpportunityCreate.jsx', import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code, {
  module, exports: module.exports,
  require: name => name === 'react' ? React : new Proxy({}, {get: (_, key) => key === '__esModule' ? true : String(key)}),
});
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)];
const copy = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? React.Children.toArray(tree.props?.children).map(copy).join(' ') : String(tree);
function component(file, exported = 'default') {
  const module = {exports: {}};
  vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/' + file, import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code, {
    module, exports: module.exports, require: name => name === 'react' ? React : name === './pc/finance-display.js' ? {actualPeriodLabel, actualSourceLabel} : new Proxy({default: 'stub'}, {get: (_, key) => key === '__esModule' ? true : String(key)}),
  });
  return module.exports[exported];
}
function render(data, disabled = false) {
  const form = {data: {showForecast: true, quarterIndex: 0, quarterOptions: [{year: 2026, quarter: 4, label: '2026 Q4'}], ...data}}, calls = [];
  const tree = module.exports.QuarterForecast({form, disabled, invokeOn: (target, name, payload) => calls.push({target, name, payload})});
  return {form, calls, inputs: nodes(tree).filter(node => node.type === 'SbAmountInput'), selects: nodes(tree).filter(node => node.type === 'SbLabeledSelect')};
}

test('PC quarter forecast preserves entered amounts regardless of stage and keeps missing separate from zero', () => {
  for (const stage of ['10%', '30%', '100%', '—']) {
    const view = render({forecastRate: stage, collection: '80', recognized: '0', predictedCollection: '24', predictedRecognized: '0'});
    assert.deepEqual(view.inputs.map(node => node.props.value), [80, 0]);
    view.inputs[0].props.onChange(70);
    view.inputs[1].props.onChange(null);
    assert.deepEqual(view.calls.map(call => [call.name, call.payload.dataset.key, call.payload.detail.value]), [['forecast', 'collection', '70'], ['forecast', 'recognized', '']]);
    assert.ok(view.calls.every(call => call.target === view.form));
  }
  assert.deepEqual(render({collection: '', recognized: null}).inputs.map(node => node.props.value), [null, null]);
});

test('PC quarter selection and confidence delegate to shared state without updating actual data', () => {
  const view = render({collection: '0.0001', recognized: '12.3456', confidenceOptions: [{value: null, label: '未填'}, {value: 'high', label: '高'}, {value: 'low', label: '低'}], collectionConfidence: 'high'}, true);
  assert.deepEqual(view.inputs.map(node => node.props.value), [0.0001, 12.3456]);
  assert.ok(view.inputs.every(node => node.props.disabled));
  view.selects[0].props.onChange(1);
  assert.equal(view.selects[1].props.value, 'high');
  view.selects[1].props.onChange('low');
  assert.deepEqual(view.calls.map(call => [call.name, call.payload.detail.value]), [['quarter', 1], ['forecast', 'low']]);
  assert.equal(view.calls[1].payload.dataset.key, 'collection_confidence');
});

test('quarter confirmations never acquire an invented arrival date or a complete status', () => {
  assert.equal(actualPeriodLabel({year: 2026, quarter: 4, occurred_on: null, period_precision: 'quarter'}), '2026 Q4');
  assert.equal(actualPeriodLabel({year: 2026, quarter: 4, occurred_on: '2026-10-08', period_precision: 'day'}), '2026-10-08');
  assert.equal(actualPeriodLabel({occurred_on: null}), '期间未标注');
  assert.equal(actualSourceLabel({source: 'operations', complete: false, status: 'partial'}), '运营确认 · 部分登记');
  assert.equal(actualSourceLabel({source: 'operations', status: 'withdrawn'}), '运营确认 · 已撤回');
});

test('PC materials preserve stable same-SKU rows and send only shared draft input events', () => {
  const Materials = component('OpportunityMaterials.jsx'), calls = [];
  const shared = {material_id: 'same-sku', quantity: '1.2500', unit_price: '0.01', usage: 'self_use', catalog_snapshot: {name: '合成物料', unit: '份', recognition_rule: 'consumption'}, inputVisibility: {consumption: true}, line_amount_text: '0.01 元'};
  const form = {data: {form: {trade_relation: 'direct', material_lines: [{...shared, client_key: 'existing-id', id: 'existing-id'}, {...shared, client_key: 'draft-2'}]}}};
  const view = Materials({form, invokeOn: (_, name, payload) => calls.push({name, payload})}), all = nodes(view);
  assert.equal(all.filter(n => n.type === 'article').length, 2);
  const prices = all.filter(n => n.type === 'Input' && n.props['aria-label'] === '成交单价（元，含税）');
  assert.deepEqual(prices.map(n => n.props.value), ['0.01', '0.01']);
  prices[1].props.onChange({target: {value: '700.00'}});
  all.filter(n => n.type === 'Button' && copy(n) === '移除')[0].props.onClick();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{name: 'materialInput', payload: {dataset: {key: 'draft-2', field: 'unit_price'}, detail: {value: '700.00'}}}, {name: 'removeMaterial', payload: {dataset: {key: 'existing-id'}}}]);
  assert.deepEqual(form.data.form.material_lines.map(r => r.unit_price), ['0.01', '0.01']);
  assert.equal(all.filter(n => n.type === 'Input' && n.props['aria-label'] === '预计消耗年份').length, 2);
  assert.equal(all.filter(n => n.type === 'SbDatePicker').length, 0);
});

test('keyboard-cleared material dates reach the shared draft before recalculation', () => {
  const Materials = component('OpportunityMaterials.jsx');
  for (const [field, visibility] of [['expected_delivery_on', 'delivery'], ['expected_start_on', 'monthly'], ['expected_oa_on', 'oa']]) {
    const calls = [], row = {client_key: 'existing-line', [field]: '2026-11-01', inputVisibility: {[visibility]: true}};
    const form = {data: {form: {material_lines: [row]}}};
    const tree = Materials({form, invokeOn: (_, name, payload) => {
      calls.push({name, payload});
      if (name === 'materialInput') row[payload.dataset.field] = payload.detail.value;
    }});
    const capture = nodes(tree).find(n => n.props.onChangeCapture);
    assert.ok(capture, 'native text deletion must update the date draft even when the picker emits no onChange');
    capture.props.onChangeCapture({target: {tagName: 'INPUT', value: ''}});
    assert.equal(row[field], '', 'recalculation reads the shared draft, not the now-empty DOM');
    assert.deepEqual(JSON.parse(JSON.stringify(calls)), [{name: 'materialInput', payload: {dataset: {key: 'existing-line', field}, detail: {value: ''}}}]);
  }
});

test('material date adapter keeps calendar changes and does not turn partial typing or disabled input into a clear', () => {
  const Materials = component('OpportunityMaterials.jsx');
  for (const disabled of [false, true]) {
    const calls = [], form = {data: {form: {material_lines: [{client_key: 'line', expected_start_on: '2026-11-01', inputVisibility: {monthly: true}}]}}};
    const tree = Materials({form, disabled, invokeOn: (_, name, payload) => calls.push({name, payload})});
    const capture = nodes(tree).find(n => n.props.onChangeCapture);
    assert.ok(capture);
    for (const value of ['2026-11-', '2026-12-01']) capture.props.onChangeCapture({target: {tagName: 'INPUT', value}});
    capture.props.onChangeCapture({target: {tagName: 'BUTTON', value: ''}});
    if (disabled) capture.props.onChangeCapture({target: {tagName: 'INPUT', value: ''}});
    assert.equal(calls.length, 0);
    if (!disabled) {
      const picker = nodes(tree).find(n => n.type === 'SbDatePicker');
      picker.props.onChange('2026-12-01'); picker.props.onChange(null);
      assert.deepEqual(calls.map(c => c.payload.detail.value), ['2026-12-01', '']);
    }
  }
});

test('PC actual display does not turn a partial zero into a full total or sum evidence', () => {
  const Financial = component('QuarterFinancial.jsx');
  const partial = {amount: null, confirmed_subtotal: '0', status: 'partial', missing_material_count: 1, sourceLabel: '运营确认', confirmations: [{id: 'a', amountText: '0', status: 'confirmed'}], entries: [{amount: '999'}]};
  const view = Financial({data: {quarterFinancial: {recognized: partial, collection: {amount: '0', amountText: '0', status: 'confirmed'}}}});
  const strip = nodes(view).find(n => n.type === 'SbMetricStrip');
  assert.equal(strip.props.items[0].value, '0');
  assert.equal(strip.props.items[1].value, null);
  assert.equal(strip.props.items[1].missingText, '部分登记');
  assert.match(copy(view), /已登记小计：\s*0\s*元/); assert.match(copy(view), /尚缺\s*1\s*项/);
  assert.doesNotMatch(copy(view), /999/);
});

test('quarter actual cards and confirmation rows preserve shared yuan/wan amounts without conflicting title units', () => {
  const shared = {exports: {}};
  vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/financialDisplay.js', import.meta.url), 'utf8'), {module: shared, exports: shared.exports});
  const Financial = component('QuarterFinancial.jsx');
  for (const [amount, expected] of [['31', '31元'], ['40', '40元'], ['0', '0元'], ['9999.99', '9,999.99元'], ['10000', '1万'], ['310000', '31万']]) {
    const period = shared.exports.bucket({amount, status: 'confirmed', source: 'operations', complete: true, confirmations: [{id: 'confirmation', amount, status: 'confirmed'}]});
    const view = Financial({data: {quarterFinancial: {recognized: period, collection: period}}});
    const strip = nodes(view).find(node => node.type === 'SbMetricStrip');
    assert.deepEqual(Array.from(strip.props.items, item => [item.label, item.value]), [['季度实际回款', expected], ['季度实际确收（不含税）', expected]]);
    for (const table of nodes(view).filter(node => node.type === 'SbTable')) {
      const column = table.props.columns.find(column => column.key === 'amount');
      assert.equal(column.title, '确认金额');
      assert.equal(column.render(null, table.props.rows[0]), expected);
    }
    assert.equal(period.amount, amount);
  }
});

test('opportunity overview keeps ACV and forecasts in their existing amount units', () => {
  const Detail = component('OpportunityDetail.jsx');
  for (const [amount, forecast] of [['1000元', '80元'], ['100万', '80万']]) {
    const view = Detail({data: {opportunity: {name: '合成商机', amount, quarterDetails: [{label: '2026 Q4', recognized: forecast, collection: forecast}]}, detailSummary: {loaded: true}}, invoke: () => {}});
    const text = copy(view).replace(/\s+/g, ' ');
    assert.match(text, new RegExp(`ACV（商机金额） ${amount}`));
    assert.match(text, new RegExp(`含税确收 ${forecast} · 回款 ${forecast}`));
    assert.doesNotMatch(text, /ACV（商机金额）.*万元|季度预测.*万元/);
  }
});

test('sales actual page stays read-only even with stale legacy management flags', () => {
  const Assets = component('CustomerAssets.jsx'), calls = [];
  const view = Assets({data: {customerId: 'c', customerName: '合成客户', canManage: true, formOpen: true, items: [{id: 'r', can_void: true}]}, invoke: name => calls.push(name)});
  assert.equal(nodes(view).filter(n => n.type === 'SbSheet').length, 0);
  assert.equal(nodes(view).find(n => n.type === 'SbTable').props.actions, undefined);
  assert.doesNotMatch(copy(view), /确认登记|登记确收|登记回款|作废/);
  assert.deepEqual(calls, []);
});

const traceModule = {exports: {}};
vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/opportunityMaterialForm.js', import.meta.url), 'utf8'), {module:traceModule,exports:traceModule.exports,require:()=>({})});
function reference(extra = {}) {
 return {status:'complete',recognized_inclusive:'0.00',recognized_exclusive:'0.00',opportunity_version:9,reference_fingerprint:'diagnostic-only-hash',lines:[{
  line_id:'line-a',status:'complete',amount_inclusive:'0.00',amount_exclusive:'0.00',calculation_trace:{
   identity:{line_version:3,catalog_version:2},calculator_version:'materials.v2',catalog_snapshot:{name:'合成服务'},
   business_definition:{status:'maintained',value:{description:'采用时的规则',applicability:'已约定服务期',exceptions:'总代转售除外',examples:[{scenario:'OA 晚于截止日',explanation:'按保存的延后策略处理'}]}},
   rule:{recognition_rule:'monthly',tax_rate:'0.1300',rounding:'ROUND_HALF_UP'},inputs:{quantity:'1.2500',unit_price:'8000000000000000.01'},
   monthly_allocation:{service_months:120,requested_start_month:'2026-01',effective_start_month:'2026-03',requested_end_month_exclusive:'2036-01',effective_end_month_exclusive:'2036-01',base_cents_per_month:'8333333333333333',tail_extra_cents:'1',
    oa:{required:true,expected_oa_on:'2026-02-28',clamped_to_month_end:true,effective_cutoff_day:28},
    months:Array.from({length:120},(_,i)=>({month:`${2026+Math.floor(i/12)}-${String(i%12+1).padStart(2,'0')}`,allocated_inclusive:i===119?'83333333333333.34':'83333333333333.33',base_cents:'8333333333333333',tail_extra_cents:i===119?'1':'0',included:i>1,exclusion_reasons:i<2?['before_effective_start']:[]}))},
  },
 }],...extra};
}
test('PC trace uses frozen descriptions and full server decimals, with all 120 months and OA exclusions',()=>{
 const Trace=component('OpportunityMaterials.jsx','MaterialCalculationTrace'),display=traceModule.exports.previewDisplay(reference()),calls=[];
 display.lines[0].expanded=true;const view=Trace({display,onToggle:key=>calls.push(key)}),all=nodes(view);
 assert.match(copy(view),/后端计算参考/);assert.match(copy(view),/不是实际确收/);assert.doesNotMatch(copy(view),/diagnostic-only-hash/);
 const descriptions=all.filter(n=>n.type==='Descriptions').flatMap(n=>n.props.items);
 assert.equal(descriptions.find(item=>item.label==='成交单价（元）').children,'8000000000000000.01');
 assert.equal(descriptions.find(item=>item.label==='规则说明').children,'采用时的规则');
 assert.equal(descriptions.find(item=>item.label==='例外情况').children,'总代转售除外');
 assert.equal(descriptions.find(item=>item.label==='截止日已按月末调整').children,'是');
 const months=all.find(n=>n.type==='Table');assert.equal(months.props.dataSource.length,120);
 assert.equal(months.props.dataSource.at(-1).amountText,'83333333333333.34 元');assert.equal(months.props.dataSource.at(-1).tail,'1');
 assert.equal(months.props.dataSource[0].statusText,'OA 延后排除');
 all.find(n=>n.type==='Button').props.onClick();assert.deepEqual(calls,['line-a']);assert.equal(display.lines[0].expanded,true);
});
test('PC trace keeps zero distinct from missing, manual and excluded results; old snapshots stay unexplained',()=>{
 const Trace=component('OpportunityMaterials.jsx','MaterialCalculationTrace');
 assert.match(copy(Trace({display:traceModule.exports.previewDisplay(reference()),onToggle:()=>{}})),/0.00 元/);
 for(const [status,missing,label] of [['incomplete',['expected_start_on'],'信息不足，未计算'],['incomplete',['manual_sales_input'],'待人工填写'],['excluded',[],'本商机不计入确收']]){
  const result=reference({status:'incomplete',recognized_inclusive:null,recognized_exclusive:null,lines:[{line_id:'line-a',status,missing_fields:missing,calculation_trace:{business_definition:{status:'historical_description_not_maintained'},identity:{},catalog_snapshot:{name:'旧快照'}}}]}),display=traceModule.exports.previewDisplay(result);
  display.lines[0].expanded=true;const view=Trace({display,onToggle:()=>{}});
  assert.match(copy(view),new RegExp(label));assert.match(copy(view),/不代表 0/);assert.match(copy(view),/历史说明未维护/);
  assert.doesNotMatch(copy(view),/采用时的规则|0.00 元/);
 }
});
test('PC materials only renders a valid shared trace and delegates expansion without touching financial fields',()=>{
 const Materials=component('OpportunityMaterials.jsx','MaterialForecastReference'),display=traceModule.exports.previewDisplay(reference()),calls=[];
 const data={form:{materials_dirty:false,material_lines:[],collection:'99',recognized:'88'},materialPreview:{result:reference(),display}},form={data};
 const view=Materials({form,invokeOn:(_,name,payload)=>calls.push({name,payload})});
 const trace=nodes(view).find(n=>typeof n.type==='function'&&n.type.name==='MaterialCalculationTrace');assert.ok(trace);
 trace.props.onToggle('line-a');assert.deepEqual(JSON.parse(JSON.stringify(calls)),[{name:'toggleMaterialTrace',payload:{dataset:{key:'line-a'}}}]);
 assert.equal(data.form.collection,'99');assert.equal(data.form.recognized,'88');
 for(const overrides of [{form:{...data.form,materials_dirty:true}},{materialPreview:{...data.materialPreview,loading:true}},{materialPreview:{...data.materialPreview,error:'版本已变化'}}]){
  const hidden=Materials({form:{data:{...data,...overrides}},invokeOn:()=>{}});
  assert.equal(nodes(hidden).some(n=>typeof n.type==='function'&&n.type.name==='MaterialCalculationTrace'),false);
 }
});

test('unsaved material references and quarter changes remain draft events, with no save or forecast writes',()=>{
 const Reference=component('OpportunityMaterials.jsx','MaterialForecastReference'),calls=[];
 const result=reference({reference_kind:'sales_forecast_draft_reference',persisted:false,draft_revision:4});
 const data={form:{materials_dirty:true,quarters:[{recognized:'80',collection:'70'}]},quarterIndex:0,quarterOptions:[{label:'2026 Q4'},{label:'2027 Q1'}],materialPreview:{draftRevision:4,result,display:traceModule.exports.previewDisplay(result)}};
 const before=JSON.stringify(data),form={data};
 const view=Reference({form,invokeOn:(target,name,payload)=>calls.push({target,name,payload})}),all=nodes(view);
 assert.ok(all.some(n=>n.type?.name==='MaterialCalculationTrace'));
 const retry=all.find(n=>n.type==='Button');assert.equal(retry.props.disabled,false);retry.props.onClick();
 all.find(n=>n.type==='SbLabeledSelect').props.onChange(1);
 assert.deepEqual(calls.map(c=>c.name),['previewMaterials','quarter']);assert.ok(calls.every(c=>c.target===form));
 assert.equal(calls[1].payload.detail.value,1);assert.equal(JSON.stringify(data),before);
 for(const patch of [{pending:true},{loading:true},{error:'目录版本已变化，请刷新'}]){
  const blocked=Reference({form:{data:{...data,materialPreview:{...data.materialPreview,...patch}}},invokeOn(){}});
  assert.equal(nodes(blocked).some(n=>n.type?.name==='MaterialCalculationTrace'),false);
 }
 const failed=Reference({form:{data:{...data,materialPreview:{error:'参考暂不可用'}}},invokeOn(){}});
 assert.match(copy(failed),/参考暂不可用/);assert.match(copy(failed),/重试计算/);
 assert.doesNotMatch(copy(failed),/0.00/);
});

test('product line choices use shared identities; material-derived membership and legacy labels stay read-only',()=>{
 const ProductLines=component('OpportunityMaterials.jsx','OpportunityProductLines'),calls=[];
 const options=[{id:'a',name:'产品甲',selected:true},{id:'b',name:'停用乙',selected:true,is_active:false},{id:'c',name:'停用丙',selected:false,is_active:false}];
 const data={productLineOptions:options,opportunityProductLines:options.slice(0,2),legacyProductLine:'历史原文',form:{product_line:'不可编辑的历史值'}};
 const before=JSON.stringify(data),form={data};
 const view=ProductLines({form,invokeOn:(target,name,payload)=>calls.push({target,name,payload})});
 const checkboxes=nodes(view).filter(n=>n.type==='Checkbox');
 assert.deepEqual(checkboxes.map(n=>n.props.checked),[true,true,false]);assert.deepEqual(checkboxes.map(n=>n.props.disabled),[false,false,true]);
 checkboxes[1].props.onChange();assert.equal(calls[0].name,'intentProductLine');assert.equal(calls[0].payload.dataset.id,'b');
 assert.equal(JSON.stringify(data),before);assert.match(copy(view),/历史填写：.*历史原文/);assert.equal(nodes(view).some(n=>n.type==='Input'),false);
 const derived=ProductLines({form:{data:{...data,productLineReadonly:true,productLineMissingCount:1}},invokeOn(){}});
 assert.equal(nodes(derived).some(n=>n.type==='Checkbox'),false);assert.match(copy(derived),/产品甲/);assert.match(copy(derived),/停用乙/);assert.match(copy(derived),/1\s+条物料尚无产品线归属/);
 for(const patch of [{productLinesLoading:true},{productLinesError:'目录不可用'},{materialLinesLoading:true}]){
  const blocked=ProductLines({form:{data:{...data,...patch}},invokeOn(){}});
  assert.ok(nodes(blocked).filter(n=>n.type==='Checkbox').every(n=>n.props.disabled));
 }
});

test('PC inputs follow shared visibility and do not infer OA from unrelated rule parameters',()=>{
 const Materials=component('OpportunityMaterials.jsx');
 const base={client_key:'a',catalog_snapshot:{recognition_rule:'delivery',rule_parameters:{oa_required:true}},inputVisibility:{delivery:true,oa:false,monthly:false,accounts:false,consumption:false}};
 const view=Materials({form:{data:{form:{material_lines:[base]}}},invokeOn(){}});
 assert.deepEqual(nodes(view).filter(n=>n.type==='SbField' && nodes(n).some(child=>child.type==='SbDatePicker')).map(n=>n.props.label),['预计交付日期']);
 const monthly={...base,inputVisibility:{monthly:true,oa:true,accounts:true}};
 const second=Materials({form:{data:{form:{material_lines:[monthly]}}},invokeOn(){}});
 assert.deepEqual(nodes(second).filter(n=>n.type==='SbField' && nodes(n).some(child=>child.type==='SbDatePicker')).map(n=>n.props.label),['预计服务开始日期','预计 OA 日期']);
 assert.ok(nodes(second).some(n=>n.type==='Input'&&n.props['aria-label']==='预计账号数'));
});
