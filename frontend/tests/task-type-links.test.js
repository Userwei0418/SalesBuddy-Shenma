const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const file=path.resolve(__dirname,'../miniprogram/pages/management-task-create/index.js');
function setup(extra={}){
 let page;const sent=[],modals=[],session={userId:'u',workspaceId:'w',role:'sales'};
 const api={listTaskRecipients:async()=>({items:[]}),getTaskPositions:async()=>({items:[]}),createTask:async value=>{sent.push(value);return{id:'t'};},...extra};
 vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:v=>page=v,getApp:()=>({ensureLogin:()=>true,globalData:{session,roles:{sales:{name:"销售",scope:"本人"}}}}),setTimeout(){},clearTimeout(){},require:n=>n.includes('apiClient')?api:require(path.resolve(path.dirname(file),n)),wx:{showToast(){},showModal:v=>modals.push(v),setStorageSync(){},navigateBack(){}}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(v,cb)=>{Object.assign(page.data,v);if(cb)cb();};
 page.getSelectedDueAt=()=>Date.now()+86400000;
 page.setData({description:'请整理实施方案并确认下一步',selectedMembers:[{id:'u',account:'u',name:'同事',team:'团队'}],selectedDue:'明天'});
 return {page,sent,modals};
}
const tick=()=>new Promise(r=>setImmediate(r));
test('客户任务必须先有客户和已核验的商机，发送携带两层关联',async()=>{
 const {page,sent,modals}=setup();page.setData({taskType:'customer',customerId:'c'});page.submitTask();assert.equal(modals.length,0);
 page.setData({opportunityId:'o',linkVerified:true});page.submitTask();assert.equal(modals.length,1);
 modals[0].success({confirm:true});await tick();assert.equal(sent[0].customerId,'c');assert.equal(sent[0].opportunityId,'o');
});
test('日常任务发送不携带残留关联，切换类型清空关联和接收人',async()=>{
 const {page,sent,modals}=setup();page.setData({customerId:'old',opportunityId:'old-op'});page.submitTask();modals[0].success({confirm:true});await tick();assert.equal(sent[0].customerId,'');assert.equal(sent[0].opportunityId,'');
 page.setData({submitting:false,submissionPending:false});page.changeTaskType({currentTarget:{dataset:{type:'customer'}}});assert.equal(page.data.customerId,'');assert.equal(page.data.opportunityId,'');assert.equal(page.data.selectedMembers.length,0);
});
test('更换客户清空旧商机，拒绝其他客户的候选商机',()=>{
 const {page}=setup();page.setData({taskType:'customer',selectorKind:'customer',selectorRows:[{id:'c2',name:'客户二'}],customerId:'c1',opportunityId:'o1',linkVerified:true});
 page.chooseTaskLink({currentTarget:{dataset:{id:'c2'}}});assert.equal(page.data.customerId,'c2');assert.equal(page.data.opportunityId,'');assert.equal(page.data.linkVerified,false);
 page.setData({selectorKind:'opportunity',selectorRows:[{id:'wrong',customer_id:'c1'}]});page.chooseTaskLink({currentTarget:{dataset:{id:'wrong'}}});assert.equal(page.data.opportunityId,'');
});
test('接收人目录晚到响应不能覆盖新客户范围',async()=>{
 const waits=[];const {page}=setup({listTaskRecipients:()=>new Promise(r=>waits.push(r))});
 page.loadRecipients();page.setData({customerId:'c2'});page.loadRecipients();
 waits[1]({items:[{id:'new',name:'新接收人',role:'sales'}]});await tick();waits[0]({items:[{id:'old',name:'旧接收人',role:'sales'}]});await tick();assert.equal(page.data.members[0].id,'new');
});
test('客户待办按客户查询并保留该客户不同商机任务',async()=>{
 const {customerLoaders}=require('../miniprogram/utils/detailReadSession');let params;
 const tasks=[{id:'t1',customer_id:'c',opportunity_id:'o1'},{id:'t2',customer_id:'c',opportunity_id:'o2'}];
 const response=await customerLoaders({listDetailTasks:async p=>{params=p;return {items:tasks,has_more:false};}},'c').tasks({page_size:20});
 assert.equal(params.customer_id,'c');assert.equal(params.opportunity_id,undefined);assert.deepEqual(response.items,tasks);
});

