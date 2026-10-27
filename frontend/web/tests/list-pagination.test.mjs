import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PAGE_SIZE_OPTIONS, listPageSizeKey, normalizePageSize, readListPageSize} from '../department-ui/pc/list-pagination.js';
import {historyParams} from '../department-ui/pc/customer-flow.js';
import {creationHistoryParams, normalizeCreationHistory, validCreationOffset} from '../department-ui/pc/opportunity-history.js';

test('page-size preferences contain only bounded numeric values and isolate workspace, account and list', () => {
  assert.deepEqual(PAGE_SIZE_OPTIONS, [10,20,50,100]);
  assert.equal(normalizePageSize('50'), 50);
  for (const value of [null,0,4,6,200,'all','bad']) assert.equal(normalizePageSize(value), 20);
  assert.equal(normalizePageSize(100,50),20);
  const actor={workspaceId:'w1',userId:'u1'};
  assert.notEqual(listPageSizeKey('tasks',actor),listPageSizeKey('customers',actor));
  assert.notEqual(listPageSizeKey('tasks',actor),listPageSizeKey('tasks',{...actor,userId:'u2'}));
  assert.notEqual(listPageSizeKey('tasks',actor),listPageSizeKey('tasks',{...actor,workspaceId:'w2'}));
  assert.equal(listPageSizeKey('tasks',null),null);
  assert.equal(readListPageSize('k',100,{getItem:()=> '100'}),100);
  assert.equal(readListPageSize('k',100,{getItem:()=>{throw Error('blocked');}}),20);
});

test('history page-size changes reach the server without changing authorization filters', () => {
  for(const pageSize of PAGE_SIZE_OPTIONS){
    const filters={scope:{kind:'member',memberId:'selected-person'},pageSize,offset:pageSize,q:'client'};
    assert.deepEqual(historyParams(filters),{scope:'team',member_id:'selected-person',limit:pageSize,offset:pageSize,q:'client'});
    assert.deepEqual(creationHistoryParams(filters),{scope:'team',member_id:'selected-person',limit:pageSize,offset:pageSize,q:'client'});
    assert.equal(validCreationOffset(200,75,pageSize),Math.floor(74/pageSize)*pageSize);
  }
});

test('creation page validation uses the selected limit and retains authoritative cursor checks', () => {
  const row={customer_id:'c',name:'example',customer_name:'Customer',creator_id:null,creator_name:null,owner_name:null,can_read_customer:false,business_created_on:null,created_at:null};
  const items=Array.from({length:50},(_,i)=>({...row,id:`o${i}`}));
  const result={items,total:75,has_more:true,next_offset:50};
  assert.equal(normalizeCreationHistory(result,0,50).items.length,50);
  assert.throws(()=>normalizeCreationHistory(result,0,20),/响应不完整/);
  assert.throws(()=>normalizeCreationHistory({...result,next_offset:100},0,50),/响应不完整/);
});
