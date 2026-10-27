const test=require('node:test');const assert=require('node:assert/strict');
const {filterCustomers,scopedCustomers,amountWan}=require('../miniprogram/utils/customerMap');
const customers=[
 {id:'1',name:'蓝海',potential:85,relationship:35,owner:'李一',owner_team_id:'t1',owner_user_ref_id:'u1',risk:'暂无重大风险',level:'Tier-1',quadrant:'主攻区',agentPlanSegment:'current_year',weeklyFollowUps:2,mapAmount:600000,customerStageCode:'active'},
 {id:'2',name:'新客户',potential:35,relationship:35,owner:'李一',owner_team_id:'t2',owner_user_ref_id:'u2',risk:'未跟进',level:'Tier-2',quadrant:'见单打单',agentPlanSegment:'long_term',weeklyFollowUps:0,mapAmount:0,customerStageCode:'prospect'},
];
function mapResult(items=[]) {return {items,activity_since:'2026-01-01',as_of:'2026-09-22'};}
function assetResult(acv_amount=9000000,portfolio_customer_count=200) {return {summary:{acv_amount,portfolio_customer_count,customer_count:1,unknown_acv_count:0,recognized_amount:null,collection_amount:null},as_of:'2026-09-22'};}
test('scope uses IDs even when colleague names are identical',()=>{assert.deepEqual(scopedCustomers(customers,'all','u1').map(x=>x.id),['1']);});
test('all includes customers with no opportunities or amounts',()=>assert.equal(filterCustomers(customers).length,2));
test('shared filtering intersects keyword, plan, multi-level and amount',()=>{
 const result=filterCustomers(customers,{keyword:' 蓝海 ',plan:'current_year',levels:['Tier-1','Tier-3'],amount:{value:'50-100',min:500000,max:999999}});
 assert.deepEqual(result.map(x=>x.id),['1']);
});
test('unknown money is not zero',()=>{assert.equal(amountWan(null),'—');assert.equal(amountWan(0),'0');assert.equal(amountWan(120000),'12');});

