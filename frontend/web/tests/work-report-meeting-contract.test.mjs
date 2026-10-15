import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {meetingView} from '../department-ui/work-report/meeting-presentation.js';
import {meetingOptions,meetingScopes,meetingSelection,meetingDraft,matchingMeetingSnapshot} from '../department-ui/work-report/meeting-selection.js';
import {createWorkReportApi} from '../department-ui/work-report/api.js';
const fixture=name=>JSON.parse(fs.readFileSync(new URL(`./fixtures/work-report-meeting/${name}.json`,import.meta.url),'utf8'));
const amount=(opening,change,closing)=>({opening,change,closing,currency:'CNY',unit:'元',known_subtotals:{opening,change,closing},coverage:{opening:'complete',change:'complete',closing:'complete'},missing_reasons:[]});

test('fixed Backend contract maps authoritative A/B totals and labels without summing rows',()=>{
  const facts=fixture('ready-snapshot.response').meeting_facts,before=JSON.stringify(facts),view=meetingView(facts);
  assert.equal(view.amounts.find(row=>row.id==='collection').change,'-200000.00');
  assert.equal(view.changes[0].money.collection.change,'-300000.00');assert.equal(view.changes[1].money.collection.change,'100000.00');
  assert.equal(view.changes[0].customerName,'示例客户A');assert.equal(view.quarterLabel,'2026 Q4');
  assert.equal(JSON.stringify(facts),before);
  facts.opportunity_changes=[];assert.equal(meetingView(facts).amounts[2].closing,'1000000.00');
});

test('team transfer expansion uses member money and member rows, never the team net zero',()=>{
  const facts=fixture('ready-snapshot.response').meeting_facts;
  const row=facts.opportunity_changes[0],person=facts.members[0];
  facts.amounts.quarter_collection=amount('1000000','0','1000000');row.amounts.quarter_collection=amount('1000000','0','1000000');
  person.amounts.quarter_collection=amount('1000000','-1000000','0');
  person.opportunity_changes=[{...structuredClone(row),amounts:{...row.amounts,quarter_collection:amount('1000000','-1000000','0')},reasons:[{code:'scope_transfer_out',text:'转交本团队其他成员',source_refs:row.source_refs}]}];
  const team=meetingView(facts),member=meetingView(facts,{memberId:person.member_id});
  assert.equal(team.amounts[2].change,'0');assert.equal(member.amounts[2].change,'-1000000');
  assert.equal(member.changes[0].money.collection.change,'-1000000');assert.equal(member.changes[0].kindLabel,'转出');assert.equal(member.members.length,0);
  assert.throws(()=>meetingView(facts,{memberId:'outside-member'}),/授权事实/);
});

test('incomplete member statistics never fall back to authoritative but unrelated team totals',()=>{
  for(const key of ['counts','amounts'])for(const absent of [undefined,null]){
    const facts=fixture('ready-snapshot.response').meeting_facts,person=facts.members[0];
    person[key]=absent;
    assert.throws(()=>meetingView(facts,{memberId:person.member_id}),/事实响应不完整/,key);
  }
});

test('unknown history, confirmed partial amounts and exact zero stay distinct',()=>{
  const facts=fixture('ready-snapshot.response').meeting_facts;
  facts.amounts.acv={opening:null,change:null,closing:'0.00',currency:'CNY',unit:'元',known_subtotals:{opening:'200000',change:null,closing:'0.00'},coverage:{opening:'partial',change:'unavailable',closing:'complete'},missing_reasons:['期初历史缺失']};
  facts.counts.new_claimed_customers={value:null,known_count:2,coverage:'partial',missing_reasons:['认领历史部分可用']};
  const view=meetingView(facts);assert.equal(view.amounts[0].opening,null);assert.equal(view.amounts[0].closing,'0.00');assert.match(view.amounts[0].reasons.join('；'),/已知小计 20 万元（非完整总额）/);
  assert.equal(view.counts[0].value,null);assert.match(view.counts[0].note,/已确认 2 家/);assert.ok(view.notices.includes('认领历史部分可用'));
  facts.amounts.acv.opening='0';assert.throws(()=>meetingView(facts),/事实响应不完整/);
});

test('actual followup time, period-only members and original plan date are preserved',()=>{
  const facts=fixture('ready-snapshot.response').meeting_facts,person=facts.members[0];
  person.membership_state='period_only';
  facts.formal_visits=[{id:'visit-a',name:'跟进',customer_id:'customer-a',customer_name:'实际跟进客户',occurred_at:'2026-10-06T08:00:00+08:00',member_ids:[person.member_id],summary:'实际拜访摘要',source_refs:[]}];
  facts.next_steps=[{id:'plan-a',opportunity_id:null,member_ids:[person.member_id],text:'确认付款时间',due_at:null,original_date_text:'本月底前',source_refs:[]}];
  person.formal_visit_ids=['visit-a'];person.next_step_ids=['plan-a'];
  const view=meetingView(facts,{memberId:person.member_id});
  assert.match(view.followups[0].atLabel,/2026\/10\/06/);assert.equal(view.followups[0].summary,'实际拜访摘要');assert.equal(view.nextSteps[0].dateLabel,'本月底前');
  assert.match(meetingView(facts).members[0].summary,/本期历史成员/);
});

