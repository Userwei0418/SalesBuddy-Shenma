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
