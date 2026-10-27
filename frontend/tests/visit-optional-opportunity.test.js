require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const base=path.resolve(__dirname,'../miniprogram');
const tick=()=>new Promise(r=>setImmediate(r));
const clone=x=>x===undefined?undefined:JSON.parse(JSON.stringify(x));
const row={id:'o1',customer_id:'c1',name:'项目一',sales_channel:'direct'};
const fields={follow_up_record:'客户确认范围',next_action:'2026-10-12由本人提交计划',contact_name:'陈经理',interaction_at:'2026-10-09',created_date:'2026-10-09'};
const pageOf=items=>({items,has_more:false,next_offset:null});
function harness(role='sales',overrides={}) {
  const storage=new Map(),calls=[],timers=[];
  const app={globalData:{session:{workspaceId:'w',userId:'u',role,permissionVersion:1}},can:()=>false,ensureLogin:()=>true};
  const api={getCustomerReference:async id=>({id,name:'客户一'}),listCustomers:async()=>({items:[]}),
    listOpportunities:async q=>{calls.push({kind:'list',q});return pageOf([row]);},
    getOpportunityDetailOverview:async id=>({id:'c1',opportunities:[{...row,id}]}),
    request:async()=>({items:[]}),
    submitVisitStage:async(stage,body)=>{calls.push({stage,body:clone(body)});return {run_id:stage};},
    waitVisitRun:async id=>({run_id:id,result:id==='structure'?{fields}:{visit_stage:'quality',quality_review:{follow_up_score:88,next_action:{passed:true}}}}),
    createVisit:async(customerId,body,participants)=>{calls.push({stage:'archive',customerId,body:clone(body),participants});return {id:'v1',fields};},
    ...overrides};
  const wx={getStorageSync:k=>clone(storage.get(k)),setStorageSync:(k,v)=>storage.set(k,clone(v)),removeStorageSync:k=>storage.delete(k),
    getRecorderManager:()=>({onStart(){},onStop(){},onError(){}}),showToast(){},navigateTo(){},pageScrollTo(){},setNavigationBarTitle(){},nextTick:fn=>Promise.resolve().then(fn)};
  function make(relative,kind='Page') {
    let definition;const file=path.join(base,relative);
    vm.runInNewContext(fs.readFileSync(file,'utf8'),{[kind]:d=>definition=d,require:n=>n.endsWith('/apiClient')?api:require(path.resolve(path.dirname(file),n)),getApp:()=>app,wx,console,Date,Map,Set,Promise,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},setInterval:()=>0,clearInterval(){}});
    return {...definition,...definition.methods,draftReady:true,userKey:require('../miniprogram/utils/draftScope').draftScope(app.globalData.session),draftKey:`visitConfirmV2:${require('../miniprogram/utils/draftScope').draftScope(app.globalData.session)}`,properties:{},data:{...clone(definition.data),canEditOpportunity:false},setData(v){Object.assign(this.data,v);},triggerEvent(){}};
  }
  return {storage,calls,api,app,make,timers,scope:require("../miniprogram/utils/draftScope").draftScope(app.globalData.session)};
}

for(const role of ['sales','fde'])for(const selected of [false,true])test(`${role} ${selected?'已选商机':'只关联客户'}从原文到确认、质检和归档使用同一可空ID`,async()=>{
  const h=harness(role),entry=h.make('pages/visit-entry/index.js');entry.onLoad({customerId:'c1'});await tick();
  entry.inputTranscript({detail:{value:'客户确认范围，下周由本人提交计划'}});
  if(selected)entry.opportunityChanged({detail:{customerId:'c1',opportunity:row,resolved:true}});
  assert.equal(entry.data.canSubmit,true);await entry.submitTranscript();
  assert.equal(h.calls.find(c=>c.stage==='structure').body.opportunity_id,selected?'o1':null);
  const source=h.storage.get(`visitStructuredV2:${h.scope}:customer:c1`);assert.equal(source.opportunityId,selected?'o1':'');
  const confirm=h.make('pages/visit-confirm/index.js');confirm.onLoad({draftContext:entry.draftContext});await tick();
  assert.equal(confirm.data.opportunityId,selected?'o1':'');assert.equal(confirm.data.opportunityEditing,false);
  await confirm.review();assert.equal(confirm.data.canSubmit,true);
  const quality=h.calls.find(c=>c.stage==='quality');assert.equal(quality.body.opportunity_id,selected?'o1':null);assert.equal(quality.body.opportunity_mutation,null);
  confirm.loadAdvice=()=>{};confirm.submitArchive();await tick();
  const saved=h.calls.find(c=>c.stage==='archive');assert.equal(saved.body.opportunity_id,selected?'o1':null);assert.equal(confirm.data.archived,true);
});

