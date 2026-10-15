require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const snapshot=require('../miniprogram/utils/visitSnapshot');
const filename=path.resolve(__dirname,'../miniprogram/pages/visit-confirm/index.js');
const values={follow_up_record:'客户确认试点，待补样本',next_action:'9月18日销售提交方案',interaction_at:'2026-09-15',created_date:'2026-09-15',contact_name:'陈经理'};
function page(api={}){let definition;const memory=new Map();const wx={getStorageSync:k=>memory.get(k),setStorageSync:(k,v)=>memory.set(k,v),removeStorageSync:k=>memory.delete(k),pageScrollTo(){},showToast(){},setNavigationBarTitle(){}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:d=>definition=d,getApp:()=>({globalData:{session:{userId:'u',workspaceId:'w'}}}),require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),n)),wx,setInterval,clearInterval,setTimeout,clearTimeout});
 return {...definition,data:{...structuredClone(definition.data),values:{...values},customerId:'customer',customerName:'演示客户',customerConfirmed:true,sourceRunId:'structure',collaboratorIds:[]},setData(o){Object.assign(this.data,o);},persist(){},userKey:'actor',draftKey:'draft',memory};
}
test('next requests independent quality; all related field changes invalidate score',async()=>{
 let body,calls=0;const p=page({submitVisitStage:async(stage,payload)=>{assert.equal(stage,'quality');body=payload;calls++;return {run_id:'quality'};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:82,next_action:{passed:true}}}})});
 await p.review();assert.equal(calls,1);assert.equal(p.data.flowStep,'result');assert.equal(p.data.canSubmit,true);assert.equal(body.source_run_id,'structure');
 p.data.values.contact_name='新人';p.refreshGate();assert.equal(p.data.canSubmit,false);
});
test('no quality generated on initial structure; old combined score never restores',()=>{
 const flow=require('../miniprogram/utils/visitFlow');const restored=flow.restoreReview({result:{fields:values,quality_review:{follow_up_score:90}}},null);
 assert.equal(restored.quality,null);assert.equal(restored.flowStep,'edit');
});
test('quality timeout resumes same run instead of creating duplicate',async()=>{
 let calls=0,waits=0;const p=page({submitVisitStage:async()=>{calls++;return {run_id:'quality'};},waitVisitRun:async()=>{if(++waits===1)throw Object.assign(Error('后台处理中'),{code:'RUN_TIMEOUT'});return {result:{visit_stage:'quality',quality_review:{follow_up_score:61,next_action:{passed:true}}}};}});
 await p.review();assert.equal(p.data.pendingReviewId,'quality');await p.review();assert.equal(calls,1);assert.equal(p.data.canSubmit,true);
});
test('save completes before advice; both linked and customer-only visits request advice',async()=>{
 for(const opportunity of [null,'opportunity']){let resolveSave;const calls=[];const p=page({createVisit:()=>new Promise(resolve=>resolveSave=resolve),requestBusinessAdvice:async()=>{calls.push('advice');throw Error('中台暂不可用');}});
 p.data.reviewPayload={fields:snapshot.fields(p.data),opportunity_id:opportunity,collaborator_ids:[]};p.data.reviewRunId='quality';p.data.quality={follow_up_score:85,next_action:{passed:true}};p.data.reviewedContent=snapshot.signature(p.data);p.refresh();p.submitArchive();assert.deepEqual(calls,[]);
 resolveSave({id:'visit',opportunity_id:opportunity});await new Promise(r=>setImmediate(r));assert.equal(p.data.archived,true);assert.equal(p.data.busy,false);assert.equal(calls.length,1);}
});
test('top FDE selection removed while opportunity form remains',()=>{const s=fs.readFileSync(filename.replace('.js','.wxml'),'utf8');assert.doesNotMatch(s,/本次协助 FDE|<fde-picker/);assert.match(s,/<opportunity-form/);assert.match(s,/disabled="\{\{!canSubmit\}\}"/);});


