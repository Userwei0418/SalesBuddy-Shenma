import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Synthetic preview contract only; this does not validate backend RLS.
function workspace() {
  const storage = new Map();
  const window = {SALES_MODE: 'preview', setTimeout, clearTimeout, localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)}};
  vm.runInNewContext(fs.readFileSync(new URL('../preview-api.js', import.meta.url), 'utf8'), {window, URL, URLSearchParams, setTimeout, clearTimeout});
  return (role, path, method = 'GET', data) => new Promise(resolve => window.SalesPreview.request({url: 'https://offline.invalid/api/v1' + path, method, data, header: {Authorization: 'Bearer preview-access-' + role}, success: resolve}));
}
const ok = async promise => { const response = await promise; assert.equal(response.statusCode, 200, response.data.message); return response.data; };

test('risk preview keeps full scoped counts across pages/tabs; decision is idempotent and leaves the risk open',async()=>{
  const read=workspace(), all=await ok(read('sales','/risks'));
  const page=await ok(read('sales','/risks?status=open&page_size=1'));
  assert.equal(page.items.length,1);assert.equal(page.summary.total,all.total);assert.ok(page.summary.resolved_count>0);
  assert.equal(page.total,page.summary.open_count);
  const me=(await ok(read('sales','/auth/me'))).actor;
  const own=all.items.find(r=>r.owner_id===me.user_id && r.status!=='resolved');assert.ok(own);
  const detail=await ok(read('sales','/risks/'+own.id));assert.equal(detail.can_decide,true);assert.equal(detail.can_resolve,false);
  const tasks=await ok(read('sales','/tasks?page_size=100'));
  const body={decision:'adopt',note:'先与客户核实预算审批',version_no:detail.version_no,request_id:'00000000-0000-4000-8000-000000000001'};
  const decided=await ok(read('sales','/risks/'+own.id+'/decision','POST',body));
  assert.equal(decided.risk.status,own.status);assert.equal(decided.risk.current_decision.decision,'adopt');
  const replay=await ok(read('sales','/risks/'+own.id+'/decision','POST',body));assert.equal(replay.idempotent_replay,true);assert.equal(replay.risk.events.length,1);
  assert.equal((await read('sales','/risks/'+own.id+'/decision','POST',{...body,note:'改了内容'})).statusCode,409);
  assert.equal((await ok(read('sales','/tasks?page_size=100'))).total,tasks.total);
  assert.equal((await read('sales','/risks/'+own.id+'/resolve','POST',{version_no:decided.risk.version_no,resolution_note:'未经负责人审批关闭'})).statusCode,403);
  const manager=await ok(read('manager','/risks/'+own.id));assert.equal(manager.can_resolve,true);
  assert.equal((await read('manager','/risks/'+own.id+'/resolve','POST',{version_no:detail.version_no,resolution_note:'风险已经消除的依据'})).statusCode,409);
  const resolved=await ok(read('manager','/risks/'+own.id+'/resolve','POST',{version_no:manager.version_no,resolution_note:'客户已提供书面预算审批'}));
  assert.equal(resolved.status,'resolved');assert.equal(resolved.can_decide,false);assert.equal(resolved.events.length,2);
  const history=await ok(read('sales','/risks?status=resolved'));assert.ok(history.items.some(r=>r.id===own.id));assert.equal(history.summary.total,all.total);
});

test('risk preview readable teammate records do not confer decision rights and invalid versions fail',async()=>{
  const read=workspace(),me=(await ok(read('fde','/auth/me'))).actor;
  const risks=await ok(read('fde','/risks'));
  const peer=risks.items.find(r=>r.owner_id!==me.user_id);assert.ok(peer);
  const detail=await ok(read('fde','/risks/'+peer.id));assert.equal(detail.can_decide,false);
  assert.equal((await read('fde','/risks/'+peer.id+'/decision','POST',{decision:'defer',note:'等待新预算',version_no:1,request_id:'00000000-0000-4000-8000-000000000002'})).statusCode,403);
  assert.equal((await read('manager','/risks/'+peer.id+'/resolve','POST',{resolution_note:'未提供版本不得关闭'})).statusCode,422);
});

