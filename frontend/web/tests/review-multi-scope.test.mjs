import {test} from 'node:test';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {REVIEW_METRICS,REVIEW_ACTIVITIES,normalizeReviewSelection,reviewSelection,selectionKey,detailSelectionKey,bindReviewScope,matchingReview,matchingPage} from '../department-ui/review/overview-model.js';
import {createReviewOverviewApi} from '../department-ui/review/overview-api.js';

const A='20000000-0000-4000-8000-000000000001',B='20000000-0000-4000-8000-000000000002';
const U='30000000-0000-4000-8000-000000000001',V='30000000-0000-4000-8000-000000000002';
const T='40000000-0000-4000-8000-000000000001',REV='a'.repeat(64),OTHER_REV='b'.repeat(64);
const selection={scope:'team',team_ids:[A,B],anchor_date:'2026-10-14',period_type:'day'};
const query={activity:'formal_visits',limit:20,offset:0};
function envelope(request=selection) {
  const selected=normalizeReviewSelection(request),ids=selected.team_ids.length?selected.team_ids:selected.member_ids;
  return {selection:selected,scope:{contract_version:'review.scope.v2',kind:selected.scope,label:'已选范围',subject_ids:ids,subjects:ids.map(id=>({scope_type:selected.scope==='team'?'team':'person',id,label:'授权主体'})),resolved_member_ids:selected.scope==='team'?[U,V]:ids,scope_key:'review-scope:v2:'+'c'.repeat(64),scope_revision:REV,membership_basis:'current_authorized_at_data_as_of'},period:{label:'2026-10-14'},data_as_of:'2026-10-14T10:00:00Z'};
}
function overview(request=selection) {
  const base=envelope(request);
  return {...base,contract_version:'review.overview.v2',quarter:{quarter:4,time_progress:'0.15'},members:[],generation:{available:false,reason_code:'review_multi_scope_generation_not_ready'},metrics:REVIEW_METRICS.map(({code})=>({metric_code:code,value:'0.00',known_subtotal:'0.00',coverage:'complete',rate:null,gap:null,day_change:null,baseline:{status:'no_baseline'},target:{kind:code,id:null,version_no:null,status:'no_target',value:null,known_subtotal:null,coverage:'unavailable',aggregation:'sum_distinct_subject_targets',components:base.scope.subject_ids.map(id=>({scope_type:base.scope.kind==='team'?'team':'person',subject_id:id,target_id:null,version_no:null,status:'no_target',value:null}))}})),activities:REVIEW_ACTIVITIES.map(({code})=>({code,count:0,known_count:0,coverage:'complete'}))};
}
const page=(request=selection,filters=query)=>({...envelope(request),contract_version:'review.page.v2',...filters,items:[],total:0,known_total:0,coverage:'complete',has_more:false});
function effective(component,value='100.00') {return {...component,target_id:T,version_no:1,status:'effective',value};}
function targetPartial(value) {
  const target=value.metrics[0].target;
  target.components[0]=effective(target.components[0]);
  Object.assign(target,{known_subtotal:'100.00',coverage:'partial'});
  return value;
}
function apiFor(respond) {const calls=[];return {calls,api:createReviewOverviewApi({getIdentity:()=> 'actor:permission-v1',getClient:()=>({request:async options=>{calls.push(options);return respond(options);}})})};}

