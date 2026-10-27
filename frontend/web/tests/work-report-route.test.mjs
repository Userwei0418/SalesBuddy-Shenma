import {test} from 'node:test';
import assert from 'node:assert/strict';
import {reportRoute,reportQuery,initialReportScope} from '../department-ui/work-report/route.js';
test('report deep links retain only valid resource references and known tabs',()=>{
 const id='10000000-0000-4000-8000-000000000001';
 const route=reportRoute({document_id:id,run_id:id,run_document_id:id,report_tab:'documents',scheme_id:id,body:'private text',reference_texts:'private sample'});
 assert.deepEqual(reportQuery(route),{report_tab:'documents',document_id:id,scheme_id:id,run_id:id,run_document_id:id});
 assert.deepEqual(reportRoute({run_id:'not-a-uuid',document_id:'../anything',report_tab:'unknown'}),{tab:'meeting',documentId:null,schemeId:null,activeRun:null});
 assert.equal(reportRoute({report_tab:'compose'}).tab,'compose');
 assert.equal(reportRoute({report_tab:'targets'}).tab,'targets');
 assert.equal(reportRoute({report_tab:'okr'}).tab,'okr');
 assert.equal(reportRoute({run_id:id}).tab,'compose');
 assert.equal(reportRoute({scheme_id:id}).tab,'compose');
 assert.equal(reportRoute({document_id:id}).tab,'documents');
});
test('Review deep links re-enter overview instead of silently generating for self; existing runs remain recoverable',()=>{
 const first='10000000-0000-4000-8000-000000000001',second='10000000-0000-4000-8000-000000000002';
 for(const review_scope of ['team','member','self','invalid']){
   const query={report_tab:'compose',review_scope,review_subjects:`${first},${second}`};
   assert.equal(reportRoute(query).tab,'meeting');
   assert.equal(reportRoute({...query,scheme_id:first}).tab,'meeting');
   assert.equal(reportRoute({...query,run_id:first}).tab,'compose');
   assert.equal(reportRoute({...query,report_tab:'documents',document_id:first}).tab,'documents');
 }
 assert.equal(reportRoute({report_tab:'compose'}).tab,'compose');
});
test('workbench scope links reuse only selectors in the freshly authorized report directory',()=>{
 const options={available_scope_kinds:['self','member','team','workspace'],members:[{id:'member-one'}],teams:[{id:'team-one'}]};
 assert.deepEqual(initialReportScope({scope:'member',member:'member-one'},options),{scope:{kind:'member',member_id:'member-one'}});
 assert.deepEqual(initialReportScope({scope:'team',team:'team-one'},options),{scope:{kind:'team',team_id:'team-one'}});
 assert.deepEqual(initialReportScope({scope:'team',team:'all'},options),{scope:{kind:'workspace'}});
 assert.ok(initialReportScope({scope:'member',member:'revoked'},options).error);
 assert.ok(initialReportScope({scope:'team',team:'all'},{...options,available_scope_kinds:['self']}).error);
 assert.equal(initialReportScope({},options),null);
});

test('running multi-scope composer reentry retains every authorized selector and rejects revoked IDs',()=>{
 const a='10000000-0000-4000-8000-000000000001',b='10000000-0000-4000-8000-000000000002';
 const options={periods:['month'],available_scope_kinds:['team'],teams:[{id:a},{id:b}],members:[]};
 const query={review_scope:'team',review_subjects:[a,b].join(','),review_anchor:'2026-10-14',period:'month'};
 assert.deepEqual(initialReportScope(query,options).scope,{kind:'team',contract_version:'work_report.scope_selection.v2',team_ids:[a,b]});
 assert.ok(initialReportScope(query,{...options,teams:[{id:a}]}).error);
});
