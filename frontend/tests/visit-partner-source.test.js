require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const snapshot = require('../miniprogram/utils/visitSnapshot');
const {draftScope} = require('../miniprogram/utils/draftScope');
const filename = path.resolve(__dirname, '../miniprogram/pages/visit-confirm/index.js');
const session = {workspaceId:'w',userId:'u',role:'sales',userName:'测试销售'};
const scope = draftScope(session);
const draftKey = `visitConfirmV2:${scope}`;
const sourceKey = `visitStructuredV2:${scope}`;
const fields = {follow_up_record:'客户确认试点范围',next_action:'2026年9月25日销售发送方案',contact_name:'测试联系人',partner_name:'未关联时的伙伴'};
const first = {id:'first',name:'商机甲',customer_id:'customer',sales_channel:'partner',partner_id:'p1',partner_name:'伙伴甲'};
const second = {id:'second',name:'商机乙',customer_id:'customer',sales_channel:'partner',partner_id:'p2',partner_name:'伙伴乙'};
const flush = () => new Promise(resolve => setImmediate(resolve));
function setup({memory=new Map(),history}={}) {
  if(!memory.has(sourceKey))memory.set(sourceKey,{draftId:'entry',runId:'structured',customerHintId:'customer',customerHint:'测试客户',result:{fields}});
  const requests=[],archives=[],patches=[];
  const api={
    request:async request=>{if(request.method==='PATCH'){patches.push(request.data);return {...history,...request.data,version_no:2};}return request.path.startsWith('/visits/')?history:{items:[]};},
    listOpportunities:async()=>({items:[first,second],has_more:false,next_offset:null}),
    submitVisitStage:async(stage,payload)=>{requests.push(payload);return {run_id:'review'};},
    waitVisitRun:async()=>({result:{visit_stage:'quality',quality_review:{follow_up_score:85,next_action:{passed:true}}}}),
    createVisit:async(customerId,values)=>{archives.push({customerId,values});return {id:'archived',fields:values,quality_review:{follow_up_score:85,next_action:{passed:true}}};},
  };
  let page;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,
    require:name=>name.endsWith('/apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    getApp:()=>({ensureLogin:()=>true,guardPage:()=>true,globalData:{session}}),
    wx:{getStorageSync:key=>structuredClone(memory.get(key)),setStorageSync:(key,value)=>memory.set(key,structuredClone(value)),removeStorageSync:key=>memory.delete(key),pageScrollTo(){},showToast(){},setNavigationBarTitle(){}},
    setInterval,clearInterval,setTimeout,clearTimeout,
  });
  page.data=structuredClone(page.data);
  page.setData=patch=>{for(const [key,value] of Object.entries(patch)){const parts=key.split('.');let obj=page.data;for(const part of parts.slice(0,-1))obj=obj[part];obj[parts.at(-1)]=value;}};
  page.loadBusinessOptions=()=>{};
  page.loadAdvice=()=>{};
  page.selectComponent=()=>({prepare:async()=>({action:page.data.opportunityId==='__new__'?'create':'update',name:'测试商机',sales_channel:(page.data.opportunityDraft||{}).partner_mode||'partner'})});
  page.onLoad(history?{visitId:'historical'}:{});
  return {page,memory,api,requests,archives,patches};
}
function choose(page,id){page.chooseOpportunity({currentTarget:{dataset:{id}}});}
function form(page,values,opportunityId=page.data.opportunityId){page.changeOpportunityForm({detail:{customerId:page.data.customerId,opportunityId,form:values}});}

test('不关联商机保留选填伙伴，空值也可质检；原文和草稿字段不被派生值覆盖',async()=>{
  const h=setup();await flush();choose(h.page,'');await h.page.review();
  assert.equal(h.requests[0].fields.partner_name,fields.partner_name);
  h.page.inputField({currentTarget:{dataset:{key:'partner_name'}},detail:{value:''}});
  await h.page.review();assert.equal(h.requests[1].fields.partner_name,'');
  assert.equal(h.memory.get(sourceKey).result.fields.partner_name,fields.partner_name);
  assert.equal(h.memory.get(draftKey).values.partner_name,'');
});

test('关联商机的伙伴同步进入质检和最终归档，不提交隐藏的旧输入',async()=>{
  const h=setup();await flush();choose(h.page,'first');await h.page.review();
  assert.equal(h.requests[0].fields.partner_name,'伙伴甲');assert.equal(h.page.data.canSubmit,true);
  h.page.submitArchive();await flush();assert.equal(h.archives[0].values.partner_name,'伙伴甲');
  assert.equal(h.archives[0].values._quality_review_run_id,'review');assert.equal(h.page.data.archived,true);
});

