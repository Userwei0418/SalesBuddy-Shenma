require('./helpers/business-options');
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const pageResponse=require('./helpers/opportunity-pages');

const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const riskId='00000000-0000-0000-0000-000000000001';
const riskRow = extra => ({id:riskId,status:'open',version_no:4,can_resolve:true,...extra});
function setup(name,api={},component=false){
  let definition;
  const file=path.resolve(__dirname,`../miniprogram/${component?'components':'pages'}/${name}/index.js`);
  const app={ensureLogin:()=>true,globalData:{role:'manager',session:{workspaceId:'w',userId:'u',role:'manager',permissionVersion:1,capabilities:{'visit.create':true,'risk.resolve':name==='risk-detail'}},roles:{manager:{name:'经理',scope:'公司'}}}};
  const timers=new Map(),toasts=[],modals=[],navigations=[];let nextTimer=0;
  const wx={showToast:v=>toasts.push(v),showModal:v=>modals.push(v),navigateBack:()=>navigations.push('back'),navigateTo:v=>navigations.push(v.url),vibrateShort(){},setStorageSync(){},removeStorageSync(){},setNavigationBarTitle(){},showNavigationBarLoading(){},hideNavigationBarLoading(){}};
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{
    Page:value=>definition=value,Component:value=>definition=value,getApp:()=>app,wx,Date,Promise,Map,Set,
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(file),name)),
    setTimeout:callback=>{const id=++nextTimer;timers.set(id,callback);return id;},clearTimeout:id=>timers.delete(id),
  });
  const page={...definition,...definition.methods,data:JSON.parse(JSON.stringify(definition.data)),properties:{},
    setData(values,callback){Object.assign(this.data,values);if(callback)callback();}};
  return {page,app,toasts,modals,navigations,timers,runTimers(){const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());}};
}
function report(id,title){return {id,status:'succeeded',result:{safe_customers:[{title,detail:'报告正文'}]}};}
const reference=runId=>({runId,action:'report_personal_daily',section:'safe_customers',row:'0'});

test('风险详情旧账号响应与错误不覆盖新账号，也不发起旧导航',async()=>{
  const first=deferred(),second=deferred();let reads=0;
  const h=setup('risk-detail',{getRisk:()=>++reads===1?first.promise:second.promise});
  h.page.onLoad({id:riskId});const old=h.page.loadRisk();
  h.app.globalData.session.userId='new';const next=h.page.loadRisk();
  second.resolve({id:riskId,title:'当前风险',status:'open'});await next;
  first.reject(Error('旧请求无权限'));await old;h.runTimers();
  assert.equal(h.page.data.risk.title,'当前风险');assert.equal(h.page.data.loading,false);
  assert.equal(h.toasts.length,0);assert.equal(h.navigations.length,0);
});

test('风险详情离开后取消返回计时器，迟到解除确认不提交',async()=>{
  let writes=0;const h=setup('risk-detail',{getRisk:async()=>riskRow(),resolveRisk:async()=>{writes++;}});
  h.page.onLoad({id:riskId});await h.page.loadRisk();h.page.data.resolutionNote='已核实解除原因';
  h.page.confirmResolve();h.page.showMissing();assert.equal(h.timers.size,1);
  h.page.onHide();h.modals[0].success({confirm:true});h.runTimers();
  assert.equal(writes,0);assert.equal(h.navigations.length,0);assert.equal(h.timers.size,0);
});

test('当前风险正常读取和确认解除仍更新详情并按原流程返回',async()=>{
  let writes=0;const h=setup('risk-detail',{getRisk:async()=>riskRow({title:'当前风险'}),resolveRisk:async(id,note,version)=>{writes++;assert.equal(id,riskId);assert.equal(note,'已核实解除原因');assert.equal(version,4);return riskRow({id,status:'resolved',version_no:5});}});
  h.page.onLoad({id:riskId});await h.page.loadRisk();h.page.data.resolutionNote='已核实解除原因';
  h.page.confirmResolve();h.modals[0].success({confirm:true});await tick();h.runTimers();
  assert.equal(writes,1);assert.equal(h.page.data.risk.status,'resolved');assert.equal(h.page.data.submitting,false);assert.deepEqual(h.navigations,['back']);
});

test('风险列表重新显示后只采用最新请求，旧finally不提前结束加载',async()=>{
  const first=deferred(),second=deferred();let reads=0;
  const h=setup('risks',{listRisks:()=>++reads===1?first.promise:second.promise});
  h.page.onLoad({});const old=h.page.loadRisks();h.page.onHide();h.page._hidden=false;
  const next=h.page.loadRisks();first.resolve({items:[{id:'old',title:'旧风险'}]});await old;
  assert.equal(h.page.data.loading,true);assert.equal(h.page.data.risks.length,0);
  second.resolve({items:[{id:'new',title:'新风险'}],total:1,summary:{total:1,open_count:1,resolved_count:0,overdue_count:0},offset:0,page_size:50,has_more:false,next_offset:null});await next;
  assert.equal(h.page.data.risks[0].id,'new');assert.equal(h.page.data.loading,false);
});

