import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scopeFilter,reviewScopeRequest,scopeQuery,readScopeQuery,multiComposePreset} from '../department-ui/review/scope-selection.js';

const id=n=>`20000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const options={period_kinds:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','team','member'],teams:[{id:id(1),name:'南区'},{id:id(2),name:'北区'}],members:[{id:id(3),name:'成员甲'},{id:id(4),name:'成员乙'}],generation:{profiles:['general','sales_week_six'],available_scope_kinds:['self']}};
const filters={scopeKind:'team',subjectIds:[id(2),id(1),id(2)],period:'week',anchor:'2026-10-10',profile:'general'};

test('URL scope round trip preserves every selected subject, period and date in canonical order',()=>{
  const query=scopeQuery(filters);
  assert.deepEqual(query,{review_scope:'team',review_subjects:`${id(1)},${id(2)}`,review_anchor:'2026-10-10',review_profile:'general',period:'week'});
  const restored=readScopeQuery(query,options);
  assert.equal(restored.error,undefined);
  assert.deepEqual(reviewScopeRequest(restored.filters,options).review,{scope:'team',team_ids:[id(1),id(2)],period_type:'week',anchor_date:'2026-10-10'});
  assert.deepEqual(filters.subjectIds,[id(2),id(1),id(2)]);
  assert.equal(readScopeQuery({period:'week'},options),null);
});
test('URL recovery refuses a stale ID, unavailable kind, empty scope or invalid date without dropping to a readable subset',()=>{
  const valid=scopeQuery(filters);
  for(const query of [
    {...valid,review_subjects:`${id(1)},${id(99)}`},
    {...valid,review_subjects:''},
    {...valid,review_scope:'workspace'},
    {...valid,review_anchor:'2026-02-30'},
    {...valid,review_anchor:'2999-01-01'},
  ]){
    const result=readScopeQuery(query,options);
    assert.equal(result.error,'已保存的查看范围已不可用，请重新选择。');
    assert.equal(Object.hasOwn(result,'filters'),false);
  }
  assert.ok(readScopeQuery(valid,{...options,available_scope_kinds:['self','member']}).error);
});
test('scope kind must match every selected directory ID and self cannot carry subjects',()=>{
  for(const selection of [
    {...filters,subjectIds:[id(1),id(3)]},
    {...filters,scopeKind:'member',subjectIds:[id(3),id(1)]},
    {...filters,scopeKind:'self',subjectIds:[id(3)]},
  ])assert.throws(()=>reviewScopeRequest(selection,options),/同类对象|本人范围/);
  const member=reviewScopeRequest({...filters,scopeKind:'member',subjectIds:[id(4),id(3)]},options);
  assert.deepEqual(member.review.member_ids,[id(3),id(4)]);
  assert.equal(Object.hasOwn(member.review,'team_ids'),false);
});
test('multiple subjects block legacy report binding while explicit single and self preserve the exact legacy scope',()=>{
  const multi=reviewScopeRequest({...filters,scopeKey:`team:${id(1)}`},options);
  assert.equal(multi.multiple,true);assert.equal(multi.legacy,null);
  assert.deepEqual(multi.review.team_ids,[id(1),id(2)]);
  const single=reviewScopeRequest({...filters,subjectIds:[id(2)]},options);
  assert.equal(single.multiple,false);assert.deepEqual(single.review.team_ids,[id(2)]);
  assert.equal(single.legacy.selection.scope.team_id,id(2));
  assert.equal(single.legacy.query.team_id,id(2));
  const self=reviewScopeRequest({...filters,scopeKind:'self',subjectIds:[]},options);
  assert.equal(self.legacy.selection.scope.kind,'self');assert.equal(self.multiple,false);
  assert.deepEqual(scopeFilter({scopeKey:`member:${id(3)}`}),{kind:'member',ids:[id(3)]});
});
test('101 distinct authorized subjects are refused instead of splitting requests or generating the first subject report',()=>{
  const teams=Array.from({length:101},(_,i)=>({id:id(i+100),name:`团队${i}`}));
  assert.throws(()=>reviewScopeRequest({...filters,subjectIds:teams.map(row=>row.id)},{...options,teams}),/1 至 100/);
  const bounded=reviewScopeRequest({...filters,subjectIds:teams.slice(0,100).map(row=>row.id)},{...options,teams});
  assert.equal(bounded.review.team_ids.length,100);assert.equal(bounded.legacy,null);
});

test('multi composer binds exact revision and checks generation directory separately from read access',()=>{
 const subject_ids=[id(1),id(2)],revision='a'.repeat(64);
 const value={selection:{scope:'team',team_ids:subject_ids,member_ids:[],period_type:'week',anchor_date:filters.anchor},period:{label:'本周'},data_as_of:'2026-10-10T12:00:00Z',scope:{contract_version:'review.scope.v2',kind:'team',label:'南北区',subject_ids,subjects:subject_ids.map(id=>({scope_type:'team',id,label:id})),resolved_member_ids:[id(3),id(4)],scope_key:'review-scope:v2:'+'b'.repeat(64),scope_revision:revision,membership_basis:'current_authorized_at_data_as_of'}};
 const permissions={can_create:true,generation_available:true,periods:options.period_kinds,available_scope_kinds:['self','team','member'],teams:options.teams,members:options.members};
 const preset=multiComposePreset(filters,value,permissions);
 assert.equal(preset.kind,'scope_v2');assert.equal(preset.overview,undefined);
 assert.deepEqual(preset.scope,{contract_version:'work_report.scope_selection.v2',kind:'team',team_ids:subject_ids,expected_scope_revision:revision});
 assert.equal(preset.capabilities.can_generate,true);
 assert.equal(multiComposePreset(filters,value,{...permissions,teams:options.teams.slice(0,1)}).capabilities.can_generate,false);
 assert.throws(()=>multiComposePreset({...filters,subjectIds:[id(1),id(9)]},value,permissions));
 assert.throws(()=>multiComposePreset(filters,{...value,scope:{...value.scope,scope_revision:'bad'}},permissions));
});