test('multi selection sorts and deduplicates without collapsing a one-element array to legacy single',()=>{
  const input={period:{kind:'day',anchor_date:'2026-10-14'},scope:{kind:'team',team_ids:[B,A,A]}};
  const result=reviewSelection(input);
  assert.deepEqual(result,{...selection,team_ids:[A,B],member_ids:[]});
  assert.deepEqual(input.scope.team_ids,[B,A,A]);
  assert.equal(selectionKey(result),selectionKey({...selection,team_ids:[B,A]}));
  assert.notEqual(selectionKey({...selection,team_ids:[A]}),selectionKey({scope:'team',team_id:A,anchor_date:selection.anchor_date,period_type:'day'}));
  assert.deepEqual(reviewSelection({period:input.period,scope:{kind:'self'}}),{scope:'self',anchor_date:selection.anchor_date,period_type:'day'});
});
test('scope inputs reject mixed kinds, aliases, empties, invalid IDs, authority fields and over-100 raw entries',()=>{
  for(const request of [
    {...selection,member_ids:[U]}, {...selection,team_id:A}, {...selection,member_id:U},
    {...selection,team_ids:[]}, {...selection,team_ids:'a,b'}, {...selection,team_ids:['not-a-uuid']},
    {...selection,team_ids:Array(101).fill(A)}, {...selection,scope_key:'pretend-authority'},
    {...selection,scope:'self'}, {...selection,scope:'member'},
    {...selection,expected_scope_revision:'invalid'},
    {scope:'team',team_id:A,member_ids:[],anchor_date:selection.anchor_date,period_type:'day'},
  ]) assert.throws(()=>normalizeReviewSelection(request));
  assert.deepEqual(normalizeReviewSelection({...selection,team_ids:Array(100).fill(A)}).team_ids,[A]);
});
test('scope revision changes detail identity only and bind never invents an opaque key',()=>{
  const value=overview(),bound=bindReviewScope(selection,value);
  assert.equal(bound.expected_scope_revision,REV);
  assert.equal(selectionKey(bound),selectionKey(selection));
  assert.notEqual(detailSelectionKey(bound),detailSelectionKey({...bound,expected_scope_revision:OTHER_REV}));
  assert.equal(Object.hasOwn(bound,'scope_key'),false);
  assert.equal(Object.hasOwn(selection,'expected_scope_revision'),false);
  const changed=page(bound);changed.scope.scope_revision=OTHER_REV;
  assert.throws(()=>matchingPage(changed,bound,query),error=>error.statusCode===409&&error.code==='WORK_REPORT_SCOPE_CHANGED'&&error.serverCode==='WORK_REPORT_SCOPE_CHANGED');
});
test('arrays including one subject require v2 envelope; legacy self and single remain v1',()=>{
  for(const request of [selection,{...selection,team_ids:[A]},{scope:'member',member_ids:[U],anchor_date:selection.anchor_date,period_type:'day'}]){
    assert.equal(matchingReview(overview(request),request).contract_version,'review.overview.v2');
    assert.equal(matchingPage(page(request),request,query).contract_version,'review.page.v2');
    const old=overview(request);old.contract_version='review.overview.v1';assert.throws(()=>matchingReview(old,request),/概览数据/);
  }
  for(const request of [{scope:'self',anchor_date:selection.anchor_date,period_type:'day'},{scope:'team',team_id:A,anchor_date:selection.anchor_date,period_type:'day'}]){
    const value=overview();Object.assign(value,{selection:request,scope:{kind:request.scope,team_id:request.team_id,label:'旧范围'},contract_version:'review.overview.v1'});
    value.metrics.forEach(row=>{row.target={status:'no_target',value:null};});
    assert.equal(matchingReview(value,request),value);
    assert.deepEqual(bindReviewScope(request,value),request);
    value.contract_version='review.overview.v2';assert.throws(()=>matchingReview(value,request),/概览数据/);
  }
});
test('every v2 scope field is validated without inferring membership from selection IDs',()=>{
  const mutations=[
    value=>value.selection.team_ids=[A],value=>value.scope.contract_version='review.scope.v1',value=>value.scope.kind='member',
    value=>value.scope.subject_ids=[B,A],value=>value.scope.subject_ids=[A,A],value=>value.scope.subjects.pop(),
    value=>value.scope.subjects[0].id=B,value=>value.scope.subjects[0].scope_type='person',value=>delete value.scope.subjects[0].label,
    value=>value.scope.resolved_member_ids=[U,U],value=>value.scope.resolved_member_ids=[V,U],value=>value.scope.resolved_member_ids=['bad'],
    value=>value.scope.scope_key='made-up-key',value=>value.scope.scope_revision='old',value=>value.scope.membership_basis='inferred_local',
  ];
  for(const mutate of mutations){const value=overview();mutate(value);assert.throws(()=>matchingReview(value,selection));}
  const emptyTeam=overview();emptyTeam.scope.resolved_member_ids=[];assert.equal(matchingReview(emptyTeam,selection),emptyTeam);
  const memberSelection={scope:'member',member_ids:[U,V],anchor_date:selection.anchor_date,period_type:'day'};
  const memberValue=overview(memberSelection);memberValue.scope.resolved_member_ids=[U];assert.throws(()=>matchingReview(memberValue,memberSelection),/成员范围/);
});
test('partial target preserves known subtotal and components but prohibits aggregate value, rate or gap',()=>{
  const value=targetPartial(overview());assert.equal(matchingReview(value,selection).metrics[0].target.known_subtotal,'100.00');
  assert.equal(value.metrics[0].target.value,null);
  for(const mutate of [row=>row.target.value='100.00',row=>row.rate='0.10',row=>row.gap='100.00',row=>row.target.status='effective',row=>row.target.coverage='complete',row=>row.target.known_subtotal=100]){
    const invalid=targetPartial(overview());mutate(invalid.metrics[0]);assert.throws(()=>matchingReview(invalid,selection));
  }
  const denied=targetPartial(overview());denied.metrics[0].target.components[1].status='permission_denied';denied.metrics[0].target.status='permission_denied';assert.equal(matchingReview(denied,selection),denied);
  denied.metrics[0].target.components[1].target_id=T;assert.throws(()=>matchingReview(denied,selection),/可见值/);
});
test('target components must cover exact distinct subjects and carry supported aggregation and metadata',()=>{
  for(const mutate of [target=>target.components.pop(),target=>target.components[1].subject_id=A,target=>target.components[0].scope_type='person',target=>target.components[0].subject_id=U,target=>target.aggregation='average',target=>target.id=T,target=>delete target.known_subtotal,target=>target.components[0].value='10.00',target=>target.components[0].version_no=-1]){
    const value=overview();mutate(value.metrics[0].target);assert.throws(()=>matchingReview(value,selection));
  }
  const single={...selection,team_ids:[A]},value=overview(single),target=value.metrics[0].target;
  target.components[0]=effective(target.components[0],'9007199254740993.21');
  Object.assign(target,{id:T,version_no:1,status:'effective',value:'9007199254740993.21',known_subtotal:'9007199254740993.21',coverage:'complete'});
  assert.equal(matchingReview(value,single).metrics[0].target.value,'9007199254740993.21');
  target.version_no=2;assert.throws(()=>matchingReview(value,single),/标识/);
});
test('explicit zero target stays zero but has no derived rate or gap; missing target disallows either in v1 too',()=>{
  const value=overview(),target=value.metrics[0].target;
  target.components=target.components.map(component=>effective(component,'0.00'));
  Object.assign(target,{status:'effective',value:'0.00',known_subtotal:'0.00',coverage:'complete'});
  assert.equal(matchingReview(value,selection).metrics[0].target.value,'0.00');
  value.metrics[0].rate='0.00';assert.throws(()=>matchingReview(value,selection),/达成率/);
  const legacy={scope:'self',anchor_date:selection.anchor_date,period_type:'day'},missing=overview();
  Object.assign(missing,{selection:legacy,scope:{kind:'self'},contract_version:'review.overview.v1'});missing.metrics[0].gap='0.00';
  assert.throws(()=>matchingReview(missing,legacy),/达成率/);
});
test('v2 still requires all three financial metrics and all activity categories',()=>{
  const value=overview();value.metrics.pop();assert.throws(()=>matchingReview(value,selection),/指标不完整/);
  const activities=overview();activities.activities.pop();assert.throws(()=>matchingReview(activities,selection),/动作数据/);
});
test('GET sends sorted repeated query keys, retains all IDs and passes the bound revision on both detail APIs',async()=>{
  const {calls,api}=apiFor(options=>options.path.startsWith('/review/overview?')?overview():page());
  const value=await api.overview({...selection,team_ids:[B,A,A]});
  let params=new URL(calls[0].path,'https://unit.invalid').searchParams;
  assert.deepEqual(params.getAll('team_ids'),[A,B]);assert.equal(params.has('team_id'),false);assert.equal(params.has('member_ids'),false);
  const bound=bindReviewScope(selection,value);
  await api.activities(bound,query);await api.opportunities(bound,query);
  for(const call of calls.slice(1)){
    params=new URL(call.path,'https://unit.invalid').searchParams;assert.equal(params.get('expected_scope_revision'),REV);assert.deepEqual(params.getAll('team_ids'),[A,B]);assert.equal(call.method,'GET');assert.equal(call.fresh,true);
  }
});
test('member arrays use repeated member_ids and invalid or mixed input makes zero requests',async()=>{
  const selected={scope:'member',member_ids:[V,U],anchor_date:selection.anchor_date,period_type:'day'};
  const {calls,api}=apiFor(()=>overview(selected));await api.overview(selected);
  assert.deepEqual(new URL(calls[0].path,'https://unit.invalid').searchParams.getAll('member_ids'),[U,V]);
  await assert.rejects(api.overview({...selected,team_ids:[A]}));
  await assert.rejects(api.activities(selected,{...query,member_id:U}));
  assert.equal(calls.length,1);
});
test('403/409 preserve codes but never leak server text or silently retry a smaller scope',async()=>{
  for(const statusCode of [403,409]){
    const code=statusCode===409?'WORK_REPORT_SCOPE_CHANGED':'TARGET_SCOPE_FORBIDDEN';
    const {calls,api}=apiFor(()=>{throw Object.assign(Error('private subject name and policy internals'),{statusCode,code,serverCode:code,details:{private:'subject'}});});
    await assert.rejects(api.overview(selection),error=>{assert.equal(error.statusCode,statusCode);assert.equal(error.code,code);assert.equal(error.serverCode,code);assert.equal(error.message.includes('private'),false);assert.equal(Object.hasOwn(error,'details'),false);return true;});
    assert.equal(calls.length,1);assert.deepEqual(new URL(calls[0].path,'https://unit.invalid').searchParams.getAll('team_ids'),[A,B]);
  }
});
test('a late response after actor or permission identity changes cannot establish scope authority',async()=>{
  let owner='a',finish;const api=createReviewOverviewApi({getIdentity:()=>owner,getClient:()=>({request:()=>new Promise(resolve=>finish=resolve)})});
  const pending=api.overview(selection);owner='a:new-permission-revision';finish(overview());await assert.rejects(pending,/身份或权限/);
});

