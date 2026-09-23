const test = require('node:test');
const assert = require('node:assert/strict');
const {DetailReadSession,detailWithPages,activeSections}=require('../miniprogram/utils/detailReadSession');
const {normalizeCustomerDetail}=require('../miniprogram/utils/customerDetail');
const access=require('../miniprogram/utils/access');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const response=(ids,has_more=false,next_offset=null)=>({items:ids.map(id=>({id})),has_more,next_offset});

test('history pagination requests only 20 rows, deduplicates in-flight reads and preserves rows on retry',async()=>{
 const reader=new DetailReadSession(null,()=> 'identity');reader.reset('customer');const first=deferred(),calls=[];
 const one=reader.load('visits',p=>{calls.push(p);return first.promise;});await reader.load('visits',()=>{throw Error('duplicate call');});
 first.resolve(response(Array.from({length:20},(_,i)=>`v${i}`),true,20));await one;
 assert.deepEqual(calls,[{page_size:20,offset:0}]);
 await reader.load('visits',()=>{throw Error('already loaded tab');});
 await reader.load('visits',p=>{calls.push(p);throw Error('offline');},{more:true});
 assert.equal(reader.state('visits').items.length,20);assert.equal(reader.state('visits').error,'offline');
 await reader.load('visits',p=>{calls.push(p);return response(['v19','v20']);},{retry:true});
 assert.equal(reader.state('visits').items.length,21);assert.equal(reader.state('visits').error,'');
 assert.deepEqual(calls.map(p=>p.offset),[0,20,20]);assert.equal(reader.state('visits').hasMore,false);
});
test('closing, switching subject, or changing permission version rejects late successes and errors',async()=>{
 for(const change of ['close','subject','permission','login'])for(const failure of [false,true]){
  const session={workspaceId:'w',userId:'u',role:'sales',permissionVersion:1,loginAt:'1'};let updates=0;
  const reader=new DetailReadSession(()=>updates++,()=>access.identity(session));reader.reset('a');const d=deferred();const wait=reader.load('tasks',()=>d.promise);
  if(change==='close')reader.close();if(change==='subject')reader.reset('b');if(change==='permission')session.permissionVersion=2;if(change==='login')session.loginAt='2';
  if(failure)d.reject(Error('late private error'));else d.resolve(response(['private']));await wait;
  assert.equal(updates,1,`${change} late result not emitted`);assert.equal(reader.state('tasks').items.length,0);
 }
});
test('unbounded or malformed history responses fail locally without replacing other sections',async()=>{
 const reader=new DetailReadSession(null,()=> 'i');reader.reset('c');await reader.load('contacts',()=>response(['contact']));
 for(const invalid of [{items:[]},{items:[{}],has_more:false},{items:[],has_more:true,next_offset:0},response(Array.from({length:21},(_,i)=>i+1))]){
  await reader.load('visits',()=>invalid,{retry:true});assert.match(reader.state('visits').error,/响应不完整/);assert.equal(reader.state('contacts').items.length,1);
 }
});
test('overview totals, risk and hexagon are database facts independent of loaded histories and focus',()=>{
 const primary={id:'last',name:'首要项目',status:'open',amount:null,stage_code:'proposal'};
 const raw={id:'c',name:'客户',primary_opportunity:primary,profile:{dimensions:Array.from({length:6},(_,i)=>({label:`维度${i}`,value:20+i}))},summary:{opportunity_count:73,contact_count:90,task_count:84,visit_count:105,open_amount:null,task_status_counts:{completed:66,in_progress:12,cancelled:6},latest_visit:{next_action:'全量最新计划'},open_risk:{title:'第72条项目风险'},status_risk:{title:'已接受风险'},},attributes:{}};
 const pages={opportunities:{items:Array.from({length:20},(_,i)=>({id:`o${i}`,status:'open',amount:10000}))},tasks:{items:[{id:'t',status:'completed'}]}};
 const detail=normalizeCustomerDetail(detailWithPages(raw,pages,primary),'last');
 assert.equal(detail.opportunities.length,21);assert.equal(detail.opportunityCount,73);assert.equal(detail.contactCount,90);assert.equal(detail.taskCount,84);assert.equal(detail.completedTaskCount,66);assert.equal(detail.pendingTaskCount,12);assert.equal(detail.visitCount,105);
 assert.equal(detail.annualValue,'—');assert.equal(detail.opportunity.amount,'—');assert.equal(detail.risk,'第72条项目风险');assert.equal(detail.nextAction,'全量最新计划');assert.deepEqual(detail.customerProfile.map(d=>d.value),[20,21,22,23,24,25]);
 assert.equal(normalizeCustomerDetail({...raw,summary:{...raw.summary,open_amount:0}}).annualValue,'0元');
 assert.equal(normalizeCustomerDetail({...raw,summary:{...raw.summary,latest_visit:{next_action:''}},visits:[{next_action:'已加载页里的旧计划'}]}).nextAction,'暂无下一步行动');
 assert.deepEqual(activeSections('overview'),['opportunities','contacts']);assert.deepEqual(activeSections('visits'),['visits']);
});