test('新建商机复用表单伙伴，直销和清空都会更新质检快照',async()=>{
  const h=setup();await flush();choose(h.page,'__new__');
  form(h.page,{name:'新项目',partner_mode:'partner',partner_id:'p2',partner_name:'伙伴乙'});
  await h.page.review();assert.equal(h.requests[0].fields.partner_name,'伙伴乙');
  form(h.page,{name:'新项目',partner_mode:'direct',partner_name:'残留名称'});
  assert.equal(h.page.data.reviewStale,true);assert.equal(h.page.data.canSubmit,false);assert.equal(snapshot.fields(h.page.data).partner_name,'直销');
  form(h.page,{name:'新项目',partner_mode:'partner',partner_name:''});
  assert.equal(snapshot.fields(h.page.data).partner_name,'');
});

test('已质检后变更商机伙伴立即禁止归档，重新质检使用新的值',async()=>{
  const h=setup();await flush();choose(h.page,'first');await h.page.review();
  form(h.page,{name:first.name,partner_mode:'partner',partner_id:'p2',partner_name:'伙伴乙'});
  assert.equal(h.page.data.canSubmit,false);h.page.submitArchive();assert.equal(h.archives.length,0);
  await h.page.review();assert.equal(h.requests[1].fields.partner_name,'伙伴乙');assert.equal(h.page.data.canSubmit,true);
});

test('切换商机和取消关联互不串值，恢复草稿仍保留独立的未关联输入',async()=>{
  const h=setup();await flush();choose(h.page,'first');
  form(h.page,{name:first.name,partner_mode:'partner',partner_id:'p3',partner_name:'草稿伙伴'});
  choose(h.page,'second');assert.equal(snapshot.fields(h.page.data).partner_name,'伙伴乙');
  choose(h.page,'');assert.equal(snapshot.fields(h.page.data).partner_name,fields.partner_name);
  choose(h.page,'first');assert.equal(snapshot.fields(h.page.data).partner_name,'草稿伙伴');
  const reopened=setup({memory:h.memory});await flush();
  assert.equal(snapshot.fields(reopened.page.data).partner_name,'草稿伙伴');
  choose(reopened.page,'');assert.equal(snapshot.fields(reopened.page.data).partner_name,fields.partner_name);
});

test('迟到表单及隐藏输入事件不能改写当前商机或未关联伙伴',async()=>{
  const h=setup();await flush();choose(h.page,'first');choose(h.page,'second');
  form(h.page,{partner_mode:'partner',partner_name:'迟到伙伴'},'first');
  h.page.inputField({currentTarget:{dataset:{key:'partner_name'}},detail:{value:'隐藏输入'}});
  assert.equal(snapshot.fields(h.page.data).partner_name,'伙伴乙');assert.equal(h.page.data.values.partner_name,fields.partner_name);
});

test('只读商机及FDE选择复用接口伙伴，不借用手工输入或旧表单',async()=>{
  const h=setup();await flush();h.page.data.isFde=true;h.page.data.canEditOpportunity=false;
  h.page.fdeOpportunityChanged({detail:{opportunity:first,verified:true}});
  assert.equal(h.page.data.linkedPartnerName,'伙伴甲');assert.equal(snapshot.fields(h.page.data).partner_name,'伙伴甲');
  h.page.fdeOpportunityChanged({detail:{opportunity:{...second,sales_channel:'direct',partner_name:null},verified:true}});
  assert.equal(snapshot.fields(h.page.data).partner_name,'直销');
});

test('伙伴未知、商机未核对时不从原文或其他商机猜值',()=>{
  const base={values:fields,opportunityId:'second',selectedOpportunity:first,opportunityDraft:{partner_name:'旧草稿'},opportunityEditing:false};
  assert.equal(snapshot.fields(base).partner_name,'');
  assert.equal(snapshot.fields({...base,selectedOpportunity:{...second,partner_name:null,sales_channel:'unknown'}}).partner_name,'');
});

test('重新读取商机伙伴变化会使原质检过期，不能沿用原审批结果',async()=>{
  const h=setup();await flush();choose(h.page,'first');await h.page.review();
  h.api.listOpportunities=async()=>({items:[{...first,partner_name:'服务器更新伙伴'}],has_more:false,next_offset:null});
  await h.page.loadOpportunities();assert.equal(h.page.data.linkedPartnerName,'服务器更新伙伴');assert.equal(h.page.data.canSubmit,false);
});

test('历史跟进补充保留历史伙伴，不能自动覆盖为商机当前伙伴',async()=>{
  const history={...fields,partner_name:'历史伙伴',customer_id:'customer',customer_name:'测试客户',opportunity_id:'first',version_no:1};
  const h=setup({history});await flush();
  h.page.data.opportunityId='first';h.page.data.selectedOpportunity=first;
  assert.equal(snapshot.fields(h.page.data).partner_name,'历史伙伴');
  h.page.saveSupplement();await flush();assert.equal(h.patches[0].partner_name,'历史伙伴');
});

test('界面只在未关联或历史补充时提供独立伙伴输入，其余复用商机表单或只读值',()=>{
  const wxml=fs.readFileSync(path.join(path.dirname(filename),'index.wxml'),'utf8');
  assert.match(wxml,/wx:if="\{\{editing \|\| !opportunityId\}\}"[^\n]*data-key="partner_name"/);
  assert.match(wxml,/随关联商机，无需重复填写/);
});
