const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const list=(items=[],has_more=false,next_offset=null)=>({items,has_more,next_offset});
const opportunity=id=>({id,name:`商机${id}`,amount:100000,status:'open',stage_code:'proposal',probability:50});
const overview=id=>({id,name:`客户${id}`,primary_opportunity:opportunity('o-last'),summary:{opportunity_count:50,contact_count:42,visit_count:100,task_count:88,task_status_counts:{completed:80,in_progress:8},open_amount:5000000},profile:{dimensions:[]}});
function harness(name,api={}){
 const filename=path.resolve(__dirname,`../miniprogram/pages/${name}/index.js`);let page,timerId=0;const timers=new Map(),toast=[];
 const app={ensureLogin:()=>true,globalData:{role:'sales',roles:{sales:{name:'销售',scope:'本人'}},session:{workspaceId:'w',userId:'u',role:'sales',permissionVersion:1,loginAt:'first'}}};
 const defaults={getCustomer(){throw Error('full customer forbidden');},getOpportunityDetail(){throw Error('full opportunity forbidden');},queryBusinessAdvice:async()=>({status:'succeeded',summary:'测试建议',suggestions:[]}),getCustomerHeader:async id=>({id,name:`客户${id}`,read_model:'detail_header_v1'}),getCustomerOverview:async id=>overview(id),listCustomerContacts:async()=>list(),listCustomerOpportunities:async()=>list(),listVisits:async()=>list(),listDetailTasks:async()=>list(),getOpportunityTimeline:async()=>list()};
 const wx={showLoading(){},hideLoading(){},showTabBar(){},hideTabBar(){},showNavigationBarLoading(){},hideNavigationBarLoading(){},showToast:x=>toast.push(x),showModal:x=>toast.push(x),navigateTo(){},setNavigationBarTitle(){}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,require:n=>n.endsWith('/apiClient')?{...defaults,...api}:require(path.resolve(path.dirname(filename),n)),wx,Date,Map,Set,setTimeout:f=>{const id=++timerId;timers.set(id,f);return id;},clearTimeout:id=>timers.delete(id)});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(v,cb)=>{Object.assign(page.data,v);if(cb)cb();};
 return {page,app,toast,runTimers(){const callbacks=[...timers.values()];timers.clear();callbacks.forEach(f=>f());}};
}
const event=(key,value)=>({currentTarget:{dataset:{[key]:value}}});

for(const name of ['customers','customer-assets']){
 test(`${name}: newly entered historical visits stay first across server cursor pages and reopening`,async()=>{
  const calls=[];
  const rows=Array.from({length:25},(_,i)=>({id:`visit-${String(i).padStart(2,'0')}`,opportunity_id:'o',
   created_at:new Date(Date.UTC(2026,8,15,23,21)-i*60000).toISOString(),recorded_on:'2026-01-01',created_date:'2026-01-01',
   interaction_at:new Date(Date.UTC(2026,8,1+i)).toISOString(),follow_up_record:`跟进摘要${i}`}));
  const h=harness(name,{getOpportunityDetailHeader:async()=>({id:'c',name:'客户c',read_model:'detail_header_v1',opportunities:[opportunity('o')]}),
   listVisits:async p=>{calls.push(p);assert.equal(p.sort,'created_desc');assert.equal(p.customer_id,'c');
    assert.equal(p.opportunity_id,name==='customer-assets'?'o':undefined);
    return p.cursor?{...list(rows.slice(20)),next_cursor:null}:{...list(rows.slice(0,20),true,20),next_cursor:'created-position-20'};}});
  const p=h.page;Object.assign(p.data,{customerId:'c',opportunityId:'o',opportunityTab:'visits'});
  const open=()=>name==='customers'?p.showCustomerDetail('c','','visits'):p.loadOpportunity();
  const visits=()=>name==='customers'?p.data.selectedCustomer.visits:p.data.relatedVisits;
  await open();await tick();
  assert.deepEqual(visits().map(v=>v.id),rows.slice(0,20).map(v=>v.id));
  assert.equal(visits()[0].recordedAt,'2026-09-16 07:21');
  assert.equal(visits()[0].date,'2026年9月1日');
  assert.equal(visits()[0].followUpRecord,'跟进摘要0');
  await p[name==='customers'?'moreDetailSection':'moreOpportunitySection'](event('section','visits'));
  assert.deepEqual(visits().map(v=>v.id),rows.map(v=>v.id));
  assert.equal(calls[1].cursor,'created-position-20');assert.equal(calls[1].offset,undefined);
  // Same-time IDs must also retain the backend order, rather than a client re-sort.
  rows.unshift({id:'new-historical',created_at:rows[0].created_at,interaction_at:'2025-01-01T00:00:00Z',follow_up_record:'新补录'});
  p.onHide();await open();await tick();
  assert.equal(calls[2].cursor,undefined);assert.equal(calls[2].offset,0);
  assert.equal(visits()[0].id,'new-historical');assert.equal(visits()[0].followUpRecord,'新补录');
 });
}

