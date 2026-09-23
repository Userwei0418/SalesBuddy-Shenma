const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
let page;
const storage = new Map();
vm.runInNewContext(fs.readFileSync(__dirname+'/../miniprogram/pages/index/index.js','utf8'), {
  Page:p=>{page=p;},
  wx: { getStorageSync: key => storage.get(key), removeStorageSync: key => storage.delete(key) },
  require:n=>n.includes('visitCards')?require('../miniprogram/utils/visitCards'):n.includes('taskOverview')?require('../miniprogram/utils/taskOverview'):{},
});
test('完成任务只更新待办汇总，不把单条通知改成零计数卡片',()=>{
  const notification=page.buildRemoteNotificationMessage({id:'n',template_code:'task_assigned',object_id:'t',body:'保留通知正文',payload:{}},{scope:'仅本人'});
  const summary={id:'today_tasks_result_demo',kind:'data-card',card:{rows:[{taskId:'t',source:'management_task'},{taskId:'other',source:'visit_follow_up'}]}};
  const context={data:{messages:[notification,summary]},setData(update){Object.assign(this.data,update);}};
  storage.set('lastCompletedTaskId','t');
  page.consumeCompletedTask.call(context);
  assert.strictEqual(context.data.messages[0],notification);
  assert.equal(context.data.messages[0].card.rows[0].title,'保留通知正文');
  assert.equal(context.data.messages[1].card.rows.length,1);
  assert.equal(context.data.messages[1].card.rows[0].taskId,'other');
  assert.equal(context.data.messages[1].card.metrics[0].value,'1');
  assert.equal(storage.has('lastCompletedTaskId'),false);
});
test('真实任务通知结构可渲染截止时间、优先级和任务详情入口',()=>{
  const message=page.buildRemoteNotificationMessage({
    id:'notification',template_code:'task_assigned',title:'王新源向你下发新任务',body:'验收任务',object_id:'task',
    payload:{due_at:'2026-09-10T18:11:27.900450+00:00',priority_code:'high',creator_name:'王新源'},
  },{scope:'仅本人'});
  assert.match(message.card.rows[0].meta,/9月11日 02:11/);
  assert.equal(message.card.metrics[1].value,'高');
  assert.equal(message.card.action.taskId,'task');
  assert.equal(message.card.action.code,'open_task_detail');
});
test('接受、拒绝和完成反馈卡片保留真实反馈与目标任务',()=>{
  for(const [template,label] of [['task_accepted','已接受'],['task_rejected','已拒绝'],['task_completed','已完成']]) {
    const message=page.buildRemoteNotificationMessage({id:'n',template_code:template,object_id:'t',body:'任务反馈',payload:{comment:'演示资源冲突',completion_note:'已交付核对清单'}},{scope:'仅本人'});
    assert.equal(message.card.metrics[0].value,label);
    assert.equal(message.card.action.taskId,'t');
    if(template==='task_rejected') assert.match(message.card.rows[0].meta,/演示资源冲突/);
    if(template==='task_completed') assert.match(message.card.rows[0].meta,/已交付核对清单/);
  }
});
test('原始下发卡片按任务最新状态显示待接受、已接受或已拒绝',()=>{
  const original=page.buildRemoteNotificationMessage({id:'n',template_code:'task_assigned',object_id:'t',body:'验收任务',payload:{}},{scope:'仅本人'});
  const pending=page.reconcileTaskCard(original,[{id:'t',status:'pending_confirm'}]);
  assert.equal(pending.card.metrics[0].value,'待接受');
  assert.equal(pending.card.rows[0].tag,'待接受');
  const accepted=page.reconcileTaskCard(original,[{id:'t',status:'pending_execution'}]);
  assert.equal(accepted.card.metrics[0].value,'已接受');
  assert.equal(accepted.card.rows[0].tag,'已接受');
  const rejected=page.reconcileTaskCard(original,[{id:'t',status:'cancelled',attributes:{rejection_comment:'资源冲突'}}]);
  assert.equal(rejected.card.metrics[0].value,'已拒绝');
  assert.equal(rejected.card.rows[0].tag,'已拒绝');
  assert.match(rejected.card.rows[0].meta,/资源冲突/);
});
test('验收通知与旧卡片回读显示待确认和驳回，保留进入详情入口',()=>{
  for(const [template,label] of [['task_completion_submitted','待发起人确认'],['task_completion_rejected','执行中 · 已驳回']]){
    const message=page.buildRemoteNotificationMessage({id:'r',template_code:template,object_id:'t',body:'验收反馈',payload:{note:'交付或改进说明'}},{scope:'仅本人'});
    assert.equal(message.card.metrics[0].value,label);assert.equal(message.card.action.taskId,'t');assert.equal(message.card.rows[0].meta,'交付或改进说明');
  }
  const original=page.buildRemoteNotificationMessage({id:'n',template_code:'task_assigned',object_id:'t',payload:{}},{scope:'仅本人'});
  const pending=page.reconcileTaskCard(original,[{id:'t',status:'pending_review',completion_note:'已交付'}]);
  assert.equal(pending.card.metrics[0].value,'待发起人确认');
  const rejected=page.reconcileTaskCard(original,[{id:'t',status:'in_progress',last_event_type:'reject_completion',last_event_note:'补齐验证'}]);
  assert.equal(rejected.card.metrics[0].value,'执行中 · 已驳回');
});
