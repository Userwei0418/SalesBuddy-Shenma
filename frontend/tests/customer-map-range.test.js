const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {validateMapPage} = require('../miniprogram/utils/customerMap');
const tick = () => new Promise(resolve=>setImmediate(resolve));
const wait = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const row = (id, scored=true) => ({id,name:'客户'+id,potential_score:scored?0:null,relationship_score:scored?60:null});
const result = (params,items=[],has_more=false) => ({items,activity_window:params.activity_window,
  activity_since:params.activity_window==='all'?null:'2026-10-01',as_of:'2026-10-13',
  refresh_allowed:params.activity_window!=='all',page_size:params.page_size,offset:params.offset,
  returned_count:items.length,has_more,next_offset:has_more?params.offset+items.length:null});
function setup(read, activityWindow='all') {
  let p;const calls=[],posts=[],timers=[];
  const session={role:'sales',userId:'u',workspaceId:'w',permissionVersion:'v1',permissions:{'battle_map.read':true},permissionGrants:[{permission_code:'battle_map.read',effect:'allow',scope_code:'teams'}]};
  const app={globalData:{role:'sales',roles:{sales:{name:'销售'}},session},ensureLogin:()=>true};
  const api={getDirectoryMembers:async()=>({items:[],teams:[],defaults:{team_id:null}}),getCustomerMap:params=>{calls.push(params);return read(params);},refreshCustomerMap:async params=>{posts.push(params);return {status:'updating'};}};
  const filename=path.resolve(__dirname,'../miniprogram/pages/customers/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:x=>p=x,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),n)),getApp:()=>app,Date,Map,Set,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
    wx:{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){}}});
  p.data=JSON.parse(JSON.stringify(p.data));p.setData=function(v,cb){Object.assign(this.data,v);if(cb)cb();};
  // Pagination cases explicitly exercise all-time reads. Null keeps the page's
  // real default for the initial-entry regression below.
  if(activityWindow!==null){p.data.activityWindow=activityWindow;p.data.activityWindowIndex=p.data.activityWindowOptions.findIndex(option=>option.value===activityWindow);}
  p.loadAssets=()=>{};p.consumePendingCustomer=()=>{};
  return {p,app,calls,posts,timers};
}
test('默认本月的显示与请求一致，手选不限跟进后返回页面仍保留选择',async()=>{
  const {p,calls}=setup(async q=>result(q,[row('scored')]),null);
  assert.equal(p.data.activityWindowOptions[p.data.activityWindowIndex].label,'本月有跟进');
  await p.loadData();assert.equal(calls[0].activity_window,'month');
  p.changeActivityWindow({detail:{value:'all'}});await tick();
  p.onHide();p.onShow();await tick();
  assert.equal(calls.at(-1).activity_window,'all');
  assert.equal(p.data.activityWindowOptions[p.data.activityWindowIndex].label,'不限跟进时间');
  p.onHide();
});
test('手选不限跟进且显式有界请求：缺评分保留列表、不补造点位、不POST评分',async()=>{
  const {p,calls,posts,timers}=setup(async q=>result(q,[row('scored'),row('pending',false)]));
  assert.deepEqual(Array.from(p.data.activityWindowOptions,x=>x.value),['all','today','3d','week','month','quarter','year']);
  p.changeActivityWindow({detail:{value:'all'}});await tick();assert.equal(p.data.mapError,'');
  assert.deepEqual({...calls[0]},{scope:'team',activity_window:'all',page_size:50,offset:0});
  assert.equal(p.data.customers.length,2);assert.equal(p.data.plotCustomers.length,1);
  assert.equal(p.data.plotCustomers[0].potential,0);assert.equal(p.data.mapUnassessedCount,1);
  assert.equal(p.data.customers[1].quadrant,'待评估');assert.equal(p.data.mapUpdating,false);
  assert.equal(posts.length,0);assert.equal(timers.length,0);
});
test('超过首屏50客户滚动继续，累计去重且不把首屏当总数',async()=>{
  const {p,calls,posts}=setup(async q=>q.offset===0?result(q,Array.from({length:50},(_,i)=>row(String(i))),true):result(q,[row('49'),row('50',false)]));
  p.changeActivityWindow({detail:{value:'all'}});await tick();assert.equal(calls.length,1);assert.equal(p.data.mapHasMore,true);assert.equal(p.data.mapLoadedCount,50);
  await p.loadMoreMapCustomers();assert.equal(calls.length,2);assert.equal(calls[1].offset,50);
  assert.equal(p.data.mapLoadedCount,51);assert.equal(p.data.customers.length,51);assert.equal(p.data.plotCustomers.length,50);assert.equal(p.data.mapHasMore,false);
  await p.loadMoreMapCustomers();assert.equal(calls.length,2);assert.equal(posts.length,0);
  p.search({detail:{value:'客户50'}});assert.equal(p.data.customers.length,1);assert.equal(p.data.mapLoadedCount,51);assert.equal(p.data.mapUnassessedCount,1);
});
test('下一页失败保留已加载客户、重试原offset，连点不重复请求',async()=>{
  const next=wait();let n=0;
  const {p,calls}=setup(q=>q.offset===0?Promise.resolve(result(q,[row('a')],true)):++n===1?next.promise:Promise.resolve(result(q,[row('b')])));
  await p.loadData();const loading=p.loadMoreMapCustomers();await p.loadMoreMapCustomers();assert.equal(calls.length,2);
  next.reject(Error('网络中断'));await loading;assert.equal(p.data.mapMoreError,'网络中断');assert.equal(p.data.customers.length,1);assert.equal(p.data.mapHasMore,true);
  await p.loadMoreMapCustomers();assert.equal(calls[2].offset,1);assert.equal(p.data.mapMoreError,'');assert.equal(p.data.customers.length,2);
});
for(const change of ['window','identity','hidden','team']) test(`${change}变化后迟到分页不得混入当前范围`,async()=>{
  const late=wait();
  const {p,app}=setup(q=>q.offset>0?late.promise:Promise.resolve(result(q,[row('first')],true)));
  await p.loadData();const pending=p.loadMoreMapCustomers();
  if(change==='window'){p.changeActivityWindow({detail:{value:'quarter'}});await tick();}
  if(change==='identity')app.globalData.session={...app.globalData.session,userId:'other'};
  if(change==='hidden')p.onHide();
  if(change==='team'){p.data.selectedTeam='other';await p.loadData();}
  late.resolve(result({activity_window:'all',page_size:50,offset:1},[row('late')]));await pending;
  assert.equal(p.data.customers.some(x=>x.id==='late'),false);
});
test('后端旧响应、错误游标或跨日分页均不伪装成全部客户',async()=>{
  const {p}=setup(async()=>({items:[row('old')],activity_since:'2026-01-01',as_of:'2026-10-13'}));
  await p.loadData();assert.match(p.data.mapError,/后端版本/);assert.equal(p.data.customers.length,0);
  const q={activity_window:'all',page_size:200,offset:200},base=result(q,[row('next')],true);
  for(const patch of [{next_offset:200},{offset:0},{returned_count:2},{page_size:500},{items:[row('x'),row('x')],returned_count:2,next_offset:202},{has_more:'true'}]) assert.throws(()=>validateMapPage({...base,...patch},q),/分页/);
  assert.throws(()=>validateMapPage(base,q,'2026-10-12'),/日期已变化/);
});
test('季度第二页补评仅传该页，轮询合并保留前页；全部窗从不补评',async()=>{
  let second=0;
  const {p,posts,timers}=setup(async q=>q.offset===0?result(q,[row('first')],true):result(q,[row('second',++second>1)]));
  p.data.activityWindow='quarter';await p.loadData();await p.loadMoreMapCustomers();
  assert.equal(posts.length,1);assert.deepEqual({...posts[0]},{scope:'team',activity_window:'quarter',page_size:50,offset:1});
  assert.equal(p.data.mapUpdating,true);assert.equal(timers.filter(Boolean).length,1);
  await timers.find(Boolean)();assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapLoadedCount,2);assert.deepEqual(Array.from(p.data.customers,x=>x.id),['first','second']);
});
test('季度补评失败不阻塞翻页，新鲜读取可重试补评并恢复准确点位',async()=>{
  let reads=0;
  const {p,calls,posts,timers}=setup(async q=>{if(++reads===2)throw Error('评分查询中断');return q.offset?result(q,[row('second')]):result(q,[row('first',reads>2)],true);});
  p.data.activityWindow='quarter';await p.loadData();await timers.find(Boolean)();
  assert.equal(p.data.mapError,'');assert.equal(p.data.mapReviewError,'评分查询中断');assert.equal(p.data.mapUpdating,false);assert.equal(p.data.customers.length,1);
  await p.loadMoreMapCustomers();assert.equal(calls.length,3);assert.equal(p.data.customers.length,2);assert.equal(p.data.plotCustomers.length,1);assert.equal(p.data.mapUnassessedCount,1);assert.equal(posts.length,2);
  await timers.at(-1)();assert.equal(p.data.mapReviewError,'');assert.equal(p.data.mapUpdating,false);assert.equal(p.data.plotCustomers.length,2);assert.equal(p.data.mapUnassessedCount,0);
});
test('补评在途仍可分页且不启动第二条补评链',async()=>{
  const {p,calls,posts,timers}=setup(async q=>result(q,[row(q.offset?'second':'first',false)],q.offset===0),'quarter');
  await p.loadData();await p.loadMoreMapCustomers();
  assert.equal(calls.length,2);assert.equal(posts.length,1);assert.equal(timers.filter(Boolean).length,1);
  assert.equal(p.data.customers.length,2);assert.equal(p.data.mapUnassessedCount,2);assert.equal(p.data.plotCustomers.length,0);p.onHide();
});
test('重置时间和团队回到默认本月，资产汇总不靠地图客户计算',async()=>{
  const {p,calls}=setup(async q=>result(q,[row(q.activity_window)]));let assets=0;p.loadAssets=()=>{assets++;};
  p.data.activityWindow='quarter';await p.loadData();p.resetMapFilters();await tick();assert.equal(calls.at(-1).activity_window,'month');assert.equal(p.data.customers[0].id,'month');
  assert.equal(p.data.activityWindowOptions[p.data.activityWindowIndex].label,'本月有跟进');
  p.data.selectedTeam='t';p.resetAllFilters();await tick();assert.equal(calls.at(-1).team_id,undefined);assert.equal(calls.length,3);assert.equal(assets,3);
  p.changeActivityWindow({detail:{value:'all'}});await tick();p.clearOperatingFilters();await tick();
  assert.equal(calls.at(-1).activity_window,'month');assert.equal(p.data.activityWindowOptions[p.data.activityWindowIndex].value,'month');
});


