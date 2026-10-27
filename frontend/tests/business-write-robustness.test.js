const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
require('./helpers/business-options');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const fields=()=>Object.entries({customer_name:'客户甲',industry:'企业软件',customer_type:'潜在客户',level_code:'Tier-1',lead_source:'销售自拓',target_team:'北区',assigned_team:'北区',assigned_sales:'销售甲',first_action:'确认需求',partner_name:'伙伴',contact_name:'联系人',contact_title:'经理',contact_role:'使用者'}).map(([key,value])=>({key,value}));
function harness(name,api={}){
 api={getPendingOpportunitySave:()=>null,canRetryOpportunitySave:()=>false,getCompanyPresentation:async()=>({opportunity_amount:{definition:{schema_version:1,warning_threshold_wan:1000}}}),...api};
 let page;const session={workspaceId:'company',userId:'owner',role:'sales',permissionVersion:'p1'},modals=[],toasts=[],storage=new Map(),timers=[],nav=[];
 const filename=path.resolve(__dirname,`../miniprogram/pages/${name}/index.js`);
 const app={ensureLogin:()=>true,globalData:{session}};let platform;
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:value=>page=value,getApp:()=>app,
   require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),n)),
   wx:platform={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
     showModal:m=>modals.push(m),showToast:t=>toasts.push(t),setNavigationBarTitle(){},navigateBack:()=>nav.push('back'),pageScrollTo(){},vibrateShort(){}},
   setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},setInterval:()=>1,clearInterval(){}});
 page.data=structuredClone(page.data);page.setData=patch=>{for(const [key,value]of Object.entries(patch)){const parts=key.split('.');let dst=page.data;for(const part of parts.slice(0,-1))dst=dst[part]||(dst[part]={});dst[parts.at(-1)]=value;}};
 page.setData({loading:false,creationOptionsReady:true,fields:fields(),missingCount:0,customerId:'customer-a',customerName:'客户甲'});
 page.draftKey='draft:company:owner';page.pendingDraftKey='pending:company:owner';
 page.members=[{name:'销售甲',account_code:'SALESA',team:'北区'}];
 page.selectComponent=()=>({prepare:async()=>({action:'create',name:'新商机',amount:100000})});
 return {page,session,modals,toasts,storage,timers,nav,wx:platform};
}

test('客户创建重复确认只发一次，切账号前未确认不能提交旧表单',async()=>{
 const pending=deferred(),calls=[];const h=harness('customer-create',{createCustomer:p=>{calls.push(p);return pending.promise;}});
 h.page.submitCustomer();h.page.submitCustomer();assert.equal(h.modals.length,1);
 h.modals[0].success({confirm:true});h.modals[0].success({confirm:true});assert.equal(calls.length,1);
 h.session.userId='other';pending.resolve({id:'created-old'});await tick();assert.equal(h.storage.has('lastCreatedCustomer'),false);assert.equal(h.toasts.length,0);
 const before=harness('customer-create',{createCustomer:async()=>assert.fail('stale request')});
 before.page.submitCustomer();before.session.workspaceId='other';before.modals[0].success({confirm:true});await tick();assert.equal(before.toasts.length,0);
});

test('客户创建确认文案后编辑内容必须重新确认，迟到失败不解锁新账号提交',async()=>{
 const h=harness('customer-create',{createCustomer:async()=>assert.fail('changed confirmation')});h.page.submitCustomer();h.page.data.fields[0].value='客户乙';h.modals[0].success({confirm:true});assert.equal(h.page.data.submitting,false);
 const first=deferred(),second=deferred();let calls=0;
 const race=harness('customer-create',{createCustomer:()=>++calls===1?first.promise:second.promise});
 race.page.submitCustomer();race.modals[0].success({confirm:true});race.session.userId='new';race.page.setData({submitting:false});
 race.page.submitCustomer();race.modals[1].success({confirm:true});first.reject(Error('old network'));await tick();assert.equal(race.page.data.submitting,true);assert.equal(race.toasts.length,0);
 second.resolve({id:'new-created'});await tick();assert.equal(race.storage.get('lastCreatedCustomer').ownerUserId,'new');
});