test('录入选择和清除持久化；换客户清空旧商机且拒绝旧客户事件',async()=>{
  const h=harness(),p=h.make('pages/visit-entry/index.js');p.onLoad({customerId:'c1'});await tick();p.inputTranscript({detail:{value:'拜访内容'}});
  p.opportunityChanged({detail:{customerId:'c1',opportunity:row,resolved:true}});assert.equal(h.storage.get(p.draftKey).opportunityId,'o1');
  p.opportunityChanged({detail:{customerId:'c1',opportunity:null,resolved:true,cleared:true}});assert.equal(p.data.canSubmit,true);assert.equal(h.storage.get(p.draftKey).opportunityId,'');
  p.opportunityChanged({detail:{customerId:'c1',opportunity:row,resolved:true}});p.confirmSelectedCustomer({id:'c2',name:'客户二'});
  assert.equal(p.data.opportunityId,'');assert.equal(p.data.opportunityName,'');assert.equal(p.data.canSubmit,true);
  p.opportunityChanged({detail:{customerId:'c1',opportunity:row,resolved:true}});assert.equal(p.data.opportunityId,'');
});

for(const role of ['sales','fde'])test(`${role}恢复录入草稿后重新解析关联，未解析ID不得送入结构化`,async()=>{
  const h=harness(role);h.storage.set(`visitEntryV2:${h.scope}`,{draftId:'d',customerId:'c1',opportunityId:'o1',opportunityName:'历史名称',transcript:'拜访内容'});
  const p=h.make('pages/visit-entry/index.js');p.onLoad();await tick();
  assert.equal(p.data.customerId,'c1');assert.equal(p.data.opportunityId,'o1');assert.equal(p.data.canSubmit,false);p.submitTranscript();assert.equal(h.calls.filter(c=>c.stage).length,0);
  p.opportunityChanged({detail:{customerId:'c1',opportunity:row,resolved:true}});assert.equal(p.data.canSubmit,true);
});

for(const role of ['sales','fde'])test(`${role}确认草稿显式空值不被结构化旧ID复活；只关联客户可质检`,async()=>{
  const h=harness(role);h.storage.set(`visitStructuredV2:${h.scope}`,{draftId:'d',runId:'structure',customerHintId:'c1',customerHint:'客户一',opportunityId:'o1',result:{fields}});
  h.storage.set(`visitConfirmV2:${h.scope}`,{draftId:'d',opportunityId:'',values:fields});
  const p=h.make('pages/visit-confirm/index.js');p.onLoad();await tick();assert.equal(p.data.opportunityId,'');await p.review();assert.equal(h.calls.find(c=>c.stage==='quality').body.opportunity_id,null);
});

test('FDE通用选项允许清除，改变关联使旧评分失效且永不生成商机mutation',async()=>{
  const h=harness('fde'),p=h.make('pages/visit-confirm/index.js');p.setData({isFde:true,customerId:'c1',customerName:'客户一',customerConfirmed:true,values:fields,sourceRunId:'structure',opportunityId:'o1'});
  await p.loadOpportunities();assert.equal(p.data.opportunityOptions.some(r=>r.id==='__new__'),false);assert.equal(p.data.opportunityEditing,false);
  await p.review();assert.equal(p.data.canSubmit,true);
  p.selectOpportunity({detail:{value:0}});assert.equal(p.data.opportunityId,'');assert.equal(p.data.reviewStale,true);assert.equal(p.data.canSubmit,false);
  await p.review();assert.equal(h.calls.filter(c=>c.stage==='quality').at(-1).body.opportunity_id,null);
  p.data.opportunityOptions.push({id:'__new__',name:'新建'});p.selectOpportunity({detail:{value:p.data.opportunityOptions.length-1}});assert.equal(p.data.opportunityId,'');
});

