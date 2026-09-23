require('./helpers/business-options');
const test=require('node:test');const assert=require('node:assert/strict');
const {opportunitySignal,visitSignal}=require('../miniprogram/utils/customerSignals');
const {adviceResult}=require('../miniprogram/utils/customerAdvice');
const {normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
const now='2026-09-12T04:00:00Z';
const opportunity={id:'o1',status:'open',probability:70,expected_close_date:'2026-10-01'};
test('商机红黄绿按关闭状态、关联风险与日期判断，不传播其他商机风险',()=>{
 assert.equal(opportunitySignal(opportunity,[],now).tone,'green');
 assert.equal(opportunitySignal({...opportunity,status:'won'},[],now).tone,'green');
 assert.equal(opportunitySignal({...opportunity,status:'lost'},[],now).tone,'red');
 assert.equal(opportunitySignal({...opportunity,expected_close_date:'2026-09-11'},[],now).detail,'关单计划逾期');
 assert.equal(opportunitySignal({...opportunity,expected_close_date:'2026-09-15'},[],now).tone,'yellow');
 assert.equal(opportunitySignal({...opportunity,expected_close_date:'2026-02-30'},[],now).detail,'待补充');
 const risk={opportunity_id:'other',status:'open',severity_code:'high',title:'高风险'};
 assert.equal(opportunitySignal(opportunity,[risk],now).tone,'green');
 assert.equal(opportunitySignal(opportunity,[{...risk,opportunity_id:'o1'}],now).tone,'yellow');
 assert.equal(opportunitySignal(opportunity,[{...risk,opportunity_id:'o1',status:'resolved'}],now).tone,'green');
});
test('跟进显示已保存质检结果，旧目标达成字段不改变质量等级',()=>{
 const visit={status:'archived',expectation_code:null,next_action:'确认采购时间',follow_up_score:75,quality_review:{follow_up_score:75,grade:'合格',next_action_passed:true}};
 assert.equal(visitSignal(visit).badgeText,'质检合格 · 75 分');
 assert.equal(visitSignal(visit).tone,'green');
 for(const expectation_code of ['met','not_met','met_50_100',null])assert.equal(visitSignal({...visit,expectation_code}).badgeText,'质检合格 · 75 分');
 assert.equal(visitSignal({status:'archived',expectation_code:'met',next_action:'确认计划'}).badgeText,'暂无质检结果');
});
test('只呈现服务器建议及处理状态，过期或未完成结果不能冒充可采纳建议',()=>{
 assert.throws(()=>adviceResult({status:'succeeded'}));
 assert.throws(()=>adviceResult({status:'superseded',summary:'旧建议',suggestions:[]}));
 const result=adviceResult({id:'a1',status:'succeeded',summary:'优先确认采购',suggestions:[{id:'s1',title:'明确负责人',evidence:'负责人未明确',action:'联系客户确认试点责任人',decision:'no_task',version_no:2}]});
 assert.equal(result.status,'ready');assert.equal(result.id,'a1');assert.equal(result.rows[0].decision,'no_task');assert.match(result.rows[0].detail,/联系客户确认试点责任人/);
});
test('建议更新时间将带时区的同一时刻统一显示为北京时间，正确跨日',()=>{
 const inputs=['2026-09-12T18:46:09Z','2026-09-13T02:46:09+08:00','2026-09-12T14:46:09-04:00'];
 for(const completed_at of inputs){
  const result=adviceResult({id:'a1',status:'succeeded',summary:'服务端建议',suggestions:[],completed_at});
  assert.equal(result.updatedAt,'2026-09-13 02:46');
 }
});
test('建议时间兼容本地旧值与纯日期，不给日期型记录虚构时分',()=>{
 for(const [completed_at,expected] of [['2026-09-13 02:46:09','2026-09-13 02:46'],['2026-09-13T02:46:09','2026-09-13 02:46'],['2026-09-13','2026-09-13'],[null,''],['invalid-date','']]){
  assert.equal(adviceResult({id:'a1',status:'succeeded',summary:'服务端建议',suggestions:[],completed_at}).updatedAt,expected);
 }
});
test('客户归一化为列表商机和跟进记录提供带原因的状态',()=>{
 const result=normalizeCustomerDetail({id:'c1',name:'客户',opportunities:[{...opportunity,status:'won'}],visits:[{id:'v1',status:'archived',follow_up_score:75,quality_review:{grade:'合格'},next_action:'继续跟进'}]});
 assert.equal(result.opportunities[0].signal.tone,'green');assert.equal(result.visits[0].signal.tone,'green');assert.ok(result.visits[0].signal.reason);
});

const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function pageWith(api){let page;const filename=path.resolve(__dirname,'../miniprogram/pages/customers/index.js');const session={userId:'u1',workspaceId:'w1'};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>{page=p},require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>({globalData:{role:'sales',session}}),wx:{showTabBar(){}},Date,Set,Map,setTimeout});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(values,cb)=>{Object.assign(page.data,values);if(cb)cb()};page._adviceCustomer={id:'c1',name:'客户甲'};page._adviceGeneration=1;page.data.selectedCustomer={id:'c1'};return {page,session};
}
test('每个标签独立分析并缓存，刷新失败可重试，切换账号后旧结果失效',async()=>{
 const calls=[];let reject=false;
 const {page,session}=pageWith({queryBusinessAdvice:async(kind,id,tab)=>{calls.push(id);assert.equal(kind,'customer');if(reject)throw Error('offline');return {id:'a1',status:'succeeded',summary:tab==='tasks'?'待办建议':'客户建议',suggestions:[]}}});
 await page.loadCustomerAdvice();assert.equal(page.data.detailAdvice.overview.status,'ready');await page.loadCustomerAdvice();assert.equal(calls.length,1);
 page.data.detailTab='tasks';await page.loadCustomerAdvice();assert.equal(page.data.detailAdvice.tasks.summary,'待办建议');assert.equal(page.data.detailAdvice.overview.summary,'客户建议');
 reject=true;await page.loadCustomerAdvice({currentTarget:{}});assert.equal(page.data.detailAdvice.tasks.status,'error');assert.equal(page.data.detailAdvice.tasks.summary,undefined);
 let finish;const late=pageWith({queryBusinessAdvice:()=>new Promise(resolve=>{finish=resolve})});const pending=late.page.loadCustomerAdvice();late.session.userId='u2';finish({id:'a2',status:'succeeded',summary:'旧账号数据',suggestions:[]});await pending;assert.notEqual(late.page.data.detailAdvice.overview.summary,'旧账号数据');
});

