import {test} from 'node:test';
import assert from 'node:assert/strict';
import {clearCache, collectFollowups, normalizeVisit, visitSource} from '../department-ui/pc/data.js';
import {facetValue, filterRecordRows, loadFollowupDetail, visitFacets} from '../department-ui/pc/visit-records.js';
const rows = [
  {id:'v1',customer_id:'c1',customer_name:'甲客户',linked_opportunities:[{id:'o1',name:'同名项目',can_open:false},{id:'o2',name:'第二项目',can_open:true}],recorder_role:'sales'},
  {id:'v2',customer_id:'c2',customer_name:'乙客户',opportunity_id:'o3',opportunity_name:'同名项目',recorder_role:'fde'},
  {id:'v3',customer_id:'c1',customer_name:'甲客户'},
].map(row => normalizeVisit(row));
function install(client) {
  const previous = globalThis.SalesRuntime;
  globalThis.SalesRuntime = {app:{globalData:{session:{userId:'u1',workspaceId:'w1',permissionVersion:1}}}, require: () => client};
  return () => {globalThis.SalesRuntime = previous; clearCache();};
}
test('facets use IDs, preserve multi-opportunity links and customer-only records', () => {
  assert.deepEqual(visitFacets(rows, 'c1').opportunities.map(o=>o.value).sort(), ['o1','o2']);
  assert.equal(visitFacets(rows).opportunities.filter(o=>o.label==='同名项目').length, 2);
  assert.deepEqual(filterRecordRows(rows,{customer:'c1',opportunity:'o2'}).map(v=>v.id),['v1']);
  assert.deepEqual(filterRecordRows(rows,{opportunity:'none'}).map(v=>v.id),['v3']);
  assert.deepEqual(filterRecordRows(rows,{customer:'c1',opportunity:'o3'}),[]);
  assert.equal(facetValue('removed',[],'客户').label,'所选客户在当前结果中无记录');
});
test('historical unknown dates and explicit unknown role snapshots are not inferred from current directory', () => {
  assert.equal(normalizeVisit({id:'v',visit_date:null,created_at:'2026-10-09T01:00:00Z'}).date,'');
  assert.equal(visitSource({recorder_id:'u1',recording_role_code_snapshot:null},new Map([['u1',{role:'sales'}]])), 'unknown');
  assert.equal(visitSource({recording_role_code_snapshot:'fde_lead'}),'fde');
  assert.equal(normalizeVisit({id:'v',contact_name:'联系人'}).contacts,'联系人');
});
test('whole-body search travels to backend and uses a separate cached result', async () => {
  const calls=[], restore=install({listVisitRange: async p => {calls.push(p); return {items:[{id:p.q||'all'}],summary:{total:1},has_more:false};}});
  try {
    const args={range:{kind:'week',start:'2026-10-05',end:'2026-10-11',today:'2026-10-09'},scope:{kind:'self'}};
    assert.equal((await collectFollowups(args)).items[0].id,'all');
    assert.equal((await collectFollowups({...args,query:'全文尾部'})).items[0].id,'全文尾部');
    assert.equal(calls[1].q,'全文尾部'); assert.equal(calls[1].date_to,'2026-10-09');
    await collectFollowups({...args,query:'全文尾部'}); assert.equal(calls.length,2);
  } finally {restore();}
});
test('malformed responses fail and incomplete pagination remains explicitly partial', async () => {
  let response = {}, restore=install({listVisitRange:async()=>response});
  try {
    const args={range:{start:'2026-10-01',end:'2026-10-09'},scope:{kind:'self'}};
    await assert.rejects(collectFollowups(args),/响应不完整/);
    response={items:[{id:'v1'}],summary:{total:100},has_more:true,next_offset:0};
    assert.equal((await collectFollowups(args)).coverage.partial,true);
  } finally {restore();}
});
test('record range consumes more than forty valid pages instead of presenting a capped set as the full table', async () => {
  let calls=0;
  const restore=install({listVisitRange:async ({offset})=>{calls++;return {items:[{id:`v${offset}`}],summary:{total:41},has_more:offset<4000,next_offset:offset<4000?offset+100:null};}});
  try {const result=await collectFollowups({range:{start:'2026-01-01',end:'2026-10-09'},scope:{kind:'self'}});assert.equal(calls,41);assert.equal(result.items.length,41);assert.equal(result.coverage.partial,false);}
  finally {restore();}
});
test('drawer reads full text, preserves explicit link denial, and never returns a stale identity or mismatched record', async () => {
  let raw={id:'v1',follow_up_record:'全文'.repeat(600),next_action:'完整下一步'}, resolve;
  const restore=install({getVisit:async()=>raw});
  try {
    const selected=normalizeVisit({id:'v1',is_summary:true,can_open_customer:false,follow_up_record:'摘要'});
    const full=await loadFollowupDetail(selected); assert.equal(full.content.length,1200); assert.equal(full.canOpenCustomer,false); assert.equal(full.isSummary,false);
    raw={id:'other'}; await assert.rejects(loadFollowupDetail(selected),/未能读取/);
    raw={id:'v1',is_summary:true}; await assert.rejects(loadFollowupDetail(selected),/未能读取/);
    raw=new Promise(r=>{resolve=r;}); const pending=loadFollowupDetail(selected);
    globalThis.SalesRuntime.app.globalData.session.userId='u2'; resolve({id:'v1'});
    await assert.rejects(pending,{name:'AbortError'});
  } finally {restore();}
});