test('member links select frozen item IDs but business navigation retains distinct customer and opportunity IDs',()=>{
  const facts=fixture('ready-snapshot.response').meeting_facts,person=facts.members[0];
  facts.new_claimed_customers=[{id:'frozen-claim-row',customer_id:'real-customer',name:'认领客户',occurred_at:'2026-10-06T08:00:00+08:00',member_ids:[person.member_id],summary:'',source_refs:[]}];
  facts.new_opportunities=[{id:'frozen-opportunity-row',customer_id:'real-customer',opportunity_id:'real-opportunity',name:'新增商机',occurred_at:null,member_ids:[person.member_id],summary:'',source_refs:[]}];
  person.new_claimed_customer_ids=['frozen-claim-row'];person.new_opportunity_ids=['frozen-opportunity-row'];
  const view=meetingView(facts,{memberId:person.member_id});
  assert.equal(view.customers[0].id,'frozen-claim-row');assert.equal(view.customers[0].customerId,'real-customer');
  assert.equal(view.opportunities[0].opportunityId,'real-opportunity');assert.notEqual(view.opportunities[0].id,'real-opportunity');
});

test('all five meeting periods use server templates and only self or managed teams enter generation',()=>{
  const options=meetingOptions(fixture('options.response'));
  for(const period of ['day','week','biweek','month','quarter']) {
    const request=meetingSelection({period,anchor:'2026-10-10',scopeKey:'self'},options);
    assert.equal(request.period.kind,period==='biweek'?'two_weeks':period);assert.deepEqual(request.scope,{kind:'self'});assert.equal(request.scope.member_ids,undefined);
  }
  assert.deepEqual(meetingSelection({period:'week',anchor:'2026-10-10',scopeKey:options.teams[0].team_id},options),fixture('create-snapshot.request'));
  const personal={...options,available_scope_kinds:['self']};assert.deepEqual(meetingScopes(personal),[{value:'self',label:'本人'}]);
  for(const scopeKey of [options.teams[0].team_id,'workspace','member'])assert.throws(()=>meetingSelection({period:'week',anchor:'2026-10-10',scopeKey},personal),/所管理的团队/);
  assert.throws(()=>meetingSelection({period:'day',anchor:'2026-02-30',scopeKey:'self'},options),/有效日期/);
});

test('save freezes the exact ready snapshot, matching default template, private intent and no client facts',()=>{
  const snapshot=fixture('ready-snapshot.response'),options=fixture('options.response');
  const intent=meetingDraft(snapshot,options);
  assert.equal(intent.snapshot_hash,snapshot.snapshot_hash);assert.equal(intent.snapshot_id,snapshot.id);assert.equal(intent.builtin_scheme_id,'meeting.week.v1');
  assert.deepEqual(Object.keys(intent).sort(),['builtin_scheme_id','snapshot_hash','snapshot_id','title']);
  assert.throws(()=>meetingDraft({...snapshot,status:'preparing'},options),/完整/);
  assert.throws(()=>meetingDraft(snapshot,{...options,can_save_draft:false}),/暂不可保存/);
  const request=fixture('create-snapshot.request');assert.equal(matchingMeetingSnapshot(snapshot,request),snapshot);
  assert.throws(()=>matchingMeetingSnapshot(snapshot,{...request,scope:{kind:'self'}}),/范围不一致/);
  const tampered=structuredClone(snapshot);tampered.meeting_facts.period.label='other';assert.throws(()=>matchingMeetingSnapshot(tampered,request),/快照与事实不一致/);
  const wider=structuredClone(snapshot);wider.scope.team_id='other-team';wider.meeting_facts.scope.team_id='other-team';assert.throws(()=>matchingMeetingSnapshot(wider,request),/解析范围/);
});

test('meeting routes reuse fresh auth, mutation receipts and reject late account results',async()=>{
  const requests=[];let account='owner-a',finish;
  const service=createWorkReportApi({getIdentity:()=>account,supportsMutation:()=>true,getClient:()=>({request:async request=>{requests.push(request);return {};}})});
  await service.meetingOptions();await service.prepareMeeting(fixture('create-snapshot.request'));await service.meetingSnapshot('snapshot');await service.saveMeeting(fixture('save-draft.request'));
  assert.deepEqual(requests.map(row=>[row.method,row.path]),[['GET','/work-reports/meeting/options'],['POST','/work-reports/meeting/snapshots'],['GET','/work-reports/meeting/snapshots/snapshot'],['POST','/work-reports/meeting/documents']]);
  assert.ok(requests.every(row=>row.fresh));
  const slow=createWorkReportApi({getIdentity:()=>account,getClient:()=>({request:()=>new Promise(resolve=>{finish=resolve;})})});
  const pending=slow.meetingSnapshot('snapshot');account='owner-b';finish(fixture('ready-snapshot.response'));await assert.rejects(pending,{code:'REPORT_IDENTITY_CHANGED'});
});
