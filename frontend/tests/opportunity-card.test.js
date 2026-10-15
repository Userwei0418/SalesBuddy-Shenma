const test=require('node:test'),assert=require('node:assert/strict');
const {cardFields,loadCardActuals}=require('../miniprogram/utils/opportunityCard');
const examples=require('./helpers/finance-periods-v2.json');
const op=examples.complete.opportunity_id,c=examples.complete.customer_id;
test('产品线不造示例值，未请求前加载中，已确认0不依赖记录数量',()=>{
 assert.equal(cardFields({product_line:' Token Plan '}).productLineLabel,'Token Plan');assert.equal(cardFields({}).productLineLabel,'未填写');assert.equal(cardFields({}).recognizedLabel,'加载中');
 assert.equal(cardFields({actuals:{recognized_amount:'0',collection_amount:null,recognized_complete:true,recognized_count:0}}).recognizedLabel,'0元');
});
test('采用来源20+30=50，原始历史50和当前50不再次相加，也不使用ACV或预测',async()=>{
 const data=structuredClone(examples.complete);data.periods[0].entry_amount='50';data.periods[0].history=[{amount:'50'}];
 const rows=await loadCardActuals({getOpportunityFinancialOverview:async()=>data,getCustomerAssets(){assert.fail('不扫描原始账本');}},[{id:op,customer_id:c,amount:9999,quarterly_forecasts:[{recognized_amount:8888}]}]);
 assert.equal(rows[0].recognizedLabel,'50元');assert.equal(rows[0].collectionLabel,'未登记');assert.equal(rows[0].amount,9999);
});
test('已有列表投影直接采用，同一财务小计不会改成完整值',async()=>{
 const actuals=examples.aggregate_complete_plus_partial;
 const rows=await loadCardActuals({getOpportunityFinancialOverview(){assert.fail('已有投影不重新扫描');}},[{id:op,customer_id:c,actuals}]);
 assert.equal(rows[0].recognizedLabel,'已登记小计 70元');assert.match(rows[0].financialHints.join(''),/物料/);
});
test('详情调用严格匹配商机/客户/schema2，失败不冒充无记录或复用别的客户',async()=>{
 for(const patch of [{opportunity_id:'other'},{customer_id:'other'},{schema_version:1},{totals:null}]){
 const result=await loadCardActuals({getOpportunityFinancialOverview:async()=>({...examples.complete,...patch})},[{id:op,customer_id:c}]);assert.equal(result[0].recognizedLabel,'暂不可用');
 }
 const result=await loadCardActuals({},[{id:op,customer_id:c}]);assert.equal(result[0].recognizedLabel,'暂不可用');
});

test('MINI-R01 卡片财务回退保留单一来源小计、冲突与撤回状态',async()=>{
 for(const status of ['single_source','conflict','withdrawn']){
  const total={amount:null,confirmed_subtotal:'20',known_exclusive_subtotal:'20',complete:false,status,source:'entries',tax_unknown:true};
  const data={schema_version:2,opportunity_id:op,customer_id:c,totals:{recognized:total,collection:{amount:null}}};
  const [row]=await loadCardActuals({getOpportunityFinancialOverview:async()=>data},[{id:op,customer_id:c}]);
  assert.equal(row.recognizedLabel,status==='withdrawn'?'已撤回':'已登记小计 20元');
  if(status==='conflict')assert.match(row.financialHints.join(''),/来源待运营核对/);
 }
});
