const test=require('node:test');
const assert=require('node:assert/strict');
const {IntentHistoryReader}=require('../miniprogram/utils/opportunityIntentHistory');
const REV='a'.repeat(64),NEXT='b'.repeat(64);
const result=(ids,{offset=0,more=false,revision=REV,subject='opp'}={})=>({opportunity_id:subject,items:ids.map(id=>({id,fields:[]})),offset,has_more:more,next_offset:more?offset+ids.length:null,history_revision:revision});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};

test('load reads at most10, reuses revision for paging, de-duplicates IDs, and retries the same offset',async()=>{
 const calls=[];let count=0;const reader=new IntentHistoryReader(async(id,options)=>{calls.push({id,...options});if(++count===2)throw Error('offline');return count===1?result(['a','b'],{more:true}):result(['b','c'],{offset:2});},()=>{},()=> 'actor');
 reader.reset('opp');await reader.load();await reader.load({more:true});assert.equal(reader.state.items.length,2);assert.equal(reader.state.error,'offline');await reader.load({retry:true});
 assert.deepEqual(reader.state.items.map(x=>x.id),['a','b','c']);assert.deepEqual(calls,[{id:'opp',limit:10,offset:0},{id:'opp',limit:10,offset:2,expected_history_revision:REV},{id:'opp',limit:10,offset:2,expected_history_revision:REV}]);
});
test('409 and a changed successful-page revision both preserve prior history until explicit refresh',async()=>{
 for(const failure of [true,false]){
  let count=0;const reader=new IntentHistoryReader(async()=>{count++;if(count===1)return result(['old'],{more:true});if(count===2){if(failure)throw Object.assign(Error('changed'),{statusCode:409});return result(['wrong'],{offset:1,revision:NEXT});}return result(['new'],{revision:NEXT});},()=>{},()=> 'actor');
  reader.reset('opp');await reader.load();await reader.load({more:true});assert.deepEqual(reader.state.items.map(x=>x.id),['old']);assert.equal(reader.state.conflict,true);await reader.load({retry:true});assert.equal(count,2);await reader.load({refresh:true});assert.deepEqual(reader.state.items.map(x=>x.id),['new']);assert.equal(reader.state.conflict,false);
 }
});
test('late success/error cannot reach a closed, different subject, or different login page',async()=>{
 for(const kind of ['close','subject','identity'])for(const failure of [false,true]){
  let actor='a',emits=0;const wait=deferred();const reader=new IntentHistoryReader(()=>wait.promise,()=>emits++,()=>actor);reader.reset('opp');const loading=reader.load();
  if(kind==='close')reader.close();if(kind==='subject')reader.reset('other');if(kind==='identity')actor='b';
  if(failure)wait.reject(Error('private error'));else wait.resolve(result(['private']));await loading;assert.equal(emits,1);assert.equal(reader.state.items.length,0);
 }
});
test('concurrent reads are not duplicated',async()=>{
 const wait=deferred();let count=0;const reader=new IntentHistoryReader(()=>{count++;return wait.promise;},()=>{},()=> 'a');reader.reset('opp');const first=reader.load();await reader.load();assert.equal(count,1);wait.resolve(result(['a']));await first;
});
test('wrong subject, invalid revision, oversize rows and invalid pagination cannot replace records',async()=>{
 for(const bad of [result(['x'],{subject:'other'}),result(['x'],{revision:'bad'}),result(Array.from({length:11},(_,i)=>String(i))),{...result(['x'],{more:true}),next_offset:0},{...result(['x']),offset:2}]){
  const reader=new IntentHistoryReader(async()=>bad,()=>{},()=> 'a');reader.reset('opp');await reader.load();assert.match(reader.state.error,/响应不完整/);assert.equal(reader.state.items.length,0);
 }
});
