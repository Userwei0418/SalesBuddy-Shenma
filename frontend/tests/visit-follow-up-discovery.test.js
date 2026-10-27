const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../miniprogram/utils/visitOpportunityFollowUp');
const id=n=>`${String(n).padStart(8,'0')}-1111-4111-8111-111111111111`;
const row=n=>({contract_version:1,opportunity_id:id(n),name:'商机'+n,version_no:1,status:'open',stage_code:null,owner:{user_id:null,display_name:null,team_id:null,team_name:null},customer_reference:{id:id(99),name:'客户'},capabilities:{can_follow_up:true,can_edit:false,can_manage_collaborators:false,can_transfer:false},capability_reasons:{transfer:'TRANSFER_NOT_READY'}});
function page(api){const session={workspaceId:'w',userId:'u'};return {session,p:{...create(api,()=>session),data:{},setData(x){Object.assign(this.data,x);},persist(){}}};}
test('分页跨页去重，短页即使total未耗尽也停止，不循环取同offset',async()=>{
 const offsets=[];const {p}=page({listOpportunityFollowUpOptions:async({offset,limit})=>{offsets.push(offset);return {contract_version:1,items:offset===0?Array.from({length:20},(_,i)=>row(i+1)):offset===20?[row(20),row(21)]:[],total:99,offset,limit};}});await p.openFollowUpDiscovery();assert.equal(p.data.followUpMore,true);await p.searchFollowUp({currentTarget:{dataset:{more:true}}});assert.equal(p.data.followUpItems.length,21);assert.equal(p.data.followUpMore,false);assert.deepEqual(offsets,[0,20]);
});
test('关闭选择器与切换身份后迟到目录不显示',async()=>{
 for(const close of [true,false]){let release;const {p,session}=page({listOpportunityFollowUpOptions:()=>new Promise(r=>release=r)});const pending=p.openFollowUpDiscovery();if(close)p.closeFollowUpDiscovery();else session.userId='other';release({contract_version:1,items:[row(1)],total:1,offset:0,limit:20});await pending;assert.deepEqual(p.data.followUpItems,[]);}
});
test('选择时重新核权，目录旧权限不能绕过最新拒绝',async()=>{
 const {p}=page({getOpportunityAccess:async()=>({...row(1),collaborators:[],capabilities:{...row(1).capabilities,can_follow_up:false}})});p.data.followUpItems=[row(1)];p.data.transcript='原稿';await p.chooseFollowUp({currentTarget:{dataset:{id:id(1)}}});assert.equal(p.data.opportunityResolved,false);assert.equal(p.data.canSubmit,false);assert.equal(p.data.transcript,'原稿');
});

test('公司客户跟进：本人近期目录选客户后可空商机，迟到核权不改目标，完整拜访保持独立',async()=>{
 let resolve;const requests=[];const customer={id:id(99),name:'客户'};
 const {p,session}=page({listCompanyCustomerFollowUpOptions:async options=>{requests.push(options);return {items:[customer],total:1,limit:20,offset:0};},getCustomerFollowUpAccess:async()=>({customer_reference:customer,can_follow_up:true})});
 session.permissions={};session.capabilities={company_customer_follow_up:true,company_opportunity_follow_up:true};
 Object.assign(p.data,{transcript:'保留原文',importId:'已上传材料'});await p.openFollowUpDiscovery();assert.equal(requests[0].order,'recent_follow_up');await p.chooseFollowUpCustomer({currentTarget:{dataset:{id:customer.id}}});
 assert.equal(p.data.targetKind,'customer');assert.equal(p.data.opportunityId,'');assert.equal(p.data.followUpResolved,true);assert.equal(p.data.followUpPickerOpen,false);assert.equal(p.data.transcript,'保留原文');assert.equal(p.data.importId,'已上传材料');
 assert.equal(require('../miniprogram/utils/access').canVisitAction(session,'visit.structure',p.data),true);assert.equal(require('../miniprogram/utils/access').can(session,'customer.read'),false);
 let oldSearch=0;p.searchDepartmentCustomers=()=>oldSearch++;p.returnToCustomerFollowUp();assert.equal(oldSearch,0);session.permissions={'visit.create':true};p.returnToCustomerFollowUp();assert.equal(oldSearch,1);assert.equal(p.data.followUpOnly,false);assert.equal(p.data.customerId,'');assert.equal(p.data.transcript,'保留原文');
 const late=page({getCustomerFollowUpAccess:()=>new Promise(r=>resolve=r)});late.session.capabilities={company_customer_follow_up:true};Object.assign(late.p.data,{customerId:customer.id,opportunityId:'',followUpOnly:true,targetKind:'customer'});const pending=late.p.refreshFollowUp();late.session.userId='other';resolve({customer_reference:customer,can_follow_up:true});await pending;assert.equal(late.p.data.followUpResolved,false);
});

test('Web 自动搜索合并输入，手动搜索和关闭取消计时，旧响应不能覆盖新词',async()=>{
 const calls=[],wait=ms=>new Promise(r=>setTimeout(r,ms));let oldReply;
 const {p,session}=page({listCompanyCustomerFollowUpOptions:async options=>{
  calls.push(options.q);
  if(options.q==='旧词')return new Promise(r=>oldReply=r);
  return {items:[{id:id(99),name:options.q}],total:1,offset:0,limit:20};
 }});
 session.capabilities={company_customer_follow_up:true};p.data.followUpPickerOpen=true;p.data.followUpPickerMode='customers';
 const input=value=>p.inputFollowUpQuery({detail:{value,autoSearch:true}});
 input('客');input('客户');assert.equal(p.data.followUpSearchPending,true);await wait(340);
 assert.deepEqual(calls,['客户']);assert.equal(p.data.followUpItems[0].name,'客户');assert.equal(p.data.followUpSearchPending,false);
 input('旧词');const old=p.searchFollowUp();input('新词');await p.searchFollowUp();
 oldReply({items:[{id:id(98),name:'旧词'}],total:1,offset:0,limit:20});await old;await wait(340);
 assert.deepEqual(calls,['客户','旧词','新词']);assert.equal(p.data.followUpItems[0].name,'新词');
 input('关闭后');p.closeFollowUpDiscovery();await wait(340);assert.equal(calls.length,3);
});

