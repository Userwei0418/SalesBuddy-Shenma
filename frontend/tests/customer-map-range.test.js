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
function setup(read) {
  let p;const calls=[],posts=[],timers=[];
  const session={role:'sales',userId:'u',workspaceId:'w',permissionVersion:'v1',permissions:{'battle_map.read':true},permissionGrants:[{permission_code:'battle_map.read',effect:'allow',scope_code:'teams'}]};
  const app={globalData:{role:'sales',roles:{sales:{name:'销售'}},session}};
  const api={getDirectoryMembers:async()=>({items:[],teams:[],defaults:{team_id:null}}),getCustomerMap:params=>{calls.push(params);return read(params);},refreshCustomerMap:async params=>{posts.push(params);return {status:'updating'};}};
  const filename=path.resolve(__dirname,'../miniprogram/pages/customers/index.js');
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:x=>p=x,require:n=>n.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),n)),getApp:()=>app,Date,Map,Set,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:id=>{timers[id-1]=null;},
    wx:{showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast(){}}});
  p.data=JSON.parse(JSON.stringify(p.data));p.setData=function(v,cb){Object.assign(this.data,v);if(cb)cb();};
  p.loadAssets=()=>{};p.consumePendingCustomer=()=>{};
  return {p,app,calls,posts,timers};
}
test('默认不限跟进且显式有界请求：缺评分保留列表、不补造点位、不POST评分',async()=>{
  const {p,calls,posts,timers}=setup(async q=>result(q,[row('scored'),row('pending',false)]));
  assert.equal(p.data.activityWindow,'all');
  assert.deepEqual(Array.from(p.data.activityWindowOptions,x=>x.value),['all','today','3d','week','month','quarter','year']);
  await p.loadData();assert.equal(p.data.mapError,'');
  assert.deepEqual({...calls[0]},{scope:'team',activity_window:'all',page_size:200,offset:0});
  assert.equal(p.data.customers.length,2);assert.equal(p.data.plotCustomers.length,1);
  assert.equal(p.data.plotCustomers[0].potential,0);assert.equal(p.data.mapUnassessedCount,1);
  assert.equal(p.data.customers[1].quadrant,'待评估');assert.equal(p.data.mapUpdating,false);
  assert.equal(posts.length,0);assert.equal(timers.length,0);
});
test('超过首屏200客户须手动继续，累计去重且不把首屏当总数',async()=>{
  const {p,calls,posts}=setup(async q=>q.offset===0?result(q,Array.from({length:200},(_,i)=>row(String(i))),true):result(q,[row('199'),row('200',false)]));
  await p.loadData();assert.equal(calls.length,1);assert.equal(p.data.mapHasMore,true);assert.equal(p.data.mapLoadedCount,200);
  await p.loadMoreMapCustomers();assert.equal(calls.length,2);assert.equal(calls[1].offset,200);
  assert.equal(p.data.mapLoadedCount,201);assert.equal(p.data.customers.length,201);assert.equal(p.data.plotCustomers.length,200);assert.equal(p.data.mapHasMore,false);
  await p.loadMoreMapCustomers();assert.equal(calls.length,2);assert.equal(posts.length,0);
  p.search({detail:{value:'客户200'}});assert.equal(p.data.customers.length,1);assert.equal(p.data.mapLoadedCount,201);assert.equal(p.data.mapUnassessedCount,1);
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
  late.resolve(result({activity_window:'all',page_size:200,offset:1},[row('late')]));await pending;
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
  assert.equal(posts.length,1);assert.deepEqual({...posts[0]},{scope:'team',activity_window:'quarter',page_size:200,offset:1});
  assert.equal(p.data.mapUpdating,true);assert.equal(timers.filter(Boolean).length,1);
  await timers.find(Boolean)();assert.equal(p.data.mapUpdating,false);assert.equal(p.data.mapLoadedCount,2);assert.deepEqual(Array.from(p.data.customers,x=>x.id),['first','second']);
});
test('季度补评失败须先重试当前范围，不允许下一页造成无限更新状态',async()=>{
  let reads=0;
  const {p,calls,timers}=setup(async q=>{if(++reads===2)throw Error('评分查询中断');return result(q,[row('first',false)],true);});
  p.data.activityWindow='quarter';await p.loadData();await timers.find(Boolean)();
  assert.equal(p.data.mapError,'评分查询中断');assert.equal(p.data.mapUpdating,false);
  await p.loadMoreMapCustomers();assert.equal(calls.length,2);assert.equal(p.data.mapUpdating,false);
});
test('重置时间和团队会重新取默认全部，资产汇总不靠地图客户计算',async()=>{
  const {p,calls}=setup(async q=>result(q,[row(q.activity_window)]));let assets=0;p.loadAssets=()=>{assets++;};
  p.data.activityWindow='quarter';await p.loadData();p.resetMapFilters();await tick();assert.equal(calls.at(-1).activity_window,'all');assert.equal(p.data.customers[0].id,'all');
  p.data.selectedTeam='t';p.resetAllFilters();await tick();assert.equal(calls.at(-1).team_id,undefined);assert.equal(calls.length,3);assert.equal(assets,3);
});