for(const taskType of ['daily','customer']) test(taskType+'任务不查询岗位，旧岗位草稿必须重新选择负责人',async()=>{
 let calls=0;const {page}=setup({getTaskPositions:async()=>{calls++;return {items:[]};}});
 page.setData({taskType});page.loadRecipients({targetPosition:'self'});await tick();
 assert.equal(calls,0);assert.equal(page.data.selectedMembers.length,0);
});
test('客户任务即便残留岗位模式，也只向指定同事发送',async()=>{
 const {page,sent,modals}=setup();page.setData({taskType:'customer',customerId:'c',opportunityId:'o',linkVerified:true,targetMode:'position',selectedPosition:{code:'self',available:true}});
 page.submitTask();modals[0].success({confirm:true});await tick();
 assert.equal(sent[0].targetPosition,null);assert.equal(sent[0].assigneeAccount,'u');
});

test('日常任务目录不限定客户商机，保留跨部门各业务角色',async()=>{
 let params;const rows=['sales','supervisor','manager','fde','fde_lead','operations','administrator'].map((role,i)=>({id:String(i),name:role,role,team:'部门'+i,account_code:'account'+i}));
 const {page}=setup({listTaskRecipients:async p=>{params=p;return {items:rows};}});
 page.setData({taskType:'daily',customerId:'c',opportunityId:'o'});page.loadRecipients();await tick();
 assert.equal(params.customer_id,undefined);assert.equal(params.opportunity_id,undefined);assert.equal(page.data.members.length,7);
 assert.deepEqual(Array.from(page.data.members,r=>r.role),rows.map(r=>r.role));
});


test('客户任务和日常任务使用相同全公司目录，不用商机关联收窄接收人',async()=>{
 const calls=[];const {page}=setup({listTaskRecipients:async p=>{calls.push(p);return {items:[]};}});
 page.setData({taskType:'customer',customerId:'c'});page.loadRecipients();await tick();assert.equal(calls.length,1);
 page.setData({opportunityId:'o',linkVerified:true});page.loadRecipients();await tick();
 assert.equal(calls.length,2);assert.equal(calls[1].customer_id,undefined);assert.equal(calls[1].opportunity_id,undefined);
 page.changeTaskType({currentTarget:{dataset:{type:'daily'}}});await tick();assert.equal(calls[2].customer_id,undefined);
});

test('全公司人员目录读取所有页，不能只显示第一页',async()=>{
 const calls=[];const {page}=setup({listTaskRecipients:async p=>{calls.push(p);return p.offset===0
  ? {items:[{id:'a',name:'运营',role:'operations'}],has_more:true,next_offset:1}
  : {items:[{id:'b',name:'其他部门FDE',role:'fde'}],has_more:false,next_offset:null};}});
 page.loadRecipients();await tick();assert.deepEqual(calls.map(p=>p.offset),[0,1]);assert.equal(page.data.members.length,2);
 assert.equal(page.data.members[0].roleLabel,'运营');assert.equal(page.data.recipientError,'');
});

test('源建议的商机固定；无商机建议允许人工选择同客户商机',()=>{
 const {page}=setup();page.setData({adviceSource:{id:'s'},customerId:'c'});page.loadTaskChoices=()=>{};
 page.adviceOpportunityId='fixed';page.openTaskSelector({currentTarget:{dataset:{kind:'opportunity'}}});assert.equal(page.data.selectorOpen,false);
 page.adviceOpportunityId='';page.openTaskSelector({currentTarget:{dataset:{kind:'opportunity'}}});assert.equal(page.data.selectorOpen,true);
 page.setData({selectorRows:[{id:'o',customer_id:'c',name:'同客户商机'}]});page.chooseTaskLink({currentTarget:{dataset:{id:'o'}}});assert.equal(page.data.opportunityId,'o');
});

