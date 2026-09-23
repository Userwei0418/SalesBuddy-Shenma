const test=require('node:test');
const assert=require('node:assert/strict');
const {loadQuarterActuals,quarterActualDisplay}=require('../miniprogram/utils/opportunityQuarterActuals');
const filters={customer_id:'c1',opportunity_id:'o1'};
const row=(year,quarter,collection_amount,recognized_amount,collection_count=1,recognized_count=1)=>({year,quarter,collection_amount,recognized_amount,collection_count,recognized_count,entry_count:collection_count+recognized_count});
const api=(items,years=[2026])=>({getCustomerAssetQuarters:async()=>({as_of:'2026-09-12',items,years})});
test('季度实绩一次读取服务器全量聚合，不下载逐笔账本或使用预测',async()=>{
 const calls=[];const result=await loadQuarterActuals({getCustomerAssetQuarters:async p=>{calls.push(p);return {as_of:p.as_of,years:[2026],items:[row(2026,3,'150000','200000',2,1),row(2026,2,null,400000,0,1)]};},getCustomerAssets(){throw Error('逐笔全量扫描禁止');}},filters,'2026-09-12');
 assert.deepEqual(calls,[{...filters,as_of:'2026-09-12'}]);assert.equal(result.currentKey,'2026-Q3');
 assert.deepEqual(quarterActualDisplay(result.groups,'2026-Q3'),{quarterCollection:'15万',quarterRecognized:'20万',quarterEntryCount:3});
 assert.equal(quarterActualDisplay(result.groups,'2026-Q2').quarterRecognized,'40万');
});
test('历史年份来自服务器，零登记与无记录区分，空库仍可选当前年',async()=>{
 const result=await loadQuarterActuals(api([row(2026,1,0,null,1,0),row(2025,4,null,30000,0,1)],[2026,2025]),filters,'2026-09-12');
 assert.equal(result.options.length,8);assert.equal(quarterActualDisplay(result.groups,'2026-Q1').quarterCollection,'0元');
 assert.equal(quarterActualDisplay(result.groups,'2026-Q1').quarterRecognized,'未登记');
 assert.equal(quarterActualDisplay(result.groups,'2025-Q4').quarterRecognized,'3万');
 const empty=await loadQuarterActuals(api([],[]),filters,'2026-09-12');assert.equal(empty.options.length,4);assert.equal(quarterActualDisplay(empty.groups,'2026-Q3').quarterCollection,'未登记');
});
test('接口失败、不完整统计和不一致金额不返回局部汇总',async()=>{
 await assert.rejects(loadQuarterActuals({getCustomerAssetQuarters:async()=>{throw Error('offline')}},filters,'2026-09-12'),/offline/);
 await assert.rejects(loadQuarterActuals({getCustomerAssetQuarters:async()=>({items:[],years:[2026],as_of:'2026-09-11'})},filters,'2026-09-12'),/响应不完整/);
 for(const bad of [row(2026,5,1,1),row(2026,1,null,1),row(2026,1,true,1),row(2026,1,'bad',1),row(2026,1,-1,1),row(2026,1,0,1,0,1),{...row(2026,1,1,1),entry_count:99}])await assert.rejects(loadQuarterActuals(api([bad]),filters,'2026-09-12'));
 await assert.rejects(loadQuarterActuals(api([row(2026,1,1,1),row(2026,1,1,1)]),filters,'2026-09-12'),/重复/);
});
