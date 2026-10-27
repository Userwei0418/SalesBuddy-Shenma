require('./helpers/business-options');
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const pageResponse = require('./helpers/opportunity-pages');
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const specs = [
  {name:'workbench',file:'pages/workbench/index.js',start:'loadData',reload:'loadAllOpportunities',
    more:'loadMoreOpportunities',overview:'applyOpportunitySummary',rows:'filteredOpportunities',
    total:'opportunityTotal',cursor:'opportunityNextOffset',loading:'opportunityListLoading',
    overviewReady:'opportunityDataReady',overviewError:'opportunityDataError',board:'opportunityBoard',
    moreError:'opportunityMoreError',query:'opportunityQuery'},
  {name:'FDE',file:'components/fde-projects/index.js',component:true,start:'load',reload:'load',
    more:'loadMore',overview:'loadOverview',rows:'filtered',total:'count',cursor:'nextOffset',
    loading:'loading',overviewReady:'overviewReady',overviewError:'overviewError',board:'overview',
    moreError:'moreError',query:'query'},
];
function rows(count=80,prefix='row') {
  return Array.from({length:count},(_,i)=>({id:`${prefix}-${i}`,name:`项目${i}`,customer_name:'客户',
    amount:100,status:'open',stage_code:'identified',probability:10,expected_close_date:'2026-10-01',
    created_at:'2026-01-01',actuals:{recognized_amount:null,collection_amount:null}}));
}
function setup(spec) {
  const requests=[],directoryRequests=[],filename=path.resolve(__dirname,'../miniprogram',spec.file);
  const app={ensureLogin:()=>true,globalData:{role:spec.component?'fde':'sales',
    roles:{sales:{name:'销售',scope:'本人'}},
    session:{workspaceId:'w',userId:'u',role:spec.component?'fde':'sales',loginAt:'first',
      permissionVersion:'one',capabilities:{}}}};
  const api={listOpportunities:params=>{
    let resolve,reject;
    const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
    requests.push({params:plain(params),resolve,reject});
    return promise;
  },getDirectoryMembers:params=>{
    let resolve,reject;
    const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
    directoryRequests.push({params:plain(params),resolve,reject});
    return promise;
  }};
  let definition;
  vm.runInNewContext(fs.readFileSync(filename,'utf8'),{
    Page:value=>{definition=value;},Component:value=>{definition=value;},
    require:name=>name.endsWith('apiClient')?api:require(path.resolve(path.dirname(filename),name)),
    getApp:()=>app,Date,Set,Map,setTimeout,clearTimeout,wx:{},
  });
  const page={...definition,...definition.methods,data:plain(definition.data),
    properties:{customerId:'',memberId:'',initialScope:''},
    setData(values,callback){Object.assign(this.data,values);if(callback)callback();}};
  if(spec.component)page.setData({year:2026});
  return {spec,page,requests,directoryRequests,app,
    hide:()=>spec.component?page.pageLifetimes.hide.call(page):page.onHide(),
    show:()=>spec.component?page.pageLifetimes.show.call(page):page.onShow(),
    autoMore:()=>spec.component?page.prefetchMore():page.onReachBottom()};
}
function listState(h) {
  const {page,spec}=h;
  return plain({ids:page.data[spec.rows].map(row=>row.id),total:page.data[spec.total],
    cursor:page.data[spec.cursor],teams:page.data.executionTeamOptions,
    owners:page.data.opportunityOwnerOptions,products:page.data.productOptions});
}
async function firstPage(h,data=rows()) {
  const pending=h.page[h.spec.start]();
  const first=h.requests.at(-1);
  assert.equal(first.params.includeOverview,false);
  assert.equal(first.params.pageSize,20);
  first.resolve(pageResponse(data,first.params));
  await pending;
  return h.requests.at(-1);
}
async function finishOverview(h,request,data=rows()) {
  request.resolve(pageResponse(data,request.params));
  await tick();
}

