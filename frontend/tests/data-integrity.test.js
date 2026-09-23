require('./helpers/business-options');
const pageResponse=require('./helpers/opportunity-pages');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));

function load(name, api = {}, saved = {}) {
  const file = path.resolve(__dirname, `../miniprogram/pages/${name}/index.js`);
  const storage = new Map(Object.entries(saved));
  api={getBusinessOptions:async()=>require('./helpers/business-options.json'),...api};
  const session = { capabilities:{'task.respond':true,'task.coordinate':true,'task.create':true,'customer.create':true}, userId: 'u1', workspaceId: 'w1', userName: '同名', role: 'supervisor', team: '南区' };
  const app = {ensureLogin: () => true, globalData: {session, role: 'supervisor', roles: {supervisor: {name:'总监',scope:'团队'}}}};
  let page;
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), {
    Page: value => page = value, getApp: () => app,
    require: name => name.includes('apiClient') ? api : require(path.resolve(path.dirname(file), name)),
    setTimeout() {}, clearTimeout() {},
    wx: {getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key), showToast() {}, navigateBack() {}, navigateTo() {}, vibrateShort() {},
      showNavigationBarLoading() {}, hideNavigationBarLoading() {}, showModal: ({success}) => success({confirm:true})},
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = (values, callback) => {Object.assign(page.data, values); if (callback) callback();};
  return {page, session, storage};
}

test('同名账号不能操作另一人的任务；按用户ID开放接受和完成', async () => {
  const task = {id:'00000000-0000-0000-0000-000000000001', status:'pending_confirm', creator_name:'同名', creator_user_ref_id:'other', assignees:[{user_id:'u2',name:'同名',responsibility:'owner'}]};
  const {page, session} = load('task-detail', {getTask: async () => task});
  page.onLoad({id: task.id}); page.loadTask(); await tick();
  assert.equal(page.data.task.canRespond, false);
  session.userId = 'u2'; page.loadTask(); await tick();
  assert.equal(page.data.task.canRespond, true);
  task.status = 'pending_execution'; page.loadTask(); await tick();
  assert.equal(page.data.task.canComplete, true);
  task.status = 'cancelled'; page.loadTask(); await tick();
  assert.equal(page.data.task.canRetry, false);
  session.userId = 'other'; page.loadTask(); await tick();
  assert.equal(page.data.task.canRetry, true);
});

test('报告详情按运行ID回查数据库；本地正文从不成为来源', async () => {
  const saved={pendingReportDetail:{title:'本地旧报告',ownerUserId:'u1',workspaceId:'w1'}};
  const run={id:'r1',status:'succeeded',completed_at:'2026-09-10T12:00:00Z',result:{title:'数据库报告',safe_customers:[{title:'数据库客户',detail:'真实持久化内容'}]}};
  const {page,storage}=load('report-detail',{getRun:async id=>{assert.equal(id,'r1');return run;}},saved);
  await page.onLoad({runId:'r1',action:'report_personal_daily',section:'safe_customers',row:'0'});
  assert.equal(page.data.detail.title,'数据库客户');assert.equal(storage.has('pendingReportDetail'),false);
  const failed=load('report-detail',{getRun:async()=>{throw new Error('无权限');}},saved);
  await failed.page.onLoad({runId:'r1',action:'report_personal_daily',section:'safe_customers',row:'0'});
  assert.equal(failed.page.data.detail,null);assert.equal(failed.page.data.error,'无权限');
});

test('总览成功提示重新读取记录，只存ID，换账号旧事件被丢弃', async () => {
  const ref={id:'c1',ownerUserId:'u1',workspaceId:'w1',name:'错误本地名字'};
  let reads=0;
  const {page,storage}=load('index',{getCustomerOverview:async id=>{reads++;return {id,name:'数据库客户',level_code:'Tier-1',team_name:'南区',owner_name:'销售甲'};}},{lastCreatedCustomer:ref});
  await page.consumeCreatedCustomerSuccess();
  assert.equal(page.data.messages[0].card.subtitle,'数据库客户');
  assert.equal(storage.has('lastCreatedCustomer'),false);
  storage.set('lastCreatedCustomer',{...ref,ownerUserId:'other'});await page.consumeCreatedCustomerSuccess();
  assert.equal(reads,1);
});

