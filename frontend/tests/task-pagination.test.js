const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const packet=(ids,more=false,next=null)=>({items:ids.map(id=>({id,title:id,status:'pending_execution'})),summary:{total:75,pending_count:55,completed_count:20,ended_count:20,stopped_count:0,unknown_outcome_count:0,filtered_total:55},has_more:more,next_offset:next});
function harness(handler){let page;const filename=path.resolve(__dirname,'../miniprogram/pages/tasks/index.js');
 const app={ensureLogin:()=>true,globalData:{session:{role:'sales',userId:'u',workspaceId:'w',permissionVersion:1}}};
 vm.runInNewContext(fs.readFileSync(filename,'utf8'),{Page:p=>page=p,getApp:()=>app,require:n=>n.endsWith('apiClient')?{listTaskPage:handler,listTasks:()=>{throw Error('bulk forbidden');}}:require(path.resolve(path.dirname(filename),n)),wx:{setNavigationBarTitle(){},navigateTo(){},switchTab(){},setStorageSync(){}}});
 page.data=JSON.parse(JSON.stringify(page.data));page.setData=(u,cb)=>{Object.assign(page.data,u);if(cb)cb();};page.onLoad();return{page,app};}
const event=(key,value)=>({currentTarget:{dataset:{[key]:value}}});
test('initial request is exactly one 20-row page; scroll appends and totals remain database totals',async()=>{
 const calls=[];const{page}=harness(async p=>{calls.push(p);return packet(p.offset?['later']:Array.from({length:20},(_,i)=>`t${i}`),!p.offset,p.offset?null:20);});
 await page.onShow();assert.equal(calls.length,1);assert.equal(calls[0].page_size,20);assert.equal(page.data.filteredTasks.length,20);assert.equal(page.data.totalCount,75);assert.equal(page.data.filteredTotal,55);
 await page.onReachBottom();assert.equal(calls[1].offset,20);assert.equal(page.data.filteredTasks.length,21);assert.equal(page.data.pendingCount,55);
});
test('more-page failure keeps previous cards and retries same offset',async()=>{
 let fail=true;const calls=[];const{page}=harness(async p=>{calls.push(p.offset);if(p.offset&&fail)throw Error('network');return packet([p.offset?'next':'first'],!p.offset,p.offset?null:20);});
 await page.onShow();await page.loadMore();assert.equal(page.data.tasks[0].id,'first');assert.equal(page.data.moreError,'network');fail=false;await page.loadMore();assert.deepEqual(calls,[0,20,20]);assert.equal(page.data.moreError,'');
});
test('filter change resets offset and drops old success/error; server receives all drilldown filters',async()=>{
 const slow=deferred(),calls=[];const{page,app}=harness(p=>{calls.push(p);return calls.length===1?slow.promise:Promise.resolve(packet(['filtered']));});
 app.globalData.session={role:'fde_lead',userId:'lead',workspaceId:'w',permissionVersion:1,capabilities:{'team.view':true}};
 page.onLoad({scope:'team',member_id:'owner',year:'2026',quarters:'1,3',team:'华东',member:'销售甲',opportunity:'1'});
 const old=page.onShow();await page.selectTab(event('key','completed'));slow.resolve(packet(['stale']));await old;
 assert.equal(page.data.tasks[0].id,'filtered');assert.equal(calls[1].offset,0);assert.equal(calls[1].tab,'completed');assert.equal(calls[1].member_id,'owner');assert.equal(calls[1].team,'华东');assert.equal(calls[1].completed_year,2026);assert.equal(calls[1].opportunity_only,true);
 await page.changeSort({detail:{value:2}});assert.equal(calls[2].order,'due_asc');assert.equal(calls[2].offset,0);
});
for(const transition of ['hide','permission'])test(`${transition} invalidates pending response`,async()=>{
 const slow=deferred();const{page,app}=harness(()=>slow.promise);const pending=page.onShow();if(transition==='hide')page.onHide();else app.globalData.session.permissionVersion=2;slow.resolve(packet(['secret']));await pending;assert.equal(page.data.tasks.length,0);
});
test('FDE self and team selectors follow capability flags',async()=>{
 const calls=[];const{page,app}=harness(async p=>{calls.push(p);return packet([]);});app.globalData.session={role:'fde',userId:'fde',capabilities:{'team.view':false}};page.onLoad({scope:'team'});await page.onShow();assert.equal(calls[0].view,'self');await page.changeTaskView(event('view','team'));assert.equal(calls.length,1);
 app.globalData.session={role:'fde_lead',userId:'lead',capabilities:{'team.view':true}};await page.onShow();await page.changeTaskView(event('view','team'));assert.equal(calls.at(-1).view,'team');
});

