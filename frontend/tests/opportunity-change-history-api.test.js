const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const fixture=require('./fixtures/opportunity-change-history.v1.json');
function harness(){
 const calls=[],storage=new Map(),filename=path.resolve(__dirname,'../miniprogram/utils/apiClient.js'),module={exports:{}};
 let response=structuredClone(fixture),status=200;
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{module,Map,Set,Date,require:n=>require(path.resolve(path.dirname(filename),n)),wx:{
  getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
  request:r=>{calls.push(r);r.success({statusCode:status,data:response});},
 }});
 module.exports.saveAuth({access_token:'synthetic-offline',actor:{workspace_id:'w',user_id:'u',permissions:{'opportunity.read':true}}});
 return {api:module.exports,calls,set(value,code=200){response=value;status=code;}};
}
test('frozen wire uses only ordinary opportunity read with explicit revision paging',async()=>{
 const h=harness(),id=fixture.opportunity_id;
 const response=await h.api.getOpportunityChangeHistory(id,{limit:20,offset:0,year:2025,quarter:1});
 assert.equal(response.items.length,1);assert.equal(response.order,'created_at_desc_id_desc');assert.equal(response.coverage_note,fixture.changes.coverage_note);
 const next=structuredClone(fixture);Object.assign(next.changes,{items:[],offset:20});
 h.set(next);await h.api.getOpportunityChangeHistory(id,{limit:20,offset:20,expected_history_revision:fixture.history_revision});
 const [first,second]=h.calls.map(r=>new URL(r.url));
 assert.equal(first.pathname,`/api/v1/opportunities/${id}/change-history`);
 assert.equal(first.searchParams.has('year'),false);assert.equal(first.searchParams.has('quarter'),false);
 assert.equal(second.searchParams.get('expected_history_revision'),fixture.history_revision);
 assert.equal(second.searchParams.get('offset'),'20');
 assert.ok(h.calls.every(r=>r.method==='GET'&&!/console|advice|directory/.test(r.url)));
});
test('conflicts and current authorization errors stay actionable',async()=>{
 for(const status of [409,403,404]){
  const h=harness();h.set({detail:{code:'OPPORTUNITY_CHANGE_HISTORY_CHANGED',message:'历史请求失败'}},status);
  await assert.rejects(h.api.getOpportunityChangeHistory(fixture.opportunity_id,{limit:20}),e=>e.statusCode===status);
 }
});
test('invalid cursor requests never leave the client',async()=>{
 const h=harness();
 for(const options of [{offset:1},{offset:1,expected_history_revision:'bad'},{limit:0},{limit:101},{offset:-1}]){
  await assert.rejects(h.api.getOpportunityChangeHistory(fixture.opportunity_id,options),/分页参数无效/);
 }
 assert.equal(h.calls.length,0);
});
test('malformed envelope, order and pagination cannot masquerade as empty history',async()=>{
 for(const mutate of [
  x=>x.schema_version=2,x=>x.opportunity_id='another',x=>x.history_revision='bad',
  x=>x.changes.order='created_at_asc',x=>x.changes.limit=10,x=>x.changes.offset=20,
  x=>x.changes.items=null,x=>x.changes.coverage_note=null,
  x=>Object.assign(x.changes,{has_more:true,next_offset:0}),
  x=>Object.assign(x.changes,{has_more:false,next_offset:20}),
 ]){
  const h=harness(),bad=structuredClone(fixture);mutate(bad);h.set(bad);
  await assert.rejects(h.api.getOpportunityChangeHistory(fixture.opportunity_id,{limit:20}),/响应不完整/);
 }
});
