require('./helpers/business-options');
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {AdvicePool}=require('../miniprogram/utils/advicePool');
const access=require('../miniprogram/utils/access');
const tick=()=>new Promise(r=>setImmediate(r));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const tabs=['overview','tasks','visits','opportunity'];
const response=(overrides={})=>({id:'analysis-overview',subject_kind:'opportunity',subject_id:'o1',opportunity_id:'o1',customer_id:'c1',section:'overview',status:'succeeded',summary:'verified advice',suggestions:[],...overrides});
function setup(api={},pool=new AdvicePool()){
  const calls=[];let page;
  const app={ensureLogin:()=>true,globalData:{session:{workspaceId:'w',userId:'u',role:'sales',permissionVersion:1,permissions:{'advice.read':true,'advice.request':true,'advice.opportunity':true}}}};
  const raw={id:'c1',name:'customer',opportunities:[{id:'o1',name:'opportunity'}]};
  const client={getOpportunityDetailHeader:async()=>raw,getOpportunityDetailOverview:async()=>({...raw,summary:{}}),
    listDetailTasks:async()=>({items:[],has_more:false,next_offset:null}),listVisits:async()=>({items:[],has_more:false,next_offset:null}),getOpportunityTimeline:async()=>({items:[],has_more:false,next_offset:null}),
    getLatestBusinessAdvice:async()=>null,requestBusinessAdvice:async()=>response({status:'queued'}),...api};
  for(const method of ['getLatestBusinessAdvice','requestBusinessAdvice']){
    const original=client[method];client[method]=(...args)=>{calls.push([method.startsWith('get')?'GET':'POST',...args]);return original(...args);};
  }
  const file=path.resolve(__dirname,'../miniprogram/pages/customer-assets/index.js');
  vm.runInNewContext(fs.readFileSync(file,'utf8'),{Page:p=>page=p,getApp:()=>app,wx:{},Date,
    require:n=>n.endsWith('apiClient')?client:n.endsWith('advicePool')?{opportunityAdvicePool:pool}:require(path.resolve(path.dirname(file),n))});
  page.data=JSON.parse(JSON.stringify(page.data));page.setData=v=>Object.assign(page.data,v);
  page.onLoad({customer_id:'c1',opportunity_id:'o1'});page._opportunityContext=raw;page._adviceGeneration=1;
  const action=()=>({currentTarget:{dataset:{adviceAction:'generate',opportunityId:page.data.opportunityId,section:page.data.opportunityTab}}});
  return {page,app,pool,calls,action,client,raw,key:tab=>pool.key(access.identity(app.globalData.session),'o1',tab)};
}
async function showAdvice(h){h.page.load=()=>{};h.page.loadQuarterActuals=()=>{};h.page.onShow();await tick();await tick();}
for(const readOnly of [false,true])test(`entry/prefetch/tab/change events only discover latest by GET; readonly=${readOnly}`,async()=>{
  const h=setup();h.page.data.readOnly=readOnly;await h.page.loadOpportunity();await tick();
  for(const tab of tabs){h.page.selectOpportunityTab({currentTarget:{dataset:{tab}}});await tick();await h.page.loadOpportunityAdvice(h.action());}
  assert.ok(h.calls.length>=4);assert.ok(h.calls.every(c=>c[0]==='GET'&&c[1]==='opportunity'&&c[2]==='o1'));
  for(const tab of tabs)assert.equal(h.page.data.opportunityAdvice[tab].status,'idle');
});
test('readonly opportunity explicit button submits once; queued refresh never posts again',async()=>{
  const post=deferred(),h=setup({requestBusinessAdvice:()=>post.promise,getLatestBusinessAdvice:async()=>response({status:'running'})});h.page.data.readOnly=true;
  const first=h.page.requestOpportunityAdvice(h.action());await tick();await h.page.requestOpportunityAdvice(h.action());assert.equal(h.calls.length,1);
  assert.deepEqual(h.calls[0],['POST','opportunity','o1','overview',true]);post.resolve(response({status:'queued'}));await first;
  assert.equal(h.page.data.opportunityAdvice.overview.id,'analysis-overview');
  await h.page.requestOpportunityAdvice(h.action());await tick();assert.deepEqual(h.calls.map(c=>c[0]),['POST','GET']);assert.equal(h.page.data.opportunityAdvice.overview.status,'running');
});
test('arbitrary event and mismatched subject/section do not generate',async()=>{
  const h=setup();
  for(const event of [undefined,{currentTarget:{}},{currentTarget:{dataset:{adviceAction:'generate',opportunityId:'other',section:'overview'}}},{currentTarget:{dataset:{adviceAction:'generate',opportunityId:'o1',section:'tasks'}}}])await h.page.requestOpportunityAdvice(event);
  assert.equal(h.calls.length,0);h.page.data.opportunityId='other';await h.page.requestOpportunityAdvice(h.action());assert.equal(h.calls.length,0);
});
for(const permission of ['advice.read','advice.request','advice.opportunity'])test(`generate checks ${permission}`,async()=>{
  const h=setup();h.app.globalData.session.permissions[permission]=false;await h.page.requestOpportunityAdvice(h.action());assert.equal(h.calls.length,0);
});
test('read-only AI actor can discover existing advice, cannot generate',async()=>{
  const h=setup({getLatestBusinessAdvice:async()=>response()});h.app.globalData.session.permissions['advice.request']=false;
  await h.page.loadOpportunityAdvice();await h.page.requestOpportunityAdvice(h.action());assert.equal(h.page.data.opportunityAdvice.overview.status,'ready');assert.deepEqual(h.calls.map(c=>c[0]),['GET']);
});
test('read permission revocation clears previous advice without any request',async()=>{
  const h=setup();h.page.data.opportunityAdvice.overview={id:'old',status:'ready',summary:'old'};h.app.globalData.session.permissions['advice.read']=false;
  await h.page.loadOpportunityAdvice();assert.equal(h.page.data.opportunityAdvice.overview,undefined);assert.equal(h.calls.length,0);
});
for(const mutate of [h=>h.app.globalData.session.userId='new',h=>h.app.globalData.session.workspaceId='other',h=>h.app.globalData.session.role='other',h=>h.app.globalData.session.permissionVersion++,h=>h.page.data.opportunityId='other',h=>h.page.data.customerId='other',h=>h.page.onHide(),h=>h.page.onUnload()])test('late read discarded: '+mutate.toString(),async()=>{
  const pending=deferred(),h=setup({getLatestBusinessAdvice:()=>pending.promise});const promise=h.page.loadOpportunityAdvice();await tick();mutate(h);pending.resolve(response({summary:'late secret'}));await promise;
  assert.notEqual(h.page.data.opportunityAdvice.overview?.summary,'late secret');assert.equal(h.pool.cache.size,0);
});
for(const mutate of [h=>h.page.data.opportunityTab='tasks',h=>h.page.data.opportunityId='other',h=>h.app.globalData.session.permissions['advice.request']=false,h=>h.page.onHide()])test('queued generation rechecks before dispatch: '+mutate.toString(),async()=>{
  const pool=new AdvicePool(1),block=deferred(),busy=pool.request('block',()=>block.promise),h=setup({},pool);
  const pending=h.page.requestOpportunityAdvice(h.action());mutate(h);block.resolve(null);await busy;await pending;assert.equal(h.calls.length,0);
});
for(const bad of [{subject_kind:'customer'},{subject_id:'other'},{opportunity_id:'other'},{customer_id:'other'},{section:'tasks'},{status:'unknown'}])test('complete subject/section contract enforced: '+JSON.stringify(bad),async()=>{
  for(const generate of [false,true]){
    const h=setup({getLatestBusinessAdvice:async()=>response(bad),requestBusinessAdvice:async()=>response(bad)});
    h.page.data.opportunityAdvice.overview={id:'old',status:'ready',summary:'old',needsCheck:true};
    await (generate?h.page.requestOpportunityAdvice(h.action()):h.page.loadOpportunityAdvice());assert.equal(h.page.data.opportunityAdvice.overview.status,'error');assert.equal(h.page.data.opportunityAdvice.overview.summary,undefined);assert.equal(h.pool.peek(h.key('overview')),null);
  }
});
for(const [status,view] of [['queued','queued'],['running','running'],['failed','error'],['superseded','stale']])test(`cold page recovers ${status} without a stored ID or POST`,async()=>{
  const h=setup({getLatestBusinessAdvice:async()=>response({status})});await h.page.loadOpportunityAdvice();assert.equal(h.page.data.opportunityAdvice.overview.status,view);assert.equal(h.page.data.opportunityAdvice.overview.id,'analysis-overview');assert.equal(h.calls[0][0],'GET');
});
test('server explicit no-record clears stale preview; malformed POST null is an error',async()=>{
  const h=setup();h.page.data.opportunityAdvice.overview={id:'old',status:'ready',summary:'old',needsCheck:true};await h.page.loadOpportunityAdvice();assert.equal(h.page.data.opportunityAdvice.overview.status,'idle');assert.equal(h.page.data.opportunityAdvice.overview.id,undefined);
  const post=setup({requestBusinessAdvice:async()=>null});await post.page.requestOpportunityAdvice(post.action());assert.equal(post.page.data.opportunityAdvice.overview.status,'error');
});
test('newer queued generation replaces cached success on reopen and manual refresh',async()=>{
  const h=setup({getLatestBusinessAdvice:async(kind,id,section)=>section==='overview'?response({id:'new',status:'queued'}):null});
  await h.pool.request(h.key('overview'),async()=>response({id:'old'}),{owner:{}});await tick();await showAdvice(h);
  assert.equal(h.page.data.opportunityAdvice.overview.status,'queued');assert.equal(h.page.data.opportunityAdvice.overview.id,'new');assert.equal(h.pool.peek(h.key('overview')),null);assert.equal(h.calls.filter(c=>c[0]==='POST').length,0);
});
test('403 discards preview while transient failure labels it unchecked and blocks decisions',async()=>{
  for(const denied of [true,false]){
    const h=setup({getLatestBusinessAdvice:async()=>{throw Object.assign(Error('read failed'),denied?{statusCode:403}:{});}});
    await h.pool.request(h.key('overview'),async()=>response(),{owner:{}});await tick();await showAdvice(h);
    const state=h.page.data.opportunityAdvice.overview;
    if(denied){assert.equal(state.status,'error');assert.equal(state.id,undefined);assert.equal(h.pool.peek(h.key('overview')),null);}
    else{assert.equal(state.summary,'verified advice');assert.equal(state.needsCheck,true);}
  }
});
test('reentering a new page with empty memory recovers server result with GET only',async()=>{
  const old=setup();await old.page.requestOpportunityAdvice(old.action());await tick();old.page.onUnload();
  const next=setup({getLatestBusinessAdvice:async(kind,id,section)=>section==='overview'?response():null});next.page.data.readOnly=true;await showAdvice(next);
  assert.equal(next.page.data.opportunityAdvice.overview.status,'ready');assert.ok(next.calls.every(c=>c[0]==='GET'));
});
test('template separates generation/read and keeps business decisions readonly',()=>{
  const text=fs.readFileSync(path.resolve(__dirname,'../miniprogram/pages/customer-assets/index.wxml'),'utf8');
  assert.match(text,/canReadAdvice && canOpportunityAdvice[^>]+bindtap="requestOpportunityAdvice"/);assert.doesNotMatch(text,/!readOnly[^>]+bindtap="requestOpportunityAdvice"/);
  assert.match(text,/bind:changed="loadOpportunityAdvice"/);assert.match(text,/<advice-actions wx:if="\{\{!readOnly/);
});
test('immediate reopen while old latest read is pending gets a new valid reader',async()=>{
  const pool=new AdvicePool(1),pending=deferred(),old=setup({getLatestBusinessAdvice:()=>pending.promise},pool);
  const oldRead=old.page.loadOpportunityAdvice();await tick();old.page.onHide();
  const next=setup({getLatestBusinessAdvice:async()=>response({id:'fresh-analysis'})},pool);
  const newRead=next.page.loadOpportunityAdvice();pending.resolve(response({id:'old-analysis'}));await oldRead;await newRead;
  assert.equal(next.page.data.opportunityAdvice.overview.status,'ready');assert.equal(next.page.data.opportunityAdvice.overview.id,'fresh-analysis');
  assert.deepEqual(next.calls.map(c=>c[0]),['GET']);assert.notEqual(old.page.data.opportunityAdvice.overview?.id,'old-analysis');
});