for(const spec of specs) {
  test(`${spec.name}: 首批先显示，统计使用同筛选且不覆盖列表分页`,async()=>{
    const h=setup(spec),overview=await firstPage(h);
    assert.equal(h.requests.length,2);
    assert.equal(h.page.data[spec.loading],false);
    assert.equal(h.page.data[spec.rows].length,20);
    assert.equal(h.page.data[spec.overviewReady],false);
    assert.equal(overview.params.includeOverview,true);
    assert.equal(overview.params.pageSize,1);
    assert.equal(overview.params.offset,0);
    for(const [key,value] of Object.entries(h.requests[0].params)) {
      if(!['includeOverview','pageSize','offset'].includes(key))assert.deepEqual(overview.params[key],value);
    }
    const before=listState(h);
    // Independent reads can observe a changed total; validate the statistics
    // response against its own summary, never replace the list's cursor/facets.
    await finishOverview(h,overview,rows(81));
    assert.equal(h.page.data[spec.overviewReady],true);
    assert.equal(h.page.data[spec.board].total,81);
    assert.deepEqual(listState(h),before);
  });

  test(`${spec.name}: 统计单独重试，网络刷新保留列表但授权与契约失败清空`,async()=>{
    const h=setup(spec),overview=await firstPage(h),before=listState(h);
    overview.reject(Object.assign(Error('统计网络超时'),{code:'NETWORK_ERROR'}));await tick();
    assert.ok(h.page.data[spec.overviewError]);
    assert.deepEqual(listState(h),before);
    const retry=h.page[spec.overview]();
    h.page[spec.overview]();
    assert.equal(h.requests.length,3,'进行中的同一统计重试只发一次');
    await finishOverview(h,h.requests[2]);await retry;
    assert.deepEqual(listState(h),before);
    assert.equal(h.page.data[spec.overviewError],'');
    const refresh=h.page[spec.reload]();
    assert.deepEqual(listState(h),before);
    h.requests.at(-1).reject(Object.assign(Error('临时离线'),{code:'NETWORK_ERROR'}));await refresh;
    assert.deepEqual(listState(h),before);
    const denied=h.page[spec.reload]();
    h.requests.at(-1).reject(Object.assign(Error('权限已收回'),{statusCode:403}));await denied;
    assert.equal(h.page.data[spec.rows].length,0);
    assert.equal(h.page.data[spec.total],0);
    assert.equal(h.page.data[spec.cursor],null);
    const deniedOverview=await firstPage(h);
    deniedOverview.reject(Object.assign(Error('登录已失效'),{statusCode:401,code:'HTTP_401'}));
    await tick();
    assert.equal(h.page.data[spec.rows].length,0,'统计鉴权失败同样不能保留旧列表');
    assert.equal(h.page.data[spec.cursor],null);
    const recovery=h.page[spec.overview](),recoveryRequest=h.requests.at(-1);
    assert.equal(recoveryRequest.params.includeOverview,false,'统计鉴权清屏后的重试先恢复列表');
    assert.equal(recoveryRequest.params.pageSize,20);
    recoveryRequest.resolve(pageResponse(rows(),recoveryRequest.params));await recovery;
    assert.equal(h.page.data[spec.rows].length,20);
    await finishOverview(h,h.requests.at(-1));
    const malformed=h.page[spec.reload]();
    h.requests.at(-1).resolve({items:[],has_more:false});
    await malformed;
    assert.equal(h.page.data[spec.rows].length,0,'无效分页契约不能冒充临时网络失败保留旧值');
    assert.equal(h.page.data[spec.cursor],null);
    const pageRecovery=h.page[spec.more](),pageRecoveryRequest=h.requests.at(-1);
    assert.equal(pageRecoveryRequest.params.offset,0,'清屏后的分页重试从第一页恢复');
    pageRecoveryRequest.resolve(pageResponse(rows(),pageRecoveryRequest.params));await pageRecovery;
    assert.equal(h.page.data[spec.rows].length,20);
    await finishOverview(h,h.requests.at(-1));
  });

  test(`${spec.name}: 预加载互斥，失败停自动重试，手动重试同游标并去重`,async()=>{
    const h=setup(spec),overview=await firstPage(h);
    await finishOverview(h,overview);
    const pending=h.page[spec.more]();
    h.autoMore();h.page[spec.more]();
    assert.equal(h.requests.length,3);
    assert.equal(h.requests[2].params.offset,20);
    h.requests[2].reject(Object.assign(Error('下一页超时'),{code:'NETWORK_ERROR'}));await pending;
    assert.equal(h.page.data[spec.rows].length,20);
    assert.equal(h.page.data[spec.cursor],20);
    h.autoMore();h.autoMore();
    assert.equal(h.requests.length,3,'持续滚动不自动重试失败的下一页');
    const retry=h.page[spec.more](),request=h.requests.at(-1);
    assert.equal(request.params.offset,20);
    const response=pageResponse(rows(),request.params);
    response.items[0]={...rows()[0]};
    request.resolve(response);await retry;
    assert.equal(h.page.data[spec.rows].length,39);
    assert.equal(new Set(h.page.data[spec.rows].map(row=>row.id)).size,39);
    assert.equal(h.page.data[spec.cursor],40);
    assert.equal(h.page.data[spec.total],80);
    assert.equal(h.requests.filter(r=>r.params.includeOverview).length,1,'翻页不重复取统计');
  });

  test(`${spec.name}: 离页保留已看窗口且丢弃旧响应，筛选和身份换代清空`,async()=>{
    const h=setup(spec),overview=await firstPage(h);
    await finishOverview(h,overview);
    const second=h.page[spec.more](),secondRequest=h.requests.at(-1);
    secondRequest.resolve(pageResponse(rows(),secondRequest.params));await second;
    const pendingMore=h.page[spec.more](),oldMore=h.requests.at(-1);
    const pendingStats=h.page[spec.overview](),oldStats=h.requests.at(-1);
    h.hide();
    const hiddenState=plain(h.page.data);
    oldMore.resolve(pageResponse(rows(),oldMore.params));
    oldStats.resolve(pageResponse(rows(81),oldStats.params));
    await pendingMore;await pendingStats;
    assert.deepEqual(plain(h.page.data),hiddenState);
    assert.equal(h.page.data[spec.rows].length,40);
    const requestCount=h.requests.length,resumed=h.show();
    assert.equal(h.page.data[spec.rows].length,40,'返回页面不退回首20条');
    const resumeStats=h.requests.at(-1);
    if(h.requests.length>requestCount) {
      assert.equal(resumeStats.params.includeOverview,true,'返回只补未完成统计');
      await finishOverview(h,resumeStats);
    }
    await resumed;

    const staleStats=h.page[spec.overview](),staleRequest=h.requests.at(-1);
    h.page.setData({[spec.query]:'不存在'});
    const filtered=h.page[spec.reload](),filterRequest=h.requests.at(-1);
    assert.equal(h.page.data[spec.rows].length,0);
    const filterState=plain(h.page.data);
    staleRequest.resolve(pageResponse(rows(),staleRequest.params));await staleStats;
    assert.deepEqual(plain(h.page.data),filterState);
    h.app.globalData.session={...h.app.globalData.session,userId:'different-user'};
    const fresh=h.page[spec.start](),freshRequest=h.requests.at(-1);
    assert.equal(h.page.data[spec.rows].length,0);
    const identityState=plain(h.page.data);
    filterRequest.resolve(pageResponse(rows(),filterRequest.params));await filtered;
    assert.deepEqual(plain(h.page.data),identityState);
    const freshRows=rows(30,'new-user').map(row=>({...row,name:`不存在匹配${row.name}`}));
    freshRequest.resolve(pageResponse(freshRows,freshRequest.params));await fresh;
    assert.equal(h.page.data[spec.rows].length,20);
    assert.ok(h.page.data[spec.rows].every(row=>row.id.startsWith('new-user-')));
    await finishOverview(h,h.requests.at(-1),freshRows);
  });

  test(`${spec.name}: TTL到期返回保留已看窗口，分块补齐后才更新游标`,async()=>{
    const h=setup(spec),data=rows(180),overview=await firstPage(h,data);
    await finishOverview(h,overview,data);
    while(h.page.data[spec.rows].length<140) {
      const pending=h.page[spec.more](),request=h.requests.at(-1);
      request.resolve(pageResponse(data,request.params));await pending;
    }
    h.hide();
    h.page[spec.component?'loadedAt':'workbenchLoadedAt']=Date.now()-31000;
    const beforeCount=h.requests.length,resumed=h.show(),first=h.requests.at(-1);
    assert.equal(h.page.data[spec.rows].length,140);
    assert.equal(h.page.data[spec.cursor],140);
    assert.equal(first.params.includeOverview,false);
    assert.equal(first.params.pageSize,100);
    const updated=data.map(row=>({...row,name:`后台已刷新${row.name}`}));
    first.resolve(pageResponse(updated,first.params));await tick();
    const last=h.requests.at(-1);
    assert.equal(last.params.offset,100);
    assert.equal(last.params.pageSize,40);
    assert.equal(h.page.data[spec.rows].length,140,'等待后段时保留完整可见窗口');
    assert.equal(h.page.data[spec.cursor],140,'中间小页不能接管列表游标');
    assert.match(h.page.data[spec.rows].find(row=>row.id==='row-0').name,/后台已刷新/);
    assert.doesNotMatch(h.page.data[spec.rows].find(row=>row.id==='row-139').name,/后台已刷新/);
    last.resolve(pageResponse(updated,last.params));await resumed;
    assert.equal(h.page.data[spec.rows].length,140);
    assert.equal(h.page.data[spec.cursor],140);
    assert.equal(h.page.data[spec.total],180);
    assert.ok(h.page.data[spec.rows].every(row=>row.name.startsWith('后台已刷新')));
    assert.equal(h.requests.length,beforeCount+3,'有界窗口两页加一次统计');
    await finishOverview(h,h.requests.at(-1),updated);
    const manual=h.page[spec.reload](),manualRequest=h.requests.at(-1);
    assert.equal(manualRequest.params.pageSize,20,'显式刷新从首20条开始');
    assert.equal(manualRequest.params.offset,0);
    manualRequest.resolve(pageResponse(updated,manualRequest.params));await manual;
    assert.equal(h.page.data[spec.rows].length,20);
    await finishOverview(h,h.requests.at(-1),updated);
  });
}

test('FDE: fresh返回恢复被hide中断的成员目录，晚到旧目录不覆盖新值',async()=>{
  const h=setup(specs[1]);
  h.app.globalData.session.capabilities={'team.view':true};
  const overview=await firstPage(h);
  await finishOverview(h,overview);
  assert.equal(h.directoryRequests.length,1);
  assert.equal(h.page.data.membersLoading,true);
  h.hide();
  assert.equal(h.page.membersInterrupted,true);
  const listCount=h.requests.length;
  h.show();
  assert.equal(h.requests.length,listCount,'fresh返回不重读已有列表');
  assert.equal(h.directoryRequests.length,2);
  assert.equal(h.page.data.membersLoading,true);
  const directory=name=>({items:[{id:'u',name,team_ids:['team']}],teams:[{id:'team',name:'团队'}],defaults:{team_id:'team'}});
  h.directoryRequests[1].resolve(directory('新成员名'));await tick();
  h.directoryRequests[0].resolve(directory('旧成员名'));await tick();
  assert.equal(h.page.data.membersLoading,false);
  assert.equal(h.page.membersInterrupted,false);
  assert.equal(h.page.data.members.find(member=>member.id==='u').name,'新成员名');
  assert.equal(h.page.data.filtered.length,20);
});