for(const name of ['customers','customer-detail']){
 test(`${name}: overview totals load first; active lists use 20-row pages and independent errors`,async()=>{
  const calls=[];let retry=false;
  const h=harness(name,{listCustomerOpportunities:async(id,p)=>{calls.push(['opportunities',id,p.offset]);return list(Array.from({length:20},(_,i)=>opportunity(`o${i}`)),true,20);},listCustomerContacts:async(id,p)=>{calls.push(['contacts',id,p.offset]);throw Error('contacts offline');},listVisits:async p=>{calls.push(['visits',p.customer_id,p.offset]);assert.equal(p.page_size,20);if(p.offset&&!retry)throw Error('next page offline');return p.offset?list([{id:'v20',follow_up_record:'第二页'}]):list(Array.from({length:20},(_,i)=>({id:`v${i}`,follow_up_record:'摘要',is_summary:true})),true,20);}});
  const p=h.page;p.data.customerId='c';if(name==='customers')await p.showCustomerDetail('c');else await p.loadCustomer();await tick();
  let detail=name==='customers'?p.data.selectedCustomer:p.data.customer;assert.equal(detail.opportunityCount,50);assert.equal(detail.contactCount,42);assert.equal(detail.opportunities.length,20);assert.equal(p.data.detailPages.contacts.error,'contacts offline');assert.equal(calls.some(c=>c[0]==='visits'),false);
  if(name==='customers')p.selectDetailTab(event('tab','visits'));else p.selectTab(event('tab','visits'));await tick();
  const more=name==='customers'?'moreDetailSection':'moreSection',again=name==='customers'?'retryDetailSection':'retrySection';await p[more](event('section','visits'));
  detail=name==='customers'?p.data.selectedCustomer:p.data.customer;assert.equal(detail.visits.length,20);assert.equal(detail.visitCount,100);assert.equal(p.data.detailPages.visits.error,'next page offline');
  retry=true;await p[again](event('section','visits'));detail=name==='customers'?p.data.selectedCustomer:p.data.customer;assert.equal(detail.visits.length,21);assert.equal(p.data.detailPages.visits.error,'');assert.deepEqual(calls.filter(c=>c[0]==='visits').map(c=>c[2]),[0,20,20]);
 });
 test(`${name}: focused customer opportunity outside first page uses customer panorama permission`,async()=>{
  const calls=[];const h=harness(name,{getOpportunityDetailOverview(){throw Error('personal opportunity scope forbidden');},getCustomerOpportunityHeader:async(c,o)=>{calls.push([c,o]);return {id:c,opportunities:[opportunity(o)]};},listCustomerOpportunities:async()=>list(Array.from({length:20},(_,i)=>opportunity(`o${i}`)),true,20)});
  if(name==='customers')await h.page.showCustomerDetail('c','o-last','opportunity');else {Object.assign(h.page.data,{customerId:'c',opportunityId:'o-last',activeTab:'opportunity'});await h.page.loadCustomer();}await tick();
  const detail=name==='customers'?h.page.data.selectedCustomer:h.page.data.customer;assert.deepEqual(calls,[['c','o-last']]);assert.equal(detail.opportunity.id,'o-last');assert.equal(detail.opportunities[0].isFocused,true);assert.equal(detail.opportunityCount,'—');
 });
 test(`${name}: late overview and history failures cannot resurrect a closed detail`,async()=>{
  const old=deferred(),history=deferred();const h=harness(name,{getCustomerHeader:()=>old.promise,listCustomerOpportunities:()=>history.promise});
  h.page.data.customerId='c';const pending=name==='customers'?h.page.showCustomerDetail('c'):h.page.loadCustomer();h.page.onHide();old.resolve(overview('c'));await pending;assert.equal(name==='customers'?h.page.data.selectedCustomer:h.page.data.customer,null);assert.equal(h.toast.length,0);
  const k=harness(name,{listCustomerOpportunities:()=>history.promise});k.page.data.customerId='c';if(name==='customers')await k.page.showCustomerDetail('c');else await k.page.loadCustomer();k.page.onHide();history.reject(Error('late'));await tick();assert.equal(k.toast.length,0);
 });
}
test('map compact popup closes when full detail opens; old popup response cannot replace it',async()=>{
 const wait=deferred();const h=harness('customers',{getCustomerOverview:id=>id==='old'?wait.promise:Promise.resolve(overview(id))});const old=h.page.showBattleCustomer('old');await h.page.showCustomerDetail('new');wait.resolve(overview('old'));await old;assert.equal(h.page.data.selectedBattleCustomer,null);assert.equal(h.page.data.selectedCustomer.id,'new');
});
test('inline visit expansion fetches full body, retry stays local, collapsed late request cannot show a wrong record',async()=>{
 let fail=true;const second=deferred(),calls=[];const h=harness('customer-detail',{listVisits:async()=>list([{id:'v1',is_summary:true,follow_up_record:'短摘要'},{id:'v2',is_summary:true,follow_up_record:'另一摘要'}]),getVisit:async id=>{calls.push(id);if(id==='v2')return second.promise;if(fail)throw Error('detail offline');return {id,customer_id:'c',follow_up_record:'完整正文'.repeat(250),customer_main_business:'软件',is_first_visit:true};}});
 Object.assign(h.page.data,{customerId:'c',activeTab:'visits'});await h.page.loadCustomer();await tick();await h.page.toggleVisit(event('id','v1'));assert.equal(h.page.data.visitDetailState.error,'detail offline');fail=false;await h.page.retryVisitDetail();assert.equal(h.page.data.customer.visits.find(v=>v.id==='v1').followUpRecord.length,1000);
 const p=h.page.toggleVisit(event('id','v2'));await h.page.toggleVisit(event('id','v1'));assert.equal(h.page.data.visitDetailState.loading,false);second.reject(Error('late v2'));await p;assert.equal(h.page.data.expandedVisitId,'v1');assert.equal(h.page.data.visitDetailState.error,'');assert.equal(calls.filter(id=>id==='v1').length,2);
});
test('opportunity overview stays responsive while each history tab loads and totals remain global',async()=>{
 const calls=[],tasks=deferred();const h=harness('customer-assets',{getOpportunityDetailHeader:async()=>({id:'c',name:'客户c',read_model:'detail_header_v1',opportunities:[opportunity('o')]}),getOpportunityDetailOverview:async()=>({...overview('c'),opportunities:[opportunity('o')]}),listDetailTasks:p=>{calls.push(p);return tasks.promise;}});
 Object.assign(h.page.data,{customerId:'c',opportunityId:'o'});await h.page.loadOpportunity();await tick();assert.equal(h.page.data.opportunity.name,'商机o');assert.equal(h.page.data.taskStats.total,88);assert.equal(calls.length,0);h.page.selectOpportunityTab(event('tab','tasks'));assert.equal(h.page.data.detailPages.tasks.loading,true);
 h.app.globalData.session.permissionVersion=2;tasks.resolve(list([{id:'secret'}]));await tick();assert.equal(h.page.data.relatedTasks.length,0);
});
test('actual form can search and page all customer opportunities and preselect a target beyond page 20',async()=>{
 const target=deferred(),calls=[];const h=harness('customer-assets',{getCustomerOpportunityOverview:(c,o)=>{calls.push(['target',c,o]);return target.promise;},listCustomerOpportunities:async(c,p)=>{calls.push(['list',c,p]);return list([opportunity(p.q?'found':p.offset?'next':'first')],!p.offset,p.offset?null:20);}});
 Object.assign(h.page.data,{canManage:true,customerId:'c',opportunityId:'outside'});const pending=h.page.openForm();await tick();h.page.searchFormOpportunity({detail:{value:'找到'}});h.runTimers();await tick();target.resolve({id:'c',opportunities:[opportunity('outside')]});await pending;
 assert.equal(h.page.data.formOpportunity.id,'outside');assert.equal(h.page.data.formTargetLoading,false);assert.equal(h.page.data.formChoices[0].id,'found');await h.page.moreFormChoices();assert.equal(calls.at(-1)[2].offset,20);assert.equal(calls.at(-1)[2].q,'找到');h.page.closeForm();
});
test('actual form late preselection never overwrites a manual choice; failed original requires explicit selection',async()=>{
 const d=deferred();const h=harness('customer-assets',{getCustomerOpportunityOverview:()=>d.promise,listCustomerOpportunities:async()=>list([opportunity('manual')])});Object.assign(h.page.data,{canManage:true,customerId:'c',opportunityId:'old'});const p=h.page.openForm();await tick();h.page.selectFormOpportunity(event('id','manual'));d.resolve({id:'c',opportunities:[opportunity('old')]});await p;assert.equal(h.page.data.formOpportunity.id,'manual');assert.equal(h.page.data.formTargetLoading,false);
 const broken=harness('customer-assets',{getCustomerOpportunityOverview:async()=>{throw Error('not visible');}});Object.assign(broken.page.data,{canManage:true,customerId:'c',opportunityId:'old'});await broken.page.openForm();assert.equal(broken.page.data.formSelectionRequired,true);broken.page.selectFormOpportunity(event('id',''));assert.equal(broken.page.data.formSelectionRequired,false);
});