test('frozen actual PostgreSQL HTTP samples pass unchanged through Web response validators',{skip:!process.env.REVIEW_MULTI_SCOPE_PG_SAMPLES},async()=>{
  const bytes=await readFile(process.env.REVIEW_MULTI_SCOPE_PG_SAMPLES);
  assert.equal(createHash('sha256').update(bytes).digest('hex'),'80b180196a4ffab89b06130e189cbc2fdfa7c4f8ba3708ee2bef0abb0a0b3759');
  const {samples}=JSON.parse(bytes);let accepted=0;
  for(const [name,value] of Object.entries(samples)) {
    if(value.contract_version?.startsWith('review.overview.')) {
      assert.equal(matchingReview(value,value.selection),value,name);accepted++;
    } else if(value.contract_version?.startsWith('review.page.')) {
      const filters=Object.fromEntries(['activity','filter','sort','metric_code','limit','offset'].filter(key=>value[key]!=null).map(key=>[key,value[key]]));
      assert.equal(matchingPage(value,value.selection,filters),value,name);accepted++;
    }
  }
  assert.equal(accepted,16);
  assert.equal(samples.teams_overview.metrics[0].target.value,'400.00');
  assert.equal(samples.teams_overview.metrics[0].rate,'0.25000000');
  assert.equal(samples.legacy_single.metrics[0].gap,'0.00');
  assert.equal(samples.missing_target.metrics.find(row=>row.metric_code==='collection_ppl').rate,null);
  assert.equal(samples.target_denied.metrics[0].gap,null);
  const self=samples.self_v2,bound=bindReviewScope(self.selection,self);
  assert.equal(bound.expected_scope_revision,self.scope.scope_revision);
  assert.equal(selectionKey(bound),selectionKey({scope:'self',anchor_date:self.selection.anchor_date,period_type:self.selection.period_type}));
  assert.equal(Object.hasOwn(bound,'member_ids'),false);
  const {calls,api}=apiFor(()=>self);await api.overview(bound);
  const params=new URL(calls[0].path,'https://unit.invalid').searchParams;
  assert.equal(params.get('scope'),'self');assert.equal(params.get('expected_scope_revision'),self.scope.scope_revision);
  assert.equal(params.has('member_ids'),false);assert.equal(params.has('team_ids'),false);
  const changed=structuredClone(samples.formal_visits);
  assert.throws(()=>matchingPage(changed,{...changed.selection,expected_scope_revision:OTHER_REV},{activity:changed.activity,limit:changed.limit,offset:changed.offset}),error=>error.code==='WORK_REPORT_SCOPE_CHANGED');
});
