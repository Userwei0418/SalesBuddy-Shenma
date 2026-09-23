require('./helpers/business-options');
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const pageResponse=require('./helpers/opportunity-pages');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const definitions=[
 {file:'pages/opportunities/index.js',load:'loadPage',more:'loadMore',rows:'filtered',total:'total',busy:'loading',moreBusy:'loadingMore'},
 {file:'pages/workbench/index.js',load:'loadAllOpportunities',more:'loadMoreOpportunities',rows:'filteredOpportunities',total:'opportunityTotal',busy:'opportunityListLoading',moreBusy:'opportunityLoadingMore'},
 {file:'components/fde-projects/index.js',load:'load',more:'loadMore',rows:'filtered',total:'count',busy:'loading',moreBusy:'loadingMore',component:true},
];
function setup(spec, clock={setTimeout,clearTimeout}){
 let definition;
 const filename=path.resolve(__dirname,'../miniprogram',spec.file),requests=[];
 const app={globalData:{role:spec.component?'fde':'sales',session:{workspaceId:'w',userId:'u',role:spec.component?'fde':'sales',loginAt:'first',permissionVersion:'one',capabilities:{}}}};
 const api={listOpportunities:params=>{const pending={...deferred(),params};requests.push(pending);return pending.promise;}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:d=>{definition=d;},Component:d=>{definition=d;},require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),getApp:()=>app,Date,Set,Map,...clock,wx:{}});
 const page={...definition,...(definition.methods||{}),data:JSON.parse(JSON.stringify(definition.data)),properties:{customerId:'',memberId:'',initialScope:''},setData(values,callback){Object.assign(this.data,values);if(callback)callback();}};
 page.setData({year:2026});
 return {page,requests,app};
}
const fixtures=n=>Array.from({length:n},(_,i)=>({id:String(i),name:`项目${i}`,customer_name:'客户',amount:100,status:'open',stage_code:'identified',probability:10,expected_close_date:'2026-10-01',actuals:{recognized_amount:null,collection_amount:null}}));
for(const spec of definitions){
 test(`${spec.file}: 1/4/20页首屏只有一次商机请求，全集汇总不等于已加载条数`,async()=>{
  for(const count of [20,80,400]){
   const {page,requests}=setup(spec),rows=fixtures(count),pending=page[spec.load]();
   assert.equal(requests.length,1);assert.equal(requests[0].params.pageSize,20);
   requests[0].resolve(pageResponse(rows,requests[0].params));await pending;await tick();
   assert.equal(page.data[spec.busy],false);assert.equal(page.data[spec.rows].length,20);
   assert.equal(page.data[spec.total],count);assert.equal(requests.length,1);
   if(count>20){const more=page[spec.more]();page[spec.more]();assert.equal(requests.length,2,'同一页不可并发重复读取');assert.equal(requests[1].params.offset,20);requests[1].resolve(pageResponse(rows,requests[1].params));await more;assert.equal(page.data[spec.rows].length,40);assert.equal(new Set(page.data[spec.rows].map(row=>row.id)).size,40);assert.equal(page.data[spec.total],count);}
  }
 });
 test(`${spec.file}: 翻页失败保留首屏，重试同一offset；换筛选拒收旧页`,async()=>{
  const {page,requests}=setup(spec),rows=fixtures(80),first=page[spec.load]();requests[0].resolve(pageResponse(rows,requests[0].params));await first;
  const second=page[spec.more]();requests[1].reject(Error('下一页超时'));await second;
  assert.equal(page.data[spec.rows].length,20);assert.equal(page.data[spec.moreBusy],false);
  const old=page[spec.more]();assert.equal(requests[2].params.offset,20);
  if(spec.component)page.data.selectedStages=['won'];else if(spec.file.includes('workbench'))page.data.opportunitySelectedStages=['won'];else page.data.selectedStages=['won'];
  const fresh=page[spec.load]();assert.deepEqual(Array.from(requests[3].params.stages),['won']);assert.equal(requests[3].params.offset,0);
  requests[3].resolve(pageResponse([{...rows[0],id:'new',status:'won',probability:100}],requests[3].params));await fresh;
  requests[2].resolve(pageResponse(rows,requests[2].params));await old;
  assert.deepEqual(Array.from(page.data[spec.rows],row=>row.id),['new']);assert.equal(page.data[spec.total],1);
 });
 test(`${spec.file}: 后续页不能越过权限变更或离页回填`,async()=>{
  for(const change of ['permission','logout','unload']){
   const {page,requests,app}=setup(spec),rows=fixtures(80),first=page[spec.load]();requests[0].resolve(pageResponse(rows,requests[0].params));await first;
   const pending=page[spec.more]();
   if(change==='permission')app.globalData.session.permissionVersion='two';
   else if(change==='logout')app.globalData.session=null;
   else if(spec.component)page.lifetimes.detached.call(page);else page.onUnload();
   const before=JSON.stringify(page.data);
   requests[1].resolve(pageResponse(rows,requests[1].params));await pending;
   assert.equal(JSON.stringify(page.data),before);
  }
 });
}