for(const returningFrom of ['background','supplement']){
 test(`inline visit returning from ${returningFrom} clears expanded summary and reloads complete fields only on demand`,async()=>{
  let generation=1;const calls=[];
  const h=harness('customer-detail',{
   listVisits:async()=>list([{id:'v1',is_summary:true,follow_up_record:'当前摘要'}]),
   getVisit:async id=>{calls.push(id);return {id,customer_id:'c',follow_up_record:`完整正文${generation}`,visit_goal:`拜访目标${generation}`};},
  });
  Object.assign(h.page.data,{customerId:'c',activeTab:'visits'});await h.page.loadCustomer();await tick();
  await h.page.toggleVisit(event('id','v1'));
  assert.equal(h.page.data.visitDetailState.ready,true);
  assert.equal(h.page.data.customer.visits[0].isSummary,false);
  if(returningFrom==='supplement')h.page.supplementVisit(event('id','v1'));
  h.page.onHide();generation=2;h.page.onShow();await tick();
  assert.equal(h.page.data.expandedVisitId,'');
  assert.equal(h.page.data.visitDetailState.ready,false);
  assert.equal(h.page.data.customer.visits[0].isSummary,true);
  assert.deepEqual(calls,['v1']);
  await h.page.toggleVisit(event('id','v1'));
  assert.deepEqual(calls,['v1','v1']);
  assert.equal(h.page.data.visitDetailState.ready,true);
  assert.equal(h.page.data.customer.visits[0].isSummary,false);
  assert.equal(h.page.data.customer.visits[0].visitGoal,'拜访目标2');
 });
}
test('inline visit template requires both the full record and ready state before displaying complete fields',()=>{
 const template=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customer-detail/index.wxml'),'utf8');
 assert.match(template,/<block wx:elif="{{!item\.isSummary && visitDetailState\.ready}}"><view><text>客户名称<\/text>/);
 assert.match(template,/<view wx:else catchtap="retryVisitDetail">点击读取完整记录<\/view>/);
});

