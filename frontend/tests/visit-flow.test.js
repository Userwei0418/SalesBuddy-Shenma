require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flow = require('../miniprogram/utils/visitFlow');
const values = {follow_up_record:'同意试点',next_action:'下周一销售提交方案',interaction_at:'2026-09-10',created_date:'2026-09-10',contact_name:'客户经理'};
test('质量分必须高于60，正文修改和未确认客户均不能绕过',()=>{
  const q={follow_up_score:61,next_action:{passed:true}};
  assert.equal(flow.canArchive(values,'customer',q,false,'review'),true);
  assert.equal(flow.canArchive(values,'customer',{...q,follow_up_score:60},false,'review'),false);
  assert.equal(flow.canArchive(values,'customer',q,true,'review'),false);
  assert.equal(flow.canArchive(values,'',q,false,'review'),false);
  assert.equal(flow.canArchive(values,'customer',q,false,''),false);
});
test('跟进日期、创建时间和对接人属于结构化录入必填项',()=>{
  const q={follow_up_score:61,next_action:{passed:true}};
  for (const key of ['interaction_at','created_date','contact_name']) {
    assert.equal(flow.canArchive({...values,[key]:''},'customer',q,false,'review'),false);
    assert.match(flow.archiveBlockReason({values:{...values,[key]:''},quality:q,customerConfirmed:true,customerId:'customer',reviewRunId:'review',reviewStale:false}),/请补充/);
  }
});
test('首次拜访必须补齐主营业务、需求、预算和联系人角色',()=>{
  const q={follow_up_score:61,next_action:{passed:true}};
  const first={...values,is_first_visit:true,customer_main_business:'企业软件',customer_needs:'建设销售系统',customer_budget:'50 万元',contact_role:'决策者'};
  assert.equal(flow.canArchive(first,'customer',q,false,'review'),true);
  for (const key of ['customer_main_business','customer_needs','customer_budget','contact_role']) {
    const changed={...first,[key]:''};
    assert.equal(flow.canArchive(changed,'customer',q,false,'review'),false);
    assert.match(flow.archiveBlockReason({values:changed,quality:q,customerConfirmed:true,customerId:'customer',reviewRunId:'review',reviewStale:false}),/首次拜访请补充/);
  }
  assert.equal(flow.canArchive({...values,is_first_visit:false},'customer',q,false,'review'),true);
});
test('更换客户清空商机、保留正文审核，并忽略迟到的商机结果',async()=>{
  let definition;let resolve;
  const api={listOpportunities:()=>new Promise(r=>{resolve=r;})};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/visit-confirm/index.js','utf8'),{
    require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):n.includes('visitSnapshot')?require('../miniprogram/utils/visitSnapshot'):n.includes('visitFlow')?flow:n.includes('visitDates')?require('../miniprogram/utils/visitDates'):n.includes('visitFirstVisit')?require('../miniprogram/utils/visitFirstVisit'):n.includes('visitOpportunityAI')?require('../miniprogram/utils/visitOpportunityAI'):api,Page:d=>{definition=d;},clearTimeout,wx:{},
  });
  const page={...definition,data:{...definition.data,values,customerId:'a',opportunityId:'oa',reviewRunId:'old'},setData(o){Object.assign(this.data,o);},persist(){},searchCustomers(){}};
  page.loadOpportunities();page.changeCustomer();resolve({items:[{id:'oa',name:'旧商机'}],has_more:false,next_offset:null});await Promise.resolve();
  assert.equal(page.data.customerId,'');assert.equal(page.data.opportunityId,'');assert.equal(page.data.reviewRunId,'old');
  assert.equal(page.data.opportunityOptions.length,1);
});
test('确认客户后才加载已有商机，已有项用于更新，新建项用于创建',async()=>{
  let definition; const calls=[];
  const api={listOpportunities:async(args)=>{calls.push(args);return {items:[{id:'o1',name:'已有商机'}],has_more:false,next_offset:null}}};
  vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/visit-confirm/index.js','utf8'),{
    require:n=>n.includes('statusLight')?require('../miniprogram/utils/statusLight'):n.includes('visitSnapshot')?require('../miniprogram/utils/visitSnapshot'):n.includes('visitFlow')?flow:n.includes('visitDates')?require('../miniprogram/utils/visitDates'):n.includes('visitFirstVisit')?require('../miniprogram/utils/visitFirstVisit'):n.includes('visitOpportunityAI')?require('../miniprogram/utils/visitOpportunityAI'):api,Page:d=>{definition=d;},clearTimeout,wx:{},
  });
  const page={...definition,data:{...definition.data,values,customers:[{id:'c1',name:'客户一',customer_type_code:'prospect'}]},setData(o){Object.assign(this.data,o);},persist(){},refresh(){}};
  page.chooseCustomer({currentTarget:{dataset:{id:'c1'}}}); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(page.data.customerConfirmed,true); assert.equal(calls[0].customerId,'c1');
  assert.equal(page.data.opportunityOptions.map(o=>o.name).join('|'),'不关联商机|已有商机|本次拜访产生新商机');
  page.selectOpportunity({detail:{value:1}}); assert.equal(page.data.selectedOpportunity.id,'o1'); assert.equal(page.data.opportunityEditing,true);
  page.selectOpportunity({detail:{value:2}}); assert.equal(page.data.selectedOpportunity,null); assert.equal(page.data.opportunityId,'__new__'); assert.equal(page.data.opportunityEditing,true);
});
test('确认页不提供新建客户入口，商机字段受客户确认状态控制',()=>{
  const wxml=fs.readFileSync(__dirname+'/../miniprogram/pages/visit-confirm/index.wxml','utf8');
  assert.doesNotMatch(wxml,/创建新客户|bindtap="createCustomer"/);
  assert.match(wxml,/wx:if="\{\{customerConfirmed && \(!isFde \|\| editing\)\}\}" class="optional-field"><view class="field-label">商机名称/);
  assert.match(wxml,/<fde-visit-opportunity wx:if="\{\{isFde && customerConfirmed && !editing\}\}"/);
  assert.match(wxml,/客户类型<\/view><picker mode="selector" range="\{\{customerTypeOptions\}\}"/);
  assert.doesNotMatch(wxml,/bindtap="changeCustomer"/);
});