test('all status tabs use supported filters while scope counts stay distinct from filtered list totals',async()=>{
 const calls=[],counts={total:100,pending_count:70,completed_count:20,ended_count:30,stopped_count:5,unknown_outcome_count:2,rejected_count:3,overdue_count:12,in_progress_count:40,deferred_count:15,needs_adjustment_count:15};
 const choices=[['all','all',undefined,100],['pending','pending',undefined,70],['in_progress','all','in_progress',40],['completed','completed',undefined,20],['deferred','all','deferred',15],['needs_adjustment','all','needs_adjustment',15],['ended','ended',undefined,30]];
 const{page}=harness(async query=>{
  calls.push(query);const expected=choices.find(([key])=>key===page.data.activeTab);
  return {items:[{id:page.data.activeTab,status:query.status||(query.tab==='completed'?'completed':query.tab==='ended'?'cancelled':'in_progress'),execution_mode:'execution_v3'}],summary:{...counts,filtered_total:expected[3]},has_more:false,next_offset:null};
 });
 await page.onShow();
 for(const [key,tab,status,total] of choices){
  if(key!=='all')await page.selectTab(event('key',key));
  assert.equal(calls.at(-1).tab,tab);assert.equal(calls.at(-1).status,status);
  assert.equal(page.data.filteredTotal,total);assert.equal(page.data.totalCount,100);
  assert.equal(page.data.pendingCount,70);assert.equal(page.data.completedCount,20);assert.equal(page.data.overdueCount,12);assert.equal(page.data.endedCount,30);
  assert.deepEqual(Array.from(page.data.tabs,row=>[row.key,row.count]),choices.map(([name,,,count])=>[name,count]));
 }
 assert.equal(calls.length,7,'one bounded read per chosen status, no second summary request');
 assert.equal(page.data.tasks[0].sourceStatus,'cancelled');
 page.stopTaskListSync(true);assert.ok(page.data.tabs.every(tab=>tab.count===null));assert.equal(page.data.pendingCount,0);
});

test('leaving a home overview clears only its subset and retains member and completed-quarter scope',async()=>{
 const calls=[];const{page,app}=harness(async query=>{calls.push(query);return packet([]);});
 app.globalData.session={role:'fde_lead',userId:'lead',workspaceId:'w',capabilities:{'team.view':true}};
 page.onLoad({overview:'today_completed',scope:'team',member_id:'owner',year:'2026',quarters:'1,3',team:'华东',opportunity:'1'});
 await page.onShow();assert.equal(page.data.activeTab,'completed');assert.equal(calls[0].overview,'today_completed');
 await page.selectTab(event('key','pending'));
 assert.equal(calls[1].tab,'pending');assert.equal(calls[1].status,undefined);assert.equal(calls[1].overview,undefined);
 assert.equal(calls[1].member_id,'owner');assert.equal(calls[1].team,'华东');assert.equal(calls[1].completed_year,2026);
 assert.deepEqual(Array.from(calls[1].completed_quarters),[1,3]);assert.equal(calls[1].opportunity_only,true);
 assert.equal(page.data.overviewTitle,'');assert.equal(page.data.overviewDescription,'');
});

test('old rejection deep link becomes ended and missing outcome totals never become a guessed zero',async()=>{
 const {page}=harness(async()=>packet([]));page.onLoad({tab:'rejected'});assert.equal(page.data.activeTab,'ended');assert.equal(page.taskParams().tab,'ended');
 page.data.activeTab='all';const old=packet([]);delete old.summary.ended_count;
 page.acceptTaskPage(old);assert.equal(page.data.completedCount,null);assert.equal(page.data.endedCount,null);
 assert.equal(page.data.tabs.find(row=>row.key==='ended').count,null);
 const current=packet([{id:'only-loaded',status:'completed'}]);current.items=[];
 current.summary={total:1000,pending_count:750,completed_count:200,ended_count:250,stopped_count:25,unknown_outcome_count:5,filtered_total:250};
 page.acceptTaskPage(current);assert.equal(page.data.completedCount,200);assert.equal(page.data.endedCount,250);assert.equal(page.data.stoppedCount,25);assert.equal(page.data.unknownOutcomeCount,5);
});
