const test=require('node:test'),assert=require('node:assert/strict');
const {metricDetails}=require('../miniprogram/utils/dashboardMetricDetails');
const selected={year:2026,quarter:4,quarters:[4],label:'2026 Q4'};
const fact=(id,patch={})=>({id,opportunity_id:'main',year:2026,quarter:4,kind:'recognized',amount:'23.00',raw_amount:'23.00',
  complete:true,missing_material_count:0,tax_unresolved:false,tax_basis:'exclusive',source:'operations',status:'confirmed',...patch});
const period=patch=>({year:2026,quarter:4,recognized_amount:null,recognized_confirmed_subtotal:null,recognized_count:0,recognized_complete:false,
  collection_amount:null,collection_confirmed_subtotal:null,collection_count:0,collection_complete:false,...patch});
function details(actualDetails,actualRows,extra={}){
  const kpis=Array.from({length:8},(_,i)=>({label:`指标${i}`,value:'总卡原值',sub:'原口径'}));
  const result=metricDetails({kpis,opportunities:[],quarterRows:[],actualRows,selected,scopeLabel:'本人',actualDetails,
    actualCoverage:{complete:true,count:actualDetails?.length||0,basis:'same_authorized_effective_actual_rows'},...extra});
  assert.ok(Object.values(result).every(detail=>detail.summary==='总卡原值 · 原口径'));
  return {recognized:result.actual_recognized_exclusive,collection:result.actual_collection};
}

test('FIN-ACTUAL 多物料合并商机贡献，采用上下游与无商机历史金额各一次，回款独立',()=>{
  const rows=[fact('a',{amount:'23.01',material_line_id:'ma'}),fact('b',{amount:'17.02',material_line_id:'mb'}),
    fact('downstream',{opportunity_id:'child',amount:'5.00',trade_relation:'downstream',upstream_opportunity_id:'main'}),
    fact('history',{opportunity_id:null,material_line_id:null,amount:'0.49',source:'entries'}),
    fact('zero',{opportunity_id:'closed',amount:'0',material_line_id:null,source:'historical'}),
    fact('collection',{kind:'collection',amount:'31.00',tax_basis:'not_applicable'})];
  const original=JSON.stringify(rows),d=details(rows,[period({recognized_amount:'45.52',recognized_confirmed_subtotal:'45.52',recognized_count:5,recognized_complete:true,
    collection_amount:'31',collection_confirmed_subtotal:'31',collection_count:1,collection_complete:true})],
    {actualOpportunities:[{id:'main',name:'已授权商机'}]});
  assert.equal(d.recognized.granularity,'opportunity_period');assert.equal(d.recognized.coverageComplete,true);
  assert.equal(d.recognized.rows.length,4);assert.equal(d.recognized.rows[0].amount,'40.03元');assert.equal(d.recognized.rows[0].name,'已授权商机');
  assert.equal(d.recognized.rows[1].amount,'5元');assert.equal(d.recognized.rows[2].amount,'0.49元');
  assert.equal(d.recognized.rows[2].name,'历史实际／未关联商机');assert.equal(d.recognized.rows[2].source,'历史逐笔采用值');
  assert.equal(d.recognized.rows[3].name,'历史商机／名称未提供');assert.equal(d.recognized.rows[3].amount,'0元');
  assert.equal(d.collection.rows.length,1);assert.equal(d.collection.rows[0].amount,'31元');assert.equal(d.collection.rows[0].tax,'来源税口径不适用');
  assert.ok([...d.recognized.rows,...d.collection.rows].every(row=>row.can_open===false));assert.equal(JSON.stringify(rows),original);
});

test('FIN-ACTUAL 部分登记缺物料按商机取max，混合税别不换算含税来源或掩盖小计',()=>{
  const rows=[fact('known',{complete:false,missing_material_count:2}),fact('tax-unknown',{amount:null,raw_amount:'113.00',
    complete:false,missing_material_count:2,tax_unresolved:true,tax_basis:'inclusive'})];
  const d=details(rows,[period({recognized_confirmed_subtotal:'23',recognized_count:1})]).recognized;
  assert.equal(d.coverageComplete,true);assert.equal(d.rows.length,1);assert.equal(d.rows[0].amount,'已登记小计 23元');
  assert.match(d.rows[0].quality,/部分登记/);assert.match(d.rows[0].quality,/2 行物料未登记/);assert.doesNotMatch(d.rows[0].quality,/4 行/);
  assert.match(d.rows[0].quality,/税口径未确认/);assert.equal(d.rows[0].tax,'来源不含税；来源含税');assert.doesNotMatch(d.rows[0].amount,/113/);
});

test('FIN-ACTUAL 明确0、未登记、全部撤回和待核对分别呈现',()=>{
  const rows=[fact('zero',{opportunity_id:'zero',amount:'0'}),fact('missing',{opportunity_id:'missing',amount:null,complete:false,status:'missing'}),
    fact('withdrawn-a',{opportunity_id:'withdrawn',amount:null,complete:false,status:'withdrawn'}),
    fact('withdrawn-b',{opportunity_id:'withdrawn',amount:null,complete:false,status:'withdrawn'}),
    fact('conflict',{opportunity_id:'conflict',amount:null,complete:false,status:'needs_confirmation'})];
  const d=details(rows,[period({recognized_confirmed_subtotal:'0',recognized_count:1})]).recognized;
  assert.deepEqual(d.rows.map(row=>row.amount),['0元','未登记','已撤回','未登记']);
  assert.match(d.rows[3].quality,/待核对/);assert.equal(d.coverageComplete,true);
});

