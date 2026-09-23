require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const access = require('../miniprogram/utils/access');
const snapshot = require('../miniprogram/utils/visitSnapshot');
const base = path.resolve(__dirname, '../miniprogram');
const session = {userId:'fde1',workspaceId:'w',role:'fde',permissionVersion:'v2',capabilities:{'visit.create':true,'customer.read':true,'opportunity.read':true}};
const app = {globalData:{session},can:key=>access.can(session,key),ensureLogin:()=>true};
function make(relative, {api={}, appOverride=app, kind='Page', wx={}}={}) {
  const filename=path.join(base,relative);let definition;
  const context={require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),Date,Map,Set,Promise,setTimeout,clearTimeout,setInterval,clearInterval,console,getApp:()=>appOverride,wx:{showToast(){},setStorageSync(){},getStorageSync(){},removeStorageSync(){},pageScrollTo(){},setNavigationBarTitle(){},nextTick:callback=>Promise.resolve().then(callback),...wx}};
  context[kind]=value=>{definition=value;};vm.runInNewContext(fs.readFileSync(filename,'utf8'),context);
  return {...definition,...definition.methods,properties:{},data:JSON.parse(JSON.stringify(definition.data||{})),setData(values,callback){for(const [key,value] of Object.entries(values)){let node=this.data;const keys=key.split('.');for(const k of keys.slice(0,-1))node=node[k]||(node[k]={});node[keys.at(-1)]=value;}if(callback)callback();},triggerEvent(){}};
}
const fields={follow_up_record:'客户确认部署范围',next_action:'2026-09-20由本人提交部署计划',contact_name:'陈经理',interaction_at:'2026-09-15',created_date:'2026-09-15'};
const own={id:'o1',name:'旧客户持续跟进商机',customer_id:'c1',customer_name:'客户一'};

test('FDE和负责人都依赖服务端正常录入能力，不获得商机商业写权限',()=>{
  for(const role of ['fde','fde_lead']){
    const s={...session,role};assert.equal(access.pageAllowed(s,'visit-entry'),true);assert.equal(access.pageAllowed(s,'visit-confirm'),true);assert.equal(access.pageAllowed(s,'opportunity-create'),false);
    assert.equal(access.can({...s,capabilities:{'visit.create':false}},'visit.create'),false);
  }
});

test('预选商机精确核验，不将客户全貌读取权限视为录入资格',async()=>{
  const queries=[],events=[];
  const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',api:{listFdeVisitOpportunities:async q=>{queries.push(q);return {items:[own],total:1};}}});
  picker.properties={customerId:'c1',selectedId:'o1'};picker.triggerEvent=(_,e)=>events.push(e);
  await picker.checkSelection();assert.equal(queries[0].opportunity_id,'o1');assert.equal(queries[0].customer_id,'c1');assert.equal(queries[0].scope,undefined);assert.equal(picker.data.selected.id,'o1');assert.equal(events.at(-1).verified,true);
});

test('已移出商机即使返回另一条可录入商机也不静默替换',async()=>{
  const events=[];const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',api:{listFdeVisitOpportunities:async()=>({items:[{...own,id:'o2'}],total:1})}});
  picker.properties={customerId:'c1',selectedId:'o1'};picker.triggerEvent=(_,e)=>events.push(e);
  await picker.checkSelection();assert.equal(picker.data.selected,null);assert.match(picker.data.error,/已不在/);assert.equal(events.at(-1).verified,false);assert.equal(picker.properties.selectedId,'o1');
});

test('清除客户时晚到的原客户核验响应不能恢复选择',async()=>{
  let resolve;const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',api:{listFdeVisitOpportunities:()=>new Promise(r=>resolve=r)}});
  picker.properties={customerId:'c1',selectedId:'o1'};const pending=picker.checkSelection();picker.properties={customerId:'',selectedId:''};picker.checkSelection();resolve({items:[own],total:1});await pending;assert.equal(picker.data.selected,null);
});

