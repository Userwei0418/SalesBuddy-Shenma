import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {generationPlan,reviewContentProblem,measureText,displayStatement,statementEvidence,matchingSnapshot,matchingDocument,exactRevision} from '../department-ui/review/report/model.js';
import {referenceKey,readReference,saveReference} from '../department-ui/review/report/recovery.js';
import {createWorkReportApi} from '../department-ui/work-report/api.js';
import {ids,selection,options,sample,clone,memoryStore} from './review-report-fixtures.mjs';

test('generation scopes come from report options, not wider readable Review scope',()=>{
 const plan=generationPlan(selection,'daily',options,ids.actor);assert.deepEqual(plan,{selection:sample().snapshot.selection,scheme:{scheme_id:ids.scheme,version_no:2}});
 assert.deepEqual(generationPlan({...selection,scope:'member',member_id:ids.actor},'daily',options,ids.actor),plan);
 for(const invalid of [{scope:'member',member_id:'another'},{scope:'workspace'},{scope:'team',team_id:'visible-but-not-managed'}])assert.throws(()=>generationPlan({...selection,...invalid},'daily',options,ids.actor),/只能/);
 assert.equal(generationPlan({...selection,scope:'team',team_id:ids.team},'daily',options,ids.actor).selection.scope.team_id,ids.team);
 assert.throws(()=>generationPlan(selection,'daily',{...options,can_create:false},ids.actor),/生成权限/);
 assert.throws(()=>generationPlan(selection,'daily',{...options,generation_available:false},ids.actor),/暂不可用/);
 assert.throws(()=>generationPlan({...selection,anchor_date:'2026-02-30'},'daily',options,ids.actor),/有效日期/);
 assert.throws(()=>generationPlan({...selection,anchor_date:'2099-01-01'},'daily',options,ids.actor),/有效日期/);
 const weekly=generationPlan({...selection,period_type:'week'},'weekly',options,ids.actor);assert.equal(weekly.scheme.scheme_id,ids.weeklyScheme);assert.deepEqual(weekly.selection.sources,['visit']);assert.equal(weekly.selection.facts_contract,undefined);assert.equal(weekly.selection.overview_binding,undefined);
 assert.throws(()=>generationPlan(selection,'weekly',options,ids.actor),/自然周/);
});
test('v2 report generation preserves every selected subject and the review revision',()=>{
 const other='00000000-0000-4000-8000-000000000010',revision='b'.repeat(64);
 const selected={scope:'team',period_type:'day',anchor_date:'2026-01-14',team_ids:[other,ids.team],expected_scope_revision:revision};
 const scopedOptions={...options,available_scope_kinds:['self','team','member'],teams:[{id:ids.team,name:'实际管理团队'},{id:other,name:'另一个管理团队'}],members:[{id:ids.actor,name:'成员甲'}]};
 const plan=generationPlan(selected,'daily',scopedOptions,ids.actor);
 assert.deepEqual(plan.selection.scope,{contract_version:'work_report.scope_selection.v2',kind:'team',team_ids:[ids.team,other],expected_scope_revision:revision});
 const memberPlan=generationPlan({scope:'member',period_type:'day',anchor_date:'2026-01-14',member_ids:[other,ids.actor],expected_scope_revision:revision},'daily',{...scopedOptions,members:[{id:ids.actor,name:'成员甲'},{id:other,name:'成员乙'}]},ids.actor);
 assert.deepEqual(memberPlan.selection.scope,{contract_version:'work_report.scope_selection.v2',kind:'member',member_ids:[ids.actor,other],expected_scope_revision:revision});
 const row=sample(),scope={contract_version:'review.scope.v2',kind:'team',label:'两个管理团队',subject_ids:[ids.team,other],subjects:[{scope_type:'team',id:ids.team,label:'实际管理团队'},{scope_type:'team',id:other,label:'另一个管理团队'}],resolved_member_ids:[ids.actor],scope_key:`review-scope:v2:${revision}`,scope_revision:revision,membership_basis:'current_authorized_at_data_as_of'};
 const snapshot={...row.snapshot,selection:{...plan.selection,scope:plan.selection.scope},scope,review_facts:{...row.snapshot.review_facts,contract_version:'work_report.review_facts.v2',scope}};
 assert.equal(matchingSnapshot(snapshot,plan),snapshot);
 const wire={...snapshot,selection:{...snapshot.selection,scope:{...plan.selection.scope,member_ids:[]}}};
 assert.equal(matchingSnapshot(wire,plan),wire);
 const reordered={...wire,selection:{...wire.selection,scope:{...wire.selection.scope,team_ids:[other,ids.team]}}};
 assert.equal(matchingSnapshot(reordered,plan),reordered);
 for(const patch of [{expected_scope_revision:'c'.repeat(64)},{member_ids:[ids.actor]},{team_ids:[ids.team]},{kind:'member'}]){
   assert.throws(()=>matchingSnapshot({...wire,selection:{...wire.selection,scope:{...wire.selection.scope,...patch}}},plan));
 }
 const memberWire={id:ids.snapshot,status:'preparing',selection:{...memberPlan.selection,scope:{...memberPlan.selection.scope,team_ids:[]}}};
 assert.equal(matchingSnapshot(memberWire,memberPlan),memberWire);
 assert.throws(()=>matchingSnapshot({...snapshot,scope:{...scope,scope_revision:'d'.repeat(64)},review_facts:{...snapshot.review_facts,scope:{...scope,scope_revision:'d'.repeat(64)}}},plan),/冻结范围/);
 const document={...row.document,snapshot_id:snapshot.id,scope,review_facts:snapshot.review_facts};
 assert.equal(matchingDocument(document,{mode:'daily',snapshotId:snapshot.id,documentId:document.id,plan}),document);
 assert.throws(()=>generationPlan({...selected,expected_scope_revision:'bad'},'daily',scopedOptions,ids.actor),/范围版本/);
 assert.throws(()=>generationPlan({...selected,team_ids:[ids.team],member_ids:[ids.actor]},'daily',scopedOptions,ids.actor),/同类成员或团队/);
 assert.throws(()=>matchingDocument({...document,review_facts:{...document.review_facts,contract_version:'work_report.review_facts.v1'}},{mode:'daily',snapshotId:snapshot.id,documentId:document.id,plan}),/冻结(范围|事实)/);
});
test('typed daily supports empty ordinary sections, max three and exact frozen references',()=>{
 const {document}=sample(),typed=document.revision.content.review_content,facts=document.review_facts;
 assert.equal(reviewContentProblem(typed,facts),'');assert.deepEqual(document.revision.content.sections[0].items,[]);
 assert.equal(displayStatement(typed.items[0].fact,facts),'冻结预计回款 9007199254740993.12345678 元。');
 assert.equal(measureText(facts.measures[0],{withUnit:true}),'9,007,199,254,740,993.12345678 元');
 assert.equal(displayStatement(typed.items[0].impact_or_risk,facts),'日变化：无可信历史基线。');
 assert.match(reviewContentProblem({...typed,items:Array(4).fill(typed.items[0])},facts),/1–3/);
 assert.match(reviewContentProblem({...typed,items:[null]},facts),/确认/);
 let changed=clone(typed);changed.items[0].fact.measure_refs=[];assert.match(reviewContentProblem(changed,facts),/指标引用/);
 changed=clone(typed);changed.items[0].fact.source_refs=['review:hidden'];assert.match(reviewContentProblem(changed,facts),/不属于/);
 assert.deepEqual(statementEvidence(typed.items[0].fact,facts).evidence.map(row=>row.source_ref),['review:metric']);
});
test('unknown, partial, pending targets and no baseline never become zero or financial contributions',()=>{
 const {document}=sample(),typed=document.revision.content.review_content,facts=document.review_facts;
 assert.equal(measureText({...facts.measures[0],value:'0'}),'0');
 assert.equal(measureText({...facts.measures[0],coverage:'partial',value:null}),'材料不完整');
 assert.equal(measureText({...facts.measures[0],coverage:'unavailable',value:null}),'暂不可用');
 const invalid=clone(facts);invalid.measures[1].value='0';invalid.measures[1].coverage='complete';invalid.measures[1].missing_reasons=[];assert.match(reviewContentProblem(typed,invalid),/历史基线/);
 invalid.measures[1].baseline={boundary_at:invalid.measures[1].comparison_boundary,definition_version:99,continuous_coverage:true,same_authorized_population:true};assert.match(reviewContentProblem(typed,invalid),/历史基线/);
 invalid.measures[0].coverage='partial';assert.match(reviewContentProblem(typed,invalid),/不完整统计/);
 const weekly=sample('weekly').document;assert.equal(reviewContentProblem(weekly.revision.content.review_content,weekly.review_facts),'');
 const risk=weekly.revision.content.review_content.groups[0].risks[0];assert.equal(statementEvidence(risk,weekly.review_facts).evidence[0].target_status,'pending');
 assert.equal(measureText({...facts.measures[0],value:'0.01234000',role:'rate'},{withUnit:true}),'1.2%（按比例换算）');
});
test('snapshot and document reads match IDs, frozen mode/scope and server hash; saves preserve ordinary and typed content',()=>{
 const row=sample(),plan=generationPlan(selection,'daily',options,ids.actor),reference={mode:'daily',snapshotId:ids.snapshot,documentId:ids.document};
 assert.equal(matchingSnapshot(row.snapshot,plan),row.snapshot);assert.equal(matchingDocument(row.document,reference),row.document);
 assert.throws(()=>matchingSnapshot({...row.snapshot,snapshot_hash:null},plan),/完整准备/);
 assert.throws(()=>matchingSnapshot({...row.snapshot,selection:{...row.snapshot.selection,scope:{kind:'team'}}},plan),/范围/);
 assert.throws(()=>matchingDocument(row.document,{...reference,snapshotId:'other'}),/不一致/);
 assert.throws(()=>matchingDocument(row.document,{...reference,mode:'weekly'}),/类型/);
 const weekly=sample('weekly').document,payload=exactRevision(weekly,'新标题');assert.equal(payload.version_no,2);assert.equal(payload.content.title,'新标题');assert.deepEqual(payload.content.review_content,weekly.revision.content.review_content);assert.deepEqual(payload.content.sections,weekly.revision.content.sections);
 const old={...row.document,revision:{...row.document.revision,content:{title:'旧六节',sections:Array.from({length:6},(_,i)=>({id:String(i),items:[]})),missing_data:[]}}};delete old.review_facts;assert.equal(matchingDocument(old,reference),old);assert.equal(exactRevision(old,'旧六节修订').content.sections.length,6);
});
test('reentry stores bounded identity/scope routing metadata, never body facts, and expires stale references',()=>{
 const store=memoryStore(),key=referenceKey('actor',selection,'daily'),plan=generationPlan(selection,'daily',options,ids.actor),row={mode:'daily',plan,snapshotId:ids.snapshot,runId:ids.run,documentId:ids.document,review_facts:sample().document.review_facts};
 saveReference(key,'actor',row,store,100);assert.equal(readReference(key,'actor',store,101).runId,ids.run);assert.equal(readReference(key,'other',store,101),null);assert.equal(store.getItem(key).includes('9007199254740993'),false);assert.equal(store.getItem(key).includes('review_facts'),false);
 assert.notEqual(key,referenceKey('actor',{...selection,scope:'team',team_id:ids.team},'daily'));
 assert.equal(readReference(key,'actor',store,100+7*86400000),null);assert.equal(store.length,0);
 for(let i=0;i<35;i++)saveReference(referenceKey('actor',{...selection,team_id:String(i)},'daily'),'actor',row,store,100);assert.equal(store.length,32);
});
test('original work-report API + original mutation receipts protect identical uncertain retries',async()=>{
 const saved=new Map(),module={exports:{}};vm.runInNewContext(readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8'),{module,Math,wx:{getStorageSync:key=>saved.get(key),setStorageSync:(key,value)=>saved.set(key,value)}});
 const receipts=module.exports,calls=[];let owner='same-session',lost=true;
 const api=createWorkReportApi({getIdentity:()=>owner,supportsMutation:receipts.supports,getClient:()=>({request:async options=>{const receipt=receipts.begin(owner,options);calls.push({...options,key:receipt.key});if(lost){lost=false;throw Error('lost response');}receipts.finish(receipt);return sample().snapshot;}})});
 const body=generationPlan(selection,'daily',options,ids.actor).selection;
 await assert.rejects(()=>api.prepare(body));await api.prepare(clone(body));assert.equal(calls[0].key,calls[1].key);assert.deepEqual(calls[0].data,calls[1].data);assert.equal(calls[0].path,'/work-reports/snapshots');assert.equal(calls[0].data.review_facts,undefined);
 await api.prepare(body);assert.notEqual(calls[2].key,calls[1].key);
});
