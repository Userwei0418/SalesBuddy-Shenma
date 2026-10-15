import {test} from 'node:test';
import assert from 'node:assert/strict';
import {canConfirmProgress, editDraft, keyResultBody, mentionBody, objectiveBody, parentChoices, periodFor, progressBody, progressDraft, progressText, targetChoices} from '../department-ui/review/targets/okr/model.js';

const people=[{id:'alice',name:'甲'},{id:'bob',name:'乙'}];
const objective={id:'o1',title:'团队目标',editable:true,status:'active',period_type:'quarter',period_start:'2026-10-01',period_end:'2026-12-31',scope_type:'team',team_id:'south',user_ref_id:null,department_code:'sales'};
const metric={code:'forecast_recognized',name:'预计确收',target_kind:'forecast_recognized',source_kind:'forecast',unit:'元',active:true,decimal_places:2};
const participants={title:'目标',owner_user_id:'alice',collaborator_user_ids:['bob','bob','alice']};

test('objective captures a single scoped identity and exact version; participants are authorized',()=>{
  const body=objectiveBody({...participants,scope_type:'team',subjectId:'south',period_type:'quarter',anchor_date:'2026-10-14'}, {people,options:{teams:[{id:'south'}]}});
  assert.deepEqual(body,{title:'目标',owner_user_id:'alice',collaborator_user_ids:['bob'],parent_id:null,scope_type:'team',team_id:'south',period_type:'quarter',anchor_date:'2026-10-14'});
  assert.throws(()=>objectiveBody({...participants,owner_user_id:'outsider',scope_type:'team',subjectId:'south'}, {people,options:{teams:[{id:'south'}]}}),/负责人/);
  const edited=objectiveBody({...objective,...participants,version_no:7},{people,options:{}});
  assert.equal(edited.version_no,7);assert.equal(Object.hasOwn(edited,'scope_type'),false);assert.equal(Object.hasOwn(edited,'period_start'),false);
});
test('period coverage and graph ancestry constrain parent selection',()=>{
  assert.deepEqual(periodFor('week','2026-10-14'),{type:'week',start:'2026-10-12',end:'2026-10-18'});
  assert.deepEqual(periodFor('month','2024-02-29'),{type:'month',start:'2024-02-01',end:'2024-02-29'});
  assert.throws(()=>periodFor('month','2026-02-30'));
  const rows=[{id:'self',status:'active',period_start:'2026-10-01',period_end:'2026-12-31'},{id:'child',parent_id:'self',status:'active',period_start:'2026-01-01',period_end:'2026-12-31'},{id:'parent',status:'active',period_start:'2026-01-01',period_end:'2026-12-31'},{id:'short',status:'active',period_start:'2026-10-01',period_end:'2026-10-31'}];
  assert.deepEqual(parentChoices(rows,'self',{start:'2026-10-01',end:'2026-12-31'}).map(row=>row.id),['parent']);
});
test('KR references only matching effective target identity, never pending or another tax basis',()=>{
  const target={...objective,id:'t1',kind:metric.target_kind,effective_amount:'9007199254740991.29'};
  const rows=[target,{...target,id:null,effective_amount:null,state:'pending_new',pending_change:{proposed_amount:'100.00'}},{...target,id:'old-tax',kind:'recognized'},{...target,id:'foreign-team',team_id:'north'},{...target,id:'other-month',period_type:'month'}];
  assert.deepEqual(targetChoices(rows,objective,metric).map(row=>row.id),['t1']);
  const body=keyResultBody({...participants,metric_code:metric.code,target_id:'t1'}, {people,objective,metrics:[metric],targets:rows});
  assert.equal(body.target_id,'t1');assert.equal(Object.hasOwn(body,'amount'),false);
  assert.throws(()=>keyResultBody({...participants,metric_code:metric.code,target_id:'old-tax'},{people,objective,metrics:[metric],targets:rows}),/生效目标/);
});
test('manual progress preserves zero and decimal strings and requires evidence plus explicit confirmation',()=>{
  const row={version_no:9,can_confirm_progress:true,progress_mode:'manual',metric:{source_kind:'manual',unit:'次',decimal_places:0}};
  assert.equal(progressDraft({...row,progress:{value:'2.00'}}).value,'2');
  assert.deepEqual(progressBody({value:'0',evidence:'本期尚未执行，已核对',confirmed:true},row),{version_no:9,value:'0',evidence:'本期尚未执行，已核对',confirmed:true});
  assert.throws(()=>progressBody({value:'1.1',evidence:'确认',confirmed:true},row),/整数/);
  assert.throws(()=>progressBody({value:'1',evidence:'确认',confirmed:false},row),/明确确认/);
  assert.throws(()=>progressBody({value:'1',evidence:'确认',confirmed:true},{...row,metric:{source_kind:'manual',unit:'元'}}),/财务/);
  assert.equal(progressBody({value:'9007199254740991.29',evidence:'证据',confirmed:true},{...row,metric:{source_kind:'manual',unit:'项',decimal_places:2}}).value,'9007199254740991.29');
});
test('missing and partial progress never become zero; mentions use only eligible recipients',()=>{
  assert.equal(progressText({metric,progress:{value:null,status:'missing'}}).value,'—');
  const partial=progressText({metric,progress:{value:'999',status:'incomplete',known_subtotal:'23.01'}});assert.equal(partial.value,'—');assert.equal(partial.subtotal,'23.01');
  assert.equal(progressText({metric,progress:{value:'0.00',complete:true}}).value,'0.00');
  assert.deepEqual(mentionBody({recipient_user_ids:['alice','alice']},{version_no:4},people),{version_no:4,recipient_user_ids:['alice']});
  assert.throws(()=>mentionBody({recipient_user_ids:['outsider']},{version_no:4},people),/成员目录/);
});
test('historical participants and parent links are preserved; manual confirmation uses exact subject authority',()=>{
  const draft=editDraft({...objective,version_no:2,title:'存量目标',owner_user_id:'hidden-owner',collaborator_user_ids:['hidden-collaborator'],parent_id:'hidden-parent'});
  const body=objectiveBody(draft,{people,options:{},parents:[]});assert.equal(body.owner_user_id,'hidden-owner');assert.deepEqual(body.collaborator_user_ids,['hidden-collaborator']);assert.equal(body.parent_id,'hidden-parent');
  assert.throws(()=>objectiveBody({...draft,owner_user_id:'other-hidden'},{people,options:{}}),/负责人/);
  const row={editable:true,status:'active',objective_status:'active',progress_mode:'manual',metric:{active:true,source_kind:'manual',unit:'次'},owner_user_id:'alice',scope:{scope_type:'team',team_id:'south'}};
  assert.equal(canConfirmProgress(row,'alice',{}),true);assert.equal(canConfirmProgress(row,'bob',{teams:[{id:'north',can_manage:true}]}),false);
  assert.equal(canConfirmProgress(row,'bob',{teams:[{id:'south',can_manage:true}]}),true);assert.equal(canConfirmProgress({...row,objective_status:'archived'},'alice',{}),false);
});