const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
function pageDefinition(api={}, app={}, wxStub={}, timers={setTimeout,clearTimeout}) {
  let page;const filename=path.resolve(__dirname,'../miniprogram/pages/customers/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),Date,Map,Set,wx:wxStub,getApp:()=>app,...timers});
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=function(values,cb){Object.assign(this.data,values);if(cb)cb();};page.data.activityWindow='today';page.data.activityWindowIndex=1;return page;
}
test('page uses the same customer IDs for dots and list, including results beyond the old 100 cap',()=>{
  const p=pageDefinition();p.visibleCustomers=Array.from({length:130},(_,i)=>({...customers[i%2],id:String(i),name:'客户'+i}));
  p.applyFilters();assert.equal(p.data.customers.length,130);
  p.data.keyword='客户12';p.applyFilters();assert.equal(p.data.customers.length,11);
  assert.equal(p.data.activeCustomerCount,11);
  assert.equal(JSON.stringify(p.data.customers.map(c=>c.id)),JSON.stringify(p.data.plotCustomers.map(c=>c.id)));
  p.data.keyword='';p.data.mapSelectedLevels=['Tier-2'];p.applyFilters();assert.equal(p.data.customers.length,65);
  assert.equal(JSON.stringify(p.data.customers.map(c=>c.id)),JSON.stringify(p.data.plotCustomers.map(c=>c.id)));
});
test('late totals cannot overwrite a newly selected period',async()=>{
  const deferred=[];const p=pageDefinition({getCustomerAssets:()=>new Promise(r=>deferred.push(r))});
  p.loadAssets();p.data.assetPeriod='all';p.loadAssets();
  deferred[1]({summary:{recognized_amount:700000,collection_amount:null},as_of:'2026-09-10'});await new Promise(r=>setImmediate(r));
  deferred[0]({summary:{recognized_amount:300000,collection_amount:null},as_of:'2026-09-10'});await new Promise(r=>setImmediate(r));
  assert.equal(p.data.recognizedText,'70');assert.equal(p.data.collectionText,'未登记');
});
test('small actual amounts never render as a confirmed zero',()=>assert.equal(amountWan(1),'0.0001'));

test('资产自动读取后端金额，不保留旧手动切换状态，明细沿用当前来源',async()=>{
 const calls=[];let url;
 const p=pageDefinition({getCustomerAssets:async params=>{
   calls.push(params);
   return {...assetResult(22315000),basis:params.basis==='entries'?'entries':'historical',historical_count:26,
    summary:{...assetResult(22315000).summary,recognized_amount:params.basis==='entries'?50000:540000,
      collection_amount:params.basis==='entries'?null:5552000,unknown_acv_count:89}};
 }},{},{navigateTo:options=>url=options.url});
 await p.loadAssets();assert.equal(p.data.acvText,'2,231.5');assert.equal(p.data.recognizedText,'54');
 assert.equal(p.data.collectionText,'555.2');assert.equal(p.data.assetResolvedBasis,'historical');
 p.openAssets({currentTarget:{dataset:{kind:'recognized'}}});assert.match(url,/basis=historical/);
 p.data.assetBasis='entries';await p.loadAssets();assert.equal(p.data.recognizedText,'54');
 assert.equal(p.data.collectionText,'555.2');assert.equal(p.data.assetResolvedBasis,'historical');
 p.openAssets({currentTarget:{dataset:{kind:'collection'}}});assert.match(url,/basis=historical/);
 assert.equal(calls[0].basis,'auto');assert.equal(calls[1].basis,'auto');
});

test('资产请求按 actual.read 隔离地图团队范围',()=>{
 const sales={globalData:{session:{role:'sales',permissions:{'actual.read':true},permissionGrants:[
   {permission_code:'actual.read',effect:'allow',scope_code:'self'}]}}};
 const salesPage=pageDefinition({},sales);salesPage.data.selectedTeam='sales-team';salesPage.data.selectedMember='sales-user';
 assert.equal(JSON.stringify(salesPage.assetParams()),JSON.stringify({scope:'self',basis:'auto',period:'year'}));

 const manager={globalData:{session:{role:'manager',permissions:{'actual.read':true},permissionGrants:[
   {permission_code:'actual.read',effect:'allow',scope_code:'workspace'}]}}};
 const managerPage=pageDefinition({},manager);managerPage.data.selectedTeam='manager-team';managerPage.data.selectedMember='manager-user';
 assert.equal(JSON.stringify(managerPage.assetParams()),JSON.stringify({scope:'team',basis:'auto',period:'year',team_id:'manager-team',owner_id:'manager-user'}));

 const fde={globalData:{session:{role:'fde',permissions:{'actual.read':true},permissionGrants:[
   {permission_code:'actual.read',effect:'allow',scope_code:'assigned'}]}}};
 const fdePage=pageDefinition({},fde);fdePage.data.selectedTeam='fde-team';
 assert.equal(JSON.stringify(fdePage.assetParams()),JSON.stringify({scope:'self',basis:'auto',period:'year'}));
});

test('资产接口失败和缺少金额字段不显示为零',async()=>{
 const p=pageDefinition({getCustomerAssets:async()=>{throw Error('暂不可用');}});
 await p.loadAssets();assert.equal(p.data.recognizedText,'—');assert.equal(p.data.acvText,'—');
 assert.equal(p.data.assetError,'暂不可用');
 const missing=pageDefinition({getCustomerAssets:async()=>({summary:{}})});await missing.loadAssets();
 assert.equal(missing.data.recognizedText,'—');assert.equal(missing.data.collectionText,'—');
 assert.ok(missing.data.acvError);
});

test('资产权限收回清除金额说明及展开状态，旧身份迟到响应不得恢复',async()=>{
 const pending=[],session={role:'sales',userId:'u',workspaceId:'w',permissionVersion:'v1',permissions:{'battle_map.read':true,'actual.read':true}};
 const app={globalData:{role:'sales',roles:{sales:{name:'销售'}},session},guardPage:()=>false};
 const p=pageDefinition({getCustomerAssets:()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))},app);
 const oldResult={...assetResult(),basis:'historical',historical_count:2,summary:{...assetResult().summary,
  recognized_confirmed_subtotal:20000,collection_confirmed_subtotal:30000,unknown_acv_count:1,
  period_precision_warning:'not_allocated_to_days',period_only_recognized_amount:990000}};
 const first=p.loadAssets();pending[0].resolve(oldResult);await first;
 assert.ok(p.data.assetFinancialHints.includes('待分配季度确收 99万'));p.data.assetNotesExpanded=true;
 p.mapIdentity=require('../miniprogram/utils/access').identity(session);
 app.globalData.session={...session,permissionVersion:'v2',permissions:{'battle_map.read':true,'actual.read':false}};
 p.onShow(); // Even a denied page guard must see cleared financial state.
 assert.equal(p.data.assetFinancialHints.length,0);assert.equal(p.data.assetNotesExpanded,false);
 assert.equal(p.data.recognizedPartial,false);assert.equal(p.data.collectionPartial,false);
 assert.equal(p.data.historicalAssetCount,0);assert.equal(p.data.unknownAcvCount,0);
 assert.equal(p.data.assetAsOf,'');assert.equal(p.data.assetResolvedBasis,'effective');
 app.globalData.session=session;const older=p.loadAssets();
 app.globalData.session={...session,permissionVersion:'v2',permissions:{'battle_map.read':true,'actual.read':false}};
 const denied=p.loadAssets();
 pending[2].reject(Object.assign(Error('资产权限已移除'),{statusCode:403}));await denied;
 pending[1].resolve(oldResult);await older;
 assert.equal(p.data.assetError,'资产权限已移除');assert.equal(p.data.assetSummary,null);
 assert.equal(p.data.assetFinancialHints.length,0);assert.equal(p.data.assetNotesExpanded,false);
 assert.equal(p.data.recognizedText,'—');assert.equal(p.data.assetLoading,false);
});


