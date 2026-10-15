import assert from 'node:assert/strict';
import {test} from 'node:test';
import {calendarHint,appendSources,ReadSequence} from '../department-ui/work-report/state.js';

test('report calendar hints use natural weeks, cross-year biweeks and leap months',()=>{
  assert.equal(calendarHint('biweek','2026-10-08'),'2026-09-28 — 2026-10-11');
  assert.equal(calendarHint('biweek','2027-01-01'),'2026-12-21 — 2027-01-03');
  assert.equal(calendarHint('month','2024-02-29'),'2024-02-01 — 2024-02-29');
  assert.equal(calendarHint('month','2025-02-29'),'');
  assert.equal(calendarHint('quarter','2026-10-08'),'2026-10-01 — 2026-12-31');
});
test('source pagination does not silently count duplicate or missing pages as complete',()=>{
  assert.throws(()=>appendSources([{ref:'visit:1'}],{items:[{ref:'visit:1'}]}),/已变化/);
  assert.throws(()=>appendSources([],{items:[],hasMore:true,nextOffset:20}),/尚未完成/);
  assert.throws(()=>appendSources([],{items:[{ref:'visit:1'}],hasMore:true}),/尚未完成/);
  assert.deepEqual(appendSources([{ref:'visit:1'}],{items:[{ref:'task:1'}],hasMore:false}),[{ref:'visit:1'},{ref:'task:1'}]);
});
test('late document and error responses cannot replace a newer document or changed identity',async()=>{
  const seq=new ReadSequence();let owner='a',resolve,reject;
  const late=seq.run(owner,()=>owner,()=>new Promise(r=>{resolve=r;}));
  const latest=await seq.run(owner,()=>owner,async()=>({id:'new'}));
  resolve({id:'old'});assert.equal((await late).current,false);assert.equal(latest.current,true);
  const failed=seq.run(owner,()=>owner,()=>new Promise((_,r)=>{reject=r;}));
  owner='b';reject(Error('old denied'));assert.equal((await failed).current,false);
  const switched=seq.run(owner,()=>owner,()=>new Promise(r=>{resolve=r;}));seq.invalidate();resolve({id:'closed'});assert.equal((await switched).current,false);
});
