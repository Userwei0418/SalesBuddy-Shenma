/** Current source API against the explicit synthetic workspace. No fetch/network. */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url)),mini=path.join(root,'source/miniprogram');
const original='沟通内容：双方核对了试点验收清单，客户要求补充数据样本范围。\n下一步计划：明天由我整理验收清单并发送客户。\n跟进日期：2026-09-15\n对接人：合成联系人甲';
const clone=value=>JSON.parse(JSON.stringify(value));
function workspace(storage=new Map()){
 const window={SALES_MODE:'preview',setTimeout,clearTimeout,localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)}};
 const context=vm.createContext({window,URL,URLSearchParams,setTimeout,clearTimeout,console}),wxStorage=new Map(),cache=new Map();
 for(const name of ['preview-workflow.js','preview-api.js'])vm.runInContext(fs.readFileSync(path.join(root,name),'utf8'),context,{filename:name});
 context.wx={request:o=>window.SalesPreview.request(o),uploadFile:o=>window.SalesPreview.uploadFile(o),getStorageSync:k=>wxStorage.get(k),setStorageSync:(k,v)=>wxStorage.set(k,v),removeStorageSync:k=>wxStorage.delete(k)};
 function load(filename){const target=path.resolve(mini,filename.endsWith('.js')?filename:filename+'.js');if(cache.has(target))return cache.get(target).exports;const module={exports:{}};cache.set(target,module);vm.runInContext('(function(require,module,exports){'+fs.readFileSync(target,'utf8')+'\n})',context)(specifier=>load(path.relative(mini,path.resolve(path.dirname(target),specifier))),module,module.exports);return module.exports;}
 const api=load('utils/apiClient');
 return {window,api,load,storage,async login(role='sales'){await api.loginWithAccount('PREVIEW_'+role.toUpperCase(),'synthetic');await api.getBusinessOptions();return (await api.listCustomers()).items[0];},direct:(endpoint,options={})=>new Promise(resolve=>window.SalesPreview.request({url:'/api/v1'+endpoint,header:{Authorization:'Bearer '+api.getAuth().access_token},...options,success:resolve}))};
}
async function quality(w,customer,extra={}){
 const op=extra.opportunity_id||null;
 const accepted=await w.api.submitVisitStage('structure',{customer_id:customer.id,text:original,opportunity_id:op,is_first_visit:false});
 const run=await w.api.waitVisitRun(accepted.run_id);
 const payload={customer_id:customer.id,source_run_id:run.id,fields:run.result.fields,summary:run.result.summary,opportunity_id:op,source_import_id:null,collaborator_ids:[],fde_participant_ids:[],opportunity_mutation:null,...extra};
 const check=await w.api.submitVisitStage('quality',payload),review=await w.api.waitVisitRun(check.run_id);
 const body={customer_id:customer.id,fields:{...payload.fields,opportunity_id:payload.opportunity_id,source_import_id:payload.source_import_id,collaborator_ids:payload.collaborator_ids,_opportunity_mutation:payload.opportunity_mutation,_quality_review_run_id:review.id},fde_participant_ids:payload.fde_participant_ids};
 return {payload,body,review,run};
}

test('new two-stage source API requires original structure, returns quality discriminator and archives its exact snapshot',async()=>{
 const w=workspace(),c=await w.login();const {body,review,run}=await quality(w,c);
 assert.equal(run.result.visit_stage,'structure');assert.equal(run.result.quality_review,undefined);
 assert.equal(review.result.visit_stage,'quality');assert.equal(review.result.quality_review.review_kind,'deterministic_field_check');
 const saved=await w.api.createVisit(c.id,body.fields,body.fde_participant_ids),reread=await w.api.getVisit(saved.id);
 assert.equal(reread.contact_name_snapshot,'合成联系人甲');assert.equal(reread.visit_date,'2026-09-15');assert.equal(reread.follow_up_record,body.fields.follow_up_record);
 await assert.rejects(w.api.createVisit(c.id,body.fields,body.fde_participant_ids),/已经归档/);
});