test('shared customers remain in each claimant map and team filter without duplicate dots',()=>{
 const shared={...customers[0],sales_members:[{id:'u1'},{id:'u2'}]};
 const directory=[{id:'u1',team_id:'t1'},{id:'u2',team_id:'t2'}];
 assert.deepEqual(scopedCustomers([shared],'t2','u2',directory).map(c=>c.id),['1']);
 assert.equal(scopedCustomers([shared],'all','u1',directory).length,1);
 assert.equal(scopedCustomers([shared],'all','u3',directory).length,0);
});

test('完整资产独立于活跃地图、搜索与周期；变更管理范围向接口重取',async()=>{
 const calls=[];
 const manager={globalData:{session:{role:'manager',permissions:{'actual.read':true},permissionGrants:[{permission_code:'actual.read',effect:'allow',scope_code:'workspace'}]}}};
 const p=pageDefinition({getCustomerAssets:async params=>{calls.push(params);return params.owner_id==='u1'?assetResult(3000000,50):assetResult();}},manager);
 p.data.role='manager';
 p.visibleCustomers=customers.map(c=>({...c,acv_amount:100}));
 await p.loadAssets();p.applyFilters();assert.equal(p.data.acvText,'900');assert.equal(p.data.scopeCustomerCount,200);assert.equal(p.data.activeCustomerCount,2);
 p.data.keyword='蓝海';p.applyFilters();assert.equal(p.data.customers.length,1);assert.equal(p.data.acvText,'900');
 p.data.selectedMember='u1';p.applyFilters();await p.loadAssets();assert.equal(p.data.acvText,'300');assert.equal(p.data.scopeCustomerCount,50);assert.equal(calls.at(-1).owner_id,'u1');
 p.data.assetPeriod='all';await p.loadAssets();assert.equal(p.data.acvText,'300');assert.equal(calls.at(-1).period,'all');
 p.data.isFde=true;p.visibleCustomers=[];p.applyFilters();assert.equal(p.data.acvText,'300');assert.equal(p.data.scopeCustomerCount,50);
});

test('资产字段缺失与未知金额均不以活跃客户ACV补算，零资产有效',async()=>{
 let response={summary:{recognized_amount:10000,collection_amount:0,customer_count:77}};
 const p=pageDefinition({getCustomerAssets:async()=>response});p.visibleCustomers=[{...customers[0],acv_amount:500000}];
 await p.loadAssets();p.applyFilters();assert.equal(p.data.acvText,'—');assert.equal(p.data.scopeCustomerCount,'—');assert.ok(p.data.acvError);assert.equal(p.data.recognizedText,'1');
 response=assetResult(null,10);response.summary.unknown_acv_count=1;await p.loadAssets();assert.equal(p.data.acvText,'—');assert.equal(p.data.scopeCustomerCount,10);assert.equal(p.data.unknownAcvCount,1);assert.equal(p.data.acvError,'');
 response=assetResult(0,0);await p.loadAssets();assert.equal(p.data.acvText,'0');assert.equal(p.data.scopeCustomerCount,0);
});

test('当前范围缺评分保留列表及已评分点位，筛选数量准确且真实零坐标有效',()=>{
 const {normalizeCustomerSummary,normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
 const rows=[{id:'zero',name:'零分',potential_score:0,relationship_score:0,quadrant_code:'order_driven'},
   {id:'missing',name:'缺潜力',potential_score:null,relationship_score:70,quadrant_code:'customer_asset'},
   {id:'empty',name:'未填写',potential_score:'',relationship_score:' '},
   {id:'invalid',name:'非法分',potential_score:101,relationship_score:20}].map(normalizeCustomerSummary);
 const p=pageDefinition();p.visibleCustomers=rows;p.applyFilters();
 assert.equal(p.data.activeCustomerCount,4);assert.equal(p.data.mapUpdating,true);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.customers.length,4);assert.equal(p.data.mapUnassessedCount,3);assert.equal(p.data.plotCustomers[0].potential,0);
 assert.equal(rows[1].potential,null);assert.equal(rows[1].quadrant,'待评估');assert.equal(rows[1].potentialText,'待评估');
 p.data.mapQuadrant='见单打单';p.data.keyword='零分';p.applyFilters();assert.equal(p.data.customers.length,1);assert.equal(p.data.mapUpdating,true);assert.equal(p.data.activeCustomerCount,1);assert.equal(p.data.mapUnassessedCount,0);
 p.visibleCustomers=[rows[0]];p.applyFilters();assert.equal(p.data.mapUpdating,false);assert.equal(p.data.customers.length,1);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.activeCustomerCount,1);
 const detail=normalizeCustomerDetail({id:'empty',name:'未评分',potential_score:null,relationship_score:null});assert.equal(detail.relationshipLevel,'待评估');assert.equal(detail.potentialBasis,'潜力待评估');assert.ok(detail.relationshipEvidence.includes('关系评分：待评估'));
});