test('FIN-ACTUAL 季度筛选在完整响应内执行，不重复跨季商机，不用预测替代实际',()=>{
  const rows=[fact('q1',{quarter:1,amount:'10.10'}),fact('q4',{amount:'0.20'}),fact('past',{year:2025,amount:'999'})];
  const d=details(rows,[period({recognized_amount:'0.20',recognized_confirmed_subtotal:'0.20',recognized_count:1,recognized_complete:true})],
    {quarterRows:[{opportunity_id:'main',year:2026,quarter:4,recognized_amount:8888,collection_amount:8888}]}).recognized;
  assert.equal(d.rows.length,1);assert.equal(d.rows[0].period,'2026 Q4');assert.equal(d.rows[0].amount,'0.2元');
  const multiple=details(rows,[period({quarter:1,recognized_amount:'10.10',recognized_confirmed_subtotal:'10.10',recognized_count:1,recognized_complete:true}),
    period({recognized_amount:'0.20',recognized_confirmed_subtotal:'0.20',recognized_count:1,recognized_complete:true})],
    {selected:{...selected,quarters:[1,4]}}).recognized;
  assert.equal(multiple.rows.length,2);assert.notEqual(multiple.rows[0].id,multiple.rows[1].id);
});

test('FIN-ACTUAL coverage缺失或部分不称完整，缺明细只显示明确标识的季度汇总',()=>{
  const periods=[period({recognized_amount:'23',recognized_confirmed_subtotal:'23',recognized_count:1,recognized_complete:true})];
  for(const actualCoverage of [undefined,{complete:false,count:1,basis:'same_authorized_effective_actual_rows'}]){
    const d=details([fact('a')],periods,{actualCoverage}).recognized;
    assert.equal(d.coverageComplete,false);assert.equal(d.rows[0].amount,'23元');assert.match(d.note,/无法完整核对总卡/);
  }
  const missing=details(undefined,periods).recognized;
  assert.equal(missing.granularity,'period_aggregate');assert.equal(missing.rows[0].amount,'23元');assert.match(missing.note,/未提供.*仅保留季度汇总/);
});

test('FIN-ACTUAL 重复ID、coverage矛盾和非法金额拒绝伪造完整贡献表',()=>{
  const periods=[period({recognized_amount:'23',recognized_confirmed_subtotal:'23',recognized_count:1,recognized_complete:true})];
  const cases=[{rows:[fact('same'),fact('same')]},{rows:[fact('a')],coverage:{complete:true,count:2,basis:'same_authorized_effective_actual_rows'}},
    {rows:[fact('a')],coverage:{complete:true,count:1,basis:'ordinary_list'}},
    ...[undefined,false,'',NaN,'-1','900719925474099.99'].map(amount=>({rows:[fact('bad',{amount})]}))];
  for(const item of cases){
    const d=details(item.rows,periods,item.coverage?{actualCoverage:item.coverage}:{}).recognized;
    assert.equal(d.coverageComplete,false);assert.equal(d.granularity,'period_aggregate');assert.match(d.note,/无法完整核对/);
    assert.equal(d.rows[0].amount,'23元');
  }
});

test('FIN-ACTUAL 自称完整但金额、null/0、数量或期间不匹配时保留总卡并降级',()=>{
  for(const patch of [{recognized_confirmed_subtotal:'24'},{recognized_confirmed_subtotal:null},
    {recognized_count:2},{recognized_complete:false},{quarter:3}]){
    const d=details([fact('a')],[period({recognized_amount:'23',recognized_confirmed_subtotal:'23',recognized_count:1,recognized_complete:true,...patch})]).recognized;
    assert.equal(d.coverageComplete,false);assert.equal(d.granularity,'period_aggregate');assert.match(d.note,/不一致.*无法完整核对/);
  }
  const nullNotZero=details([],[period({recognized_confirmed_subtotal:'0'})]).recognized;
  assert.equal(nullNotZero.coverageComplete,false);
  const empty=details([],[]).recognized;assert.equal(empty.coverageComplete,true);assert.equal(empty.rows.length,0);
});

test('FIN-ACTUAL 分级精确求和0.10+0.20，保留大额到分',()=>{
  for(const [a,b,total,display] of [['0.10','0.20','0.30','0.3元'],['12345.67','0.49','12346.16','12,346.16元'],
    ['90071992547409.90','0.01','90071992547409.91','90,071,992,547,409.91元']]){
    const d=details([fact('a',{amount:a}),fact('b',{amount:b})],[period({recognized_amount:total,recognized_confirmed_subtotal:total,recognized_count:2,recognized_complete:true})]).recognized;
    assert.equal(d.coverageComplete,true);assert.equal(d.rows[0].amount,display);assert.doesNotMatch(display,/万/);
  }
  for(const rows of [[fact('large',{amount:'90071992547409.92'})],
    [fact('large',{amount:'90071992547409.91'}),fact('cent',{amount:'0.01'})]]){
    const d=details(rows,[period({recognized_confirmed_subtotal:'90071992547409.92'})]).recognized;
    assert.equal(d.coverageComplete,false);assert.equal(d.granularity,'period_aggregate');assert.match(d.note,/无法完整核对/);
  }
});
