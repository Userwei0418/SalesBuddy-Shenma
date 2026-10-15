import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readRunReference,saveRunReference} from '../department-ui/work-report/run-recovery.js';
const run={id:'20000000-0000-4000-8000-000000000001',documentId:null};
const makeStorage=()=>{const rows=new Map();return {rows,getItem:key=>rows.get(key),setItem:(key,value)=>rows.set(key,value),removeItem:key=>rows.delete(key)};};
test('recovery retains only one account-isolated reference, never content or an authorization decision',()=>{
 const storage=makeStorage();
 saveRunReference('tenant-a:user-a:login1',{...run,body:'PRIVATE',token:'SECRET',status:'running'},storage,100);
 assert.deepEqual(readRunReference('tenant-a:user-a:login1',storage,101),run);
 for(const owner of ['tenant-b:user-a:login1','tenant-a:user-b:login1','tenant-a:user-a:login2',''])assert.equal(readRunReference(owner,storage,101),null);
 assert.doesNotMatch([...storage.rows.values()].join(''),/PRIVATE|SECRET|running/);
 saveRunReference('tenant-b:user-a:login1',run,storage,102);assert.equal(storage.rows.size,1);
 saveRunReference('tenant-a:user-a:login1',null,storage,103);assert.deepEqual(readRunReference('tenant-b:user-a:login1',storage,103),run);
 saveRunReference('tenant-b:user-a:login1',null,storage,104);assert.equal(storage.rows.size,0);
});
test('navigation does not renew the seven-day reference lifetime; expiry never issues a cancellation',()=>{
 const storage=makeStorage(),week=7*24*60*60*1000;
 saveRunReference('owner',run,storage,100);const original=[...storage.rows.values()][0];
 saveRunReference('owner',run,storage,100+week-1);assert.equal([...storage.rows.values()][0],original);
 assert.equal(readRunReference('owner',storage,100+week),null);assert.equal(storage.rows.size,0);
 saveRunReference('owner',{...run,id:'bad'},storage,100);assert.equal(storage.rows.size,0);
 const broken={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
 assert.equal(readRunReference('owner',broken),null);assert.doesNotThrow(()=>saveRunReference('owner',run,broken));
});
