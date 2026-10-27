import {test} from 'node:test';
import assert from 'node:assert/strict';
import {searchCustomers} from '../department-ui/pc/knowledge-data.js';

test('Playbook search keeps the detail-readable directory and explicitly requests customer identity', async () => {
  const previous=globalThis.SalesRuntime,calls=[];
  globalThis.SalesRuntime={require:name=>{assert.equal(name,'utils/apiClient');return {listCustomers:async options=>{calls.push(options);return {items:[{id:'dual',name:'Dual entity',business_identities:['customer','partner']}]};}};}};
  try {
    assert.deepEqual(await searchCustomers('  query  '),[{id:'dual',name:'Dual entity',owner:'',industry:'',level:''}]);
    assert.deepEqual(calls,[{q:'query',pageSize:50,businessIdentity:'customer'}]);
  } finally {globalThis.SalesRuntime=previous;}
});