test('客户建档与申请均不把登录公司信息当作客户外部编号，申请保持待审批语义',async()=>{
 for(const requestMode of [false,true]){
  const calls=[];
  const h=harness('customer-create',{
   createCustomer:async payload=>{assert.equal(requestMode,false);calls.push(payload);return{id:'created'};},
   createCustomerCreationRequest:async payload=>{assert.equal(requestMode,true);calls.push(payload);return{id:'request',status:'pending'};},
  });
  Object.assign(h.session,{companyCode:'LOGIN-COMPANY',companyName:'登录公司'});
  h.page.setData({requestMode});h.storage.set(h.page.draftKey,{fields:fields()});
  h.page.submitCustomer();h.modals[0].success({confirm:true});await tick();
  assert.equal(calls.length,1);assert.equal(calls[0].name,'客户甲');
  assert.equal(Object.hasOwn(calls[0],'company_reference'),false);
  assert.equal(h.storage.has(h.page.draftKey),false);
  assert.equal(h.storage.has('lastCreatedCustomer'),!requestMode);
  if(requestMode)assert.equal(h.toasts[0].title,'申请已提交，等待运营审批');
 }
});

test('建档并下发的第一步完成后切账号，不使用新账号发第二步，原账号保留建档回执',async()=>{
 const pending=deferred(),assignments=[];
 const h=harness('customer-assign-confirm',{createCustomer:()=>pending.promise,assignCustomer:(...args)=>{assignments.push(args);return Promise.resolve({});}});
 h.page.confirmArchive();h.modals[0].success({confirm:true});h.session.userId='new';
 pending.resolve({id:'created-original'});await tick();
 assert.equal(assignments.length,0);assert.equal(h.storage.get(h.page.draftKey).createdCustomerId,'created-original');
 assert.equal(h.storage.get(h.page.draftKey).ownerUserId,'owner');assert.equal(h.toasts.length,0);
});

test('建档并下发正常重试仅补第二步，确认重复回调不重复建档或下发',async()=>{
 let creates=0,assigns=0;const h=harness('customer-assign-confirm',{createCustomer:async()=>{creates++;return{id:'created'};},assignCustomer:async()=>{if(++assigns===1)throw Error('暂不可用');return{customer_id:'created'};}});
 h.page.confirmArchive();h.modals[0].success({confirm:true});h.modals[0].success({confirm:true});await tick();
 assert.equal(creates,1);assert.equal(assigns,1);assert.equal(h.page.createdCustomerId,'created');
 h.page.confirmArchive();h.modals[1].success({confirm:true});await tick();assert.equal(creates,1);assert.equal(assigns,2);
 assert.equal(h.storage.get('lastManagementCustomerSuccess').ownerUserId,'owner');
});

test('客户编辑写入锁定客户ID，换客户或销毁后的回执不写跳转标记',async()=>{
 for(const change of [h=>h.page.data.customerId='customer-b',h=>h.session.userId='other',h=>h.page.onUnload()]){
  const pending=deferred(),calls=[];const h=harness('customer-edit',{updateCustomer:(...args)=>{calls.push(args);return pending.promise;}});
  h.page.data.form={industry:'软件',customer_type:'潜在客户',level_code:'Tier-1',source:'自拓',partner_name:'伙伴'};
  h.page.submit();change(h);pending.resolve({id:'customer-a'});await tick();
  assert.equal(calls[0][0],'customer-a');assert.equal(h.storage.has('pendingOpenCustomerId'),false);assert.equal(h.toasts.length,0);
 }
});

test('商机表单准备期间更换客户不产生串客户写入，旧finally不解锁新操作',async()=>{
 const prepare=deferred(),posts=[];const h=harness('opportunity-create',{createOpportunity:(...args)=>{posts.push(args);return Promise.resolve({});}});
 h.page.selectComponent=()=>({prepare:()=>prepare.promise});const action=h.page.submit();h.page.data.customerId='customer-b';prepare.resolve({name:'客户甲商机'});await action;assert.equal(posts.length,0);
 const first=deferred(),second=deferred();let n=0;
 const race=harness('opportunity-create',{createOpportunity:()=>++n===1?first.promise:second.promise});
 const old=race.page.submit();await tick();race.session.userId='new';race.page.setData({busy:false});const current=race.page.submit();await tick();
 first.reject(Error('old'));await old;assert.equal(race.page.data.busy,true);assert.equal(race.page.data.error,'');
 second.resolve({id:'00000000-0000-4000-8000-000000000001',changed:true,version_no:1,event_id:'00000000-0000-4000-8000-000000000002'});await current;assert.equal(race.storage.get('pendingOpenOpportunityId'),'00000000-0000-4000-8000-000000000001');
});

