import test from 'node:test';
import assert from 'node:assert/strict';
import {amountCny, opportunityAppearance, normalizeDemoDraft, demoDraftKey, searchBusiness, validateDemoDraft} from '../department-ui/shenzhou-demo.mjs';

test('CNY uses the exact yuan value and never expands rounded shorthand',()=>{
  for (const [value,want] of [['239736.15','239,736.15'],[50000,'50,000.00'],[0,'0.00'],['001.2','1.20'],['9007199254740993.01','9,007,199,254,740,993.01']]) assert.equal(amountCny(value),want);
  for (const bad of [null,undefined,'','5万','-10','NaN','1.234',{},true]) assert.equal(amountCny(bad),'未填写');
  assert.equal(amountCny(5,'USD'),'币种待确认');
});
test('eight fields do not infer unconfirmed partner/customer/source mappings',()=>{
  const facts=opportunityAppearance({name:'样例商机',amount:'239736.15',expected_close_date:'2026-09-11',partner_name:'不得代入',customer_name:'不得代入',source:'不得代入',is_framework:true});
  assert.equal(facts.length,8);assert.equal(facts[1][1],'239,736.15');assert.equal(facts[4][1],'2026-09-11');
  for (const i of [2,3,5,6,7]) assert.equal(facts[i][1],'未填写');
});
test('demo drafts discard stale/unknown IDs and never carry partner payload',()=>{
  assert.deepEqual(normalizeDemoDraft({version:1,followUpType:'商机推进',relatedBusinessIds:['demo-001','unknown','demo-001'],partner_name:'old'}),{version:1,followUpType:'商机推进',relatedBusinessIds:['demo-001']});
  assert.deepEqual(normalizeDemoDraft({version:2,followUpType:'商机推进',relatedBusinessIds:['demo-001']}),normalizeDemoDraft(null));
  assert.equal(normalizeDemoDraft({version:1,followUpType:'other'}).followUpType,'');
});
test('draft scope separates accounts, customers and entry records',()=>{
  const keys=[demoDraftKey({userKey:'a',entryDraftId:'1'},{customerId:'c'}),demoDraftKey({userKey:'b',entryDraftId:'1'},{customerId:'c'}),demoDraftKey({userKey:'a',entryDraftId:'1'},{customerId:'d'}),demoDraftKey({userKey:'a',entryDraftId:'2'},{customerId:'c'}),demoDraftKey({userKey:'a',entryDraftId:'1'},{customerId:'c',visitId:'saved-1',editing:true})];
  assert.equal(new Set(keys).size,keys.length);
  assert.equal(demoDraftKey({userKey:'a',entryDraftId:'1'},{customerId:'c',visitId:'saved-1',archived:true}),keys[0],'archive keeps the entry draft scope');
});
test('search and validation support empty, missing and complete demo selections',()=>{
  assert.equal(searchBusiness('DEMO-002')[0].id,'demo-002');assert.equal(searchBusiness('  扩容 ')[0].id,'demo-004');assert.equal(searchBusiness('no-such-record').length,0);
  assert.ok(validateDemoDraft(normalizeDemoDraft(null)).followUpType);
  assert.deepEqual(validateDemoDraft({followUpType:'客户拜访',relatedBusinessIds:['demo-001']}),{followUpType:'',relatedBusinessIds:''});
});
