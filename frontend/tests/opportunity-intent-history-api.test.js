const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');

test('intent history uses ordinary read API and preserves revision/409 without console calls',async()=>{
 const storage=new Map(),calls=[],filename=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
 let response={schema_version:1,opportunity_id:'opp',field:'intent_product_line_ids',history_revision:'a'.repeat(64),changes:{items:[],limit:10,offset:0,has_more:false,next_offset:null}},status=200;
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,Map,Set,Date,require:n=>require(path.resolve(path.dirname(filename),n)),wx:{getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:r=>{calls.push(r);r.success({statusCode:status,data:response});}}});
 const api=module.exports;api.saveAuth({access_token:'offline-test',actor:{workspace_id:'w',user_id:'u'}});
 const value=await api.getOpportunityIntentHistory('opp',{limit:10,offset:0});assert.equal(value.opportunity_id,'opp');assert.equal(value.history_revision,'a'.repeat(64));assert.equal(value.offset,0);
 await api.getOpportunityIntentHistory('opp',{limit:10,offset:10,expected_history_revision:'a'.repeat(64)});
 const urls=calls.map(r=>new URL(r.url));assert.equal(urls[0].pathname,'/api/v1/opportunities/opp/intent-product-line-history');assert.equal(urls[1].searchParams.get('expected_history_revision'),'a'.repeat(64));assert.equal(urls[1].searchParams.get('offset'),'10');assert.equal(urls[0].searchParams.has('year'),false);assert.equal(urls[0].searchParams.has('quarter'),false);assert.ok(calls.every(r=>r.method==='GET'));
 status=409;response={detail:{code:'OPPORTUNITY_INTENT_HISTORY_CHANGED',message:'变更记录已更新'}};
 await assert.rejects(api.getOpportunityIntentHistory('opp',{offset:10,expected_history_revision:'a'.repeat(64)}),e=>e.statusCode===409);
 status=200;response={schema_version:1,field:'other',changes:{items:[]}};await assert.rejects(api.getOpportunityIntentHistory('opp'),/响应不完整/);
 assert.ok(calls.every(r=>!r.url.includes('/console/')));
});