test('保存中切后台保留写锁，收到本账号成功后更新结果，不迟到导航其他页面',async()=>{
 const pending=deferred();const h=harness('opportunity-create',{createOpportunity:()=>pending.promise});
 const action=h.page.submit();await tick();h.page.onHide();pending.resolve({id:'00000000-0000-4000-8000-000000000001',changed:true,version_no:1,event_id:'00000000-0000-4000-8000-000000000002'});await action;
 assert.equal(h.page.data.existing.id,'00000000-0000-4000-8000-000000000001');h.timers.forEach(fn=>fn());assert.equal(h.nav.length,0);assert.equal(h.toasts.length,0);
});

function visitHarness(api){
 const h=harness('visit-confirm',api);h.page.setData({canSubmit:true,sourceRunId:'structure',reviewRunId:'quality',isFde:false,
  reviewPayload:{fields:{follow_up_record:'本次沟通',next_action:'下次推进'},collaborator_ids:[]},quality:{follow_up_score:85},values:{follow_up_record:'本次沟通',next_action:'下次推进'}});
 h.page.entryDraftId='draft-a';h.page.persist=()=>true;h.page.refresh=()=>{};h.page.refreshGate=()=>{};h.page.closeOpportunityPicker=()=>{};h.page.invalidateOpportunityReads=()=>{};
 h.page.userKey='company:owner';return h;
}
test('拜访归档确认后客户或质检版本变化不提交，重复确认只有一次归档',async()=>{
 for(const change of [h=>h.page.data.customerId='customer-b',h=>h.page.data.reviewRunId='quality-new',h=>h.session.userId='new']){
  const h=visitHarness({createVisit:async()=>assert.fail('stale confirmation')});h.page.archive();change(h);h.modals[0].success({confirm:true});await tick();
 }
 let calls=0;const pending=deferred();const h=visitHarness({createVisit:()=>{calls++;return pending.promise;},requestBusinessAdvice:async()=>({id:'advice'})});
 h.page.archive();h.page.archive();await tick();assert.equal(h.modals.length,1);h.modals[0].success({confirm:true});h.modals[0].success({confirm:true});assert.equal(calls,1);
 pending.resolve({id:'visit-a',fields:{}});await tick();assert.equal(h.page.data.archived,true);
});
test('旧账号的归档和补充回执不清新账号草稿、不触发新账号AI建议',async()=>{
 const pending=deferred();let advice=0;
 const h=visitHarness({createVisit:()=>pending.promise,requestBusinessAdvice:async()=>{advice++;return{id:'wrong'};}});
 h.storage.set(h.page.draftKey,{preserve:true});h.page.submitArchive();h.session.userId='new';pending.resolve({id:'old-visit',fields:{}});await tick();
 assert.equal(h.page.data.archived,false);assert.equal(h.storage.has(h.page.draftKey),true);assert.equal(advice,0);
 const supplement=deferred();const edit=visitHarness({request:()=>supplement.promise});edit.page.setData({visitId:'visit-a',version:3});
 edit.page.saveSupplement();edit.page.data.visitId='visit-b';supplement.resolve({version_no:4});await tick();assert.equal(edit.page.data.version,3);assert.equal(edit.toasts.length,0);
});
test('待办建议迟到不能覆盖另一条归档记录，也不能解除另一条记录的生成锁',async()=>{
 const first=deferred(),second=deferred();let calls=0;
 const h=visitHarness({requestBusinessAdvice:()=>++calls===1?first.promise:second.promise});h.page.setData({archived:true,visitId:'a'});
 const old=h.page.loadAdvice();h.page.setData({visitId:'b',adviceBusy:false});const current=h.page.loadAdvice();
 first.resolve({id:'old-advice'});await old;assert.equal(h.page.data.adviceBusy,true);assert.equal(h.page.data.advice,null);
 second.resolve({id:'current-advice',subject_kind:'visit',subject_id:'b',section:'tasks',status:'queued'});await current;assert.equal(h.page.data.advice.id,'current-advice');assert.equal(h.page.data.adviceBusy,false);
});