test('报告详情拒绝同账号旧权限版本响应，并在返回页面后重读',async()=>{
  const pending=[];const h=setup('report-detail',{getRun:()=>{const request=deferred();pending.push(request);return request.promise;}});
  const old=h.page.onLoad(reference('r1'));h.app.globalData.session.permissionVersion=2;
  pending[0].resolve(report('r1','旧权限内容'));await old;assert.equal(h.page.data.detail,null);
  h.page.onHide();const next=h.page.onShow();assert.equal(pending.length,2);
  pending[1].resolve(report('r1','新权限内容'));await next;assert.equal(h.page.data.detail.title,'新权限内容');
});

test('旧报告失败不清空已加载的新报告，卸载后成功回包也无效',async()=>{
  const oldRequest=deferred();const h=setup('report-detail',{getRun:id=>id==='old'?oldRequest.promise:Promise.resolve(report(id,'新报告'))});
  const old=h.page.onLoad(reference('old'));h.page.reference=reference('new');await h.page.loadReport();
  oldRequest.reject(Error('旧报告失败'));await old;assert.equal(h.page.data.detail.title,'新报告');assert.equal(h.page.data.error,'');
  const pending=deferred();const other=setup('report-detail',{getRun:()=>pending.promise});
  const request=other.page.onLoad(reference('r'));other.page.onUnload();pending.resolve(report('r','已卸载'));await request;
  assert.equal(other.page.data.detail,null);assert.equal(other.page.data.loading,false);
});

test('成员画像按账号与页面生命周期隔离，返回可重新读取',async()=>{
  const pending=[];const h=setup('member-growth',{getMemberSalesGrowth:()=>{const request=deferred();pending.push(request);return request.promise;}});
  const old=h.page.onLoad({account:'ONE'});h.page.onHide();h.app.globalData.session.userId='new';const next=h.page.onShow();
  pending[0].reject(Error('旧成员画像失败'));await old;assert.equal(h.page.data.loading,true);
  pending[1].resolve({subject:{name:'成员甲'},latest:{overall_score:80,dimension_scores:{}},history:[]});await next;
  assert.equal(h.page.data.ready,true);assert.equal(h.page.data.subject.name,'成员甲');
  assert.equal(h.page.data.statusText,'最近一次复盘 · 数据已同步');
});

test('成员画像旧权限返回计时器不离开新页面',()=>{
  const h=setup('member-growth');h.app.globalData.session.role='sales';
  h.page.onLoad({account:'ONE'});assert.equal(h.timers.size,1);
  h.page.onHide();h.runTimers();assert.equal(h.navigations.length,0);
});

test('同身份返回FDE成员画像不卸载现有看板，保留子组件筛选',()=>{
  const h=setup('member-growth');h.app.globalData.session.role='fde';
  h.page.onLoad({member_id:'u'});assert.equal(h.page.data.isFde,true);
  const updates=[];const setData=h.page.setData;h.page.setData=function(values,callback){updates.push(values);setData.call(this,values,callback);};
  h.page.onHide();h.page.onShow();assert.equal(h.page.data.isFde,true);
  assert.ok(updates.every(values=>values.isFde!==false));
});

test('FDE记录页离开卸载记录，返回先重新校验页面权限',()=>{
  const h=setup('fde-records');const context={scope:'self',year:2026,quarters:[3]};
  h.page.onLoad({context:encodeURIComponent(JSON.stringify(context))});assert.equal(h.page.data.context.year,2026);
  h.page.onHide();assert.equal(h.page.data.context,null);
  h.app.guardPage=()=>false;h.page.onShow();assert.equal(h.page.data.context,null);
  h.app.guardPage=()=>true;h.page.onShow();assert.equal(h.page.data.context.year,2026);
});

test('拜访切换后旧建议与旧商机跳转结果不应用到新记录',async()=>{
  const advice=deferred(),link=deferred();const h=setup('visit-detail',{getLatestBusinessAdvice:()=>advice.promise,getOpportunityDetailHeader:()=>link.promise,getVisit:async()=>({id:'new',customer_id:'c2',customer_name:'新客户'})});
  h.page.data.visitId='old';h.page.data.visit={id:'old',linkedOpportunities:[{id:'o'}]};
  const oldAdvice=h.page.loadVisitAdvice();const oldLink=h.page.openOpportunity({currentTarget:{dataset:{id:'o'}}});
  h.page.data.visitId='new';h.page.loadVisitAdvice=()=>{};await h.page.loadVisit();
  advice.resolve({id:'old-advice',status:'succeeded',summary:'旧建议',suggestions:[]});link.resolve({id:'c',opportunities:[{id:'o'}]});
  await Promise.all([oldAdvice,oldLink]);assert.equal(h.page.data.visit.id,'new');assert.equal(h.page.data.visitAdvice.status,'idle');assert.equal(h.navigations.length,0);
});

