import test from 'node:test';
import assert from 'node:assert/strict';
import {changeScopeSelection, dateFilterError, dateFilterLabel, ownersInTeams, scopeFilterLabel, scopeSelection} from '../department-ui/pc/opportunity-scope-date.js';
import {presentationError, querySignature} from '../department-ui/pc/opportunity-workspace.js';

const teams = [{id:'south',name:'南区'},{id:'east',name:'东区'}];
const owners = [{id:'a',name:'甲',team_ids:['south']},{id:'b',name:'乙',team_ids:['east']},{id:'c',name:'丙',team_ids:['east','south']},{id:'u',name:'未分组',team_ids:[]}];
const base = {schema_version:2,columns:[{key:'name'}],filters:[],sorts:[],scope:{kind:'authorized'},period:{kind:'fixed',year:2026,quarter:4}};

test('legacy single selections restore without mutating saved view; new changes remove singular keys',()=>{
  const original = {kind:'authorized',team_id:'south',owner_id:'a'};
  assert.deepEqual(scopeSelection(original),{team_ids:['south'],owner_ids:['a']});
  assert.deepEqual(changeScopeSelection(original,{team_ids:['south','east']},owners),{kind:'authorized',team_ids:['south','east'],owner_ids:['a']});
  assert.deepEqual(original,{kind:'authorized',team_id:'south',owner_id:'a'});
});
test('department union supplies members; narrowing departments preserves compatible selections only',()=>{
  assert.deepEqual(ownersInTeams(owners,['south','east']).map(item=>item.id),['a','b','c']);
  assert.deepEqual(ownersInTeams(owners,[]),owners);
  const scope={kind:'authorized',team_ids:['south','east'],owner_ids:['a','b','c']};
  assert.deepEqual(changeScopeSelection(scope,{team_ids:['east']},owners),{kind:'authorized',team_ids:['east'],owner_ids:['b','c']});
  assert.deepEqual(changeScopeSelection(scope,{team_ids:[]},owners).owner_ids,['a','b','c']);
  assert.deepEqual(changeScopeSelection(scope,{owner_ids:[]},owners).team_ids,['south','east']);
});
test('date range accepts same-day and cross-year without altering financial quarter or date meaning',()=>{
  for(const [start,end] of [['2026-10-12','2026-10-12'],['2026-12-25','2027-01-08'],['2028-02-29','2028-03-01']]) {
    const date_filter={field:'business_created_on',start,end};
    assert.equal(dateFilterError(date_filter),'');
    assert.equal(presentationError({...base,date_filter},[{key:'name'}]),'');
    assert.notEqual(querySignature({...base,date_filter}),querySignature(base));
    assert.notEqual(querySignature({...base,date_filter}),querySignature({...base,date_filter:{...date_filter,field:'expected_close_date'}}));
    assert.deepEqual(base.period,{kind:'fixed',year:2026,quarter:4});
  }
});
test('malformed, impossible or reversed saved dates are rejected before query/save',()=>{
  for(const value of [{field:'created_at',start:'2026-01-01',end:'2026-01-02'},{field:'business_created_on',start:'2026-02-30',end:'2026-03-01'},{field:'business_created_on',start:'2026-10-12',end:'2026-10-01'},{field:'expected_close_date',start:'2026-01-01',end:''}]) {
    assert.ok(dateFilterError(value));
    assert.ok(presentationError({...base,date_filter:value},[{key:'name'}]));
  }
  assert.equal(dateFilterError(null),'');assert.equal(dateFilterError(undefined),'');
});
test('board context identifies date field, all-scope and exact saved names',()=>{
  assert.equal(scopeFilterLabel({kind:'authorized',team_ids:['south','east'],owner_ids:['a','c']},teams,owners),'南区、东区 · 甲、丙');
  assert.equal(scopeFilterLabel({kind:'self'},teams,owners),'本人');
  assert.match(scopeFilterLabel(base.scope,teams,owners),/全部可见部门.*全部可见成员/);
  assert.equal(dateFilterLabel({field:'expected_close_date',start:'2026-10-01',end:'2026-10-12'}),'预计关单日期：2026-10-01 — 2026-10-12');
});