test('缺评分与补评失败不隐藏已授权客户，团队筛选仍保持准确数量',()=>{
 const {normalizeCustomerSummary}=require('../miniprogram/utils/customerDetail');
 const rows=Array.from({length:74},(_,i)=>normalizeCustomerSummary({id:String(i),name:`活跃客户${i}`,
   owner_team_id:i<68?'ready':'incomplete',potential_score:i<68?70:null,relationship_score:i<68?50:null}));
 const p=pageDefinition();p.visibleCustomers=rows;p.applyFilters();
 assert.equal(p.visibleCustomers.length,74);assert.equal(p.data.activeCustomerCount,74);assert.equal(p.data.plotCustomers.length,68);assert.equal(p.data.customers.length,74);assert.equal(p.data.mapUnassessedCount,6);
 p.data.selectedTeam='ready';p.applyFilters();assert.equal(p.data.mapUpdating,false);assert.equal(p.data.activeCustomerCount,68);assert.equal(p.data.plotCustomers.length,68);assert.equal(p.data.customers.length,68);
 p.data.selectedTeam='all';p.applyFilters();assert.equal(p.data.mapUpdating,true);assert.equal(p.data.customers.length,74);assert.equal(p.visibleCustomers.length,74);
 p.failMapReview(Error('地图评估失败'));assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapError,'');assert.equal(p.data.mapReviewError,'地图评估失败');assert.equal(p.data.customers.length,74);assert.equal(p.data.plotCustomers.length,68);assert.equal(p.visibleCustomers.length,74);
 p.data.selectedTeam='ready';p.data.keyword='活跃客户6';p.applyFilters();assert.equal(p.data.mapError,'');assert.equal(p.data.mapReviewError,'');assert.equal(p.data.activeCustomerCount,9);assert.equal(p.data.plotCustomers.length,9);assert.equal(p.data.customers.length,9);
 const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customers/index.wxml'),'utf8');
 const chooserBoundary=wxml.indexOf('<sb-sheet visible="{{plotCandidates.length > 0}}"');
 assert.ok(chooserBoundary>0,'地图主体应在点位选择弹层之前');
 assert.match(wxml.slice(0,chooserBoundary),/activityWindow === 'all'/); // All-time customers remain visible without fabricated positions.
 assert.match(wxml,/'已加载范围显示 ' \+ activeCustomerCount \+ ' 家'/);
 assert.match(wxml,/'活跃客户列表'/);assert.match(wxml,/activityWindowOptions\[activityWindowIndex\]\.label.*activitySince.*mapAsOf/);
 assert.match(wxml,/客户潜力 × 关系深度.*筛选结果/);
});

test('未知金额不进入0–50万筛选，也不伪装成0元',()=>{
 const {normalizeCustomerSummary}=require('../miniprogram/utils/customerDetail');
 const rows=[null,0,500000].map((opportunity_amount,i)=>normalizeCustomerSummary({id:String(i),opportunity_amount}));
 assert.equal(rows[0].estimatedAmount,'—');assert.equal(rows[0].mapAmount,null);
 assert.deepEqual(filterCustomers(rows,{amount:{value:'0-50',min:0,max:499999.99}}).map(x=>x.id),['1']);
});

