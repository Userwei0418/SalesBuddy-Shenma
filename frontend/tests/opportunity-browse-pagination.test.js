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
 const app={ensureLogin:()=>true,globalData:{roles:{sales:{name:'销售',scope:'本人'}},role:spec.component?'fde':'sales',session:{workspaceId:'w',userId:'u',role:spec.component?'fde':'sales',loginAt:'first',permissionVersion:'one',capabilities:{}}}};
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

test('WEB004 阶段整组原子提交一次请求，非法值不部分生效，原生toggle兼容',async()=>{
 const {page,requests}=setup(salesWorkbench);
 const pending=page.setOpportunityStages({detail:{value:['won','identified','won','solution']}});
 assert.equal(requests.length,1);assert.deepEqual(Array.from(requests[0].params.stages),['identified','solution','won']);assert.equal(page.data.opportunityStageLabel,'已选3项');
 const before=JSON.stringify(page.data);assert.equal(page.setOpportunityStages({detail:{value:['lost','bad']}}),false);assert.equal(JSON.stringify(page.data),before);assert.equal(requests.length,1);
 requests[0].resolve(pageResponse([],requests[0].params));await pending;
 const toggled=page.toggleOpportunityStage({currentTarget:{dataset:{value:'solution'}}});assert.equal(requests.length,2);assert.deepEqual(Array.from(requests[1].params.stages),['identified','won']);requests[1].resolve(pageResponse([],requests[1].params));await toggled;
 const clear=page.setOpportunityStages({detail:{value:[]}});assert.equal(requests.length,3);assert.equal(requests[2].params.stages.length,0);assert.equal(page.data.opportunityFilterActive,false);requests[2].resolve(pageResponse([],requests[2].params));await clear;
});

test('WEB004 季度整组一次请求，统计与列表独立，指定年空季度代表全年',async()=>{
 const {page,requests}=setup(salesWorkbench);
 page.data.opportunityCloseIndex=1;
 const stats=page.setOpportunityQuarterSelection({dataset:{scope:'summary'},detail:{year:2025,quarters:[3,1,3]}});
 assert.equal(requests.length,1);assert.deepEqual(Array.from(requests[0].params.overviewQuarters),[1,3]);assert.equal(requests[0].params.overviewYear,2025);assert.equal(requests[0].params.closePeriod,'month');assert.equal(requests[0].params.quarters.length,0);
 requests[0].resolve(pageResponse([],requests[0].params));await stats;
 const list=page.setOpportunityQuarterSelection({currentTarget:{dataset:{scope:'list'}},detail:{year:2024,quarters:[2,4]}});
 assert.equal(requests.length,2);assert.equal(requests[1].params.closePeriod,'all');assert.deepEqual(Array.from(requests[1].params.quarters),[2,4]);assert.equal(requests[1].params.year,2024);assert.deepEqual(Array.from(requests[1].params.overviewQuarters),[1,3]);
 assert.equal(page.data.summaryYearOptions[page.data.listYearIndex].value,2024);assert.equal(page.data.summaryYearOptions[page.data.summaryYearIndex].value,2025);
 requests[1].resolve(pageResponse([],requests[1].params));await list;
 const full=page.setOpportunityQuarterSelection({dataset:{scope:'list'},detail:{year:2024,quarters:[]}});assert.deepEqual(Array.from(requests[2].params.quarters),[1,2,3,4]);requests[2].resolve(pageResponse([],requests[2].params));await full;
 const all=page.setOpportunityQuarterSelection({dataset:{scope:'list'},detail:{year:'all',quarters:[]}});assert.equal(requests[3].params.quarters.length,0);assert.equal(page.data.listYearIndex,0);assert.equal(page.data.summaryYearOptions[page.data.summaryYearIndex].value,2025);requests[3].resolve(pageResponse([],requests[3].params));await all;
 const before=JSON.stringify(page.data);
 for(const detail of [{year:'all',quarters:[1]},{year:2026,quarters:[5]},{year:2026,quarters:['1']},{year:1999,quarters:[]},{year:'2026',quarters:[]}])assert.equal(page.setOpportunityQuarterSelection({dataset:{scope:'list'},detail}),false);
 assert.equal(requests.length,4);assert.equal(JSON.stringify(page.data),before);
});

test('WEB004 快速整组筛选每次仅一请求，后到旧结果不覆盖新阶段或季度',async()=>{
 const {page,requests}=setup(salesWorkbench),rows=fixtures(80);
 const first=page.setOpportunityStages({detail:{value:['identified','qualified']}});
 const second=page.setOpportunityStages({detail:{value:['won','lost']}});
 const third=page.setOpportunityQuarterSelection({dataset:{scope:'summary'},detail:{year:2026,quarters:[2,3]}});
 assert.equal(requests.length,3);requests[2].resolve(pageResponse([],requests[2].params));await third;
 const accepted=JSON.stringify(page.data);requests[1].resolve(pageResponse(rows,requests[1].params));requests[0].resolve(pageResponse(rows,requests[0].params));await Promise.all([first,second]);assert.equal(JSON.stringify(page.data),accepted);
});