test('visit seek pages forward cursor and preserve the failed position without refreshing overview',async()=>{
 const reader=new DetailReadSession(null,()=> 'identity');reader.reset('customer');const calls=[];
 const first={...response(['v1'],true,20),next_cursor:'position-1'};
 await reader.load('visits',p=>{calls.push(p);return first;});
 await reader.load('visits',p=>{calls.push(p);throw Error('offline');},{more:true});
 assert.equal(reader.state('visits').nextCursor,'position-1');
 assert.equal(reader.state('visits').items.length,1);
 await reader.load('visits',p=>{calls.push(p);return {...response(['v2'],true,null),next_cursor:'position-2'};},{retry:true});
 await reader.load('visits',p=>{calls.push(p);return {...response(['v3']),next_cursor:null};},{more:true});
 assert.deepEqual(calls,[{page_size:20,offset:0},{page_size:20,cursor:'position-1'},{page_size:20,cursor:'position-1'},{page_size:20,cursor:'position-2'}]);
 assert.equal(reader.state('visits').items.length,3);assert.equal(reader.state('visits').hasMore,false);
});
test('visit cursor resets on subject or identity change and never accepts a repeating cursor',async()=>{
 let identity='one';const reader=new DetailReadSession(null,()=>identity);reader.reset('a');
 await reader.load('visits',()=>({...response(['v1'],true,20),next_cursor:'a-1'}));
 await reader.load('visits',()=>({...response(['v2'],true,null),next_cursor:'a-1'}),{more:true});
 assert.match(reader.state('visits').error,/响应不完整/);assert.equal(reader.state('visits').items.length,1);
 reader.reset('b');const calls=[];await reader.load('visits',p=>{calls.push(p);return response(['b']);});
 assert.deepEqual(calls,[{page_size:20,offset:0}]);
 identity='two';reader.reset('b');const d=deferred();const pending=reader.load('visits',()=>d.promise);identity='three';
 d.resolve({...response(['private'],true,20),next_cursor:'secret'});await pending;
 assert.equal(reader.state('visits').nextCursor,null);
});

test('complete summary resources deduplicate requests and discard results after permission change or close',async()=>{
 for(const reject of [false,true])for(const invalidate of ['close','identity']){
  let identity='one',finish,fail,calls=0;const pending=new Promise((a,b)=>{finish=a;fail=b;});
  const reader=new DetailReadSession(()=>{},()=>identity);reader.reset('customer');
  const first=reader.loadResource('overview',()=>{calls++;return pending;});await reader.loadResource('overview',()=>{calls++;return pending;});assert.equal(calls,1);
  if(invalidate==='close')reader.close();else identity='two';reject?fail(Error('late')):finish({id:'customer',summary:{visit_count:99}});await first;
  assert.equal(reader.resource('overview').data,null);assert.equal(reader.resource('overview').error,'');
 }
});