test('本次 FDE 选择进入质检和归档，修改参与人员使质检失效',async()=>{
 let reviewed,archived;
 const p=page({submitVisitStage:async(stage,body)=>{reviewed=body;return {run_id:'quality'};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:92,next_action:{passed:true}}}}),createVisit:async(customer,fields,participants)=>{archived={...fields,_fde_participant_ids:participants};return {id:'visit'};}});
 p.data.opportunityId='opportunity';p.data.selectedOpportunity={id:'opportunity'};p.data.opportunityEditing=true;p.data.opportunityDraft={visit_fde_member_ids:['zhang']};p.selectComponent=()=>({prepare:async()=>({name:'项目'})});
 await p.review();assert.deepEqual(reviewed.fde_participant_ids,['zhang']);assert.equal(p.data.canSubmit,true);
 p.data.opportunityDraft.visit_fde_member_ids=['ye'];p.refreshGate();assert.equal(p.data.canSubmit,false);
 p.data.opportunityDraft.visit_fde_member_ids=['zhang'];p.refreshGate();p.submitArchive();await new Promise(r=>setImmediate(r));
 assert.deepEqual(Array.from(archived._fde_participant_ids),['zhang']);
});

test('建议层仅顶部下滑关闭，横滑、向上滑与取消手势保留内容',()=>{
 const p=page();p.data.showAdvice=true;
 const gesture=(dx,dy)=>{p.startAdviceDrag({touches:[{clientX:100,clientY:100}]});p.endAdviceDrag({changedTouches:[{clientX:100+dx,clientY:100+dy}]});};
 gesture(70,50);assert.equal(p.data.showAdvice,true);gesture(0,-80);assert.equal(p.data.showAdvice,true);
 p.startAdviceDrag({touches:[{clientX:100,clientY:100}]});p.cancelAdviceDrag();p.endAdviceDrag({changedTouches:[{clientX:100,clientY:200}]});assert.equal(p.data.showAdvice,true);
 gesture(5,65);assert.equal(p.data.showAdvice,false);p.openAdvice();p.closeAdvice();assert.equal(p.data.showAdvice,false);
});

test('补充商机字段参与质检快照，编辑后必须重质检，归档保留同一v2原子修改',async()=>{
 const opp=require('../miniprogram/utils/opportunity');const row={id:'opportunity',name:'项目',amount:50000,probability:10,status:'open',expected_close_date:'2027-01-01',sales_channel:'direct',version_no:7,fde_members:[]};
 const draft=opp.formForVisit(row);draft.extra_values={summary:'原总结',is_key:false,participants:[{user_id:'person',role:'other',name:'显示名'}]};draft.extra_dirty={summary:true,is_key:true,participants:true};draft.visit_fde_member_ids=['attendee'];
 const reviews=[],archives=[];const p=page({submitVisitStage:async(stage,body)=>{reviews.push(structuredClone(body));return {run_id:'quality-'+reviews.length};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:92,next_action:{passed:true}}}}),createVisit:async(customer,fields,attendees)=>{archives.push({fields,attendees});return {id:'visit'};}});
 p.data.opportunityId=row.id;p.data.selectedOpportunity=row;p.data.opportunityOptions=[row];p.data.opportunityEditing=true;p.data.opportunityDraft=draft;p.selectComponent=()=>({prepare:async()=>opp.payload(p.data.opportunityDraft,row)});
 await p.review();assert.equal(reviews.length,1);assert.equal(reviews[0].opportunity_mutation.fields_schema_version,2);assert.equal(reviews[0].opportunity_mutation.is_key,false);assert.deepEqual(reviews[0].fde_participant_ids,['attendee']);assert.equal(p.data.canSubmit,true);
 p.data.opportunityDraft.extra_values.summary='新总结';p.refreshGate();assert.equal(p.data.canSubmit,false);p.submitArchive();assert.equal(archives.length,0);
 await p.review();assert.equal(reviews.length,2);assert.equal(p.data.canSubmit,true);await p.submitArchive();assert.equal(archives.length,1);assert.equal(archives[0].fields._opportunity_mutation.summary,'新总结');assert.equal(archives[0].fields._opportunity_mutation.fields_schema_version,2);assert.deepEqual(JSON.parse(JSON.stringify(archives[0].fields._opportunity_mutation.participants)),[{user_id:'person',role:'other'}]);assert.deepEqual(Array.from(archives[0].attendees),['attendee']);
});
