import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import React from 'react';
import {transformSync} from 'esbuild';
import {historyScore, historyView, basisLabel, fdeUnit} from '../department-ui/profile-history.js';

function component(file, imports = {}, exported = 'default') {
  const module = {exports: {}};
  vm.runInNewContext(transformSync(fs.readFileSync(new URL('../department-ui/' + file, import.meta.url), 'utf8'), {loader: 'jsx', format: 'cjs'}).code, {
    module, exports: module.exports, require: name => name === 'react' ? React : name === './profile-history.js' ? {historyScore, historyView, basisLabel, fdeUnit} : imports[name] || new Proxy({}, {get: (_, key) => key === '__esModule' ? true : String(key)}),
  });
  return module.exports[exported];
}
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...React.Children.toArray(tree.props?.children).flatMap(nodes)];
const text = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree === 'object' ? React.Children.toArray(tree.props?.children).map(text).join(' ') : String(tree);
const row = (review_date, overall_score, framework_version = 1) => ({review_date, overall_score, score_summary:{value:overall_score},score_basis:{snapshot_status:'frozen',basis_id:'basis-'+framework_version,framework_version}});

test('calendar spacing uses dates, preserves zero and never treats a missing assessment as zero', () => {
  const value = historyView([row('2026-10-09', 90), row('2026-10-01', 0), row('2026-10-02', '70')]);
  assert.deepEqual(value.points.map(p => [p.date, p.score, p.x]), [['2026-10-01', 0, 0], ['2026-10-02', 70, 0.125], ['2026-10-09', 90, 1]]);
  assert.equal(value.segments.length, 2);
  for (const missing of [null, undefined, '', ' ', [], {}, '--', false, true, 'n/a', NaN, Infinity, -1, 101]) assert.equal(historyScore(missing), null);
});
test('missing results, changed or unknown frameworks and repeated dates do not create continuous trends', () => {
  const value = historyView([row('2026-10-01', 60), row('2026-10-02', null), row('2026-10-03', 70), row('2026-10-04', 80, 2), {review_date: '2026-10-05', overall_score: 90}]);
  assert.equal(value.points.length, 4);assert.equal(value.segments.length, 0);
  assert.equal(value.unknownRules, true);assert.equal(value.multipleRules, true);
  assert.equal(historyView([row('2026-10-01', 30), row('2026-10-01', 60)]).segments.length, 0);
});
test('undated and invalid-calendar results remain in the table but are not placed on a fabricated timeline', () => {
  const value = historyView([row('2026-02-30', 90), row('', 80), row('2026-10-01', 70)]);
  assert.equal(value.rows.length, 3);assert.equal(value.points.length, 1);assert.equal(value.points[0].x, 0.5);
  const dimension = historyView([{...row('2026-10-01', 90), dimension_scores: {plan: {score: 0}}}], 'plan');
  assert.equal(dimension.points[0].score, 0);
  assert.equal(historyView([row('2026-10-01', 90)], 'plan').points.length, 0);
});
test('history renders one real point with date and accessible score, and keeps null in the detail table', () => {
  const History = component('ProfileHistory.jsx');
  const view = History({history: [row('2026-10-01', 0), row('2026-10-02', null)]});
  assert.equal(nodes(view).filter(node => node.type === 'circle').length, 1);
  assert.equal(nodes(view).filter(node => node.type === 'path').length, 0);
  assert.match(nodes(view).find(node => node.type === 'circle').props['aria-label'], /2026-10-01：0分/);
  assert.match(text(view), /未评估/);assert.match(text(view), /不用当前规则重算历史/);
});
test('FDE history presents ratios without inventing a performance score or framework', () => {
  const History = component('ProfileHistory.jsx');
  const view = History({history: [{date: '2026-10-01', overall_score: 50}, {date: '2026-10-03', overall_score: null}], unit: '%', factRatios: true});
  assert.match(text(view), /50%/);assert.match(text(view), /事实指标/);
  assert.doesNotMatch(text(view), /历史评分|当前综合分|V事实比例/);
  assert.equal(nodes(view).filter(node => node.type === 'path').length, 0);
  assert.equal(historyView([{date:'2026-10-08T18:30:00Z',overall_score:50}], 'overall', true).rows[0].date,'2026-10-09');
});

