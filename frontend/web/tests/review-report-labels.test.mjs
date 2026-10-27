import test from 'node:test';
import assert from 'node:assert/strict';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {measureSubjectLabel,metricLabel,missingLabel,reviewContentProblem,displayStatement,reportText} from '../department-ui/review/report/model.js';
import {evidenceView} from '../department-ui/review/report/evidence.js';
import {displayItem} from '../department-ui/work-report/content.js';
import {sample,clone} from './review-report-fixtures.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
const compiled=buildSync({absWorkingDir:root,entryPoints:['department-ui/review/report/ReviewReportContent.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
const mod=new Module(root+'review-labels.cjs');mod.filename=root+'review-labels.cjs';mod.paths=Module._nodeModulePaths(root);mod._compile(compiled,mod.filename);
const render=(content,facts)=>renderToStaticMarkup(React.createElement(mod.exports.default,{content,facts}));
const a='00000000-0000-4000-8000-000000000001',b='00000000-0000-4000-8000-000000000002';
function fixture(){
 const doc=sample('weekly').document,facts=doc.review_facts,content=doc.revision.content.review_content;
 facts.contract_version='work_report.review_facts.v2';
 facts.scope={contract_version:'review.scope.v2',kind:'team',label:'南区、北区',subject_ids:[a,b],subjects:[{scope_type:'team',id:a,label:'南区'},{scope_type:'team',id:b,label:'北区'}],resolved_member_ids:[a],scope_key:'review-scope:v2:'+'a'.repeat(64),scope_revision:'b'.repeat(64),membership_basis:'current_authorized_at_data_as_of'};
 facts.evidence[0].text=JSON.stringify({metric_code:'forecast_collection',scope:{scope_type:'team',record_id:a}});
 facts.evidence.push({...facts.evidence[0],source_ref:'review:north',text:JSON.stringify({metric_code:'forecast_collection',scope:{scope_type:'team',record_id:b}})});
 facts.measures.push({...facts.measures[0],measure_id:'opaque_north.forecast',source_refs:['review:north']});
 facts.groups[0].title='forecast_collection';facts.groups[0].source_refs.push('review:north');facts.groups[0].measure_refs.push('opaque_north.forecast');
 return {facts,content};
}
test('attribution uses each measure frozen query subject, not equal values, order, namespace or current account',()=>{
 const {facts}=fixture(),before=JSON.stringify(facts);
 assert.equal(measureSubjectLabel(facts.measures[0],facts),'团队 · 南区');
 assert.equal(measureSubjectLabel(facts.measures[2],facts),'团队 · 北区');
 const reordered={...facts,measures:[...facts.measures].reverse(),evidence:[...facts.evidence].reverse(),scope:{...facts.scope,subjects:[...facts.scope.subjects].reverse()}};
 assert.equal(measureSubjectLabel({...facts.measures[2],measure_id:'scope_south_forecast'},reordered),'团队 · 北区');
 assert.equal(JSON.stringify(facts),before);
 const person=clone(facts);person.scope.subjects[0].scope_type='person';person.scope.subjects[0].label='成员甲';person.evidence[0].text=JSON.stringify({metric_code:'forecast_collection',scope:{scope_type:'person',record_id:a}});
 assert.equal(measureSubjectLabel(person.measures[0],person),'成员 · 成员甲');
});
test('missing, conflicting, malformed, duplicate, wrong-metric and unlisted evidence never borrows another scope',()=>{
 const {facts}=fixture(),measure=facts.measures[0];
 for(const refs of [[],['review:target'],['review:absent'],['review:metric','review:north']])assert.equal(measureSubjectLabel({...measure,source_refs:refs},facts),'归属待核对');
 for(const text of ['not JSON','null',JSON.stringify({scope:{scope_type:'team',record_id:a}}),JSON.stringify({metric_code:'recognized',scope:{scope_type:'team',record_id:a}}),JSON.stringify({metric_code:'forecast_collection',scope:{scope_type:'team',record_id:'outside'}})]){
   const bad=clone(facts);bad.evidence[0].text=text;assert.equal(measureSubjectLabel(measure,bad),'归属待核对');
 }
 assert.equal(measureSubjectLabel(measure,{...facts,evidence:[...facts.evidence,facts.evidence[0]]}),'归属待核对');
 assert.equal(measureSubjectLabel(measure,{...facts,scope:{...facts.scope,subjects:[...facts.scope.subjects,facts.scope.subjects[0]]}}),'归属待核对');
 assert.equal(measureSubjectLabel(measure,{...facts,scope:{kind:'team',label:'不能据此猜归属'}}),'归属待核对');
});
test('weekly markup labels every metric with subject, translates code headings, and leaves frozen content unchanged',()=>{
 const {facts,content}=fixture(),before=JSON.stringify({facts,content});
 assert.equal(reviewContentProblem(content,facts),'');
 const html=render(content,facts);
 assert.match(html,/<h3>预测回款<\/h3>/);assert.doesNotMatch(html,/<h3>forecast_collection<\/h3>/);
 assert.match(html,/团队 · 南区/);assert.match(html,/团队 · 北区/);
 assert.equal((html.match(/class="rr-measure-subject"/g)||[]).length,3);
 assert.equal((html.match(/9,007,199,254,740,993.12345678/g)||[]).length,2);
 assert.equal(JSON.stringify({facts,content}),before);
 const kr=clone(facts);kr.groups[0].key_result_id=a;kr.groups[0].title='签下两家重点客户';
 assert.match(render(content,kr),/<h3>签下两家重点客户<\/h3>/);
});
test('missing reasons use Chinese labels or safe generic copy, preserving already Chinese explanations',()=>{
 for(const code of ['unknown_eligibility','currency_unavailable','missing_forecast_amount','task_alignment_missing','task_key_result_restricted','no_confirmed_progress','unknown_creation_date','no_actual_evidence']){
   assert.notEqual(missingLabel(code),code);assert.match(missingLabel(code),/[\u3400-\u9fff]/u);
 }
 assert.equal(missingLabel('new_unknown_code'),'部分材料暂不可用，请查看依据并核实');
 assert.equal(missingLabel('来源权限不覆盖所选范围'),'来源权限不覆盖所选范围');
 assert.equal(metricLabel('collection'),'实际回款');assert.equal(metricLabel('recognized'),'实际确收');assert.equal(metricLabel('unmapped'),'业务指标');
 const {facts,content}=fixture();content.missing_data.push('new_unknown_code','missing_forecast_amount');
 const html=render(content,facts);assert.doesNotMatch(html,/new_unknown_code|missing_forecast_amount/);assert.match(html,/部分商机缺少预测金额/);
});


test('frozen evidence renders exact subtotals and missing reasons without raw references, hash or JSON',()=>{
 const {facts}=fixture();
 const row={...facts.evidence[0],record_version:'a'.repeat(64),text:JSON.stringify({metric_code:'forecast_collection',value:null,known_subtotal:'1510000.00',coverage:'partial',missing_reasons:['unknown_eligibility'],period:{year:2026,quarter:4},scope:{scope_type:'team',record_id:a},forecast_id:'PRIVATE-REFERENCE'})};
 const before=JSON.stringify({facts,row});
 const html=renderToStaticMarkup(React.createElement(mod.exports.FrozenEvidence,{row,facts}));
 assert.match(html,/预测回款/);assert.match(html,/已知小计（非完整总额）/);assert.match(html,/151.00 万元/);assert.match(html,/数据未齐/);assert.match(html,/2026年 Q4/);assert.match(html,/南区/);assert.match(html,/回款信心或赢单概率/);
 assert.doesNotMatch(html,/PRIVATE-REFERENCE|forecast_collection|record_id|known_subtotal|review:metric|aaaaaaaaaaaaaaaa|<pre>/);
 assert.equal(JSON.stringify({facts,row}),before);
 const zero={...row,text:JSON.stringify({metric_code:'forecast_collection',value:'0.00',known_subtotal:'0.00',coverage:'complete',missing_reasons:[]})};
 assert.match(JSON.stringify(evidenceView(zero,facts)),/0.00 万元/);assert.doesNotMatch(JSON.stringify(evidenceView(zero,facts)),/已知小计/);
 const huge={...row,text:JSON.stringify({metric_code:'forecast_collection',value:'9007199254740993.12345678',coverage:'complete'})};
 assert.match(JSON.stringify(evidenceView(huge,facts)),/900,719,925,474.099312345678/);
 for(const text of ['{bad json',JSON.stringify({internal_id:'HIDDEN',hash:'HIDDEN'})])assert.doesNotMatch(JSON.stringify(evidenceView({...row,text},facts)),/bad json|HIDDEN/);
 const legacy=renderToStaticMarkup(React.createElement(mod.exports.FrozenEvidence,{row:{...row,text:'冻结说明 <script>unsafe()</script>'},facts}));
 assert.match(legacy,/&lt;script&gt;/);assert.doesNotMatch(legacy,/<script>/);
});
test('metric names are display aliases only; tokens, saved text and manual prose stay intact',()=>{
 const {facts}=fixture(),statement={text:'forecast_collection：{{metric:forecast_collection.forecast}}；collection_ppl 与 forecast_recognized。'},before=JSON.stringify(statement);
 assert.match(displayStatement(statement,facts),/预测回款：9007199254740993.12345678；回款 PPL 储备 与 预测确收/);
 assert.equal(JSON.stringify(statement),before);
 assert.equal(reportText('forecast_collection_suffix'),'forecast_collection_suffix');
 assert.equal(displayItem({text:'forecast_collection',kind:'fact'},[]).text,'预测回款');
 assert.equal(displayItem({text:'forecast_collection',kind:'manual'},[]).text,'forecast_collection');
 assert.equal(displayItem({text:'forecast_collection',kind:'user_supplement'},[]).text,'forecast_collection');
});
