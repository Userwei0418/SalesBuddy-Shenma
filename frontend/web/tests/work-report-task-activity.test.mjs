import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {taskActivityView,withTaskActivity} from '../department-ui/work-report/task-activity.js';
import {taskActivityFixture,taskAsOf,taskId,taskCount} from './fixtures/work-report-task-activity.mjs';

test('task activity uses server counts and member references without adding co-owners or filtering completed tasks by creation/current state',()=>{
  const activity=taskActivityFixture(),before=JSON.stringify(activity);
  const team=taskActivityView(activity,{dataAsOf:taskAsOf}),person=taskActivityView(activity,{memberId:taskId(2),dataAsOf:taskAsOf});
  assert.deepEqual(team.counts.map(row=>row.value),[2,2]);
  assert.deepEqual(person.counts.map(row=>row.value),[1,1]);
  assert.equal(team.taskActivity.created[0].ownerLabel,'成员甲、成员乙');
  assert.deepEqual(person.taskActivity.created.map(row=>row.taskId),[taskId(10)]);
  assert.equal(person.taskActivity.completed[0].statusLabel,'执行中');
  assert.match(person.taskActivity.completed[0].createdLabel,/2026\/10\/08/);
  assert.match(person.taskActivity.completed[0].completedLabel,/2026\/10\/12/);
  assert.equal(JSON.stringify(activity),before);
});

test('old response, missing member statistics, partial coverage and confirmed zero stay distinct',()=>{
  assert.deepEqual(taskActivityView(null).counts.map(row=>row.value),[null,null]);
  const activity=taskActivityFixture();activity.members[0].counts=undefined;
  const missing=taskActivityView(activity,{memberId:taskId(1),dataAsOf:taskAsOf});
  assert.deepEqual(missing.counts.map(row=>row.value),[null,null]);assert.equal(missing.taskActivity.created.length,2);
  activity.counts.new_tasks={value:null,known_count:2,coverage:'partial',missing_reasons:['历史归属未完整取得']};
  activity.counts.completed_tasks=taskCount(0);activity.completed_tasks=[];activity.members.forEach(row=>row.completed_task_ids=[]);
  const view=taskActivityView(activity,{dataAsOf:taskAsOf});assert.equal(view.counts[0].value,null);assert.match(view.counts[0].note,/已确认 2/);assert.equal(view.counts[1].value,0);
  assert.deepEqual(taskActivityView(activity,{memberId:taskId(99),dataAsOf:taskAsOf}).taskActivity,{created:[],completed:[]});
});

test('mismatched observation time, duplicate task IDs, missing sources and wrong member links are rejected',()=>{
  for(const change of [value=>value.data_as_of='2026-10-11T11:00:00+08:00',value=>value.new_tasks.push(value.new_tasks[0]),
    value=>value.new_tasks[0].source_refs=['unknown'],value=>value.members[1].new_task_ids=[taskId(11)],
    value=>value.members[0].new_task_ids.push(taskId(99)),value=>value.new_tasks[0].task_id=taskId(99)]) {
    const activity=taskActivityFixture();change(activity);assert.throws(()=>taskActivityView(activity,{dataAsOf:taskAsOf}),/任务统计响应不完整/);
  }
});

test('task decoration adds presentation only and never mutates the original report facts or counters',()=>{
  const original={counts:[{id:'customers',value:3}],notices:[],definitions:['原统计口径'],amounts:[{closing:'123.00'}]},before=JSON.stringify(original);
  const view=withTaskActivity(original,taskActivityFixture(),{dataAsOf:taskAsOf});
  assert.equal(JSON.stringify(original),before);assert.equal(view.counts.length,3);assert.equal(view.amounts,original.amounts);
  assert.equal(view.taskActivity.completed[0].taskId,taskId(11));
});

test('backend contract fixtures preserve complete, partial, zero and legacy states for team and members',()=>{
  // Copied unchanged from Backend's fixed synthetic contract fixture, 2026-10-12.
  const fixtures=JSON.parse(fs.readFileSync(new URL('./fixtures/work-report-task-activity.response.json',import.meta.url),'utf8'));
  for(const key of ['available','partial','zero','legacy_missing','legacy_null']) {
    const activity=fixtures[key].task_activity,team=taskActivityView(activity,{dataAsOf:activity?.data_as_of});
    assert.deepEqual(team.counts.map(row=>row.value),key==='available'?[2,2]:key==='partial'?[2,null]:key==='zero'?[0,0]:[null,null]);
    for(const member of activity?.members || []) {
      const shown=taskActivityView(activity,{dataAsOf:activity.data_as_of,memberId:member.member_id});
      assert.deepEqual(shown.counts.map(row=>row.value),[member.counts.new_tasks.value,member.counts.completed_tasks.value]);
      assert.deepEqual(shown.taskActivity.completed.map(row=>row.taskId),member.completed_task_ids);
    }
  }
  const available=taskActivityView(fixtures.available.task_activity,{dataAsOf:fixtures.available.task_activity.data_as_of});
  assert.ok(available.taskActivity.created.some(row=>row.statusLabel==='待发起人确认'));
  assert.ok(!available.taskActivity.completed.some(row=>row.statusLabel==='待发起人确认'));
});
