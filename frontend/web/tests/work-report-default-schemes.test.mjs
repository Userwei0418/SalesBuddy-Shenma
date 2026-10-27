import {test} from 'node:test';
import assert from 'node:assert/strict';
import {composeSchemes,chooseComposeScheme,compatibleScheme} from '../department-ui/work-report/default-schemes.js';
const weekly={id:'saved-week',version_no:7,name:'保留现有六节周报',config:{default_period:'week',facts_contract:'work_report.sales_week_facts.v1',sections:['unchanged']}};
const personalMonth={id:'saved-month',version_no:3,name:'我的月报',config:{default_period:'month',sections:['custom']}};
const options={builtin_schemes:['day','two_weeks','month','quarter'].map(period=>({scheme_id:`builtin-${period}`,version_no:1,name:period,config_hash:'hash',config:{default_period:period,sections:['general']}})),general_scheme:{default_period:'day'}};
const weeklyDefaults=[
  {scheme_id:'builtin-week-general',version_no:1,name:'默认通用周报',config_hash:'general-hash',config:{default_period:'week',facts_contract:null}},
  {scheme_id:'builtin-week-sales',version_no:1,name:'默认销售六节周报',config_hash:'sales-hash',config:{default_period:'week',facts_contract:weekly.config.facts_contract}},
];
test('empty weekly scheme lists use server defaults matching the requested facts contract',()=>{
  const rows=composeSchemes([],{builtin_schemes:[...options.builtin_schemes,...weeklyDefaults]});
  assert.equal(chooseComposeScheme(rows,{period:'week'})?.id,'builtin-week-general');
  assert.equal(chooseComposeScheme(rows,{period:'week',factsContract:weekly.config.facts_contract})?.id,'builtin-week-sales');
  const generalOnly=rows.filter(row=>row.id!=='builtin-week-sales');
  assert.equal(chooseComposeScheme(generalOnly,{period:'week',factsContract:weekly.config.facts_contract}),undefined);
  assert.equal(chooseComposeScheme(rows,{period:'week',schemeId:'deleted'}),undefined);
});
test('new weekly defaults preserve saved weekly preference and all four earlier default identities',()=>{
  const rows=composeSchemes([weekly],{builtin_schemes:[...weeklyDefaults,...options.builtin_schemes]});
  assert.equal(chooseComposeScheme(rows,{period:'week',factsContract:weekly.config.facts_contract}),weekly);
  for(const period of ['day','two_weeks','month','quarter'])assert.equal(chooseComposeScheme(rows,{period}).id,`builtin-${period}`);
});
test('old weekly reference and content stay exact; each nonweekly period has its own server builtin',()=>{
  const rows=composeSchemes([weekly],options);
  assert.equal(chooseComposeScheme(rows,{period:'week'}),weekly);
  for(const period of ['day','two_weeks','month','quarter'])assert.equal(chooseComposeScheme(rows,{period}).id,`builtin-${period}`);
  assert.equal(weekly.version_no,7);assert.deepEqual(weekly.config.sections,['unchanged']);
});
test('matching user schemes precede defaults and explicitly selected custom schemes are retained',()=>{
  const rows=composeSchemes([weekly,personalMonth],options);
  assert.equal(chooseComposeScheme(rows,{period:'month'}),personalMonth);
  assert.equal(chooseComposeScheme(rows,{period:'day',schemeId:personalMonth.id}),personalMonth);
  assert.equal(compatibleScheme(weekly,'day'),false);assert.equal(compatibleScheme(personalMonth,'quarter'),true);
  assert.equal(compatibleScheme(rows.find(row=>row.id==='builtin-day'),'quarter'),false);
  assert.equal(chooseComposeScheme(rows,{period:'week',schemeId:'deleted'}),undefined);
});
test('general editor configuration does not become a fabricated runnable reference',()=>{
  const rows=composeSchemes([weekly],{general_scheme:{default_period:'day'}});
  assert.equal(rows.length,1);assert.equal(chooseComposeScheme(rows,{period:'day'}),undefined);
  assert.throws(()=>composeSchemes([weekly],{builtin_schemes:[{...options.builtin_schemes[0],scheme_id:weekly.id}]}),/响应不完整/);
});