test('接近底部仅有一页在途，失败暂停预取并允许手动补偿',async()=>{
  const next=wait();let second=0;
  const {p,calls}=setup(q=>q.offset===0?Promise.resolve(result(q,[row('first')],true)):++second===1?next.promise:Promise.resolve(result(q,[row('second')])));
  await p.loadData();const pending=p.onReachBottom();await p.onReachBottom();assert.equal(calls.length,2);
  next.reject(Error('请求超时'));await pending;
  await p.onReachBottom();assert.equal(calls.length,2);assert.equal(p.data.customers[0].id,'first');
  await p.loadMoreMapCustomers();assert.equal(calls.length,3);assert.equal(p.data.customers.length,2);
  assert.equal(JSON.parse(fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customers/index.json'))).onReachBottomDistance,480);
});
test('同一范围刷新失败保留旧数据、日期和游标，暂停翻页直到刷新完成',async()=>{
  let fail=false;
  const {p,calls}=setup(async q=>{if(fail)throw Error('请求超时');return result(q,[row('saved')],true);});
  await p.loadData();const cursor=p.data.mapNextOffset,asOf=p.data.mapAsOf;fail=true;
  await p.loadData();assert.equal(p.data.mapError,'');assert.equal(p.data.mapRefreshError,'请求超时');
  assert.equal(p.data.customers[0].id,'saved');assert.equal(p.data.mapNextOffset,cursor);assert.equal(p.data.mapAsOf,asOf);
  await p.onReachBottom();await p.loadMoreMapCustomers();assert.equal(calls.length,2);
  fail=false;await p.loadData();assert.equal(p.data.mapRefreshError,'');assert.equal(p.data.customers.length,1);
});
test('多页刷新整体校验后才替换，失败不得混合新旧分页',async()=>{
  let refresh=false;const late=wait();
  const {p}=setup(q=>!refresh?Promise.resolve(result(q,[row(q.offset?'old-b':'old-a')],q.offset===0)):q.offset?late.promise:Promise.resolve(result(q,[row('new-a')],true)));
  await p.loadData();await p.loadMoreMapCustomers();refresh=true;const pending=p.loadData();await tick();
  assert.deepEqual(Array.from(p.data.customers,x=>x.id),['old-a','old-b']);assert.equal(p.data.mapRefreshing,true);
  late.reject(Error('第二页超时'));await pending;
  assert.deepEqual(Array.from(p.data.customers,x=>x.id),['old-a','old-b']);assert.equal(p.data.mapLoadedCount,2);assert.equal(p._mapPages.size,2);
});
test('返回刷新最多四页，完成后显式提示继续加载，沿用新游标',async()=>{
  const {p,calls}=setup(async q=>result(q,[row(String(q.offset))],true));
  await p.loadData();for(let i=0;i<5;i++)await p.loadMoreMapCustomers();assert.equal(p._mapPages.size,6);
  const before=calls.length;await p.loadData();assert.equal(calls.length-before,4);assert.equal(p._mapPages.size,4);
  assert.equal(p.data.mapLoadedCount,4);assert.equal(p.data.mapNextOffset,4);assert.equal(p.data.mapRefreshTruncated,true);
});
for(const statusCode of [401,403])test(`分页${statusCode}立即清除旧地图、目录与资产，不作为可保留的网络失败`,async()=>{
  const {p}=setup(async q=>{if(q.offset)throw Object.assign(Error('权限已变化'),{statusCode});return result(q,[row('private')],true);});
  await p.loadData();p.data.acvText='900';p.data.scopeCustomerCount=357;p.data.memberPickerMembers=[{id:'private'}];
  await p.loadMoreMapCustomers();assert.equal(p.visibleCustomers,null);assert.equal(p.data.customers.length,0);assert.equal(p.data.plotCustomers.length,0);
  assert.equal(p.data.acvText,'—');assert.equal(p.data.memberPickerMembers.length,0);assert.equal(p.data.mapHasMore,false);assert.equal(p.data.mapError,'权限已变化');
});
test('身份变化时迟到分页拒绝回填，同时清除已有身份的结果',async()=>{
  const next=wait();const {p,app}=setup(q=>q.offset?next.promise:Promise.resolve(result(q,[row('private')],true)));
  await p.loadData();const pending=p.loadMoreMapCustomers();app.globalData.session={...app.globalData.session,userId:'other'};
  next.resolve(result({activity_window:'all',page_size:50,offset:1},[row('late')]));await pending;
  assert.equal(p.data.customers.length,0);assert.equal(p.visibleCustomers,null);assert.match(p.data.mapError,/账号或权限/);
});
test('打开客户详情时底部事件不会继续后台加载客户',async()=>{
  const {p,calls}=setup(async q=>result(q,[row('first')],true));await p.loadData();
  p.data.selectedCustomer={id:'first'};await p.onReachBottom();assert.equal(calls.length,1);
});

test('同范围刷新遇到权限拒绝不保留缓存，迟到目录也不能恢复旧数据',async()=>{
  let denied=false;const {p}=setup(async q=>{if(denied)throw Object.assign(Error('无权查看'),{statusCode:403});return result(q,[row('private')],true);});
  await p.loadData();denied=true;await p.loadData();
  assert.equal(p.visibleCustomers,null);assert.equal(p.data.mapLoadedCount,0);assert.equal(p.data.mapRefreshError,'');assert.equal(p.data.mapError,'无权查看');
});
test('刷新后的后续页跨日失败保留整个旧窗口，而不是接入新游标',async()=>{
  let refresh=false;
  const {p}=setup(async q=>({...result(q,[row(`${refresh?'new':'old'}-${q.offset}`)],q.offset===0),as_of:refresh&&q.offset?'2026-10-14':'2026-10-13'}));
  await p.loadData();await p.loadMoreMapCustomers();refresh=true;await p.loadData();
  assert.deepEqual(Array.from(p.data.customers,x=>x.id),['old-0','old-1']);assert.equal(p.data.mapAsOf,'2026-10-13');
  assert.equal(p.data.mapNextOffset,null);assert.match(p.data.mapRefreshError,/日期已变化/);
});
