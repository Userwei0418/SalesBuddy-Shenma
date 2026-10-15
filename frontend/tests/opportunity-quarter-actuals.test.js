const test=require('node:test'),assert=require('node:assert/strict');
const {loadQuarterActuals,quarterActualDisplay}=require('../miniprogram/utils/opportunityQuarterActuals');
const examples=require('./helpers/finance-periods-v2.json');
const filters={customer_id:examples.missing.customer_id,opportunity_id:examples.missing.opportunity_id};
async function display(name){const r=await loadQuarterActuals({getOpportunityFinancialOverview:async()=>examples[name]},filters,'2026-10-08');return quarterActualDisplay(r.groups,'2026-Q4');}
test('schema2采用20+30=50，原始资料不会再加一次',async()=>{
 const r=await display('complete');assert.equal(r.quarterRecognized,'50元');assert.equal(r.quarterCollection,'未登记');assert.equal(r.quarterEntryCount,2);
 assert.equal(r.quarterFinancial.recognized.confirmations.length,2);
});
test('未登记、明确0、部分0与撤回严格区分',async()=>{
 assert.equal((await display('missing')).quarterRecognized,'未登记');
 const partial=await display('partial_zero');assert.equal(partial.quarterRecognized,'已登记小计 0元');assert.match(partial.quarterActualHint,/1 行物料/);
 assert.equal((await display('withdrawn')).quarterRecognized,'已撤回');
 const data=JSON.parse(JSON.stringify(examples.complete));data.periods[0].amount='0';
 const r=await loadQuarterActuals({getOpportunityFinancialOverview:async()=>data},filters,'2026-10-08');assert.equal(quarterActualDisplay(r.groups,'2026-Q4').quarterRecognized,'0元');
});
test('错误契约、目标串线、重复期间与无效金额均不伪造有效结果',async()=>{
 for(const patch of [{schema_version:1},{opportunity_id:'other'},{customer_id:'other'},{periods:[...examples.complete.periods,examples.complete.periods[0]]},{periods:[{...examples.complete.periods[0],amount:true}]}]){
 await assert.rejects(loadQuarterActuals({getOpportunityFinancialOverview:async()=>({...examples.complete,...patch})},filters,'2026-10-08'));
 }
});
test('季度事实不编造发生日期，空季度仍可选择',async()=>{
 const r=await loadQuarterActuals({getOpportunityFinancialOverview:async()=>examples.complete},filters,'2026-10-08');
 assert.equal(r.options.length,4);assert.equal(r.groups['2026-Q4'].recognized.occurred_on,null);assert.equal(quarterActualDisplay(r.groups,'2026-Q1').quarterRecognized,'未登记');
});