function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function loadingPage(api,timers){
 const toasts=[];
 const p=pageDefinition(api,{globalData:{role:'sales',roles:{sales:{name:'销售',scope:'本人'}},session:{team:'南区'}}},
  {showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(value){toasts.push(value.title);}},timers);
 p.loadAssets=()=>{};p.consumePendingCustomer=()=>{};
 return {p,toasts};
}
test('地图等待当前请求时显示加载状态，旧请求完成不能提前显示零客户',async()=>{
 const requests=[];
 const {p}=loadingPage({getCustomerMap:()=>{const next=deferred();requests.push(next);return next.promise;},
  getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]})});
 assert.equal(p.data.mapLoading,true);
 const old=p.loadData(),current=p.loadData();
 requests[0].resolve(mapResult());await old;
 assert.equal(p.data.mapLoading,true);
 requests[1].resolve(mapResult([{id:'current',name:'当前客户',potential_score:70,relationship_score:60}]));await current;
 assert.equal(p.data.mapLoading,false);assert.equal(p.data.activeCustomerCount,1);
 const failed=p.loadData();assert.equal(p.data.mapRefreshing,true);assert.equal(p.data.customers.length,1);
 requests[2].reject(Error('map offline'));await failed;
 assert.equal(p.data.mapLoading,false);assert.equal(p.data.mapRefreshError,'map offline');assert.equal(p.data.customers.length,1);
 const wxml=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customers/index.wxml'),'utf8');
 assert.match(wxml,/wx:if="\{\{mapLoading\}\}"[^>]*>正在加载客户/);
 assert.match(wxml,/!mapLoading && !mapError && !customers.length/);
});
test('地图本年计划包含只有季度的商机，跨年和未知计划不补造具体日期',async()=>{
 const items=[
  {id:'current-quarter',plan_close_periods:[{date:null,year:2026,quarter:3}]},
  {id:'next-quarter',plan_close_periods:[{date:null,year:2027,quarter:1}]},
  {id:'unknown',plan_close_periods:[{date:null,year:null,quarter:null}]},
  {id:'dated',plan_close_periods:[{date:'2026-09-25',year:2025,quarter:1}]},
  {id:'legacy',plan_close_dates:['2026-12-01']},
  {id:'empty',plan_close_periods:[],plan_close_dates:['2026-12-01']},
 ].map(item=>({...item,name:item.id,potential_score:70,relationship_score:50}));
 const {p}=loadingPage({getCustomerMap:async()=>mapResult(items),getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]})});
 p.data.currentPlanYear=2026;await p.loadData();
 const byId=Object.fromEntries(p.visibleCustomers.map(item=>[item.id,item]));
 assert.equal(byId['current-quarter'].agentPlanSegment,'current_year');
 assert.match(byId['current-quarter'].agentPlanReason,/2026 Q3/);assert.doesNotMatch(byId['current-quarter'].agentPlanReason,/2026-0[79]-/);
 assert.equal(byId['next-quarter'].agentPlanSegment,'long_term');assert.match(byId['next-quarter'].agentPlanReason,/跨年度/);
 assert.equal(byId.unknown.agentPlanSegment,'long_term');assert.match(byId.unknown.agentPlanReason,/尚未形成/);
 assert.equal(byId.dated.agentPlanSegment,'current_year');assert.match(byId.dated.agentPlanReason,/2026-09-25/);
 assert.equal(byId.legacy.agentPlanSegment,'current_year');assert.match(byId.legacy.agentPlanReason,/2026-12-01/);
 assert.equal(byId.empty.agentPlanSegment,'long_term');assert.match(byId.empty.agentPlanReason,/暂无活跃商机/);
 p.data.mapPlanSegment='current_year';p.applyFilters();
 assert.deepEqual(Array.from(p.data.customers,item=>item.id).sort(),['current-quarter','dated','legacy']);
 assert.equal(byId['current-quarter'].plan_close_periods[0].date,null);
});
test('customer facts display before a delayed directory and survive a directory failure',async()=>{
 const directory=deferred();const {p,toasts}=loadingPage({getCustomerMap:async()=>mapResult([{id:'real-customer',name:'来自数据库的客户',potential_score:80,relationship_score:30}]),getDirectoryMembers:()=>directory.promise});
 await p.loadData();assert.equal(p.data.customers.length,1);assert.equal(p.data.customers[0].id,'real-customer');
 directory.reject(Error('timeout'));await new Promise(r=>setImmediate(r));
 assert.equal(p.data.customers.length,1);assert.match(toasts[0],/成员筛选/);assert.ok(p.data.directoryError);assert.equal(p.data.directoryLoading,false);
});
test('late directory from an older map request cannot overwrite the current filter',async()=>{
 const old=deferred(),current=deferred();let calls=0;
 const {p}=loadingPage({getCustomerMap:async()=>mapResult(),getDirectoryMembers:()=>calls++===0?old.promise:current.promise});
 await p.loadData();await p.loadData();
 current.resolve({defaults:{team_id:null},teams:[{id:'new-team',name:'当前团队'}],items:[{id:'new-member',name:'当前成员',team_id:'new-team',team_ids:['new-team'],team:'当前团队'}]});await new Promise(r=>setImmediate(r));
 old.resolve({defaults:{team_id:null},teams:[{id:'old-team',name:'旧团队'}],items:[{id:'old-member',name:'旧成员',team_id:'old-team',team_ids:['old-team'],team:'旧团队'}]});await new Promise(r=>setImmediate(r));
 assert.equal(p.filterMembers[0].id,'new-member');
});
test('a failed map stays failed even when the directory succeeds later',async()=>{
 const directory=deferred();const {p}=loadingPage({getCustomerMap:async()=>{throw Error('map offline');},getDirectoryMembers:()=>directory.promise});
 await p.loadData();directory.resolve({teams:[],defaults:{team_id:null},items:[{id:'m',name:'成员',team_ids:[]}]});await new Promise(r=>setImmediate(r));
 assert.equal(p.visibleCustomers,null);assert.equal(p.data.customers.length,0);assert.equal(p.data.mapError,'map offline');
});