for(const stage of ['structure','quality','archive'])test(`服务端${stage}拒绝可读但不可写目标时保留草稿`,async()=>{
  const denied=Object.assign(Error('无权为该客户记录跟进'),{statusCode:403});
  const h=harness('fde'),entry=h.make('pages/visit-entry/index.js');entry.onLoad({customerId:'c1'});await tick();entry.inputTranscript({detail:{value:'客户原文必须保留'}});
  const original=h.api.submitVisitStage;
  h.api.submitVisitStage=async(s,body)=>{if(s===stage)throw denied;return original(s,body);};
  if(stage==='archive')h.api.createVisit=async()=>{throw denied;};
  await entry.submitTranscript();
  if(stage==='structure'){assert.match(entry.data.errorText,/无权/);assert.equal(h.storage.get(entry.draftKey).transcript,'客户原文必须保留');return;}
  const p=h.make('pages/visit-confirm/index.js');p.onLoad({draftContext:entry.draftContext});await tick();await p.review();
  if(stage==='archive'){p.submitArchive();await tick();assert.equal(p.data.archived,false);}
  assert.match(p.data.errorText,/无权/);assert.ok(h.storage.get(p.draftKey));assert.ok(h.storage.get(entry.draftKey));
});

test('通用选择器按客户分页搜索，精确恢复不限定本人参与，resolved不表示授权',async()=>{
  const h=harness('fde'),p=h.make('components/visit-opportunity-selector/index.js','Component'),events=[];p.properties={customerId:'c1',selectedId:''};p.triggerEvent=(_,e)=>events.push(e);
  h.api.listOpportunities=async q=>{h.calls.push(q);return {items:[{...row,id:q.offset?'o2':'o1'}],has_more:q.offset===0,next_offset:q.offset?null:20};};
  p.data.query='项目';await p.load();await p.more();assert.equal(h.calls[1].offset,20);assert.equal(h.calls[1].query,'项目');assert.equal(h.calls[1].scope,undefined);assert.equal(p.data.items.length,2);
  p.choose({currentTarget:{dataset:{id:'o2'}}});assert.equal(events.at(-1).resolved,true);assert.equal(events.at(-1).verified,undefined);
  p.properties.selectedId='outside';await p.checkSelection();assert.equal(p.data.selected.id,'outside');
});

test('选择器不可见或错客户的恢复保留ID；显式清除可继续客户级录入',async()=>{
  const h=harness(),p=h.make('components/visit-opportunity-selector/index.js','Component'),events=[];p.properties={customerId:'c1',selectedId:'private'};p.triggerEvent=(_,e)=>events.push(e);
  h.api.getOpportunityDetailOverview=async()=>({id:'c2',opportunities:[{...row,id:'private'}]});await p.checkSelection();assert.match(p.data.error,/不属于当前客户/);assert.equal(p.properties.selectedId,'private');assert.equal(events.at(-1).resolved,false);
  p.clear();assert.equal(events.at(-1).cleared,true);assert.equal(events.at(-1).resolved,true);
});

test('清除、换客户、身份切换和离页均作废选择器晚到响应',async()=>{
  for(const action of ['clear','customer','identity','detach']){
    let resolve;const h=harness(),p=h.make('components/visit-opportunity-selector/index.js','Component'),events=[];
    p.properties={customerId:'c1',selectedId:'o1'};p.triggerEvent=(_,e)=>events.push(e);h.api.getOpportunityDetailOverview=()=>new Promise(r=>resolve=r);
    const pending=p.checkSelection();
    if(action==='clear')p.clear();if(action==='customer'){p.properties={customerId:'c2',selectedId:''};p.checkSelection();}if(action==='identity')h.app.globalData.session.userId='other';if(action==='detach')p.lifetimes.detached.call(p);
    resolve({id:'c1',opportunities:[row]});await pending;assert.equal(p.data.selected,null,action);assert.equal(events.some(e=>e.opportunity),false,action);
  }
});