test('客户主档错误对象响应拒绝展示，已有会话保护保留',async()=>{
  const h=setup('customer-detail',{getCustomerHeader:async()=>({id:'other',name:'不匹配客户'})});
  h.page.onLoad({id:'wanted'});await h.page.loadCustomer();
  assert.equal(h.page.data.customer,null);assert.match(h.modals[0].content,/客户资料不匹配/);
});

test('商机Demo旧失败与finally不覆盖重返页面的新请求',async()=>{
  const first=deferred(),second=deferred();let reads=0;const h=setup('customer-assets',{listDemoScenes:()=>++reads===1?first.promise:second.promise});
  h.page.data.customerId='c';h.page.data.opportunityId='o';h.page.data.opportunity={id:'o'};h.page.data.canReadDemo=true;
  const old=h.page.loadDemoScenes();h.page.onHide();const next=h.page.loadDemoScenes();
  first.reject(Error('旧场景失败'));await old;assert.equal(h.page.data.demoError,'');assert.equal(h.page.data.demosLoading,true);
  second.resolve({data_source:'database',items:[{id:'new'}],total:1});await next;
  assert.equal(h.page.data.demos[0].id,'new');assert.equal(h.page.data.demosLoading,false);
});

test('FDE本人记录与录入资格不能从旧商机串入新商机',async()=>{
  const visits=deferred(),eligibility=deferred();const h=setup('customer-assets',{getFdeActivity:()=>visits.promise,listFdeVisitOpportunities:()=>eligibility.promise});
  h.page.data.isFde=true;h.page.data.customerId='c';h.page.data.opportunityId='old';
  const old=h.page.loadFdeOwnVisits(),check=h.page.checkFdeVisitEligibility();h.page.data.opportunityId='new';
  visits.resolve({items:[{id:'v-old'}],total:1});eligibility.resolve({items:[{id:'old',customer_id:'c'}]});await Promise.all([old,check]);
  assert.equal(h.page.data.fdeOwnVisits.length,0);assert.equal(h.page.data.canRecordThisOpportunity,false);
});

test('商机协助人员确认弹窗离开或切账号后不能提交',async()=>{
  let writes=0;const h=setup('customer-assets',{updateFdeMembers:async()=>{writes++;}});
  h.page.data.opportunityId='o';h.page.data.customerId='c';h.page.data.opportunity={id:'o',version_no:1,fde_members:[{id:'one',name:'甲'}]};h.page.data.fdeMembers=[];h.page.data.canManageFdeRelation=true;
  const operation=h.page.saveFdeMembers();h.page.onHide();h.modals[0].success({confirm:true});await operation;
  assert.equal(writes,0);assert.equal(h.page.data.fdeRelationSaving,false);
});

test('销售实绩旧写方法已移除，切账号不能触发写入',()=>{
 const h=setup('customer-assets');for(const name of ['submitConfirmed','submit','voidEntry','openForm'])assert.equal(h.page[name],undefined);
});

test('当前商机保存协助名单仍结束加载，保留返回版本',async()=>{
  const writes=[];const h=setup('customer-assets',{updateFdeMembers:async(id,members,version)=>{writes.push([id,...members,version]);return {version_no:2,fde_members:[{id:'one'}],changed:true};}});
  Object.assign(h.page.data,{customerId:'c',opportunityId:'o',opportunity:{id:'o',version_no:1,fde_members:[]},fdeMembers:[{id:'one'}],canManageFdeRelation:true});
  await h.page.saveFdeMembers();assert.deepEqual(writes,[['o','one',1]]);assert.equal(h.page.data.opportunity.version_no,2);assert.equal(h.page.data.fdeRelationSaving,false);

});

test('工作台隐藏使旧请求失效，返回不会复用失效的加载Promise',async()=>{
  const lists=[];const h=setup('workbench',{listOpportunities:()=>{const r=deferred();lists.push(r);return r.promise;}});
  const old=h.page.loadData();h.page.onHide();const next=h.page.onShow();assert.equal(lists.length,2);
  lists[0].reject(Error('旧列表失败'));await old;
  assert.equal(h.page.data.opportunityListLoading,true);assert.equal(h.page.data.opportunityDataReady,false);
  lists[1].resolve(pageResponse([], {includeOverview:true, overviewYear:new Date().getUTCFullYear(), overviewQuarters:[1,2,3,4]}));await next;
  assert.equal(h.page.data.opportunityBoard.total,0);assert.equal(h.page.data.opportunityListError,'');assert.equal(h.page.workbenchLoading,null);
});