async function loadWorkbenchRows(count=40){
 const context=setup(salesWorkbench),{page,requests}=context,rows=fixtures(180);
 const first=page.loadData();requests[0].resolve(pageResponse(rows,requests[0].params));await first;
 while(page.data.filteredOpportunities.length<count){const more=page.loadMoreOpportunities(),request=requests.at(-1);request.resolve(pageResponse(rows,request.params));await more;}
 return {...context,rows};
}
test('WEB004 详情返回过期刷新保留已加载40条，继续翻页仍20且offset正确',async()=>{
 const {page,requests,rows}=await loadWorkbenchRows();page.onHide();page.workbenchLoadedAt=Date.now()-31000;
 const pending=page.onShow();assert.equal(requests.length,3);assert.equal(requests[2].params.pageSize,40);assert.equal(requests[2].params.offset,0);assert.equal(page.data.filteredOpportunities.length,40);
 requests[2].resolve(pageResponse(rows,requests[2].params));await pending;
 assert.equal(page.data.filteredOpportunities.length,40);assert.equal(page.data.opportunityNextOffset,40);
 const more=page.loadMoreOpportunities();assert.equal(requests[3].params.pageSize,20);assert.equal(requests[3].params.offset,40);requests[3].resolve(pageResponse(rows,requests[3].params));await more;assert.equal(page.data.filteredOpportunities.length,60);
});
test('WEB004 恢复超过100条按有限窗口重读，中间结果不覆盖完整可见范围',async()=>{
 const {page,requests,rows}=await loadWorkbenchRows(140),start=requests.length;
 const pending=page.loadAllOpportunities();assert.equal(requests[start].params.pageSize,100);requests[start].resolve(pageResponse(rows,requests[start].params));await tick();
 assert.equal(page.data.filteredOpportunities.length,140);assert.equal(requests[start+1].params.pageSize,40);assert.equal(requests[start+1].params.offset,100);assert.equal(requests[start+1].params.includeOverview,false);
 requests[start+1].resolve(pageResponse(rows,requests[start+1].params));await pending;assert.equal(page.data.filteredOpportunities.length,140);assert.equal(page.data.opportunityNextOffset,140);assert.equal(requests.length,start+2);
});
test('WEB004 恢复期间权限变化拒收旧窗口，范围改变从首20重读',async()=>{
 const {page,requests,rows,app}=await loadWorkbenchRows(140),start=requests.length;
 const old=page.loadAllOpportunities();requests[start].resolve(pageResponse(rows,requests[start].params));await tick();
 app.globalData.session.permissionVersion='changed';const fresh=page.loadData();assert.equal(requests[start+2].params.pageSize,20);assert.equal(page.data.filteredOpportunities.length,0);
 requests[start+2].resolve(pageResponse(rows,requests[start+2].params));await fresh;const accepted=JSON.stringify(page.data);
 requests[start+1].resolve(pageResponse(rows,requests[start+1].params));await old;assert.equal(JSON.stringify(page.data),accepted);
 const filter=page.setOpportunityStages({detail:{value:['lost']}});assert.equal(requests.at(-1).params.pageSize,20);assert.equal(page.data.filteredOpportunities.length,0);requests.at(-1).resolve(pageResponse(rows,requests.at(-1).params));await filter;assert.equal(page.data.opportunityTotal,0);
});
test('WEB004 恢复窗口失败或快照变化不伪装旧值为最新，重试回首20',async()=>{
 for(const failure of ['offline','total','duplicate','offset']){
  const {page,requests,rows}=await loadWorkbenchRows(140),start=requests.length;
  const pending=page.loadAllOpportunities();requests[start].resolve(pageResponse(rows,requests[start].params));await tick();const next=requests[start+1];
  if(failure==='offline')next.reject(Error('恢复失败'));
  else{const response=pageResponse(rows,next.params);if(failure==='total')response.summary.total++;if(failure==='duplicate')response.items[0]={...rows[0]};if(failure==='offset')response.next_offset=100;next.resolve(response);}
  await pending;assert.equal(page.data.opportunityDataReady,false);assert.ok(page.data.opportunityListError);assert.equal(page.data.filteredOpportunities.length,0);assert.equal(page.listOpportunities.length,0);assert.equal(page.data.opportunityNextOffset,null);
  const retry=page.loadAllOpportunities();assert.equal(requests.at(-1).params.pageSize,20);requests.at(-1).resolve(pageResponse(rows,requests.at(-1).params));await retry;assert.equal(page.data.filteredOpportunities.length,20);
 }
});