test('属性通知离开父更新栈，较早的延迟重置不能覆盖新选择',async()=>{
  const h=harness(),p=h.make('components/visit-opportunity-selector/index.js','Component'),events=[];p.properties={customerId:'c1',selectedId:''};p.triggerEvent=(_,e)=>events.push(e);
  p.notify(null,{resolved:false},true);p.data.items=[row];p.choose({currentTarget:{dataset:{id:'o1'}}});await tick();assert.equal(events.length,1);assert.equal(events[0].opportunity.id,'o1');
});

test('可选列表读取失败不阻断空关联，已有ID读取失败不能被静默清除',async()=>{
  const h=harness('fde',{listOpportunities:async()=>{throw Error('列表暂不可读');}}),p=h.make('pages/visit-confirm/index.js');
  p.setData({isFde:true,customerId:'c1',customerName:'客户一',customerConfirmed:true,values:fields,sourceRunId:'structure',opportunityId:''});
  await p.loadOpportunities();assert.equal(p.opportunityNeedsResolution(),false);await p.review();assert.equal(p.data.canSubmit,true);
  p.setData({opportunityId:'o1'});await p.loadOpportunities();assert.equal(p.data.opportunityId,'o1');assert.equal(p.opportunityNeedsResolution(),true);
  p.selectOpportunity({detail:{value:0}});assert.equal(p.data.opportunityId,'');assert.equal(p.opportunityNeedsResolution(),false);
});

test('客户恢复期间切换身份，旧客户回读不得覆盖新会话',async()=>{
  let resolve;const h=harness('fde',{getCustomerReference:()=>new Promise(r=>resolve=r)}),p=h.make('pages/visit-entry/index.js');
  const pending=p.loadLinkedCustomer('old');h.app.globalData.session.userId='other';
  resolve({id:'old',name:'旧客户'});await pending;assert.equal(p.data.customerId,'');assert.equal(p.data.customerConfirmed,false);
});


test('有编辑权限时选择已有商机仍仅关联，单次参与人进入质检与归档',async()=>{
  const h=harness(),p=h.make('pages/visit-confirm/index.js');
  Object.assign(p.data,{customerId:'c1',customerConfirmed:true,canEditOpportunity:true,values:fields,sourceRunId:'s'});
  await p.loadOpportunities();p.selectOpportunity({detail:{value:1}});
  assert.equal(p.data.opportunityEditing,false);
  p.changeVisitParticipants({detail:{members:[{id:'f1',name:'参与人'}],memberIds:['f1']}});
  await p.review();const quality=h.calls.find(c=>c.stage==='quality');
  assert.equal(quality.body.opportunity_mutation,null);assert.deepEqual(quality.body.fde_participant_ids,['f1']);
  p.loadAdvice=()=>{};p.submitArchive();await tick();
  const saved=h.calls.find(c=>c.stage==='archive');
  assert.equal(Object.hasOwn(saved.body,'_opportunity_mutation'),false);assert.deepEqual(Array.from(saved.participants),['f1']);
});

test('显式编辑可取消，关联模式改变参与人也必须重新质检',async()=>{
  const h=harness(),p=h.make('pages/visit-confirm/index.js');
  Object.assign(p.data,{customerId:'c1',customerConfirmed:true,canEditOpportunity:true,values:fields,sourceRunId:'s'});
  await p.loadOpportunities();p.selectOpportunity({detail:{value:1}});
  p.startOpportunityEdit();assert.equal(p.data.opportunityEditing,true);
  p.cancelOpportunityEdit();assert.equal(p.data.opportunityEditing,false);
  await p.review();assert.equal(p.data.canSubmit,true);
  p.changeVisitParticipants({detail:{members:[{id:'f2'}],memberIds:['f2']}});
  assert.equal(p.data.reviewStale,true);assert.equal(p.data.canSubmit,false);
  p.data.selectedOpportunity.capabilities={can_edit:false};p.startOpportunityEdit();assert.equal(p.data.opportunityEditing,false);
});

