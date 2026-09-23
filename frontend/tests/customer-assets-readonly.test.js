require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const {AdvicePool}=require('../miniprogram/utils/advicePool');
function pageDefinition(api = {}, pool = new AdvicePool()) {
  let page;
  api={getOpportunityDetailHeader:api.getOpportunityDetailOverview,...api};
  const filename = path.resolve(__dirname, '../miniprogram/pages/customer-assets/index.js');
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: definition => { page = definition; },
    require: name => name.endsWith('apiClient') ? api : name.endsWith('advicePool') ? {opportunityAdvicePool:pool} : require(path.resolve(path.dirname(filename), name)),
    Date,
    wx: {},
    getApp: () => ({ ensureLogin: () => true,globalData:{session:{workspaceId:"w",userId:"u",role:"sales",permissionVersion:1}} }),
    clearTimeout,
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = function setData(values, callback) { Object.assign(this.data, values); if (callback) callback(); };
  return page;
}

test('历史金额明细传递来源并保留季度、税口径，不允许作为逐笔作废',async()=>{
 const requests=[];
 const page=pageDefinition({getCustomerAssets:async params=>{
   requests.push(params);return {basis:'historical',historical_count:1,
     summary:{recognized_amount:540000,entry_count:1},items:[{id:'h',customer_id:'c',customer_name:'客户',
       amount:540000,occurred_on:null,period_label:'2026 Q2',tax_basis:'unknown',source_ref:'Q2真实确收'}],
     has_more:false,can_manage:true,as_of:'2026-09-23'};
 }});
 page.onLoad({basis:'historical',customer_id:'c',customer_name:'客户'});await page.load();
 assert.equal(requests[0].basis,'historical');assert.equal(page.data.totalText,'54');
 assert.equal(page.data.items[0].occurred_on,null);assert.equal(page.data.items[0].period_label,'2026 Q2');
 assert.equal(page.data.items[0].taxBasisText,'税口径未确认');
 // wx.showModal is intentionally absent: historical values cannot invoke void.
 page.voidEntry({currentTarget:{dataset:{id:'h'}}});await page.openForm();assert.equal(page.data.formOpen,false);
});

test('商机实绩入口加载完整商机信息并启用只读模式', async () => {
  const page = pageDefinition({
    getOpportunityDetailOverview: async () => ({
      id: 'c1', name: '示例客户', owner_name: '刘志德', team_name: '南区',
      opportunities: [{
        id: 'o1', name: 'Token Plan', status: 'open', stage_code: 'qualified', probability: 30,
        amount: 5000000, expected_close_date: '2026-12-31', partner_name: '伙伴甲', product_line: '产品线A',
        quarterly_forecasts: [{ year: 2026, quarter: 4, recognized_amount: 100000, collection_amount: 80000 }],
      }],
      visits: [], tasks: [], contacts: [], risks: [],
    }),
  });
  page.onLoad({ customer_id: 'c1', customer_name: '示例客户', opportunity_id: 'o1', readonly: '1', period: 'all' });
  await page.loadOpportunity();
  assert.equal(page.data.readOnly, true);
  assert.equal(page.data.opportunity.name, 'Token Plan');
  assert.equal(page.data.opportunity.statusLabel, '推进中');
  assert.equal(page.data.opportunity.grade.code, 'A');
  assert.equal(page.data.opportunity.quarterDetails.length, 1);
});

test('只读实绩页隐藏登记和作废入口', () => {
  const wxml = fs.readFileSync(__dirname + '/../miniprogram/pages/customer-assets/index.wxml', 'utf8');
  const sharedWxss = fs.readFileSync(__dirname + '/../miniprogram/pages/customer-assets/opportunity-view.wxss', 'utf8');
  assert.match(wxml, /class="opportunity-view surface"/);
  assert.match(wxml, /class="actual-page actual-refresh"/);
  assert.match(wxml, /class="actual-total-label"/);
  assert.match(wxml, /class="actual-customer-mark"/);
  assert.match(sharedWxss, /\.actual-page\.actual-refresh/);
  assert.match(wxml, /商业资料只读；协助名单由有权人员单独维护/);
  assert.match(wxml, /商机等级/);
  assert.match(wxml, /<block wx:if="\{\{!readOnly\}\}">/);
  assert.equal((wxml.match(/canManage && !readOnly/g) || []).length, 1);
});

