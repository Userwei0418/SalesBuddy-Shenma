const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const filename = path.resolve(__dirname, '../miniprogram/pages/task-detail/index.js');
const taskId = '11111111-1111-1111-1111-111111111111';
const tick = () => new Promise(resolve => setImmediate(resolve));
const permissions = ['task.accept', 'task.decline', 'task.complete', 'task.review', 'task.coordinate'];
const cases = [
  { name: '接受', permission: 'task.accept', status: 'pending_confirm', invoke: page => page.acceptTask() },
  { name: '拒绝', permission: 'task.decline', status: 'pending_confirm', invoke: page => page.rejectTask() },
  { name: '提交完成', permission: 'task.complete', status: 'pending_execution', invoke: page => page.markCompleted() },
  { name: '验收完成', permission: 'task.review', status: 'pending_review', invoke: page => page.reviewCompletion({ currentTarget: { dataset: { event: 'approve_completion' } } }) },
  { name: '驳回完成', permission: 'task.review', status: 'pending_review', invoke: page => page.reviewCompletion({ currentTarget: { dataset: { event: 'reject_completion' } } }) },
];

async function harness(spec = cases[0], options = {}) {
  let page;
  const calls = [], reads = [], modals = [], toasts = [], timers = [], storage = [], navigations = [];
  const session = { userId: 'owner', workspaceId: 'company', permissionVersion: 'p1',
    permissions: Object.fromEntries(permissions.map(permission => [permission, true])) };
  const task = { id: taskId, version_no: 3, status: spec.status, creator_user_ref_id: 'owner',
    creator_name: '发起人', assignees: [{ user_id: 'owner', name: '当前负责人', responsibility: 'owner' }],
    action_permissions: { ...session.permissions }, events: [], ...options.task };
  const response = { ...task, version_no: 4, status: 'completed' };
  const api = { getTask: async id => { reads.push(id); return options.read ? options.read() : { ...task }; },
    getTaskReassignmentOptions:async()=>({items:[{id:'next',name:'新接收人',account_code:'NEXT',team_ids:['north']}],teams:[{id:'north',name:'北区'}],defaults:{team_id:'north'}}),
    coordinateTask:(...args)=>{calls.push(args);return options.submit?options.submit(...args):Promise.resolve(response);},
    respondTask: (...args) => { calls.push(args); return options.submit ? options.submit(...args) : Promise.resolve(response); },
    completeTask: (...args) => { calls.push(args); return options.submit ? options.submit(...args) : Promise.resolve(response); } };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => page = value, getApp: () => ({ ensureLogin:()=>true, globalData: { session } }),
    require: name => name.endsWith('apiClient') ? api : require(path.resolve(path.dirname(filename), name)),
    wx: { showModal: value => modals.push(value), showToast: value => toasts.push(value),
      setStorageSync: (...args) => storage.push(args), vibrateShort() {}, navigateBack: () => navigations.push('back') },
    setTimeout: callback => timers.push(callback),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = patch => Object.assign(page.data, patch);
  page.setData({ taskId, responseComment: '无法按期完成', completionNote: '完成说明', reviewNote: '补充验证' });
  await page.loadTask();
  return { page, session, task, response, calls, reads, modals, toasts, timers, storage, navigations };
}

test('待交接覆盖底层待接受文案，保留无交接任务的原提示和能力', async () => {
  const blocked = await harness(cases[0], { task: { handover_required: true } });
  assert.equal(blocked.page.data.task.statusLabel, '待交接');
  assert.equal(blocked.page.data.task.stageLabel, '接收资格已变化，等待协调处理');
  assert.equal(blocked.page.data.task.canAccept, false);
  assert.equal(blocked.page.data.task.canDecline, false);
  const normal = await harness();
  assert.equal(normal.page.data.task.stageLabel, '等待接收方接受或拒绝');
  assert.equal(normal.page.data.task.canAccept, true);
  const markup = fs.readFileSync(filename.replace('.js', '.wxml'), 'utf8');
  assert.match(markup, /progressLabel="\{\{task\.stageLabel\}\}"/);
});