test('临近预计关单按真实阶段提醒，不把早期商机当成签约',()=>{
 const cases=[[10,'商机待确认','意向沟通'],[30,'方案待推进','商机确认'],[50,'方案待落实','方案沟通'],[70,'谈判待推进','商务谈判'],[90,'签约待落实','客户签约']];
 for(const [probability,detail,stage] of cases){
  const row={...opportunity,probability,expected_close_date:'2026-09-13'};
  const near=opportunitySignal(row,[],now);assert.equal(near.tone,'yellow');assert.equal(near.detail,detail);assert.match(near.reason,new RegExp(stage));
  if(probability<90)assert.doesNotMatch(near.detail+near.reason,/签约/);
  const overdue=opportunitySignal({...row,expected_close_date:'2026-09-11'},[],now);
  assert.equal(overdue.tone,'red');assert.equal(overdue.detail,'关单计划逾期');assert.match(overdue.reason,new RegExp(stage));assert.doesNotMatch(overdue.detail,/签约/);
  for(const status of ['won','lost'])assert.equal(opportunitySignal({...row,status},[],now).detail,status==='won'?'已成单':'已丢单');
 }
});
test('关单提醒按北京时间和七天边界判断，阶段编码可补充概率缺失',()=>{
 const row={...opportunity,probability:30};
 assert.equal(opportunitySignal({...row,expected_close_date:'2026-09-12'},[],now).detail,'方案待推进');
 assert.equal(opportunitySignal({...row,expected_close_date:'2026-09-19'},[],now).detail,'方案待推进');
 assert.equal(opportunitySignal({...row,expected_close_date:'2026-09-20'},[],now).tone,'green');
 assert.equal(opportunitySignal({...row,expected_close_date:'2026-09-12'},[],'2026-09-12T16:00:00Z').detail,'关单计划逾期');
 assert.equal(opportunitySignal({...row,probability:null,stage_code:'identified',expected_close_date:'2026-09-13'},[],now).detail,'商机待确认');
 assert.equal(opportunitySignal({...row,probability:null,expected_close_date:'2026-09-13'},[],now).detail,'阶段待确认');
});