test('确认页绑定上一页客户并仅加载该客户商机',()=>{
  const js=fs.readFileSync(__dirname+'/../miniprogram/pages/visit-confirm/index.js','utf8');
  assert.match(js,/const boundCustomerId = source\.customerHintId/);
  assert.match(js,/customerConfirmed: Boolean\(boundCustomerId && boundCustomerName\)/);
  assert.match(js,/else this\.loadOpportunities\(\)/);
  assert.match(js,/listOpportunities\(\{customerId:id,pageSize:20/);
});
test('质量审核与下一步审核位于结构化录入之前',()=>{
  const wxml=fs.readFileSync(__dirname+'/../miniprogram/pages/visit-confirm/index.wxml','utf8');
  assert.ok(wxml.indexOf('class="review-at-top"') < wxml.indexOf('class="card structured-entry"'));
  assert.equal((wxml.match(/>AI 质量审核</g)||[]).length,1);
  assert.equal((wxml.match(/>下一步审核</g)||[]).length,1);
});
test('AI 商机结果可匹配已有商机并按原文预填字段',()=>{
  const ai=require('../miniprogram/utils/visitOpportunityAI');
  const suggestion=ai.normalizeOpportunitySuggestion({opportunity_draft:{opportunity_name:'数据平台一期',acv_wan:'80',stage:'方案沟通－50%',expected_close_date:'2026-12-20',partner_name:'云伙伴'}});
  const existing={id:'o1',name:'数据平台一期',amount:500000,probability:30,status:'open',expected_close_date:'2026-11-01'};
  assert.equal(ai.matchExistingOpportunity([existing],suggestion).id,'o1');
  const form=ai.formFromSuggestion(existing,suggestion);
  assert.equal(form.amount,'80'); assert.equal(form.stageIndex,2); assert.equal(form.expected_close_date,'2026-12-20'); assert.equal(form.partner_name,'云伙伴');
});

test('明确不关联商机时不预选已有项目，人民币小额不被放大',()=>{
  const ai=require('../miniprogram/utils/visitOpportunityAI');
  assert.equal(ai.normalizeOpportunitySuggestion({action:'none',name:'不应关联',amount:80000}).hasContent,false);
  assert.equal(ai.normalizeOpportunitySuggestion({action:'update',opportunity_id:'own',amount:1200}).amount,'0.12');
  assert.equal(ai.normalizeOpportunitySuggestion({action:'update',status:'lost'}).stageIndex,6);
});
