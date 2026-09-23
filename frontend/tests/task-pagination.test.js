const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
const packet=(ids,more=false,next=null)=>({items:ids.map(id=>({id,title:id,status:'pending_execution'})),summary:{total:75,pending_count:55,completed_count:20,filtered_total:55},has_more:more,next_offset:next});
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