for(const name of ['customers','customer-detail']){
 test(`${name}: header and visible history work before the requested complete summary`,async()=>{
  const summary=deferred();let summaryCalls=0,visits=0,advice=0;
  const h=harness(name,{getCustomerOverview:()=>{summaryCalls++;return summary.promise;},listVisits:async()=>{visits++;return list([{id:'v',is_summary:true,follow_up_record:'已加载'}]);},queryBusinessAdvice:async()=>{advice++;return {status:'succeeded',summary:'建议',suggestions:[]};}});
  h.page.data.customerId='c';h.page.data.activeTab='visits';
  if(name==='customers')await h.page.showCustomerDetail('c','','visits');else await h.page.loadCustomer();await tick();
  const detail=()=>name==='customers'?h.page.data.selectedCustomer:h.page.data.customer;
  const tab=value=>name==='customers'?h.page.selectDetailTab(event('tab',value)):h.page.selectTab(event('tab',value));
  assert.equal(summaryCalls,0);assert.equal(visits,1);assert.equal(detail().visits[0].followUpRecord,'已加载');assert.equal(detail().visitCount,'—');assert.equal(detail().signal.tone,'gray');
  tab('overview');await tick();assert.equal(summaryCalls,1);assert.equal(h.page.data.detailSummary.loading,true);assert.equal(detail().name,'客户c');
  tab('visits');tab('overview');await tick();const adviceBefore=advice;assert.equal(summaryCalls,1);assert.equal(visits,1);
  summary.resolve(overview('c'));await tick();assert.equal(detail().visitCount,100);assert.equal(h.page.data.detailSummary.loaded,true);assert.equal(visits,1);assert.equal(advice,adviceBefore);
  tab('visits');tab('overview');await tick();assert.equal(summaryCalls,1);
 });
 test(`${name}: summary failure retries locally without discarding lists or restarting advice`,async()=>{
  let attempts=0,listCalls=0,advice=0;
  const h=harness(name,{getCustomerOverview:async()=>{if(++attempts===1)throw Error('summary offline');return overview('c');},listCustomerOpportunities:async()=>{listCalls++;return list([opportunity('o')]);},queryBusinessAdvice:async()=>{advice++;return {status:'succeeded',summary:'建议',suggestions:[]};}});
  h.page.data.customerId='c';if(name==='customers')await h.page.showCustomerDetail('c');else await h.page.loadCustomer();await tick();
  const detail=()=>name==='customers'?h.page.data.selectedCustomer:h.page.data.customer;
  assert.equal(detail().opportunities.length,1);assert.equal(detail().opportunityCount,'—');assert.equal(detail().signal.tone,'gray');assert.equal(h.page.data.detailSummary.error,'summary offline');
  const adviceBefore=advice;await h.page.retryCustomerSummary();assert.equal(attempts,2);assert.equal(listCalls,1);assert.equal(advice,adviceBefore);assert.equal(detail().opportunityCount,50);
 });
}
test('opportunity header opens history without summary; summary merge preserves edited FDE selection',async()=>{
 const summary=deferred();let calls=0;
 const h=harness('customer-assets',{getOpportunityDetailHeader:async()=>({id:'c',name:'客户c',read_model:'detail_header_v1',opportunities:[opportunity('o')]}),getOpportunityDetailOverview:()=>{calls++;return summary.promise;},listVisits:async()=>list([{id:'v',opportunity_id:'o',is_summary:true,follow_up_record:'本页拜访'}])});
 Object.assign(h.page.data,{customerId:'c',opportunityId:'o',opportunityTab:'visits'});await h.page.loadOpportunity();await tick();
 assert.equal(calls,0);assert.equal(h.page.data.relatedVisits.length,1);assert.equal(h.page.data.relatedVisitCount,'—');assert.equal(h.page.data.taskStats.total,'—');assert.equal(h.page.data.opportunity.signal.tone,'gray');
 h.page.selectOpportunityTab(event('tab','overview'));assert.equal(calls,1);h.page.fdeMembersChanged({detail:{members:[{id:'manual',name:'人工选择'}]}});
 summary.resolve({...overview('c'),opportunities:[opportunity('o')]});await tick();assert.equal(h.page.data.taskStats.total,88);assert.equal(h.page.data.fdeMembers[0].id,'manual');assert.equal(h.page.data.fdeRelationDirty,true);
 h.page.selectOpportunityTab(event('tab','visits'));h.page.selectOpportunityTab(event('tab','overview'));await tick();assert.equal(calls,1);
});

for(const name of ['customers','customer-detail']){
 test(`${name}: customer summary cannot imply an off-page focused opportunity has no risks`,async()=>{
  const h=harness(name,{getCustomerOverview:async id=>({...overview(id),read_model:'detail_overview_v1'}),getCustomerOpportunityHeader:async(c,o)=>({id:c,name:'客户',read_model:'detail_header_v1',opportunities:[opportunity(o)]}),listCustomerOpportunities:async()=>list([opportunity('first')],true,20)});
  if(name==='customers')await h.page.showCustomerDetail('c','target','opportunity');else {Object.assign(h.page.data,{customerId:'c',opportunityId:'target',activeTab:'opportunity'});await h.page.loadCustomer();}
  await h.page.loadCustomerSummary();await tick();
  const detail=name==='customers'?h.page.data.selectedCustomer:h.page.data.customer;
  assert.equal(detail.opportunityCount,50);assert.equal(detail.opportunities.find(item=>item.id==='target').signal.tone,'gray');
 });
}