test('snapshot changes to dates, contacts, association, collaborators and FDE participation all invalidate quality',async()=>{
 const w=workspace(),c=await w.login(),{body,payload}=await quality(w,c),op=(await w.api.listCustomerOpportunities(c.id)).items[0];
 const changes=[{...body,fields:{...body.fields,interaction_at:'2026-09-14'}},{...body,fields:{...body.fields,contact_name:'已变更联系人'}},{...body,fields:{...body.fields,opportunity_id:op.id}},{...body,fields:{...body.fields,collaborator_ids:[w.api.getAuth().actor.user_id]}},{...body,fde_participant_ids:['00000001-0000-4000-8000-000000000004']}];
 for(const changed of changes){const response=await w.direct('/visits',{method:'POST',data:changed});assert.equal(response.statusCode,409,response.data.message);}
 for(const bad of [{...payload,source_run_id:'00000000-0000-4000-8000-000000000000'},{...payload,fields:{...payload.fields,contact_name:''}},{...payload,fields:{...payload.fields,interaction_at:'2026-02-30'}},{...payload,fields:{...payload.fields,unrecognized:'forbidden'}}])await assert.rejects(w.api.submitVisitStage('quality',bad));
 const rereview=await w.api.submitVisitStage('quality',{...payload,fields:{...payload.fields,contact_name:'已变更联系人'}});assert.ok(rereview.run_id);
});

test('stage and archive idempotency keys replay one accepted run and one archived entity',async()=>{
 const w=workspace(),c=await w.login();
 const opts={method:'POST',data:{customer_id:c.id,text:original},header:{Authorization:'Bearer '+w.api.getAuth().access_token,'Idempotency-Key':'structure-one'}};
 const first=await w.direct('/visit-flow/structure',opts),again=await w.direct('/visit-flow/structure',opts);assert.equal(first.statusCode,202);assert.equal(again.data.run_id,first.data.run_id);
 assert.equal((await w.direct('/visit-flow/structure',{...opts,data:{...opts.data,text:original+'变更'}})).statusCode,409);
 const {body}=await quality(w,c);const archive={method:'POST',data:body,header:{...opts.header,'Idempotency-Key':'archive-one'}};
 const a=await w.direct('/visits',archive),b=await w.direct('/visits',archive);assert.equal(a.statusCode,200);assert.equal(a.data.id,b.data.id);
});

test('FDE structure must target its participating opportunity and same actor keeps quality/readback',async()=>{
 const w=workspace(),c=await w.login('fde');await assert.rejects(w.api.submitVisitStage('structure',{customer_id:c.id,text:original}),/本人参与/);
 const op=(await w.api.listFdeVisitOpportunities({customer_id:c.id})).items[0];const {body,payload,run}=await quality(w,c,{opportunity_id:op.id});
 await assert.rejects(w.api.submitVisitStage('quality',{...payload,collaborator_ids:[w.api.getAuth().actor.user_id]}));
 const saved=await w.api.createVisit(c.id,body.fields,[]);assert.equal(saved.opportunity_id,op.id);assert.ok(saved.fde_participant_ids.includes(w.api.getAuth().actor.user_id));
 await w.login('sales');await assert.rejects(w.api.waitVisitRun(run.id),/不存在|不可见/);
});

test('new recipient pagination collects every company colleague without granting CRM visibility',async()=>{
 const w=workspace();await w.login();const first=await w.api.listTaskRecipients({page_size:2,offset:0}),second=await w.api.listTaskRecipients({page_size:2,offset:first.next_offset});
 assert.equal(first.has_more,true);assert.equal(first.items.length,2);assert.equal(second.items.length,2);assert.notEqual(first.items[0].id,second.items[0].id);
 const all=await w.load('utils/taskRecipients').allTaskRecipients(w.api);assert.equal(all.length,5);assert.equal(new Set(all.map(p=>p.id)).size,5);
 const salesCustomers=await w.api.listTaskCustomers();assert.equal(salesCustomers.items.length,6);
 const foreign='00000010-0000-4000-8000-000000000008';assert.equal((await w.api.listTaskOpportunities({customerId:foreign})).items.length,0);await assert.rejects(w.api.getCustomerHeader(foreign),/不可见/);
});