for (const spec of cases) {
  test(`${spec.name}重复点击仅一个确认，重复回调仅一次写入并保留版本`, async () => {
    const h = await harness(spec);
    spec.invoke(h.page); spec.invoke(h.page);
    assert.equal(h.modals.length, 1);
    h.modals[0].success({ confirm: true }); h.modals[0].success({ confirm: true });
    await tick();
    assert.equal(h.calls.length, 1); assert.equal(h.calls[0][0], taskId);
    assert.equal(h.calls[0].at(-1), 3); assert.equal(h.page.data.task.version_no, 4);
    assert.equal(h.page.data.submitting, false);
  });

  test(`${spec.name}确认前的离页、账号、公司、版本和权限变更均不能发旧请求`, async () => {
    for (const invalidate of [h => h.page.onHide(), h => h.page.onUnload(), h => h.session.userId = 'other',
      h => h.session.workspaceId = 'other-company', h => h.session.permissionVersion = 'p2',
      h => h.page.data.task.version_no = 8, h => h.page.data.task.id = 'other-task',
      h => h.session.permissions[spec.permission] = false, h => h.page.data.task.action_permissions[spec.permission] = false]) {
      const h = await harness(spec); spec.invoke(h.page); invalidate(h);
      h.modals[0].success({ confirm: true }); await tick();
      assert.equal(h.calls.length, 0); assert.equal(h.toasts.length, 0);
    }
  });

  test(`${spec.name}写入进行中旧回执不能覆盖新的页面或权限状态`, async () => {
    for (const invalidate of [h => h.page.onHide(), h => h.session.userId = 'other',
      h => h.page.data.task.version_no = 8, h => h.page.data.task.id = 'other-task',
      h => h.session.permissions[spec.permission] = false]) {
      let finish;
      const h = await harness(spec, { submit: () => new Promise(resolve => finish = resolve) });
      spec.invoke(h.page); h.modals[0].success({ confirm: true }); await tick();
      assert.equal(h.calls.length, 1); invalidate(h); finish(h.response); await tick();
      assert.equal(h.page.data.task.status, spec.status); assert.equal(h.toasts.length, 0);
      assert.equal(h.storage.length, 0); assert.equal(h.timers.length, 0);
      assert.equal(h.page.data.submitting, false);
    }
  });
}

test('页面重新加载使未确认弹窗失效，新弹窗不受旧回调影响', async () => {
  const h = await harness(); h.page.acceptTask(); const old = h.modals[0];
  await h.page.loadTask(); h.page.acceptTask(); assert.equal(h.modals.length, 2);
  old.success({ confirm: true }); h.modals[1].success({ confirm: true }); await tick();
  assert.equal(h.calls.length, 1); assert.equal(h.page.data.task.version_no, 4);
});

test('确认完成后立即离页或切账号不会迟到返回其他页面', async () => {
  for (const invalidate of [h => h.page.onHide(), h => h.session.userId = 'other', h => h.page.data.task.version_no = 8]) {
    const h = await harness(cases[2]); h.page.markCompleted(); h.modals[0].success({ confirm: true }); await tick();
    assert.equal(h.storage.length, 1); assert.equal(h.timers.length, 1);
    invalidate(h); h.timers[0](); assert.equal(h.navigations.length, 0);
  }
  const normal = await harness(cases[2]); normal.page.markCompleted(); normal.modals[0].success({ confirm: true }); await tick();
  normal.timers[0](); assert.equal(normal.navigations.length, 1);
});

test('服务失败后允许重新确认，旧请求失败不会污染新页面', async () => {
  const h = await harness(cases[0], { submit: async () => { throw Error('任务已被更新，请刷新'); } });
  h.page.acceptTask(); h.modals[0].success({ confirm: true }); await tick();
  assert.equal(h.page.data.submitting, false); assert.match(h.toasts[0].title, /请刷新/);
  h.page.acceptTask(); assert.equal(h.modals.length, 2);
  let reject;
  const late = await harness(cases[0], { submit: () => new Promise((resolve, fail) => reject = fail) });
  late.page.acceptTask(); late.modals[0].success({ confirm: true }); await tick();
  late.page.onHide(); reject(Error('旧错误')); await tick(); assert.equal(late.toasts.length, 0);
});