test('属性核验通知离开父更新栈，后续用户选择不会被延迟清空',async()=>{
  const ticks=[],events=[];
  const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',wx:{nextTick:callback=>ticks.push(callback)},api:{listFdeVisitOpportunities:async()=>({items:[own],total:1})}});
  picker.properties={customerId:'c1',selectedId:''};picker.triggerEvent=(_,event)=>events.push(event);
  const pending=picker.checkSelection(false,true);
  assert.equal(events.length,0,'属性更新栈内不反入父页面');
  await pending;
  picker.choose({currentTarget:{dataset:{id:'o1'}}});
  assert.equal(events.length,1);assert.equal(events[0].verified,true);
  ticks.forEach(callback=>callback());
  assert.equal(events.length,1,'旧的延迟撤销不能覆盖当前已确认选择');
  assert.equal(picker.data.selected.id,'o1');
});

test('延迟核验通知在客户再变更、换身份或组件离页后作废',()=>{
  const ticks=[],events=[];const actorApp={globalData:{session:{...session}}};
  const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',appOverride:actorApp,wx:{nextTick:callback=>ticks.push(callback)}});
  picker.properties={customerId:'',selectedId:'o1'};picker.triggerEvent=(_,event)=>events.push(event);
  picker.observers['customerId, selectedId'].call(picker);picker.properties={customerId:'',selectedId:''};picker.observers['customerId, selectedId'].call(picker);
  ticks.shift()();assert.equal(events.length,0,'旧上下文通知被新核验替代');
  ticks.shift()();assert.equal(events.length,1);assert.equal(events[0].verified,false);
  picker.checkSelection(true,true);actorApp.globalData.session={...session,permissionVersion:'v3'};
  ticks.shift()();assert.equal(events.length,1,'旧身份通知不得回填');
  picker.checkSelection(true,true);picker.lifetimes.detached.call(picker);
  ticks.shift()();assert.equal(events.length,1,'离页后不触发父页面更新');
});

test('父页同步取消旧录入资格，商机核验结果一次更新提交状态',()=>{
  const page=make('pages/visit-entry/index.js');
  page.setData({isFde:true,customerId:'old',opportunityId:'o1',fdeOpportunityVerified:true,canSubmit:true,transcript:'复核业务需求'});
  page.confirmSelectedCustomer({id:'c1',name:'客户一'});
  assert.equal(page.data.fdeOpportunityVerified,false);assert.equal(page.data.canSubmit,false);
  const updates=[],setData=page.setData;page.setData=function(values){updates.push(values);setData.call(this,values);};
  page.fdeOpportunityChanged({detail:{opportunity:own,verified:true}});
  assert.equal(updates.length,1);assert.equal(page.data.canSubmit,true);
  assert.equal(page.data.opportunityId,'o1');assert.equal(page.data.fdeOpportunityVerified,true);
  updates.length=0;page.fdeOpportunityChanged({detail:{opportunity:null,verified:false}});
  assert.equal(updates.length,1);assert.equal(page.data.canSubmit,false);
  assert.equal(page.entryReady(page.data.transcript),false);
});

test('FDE页面恢复时在重验商机之前立即取消提交资格，销售选择不受影响',()=>{
  const page=make('pages/visit-entry/index.js');let checked=false;
  page.setData({isFde:true,customerId:'c1',opportunityId:'o1',fdeOpportunityVerified:true,canSubmit:true,transcript:'复核业务需求'});
  page.selectComponent=()=>({checkSelection(force){checked=true;assert.equal(force,true);assert.equal(page.data.fdeOpportunityVerified,false);assert.equal(page.data.canSubmit,false);}});
  page.onShow();assert.equal(checked,true);
  page.data.isFde=false;page.confirmSelectedCustomer({id:'c2',name:'销售客户'});
  assert.equal(page.data.canSubmit,true,'销售仍可不关联商机录入');
});

test('本人商机查询翻页携带客户和查询词，不添加团队范围',async()=>{
  const queries=[];const picker=make('components/fde-visit-opportunity/index.js',{kind:'Component',api:{listFdeVisitOpportunities:async q=>{queries.push(q);return {items:[{...own,id:`o${q.offset}`}],total:2,has_more:q.offset===0,next_offset:q.offset+1};}}});
  picker.properties={customerId:'c1',selectedId:''};picker.data.query='持续';await picker.load();await picker.more();assert.equal(queries[1].q,'持续');assert.equal(queries[1].offset,1);assert.equal(queries[1].customer_id,'c1');assert.equal(picker.data.items.length,2);
});