test('详情默认当前季度、切换季度只更改两项实绩，迟到响应不覆盖新商机', async () => {
  const date=new Date(Date.now()+8*3600000).toISOString().slice(0,10);
  const currentYear=date.slice(0,4);
  let finishOld;
  const page=pageDefinition({getCustomerAssetQuarters:options=>options.opportunity_id==='old'
    ? new Promise(resolve=>{finishOld=resolve})
    : Promise.resolve({as_of:date,years:[Number(currentYear)],items:[{year:Number(currentYear),quarter:Math.ceil(Number(date.slice(5,7))/3),collection_amount:120000,recognized_amount:null,collection_count:1,recognized_count:0,entry_count:1}]})});
  page.onLoad({customer_id:'c1',opportunity_id:'old',readonly:'1',period:'all'});
  const old=page.loadQuarterActuals();
  page.setData({opportunityId:'new'});
  await page.loadQuarterActuals();
  finishOld({as_of:date,years:[Number(currentYear)],items:[]});await old;
  assert.equal(page.data.quarterCollection,'12万');
  assert.equal(page.data.quarterRecognized,'未登记');
  const currentQuarter=Math.ceil(Number(date.slice(5,7))/3);
  assert.equal(page.data.quarterActualOptions[page.data.quarterActualIndex].key,`${currentYear}-Q${currentQuarter}`);
  page.changeActualQuarter({detail:{value:currentQuarter===1?1:0}});
  assert.equal(page.data.quarterCollection,'未登记');
  assert.equal(page.data.period,'all');
  assert.equal(page.data.readOnly,true);
});

const {TABS,opportunityContext,opportunityEvents}=require('../miniprogram/utils/opportunityAdvice');
const current={id:'o1',name:'当前商机',status:'open',amount:100000,probability:50,expected_close_date:'2027-01-01'};
const linkedRaw={id:'c1',name:'客户一',owner_name:'负责人',team_name:'南区',opportunities:[current,{id:'o2',name:'不相关商机秘密'}],tasks:[{id:'t1',opportunity_id:'o1',title:'目标待办',status:'pending_execution'},{id:'t2',opportunity_id:'o2',title:'其他商机待办'},{id:'t3',title:'客户通用待办'}],visits:[{id:'v1',opportunity_id:'o1',follow_up_record:'目标跟进',status:'confirmed',expectation_code:null,follow_up_score:75,quality_review:{follow_up_score:75,grade:'合格',next_action_passed:true},next_action:'约演示',interaction_at:'2026-09-10T10:00:00Z'},{id:'v2',opportunity_id:'o2',follow_up_record:'其他商机跟进'},{id:'v3',follow_up_record:'客户通用跟进'}],risks:[{opportunity_id:'o2',title:'其他商机风险'}]};

test('四类商机建议仅使用目标商机及明确关联记录',()=>{
 assert.throws(()=>opportunityContext(linkedRaw,'missing'),/不存在/);
 const context=opportunityContext(linkedRaw,'o1');assert.equal(context.tasks.length,1);assert.equal(context.visits.length,1);
 const events=opportunityEvents(context);assert.equal(events.length,1);assert.equal(events[0].title,'跟进记录');
});
test('商机事件时间按北京时间展示，时间戳排序保留原时刻，拜访日期不附加时分',()=>{
 const events=opportunityEvents({opportunities:[{name:'测试商机',created_at:'2026-09-12T18:46:09Z',updated_at:'2026-09-13T03:00:00+08:00'}],
  visits:[{id:'date-only',visit_date:'2026-09-13'},{id:'legacy-local',interaction_at:'2026-09-13 04:10:00'}],
  tasks:[{id:'t1',title:'确认计划',created_at:'2026-09-12T19:30:00Z',status:'completed',completed_at:'2026-09-13T05:30:00+08:00'}]});
 const byKey=Object.fromEntries(events.map(item=>[item.key,item]));
 assert.equal(byKey.created.date,'2026-09-13 02:46');
 assert.equal(byKey.updated.date,'2026-09-13 03:00');
 assert.equal(byKey['task:t1'].date,'2026-09-13 03:30');
 assert.equal(byKey['done:t1'].date,'2026-09-13 05:30');
 assert.equal(byKey['visit:date-only'].date,'2026-09-13');
 assert.equal(byKey['visit:legacy-local'].date,'2026-09-13 04:10');
 assert.equal(byKey.created.at,Date.parse('2026-09-12T18:46:09Z'));
 assert.equal(byKey['done:t1'].at,Date.parse('2026-09-12T21:30:00Z'));
});

