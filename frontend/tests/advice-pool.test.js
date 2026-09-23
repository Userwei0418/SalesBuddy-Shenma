const test=require('node:test');
const assert=require('node:assert/strict');
const {AdvicePool}=require('../miniprogram/utils/advicePool');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
test('two concurrent requests, clicked queued tab overtakes prefetch, same key shares one request',async()=>{
 const pool=new AdvicePool(),owner={},started=[],pending={};
 const request=tab=>pool.request(tab,()=>{started.push(tab);pending[tab]=deferred();return pending[tab].promise;},{owner});
 const a=request('overview'),b=request('tasks'),c=request('visits'),d=request('progress');
 assert.equal(request('overview'),a);await tick();assert.deepEqual(started,['overview','tasks']);
 pool.prioritize('progress');pending.overview.resolve({status:'succeeded'});await a;await tick();
 assert.deepEqual(started,['overview','tasks','progress']);assert.equal(pool.active,2);
 pending.tasks.resolve({status:'succeeded'});await b;await tick();assert.equal(started[3],'visits');
 pending.progress.resolve({status:'succeeded'});pending.visits.resolve({status:'succeeded'});await Promise.all([c,d]);
});
test('leaving cancels unstarted work, in-flight result can be reused but next request revalidates',async()=>{
 const pool=new AdvicePool(1),owner={},pending=deferred();let secondCalled=false,calls=0;
 const a=pool.request('a',()=>{calls++;return pending.promise;},{owner});
 const b=pool.request('b',()=>{secondCalled=true;},{owner});const cancelled=assert.rejects(b,/页面已离开/);
 await tick();pool.release(owner);await cancelled;pending.resolve({status:'succeeded',id:'a'});await a;await tick();
 assert.equal(secondCalled,false);assert.equal(pool.peek('a').id,'a');
 await pool.request('a',async()=>{calls++;return {status:'succeeded',id:'new'};},{owner:{}});
 assert.equal(calls,2);assert.equal(pool.peek('a').id,'new');
});
test('cache identity includes tenant, account and permissions; stale identity cannot publish; cache bounded',async()=>{
 const pool=new AdvicePool(2,2);let valid=true;const pending=deferred();
 const key=pool.key('tenant:user:role:v1','op','overview');
 const a=pool.request(key,()=>pending.promise,{valid:()=>valid});const rejected=assert.rejects(a,/登录身份/);
 await tick();valid=false;pending.resolve({status:'succeeded'});await rejected;assert.equal(pool.peek(key),null);
 for(const identity of ['tenant:user:role:v1','tenant:user:role:v2','other:user:role:v2']){
  await pool.request(pool.key(identity,'op','overview'),async()=>({status:'succeeded',identity}));await tick();
 }
 assert.equal(pool.cache.size,2);assert.equal(pool.peek(key),null);
 assert.equal(pool.peek(pool.key('tenant:user:role:v2','op','overview')).identity,'tenant:user:role:v2');
});
test('one failure does not block queued tabs and is never cached',async()=>{
 const pool=new AdvicePool(1);const bad=pool.request('bad',async()=>{throw Error('failed');});
 const good=pool.request('good',async()=>({status:'succeeded'}));await assert.rejects(bad);await good;
 assert.equal(pool.peek('bad'),null);assert.equal(pool.peek('good').status,'succeeded');
});