test('拒绝旧地图和卸载后响应；地图失败不清空独立资产且不请求完整看板',async()=>{
 const pending=[];let dashboardCalls=0;
 const {p}=loadingPage({getCustomerMap:()=>new Promise((resolve,reject)=>pending.push({resolve,reject})),
   getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),getDashboard:()=>{dashboardCalls++;throw Error('unused');}});
 const first=p.loadData(),second=p.loadData();
 pending[1].resolve(mapResult([{id:'new',name:'新结果',potential_score:80,relationship_score:40,acv_amount:1200000}]));await second;
 pending[0].resolve(mapResult([{id:'old',name:'旧结果',acv_amount:5}]));await first;
 assert.equal(p.data.acvText,'—');assert.equal(p.data.customers[0].id,'new');assert.equal(dashboardCalls,0);
 p.data.acvText='900';p.data.scopeCustomerCount=200;
 const failed=p.loadData();pending[2].reject(Error('map offline'));await failed;
 assert.equal(p.data.acvText,'900');assert.equal(p.data.scopeCustomerCount,200);
 const closing=p.loadData();p.onUnload();pending[3].resolve(mapResult([{id:'late',name:'迟到',acv_amount:1}]));await closing;
 assert.equal(p.data.customers.length,0);
});

test('旧地图接口未给活跃窗口时明确报错，不把全量客户当活跃',async()=>{
 const {p}=loadingPage({getCustomerMap:async()=>({items:[customers[0]]}),getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]})});
 await p.loadData();assert.equal(p.data.customers.length,0);assert.match(p.data.mapError,/客户时间范围/);
});

test('活动窗口使用后端业务日期，非法反向日期仍明确拒绝',async()=>{
 const queries=[];let response={...mapResult(),activity_since:'2026-09-20'};
 const {p}=loadingPage({getCustomerMap:async query=>{queries.push(query);return response;},getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]})});
 p.data.activityWindow='3d';await p.loadData();
 assert.equal(queries[0].activity_window,'3d');assert.equal(p.data.mapError,'');assert.equal(p.data.activitySince,'2026-09-20');assert.equal(p.data.mapAsOf,'2026-09-22');
 response={...mapResult(),activity_since:'2026-09-23'};await p.loadData();
 assert.match(p.data.mapError,/客户时间范围/);assert.equal(p.data.customers.length,0);
});

function pollClock(){
 let id=0;const timers=new Map();
 return {setTimeout(fn,ms){assert.equal(ms,3000);timers.set(++id,fn);return id;},clearTimeout(key){timers.delete(key);},
  get size(){return timers.size;},async next(){const [key,fn]=timers.entries().next().value;timers.delete(key);await fn();}};
}
const incompleteMapRow={id:'needs-score',name:'当年活跃客户',potential_score:null,relationship_score:null};
const completeMapRow={...incompleteMapRow,potential_score:75,relationship_score:20,quadrant_code:'main_attack'};

test('缺分只提交一次补评，轮询成功后数量、地图点和列表一致',async()=>{
 const clock=pollClock();let reads=0,posts=0;
 const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),
  getCustomerMap:async()=>mapResult([++reads===1?incompleteMapRow:completeMapRow]),
  refreshCustomerMap:async()=>{posts++;return {status:'updating',enqueued_count:1};}},clock);
 await p.loadData();assert.equal(posts,1);assert.equal(p.data.mapUpdating,true);assert.equal(p.data.customers.length,1);assert.equal(p.data.mapUnassessedCount,1);assert.equal(p.data.activeCustomerCount,1);assert.equal(clock.size,1);
 await clock.next();assert.equal(posts,1);assert.equal(reads,2);assert.equal(clock.size,0);assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapError,'');
 assert.equal(p.data.customers.length,1);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.activeCustomerCount,1);
 assert.equal(p.visibleCustomers[0].potential,75);
});

test('评分完整和零客户不触发模型补评',async()=>{
 let posts=0;const clock=pollClock();let result=mapResult([completeMapRow]);
 const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),getCustomerMap:async()=>result,
  refreshCustomerMap:async()=>{posts++;return {status:'updating'};}},clock);
 await p.loadData();result=mapResult();await p.loadData();assert.equal(posts,0);assert.equal(clock.size,0);assert.equal(p.data.mapUpdating,false);assert.equal(p.data.activeCustomerCount,0);
});