test('daily/customer task association survives receiver acceptance/completion and rejects missing or mismatched links',async()=>{
 const w=workspace(),c=await w.login(),op=(await w.api.listTaskOpportunities({customerId:c.id})).items[0],next=(await w.api.listCustomers()).items[1];
 const common={description:'合成任务核对关联并反馈',assigneeAccount:'PREVIEW_FDE',dueAt:new Date(Date.now()+86400000).toISOString()};
 await assert.rejects(w.api.createTask({...common,associationKind:'customer',customerId:c.id}),/必须同时关联/);
 await assert.rejects(w.api.createTask({...common,associationKind:'daily',customerId:c.id,opportunityId:op.id}),/日常任务/);
 await assert.rejects(w.api.createTask({...common,associationKind:'customer',customerId:next.id,opportunityId:op.id}),/属于所选客户/);
 const task=await w.api.createTask({...common,associationKind:'customer',customerId:c.id,opportunityId:op.id});assert.equal(task.association_kind,'customer');
 const daily=await w.api.createTask({...common,associationKind:'daily'});assert.equal(daily.customer_id,null);assert.equal(daily.opportunity_id,null);
 await w.login('fde');const accepted=await w.api.respondTask(task.id,'accept','',task.version_no),completed=await w.api.completeTask(task.id,'合成回执已核对',accepted.version_no);
 assert.equal(completed.status,'pending_review');assert.equal((await w.api.getTask(task.id)).opportunity_id,op.id);
});

test('Demo CRUD persists source object, checks version/actor and counts real synthetic scenes only',async()=>{
 const w=workspace(),c=await w.login('fde'),op=(await w.api.listFdeVisitOpportunities({customer_id:c.id})).items[0];
 const before=await w.api.listDemoScenes(op.id);assert.equal(before.editable,true);assert.equal(before.total,0);
 const created=await w.api.createDemoScenes(op.id,[{name:'合成需求检索',description:'仅登记场景，不运行部署'}]);const row=created.items[0];assert.equal(row.version_no,1);
 const edited=await w.api.updateDemoScene(row.id,{name:'合成需求检索调整',description:'第二版',version_no:row.version_no});assert.equal(edited.version_no,2);
 await assert.rejects(w.api.deleteDemoScene(row.id,1),/版本/);
 const dashboard=await w.api.getFdeDashboard({scope:'self',year:new Date().getFullYear(),quarters:[]});assert.equal(dashboard.summary.own_demo_scene_count,1);assert.equal(dashboard.company_rankings.items[0].demo_scene_count,1);
 await w.login('sales');assert.equal((await w.api.getDemoScene(row.id)).can_edit,false);await assert.rejects(w.api.updateDemoScene(row.id,{name:'越权',description:'不应保存',version_no:2}),/创建人/);
 await w.login('fde');await w.api.deleteDemoScene(row.id,2);assert.equal((await w.api.listDemoScenes(op.id)).total,0);await assert.rejects(w.api.getDemoScene(row.id),/不存在/);
});

test('first targets take effect; whole subsequent batch awaits approval and cannot overwrite effective values',async()=>{
 const w=workspace();await w.login();const month=Number(new Date(Date.now()+28800000).toISOString().slice(5,7));const query={scope:'self',period_type:'quarter',anchor_date:new Date().getFullYear()+'-'+String(Math.floor((month-1)/3)*3+1).padStart(2,'0')+'-01'};
 assert.equal((await w.api.getTargets(query)).items.length,0);
 const first=await w.api.saveTargetBatch({...query,reason:'合成首次目标依据',items:[{kind:'collection',amount:'1234.56',version_no:null},{kind:'recognized',amount:'2000.01',version_no:null}]});assert.equal(first.status,'saved');
 const read=await w.api.getTargets(query);assert.equal(read.items[0].amount_text,'1234.56');assert.equal(read.items[1].version_no,1);
 const pending=await w.api.saveTargetBatch({...query,reason:'合成目标调整依据',items:[{kind:'collection',amount:'1500.01',version_no:1},{kind:'recognized',amount:'2500.99',version_no:1}]});assert.equal(pending.status,'pending');
 const after=await w.api.getTargets(query);assert.equal(after.items[0].amount_text,'1234.56');assert.equal(after.pending_batches.length,1);assert.equal(after.pending_batches[0].items[1].proposed_amount,'2500.99');
 await assert.rejects(w.api.saveTargetBatch({...query,reason:'重复',items:[{kind:'collection',amount:'9',version_no:1}]}),/待运营审批/);
 const restored=workspace(w.storage);await restored.login();assert.equal((await restored.api.getTargets(query)).pending_batches.length,1);
});