test('纯客户跟进可携带出席FDE，人员变更需重新质检，归档不创建商机',async()=>{
  const h=harness(),p=h.make('pages/visit-confirm/index.js');
  Object.assign(p.data,{customerId:'c1',customerConfirmed:true,values:fields,sourceRunId:'s'});
  await p.loadOpportunities();
  p.changeVisitParticipants({detail:{members:[{id:'f1',name:'出席人一'}],memberIds:['f1']}});
  await p.review();assert.equal(p.data.canSubmit,true);
  p.changeVisitParticipants({detail:{members:[{id:'f2',name:'出席人二'}],memberIds:['f2']}});
  assert.equal(p.data.reviewStale,true);assert.equal(p.data.canSubmit,false);
  p.loadAdvice=()=>{};p.submitArchive();await tick();
  assert.equal(h.calls.some(c=>c.stage==='archive'),false);
  await p.review();assert.equal(p.data.canSubmit,true);
  const quality=h.calls.filter(c=>c.stage==='quality').at(-1);
  assert.equal(quality.body.customer_id,'c1');assert.equal(quality.body.opportunity_id,null);
  assert.equal(quality.body.opportunity_mutation,null);assert.deepEqual(quality.body.fde_participant_ids,['f2']);
  p.submitArchive();await tick();
  const saved=h.calls.find(c=>c.stage==='archive');
  assert.equal(saved.customerId,'c1');assert.equal(saved.body.opportunity_id,null);
  assert.equal(Object.hasOwn(saved.body,'_opportunity_mutation'),false);
  assert.deepEqual(Array.from(saved.participants),['f2']);assert.equal(p.data.archived,true);
});