test('回执任务ID错误不覆盖详情，确认框关闭后允许再次操作', async () => {
  const h = await harness(cases[0], { submit: async () => ({ id: 'wrong-task', version_no: 4, status: 'completed' }) });
  h.page.acceptTask(); h.modals[0].success({ confirm: true }); await tick();
  assert.equal(h.page.data.task.id, taskId); assert.equal(h.page.data.task.status, 'pending_confirm');
  assert.match(h.toasts[0].title, /回执不匹配/); assert.equal(h.page.data.submitting, false);
  h.page.acceptTask(); h.modals[1].fail(); h.page.acceptTask(); assert.equal(h.modals.length, 3);
});

test('转交历史展示当时的原接收人和新接收人，不按现在的负责人倒推', async () => {
  const h = await harness(cases[0], { task: { events: [
    { id: 'new', event_type: 'reassign', actor_name: '更名后的协调人', note: '请继续跟进', payload: { actor_name:'当时的协调人', previous_owners: [{ user_id: 'old', name: '原接收人' }], new_owner: { user_id: 'new', display_name: '新接收人' } } },
    { id: 'legacy', event_type: 'reassign', actor_name: '协调人', note: '历史交接', payload: { previous_owners: [{ user_id: 'old' }], new_owner: { user_id: 'owner' } } },
  ] } });
  assert.equal(h.page.data.task.history[0].transferSummary, '原接收人 → 新接收人');
  assert.equal(h.page.data.task.history[0].actor_name, '当时的协调人');
  assert.equal(h.page.data.task.history[1].transferSummary, '');
  assert.equal(h.page.data.task.history[1].note, '历史交接'); assert.equal(h.page.data.task.owner, '当前负责人');
  assert.equal(h.page.data.task.history[1].actor_name, '协调人');
});

for (const spec of [cases[0],cases[2],cases[3]]) for (const uncertain of [false,true]) {
  test(`${spec.name}切后台返回读到旧版本后，旧写入${uncertain?'网络结果未知':'成功'}仅回读一次权威状态`,async()=>{
    let settle,committed=false,latest;
    const h=await harness(spec,{submit:()=>new Promise((resolve,reject)=>settle=()=>uncertain?reject(Error('连接中断')):resolve({...latest,creator_name:'不应直接采用旧回执'})),
      read:()=>committed?latest:{id:taskId,version_no:3,status:spec.status,creator_user_ref_id:'owner',creator_name:'原始读取',assignees:[{user_id:'owner',responsibility:'owner'}],action_permissions:Object.fromEntries(permissions.map(p=>[p,true]))}});
    latest={...h.task,version_no:4,status:spec===cases[0]?'pending_execution':'completed',creator_name:'权威读取'};
    spec.invoke(h.page);h.modals[0].success({confirm:true});await tick();assert.equal(h.calls.length,1);
    h.page.onHide();h.page.onShow();await tick();
    assert.equal(h.page.data.task.version_no,3);assert.equal(h.page.data.submitting,true);
    spec.invoke(h.page);assert.equal(h.modals.length,1);
    committed=true;settle();await tick();
    assert.equal(h.reads.length,3);assert.equal(h.page.data.task.version_no,4);
    assert.equal(h.page.data.task.creator,'权威读取');assert.equal(h.page.data.submitting,false);
    assert.equal(h.toasts.length,0);assert.equal(h.storage.length,0);assert.equal(h.timers.length,0);
  });
}

test('旧写入结束时隐藏、销毁、其他账号或其他任务不发协调回读',async()=>{
  for(const invalidate of [h=>h.page.onHide(),h=>h.page.onUnload(),h=>h.session.userId='other',
    h=>h.session.workspaceId='other-company',h=>h.page.setData({taskId:'22222222-2222-2222-2222-222222222222',task:{id:'22222222-2222-2222-2222-222222222222'}})]){
    let settle;const h=await harness(cases[0],{submit:()=>new Promise(resolve=>settle=resolve)});
    h.page.acceptTask();h.modals[0].success({confirm:true});await tick();invalidate(h);settle(h.response);await tick();
    assert.equal(h.reads.length,1);assert.equal(h.toasts.length,0);
  }
});