for(const response of [{status:'unavailable',reason:'当前模型未配置'},{status:'invalid'}]){
 test(`补评${response.status}非阻塞提示，保留混合评分列表及真实零点位`,async()=>{
  const clock=pollClock();let posts=0;
  const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),getCustomerMap:async()=>mapResult([incompleteMapRow,{...completeMapRow,id:'zero',potential_score:0,relationship_score:0}]),
   refreshCustomerMap:async()=>{posts++;return response;}},clock);
  await p.loadData();assert.equal(posts,1);assert.equal(clock.size,0);assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapError,'');assert.ok(p.data.mapReviewError);assert.equal(p.data.activeCustomerCount,2);assert.equal(p.data.customers.length,2);assert.equal(p.data.mapUnassessedCount,1);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.plotCustomers[0].potential,0);
 });
}

for(const statusCode of [401,403]) {
 test(`补评${statusCode}清除客户与点位，不降级为非阻塞提示`,async()=>{
  const clock=pollClock();
  const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),
   getCustomerMap:async()=>mapResult([incompleteMapRow,{...completeMapRow,id:'ready'}]),
   refreshCustomerMap:async()=>{throw Object.assign(Error('地图权限已失效'),{statusCode});}},clock);
  p.data.acvText='900';await p.loadData();
  assert.equal(p.visibleCustomers,null);assert.equal(p.data.customers.length,0);assert.equal(p.data.plotCustomers.length,0);
  assert.equal(p.data.mapReviewError,'');assert.equal(p.data.mapError,'地图权限已失效');assert.equal(p.data.acvText,'—');assert.equal(clock.size,0);
 });
}

test('最多轮询10次后停止，保留完整客户数并提示稍后重试',async()=>{
 const clock=pollClock();let posts=0,reads=0;
 const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),getCustomerMap:async()=>{reads++;return mapResult([incompleteMapRow]);},
  refreshCustomerMap:async()=>{posts++;return {status:'ready',enqueued_count:0};}},clock);
 await p.loadData();for(let i=0;i<10;i++)await clock.next();
 assert.equal(posts,1);assert.equal(reads,11);assert.equal(clock.size,0);assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapError,'');assert.match(p.data.mapReviewError,/稍后重试/);assert.equal(p.data.customers.length,1);assert.equal(p.data.mapUnassessedCount,1);assert.equal(p.data.activeCustomerCount,1);assert.equal(p.visibleCustomers.length,1);
});

for(const exit of ['onHide','onUnload']){
 test(`${exit}清除轮询且迟到补评回执不能重新启动`,async()=>{
  const clock=pollClock();const post=deferred();
  const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),getCustomerMap:async()=>mapResult([incompleteMapRow]),refreshCustomerMap:()=>post.promise},clock);
  const loading=p.loadData();await new Promise(r=>setImmediate(r));p[exit]();post.resolve({status:'updating'});await loading;assert.equal(clock.size,0);
 });
}

test('新加载使旧轮询响应失效，旧响应不能替换客户或重新排队',async()=>{
 const clock=pollClock(),late=deferred();let reads=0;
 const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),
  getCustomerMap:()=>{reads++;return reads===2?late.promise:Promise.resolve(mapResult([reads===1?incompleteMapRow:{...completeMapRow,id:'new'}]));},
  refreshCustomerMap:async()=>({status:'updating'})},clock);
 await p.loadData();const polling=clock.next();await p.loadData();late.resolve(mapResult([{...completeMapRow,id:'old'}]));await polling;
 assert.equal(p.data.customers[0].id,'new');assert.equal(p.visibleCustomers[0].id,'new');assert.equal(clock.size,0);
});

test('已排队轮询在隐藏或新加载时清理，轮询失败仍允许完整团队筛选',async()=>{
 const clock=pollClock();let reads=0;
 const {p}=loadingPage({getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),
  getCustomerMap:async()=>{if(++reads===2)throw Error('地图网络中断');return mapResult([{...incompleteMapRow,owner_team_id:'bad'},{...completeMapRow,id:'good',owner_team_id:'good'}]);},
  refreshCustomerMap:async()=>({status:'updating'})},clock);
 await p.loadData();await clock.next();assert.equal(clock.size,0);assert.equal(p.data.mapError,'');assert.equal(p.data.mapReviewError,'地图网络中断');assert.equal(p.data.customers.length,2);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.visibleCustomers.length,2);
 p.data.selectedTeam='good';p.applyFilters();assert.equal(p.data.mapError,'');assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.activeCustomerCount,1);
 await p.loadData();assert.equal(clock.size,1);p.onHide();assert.equal(clock.size,0);
});

