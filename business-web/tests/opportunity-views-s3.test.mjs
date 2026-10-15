import {test} from 'node:test';
import assert from 'node:assert/strict';
import {captureViewConfig,defaultViewConfig,viewState,validateViewConfig,groupViewRows} from '../department-ui/opportunityViewState.mjs';
const data={executionTeamOptions:[{value:'all',label:'全部团队'}],opportunityOwnerOptions:[{value:'all',label:'全部负责人'}],opportunityGradeOptions:[{value:'all'},...['A','B','C','D'].map(value=>({value}))],opportunityCloseOptions:['all','month','quarter','year'].map(value=>({value})),opportunityStageOptions:[{value:'qualified'}],quarterYearOptions:[{value:2026,label:'2026年'}]};
test('saved config restores every narrowing filter, stable IDs and display setting together',()=>{
 const config={...defaultViewConfig(),columns:['name','owner','amount','product'],order:'close_date',group_by:'none',filters:{q:'项目',scope:'self',team_id:'00000001-0000-4000-8000-000000000001',owner_id:'00000002-0000-4000-8000-000000000001',stages:['qualified'],grade:'B',product_line:'基础设施',year:2030,quarters:[2,3],close_period:'all',include_closed:false}};
 const state=viewState(config,data);assert.deepEqual(captureViewConfig({...data,...state}),config);
 assert.equal(state.executionTeamOptions[state.executionTeamIndex].label,'已保存团队');assert.equal(state.opportunityOwnerOptions[state.opportunityOwnerIndex].label,'已保存负责人');assert.equal(state.quarterYearOptions[state.listYearIndex].value,2030);
});
test('nullable unused year round trips without silently changing config',()=>{
 const config=defaultViewConfig(null);assert.deepEqual(captureViewConfig({...data,...viewState(config,data)}),config);
});
test('default selection clears every prior filter and resets all display settings',()=>{
 const dirty={...data,opportunityViewScope:'team',opportunityIncludeClosed:false,opportunityProductLine:'旧',executionTeamKey:'old',opportunityQuery:'旧',opportunitySelectedStages:['won'],opportunityViewOrder:'close_date',opportunityViewColumns:['name','owner']};
 const config=defaultViewConfig(2026);assert.deepEqual(captureViewConfig({...dirty,...viewState(config,dirty)}),config);
});
test('unrecognized or contradictory persisted settings are rejected as a whole',()=>{
 for(const bad of [{...defaultViewConfig(),schema_version:2},{...defaultViewConfig(),columns:['name','secret']},{...defaultViewConfig(),order:'active_first'},{...defaultViewConfig(),group_by:'team'},{...defaultViewConfig(),filters:{...defaultViewConfig().filters,quarters:[2],close_period:'month'}}])assert.throws(()=>validateViewConfig(bad));
});
test('rows use original server order and group encounter order without sorting by stage',()=>{
 const rows=[{id:'a',expected_close_date:'2026-10-01'},{id:'b',expected_close_date:'2025-02-01'},{id:'c',expected_close_date:'2026-11-01'},{id:'d'}];
 assert.deepEqual(groupViewRows(rows,'none')[0].items,rows);assert.deepEqual(groupViewRows(rows,'quarter').map(g=>g.items.map(r=>r.id)),[['a','c'],['b'],['d']]);
});
