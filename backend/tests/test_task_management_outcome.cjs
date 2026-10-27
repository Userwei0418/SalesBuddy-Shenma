const test=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const load=()=>import(pathToFileURL(path.resolve(__dirname,'../src/sales_backend/web/assets/task-management.js')).href);
test('admin uses ended and normal-completed server tabs without raw completed status',async()=>{
  const {managementFilters}=await load();
  for(const status of ['ended','completed']){
    const result=managementFilters({status});
    assert.equal(result.tab,status);assert.equal(result.status,undefined);
  }
  assert.equal(managementFilters({status:'cancelled'}).status,'cancelled');
});
test('only successful outcomes have completion styling in list and calendar',async()=>{
  const {taskOutcome}=await load();
  const base={status:'completed',execution_mode:'execution_v3'};
  assert.deepEqual(taskOutcome({...base,completion_action:'close'}),{label:'已停止',style:'cancelled'});
  assert.deepEqual(taskOutcome({...base,completion_action:'submit_completion'}),{label:'正常完成',style:'completed'});
  assert.equal(taskOutcome({...base,completion_action:'future'}).style,'cancelled');
  assert.equal(taskOutcome({...base,end_reason:'stopped',completion_action:'submit_completion'}).label,'已停止');
  assert.equal(taskOutcome({status:'cancelled',end_event_type:'reject',last_event_type:'comment'}).label,'历史拒绝');
});