test('FDE记录组件隐藏使旧结果失效，返回从首页重读且不重叠旧页',async()=>{
  const pending=[];const h=setup('fde-dashboard',{getFdeActivity:()=>{const r=deferred();pending.push(r);return r.promise;}},true);
  h.page.properties={recordsOnly:true,recordContext:{scope:'self',year:2026,quarters:[]}};
  h.page.lifetimes.attached.call(h.page);h.page.pageLifetimes.hide.call(h.page);
  const next=h.page.pageLifetimes.show.call(h.page);assert.equal(pending.length,2);
  pending[0].resolve({items:[{id:'old'}],total:1,has_more:false});await tick();assert.equal(h.page.data.activity.length,0);assert.equal(h.page.data.activityLoading,true);
  pending[1].resolve({items:[{id:'new'}],total:1,has_more:false});await next;
  assert.equal(h.page.data.activity[0].id,'new');assert.equal(h.page.data.activityLoading,false);
});

test('FDE看板返回保留季度筛选，旧失败不盖掉新加载',async()=>{
  const pending=[],queries=[];const h=setup('fde-dashboard',{getFdeDashboard:query=>{queries.push(query);const r=deferred();pending.push(r);return r.promise;}},true);
  h.page.data.year=2026;h.page.data.quarters=[2];h.page.properties={recordsOnly:false};
  const old=h.page.load();h.page.pageLifetimes.hide.call(h.page);const next=h.page.pageLifetimes.show.call(h.page);
  assert.equal(pending.length,2);assert.deepEqual(Array.from(queries[1].quarters),[2]);
  pending[0].reject(Error('旧看板失败'));await old;assert.equal(h.page.data.error,'');assert.equal(h.page.data.loading,true);
  pending[1].resolve({data_source:'database',summary:{},ranking:[],recent_visits:[]});await next;
  assert.equal(h.page.data.ready,true);assert.equal(h.page.data.loading,false);assert.equal(h.page.data.error,'');
});


test('风险解除在途离页返回仍锁住写入，结算后单次权威回读且不重放POST',async()=>{
  const pending=deferred();let reads=0,writes=0,resolved=false;
  const h=setup('risk-detail',{getRisk:async()=>{reads++;return riskRow({status:resolved?'resolved':'open'});},resolveRisk:()=>{writes++;return pending.promise;}});
  h.page.onLoad({id:riskId});await h.page.onShow();h.page.data.resolutionNote='已核实解除原因';
  h.page.confirmResolve();h.modals[0].success({confirm:true});h.page.onHide();await h.page.onShow();
  assert.equal(h.page.data.submitting,true);h.page.confirmResolve();assert.equal(h.modals.length,1);assert.equal(writes,1);
  resolved=true;pending.resolve(riskRow({status:'resolved',version_no:5}));await tick();
  assert.equal(reads,3);assert.equal(writes,1);assert.equal(h.page.data.risk.status,'resolved');assert.equal(h.page.data.submitting,false);
});



test('协助名单写入在途返回不会二次提交，结算回读服务端版本',async()=>{
  const pending=deferred();let writes=0,reads=0;
  const h=setup('customer-assets',{updateFdeMembers:()=>{writes++;return pending.promise;}});
  Object.assign(h.page.data,{customerId:'c',opportunityId:'o',opportunity:{id:'o',version_no:1,fde_members:[]},fdeMembers:[{id:'one'}],canManageFdeRelation:true});
  h.page.load=()=>{};h.page.loadQuarterActuals=()=>{};h.page.loadOpportunity=()=>reads++;
  const saving=h.page.saveFdeMembers();h.page.onHide();h.page.onShow();assert.equal(h.page.data.fdeRelationSaving,true);
  await h.page.saveFdeMembers();assert.equal(writes,1);pending.resolve({version_no:2,fde_members:[{id:'one'}],changed:true});await saving;
  assert.equal(writes,1);assert.equal(reads,2);assert.equal(h.page.data.fdeRelationSaving,false);
});




test('风险解除传输失败先回读实际状态，不根据失败自行再次提交',async()=>{
  let reads=0,writes=0;const h=setup('risk-detail',{getRisk:async()=>riskRow({status:++reads===1?'open':'resolved'}),resolveRisk:async()=>{writes++;throw Error('连接中断');}});
  h.page.onLoad({id:riskId});await h.page.onShow();h.page.data.resolutionNote='已核实解除原因';
  h.page.confirmResolve();h.modals[0].success({confirm:true});await tick();
  assert.equal(writes,1);assert.equal(reads,2);assert.equal(h.page.data.risk.status,'resolved');h.page.confirmResolve();assert.equal(h.modals.length,1);
});
