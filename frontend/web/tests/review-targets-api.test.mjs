import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {reviewWrite} from '../department-ui/review/targets/api.js';

function fixture(request) {
  const storage = new Map(), module = {exports: {}};
  vm.runInNewContext(readFileSync(new URL('../source/miniprogram/utils/requestIdentity.js', import.meta.url), 'utf8'), {module, Math, wx: {getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value)}});
  const client = {request, getBaseUrl: () => 'https://review-api.test/api/v1', getAuth: () => ({actor: {workspace_id:'w1',user_id:'u1'}})};
  globalThis.SalesRuntime = {app: {globalData: {session: {workspaceId:'w1',userId:'u1',permissionVersion:1}}}, require: name => name === 'utils/apiClient' ? client : name === 'utils/requestIdentity' ? module.exports : {identity: session => JSON.stringify(session)}};
  return {storage, receipts: module.exports};
}

test('review mutation deduplicates in flight and sends explicit Idempotency-Key through old client', async () => {
  const calls = []; let release;
  const {storage, receipts} = fixture(options => {calls.push(options); return new Promise(resolve => {release=resolve;});});
  const one=reviewWrite('/review/targets',{amount:'1.01'}), two=reviewWrite('/review/targets',{amount:'1.01'});
  assert.equal(calls.length,1); assert.match(calls[0].idempotencyKey,/^[\da-f-]{36}$/);
  release({status:'pending',target:null,items:[]}); await Promise.all([one,two]);
  assert.deepEqual(Object.keys(storage.get(receipts.STORAGE_KEY)),[]);
});

test('uncertain result retains receipt and retry reuses it; deterministic rejection clears it', async () => {
  const calls=[];let fail=true;
  const {storage,receipts}=fixture(async options=>{calls.push(options);if(fail)throw Object.assign(Error('network'),{statusCode:502});return {status:'effective'};});
  await assert.rejects(reviewWrite('/review/targets',{amount:'2.01'}));assert.equal(Object.keys(storage.get(receipts.STORAGE_KEY)).length,1);
  fail=false;await reviewWrite('/review/targets',{amount:'2.01'});assert.equal(calls[0].idempotencyKey,calls[1].idempotencyKey);
  const rejected=fixture(async()=>{throw Object.assign(Error('invalid'),{statusCode:422});});
  await assert.rejects(reviewWrite('/review/targets',{amount:'wrong'}));
  assert.deepEqual(Object.keys(rejected.storage.get(rejected.receipts.STORAGE_KEY)),[]);
});

test('invalid success receipt does not discard the saved mutation identity', async () => {
  const {storage,receipts}=fixture(async()=>({status:'unknown'}));
  await assert.rejects(reviewWrite('/review/targets',{amount:'3.01'}),/完整保存回执/);assert.equal(Object.keys(storage.get(receipts.STORAGE_KEY)).length,1);
});