test('客户加载失败和更新回执在隐藏期间不会提示或返回其它页面',async()=>{
 const load=deferred();const h=harness('customer-edit',{getBusinessOptions:async()=>({}),getCustomerOverview:()=>load.promise});
 const request=h.page.loadCustomer();h.page.onHide();load.reject(Error('不存在'));await request;h.timers.forEach(fn=>fn());
 assert.equal(h.nav.length,0);assert.equal(h.toasts.length,0);
 const save=deferred();const edit=harness('customer-edit',{updateCustomer:()=>save.promise});
 edit.page.data.form={industry:'软件',customer_type:'潜在客户',level_code:'Tier-1',source:'自拓',partner_name:'伙伴'};
 edit.page.submit();edit.page.onHide();save.resolve({});await tick();edit.timers.forEach(fn=>fn());
 assert.equal(edit.toasts.length,0);assert.equal(edit.nav.length,0);assert.equal(edit.page.data.saving,false);
});

test('客户或商机重载过程中不解除仍在进行的保存锁',async()=>{
 const save=deferred(),load=deferred();let calls=0;
 const h=harness('customer-edit',{getBusinessOptions:async()=>({}),getCustomerOverview:()=>load.promise,updateCustomer:()=>{calls++;return save.promise;}});
 h.page.data.form={industry:'软件',customer_type:'潜在客户',level_code:'Tier-1',source:'自拓',partner_name:'伙伴'};
 h.page.submit();h.page.loadCustomer();assert.equal(h.page.data.saving,true);h.page.submit();assert.equal(calls,1);
 save.resolve({});await tick();assert.equal(h.page.data.saving,false);
 const post=deferred(),read=deferred();const op=harness('opportunity-create',{createOpportunity:()=>post.promise,getCustomerReference:()=>read.promise});
 const request=op.page.submit();await tick();op.page.load();assert.equal(op.page.data.busy,true);
 post.resolve({id:'00000000-0000-4000-8000-000000000001',changed:true,event_id:'00000000-0000-4000-8000-000000000002',version_no:1});await request;assert.equal(op.page.data.busy,false);
});

test('客户隐藏期间创建成功后不得再次建档，返回页时完成原来的返回动作',async()=>{
 for(const name of ['customer-create','customer-assign-confirm']){
  const saved=deferred();let count=0;
  const h=harness(name,{createCustomer:()=>{count++;return saved.promise;},assignCustomer:async()=>({customer_id:'created'})});
  const submit=()=>name==='customer-create'?h.page.submitCustomer():h.page.confirmArchive();
  submit();h.modals[0].success({confirm:true});h.page.onHide();saved.resolve({id:'created'});await tick();
  submit();assert.equal(count,1);assert.equal(h.modals.length,1);assert.equal(h.toasts.length,0);
  h.timers.forEach(fn=>fn());assert.equal(h.nav.length,0);h.page.onShow();assert.equal(h.nav.length,1);
 }
});

test('客户语音转写后切账号不能继续发起Agent或回填另一账号表单',async()=>{
 for(const stopAt of ['transcription','agent']){
  const pending=deferred();let agents=0,started=0;const callbacks={};
  const h=harness('customer-create',{transcribeAudio:()=>stopAt==='transcription'?pending.promise:Promise.resolve({text:'旧账号录音'}),runAgent:()=>{agents++;return pending.promise;}});
  h.wx.getRecorderManager=()=>({onStart:fn=>callbacks.start=fn,onStop:fn=>callbacks.stop=fn,onError:fn=>callbacks.error=fn,start:()=>{started++;callbacks.start();}});
  h.page.requestRecordPermission=granted=>granted();h.page.session=h.session;
  h.page.toggleVoice();assert.equal(started,1);callbacks.stop({tempFilePath:'audio',duration:2000});await tick();
  const before=JSON.stringify(h.page.data.fields);h.session.userId='new';
  pending.resolve(stopAt==='transcription'?{text:'旧账号录音'}:{result:{customer_name:'串账号的客户'}});await tick();
  assert.equal(agents,stopAt==='transcription'?0:1);assert.equal(JSON.stringify(h.page.data.fields),before);assert.equal(h.toasts.length,0);
 }
});