test('旧写结束后权威读取优先于回前台尚未返回的旧读取',async()=>{
  let finishWrite,finishStale,reads=0,latest;
  const base={id:taskId,version_no:3,status:'pending_confirm',creator_user_ref_id:'owner',assignees:[{user_id:'owner',responsibility:'owner'}],action_permissions:Object.fromEntries(permissions.map(p=>[p,true]))};
  const h=await harness(cases[0],{read:()=>++reads===2?new Promise(resolve=>finishStale=resolve):reads>2?latest:base,
    submit:()=>new Promise(resolve=>finishWrite=resolve)});
  latest={...base,version_no:4,status:'pending_execution'};
  h.page.acceptTask();h.modals[0].success({confirm:true});await tick();
  h.page.onHide();h.page.onShow();await tick();assert.equal(h.page.data.loading,true);
  finishWrite(latest);await tick();assert.equal(h.page.data.task.version_no,4);
  finishStale(base);await tick();assert.equal(h.page.data.task.version_no,4);assert.equal(h.reads.length,3);
});

test('未知网络结果后即使未切后台也回读，已提交的结果不要求重复操作',async()=>{
  let committed=false;
  const base={id:taskId,version_no:3,status:'pending_confirm',creator_user_ref_id:'owner',assignees:[{user_id:'owner',responsibility:'owner'}],action_permissions:Object.fromEntries(permissions.map(p=>[p,true]))};
  const h=await harness(cases[0],{read:()=>committed?{...base,version_no:4,status:'pending_execution'}:base,
    submit:async()=>{committed=true;throw Error('连接中断');}});
  h.page.acceptTask();h.modals[0].success({confirm:true});await tick();
  assert.equal(h.reads.length,2);assert.equal(h.page.data.task.status,'pending_execution');assert.equal(h.page.data.task.canAccept,false);
});

for(const uncertain of [false,true])test(`转交在后台完成且${uncertain?'网络结果未知':'回执成功'}，返回后通过权威GET显示新负责人`,async()=>{
  let settle,committed=false;
  const base={id:taskId,version_no:3,status:'pending_execution',can_coordinate:true,creator_user_ref_id:'owner',
    assignees:[{user_id:'owner',name:'原接收人',responsibility:'owner'}],action_permissions:Object.fromEntries(permissions.map(p=>[p,true]))};
  const latest={...base,version_no:4,status:'pending_confirm',assignees:[{user_id:'next',name:'权威新接收人',responsibility:'owner'}]};
  const h=await harness(cases[2],{read:()=>committed?latest:base,
    submit:()=>new Promise((resolve,reject)=>settle=()=>uncertain?reject(Error('连接中断')):resolve({...latest,owner_name:'旧回执姓名'}))});
  await h.page.openCoordination();h.page.confirmCoordinateMember({detail:{ids:['next']}});
  h.page.coordinateNote({detail:{value:'正常交接'}});h.page.coordinate({currentTarget:{dataset:{event:'reassign'}}});
  h.modals[0].success({confirm:true});await tick();assert.equal(h.calls.length,1);
  h.page.onHide();h.page.onShow();await tick();assert.equal(h.page.data.task.owner,'原接收人');assert.equal(h.page.data.submitting,true);
  committed=true;settle();await tick();
  assert.equal(h.reads.length,3);assert.equal(h.page.data.task.owner,'权威新接收人');assert.equal(h.page.data.task.version_no,4);
  assert.equal(h.page.data.submitting,false);assert.equal(h.page.data.task.canComplete,false);assert.equal(h.toasts.length,0);
});

test('历史操作人快照为空或类型错误时回退兼容旧记录',async()=>{
  for(const actor_name of ['', '  ', null, 3, {}]){
    const h=await harness(cases[0],{task:{events:[{id:'old',event_type:'reassign',actor_name:'旧记录操作人',payload:{actor_name}}]}});
    assert.equal(h.page.data.task.history[0].actor_name,'旧记录操作人');
  }
});
