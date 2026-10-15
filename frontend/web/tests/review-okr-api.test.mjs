import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {allPages,participantDirectory,reviewOkrApi} from '../department-ui/review/targets/okr/api.js';
function fixture(request){
  const storage=new Map(),module={exports:{}};
  vm.runInNewContext(readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8'),{module,Math,wx:{getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value)}});
  const client={request,getBaseUrl:()=> 'https://review-okr-api.test/api/v1',getAuth:()=>({actor:{workspace_id:'w1',user_id:'u1'}})};
  globalThis.SalesRuntime={app:{globalData:{session:{workspaceId:'w1',userId:'u1',permissionVersion:1}}},require:name=>name==='utils/apiClient'?client:name==='utils/requestIdentity'?module.exports:{identity:session=>JSON.stringify(session)}};
  return{storage,receipts:module.exports};
}
test('parent catalog follows every page and rejects changing or incomplete pagination',async()=>{
  fixture(async()=>({}));const calls=[];
  const all=Array.from({length:201},(_,i)=>({id:`id-${i}`}));
  assert.equal((await allPages(async params=>{calls.push(params);return{items:all.slice(params.offset,params.offset+100),total:all.length};})).length,201);
  assert.deepEqual(calls.map(call=>call.offset),[0,100,200]);
  await assert.rejects(allPages(async()=>({items:[{id:'one'}],total:3})),/不完整/);
  await assert.rejects(allPages(async({offset})=>({items:all.slice(offset,offset+100),total:offset?202:201})),/变化/);
  assert.equal((await allPages(async()=>({items:[{id:null,row_key:'pending-new'}],total:1})))[0].id,null);
});
test('participant candidates merge exact authorized IDs; directory denial does not erase writable options people',async()=>{
  fixture(async({path})=>path.startsWith('/directory/')?{items:[{id:'a',name:'甲',team_ids:[]}]}:{people:[{id:'a',name:'甲'},{id:'ops',name:'运营',can_manage:true}]});
  assert.deepEqual((await participantDirectory()).items.map(row=>row.id),['a','ops']);
  fixture(async({path})=>{if(path.startsWith('/directory/'))throw Object.assign(Error('denied'),{statusCode:403});return{people:[{id:'self',name:'本人'}]};});
  const result=await participantDirectory();assert.equal(result.limited,true);assert.deepEqual(result.items,[{id:'self',name:'本人'}]);
});
test('O/KR saves carry explicit persistent idempotency; malformed receipt retries reuse it',async()=>{
  const calls=[];let valid=false;const {storage,receipts}=fixture(async options=>{calls.push(options);return valid?{id:'o1',version_no:2}:{};});
  await assert.rejects(reviewOkrApi.saveObjective(null,{title:'目标'}),/回执不完整/);assert.equal(Object.keys(storage.get(receipts.STORAGE_KEY)).length,1);
  valid=true;await reviewOkrApi.saveObjective(null,{title:'目标'});assert.match(calls[0].idempotencyKey,/^[\da-f-]{36}$/);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.deepEqual(Object.keys(storage.get(receipts.STORAGE_KEY)),[]);
});
test('mention receipt only represents queued notice, and deterministic denial clears its intent',async()=>{
  const {storage,receipts}=fixture(async()=>{throw Object.assign(Error('forbidden'),{statusCode:403});});
  await assert.rejects(reviewOkrApi.mention('objective','o1',{version_no:2,recipient_user_ids:['u2']}));assert.deepEqual(Object.keys(storage.get(receipts.STORAGE_KEY)),[]);
  fixture(async()=>({status:'queued',created:0,permission_changed:false}));assert.equal((await reviewOkrApi.mention('objective','o1',{version_no:2,recipient_user_ids:['u2']})).created,0);
});