test('未提交的客户建档/下发/拜访归档确认在隐藏后作废，不发新写请求',async()=>{
 for(const name of ['customer-create','customer-assign-confirm','visit-confirm']){
  let calls=0;const api={createCustomer:async()=>{calls++;return{id:'wrong'};},createVisit:async()=>{calls++;return{id:'wrong'};}};
  const h=name==='visit-confirm'?visitHarness(api):harness(name,api);
  if(name==='customer-create')h.page.submitCustomer();else if(name==='customer-assign-confirm')h.page.confirmArchive();else h.page.archive();
  h.page.onHide();h.modals[0].success({confirm:true});await tick();assert.equal(calls,0);
 }
 const prepared=deferred();let posts=0;const h=harness('opportunity-create',{createOpportunity:async()=>{posts++;}});
 h.page.selectComponent=()=>({prepare:()=>prepared.promise});const request=h.page.submit();h.page.onHide();prepared.resolve({action:'update'});await request;
 assert.equal(posts,0);assert.equal(h.page.data.busy,false);
});

test('拜访切账号后等待新同事目录时保留新草稿的协同人，不被旧目录剔除',async()=>{
 const directory=deferred();const h=harness('visit-confirm',{request:()=>directory.promise});
 h.page.loadBusinessOptions=()=>{};h.page.loadOpportunities=()=>{};
 h.page.writeIdentity=require('../miniprogram/utils/access').identity(h.session);
 h.page.confirmOptions={};h.page.confirmInitialized=true;h.page.allColleagues=[{id:'old-person',name:'旧公司同事'}];
 h.session.userId='new';h.storage.set('visitConfirmV2:company:new',{values:{follow_up_record:'新账号内容'},customerId:'c',customerName:'客户',collaboratorIds:['new-person']});
 h.page.onShow();assert.deepEqual(Array.from(h.page.data.collaboratorIds),['new-person']);assert.equal(h.page.allColleagues,undefined);
 directory.resolve({items:[{id:'new-person',name:'新公司同事'}]});await tick();
 assert.deepEqual(Array.from(h.page.data.collaboratorIds),['new-person']);assert.equal(h.page.data.collaboratorNames,'新公司同事');
});

test('同一待办建议的旧回读不能覆盖返回后已采纳状态',async()=>{
 const old=deferred(),latest=deferred();let count=0;
 const h=visitHarness({getLatestBusinessAdvice:()=>++count===1?old.promise:latest.promise});h.page.setData({archived:true,visitId:'v',advice:{id:'advice',status:'pending'}});
 const first=h.page.reloadAdvice();h.page.onHide();h.page.onShow();
 latest.resolve({id:'advice',subject_kind:'visit',subject_id:'v',section:'tasks',status:'succeeded',summary:'最新建议',suggestions:[{id:'s',decision:'accepted'}]});await tick();old.resolve({id:'advice',status:'pending'});await first;
 assert.equal(h.page.data.advice.suggestions[0].decision,'accepted');
});

test('拜访质检的商机确认在隐藏后返回，不启动新的质检请求',async()=>{
 const prepared=deferred();let posts=0;const h=visitHarness({submitVisitStage:async()=>{posts++;}});
 h.page.setData({customerConfirmed:true,opportunityEditing:true,values:{follow_up_record:'沟通内容',next_action:'明天继续',interaction_at:'2026-09-27',created_date:'2026-09-27',contact_name:'王经理'}});
 h.page.selectComponent=()=>({prepare:()=>prepared.promise});const request=h.page.review();h.page.onHide();prepared.resolve(null);await request;
 assert.equal(posts,0);assert.equal(h.page.preparing,false);
});

test('拜访客户搜索旧账号失败不覆盖新请求，新账号名单正常恢复',async()=>{
 const old=deferred(),current=deferred();let calls=0;
 const h=visitHarness({listCustomers:()=>++calls===1?old.promise:current.promise});h.page.setData({customerQuery:'公司',errorText:''});
 const first=h.page.searchCustomers();h.session.userId='new';const second=h.page.searchCustomers();
 old.reject(Error('旧账号无权限'));await first;assert.equal(h.page.data.searching,true);assert.equal(h.page.data.errorText,'');
 current.resolve({items:[{id:'new-customer'}]});await second;assert.equal(h.page.data.searching,false);assert.equal(h.page.data.customers[0].id,'new-customer');assert.equal(h.toasts.length,0);
});