test('FDE缺商机禁止AI整理，销售仍能无商机录入',()=>{
  const page=make('pages/visit-entry/index.js');page.setData({isFde:true,customerId:'c1',transcript:'已沟通并约定下次复核'});
  assert.equal(page.entryReady(page.data.transcript),false);page.submitTranscript();assert.match(page.data.errorText,/本人参与/);
  page.fdeOpportunityChanged({detail:{opportunity:own,verified:true}});assert.equal(page.data.canSubmit,true);
  page.fdeOpportunityChanged({detail:{opportunity:null,verified:false}});assert.equal(page.data.canSubmit,false);
  page.data.isFde=false;assert.equal(page.entryReady(page.data.transcript),true);
});

test('FDE与负责人原文只调用结构化，不调用质检或新建商机Agent',async()=>{
 for(const role of ['fde','fde_lead']){
  const calls=[],saved=[];const actorApp={...app,globalData:{session:{...session,role}}};
  const page=make('pages/visit-entry/index.js',{appOverride:actorApp,api:{submitVisitStage:async(stage,body)=>{calls.push({stage,body});return {run_id:'structure'};},waitVisitRun:async()=>({id:'structure',result:{visit_stage:'structure',fields}}),runAgent:()=>{throw Error('不应调用商机判断');}},wx:{setStorageSync:(key,value)=>saved.push({key,value}),navigateTo(){}}});
  page.setData({isFde:true,customerId:'c1',customerName:'客户一',opportunityId:'o1',fdeOpportunityVerified:true,transcript:'复核旧客户项目使用反馈'});
  page.submitTranscript();await new Promise(r=>setImmediate(r));assert.equal(calls.length,1);assert.equal(calls[0].stage,'structure');assert.equal(calls[0].body.opportunity_id,'o1');
  const result=saved.find(row=>String(row.key).includes('visitStructured')).value;assert.equal(result.opportunityId,'o1');assert.equal(result.result.quality_review,undefined);
 }
});

test('FDE换商机使旧质检失效，人工重审绑定新商机',async()=>{
 const calls=[],quality={follow_up_score:88,next_action:{passed:true}};
 const page=make('pages/visit-confirm/index.js',{api:{submitVisitStage:async(stage,body)=>{calls.push({stage,body});return {run_id:'quality'};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:quality}})}});
 page.setData({isFde:true,customerId:'c1',customerName:'客户一',customerConfirmed:true,opportunityId:'o1',fdeOpportunityVerified:true,values:fields,quality,sourceRunId:'structure',reviewRunId:'old-quality'});
 page.data.reviewedContent=snapshot.signature(page.data);page.refreshGate();assert.equal(page.data.reviewStale,false);
 page.fdeOpportunityChanged({detail:{opportunity:{...own,id:'o2'},verified:true}});assert.equal(page.data.reviewStale,true);assert.equal(page.data.canSubmit,false);assert.equal(calls.length,0);
 await page.review();assert.equal(calls[0].stage,'quality');assert.equal(calls[0].body.opportunity_id,'o2');assert.equal(calls[0].body.source_run_id,'structure');assert.equal(page.data.reviewStale,false);
});

test('FDE商机详情的本人记录读取全历史自身范围，分页不混入全部跟进',async()=>{
  const queries=[];const page=make('pages/customer-assets/index.js',{api:{getFdeActivity:async q=>{queries.push(q);return {items:[{id:`v${q.offset}`,customer_id:'c1',opportunity_id:'o1',recorder_name:'本人',interaction_at:'2025-09-01',can_read_detail:true}],total:2,has_more:q.offset===0,next_offset:q.offset+1};}}});
  page.setData({isFde:true,opportunityId:'o1',relatedVisits:[{id:'sales-visit'}]});await page.loadFdeOwnVisits();await page.moreFdeOwnVisits();assert.equal(queries[0].scope,'self');assert.equal(queries[0].period,'all');assert.equal(queries[0].opportunity_id,'o1');assert.equal(page.data.fdeOwnVisits.length,2);assert.equal(page.data.relatedVisits[0].id,'sales-visit');
});

