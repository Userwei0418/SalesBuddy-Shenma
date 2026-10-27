import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import Module from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {buildSync} from 'esbuild';
import {taskActivityView} from '../department-ui/work-report/task-activity.js';
import {taskActivityFixture,taskAsOf,taskCount} from './fixtures/work-report-task-activity.mjs';

function activity() {
  const old=taskActivityFixture();
  const fields={updated_at:taskAsOf,execution_mode:'execution_v3',processing_reason:null,processing_detail:null,latest_feedback:null,next_step:null,completion_result:null};
  const completed={...old.completed_tasks[0],...fields,completion_action:'review_completion',completion_result:'交付结果已验收通过'};
  const review={...old.new_tasks[0],...fields,status:'needs_adjustment',status_label:'需调整',processing_reason:'awaiting_review',latest_feedback:'交付说明已提交'};
  return {...old,contract_version:'work_report.task_activity.v2',
    counts:{completed_tasks:taskCount(1),progressing_tasks:taskCount(1)},completed_tasks:[completed],progressing_tasks:[review],
    members:old.members.map(member=>({...member,counts:{completed_tasks:taskCount(completed.member_ids.includes(member.member_id)?1:0),progressing_tasks:taskCount(1)},
      completed_task_ids:completed.member_ids.includes(member.member_id)?[completed.id]:[],progressing_task_ids:[review.id]}))};
}

test('R2 displays completion evidence and true progressing inventory, including pending review',()=>{
  const value=activity();
  const result=taskActivityView(value,{dataAsOf:taskAsOf});
  assert.deepEqual(result.counts.map(row=>row.id),['completed_tasks','progressing_tasks']);
  assert.equal(result.taskActivity.progressing[0].processingLabel,'待验收');
  assert.equal(result.taskActivity.completed[0].completionResult,'交付结果已验收通过');
  assert.equal(result.taskActivity.created,undefined);
  value.progressing_tasks[0].processing_reason='unrecognized';
  assert.throws(()=>taskActivityView(value,{dataAsOf:taskAsOf}),/响应不完整/);
});

test('new task rows retain the four states and never expose old cancellation or checklist actions',()=>{
  const context=vm.createContext({Intl});
  vm.runInContext(fs.readFileSync(new URL('../task-workspace.js',import.meta.url),'utf8'),context);
  const result=context.SalesTasks.presentation({route:'pages/tasks/index'},{filteredTasks:[{
    id:'test',title:'交付方案',status:'needs_adjustment',statusLabel:'需调整',processing_reason:'awaiting_review',
    execution_mode:'execution_v3',showProgress:true,canCancel:true,requires_action:true
  }],sortOptions:[],tabs:[{key:'needs_adjustment'}],needsAdjustmentCount:4},Date.now());
  assert.equal(result.webTaskRows[0].status,'needs_adjustment');
  assert.equal(result.webTaskRows[0].showProgress,false);
  assert.equal(result.webTaskRows[0].canCancel,false);
  assert.equal(result.webTaskRows[0].webAction,'查看验收');
  assert.equal(result.webTaskTabs.find(row=>row.key==='needs_adjustment').count,4);
});

test('Tasks renders feedback text and empty structured feedback without rendering API objects',()=>{
  const root=fileURLToPath(new URL('../',import.meta.url));
  const code=buildSync({absWorkingDir:root,entryPoints:['department-ui/Tasks.jsx'],bundle:true,write:false,platform:'node',format:'cjs',packages:'external',loader:{'.css':'empty'}}).outputFiles[0].text;
  const loaded=new Module(root+'task-feedback-test.cjs');loaded.filename=root+'task-feedback-test.cjs';loaded.paths=Module._nodeModulePaths(root);loaded._compile(code,loaded.filename);
  for(const [feedback,expected] of [
    [{latestFeedback:'已归一反馈',latest_feedback:{text:'原反馈'}},'已归一反馈'],
    [{latestFeedback:'',latest_feedback:{text:'结构化反馈'}},'结构化反馈'],
    [{latest_feedback:'历史文字反馈'},'历史文字反馈'],
    [{latestFeedback:'',latest_feedback:{text:''}},'暂无反馈'],
    [{latest_feedback:{text:{unexpected:true}}},'暂无反馈'],
    [{latest_feedback:null},'暂无反馈'],
  ]) {
    const props={page:{route:'pages/tasks/index'},data:{sortOptions:[],webTaskRows:[{id:'feedback-row',webTitle:'反馈验证',...feedback}],filteredTotal:1},invoke(){},invokeOn(){},select:()=>null};
    const html=renderToStaticMarkup(React.createElement(loaded.exports.default,props));
    assert.ok(html.includes(expected));assert.doesNotMatch(html,/\[object Object\]/);
  }
});