test('scope and FDE ranking contracts reflect the selected cohort with no fabricated score history',async()=>{
 const w=workspace();await w.login('fde_lead');const options=await w.api.getFdeScopeOptions();assert.equal(options.members.length,2);
 const selected=options.members.find(row=>row.role==='fde'),profile=await w.api.getFdeProfile({days:30,scope:'self',member_id:selected.id});assert.equal(profile.member_id,selected.id);assert.equal(profile.can_review,false);assert.deepEqual(clone(profile.history),[]);assert.ok(profile.latest.dimensions.every(row=>row.score===null));
 const result=await w.api.getFdeDashboard({scope:'team',member_id:selected.id,year:new Date().getFullYear(),quarters:[]});const ranks=w.load('utils/fdeCompanyRankings').companyRankings(result.company_rankings,new Date().getFullYear(),[]);assert.equal(ranks.companyRankingReady,true);assert.equal(result.company_rankings.selection.member_id,selected.id);
 await assert.rejects(w.api.reviewFdeProfile(),/Agent 暂未接入/);
});

test('new sales dashboard member selection, contract-v2 rankings and zero-member team do not mix facts',async()=>{
 const w=workspace();await w.login('manager');const options=await w.api.getDashboardOptions(),person=options.members.find(row=>row.role==='sales');assert.ok(person);
 const facts=await w.api.getDashboard(true,{member_id:person.id});assert.equal(facts.scope,'member');assert.equal(facts.selection.member_id,person.id);assert.ok(facts.opportunities.every(row=>row.owner_id===person.id));
 const ranks=await w.api.getDashboardRankings({personal:true,member_id:person.id,year:new Date().getFullYear(),quarters:[1,2,3,4]});assert.equal(ranks.contract_version,2);assert.equal(ranks.scope,'peer');assert.ok(ranks.opportunity_acv.rows.every(row=>row.role==='sales'));
 const empty=await w.api.getDashboard(false,{team_groups:['team:00000003-0000-4000-8000-000000000002']});assert.equal(empty.scope,'team');assert.equal(empty.opportunities.length,0);
 const company=await w.api.getDashboardRankings({personal:false,team_groups:['team:00000003-0000-4000-8000-000000000002'],year:new Date().getFullYear(),quarters:[1,2,3,4]});assert.equal(company.scope,'company_teams');assert.equal(company.opportunity_acv.rows.length,2);assert.equal(company.opportunity_acv.rows.find(row=>row.code==='team:00000003-0000-4000-8000-000000000002').value,0);
 await w.login('sales');await assert.rejects(w.api.getDashboard(true,{member_id:options.members.find(row=>row.role==='supervisor').id}),/无权|本人|范围/);
});

test('a task recipient without participation receives task summary without gaining opportunity access',async()=>{
 const w=workspace(),c=await w.login(),helpers=w.load('utils/opportunity');
 const form={...helpers.formFor(null),name:'合成无FDE参与项目',amount:'25',stageIndex:0,expected_close_date:'2026-12-20'};
 const op=await w.api.createOpportunity(c.id,{...helpers.payload(form,null),fde_member_ids:[]});
 const task=await w.api.createTask({description:'合成未参与人接收必要任务摘要',assigneeAccount:'PREVIEW_FDE',dueAt:new Date(Date.now()+86400000).toISOString(),associationKind:'customer',customerId:c.id,opportunityId:op.id});
 await w.login('fde');assert.equal((await w.api.getTask(task.id)).opportunity_name,op.name);await assert.rejects(w.api.getOpportunityDetailHeader(op.id),/不可见/);assert.equal((await w.api.listFdeVisitOpportunities({customer_id:c.id,opportunity_id:op.id})).items.length,0);
});

test('new stage review binds commercial mutation before atomic visit plus opportunity save',async()=>{
 const w=workspace(),c=await w.login(),helpers=w.load('utils/opportunity'),before=(await w.api.listCustomerOpportunities(c.id)).total;
 const mutation=helpers.payload({...helpers.formFor(null),name:'合成stage共同保存项目',amount:'25',stageIndex:0,expected_close_date:'2026-12-20'},null);
 const {body,payload}=await quality(w,c,{opportunity_mutation:mutation});
 const changed={...body,fields:{...body.fields,_opportunity_mutation:{...mutation,amount:260000}}};
 assert.equal((await w.direct('/visits',{method:'POST',data:changed})).statusCode,409);assert.equal((await w.api.listCustomerOpportunities(c.id)).total,before);
 const saved=await w.api.createVisit(c.id,body.fields,body.fde_participant_ids);assert.ok(saved.opportunity_id);assert.equal((await w.api.listCustomerOpportunities(c.id)).total,before+1);assert.equal((await w.api.getOpportunityDetailOverview(saved.opportunity_id)).primary_opportunity.amount,250000);
 assert.equal(payload.opportunity_mutation.name,'合成stage共同保存项目');
});