test('Web 选客户先列商机，确认客户或商机分别核权，取消保留原目标',async()=>{
 const customer={id:id(99),name:'客户'},opportunity={opportunity_id:id(1),name:'商机',customer_reference:customer,can_follow_up:true};
 for(const target of ['cancel','customer','opportunity','denied']){
  const requests=[];let customerChecks=0,opportunityChecks=0;
  const {p,session}=page({listCompanyOpportunityFollowUpOptions:async options=>{requests.push(options);return {items:[opportunity],total:1,offset:0,limit:20};},
   getCustomerFollowUpAccess:async()=>{customerChecks++;return {customer_reference:customer,can_follow_up:target!=='denied'};},
   getOpportunityFollowUpAccess:async()=>{opportunityChecks++;return opportunity;}});
  session.capabilities={company_customer_follow_up:true,company_opportunity_follow_up:true};
  Object.assign(p.data,{followUpPickerOpen:true,followUpPickerMode:'customers',followUpItems:[customer],customerId:id(88),customerName:'原客户',transcript:'保留原文',importId:'原附件'});
  await p.chooseFollowUpCustomer({currentTarget:{dataset:{id:customer.id,showOpportunities:true}}});
  assert.equal(requests[0].customer_id,customer.id);assert.equal(p.data.followUpPickerOpen,true);assert.equal(p.data.customerId,id(88));assert.equal(customerChecks,0);
  if(target==='cancel'){p.closeFollowUpDiscovery();assert.equal(p.data.customerId,id(88));}
  else if(target==='opportunity'){await p.chooseFollowUp({currentTarget:{dataset:{id:opportunity.opportunity_id}}});assert.equal(opportunityChecks,1);assert.equal(p.data.targetKind,'opportunity');assert.equal(p.data.followUpPickerOpen,false);}
  else {await p.confirmFollowUpCustomer();assert.equal(customerChecks,1);assert.equal(p.data.opportunityId,'');assert.equal(p.data.followUpResolved,target!=='denied');assert.equal(p.data.followUpPickerOpen,target==='denied');}
  assert.equal(p.data.transcript,'保留原文');assert.equal(p.data.importId,'原附件');
 }
});

for(const kind of ['opportunity','customer'])test(`加载更多时选择${kind==='opportunity'?'商机':'客户'}被拒绝后，旧响应不覆盖目录且可手动重试`,async()=>{
 const customer={id:id(99),name:'客户'},requests=[];let finishMore;
 const item=n=>kind==='customer'?{id:id(n),name:'客户'+n}:{opportunity_id:id(n),name:'商机'+n,customer_reference:customer,can_follow_up:true};
 const list=async options=>{
  requests.push(options);
  if(options.offset===20)return new Promise(resolve=>finishMore=resolve);
  return {items:requests.length===1?Array.from({length:20},(_,i)=>item(i+1)):[item(30)],total:requests.length===1?21:1,offset:0,limit:20};
 };
 const denied=async()=>{throw Error('当前已无权跟进该目标');};
 const {p,session}=page({listCompanyOpportunityFollowUpOptions:list,listCompanyCustomerFollowUpOptions:list,getOpportunityFollowUpAccess:denied,getCustomerFollowUpAccess:denied});
 session.capabilities={company_customer_follow_up:true,company_opportunity_follow_up:true};
 Object.assign(p.data,{followUpPickerOpen:true,followUpPickerMode:kind==='customer'?'customers':'opportunities',transcript:'保留原文',importId:'原附件'});
 await p.searchFollowUp();const originalItems=p.data.followUpItems;
 const more=p.searchFollowUp({currentTarget:{dataset:{more:true}}});
 assert.equal(p.data.followUpLoading,true);
 await p[kind==='customer'?'chooseFollowUpCustomer':'chooseFollowUp']({currentTarget:{dataset:{id:id(1)}}});
 assert.equal(p.data.followUpPickerOpen,true);
 assert.equal(p.data.followUpResolved,false);
 assert.equal(p.data.canSubmit,false);
 assert.equal(p.data.followUpLoading,false,'取消在途目录请求必须同时结束 loading');
 assert.equal(p.data.followUpSearchPending,false);
 assert.equal(p.data.followUpChecking,false);
 assert.equal(p.data.transcript,'保留原文');assert.equal(p.data.importId,'原附件');
 finishMore({items:[item(21)],total:21,offset:20,limit:20});await more;
 assert.deepEqual(p.data.followUpItems,originalItems,'已取消的更多响应不得追加到目录');
 await p.searchFollowUp();
 assert.deepEqual(requests.map(request=>request.offset),[0,20,0],'手动搜索应重新发请求');
 assert.equal(p.data.followUpItems.length,1);
 assert.equal(p.data.followUpItems[0][kind==='customer'?'id':'opportunity_id'],id(30));
 assert.equal(p.data.followUpLoading,false);
});