for(const taskType of ['daily','customer']) test(taskType+'任务均由人工选择一名负责人，旧模式不能恢复岗位派发',async()=>{
 const {page,sent,modals}=setup();page.setData({taskType,customerId:'c',opportunityId:'o',linkVerified:true,targetMode:'position',selectedPosition:{code:'self',available:true}});
 page.submitTask();modals[0].success({confirm:true});await tick();
 assert.equal(sent[0].targetPosition,null);assert.equal(sent[0].assigneeAccount,'u');
});
test('任务客户选单使用专用分页，滚动下一页保留搜索条件',async()=>{
 const calls=[];const {page}=setup({listTaskCustomers:async p=>{calls.push(p);return p.offset===0
 ?{items:[{id:'c1',name:'客户一'}],has_more:true,next_offset:20}
 :{items:[{id:'c2',name:'客户二'}],has_more:false,next_offset:null};}});
 page.setData({selectorOpen:true,selectorKind:'customer',selectorQuery:'客户'});
 await page.loadTaskChoices();await page.moreTaskChoices();
 assert.deepEqual(calls.map(p=>[p.offset,p.q,p.pageSize]),[[0,'客户',20],[20,'客户',20]]);
 assert.equal(page.data.selectorRows.length,2);assert.equal(page.data.selectorMore,false);
});
test('商机选单只读取当前客户可关联的商机，旧查询晚到不能覆盖新客户',async()=>{
 const waits=[],calls=[];const {page}=setup({listTaskOpportunities:p=>{calls.push(p);return new Promise(r=>waits.push(r));}});
 page.setData({selectorOpen:true,selectorKind:'opportunity',customerId:'c1'});const old=page.loadTaskChoices();
 page.setData({customerId:'c2'});const current=page.loadTaskChoices();
 waits[1]({items:[{id:'o2',customer_id:'c2'}],has_more:false});await current;
 waits[0]({items:[{id:'o1',customer_id:'c1'}],has_more:false});await old;
 assert.deepEqual(calls.map(p=>p.customerId),['c1','c2']);assert.equal(page.data.selectorRows[0].id,'o2');
});
test('建议或重发草稿的商机已失去关联权限时不能继续下发',async()=>{
 const {page,modals}=setup({getCustomerReference:async()=>({id:'c',name:'客户'}),listTaskOpportunities:async()=>({items:[]})});
 page.setData({taskType:'customer',customerId:'c',opportunityId:'o'});await page.hydrateTaskLink();
 assert.equal(page.data.linkVerified,false);assert.match(page.data.linkError,/无权关联/);page.submitTask();assert.equal(modals.length,0);
});

for(const linked of [false,true])test(`拜访建议采纳表单关联商机=${linked}选择正确待办类型`,async()=>{
 const {page}=setup({getBusinessAdvice:async()=>({subject_kind:'visit',status:'succeeded',customer_id:'c',opportunity_id:linked?'o':null,suggestions:[{id:'s',version_no:1,decision:'pending',title:'核对范围',action:'整理并确认客户需求'}]}),getCustomerReference:async()=>({id:'c',name:'客户'}),listTaskOpportunities:async()=>({items:[{id:'o',customer_id:'c',name:'商机'}]})});
 page.onLoad({adviceId:'a',suggestionId:'s'});await tick();
 assert.equal(page.data.taskType,linked?'customer':'daily');assert.equal(page.data.customerId,linked?'c':'');assert.equal(page.data.opportunityId,linked?'o':'');assert.equal(page.data.adviceSource.dailyVisit,!linked);
});