test('商机详情未获本人录入资格时不可发起录入，取得资格后带精确上下文',async()=>{
  const opened=[];let eligible=false;const page=make('pages/customer-assets/index.js',{api:{listFdeVisitOpportunities:async()=>({items:eligible?[own]:[],total:eligible?1:0})},wx:{navigateTo:r=>opened.push(r.url)}});
  page.setData({isFde:true,customerId:'c1',opportunityId:'o1'});await page.checkFdeVisitEligibility();page.recordFdeVisit();assert.equal(opened.length,0);
  eligible=true;await page.checkFdeVisitEligibility();page.recordFdeVisit();assert.match(opened[0],/customer_id=c1&opportunity_id=o1/);
});

test('普通FDE不编辑销售名单，拜访页无协同人和协助FDE选择',()=>{
 const detail=make('pages/customer-assets/index.js');detail.data.isFde=true;detail.fdeMembersChanged({detail:{members:[own]}});assert.equal(detail.data.fdeRelationDirty,undefined);
 const markup=fs.readFileSync(path.join(base,'pages/visit-confirm/index.wxml'),'utf8');
 assert.match(markup,/wx:if="\{\{!isFde\}\}" class="collaborator-row"/);assert.match(markup,/!isFde && !editing && customerConfirmed && opportunityEditing/);assert.doesNotMatch(markup,/<fde-picker/);
});

test('FDE主管独立商机页保留名单管理，本人拜访页仍不代选参与人',async()=>{
  const writes=[];
  const lead={...session,role:'fde_lead',capabilities:{...session.capabilities,'fde.members.manage':true}};
  const leadApp={...app,globalData:{session:lead},can:key=>access.can(lead,key)};
  const page=make('pages/customer-assets/index.js',{appOverride:leadApp,api:{updateFdeMembers:async(...args)=>{writes.push(args);return {version_no:2,changed:true,fde_members:[{id:'fde2',name:'本团队成员'}]};}}});
  page.setData({isFde:true,isFdeLead:true,canManageFdeRelation:true,opportunityId:'o1',opportunity:{id:'o1',version_no:1,fde_members:[]}});
  page.fdeMembersChanged({detail:{members:[{id:'fde2',name:'本团队成员'}]}});await page.saveFdeMembers();
  assert.equal(writes.length,1);assert.equal(writes[0][0],'o1');assert.deepEqual(Array.from(writes[0][1]),['fde2']);assert.equal(writes[0][2],1);
  const confirm=make('pages/visit-confirm/index.js',{appOverride:leadApp});confirm.data.isFde=true;confirm.data.isFdeLead=true;
  confirm.fdeOpportunityChanged({detail:{opportunity:own,verified:true}});assert.equal(confirm.data.opportunityEditing,false);assert.equal(confirm.data.opportunityDraft,null);
});

test('真实HTTP适配器传递本人商机筛选和AI商机上下文，保留销售旧调用',async()=>{
  const storage=new Map(),requests=[];
  global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),request:o=>{if(o.url.endsWith('/metadata/business-options')){o.success({statusCode:200,data:require('./helpers/business-options.json')});return;}requests.push(o);const url=new URL(o.url);const data=url.pathname.endsWith('/conversations')?{id:'conv'}:url.pathname.endsWith('/messages')?{run_id:'run'}:url.pathname.includes('/agent/runs/')?{id:'run',status:'waiting_human',result:{}}:{items:[],total:0};o.success({statusCode:200,data});}};
  const modulePath=require.resolve('../miniprogram/utils/apiClient');delete require.cache[modulePath];const api=require(modulePath);
  try {
    api.saveAuth({access_token:'unit-test-token',actor:{workspace_id:'w',user_id:'fde1'}});
    await api.listFdeVisitOpportunities({customer_id:'c1',opportunity_id:'o1',limit:1,offset:0});
    const url=new URL(requests[0].url);assert.equal(url.pathname,'/api/v1/fde/visit-opportunities');assert.equal(url.searchParams.get('opportunity_id'),'o1');assert.equal(url.searchParams.get('offset'),'0');
    await api.runAgent('visit_entry','原文','c1','o1');
    assert.deepEqual(requests.find(r=>new URL(r.url).pathname.endsWith('/conversations')).data,{mode:'visit_entry',customer_id:'c1',opportunity_id:'o1'});
    const prior=requests.length;await api.runAgent('visit_entry','销售原文','c1');assert.deepEqual(requests.slice(prior).find(r=>new URL(r.url).pathname.endsWith('/conversations')).data,{mode:'visit_entry',customer_id:'c1'});
  } finally { delete global.wx; delete require.cache[modulePath]; }
});