const followId='11111111-1111-4111-8111-111111111111',followCustomer='22222222-2222-4222-8222-222222222222';
function followAccess(){return {contract_version:1,opportunity_id:followId,name:'协作商机',stage_code:'proposal',status:'open',version_no:1,owner:{user_id:null,display_name:null,team_id:null,team_name:null},customer_reference:{id:followCustomer,name:'最小客户'},collaborators:[],capabilities:{can_transfer:false,can_manage_collaborators:false,can_follow_up:true,can_edit:false},capability_reasons:{transfer:'TRANSFER_NOT_READY'}};}
test('G1发现→最小引用→结构化→质检→归档保留同一商机且不请求客户详情或普通商机目录',async()=>{
 let accessReads=0;const h=harness('sales',{getCustomerReference:async()=>{throw Error('禁止客户详情');},listOpportunities:async()=>{throw Error('禁止普通目录');},getOpportunityAccess:async()=>{accessReads++;return followAccess();},listOpportunityFollowUpOptions:async({offset,limit})=>({contract_version:1,items:[followAccess()],total:1,offset,limit})});
 const entry=h.make('pages/visit-entry/index.js');entry.onLoad();await tick();await entry.openFollowUpDiscovery();assert.equal(entry.data.followUpItems.length,1);await entry.chooseFollowUp({currentTarget:{dataset:{id:followId}}});assert.equal(entry.data.customerName,'最小客户');assert.equal(entry.data.followUpOnly,true);
 entry.inputTranscript({detail:{value:'客户确认范围，下周由本人提交计划'}});await entry.submitTranscript();assert.equal(h.calls.find(c=>c.stage==='structure').body.opportunity_id,followId);
 const confirm=h.make('pages/visit-confirm/index.js');confirm.onLoad({draftContext:entry.draftContext});await tick();assert.equal(confirm.data.followUpOnly,true);assert.equal(confirm.canMutateOpportunity(),false);await confirm.review();assert.equal(confirm.data.canSubmit,true);
 const quality=h.calls.find(c=>c.stage==='quality');assert.equal(quality.body.opportunity_id,followId);assert.equal(quality.body.opportunity_mutation,null);confirm.loadAdvice=()=>{};await confirm.submitArchive();await tick();const saved=h.calls.find(c=>c.stage==='archive');assert.equal(saved.body.opportunity_id,followId);assert.equal(saved.body._opportunity_mutation,undefined);assert.equal(confirm.data.archived,true);assert.ok(accessReads>=5);
});
test('G1撤权阻止结构化且保留原文/关联；403不回退普通客户接口',async()=>{
 let revoked=false;const h=harness('sales',{getOpportunityAccess:async()=>{if(revoked)throw Object.assign(Error('权限已撤销'),{statusCode:403});return followAccess();}}),entry=h.make('pages/visit-entry/index.js');entry.onLoad({followUpOnly:'1',customerId:followCustomer,opportunityId:followId});await tick();entry.inputTranscript({detail:{value:'未提交的录音原文'}});revoked=true;await entry.submitTranscript();assert.equal(h.calls.filter(c=>c.stage).length,0);assert.equal(entry.data.transcript,'未提交的录音原文');assert.equal(entry.data.opportunityId,followId);assert.equal(entry.data.canSubmit,false);assert.match(entry.data.errorText,/撤销/);
});
for(const phase of ['quality','archive'])test(`G1 ${phase}前撤权阻止写入、保留确认草稿`,async()=>{
 let revoked=false;const h=harness('sales',{getOpportunityAccess:async()=>{if(revoked)throw Object.assign(Error('权限已撤销'),{statusCode:403});return followAccess();}}),entry=h.make('pages/visit-entry/index.js');entry.onLoad({followUpOnly:'1',customerId:followCustomer,opportunityId:followId});await tick();entry.inputTranscript({detail:{value:'客户确认范围，下周由本人提交计划'}});await entry.submitTranscript();const confirm=h.make('pages/visit-confirm/index.js');confirm.onLoad({draftContext:entry.draftContext});await tick();if(phase==='archive')await confirm.review();const previous=JSON.stringify(confirm.data.values);revoked=true;if(phase==='quality')await confirm.review();else await confirm.submitArchive();assert.equal(h.calls.filter(c=>c.stage===phase).length,0);assert.equal(JSON.stringify(confirm.data.values),previous);assert.equal(confirm.data.opportunityId,followId);assert.equal(confirm.data.canSubmit,false);
});
for(const oldId of [followId,'old-opportunity',''])test(`纯跟进来源覆盖旧确认模式和关联(${oldId||'空'})，保留正文但重新质检`,async()=>{
 let ordinary=0;const h=harness('sales',{getOpportunityAccess:async()=>followAccess(),listOpportunities:async()=>{ordinary++;return pageOf([]);}});
 h.storage.set(`visitStructuredV2:${h.scope}`,{draftId:'same',runId:'new-run',followUpOnly:true,customerHintId:followCustomer,customerHint:'最小客户',opportunityId:followId,result:{fields}});
 h.storage.set(`visitConfirmV2:${h.scope}`,{draftId:'same',sourceRunId:'old-run',followUpOnly:false,opportunityId:oldId,values:{...fields,follow_up_record:'人工保存正文'},customerId:followCustomer,customerName:'最小客户',reviewRunId:'old-review',reviewPayload:{opportunity_mutation:{name:'不得沿用'}},isFirstVisit:true});
 const p=h.make('pages/visit-confirm/index.js');p.onLoad();await tick();assert.equal(p.data.followUpOnly,true);assert.equal(p.data.opportunityId,followId);assert.equal(p.data.sourceRunId,'new-run');assert.equal(p.data.values.follow_up_record,'人工保存正文');assert.equal(p.data.reviewRunId,'');assert.equal(p.data.reviewPayload,null);assert.equal(p.data.isFirstVisit,false);assert.equal(ordinary,0);
});