test('FDE汇总未知金额来自全集，未加载项目的未知值也不能变成0',async()=>{
 const spec=definitions.find(row=>row.component),{page,requests}=setup(spec),rows=fixtures(80);
 rows[79].amount=null;
 const pending=page.load();requests[0].resolve(pageResponse(rows,requests[0].params));await pending;
 assert.equal(page.data.items.length,20);assert.equal(page.data.count,80);assert.equal(page.data.acv,'—');
 const second=page.load();requests[1].resolve(pageResponse([{...rows[0],amount:null}],requests[1].params));await second;
 assert.equal(page.data.items[0].amountText,'—');assert.equal(page.data.acv,'—');
 const empty=page.load();requests[2].resolve(pageResponse([],requests[2].params));await empty;assert.equal(page.data.acv,'0');
});

function searchClock(){
 let serial=0;const timers=new Map();
 return {setTimeout(fn){const id=++serial;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);},flush(){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());}};
}
const salesWorkbench=definitions.find(row=>row.file==='pages/workbench/index.js');
test('销售商机搜索覆盖后续页的客户、商机、产品线，分页保留搜索及已有筛选',async()=>{
 const clock=searchClock(),{page,requests}=setup(salesWorkbench,clock),rows=fixtures(80);
 rows.slice(50).forEach((row,i)=>{row[['name','customer_name','product_line'][i%3]]='搜索目标';});
 const initial=page.loadAllOpportunities();requests[0].resolve(pageResponse(rows,requests[0].params));await initial;
 page.data.opportunitySelectedStages=['identified'];
 page.searchOpportunities({detail:{value:' 搜索目 '}});
 page.searchOpportunities({detail:{value:' 搜索目标 '}});
 assert.equal(requests.length,1);clock.flush();assert.equal(requests.length,2);
 assert.equal(requests[1].params.query,'搜索目标');assert.equal(requests[1].params.offset,0);
 assert.deepEqual(Array.from(requests[1].params.stages),['identified']);
 requests[1].resolve(pageResponse(rows,requests[1].params));await tick();
 assert.equal(page.data.opportunityTotal,30);assert.equal(page.data.filteredOpportunities.length,20);
 const more=page.loadMoreOpportunities();assert.equal(requests[2].params.query,'搜索目标');assert.equal(requests[2].params.offset,20);
 requests[2].resolve(pageResponse(rows,requests[2].params));await more;
 assert.equal(page.data.filteredOpportunities.length,30);assert.equal(page.data.opportunityFilterActive,true);
 page.searchOpportunities({detail:{value:''}});clock.flush();
 assert.equal(requests[3].params.query,'');assert.deepEqual(Array.from(requests[3].params.stages),['identified']);
 requests[3].resolve(pageResponse(rows,requests[3].params));await tick();assert.equal(page.data.opportunityTotal,80);
});

test('输入搜索词立即拒收旧分页，重置取消待发搜索且恢复全部列表',async()=>{
 const clock=searchClock(),{page,requests}=setup(salesWorkbench,clock),rows=fixtures(80);
 const initial=page.loadAllOpportunities();requests[0].resolve(pageResponse(rows,requests[0].params));await initial;
 const old=page.loadMoreOpportunities();page.searchOpportunities({detail:{value:'不存在'}});
 requests[1].resolve(pageResponse(rows,requests[1].params));await old;
 assert.equal(page.data.filteredOpportunities.length,0);assert.equal(page.data.opportunityListLoading,true);
 clock.flush();requests[2].resolve(pageResponse(rows,requests[2].params));await tick();
 assert.equal(page.data.opportunityTotal,0);assert.equal(page.data.opportunityListLoading,false);
 page.searchOpportunities({detail:{value:'待发送'}});page.resetOpportunityFilters();clock.flush();
 assert.equal(requests.length,4);assert.equal(requests[3].params.query,'');
 requests[3].resolve(pageResponse(rows,requests[3].params));await tick();
 assert.equal(page.data.opportunityFilterActive,false);assert.equal(page.data.filteredOpportunities.length,20);
});

test('销售搜索防抖不会在卸载或身份变化后发送请求',()=>{
 for(const change of ['unload','identity']){
  const clock=searchClock(),{page,requests,app}=setup(salesWorkbench,clock);
  page.searchOpportunities({detail:{value:'客户'}});
  if(change==='unload')page.onUnload();else app.globalData.session.permissionVersion='changed';
  clock.flush();assert.equal(requests.length,0);
 }
});