test('拜访客户选择按实际客户接口mine枚举查询，FDE身份在首次加载前明确',async()=>{
  for(const role of ['fde','fde_lead','sales']){
    const queries=[];
    const actorApp={...app,globalData:{session:{...session,role}}};
    const page=make('pages/visit-entry/index.js',{appOverride:actorApp,api:{listCustomers:async options=>{queries.push(options);return {items:[{id:'c1',name:'测试客户'}]};}}});
    await page.searchDepartmentCustomers();
    assert.equal(page.data.isFde,role!=='sales');
    assert.equal(queries[0].scope,role==='sales'?'company':'mine');
    assert.ok(['mine','department','company'].includes(queries[0].scope));
    assert.equal(page.data.customerResults[0].id,'c1');
  }
});

test('客户接口422与网络错误显示可重试状态，重试恢复不会被当作无匹配',async()=>{
  for(const statusCode of [422,503]){
    let failed=true;
    const page=make('pages/visit-entry/index.js',{api:{listCustomers:async()=>{if(failed)throw Object.assign(new Error('server detail'),{statusCode});return {items:[{id:'c1',name:'测试客户'}]};}}});
    await page.searchDepartmentCustomers();
    assert.ok(page.data.customerSearchError);assert.equal(page.data.searching,false);assert.equal(page.data.customerResults.length,0);
    failed=false;await page.searchDepartmentCustomers();
    assert.equal(page.data.customerSearchError,'');assert.equal(page.data.customerResults.length,1);
  }
});

test('FDE结构化后先核对，再点下一步独立评分；商机核验不自动调用质检',async()=>{
 const calls=[];const page=make('pages/visit-confirm/index.js',{api:{submitVisitStage:async(stage,body)=>{calls.push({stage,body});return {run_id:'quality'};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:88,next_action:{passed:true}}}})}});
 page.setData({isFde:true,customerConfirmed:true,customerName:'客户一',customerId:'c1',opportunityId:'o1',sourceRunId:'structure',values:fields});
 page.onReady();page.fdeOpportunityChanged({detail:{opportunity:own,verified:true}});await new Promise(r=>setImmediate(r));assert.equal(calls.length,0);assert.equal(page.data.quality,null);assert.equal(page.data.canSubmit,false);
 await page.review();assert.equal(calls.length,1);assert.equal(calls[0].stage,'quality');assert.equal(page.data.canSubmit,true);
 page.fdeOpportunityChanged({detail:{opportunity:own,verified:true}});page.onReady();assert.equal(calls.length,1);
});

test('FDE与负责人归档不请求待办建议，保存不携带协同人或代选FDE',async()=>{
 for(const role of ['fde','fde_lead']){
  let saved;const calls=[];const actorApp={...app,globalData:{session:{...session,role}}};
  const page=make('pages/visit-confirm/index.js',{appOverride:actorApp,api:{submitVisitStage:async(stage,body)=>{assert.equal(body.opportunity_mutation,null);assert.equal(body.collaborator_ids.length,0);assert.equal(body.fde_participant_ids.length,0);return {run_id:'quality'};},waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:88,next_action:{passed:true}}}}),createVisit:async(...args)=>{saved=args;return {id:'visit',opportunity_id:'o1'};},queryBusinessAdvice:async()=>{calls.push('query');},getBusinessAdvice:async()=>{calls.push('reload');}}});
  page.setData({isFde:true,customerConfirmed:true,customerId:'c1',customerName:'客户一',opportunityId:'o1',fdeOpportunityVerified:true,sourceRunId:'structure',values:fields,collaboratorIds:['legacy'],opportunityEditing:true,opportunityDraft:{visit_fde_member_ids:['other-fde']}});
  await page.review();assert.equal(page.data.canSubmit,true);assert.equal(saved,undefined,'评分后仍须人工确认');
  page.submitArchive();await new Promise(r=>setImmediate(r));assert.equal(page.data.archived,true);assert.equal(saved[1].collaborator_ids.length,0);assert.equal(saved[2].length,0);assert.equal(saved[1]._opportunity_mutation,null);
  page.data.advice={id:'old-advice'};await page.loadAdvice();await page.reloadAdvice();page.openAdvice();assert.equal(page.data.showAdvice,false);assert.deepEqual(calls,[]);
 }
});