test('公司客户跟进：恢复材料后以同一可空商机目标结构化、质检、归档且不带资料或商机写入',async()=>{
  const customer='11111111-1111-4111-8111-111111111111';let checked=0;
  const h=harness('sales',{getCustomerFollowUpAccess:async id=>{checked++;assert.equal(id,customer);return {customer_reference:{id,name:'公司客户'},can_follow_up:true};},getCustomerReference:async()=>{throw Error('不应读取完整客户目录');},runAgent:async()=>{throw Error('不应生成商机草稿');}});
  h.app.globalData.session.permissions={};h.app.globalData.session.capabilities={company_customer_follow_up:true};
  const entry=h.make('pages/visit-entry/index.js');entry.onLoad({customerId:customer});await tick();
  entry.setData({importId:'material-1',hasTranscription:true,isFirstVisit:true});entry.inputTranscript({detail:{value:'客户确认范围，下周由本人提交计划'}});
  assert.equal(entry.data.targetKind,'customer');assert.equal(entry.data.followUpOnly,true);assert.equal(entry.data.canSubmit,true);entry.persist();
  const restored=h.make('pages/visit-entry/index.js');restored.onLoad({customerId:customer});await tick();
  assert.equal(restored.data.importId,'material-1');assert.equal(restored.data.followUpResolved,true);assert.equal(restored.data.isFirstVisit,false);
  await restored.submitTranscript();const structure=h.calls.find(c=>c.stage==='structure');assert.equal(structure.body.customer_id,customer);assert.equal(structure.body.opportunity_id,null);assert.equal(structure.body.is_first_visit,false);assert.equal(structure.body.source_import_id,'material-1');
  const confirm=h.make('pages/visit-confirm/index.js');confirm.onLoad({draftContext:restored.draftContext});await tick();
  confirm.data.values.customer_main_business='不应写客户资料';confirm.data.isFirstVisit=true;await confirm.review();
  const quality=h.calls.find(c=>c.stage==='quality');assert.equal(quality.body.customer_id,customer);assert.equal(quality.body.opportunity_id,null);assert.equal(quality.body.fields.is_first_visit,false);assert.equal(quality.body.fields.customer_main_business,'');assert.equal(quality.body.opportunity_mutation,null);assert.deepEqual(quality.body.fde_participant_ids,[]);assert.deepEqual(quality.body.collaborator_ids,[]);
  confirm.loadAdvice=()=>{};await confirm.submitArchive();await tick();const archive=h.calls.find(c=>c.stage==='archive');assert.equal(archive.customerId,customer);assert.equal(archive.body.opportunity_id,null);assert.equal(Object.hasOwn(archive.body,'_opportunity_mutation'),false);assert.equal(confirm.data.archived,true);assert.ok(checked>=5);
});

test('公司客户跟进：独立历史列表按读能力分页，账号切换丢弃迟到结果且保留原FDE宿主',async()=>{
  let release;const calls=[];const h=harness('sales',{listVisitRange:options=>{calls.push(options);return new Promise(r=>release=r);}});
  h.app.globalData.session.permissions={};h.app.globalData.session.capabilities={customer_claim_visit_read:true};
  const p=h.make('pages/fde-records/index.js');p.onLoad({mode:'authorized'});const pending=p.onShow();assert.equal(calls[0].scope,'all');assert.equal(calls[0].page_size,20);h.app.globalData.session.userId='other';release({items:[{id:'old',customer_name:'不可残留'}],has_more:false,summary:{total:1}});await pending;assert.equal(p.data.records.length,0);
  h.api.listVisitRange=async options=>({items:[{id:'v',customer_id:'c',customer_name:'可见客户',follow_up_record:'正式记录',can_open_customer:false}],has_more:false,summary:{total:1}});await p.onShow();assert.equal(p.data.records.length,1);p.onHide();assert.equal(p.data.records.length,0);
  const legacy=h.make('pages/fde-records/index.js'),context={scope:'self',year:2026,quarters:[4]};legacy.onLoad({context:encodeURIComponent(JSON.stringify(context))});legacy.onShow();assert.equal(legacy.data.authorized,false);assert.equal(legacy.data.context.scope,'self');assert.equal(calls.length,1);
});