test('商机分页失败或缺少集合时显示局部错误，总览不使用零占位', async () => {
  for (const listOpportunities of [async()=>{throw Error('离线');},async()=>({items:[]})]) {
    const {page}=load('workbench',{listOpportunities,getOpportunityOverview:async()=>{throw Error('总览离线');}});
    await page.loadData();
    assert.equal(page.data.opportunityDataReady,false);assert.equal(page.data.opportunityListLoading,false);assert.ok(page.data.opportunityListError);assert.ok(page.data.opportunityDataError);
  }
});

test('商机工作台再次进入时复用新鲜数据，过期后后台刷新也不清空界面', async () => {
  let requestCount=0,resolveRefresh;
  const {page}=load('workbench',{getOpportunityOverview:async()=>({metrics:{won:0,total:0,active:0,newCount:0,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}),listOpportunities:()=>{
    requestCount+=1;if(requestCount===1)return Promise.resolve(pageResponse([]));return new Promise(resolve=>{resolveRefresh=resolve;});
  }});
  await page.loadData();assert.equal(page.data.dataReady,true);page.onShow();assert.equal(requestCount,1);
  page.workbenchLoadedAt=Date.now()-31000;page.onShow();assert.equal(requestCount,2);assert.equal(page.data.dataReady,true);assert.equal(page.data.loading,false);
  resolveRefresh(pageResponse([]));await tick();
});

test('商机总览按所选季度统计新增，使用包含关闭商机的完整数据', async () => {
  const items = [
    {id:'current',created_at:'2026-04-02',expected_close_date:'2026-07-01',status:'open'},
    {id:'older',created_at:'2026-01-02',expected_close_date:'2026-04-01',status:'open'},
    {id:'unknown',status:'won',closed_at:'2026-04-30'},
  ];
  const response = {summary:{},members:[],customers:[],tasks:[],risks:[],opportunities:items.filter(item=>item.status==='open')};
  const {page} = load('workbench',{getOpportunityOverview:async()=>({metrics:{won:1,total:1,active:1,newCount:1,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}),getWorkbench:async()=>response,listOpportunities:async options=>pageResponse(items,options)});
  await page.loadData(); await tick();
  page.data.summaryQuarter = require('../miniprogram/utils/opportunityQuarter').quarterSelection(2026);
  page.toggleQuarter({currentTarget:{dataset:{scope:'summary',value:2}}});
  await tick();
  assert.equal(page.data.opportunityBoard.total,1);
  assert.equal(page.data.opportunityBoard.won,1);
  assert.equal(page.data.opportunityBoard.newCount,1);
});

test('商机经营补齐含关闭商机的列表后同步总览完整统计', async () => {
  const active = {id:'active',customer_id:'c1',customer_name:'客户一',status:'open',probability:30,amount:500000,owner_name:'销售',team_name:'南区'};
  const closed = {id:'closed',customer_id:'c2',customer_name:'客户二',status:'lost',amount:100000,owner_name:'销售',team_name:'南区'};
  const response = {summary:{},members:[],customers:[],tasks:[],risks:[],opportunities:[active]};
  const {page} = load('workbench',{getOpportunityOverview:async()=>({metrics:{won:1,total:2,active:1,newCount:2,missingCloseDates:0,missingWonDates:0,missingCreatedDates:0}}),getWorkbench:async()=>response,listOpportunities:async options=>pageResponse([active,closed],options)});
  await page.loadData(); await tick();
  assert.equal(page.data.opportunityBoard.total,2);
  assert.equal(page.data.opportunityBoard.active,1);
  assert.deepEqual(Array.from(page.data.filteredOpportunities,item=>item.id),['active','closed']);
});

test('列表团队筛选和重置不改变商机总览', async () => {
  const {page} = load('workbench',{listOpportunities:async options=>pageResponse(page.baseOpportunities.map(row=>({...row,team_name:row.team,owner_name:row.owner})),options)});
  page.data.role = 'manager';
  page.data.executionTeamOptions = [{value:'all',label:'全部团队'},{value:'南区',label:'南区'},{value:'东区',label:'东区'}];
  page.baseMembers = [{name:'销售甲',team:'南区',role:'sales'},{name:'销售乙',team:'东区',role:'sales'}];
  page.baseCustomers = []; page.baseTasks = []; page.baseRisks = [];
  page.baseOpportunities = [
    {id:'south',team:'南区',owner:'销售甲',amount:100000,created_at:'2026-09-01',stageCode:'confirmed',amountBandCode:'C',gradeCode:'C'},
    {id:'east',team:'东区',owner:'销售乙',amount:200000,created_at:'2026-09-02',stageCode:'confirmed',amountBandCode:'C',gradeCode:'C'},
  ];
  page.listOpportunities = page.baseOpportunities;
  page.data.opportunityDataReady = true;
  page.data.opportunityBoard = {total:2};
  page.changeExecutionTeam({detail:{value:2}});await tick();
  assert.equal(page.data.executionTeamLabel,'东区');
  assert.deepEqual(Array.from(page.data.filteredOpportunities,item=>item.id),['east']);
  assert.equal(page.data.opportunityBoard.total,2);
  assert.equal(page.data.opportunityFilterActive,true);
  page.resetOpportunityFilters();await tick();
  assert.deepEqual(Array.from(page.data.filteredOpportunities,item=>item.id),['south','east']);
  assert.equal(page.data.opportunityBoard.total,2);
  assert.equal(page.data.executionTeamLabel,'全部团队');
});

test('客户详情保留缺失与真实零，不推断联系人角色，不把拒绝任务算入待办', () => {
  const {normalizeCustomerDetail} = require('../miniprogram/utils/customerDetail');
  const data = normalizeCustomerDetail({id:'c',name:'客户',customer_type_code:'opportunity',cooperation_years:0,
    contacts:[{name:'某人',title:'总经理',is_primary:true}],tasks:['pending_execution','cancelled','completed'].map(status=>({status}))});
  assert.equal(data.contacts[0].role,'角色待补充'); assert.equal(data.contacts[0].strength,'首要联系人');
  assert.equal(data.cooperationYears,'0年'); assert.equal(data.customerType,'商机客户'); assert.equal(data.pendingTaskCount,1);
  assert.match(data.grossProfitContract,/尚未接入/);
  assert.equal(normalizeCustomerDetail({id:'c',name:'客户'}).cooperationYears,'未登记');
});

test('客户拜访按时间倒序并保留完整首次拜访字段', () => {
  const {normalizeCustomerDetail} = require('../miniprogram/utils/customerDetail');
  const data = normalizeCustomerDetail({id:'c',name:'客户',visits:[
    {id:'old',interaction_at:'2026-08-01 09:00',created_at:'2026-08-01 10:00'},
    {id:'new',interaction_at:'2026-09-10 14:30',created_at:'2026-09-10 15:00',interaction_mode_code:'offline_meeting',duration_minutes:45,
      expectation_code:'met',is_first_visit:true,customer_main_business:'企业软件',customer_needs:'建设销售系统',customer_budget:'50万元',contact_role:'决策者'},
  ]});
  assert.deepEqual(data.visits.map(item=>item.id),['new','old']);
  assert.equal(data.visits[0].mode,'线下会议');
  assert.equal(data.visits[0].duration,'45分钟');
  assert.equal(data.visits[0].expectation,'达成100%');
  assert.equal(data.visits[0].firstVisitText,'是');
  assert.equal(data.visits[0].customerMainBusiness,'企业软件');
  assert.equal(data.visits[0].contactRole,'决策者');
});

test('旧建档下发表单迁移到约定客户字段，缺角色不能提交，已创建客户重试不重复创建', async () => {
  const creates = [], assignments = [];
  const {page} = load('customer-assign-confirm', {
    getDirectoryMembers: async () => ({items:[{name:'销售',role:'sales',account_code:'XS002',team:'南区'}]}),
    createCustomer: async body => {creates.push(body); return {id:'created'};},
    assignCustomer: async (id, body) => {assignments.push({id,...body}); if(assignments.length===1) throw Error('分配失败'); return {id:'assignment'};},
  }, {'managementCustomerDraft:w1:u1':{creator:'同名',fields:[
    {key:'customer_name',value:'演示'}, {key:'industry',value:'',required:true},
    {key:'lead_source',value:'公司分配'}, {key:'contact_name',value:'联系人'}, {key:'contact_title',value:'经理'},
    {key:'estimated_amount',value:'100'}, {key:'first_action',value:'明日电话确认需求'},
  ]}});
  await page.onLoad(); await tick();
  assert.equal(page.data.fields.length,12);
  assert.equal(page.data.fields.find(f=>f.key==='industry').required,false);
  assert.equal(page.data.fields.find(f=>f.key==='partner_name').required,false);
  assert.equal(page.data.fields.find(f=>f.key==='lead_source').value,'销售线索');
  page.confirmArchive(); await tick(); assert.equal(creates.length,0);
  page.refresh(page.data.fields.map(f=>f.key==='contact_role'?{...f,value:'决策者'}:f.key==='level_code'?{...f,value:'Tier-1'}:f));
  page.confirmArchive(); await tick();
  assert.equal(creates.length,1); assert.equal(creates[0].contact_role,'决策者'); assert.equal(creates[0].customer_type,'潜在客户'); assert.equal(creates[0].level_code,'Tier-1');
  for (const key of ['opportunity_name','estimated_amount','demand_summary','next_action']) assert.equal(Object.hasOwn(creates[0],key),false);
  page.confirmArchive(); await tick(); assert.equal(creates.length,1); assert.equal(assignments.length,2); assert.equal(assignments[1].id,'created');
});

test('跟进公司客户不申请认领，原始内容和客户选择真实保留', async () => {
  let claims=0;
  const context=load('visit-entry',{claimCustomer:async()=>{claims++;throw Error('不应调用');}});
  context.session.role='sales';context.page.draftKey='draft';
  context.page.data.customerResults=[{id:'c1',name:'他人认领客户',claimed:false,can_claim:false}];
  context.page.data.transcript='真实录入的拜访内容';
  context.page.chooseCustomer({currentTarget:{dataset:{id:'c1'}}});await tick();
  assert.equal(claims,0);assert.equal(context.page.data.customerConfirmed,true);
  assert.equal(context.page.data.customerId,'c1');
  assert.equal(context.page.data.transcript,'真实录入的拜访内容');
});


test('拒绝后重发草稿按账号隔离，并按用户ID选回同名接收人', async () => {
  const task={id:'00000000-0000-0000-0000-000000000001',description:'原交付内容',status:'cancelled',creator_user_ref_id:'u1',assignees:[{user_id:'u2',name:'同名',responsibility:'owner'}]};
  const original=load('task-detail',{getTask:async()=>task});
  original.page.onLoad({id:task.id});original.page.loadTask();await tick();original.page.retryTask();
  assert.equal(original.storage.has('retryTaskDraft'),false);
  assert.equal(original.storage.get('retryTaskDraft:w1:u1').assigneeId,'u2');
  const api={getTaskPositions:async()=>({items:[]}),listTaskRecipients:async()=>({items:[{id:'u3',name:'同名',role:'sales'},{id:'u2',name:'同名',role:'sales'}]})};
  const create=load('management-task-create',api,Object.fromEntries(original.storage));
  create.page.onLoad({retry:1});await tick();
  assert.equal(create.page.data.selectedMember.id,'u2');
  assert.equal(create.storage.has('retryTaskDraft:w1:u1'),false);
  const foreign=load('management-task-create',api,{'retryTaskDraft:w1:u2':{description:'他人的任务',assigneeId:'u3'}});
  foreign.page.onLoad({retry:1});await tick();
  assert.equal(foreign.page.data.description,'');assert.equal(foreign.page.data.selectedMember,null);
});

test('负责人来自全员目录，切换负责人保留表单内容', async () => {
  const api={listTaskRecipients:async()=>({items:[{id:'ops',name:'运营',account_code:'OPS001',role:'operations'},{id:'fde',name:'技术同事',account_code:'FDE001',role:'fde'}]})};
  const {page}=load('management-task-create',api);
  page.onLoad({});await tick();
  page.changeAssignee({detail:{value:0}});assert.equal(page.data.selectedMember.id,'ops');
  page.inputDescription({detail:{value:'需要协助核对资料'}});
  page.changeAssignee({detail:{value:1}});
  assert.equal(page.data.description,'需要协助核对资料');
  assert.equal(page.data.selectedMember.id,'fde');
});

test('待领取候选可以响应，其他人领取后只能查看且不能代完成', async () => {
  let row={id:'11111111-1111-1111-1111-111111111111',status:'pending_confirm',target_position:'supervisor',requires_action:true,assignees:[],candidates:[{user_id:'u1',decision:'pending'}]};
  const {page}=load('task-detail',{getTask:async()=>row});
  page.onLoad({id:row.id});page.onShow();await tick();
  assert.equal(page.data.task.canRespond,true);assert.equal(page.data.task.responseLabel,'领取任务');
  row={...row,status:'pending_execution',requires_action:false,assignees:[{user_id:'u2',name:'其他主管',responsibility:'owner'}]};
  page.loadTask();await tick();
  assert.equal(page.data.task.canRespond,false);assert.equal(page.data.task.canComplete,false);
  assert.equal(page.data.task.owner,'其他主管');
});


test('采纳建议从后台回查并允许人工修改，原生保存带建议版本和业务关联', async () => {
  let decision;
  const api={listTaskRecipients:async()=>({items:[{id:'u1',name:'同名',account_code:'XS001',role:'sales'}]}),
    getCustomerReference:async()=>({id:'c1',name:'客户'}),listTaskOpportunities:async()=>({items:[{id:'o1',customer_id:'c1',name:'商机'}]}),
    getBusinessAdvice:async()=>({id:'advice1',status:'succeeded',customer_id:'c1',opportunity_id:'o1',suggestions:[{id:'s1',version_no:3,title:'试点范围',action:'建议联系客户核对试点范围',decision:'pending'}]}),
    decideSuggestion:async(id,body)=>{decision={id,body};return {task:{id:'t1'}};},
    createTask:async()=>{throw Error('采纳必须走建议决定入口');}};
  const {page,storage}=load('management-task-create',api);
  page.onLoad({adviceId:'advice1',suggestionId:'s1'});await tick();
  assert.equal(page.data.description,'建议联系客户核对试点范围');
  page.inputDescription({detail:{value:'人工修改：先请客户补充验收标准'}});
  page.changeAssignee({detail:{value:0}});
  page.submitTask();await tick();
  assert.equal(decision.id,'s1');assert.equal(decision.body.version_no,3);assert.equal(decision.body.decision,'adopted');
  assert.equal(decision.body.task.description,'人工修改：先请客户补充验收标准');
  assert.equal(decision.body.task.customer_id,'c1');assert.equal(decision.body.task.opportunity_id,'o1');
  assert.equal(storage.get('lastManagementTaskCreated').id,'t1');
});

test('过期建议阻止建任务；离开页面后慢返回不再覆盖表单', async () => {
  let resolveAdvice;
  const api={getTaskPositions:async()=>({items:[]}),listTaskRecipients:async()=>({items:[]}),
    getBusinessAdvice:()=>new Promise(resolve=>{resolveAdvice=resolve;})};
  const {page}=load('management-task-create',api);
  page.onLoad({adviceId:'advice1',suggestionId:'s1'});
  resolveAdvice({status:'superseded',suggestions:[]});await tick();
  assert.match(page.data.adviceError,/已变化/);assert.equal(page.data.adviceSource,null);
  const departed=load('management-task-create',api);departed.page.onLoad({adviceId:'advice1',suggestionId:'s1'});
  departed.page.isUnloading=true;departed.page.setData=()=>{throw Error('已离开页面不得更新');};
  resolveAdvice({status:'succeeded',suggestions:[]});await tick();
});

test('客户六维画像只绘制后端指标，缺失不按前端记录数猜分', () => {
 const {normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
 const missing=normalizeCustomerDetail({id:'c',name:'客户',visits:[],contacts:[],risks:[]});
 assert.equal(missing.profileComplete,false);assert.equal(missing.customerProfile[5].value,'—');
 const dimensions=['客户潜力','关系深度','商机成熟','拜访活跃','决策链','风险健康'].map((label,i)=>({label,value:i*10}));
 const measured=normalizeCustomerDetail({id:'c',name:'客户',profile:{dimensions},visits:[]});
 assert.equal(measured.profileComplete,true);assert.equal(measured.customerProfile[3].value,30);
 assert.equal(measured.customerProfile[0].value,0);
});