test('重置团队人员筛选重新取完整资产，旧范围资产迟到也不能覆盖',async()=>{
 const requests=[];const manager={globalData:{session:{role:'manager',permissions:{'actual.read':true},permissionGrants:[{permission_code:'actual.read',effect:'allow',scope_code:'workspace'}]}}};
 const p=pageDefinition({getCustomerAssets:params=>{const wait=deferred();requests.push({params,...wait});return wait.promise;}},manager);
 p.visibleCustomers=customers;p.data.role='manager';p.data.selectedTeam='t1';p.data.selectedMember='u1';
 p.loadData=function(){return this.loadAssets();};const older=p.loadAssets();p.resetAllFilters();assert.equal(requests.length,2);assert.equal(requests[1].params.scope,'team');assert.equal(requests[1].params.team_id,undefined);assert.equal(requests[1].params.owner_id,undefined);
 requests[1].resolve(assetResult(9000000,200));await new Promise(r=>setImmediate(r));
 requests[0].resolve(assetResult(3000000,50));await older;assert.equal(p.data.acvText,'900');assert.equal(p.data.scopeCustomerCount,200);
});

test('新账号加载立即失效旧资产响应，地图失败仍展示已读取的当前资产',async()=>{
 const requests=[];const app={globalData:{role:'sales',roles:{sales:{name:'销售',scope:'本人'}},session:{role:'sales',userId:'one'}}};
 const p=pageDefinition({getCustomerMap:async()=>{throw Error('map offline');},getDirectoryMembers:async()=>({teams:[],defaults:{team_id:null},items:[]}),
   getCustomerAssets:()=>{const wait=deferred();requests.push(wait);return wait.promise;}},app,{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){}});
 await p.loadData();app.globalData.session.userId='two';await p.loadData();requests[1].resolve(assetResult(1000000,4));await new Promise(r=>setImmediate(r));
 requests[0].resolve(assetResult(9000000,200));await new Promise(r=>setImmediate(r));assert.equal(p.data.acvText,'100');assert.equal(p.data.scopeCustomerCount,4);assert.equal(p.data.mapError,'map offline');
});

test('overlapping hit areas expose the middle customer regardless of draw order',()=>{
 const {overlappingPlotIds}=require('../miniprogram/utils/customerMap');
 const rect=(id,left,top)=>({dataset:{id},left,top,right:left+30,bottom:top+30});
 const rects=[rect('left',90,90),rect('middle',100,100),rect('topmost',110,110),rect('separate',300,300)];
 assert.deepEqual(overlappingPlotIds(rects,'topmost'),['left','middle','topmost']);
 assert.deepEqual(overlappingPlotIds(rects,'separate'),['separate']);
 assert.deepEqual(overlappingPlotIds(rects,'missing'),[]);
 const same=Array.from({length:12},(_,i)=>rect(String(i),10,10));
 assert.equal(overlappingPlotIds(same,'11').length,12);
});
test('map chooser opens selected customer and single point bypasses chooser',()=>{
 let callback;const wxStub={hideTabBar(){},showTabBar(){},createSelectorQuery(){return {in(){return this;},selectAll(){return this;},fields(_,cb){callback=cb;return this;},exec(){}};}};
 const p=pageDefinition({}, {}, wxStub);const opened=[];p.showBattleCustomer=id=>opened.push(id);
 p.data.plotCustomers=[{id:'1',name:'中间'},{id:'2',name:'上层'}];
 p.openBattleCustomer({currentTarget:{dataset:{id:'2'}}});
 callback([{dataset:{id:'1'},left:0,right:30,top:0,bottom:30},{dataset:{id:'2'},left:10,right:40,top:10,bottom:40}]);
 assert.equal(p.data.plotCandidates.length,2);assert.equal(opened.length,0);
 p.choosePlotCustomer({currentTarget:{dataset:{id:'1'}}});assert.deepEqual(opened,['1']);assert.equal(p.data.plotCandidates.length,0);
 p.openBattleCustomer({currentTarget:{dataset:{id:'2'}}});
 callback([{dataset:{id:'1'},left:0,right:30,top:0,bottom:30},{dataset:{id:'2'},left:80,right:110,top:80,bottom:110}]);
 assert.deepEqual(opened,['1','2']);
 p.openBattleCustomer({currentTarget:{dataset:{id:'2'}}});p.data.plotCustomers=[];
 callback([{dataset:{id:'2'},left:80,right:110,top:80,bottom:110}]);assert.equal(opened.length,2);
});

test('地图选项保留无成员无客户的后端团队，不从客户或成员补造团队',()=>{
 const p=pageDefinition();p.data.role='manager';p.visibleCustomers=[{id:'c',name:'客户',owner_user_ref_id:'u',owner_team_id:'legacy',acv_amount:100}];
 p.applyMapDirectory({teams:[{id:'empty',name:'新业务团队'},{id:'real',name:'真实团队'}],items:[{id:'u',name:'成员',team_id:'legacy',team:'旧分组',team_ids:['real']}]},false);
 assert.deepEqual(Array.from(p.data.teamOptions,row=>row.value),['all','empty','real']);
 assert.equal(scopedCustomers(p.visibleCustomers,'empty','all',p.filterMembers).length,0);
 assert.equal(scopedCustomers(p.visibleCustomers,'real','all',p.filterMembers).length,1);
});
