import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createTransferController} from '../department-ui/opportunity-access/transfer-controller.js';
import {transferResult,createOpportunityAccessApi} from '../department-ui/opportunity-access/api.js';
import fs from 'node:fs';
import vm from 'node:vm';
const id='00000011-0000-4000-8000-000000000001',person={user_id:'00000001-0000-4000-8000-000000000002',display_name:'合成销售',teams:[{team_id:'00000030-0000-4000-8000-000000000001',name:'合成团队'}]};
const access=()=>({version_no:1,capabilities:{can_transfer:true},owner:{display_name:'旧主责'}});
const result=()=>({changed:true,event_id:'00000020-0000-4000-8000-000000000001',version_no:2,owner:{user_id:person.user_id,display_name:person.display_name,team_id:person.teams[0].team_id,team_name:'合成团队'},collaborators:[],sync_status:'pending'});
const fail=statusCode=>Object.assign(Error('synthetic'),{statusCode});
async function select(c){await c.load();await c.search();c.choose(person.user_id);c.edit('teamId',person.teams[0].team_id);c.edit('reason','区域调整');}
test('no transfer permission means no candidates or write regardless of collaboration rights',async()=>{
 let writes=0;const c=createTransferController(id,{access:async()=>({...access(),capabilities:{can_transfer:false,can_manage_collaborators:true}}),transferCandidates:async()=>{writes++;},transfer:async()=>{writes++;}});await c.load();await c.search();await c.save();assert.equal(writes,0);
});
test('uncertain transfer locks target and replays exact original payload; readback denial does not undo successful receipt',async()=>{
 let reads=0;const bodies=[];const c=createTransferController(id,{access:async()=>{if(++reads>1)throw fail(403);return access();},transferCandidates:async()=>({items:[person],total:1}),transfer:async(_id,b)=>{bodies.push(b);if(bodies.length===1)throw fail(502);return result();}});await select(c);await c.save();assert.equal(c.getState().phase,'uncertain');c.edit('reason','must not change');await c.load();await c.save();assert.deepEqual(bodies[0],bodies[1]);assert.equal(c.getState().phase,'done');assert.equal(c.getState().result.owner.user_id,person.user_id);assert.equal(c.getState().access,null);
});
test('transfer result validates owner team and sync status rather than claiming external synchronization',()=>{
 assert.equal(transferResult(result()).sync_status,'pending');for(const value of [{...result(),owner:{...result().owner,team_id:null}},{...result(),sync_status:'synced'}])assert.throws(()=>transferResult(value));
});
test('transfer transport keeps persistent key after unknown response and rejects cross-identity receipt',async()=>{
 const module={exports:{}},storage=new Map();vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8'),{module,Math,wx:{getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v)}});
 let who='actor',count=0;const calls=[],client={getAuth:()=>({actor:{workspace_id:'space',user_id:'user'}}),getBaseUrl:()=>'/api/v1',request:async o=>{calls.push(o);if(++count===1)throw fail(502);if(count===3)who='other';return result();}};
 const api=createOpportunityAccessApi({getClient:()=>client,getIdentity:()=>who,getReceipts:()=>module.exports}),body={version_no:1,new_owner_user_ref_id:person.user_id,new_owner_team_id:person.teams[0].team_id,reason:'调整'};
 await assert.rejects(api.transfer(id,body));await api.transfer(id,body);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);assert.equal(calls[0].method,'POST');await assert.rejects(api.transfer(id,{...body,version_no:2}),{code:'SESSION_CHANGED'});
});
