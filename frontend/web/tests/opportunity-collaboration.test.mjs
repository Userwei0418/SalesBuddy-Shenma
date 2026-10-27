import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';
import {createOpportunityAccessApi, accessResponse} from '../department-ui/opportunity-access/api.js';
import {createCollaborationController} from '../department-ui/opportunity-access/controller.js';
const id='00000011-0000-4000-8000-000000000001', member={user_id:'00000001-0000-4000-8000-000000000002',display_name:'合成协作人'};
const record = () => ({contract_version:1,opportunity_id:id,name:'合成商机',version_no:1,owner:{user_id:null,display_name:null,team_id:null,team_name:null},customer_reference:{id:'00000010-0000-4000-8000-000000000001',name:'合成客户'},collaborators:[],capabilities:{can_transfer:false,can_manage_collaborators:true,can_follow_up:true,can_edit:false},capability_reasons:{transfer:'TRANSFER_NOT_READY'}});
const receipt = () => ({changed:true,event_id:'00000020-0000-4000-8000-000000000001',version_no:2,collaborators:[member]});
const fail = status => Object.assign(Error('synthetic failure'),{statusCode:status});
function transport(request) {
  const storage=new Map(),module={exports:{}};let actor='actor-1';
  vm.runInNewContext(fs.readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js',import.meta.url),'utf8'),{module,Math,wx:{getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v)}});
  const client={request,getAuth:()=>({actor:{workspace_id:'workspace',user_id:'user'}}),getBaseUrl:()=>'/api/v1'};
  return {api:createOpportunityAccessApi({getClient:()=>client,getIdentity:()=>actor,getReceipts:()=>module.exports}),storage,change:()=>{actor='actor-2';}};
}
test('complete G1 object capabilities required; unknown version/missing boolean never gains permission',()=>{
  assert.equal(accessResponse(record(),id).capabilities.can_transfer,false);
  for(const invalid of [{...record(),contract_version:2},{...record(),capabilities:{can_manage_collaborators:true}},{...record(),opportunity_id:member.user_id}])assert.throws(()=>accessResponse(invalid,id),/不完整/);
});
test('unknown write uses same persistent key, duplicate in flight sends once, complete receipt clears',async()=>{
  const calls=[];let release;
  const t=transport(options=>{calls.push(options);return new Promise(resolve=>{release=resolve;});});
  const data={version_no:1,member_ids:[member.user_id]};
  const a=t.api.save(id,data),b=t.api.save(id,data);assert.equal(calls.length,1);
  release({});await assert.rejects(a);await assert.rejects(b);
  const c=t.api.save(id,data);assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);release(receipt());await c;
  assert.equal(Object.keys(t.storage.get('salesPendingMutations:v1')).length,0);
});
test('read after identity switch is rejected and never returned to new actor',async()=>{
  let release;const t=transport(()=>new Promise(resolve=>{release=resolve;}));
  const p=t.api.access(id);t.change();release(record());await assert.rejects(p,{code:'SESSION_CHANGED'});
});
test('forbidden and version rejection finish receipt, network failure retains it',async()=>{
  for(const status of [403,404,409,422,502]){
    const t=transport(async()=>{throw fail(status);});await assert.rejects(t.api.save(id,{version_no:1,member_ids:[]}));
    assert.equal(Object.keys(t.storage.get('salesPendingMutations:v1')).length,status===502?1:0);
  }
});
test('candidate lookup uses collaborate purpose and paging; transfer route never called',async()=>{
  const paths=[];const t=transport(async o=>{paths.push(o.path);return {items:[member],total:21};});
  await t.api.candidates(id,'测试',20);const url=new URL(paths[0],'https://test.local');
  assert.equal(url.searchParams.get('purpose'),'collaborate');assert.equal(url.searchParams.get('offset'),'20');assert.equal(url.searchParams.get('q'),'测试');
});
test('read-only capability prevents directory reads and writes independent of roles',async()=>{
  let calls=0;const c=createCollaborationController(id,{access:async()=>({...record(),capabilities:{...record().capabilities,can_manage_collaborators:false}}),candidates:async()=>{calls++;},save:async()=>{calls++;}});
  await c.load();await c.search();c.toggle(member,true);await c.save();assert.equal(calls,0);assert.equal(c.getState().draft.length,0);
});
test('version conflict rereads but preserves choices, requires deliberate acknowledgment before resave',async()=>{
  let reads=0,writes=0;const bodies=[];
  const c=createCollaborationController(id,{access:async()=>({...record(),version_no:++reads}),save:async(_id,body)=>{bodies.push(body);if(++writes===1)throw fail(409);return receipt();}});
  await c.load();c.toggle(member,true);await c.save();assert.equal(c.getState().phase,'conflict');assert.deepEqual(c.getState().draft,[member]);
  await c.save();assert.equal(writes,1);c.acknowledge();await c.save();assert.equal(writes,2);assert.equal(bodies[1].version_no,2);assert.equal(c.dirty(),false);
});
test('uncertain save locks edits and replays original payload; no double click writes',async()=>{
  const bodies=[];let reject;const c=createCollaborationController(id,{access:async()=>record(),save:(_id,body)=>{bodies.push(body);if(bodies.length===1)return new Promise((_r,j)=>{reject=j;});return receipt();}});
  await c.load();c.toggle(member,true);const p=c.save();await c.save();assert.equal(bodies.length,1);reject(fail(502));await p;
  c.toggle(member,false);assert.deepEqual(c.getState().draft,[member]);await c.load();assert.equal(c.getState().phase,'uncertain');await c.save();assert.deepEqual(bodies[0],bodies[1]);
});
test('revoked read/write leaves local selection but no stale capabilities',async()=>{
  for(const status of [403,404]){
    const c=createCollaborationController(id,{access:async()=>record(),save:async()=>{throw fail(status);}});
    await c.load();c.toggle(member,true);await c.save();assert.equal(c.getState().phase,'denied');assert.equal(c.getState().access,null);assert.deepEqual(c.getState().draft,[member]);
  }
});
test('late candidate response cannot overwrite newer search or survive disposal',async()=>{
  const resolve=[];const c=createCollaborationController(id,{access:async()=>record(),candidates:()=>new Promise(r=>resolve.push(r))});
  await c.load();const a=c.search('old'),b=c.search('new');resolve[1]({items:[member],total:1});await b;resolve[0]({items:[],total:0});await a;assert.deepEqual(c.getState().candidates,[member]);
  const p=c.search('later');c.dispose();resolve[2]({items:[member],total:1});await p;assert.equal(c.getState().candidates.length,0);
});
test('maximum selection and explicit empty set removal use full target list',async()=>{
  let body;const people=Array.from({length:30},(_,i)=>({...member,user_id:String(i)}));
  const c=createCollaborationController(id,{access:async()=>({...record(),collaborators:people}),save:async(_id,b)=>{body=b;return {...receipt(),collaborators:[]};}});
  await c.load();c.toggle(member,true);assert.equal(c.getState().draft.length,30);people.forEach(p=>c.toggle(p,false));await c.save();assert.deepEqual(body.member_ids,[]);
});