test('详情四个标签各自生成并缓存建议，任务跟进数量不串入同客户其他商机',async()=>{
 const prompts=[];const page=pageDefinition({getOpportunityDetailOverview:async()=>({...linkedRaw,tasks:[],visits:[],summary:{task_count:1,task_status_counts:{},visit_count:1}}),listDetailTasks:async()=>({items:[linkedRaw.tasks[0]],has_more:false,next_offset:null}),listVisits:async()=>({items:[linkedRaw.visits[0]],has_more:false,next_offset:null}),getOpportunityTimeline:async()=>({items:[],has_more:false,next_offset:null}),queryBusinessAdvice:async(kind,id,tab)=>{assert.equal(kind,'opportunity');assert.equal(id,'o1');prompts.push(tab);return {id:'a1',status:'succeeded',summary:'仅针对当前商机',suggestions:[]};}});
 page.onLoad({customer_id:'c1',opportunity_id:'o1',readonly:'1'});await page.loadOpportunity();await new Promise(r=>setImmediate(r));
 assert.equal(page.data.relatedTasks.length,0);assert.equal(page.data.relatedVisits.length,0);assert.equal(page.data.taskStats.total,1);
 assert.equal(page.data.opportunity.ownerLabel,'负责人');
 for(const tab of ['tasks','visits','opportunity']){page.selectOpportunityTab({currentTarget:{dataset:{tab}}});await new Promise(r=>setImmediate(r));}
 assert.equal(page.data.relatedTasks.length,1);assert.equal(page.data.relatedVisits.length,1);assert.equal(prompts.length,4);
 assert.equal(page.data.relatedVisits[0].signal.badgeText,'质检合格 · 75 分');
 for(const tab of Object.keys(TABS))assert.equal(page.data.opportunityAdvice[tab].status,'ready');
 page.selectOpportunityTab({currentTarget:{dataset:{tab:'overview'}}});await new Promise(r=>setImmediate(r));assert.equal(prompts.length,4);
 await page.loadOpportunityAdvice({currentTarget:{}});assert.equal(prompts.length,5);
});

test('离开商机详情后迟到建议不写回，失败明确展示并允许重试',async()=>{
 let finish;const page=pageDefinition({getOpportunityDetailOverview:async()=>linkedRaw,queryBusinessAdvice:()=>new Promise(r=>finish=r)});
 page.onLoad({customer_id:'c1',opportunity_id:'o1',readonly:'1'});await page.loadOpportunity();
 assert.equal(page.data.opportunityAdvice.overview.status,'loading');
 page.onUnload();finish({id:'a2',status:'succeeded',summary:'旧建议',suggestions:[]});await new Promise(r=>setImmediate(r));
 assert.notEqual(page.data.opportunityAdvice.overview.status,'ready');
 let failed=true;const p=pageDefinition({getOpportunityDetailOverview:async()=>linkedRaw,queryBusinessAdvice:async()=>{if(failed)throw Error('offline');return {id:'a3',status:'succeeded',summary:'恢复成功',suggestions:[]};}});
 p.onLoad({customer_id:'c1',opportunity_id:'o1'});await p.loadOpportunity();await new Promise(r=>setImmediate(r));assert.equal(p.data.opportunityAdvice.overview.status,'error');
 failed=false;await p.loadOpportunityAdvice({currentTarget:{}});assert.equal(p.data.opportunityAdvice.overview.summary,'恢复成功');
});

test('未点击也预取四个标签，重新进入先预览并校验；权限拒绝清除旧建议',async()=>{
 const pool=new AdvicePool(),calls=[];
 const ready=async(kind,id,tab)=>{calls.push(tab);return {id:tab,status:'succeeded',summary:'缓存摘要',suggestions:[]};};
 const first=pageDefinition({getOpportunityDetailOverview:async()=>linkedRaw,queryBusinessAdvice:ready},pool);
 first.onLoad({customer_id:'c1',opportunity_id:'o1'});await first.loadOpportunity();
 await new Promise(r=>setImmediate(r));assert.equal(calls.length,4);
 const deferred=[];
 const second=pageDefinition({getOpportunityDetailOverview:async()=>linkedRaw,queryBusinessAdvice:()=>new Promise(resolve=>deferred.push(resolve))},pool);
 second.onLoad({customer_id:'c1',opportunity_id:'o1'});await second.loadOpportunity();
 assert.equal(second.data.opportunityAdvice.overview.summary,'缓存摘要');
 assert.equal(second.data.opportunityAdvice.overview.checking,true);
 assert.equal(second.data.opportunityAdvice.overview.needsCheck,false);
 await new Promise(r=>setImmediate(r));assert.equal(deferred.length,2);
 deferred.splice(0).forEach(resolve=>resolve({id:'fresh',status:'succeeded',summary:'已更新摘要',suggestions:[]}));
 await new Promise(r=>setImmediate(r));
 deferred.splice(0).forEach(resolve=>resolve({id:'fresh',status:'succeeded',summary:'已更新摘要',suggestions:[]}));
 await new Promise(r=>setImmediate(r));assert.equal(second.data.opportunityAdvice.overview.checking,false);
 assert.equal(second.data.opportunityAdvice.overview.summary,'已更新摘要');
 const third=pageDefinition({getOpportunityDetailOverview:async()=>linkedRaw,queryBusinessAdvice:async()=>{throw Object.assign(Error('权限已变化'),{statusCode:403});}},pool);
 third.onLoad({customer_id:'c1',opportunity_id:'o1'});await third.loadOpportunity();await new Promise(r=>setImmediate(r));
 assert.equal(third.data.opportunityAdvice.overview.status,'error');assert.equal(third.data.opportunityAdvice.overview.summary,undefined);
});