test('frozen company score wins over framework score; null never falls back; equal framework versions alone are not comparable',()=>{
 const records=[{...row('2026-10-01',95),score_summary:{value:60}},{...row('2026-10-02',90),score_summary:{value:null}},{...row('2026-10-03',90),score_summary:{value:70},score_basis:{snapshot_status:'frozen',framework_version:1,basis_id:'other-policy'}},{review_date:'2026-10-04',overall_score:88,input_snapshot:{framework_version:1}}];
 const value=historyView(records);assert.deepEqual(value.rows.map(r=>r.score),[60,null,70,88]);assert.equal(value.segments.length,0);
 assert.equal(value.rows[3].ruleLabel,'历史口径未知');assert.equal(value.unknownRules,true);
 assert.equal(historyView([{...row('2026-10-01',90),score_summary:null}]).rows[0].score,null);
 assert.equal(fdeUnit('record_quality'),'分');assert.equal(fdeUnit('task_completion'),'%');assert.equal(fdeUnit('unrecognized'),'');
});
test('evidence coverage preserves 0, 80/81 and legacy unknown without conflating FDE advice limits',()=>{
 const Coverage=component('ProfileReview.jsx',{},'EvidenceCoverage');
 assert.match(text(Coverage({value:{status:'known',used_visits:80,total_eligible_visits:81,limit:80,truncated:true}})),/80\s*\/\s*81/);
 assert.match(text(Coverage({value:{status:'known',used_visits:80,total_eligible_visits:81,limit:80,truncated:true}})),/未覆盖全部材料/);
 assert.match(text(Coverage({value:{status:'legacy_unknown',used_visits:80}})),/不能确认已使用全部记录/);
 assert.match(text(Coverage({value:{status:'known',used_visits:0,total_eligible_visits:0,truncated:false}})),/0\s*\/\s*0/);
 const fde=text(Coverage({fde:true,value:{archived_visits:90,readable_archived_visits:80,assigned_projects:2,assigned_customers:1,owned_tasks:30},coaching:{status:'known',selected_visits:12,visit_limit:12,selected_tasks:20,task_limit:20}}));
 assert.match(fde,/归档记录\s*90/);assert.match(fde,/上限 12/);assert.match(fde,/上限 20/);assert.match(fde,/不改变上方事实指标/);
});
test('manual update follows canReview and busy state; rendering makes no mutation',()=>{
 const Context=component('ProfileReview.jsx',{},'ReviewContext'),calls=[];
 const idle=Context({value:{canReview:true,reviewId:'r',evaluationState:'partially_assessed'},onRequest:()=>calls.push('request'),onEvidence:id=>calls.push(id)});
 assert.deepEqual(calls,[]);assert.match(text(idle),/不表示六个维度均已评估/);
 nodes(idle).find(n=>n.type==='Button'&&text(n)==='更新我的评估').props.onClick();assert.deepEqual(calls,['request']);
 const denied=Context({value:{canReview:false},onRequest:()=>{}});assert.equal(nodes(denied).filter(n=>n.type==='Button').length,0);
 const busy=Context({value:{canReview:true,reviewId:'r',currentReview:{status:'queued'}},onRequest:()=>{}});
 assert.equal(nodes(busy).find(n=>n.type==='Button').props.disabled,true);assert.match(text(busy),/保留上次/);
});
test('evidence drawer hides stale or forbidden detail and only navigates explicit currently-readable references',()=>{
 const Evidence=component('ProfileReview.jsx',{},'GrowthEvidence'),calls=[],id='00000000-0000-4000-8000-000000000001';
 const result={assessment_kind:'sales_competency',review:{id:'review',summary:'safe summary'},evidence:[{readable:false,visit_id:id,detail:'MUST_NOT_SHOW'},{readable:true,visit_id:id,detail:'可核对依据'}]};
 const props={value:{visible:true,reviewId:'review',result},onSource:(...args)=>calls.push(args)};
 const view=Evidence(props);assert.doesNotMatch(text(view),/MUST_NOT_SHOW/);assert.match(text(view),/当前无法查看/);
 const link=nodes(view).find(n=>n.type==='Button');link.props.onClick();assert.deepEqual(calls,[['visit',id]]);
 for(const value of [{...props.value,loading:true},{...props.value,error:'已撤权'},{...props.value,reviewId:'other'}]){
  const hidden=Evidence({...props,value});assert.doesNotMatch(text(hidden),/safe summary|可核对依据|MUST_NOT_SHOW/);assert.equal(nodes(hidden).filter(n=>n.type==='Button').length,0);
 }
 assert.equal(Evidence({...props,value:{...props.value,visible:false}}),null);
});
test('sales dimensions expose assessment separately from advice and missing scores remain unassessed', () => {
  const Profile = component('Profile.jsx');
  const view = Profile({page: {}, data: {activeProfileTab: 'profile', growthReady: true, dimensions: [{code: 'need', score: null, assessment: '缺少需求确认依据', coachingAction: '下一次核实需求'}]}, invoke: () => {}});
  const table = nodes(view).find(node => node.type === 'SbTable');
  const assessment = table.props.columns.find(column => column.key === 'assessment');
  assert.equal(assessment.render(null, table.props.rows[0]), '缺少需求确认依据');
  assert.equal(text(table.props.columns.find(column => column.key === 'score').render(null, table.props.rows[0])), '未评估');
});

test('mixed team rules show no total or artificial personal history; insufficient-data advice is not labelled as generated AI',()=>{
 const Profile=component('Profile.jsx');
 const mixed=Profile({page:{},data:{activeProfileTab:'profile',role:'sales',profileScopeMode:'team',growthReady:true,overallScore:'99',growthReview:{comparisonStatus:'mixed_basis'}},invoke:()=>{}});
 assert.equal(nodes(mixed).some(node=>node.type?.name==='ScoreTile'),false);assert.equal(nodes(mixed).some(node=>node.type?.name==='Sparkline'),false);
 const empty=Profile({page:{},data:{activeProfileTab:'profile',growthReady:true,growthReview:{evaluationState:'insufficient_data'},aiAdvice:['补充正式跟进']},invoke:()=>{}});
 assert.match(text(empty),/材料补充建议/);assert.equal(nodes(empty).some(node=>node.type==='SbAiBadge'),false);
});
