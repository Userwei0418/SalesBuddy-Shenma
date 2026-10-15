import {test} from 'node:test';
import assert from 'node:assert/strict';
import {snapshotSelection} from '../department-ui/work-report/composition.js';

const options={periods:['day','week','two_weeks','month','quarter'],available_scope_kinds:['self','member','team','workspace'],members:[{id:'member'}],teams:[{id:'team'}],sources:[{id:'visit',available:true},{id:'risk',available:false}]};
const draft={period:'biweek',anchor:'2026-01-01',scope:{kind:'self'},sources:['visit'],supplement:'',supplementId:'supplement'};
test('snapshot requests contain selectors only, while rejecting unavailable scope, source and dates',()=>{
  const all=snapshotSelection({...draft,scope:{kind:'workspace',member_ids:['untrusted'],team_id:'untrusted'}},options);
  assert.deepEqual(all.scope,{kind:'workspace'});assert.deepEqual(all.period,{kind:'two_weeks',anchor_date:'2026-01-01',timezone:'Asia/Shanghai'});assert.equal('metrics' in all,false);
  for(const patch of [{anchor:'2026-02-30'},{anchor:'2999-01-01'},{scope:{kind:'member',member_id:'outside'}},{scope:{kind:'team',team_id:'outside'}},{sources:['risk']},{sources:[]}])assert.throws(()=>snapshotSelection({...draft,...patch},options));
  assert.throws(()=>snapshotSelection({...draft,scope:{kind:'workspace'}},{...options,available_scope_kinds:['self']}));
  assert.throws(()=>snapshotSelection(draft,{...options,periods:['day']}));
});
test('multi-member and multi-team reports preserve V2 array selectors and deduplicate them',()=>{
  const multi={...options,members:[{id:'member-a'},{id:'member-b'}],teams:[{id:'team-a'},{id:'team-b'}]};
  const members=snapshotSelection({...draft,scope:{kind:'member',member_ids:['member-b','member-a','member-b']}},multi);
  assert.deepEqual(members.scope,{contract_version:'work_report.scope_selection.v2',kind:'member',member_ids:['member-b','member-a']});
  const teams=snapshotSelection({...draft,scope:{kind:'team',team_ids:['team-a','team-b','team-a']}},multi);
  assert.deepEqual(teams.scope,{contract_version:'work_report.scope_selection.v2',kind:'team',team_ids:['team-a','team-b']});
  assert.throws(()=>snapshotSelection({...draft,scope:{kind:'member',member_ids:[]}},multi),/1 至 100/);
});
test('supplement-only preparation preserves retry identity and cannot become completed system facts',()=>{
  const input={...draft,sources:[],supplement:'  下期计划  '};
  const selected=snapshotSelection(input,options);
  assert.deepEqual(selected.supplements,[{id:'supplement',text:'下期计划',declared_kind:'context'}]);
  assert.deepEqual(snapshotSelection(input,options),selected);
  assert.throws(()=>snapshotSelection({...input,supplement:'字'.repeat(16001)},options),/16000/);
});
test('six-section facts are selected by the saved scheme contract, never retrofitted to old or nonweekly requests',()=>{
  const facts_contract='work_report.sales_week_facts.v1',input={...draft,period:'week',facts_contract,sources:['actual']};
  const available={...options,sources:[...options.sources,{id:'actual',available:true}]};
  assert.equal(snapshotSelection(input,available).facts_contract,facts_contract);
  assert.equal('facts_contract' in snapshotSelection(draft,available),false);
  assert.throws(()=>snapshotSelection({...input,period:'month'},available),/仅适用于自然周/);
  assert.throws(()=>snapshotSelection({...input,facts_contract:'future'},available),/版本暂不受支持/);
  assert.throws(()=>snapshotSelection({...input,facts_contract:undefined},available),/新版销售周报/);
});
test('supplements target explicit weekly sections and remain user plans rather than confirmed amounts',()=>{
  const facts_contract='work_report.sales_week_facts.v1',input={...draft,period:'week',facts_contract,supplement:'客户甲下周预计回款 10 万元，尚未核实',supplementSection:'next_week_collection'};
  const value=snapshotSelection(input,options);
  assert.deepEqual(value.supplements,[{id:'supplement',text:input.supplement,declared_kind:'plan',section_id:'next_week_collection'}]);
  assert.equal('metrics' in value,false);assert.equal('actuals' in value,false);
  assert.throws(()=>snapshotSelection({...input,supplementSection:'invented'},options),/补充章节/);
  assert.throws(()=>snapshotSelection({...input,facts_contract:undefined},options),/补充章节/);
  const old=snapshotSelection({...draft,supplement:'旧的自由补充'},options);
  assert.equal('section_id' in old.supplements[0],false);assert.equal(old.supplements[0].declared_kind,'context');
});

test('generic V2 supports every original period without changing to daily/weekly Review mode',()=>{
  for(const period of ['day','week','biweek','month','quarter'])for(const kind of ['team','member']){
    const scope={kind,[kind+'_ids']:[kind],expected_scope_revision:'a'.repeat(64)};
    const selection=snapshotSelection({...draft,period,scope},options);
    assert.equal(selection.scope.contract_version,'work_report.scope_selection.v2');
    assert.equal(selection.scope.expected_scope_revision,scope.expected_scope_revision);
    assert.equal(selection.period.kind,period==='biweek'?'two_weeks':period);
    assert.equal('review_mode' in selection,false);
  }
  assert.throws(()=>snapshotSelection({...draft,scope:{kind:'team',team_ids:['team'],member_ids:['member']}},options),/同类/);
  assert.throws(()=>snapshotSelection({...draft,scope:{kind:'team',team_ids:['team'],expected_scope_revision:'bad'}},options),/范围版本/);
});
