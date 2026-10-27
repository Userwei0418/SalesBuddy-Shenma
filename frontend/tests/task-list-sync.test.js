const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = path.resolve(__dirname, '../miniprogram/pages/tasks/index.js');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
function task(id = 'task-1', version = 1, extra = {}) {
  return {id, title: id, status: 'in_progress', execution_mode: 'execution_v1', version_no: version,
    requirements_version: 1, responsibility_version: 1, can_cancel: true,
    executor: {user_id: 'executor', name: '执行人'}, current_reviewer: 'owner',
    progress: {requirements_version: 1, basis: 'checklist', completed: 2, total: 5, percent: 40},
    updated_at: '2026-10-17T12:00:00Z', ...extra};
}
function packet(rows, q = {}, extra = {}) {
  const offset = q.offset || 0, size = q.page_size || 20;
  return {items: rows.slice(offset, offset + size), summary: {total: rows.length,
    pending_count: rows.length, completed_count: 0, filtered_total: rows.length},
    has_more: offset + size < rows.length, next_offset: offset + size < rows.length ? offset + size : null, ...extra};
}
function harness(handler) {
  let page, timerId = 0, now = 0;
  const timers = new Map(), listeners = new Set(), calls = [], patches = [], navigations = [];
  const app = {ensureLogin: () => true, globalData: {session: {workspaceId: 'w', userId: 'owner', role: 'sales',
    permissionVersion: 1, permissions: {'task.read': true, 'task.cancel': true}}}};
  const clock = {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, at: now + delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    async tick(ms) { const end = now + ms; let budget = 100; while (budget--) {
      const next = [...timers].filter(([,t]) => t.at <= end).sort((a,b) => a[1].at - b[1].at)[0];
      if (!next) break; now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
    } assert.ok(budget > 0, 'timer loop must remain bounded'); now = end; await flush(); },
  };
  class ClockDate extends Date { constructor(...args) { super(...(args.length ? args : [1792238400000 + now])); } static now() { return 1792238400000 + now; } }
  const state = {bounds: []};
  const wx = {setNavigationBarTitle() {}, showToast() {}, navigateTo: v => navigations.push(v), switchTab() {}, setStorageSync() {},
    onNetworkStatusChange: fn => listeners.add(fn), offNetworkStatusChange: fn => listeners.delete(fn),
    createSelectorQuery() { let callback; const query = {in() {return query;}, selectAll() {return query;},
      boundingClientRect(fn) {callback = fn; return query;}, exec() {callback(state.bounds);}}; return query; },
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), {
    Page: value => {page = value;}, getApp: () => app, wx, Date: ClockDate,
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    require: name => name.endsWith('apiClient') ? {listTaskPage: q => {calls.push({...q}); return handler(q);}}
      : require(path.resolve(path.dirname(filename), name)),
  });
  page.data = JSON.parse(JSON.stringify(page.data));
  page.setData = patch => {patches.push(patch); Object.assign(page.data, patch);}; page.onLoad();
  return {page, app, calls, timers, listeners, clock, patches, state, navigations,
    network(connected) { for (const fn of [...listeners]) fn({isConnected: connected}); },
  };
}

test('foreground refreshes one saved server version within 3 seconds including progress, flags and owner', async () => {
  let rows = [task()]; const h = harness(async q => packet(rows, q));
  await h.page.onShow(); assert.equal(h.calls.length, 1); assert.equal(h.timers.size, 1);
  rows = [task('task-1', 2, {progress: {requirements_version: 1, basis: 'checklist', completed: 3, total: 5, percent: 60},
    executor: {user_id: 'new-owner', name: '新执行人'}, execution_flags: {paused: true}})];
  await h.clock.tick(2999); assert.equal(h.page.data.tasks[0].progressView.percent, 40);
  await h.clock.tick(1); const updated = h.page.data.tasks[0];
  assert.equal(updated.progressView.percent, 60); assert.equal(updated.version_no, 2);
  assert.equal(updated.statusLabel, '已暂停'); assert.equal(updated.owner, '新执行人');
  assert.equal(h.calls.length, 2); assert.equal(h.page.data.taskSyncState, 'connected');
  const patch = h.patches.find(p => p.tasks?.[0]?.version_no === 2);
  assert.equal(patch.tasks[0].progressView.percent, 60); assert.equal(patch.tasks[0].statusLabel, '已暂停');
  h.page.onUnload(); assert.equal(h.timers.size, 0); assert.equal(h.listeners.size, 0);
});

test('loaded pages and active filters remain while changed rows and authoritative counts refresh', async () => {
  let rows = Array.from({length: 45}, (_,i) => task(`t${i}`));
  const h = harness(async q => packet(rows, q)); await h.page.onShow(); await h.page.loadMore();
  rows = rows.filter(row => row.id !== 't1').map(row => row.id === 't25' ? task('t25', 3) : row);
  await h.clock.tick(3000);
  assert.equal(h.calls.at(-1).page_size, 40); assert.equal(h.calls.at(-1).offset, 0);
  assert.equal(h.page.data.tasks.length, 40); assert.equal(h.page.data.filteredTotal, 44);
  assert.equal(h.page.data.tasks.some(row => row.id === 't1'), false);
  assert.equal(h.page.data.tasks.find(row => row.id === 't25').version_no, 3);
  assert.equal(h.page.data.activeTab, 'all'); assert.equal(h.page.data.nextOffset, 40);
  h.page.onUnload();
});

test('long scrolled list uses one bounded visible-window query, keeps other loaded cards and never navigates', async () => {
  let rows = Array.from({length: 160}, (_,i) => task(`t${i}`));
  const h = harness(async q => packet(rows, q)); await h.page.onShow();
  for (let i = 0; i < 5; i++) await h.page.loadMore();
  h.state.bounds = rows.slice(0,120).map((_,i) => ({top: (i - 105) * 100, bottom: (i - 104) * 100}));
  rows = rows.map(row => row.id === 't105' ? task('t105', 4) : row);
  const before = h.calls.length; await h.clock.tick(3000);
  assert.equal(h.calls.length, before + 1); assert.equal(h.calls.at(-1).offset, 20);
  assert.equal(h.calls.at(-1).page_size, 100); assert.equal(h.page.data.tasks.length, 120);
  assert.equal(h.page.data.tasks[0].id, 't0'); assert.equal(h.page.data.tasks.find(row => row.id === 't105').version_no, 4);
  assert.equal(h.page.data.nextOffset, 120); assert.equal(h.navigations.length, 0); h.page.onUnload();
});

test('a 119-row list refreshes its visible final row instead of rounding the tail window down', async () => {
  let rows=Array.from({length:119},(_,i)=>task(`t${i}`));
  const h=harness(async q=>packet(rows,q)); await h.page.onShow();
  for(let i=0;i<5;i++)await h.page.loadMore();
  h.state.bounds=rows.map((_,i)=>({top:(i-115)*100,bottom:(i-114)*100}));
  rows=rows.map(row=>row.id==='t118'?task('t118',9):row);
  const before=h.calls.length; await h.clock.tick(3000);
  assert.equal(h.calls.length,before+1); assert.equal(h.calls.at(-1).offset,19);
  assert.equal(h.page.data.tasks.find(row=>row.id==='t118').version_no,9);
  assert.equal(h.page.data.tasks.length,119); assert.equal(h.page.data.taskSyncState,'connected');
  h.page.onUnload();
});

test('moving the last task to the first window repairs the boundary without permanently dropping a task', async () => {
  let rows=Array.from({length:120},(_,i)=>task(`t${i}`));
  const h=harness(async q=>packet(rows,q)); await h.page.onShow();
  for(let i=0;i<5;i++)await h.page.loadMore();
  h.state.bounds=rows.map((_,i)=>({top:i*100,bottom:(i+1)*100}));
  rows=[task('t119',9),...rows.slice(0,119)];
  const before=h.calls.length; await h.clock.tick(3000);
  assert.equal(h.calls.length,before+2); assert.equal(h.calls.at(-1).offset,100);
  assert.deepEqual(Array.from(h.page.data.tasks,row=>row.id),rows.map(row=>row.id));
  assert.equal(h.page.data.tasks.length,120); assert.equal(h.page.data.hasMore,false);
  assert.equal(h.page.data.taskSyncState,'connected');
  await h.clock.tick(9000); await h.page.loadMore();
  assert.equal(h.page.data.tasks.find(row=>row.id==='t99').id,'t99'); h.page.onUnload();
});

test('large reordered caches mark unverified windows and retain a bounded recoverable pagination path', async () => {
  let rows=Array.from({length:320},(_,i)=>task(`t${i}`));
  const h=harness(async q=>packet(rows,q)); await h.page.onShow();
  for(let i=0;i<15;i++)await h.page.loadMore();
  h.state.bounds=rows.map((_,i)=>({top:i*100,bottom:(i+1)*100}));
  rows=[task('t319',9),...rows.slice(0,319)];
  const before=h.calls.length; await h.clock.tick(3000);
  assert.equal(h.calls.length,before+2); assert.equal(h.page.data.taskSyncState,'partial');
  assert.equal(h.page.data.hasMore,true); assert.equal(h.page.data.nextOffset,200);
  assert.ok(h.page.data.tasks.some(row=>row.taskSyncStale));
  const repairStart=h.calls.length; await h.page.loadMore();
  assert.equal(h.calls.length,repairStart+2); assert.equal(h.page.data.taskSyncState,'connected');
  assert.equal(h.page.data.hasMore,false);
  assert.deepEqual(Array.from(h.page.data.tasks,row=>row.id),rows.map(row=>row.id));
  assert.equal(new Set(h.page.data.tasks.map(row=>row.id)).size,320); h.page.onUnload();
});

test('a changed total invalidates unseen cached positions even when the visible window IDs did not change', async () => {
  let rows=Array.from({length:120},(_,i)=>task(`t${i}`));
  const h=harness(async q=>packet(rows,q)); await h.page.onShow();
  for(let i=0;i<5;i++)await h.page.loadMore();
  h.state.bounds=rows.map((_,i)=>({top:i*100,bottom:(i+1)*100}));
  rows=rows.filter(row=>row.id!=='t110'); await h.clock.tick(3000);
  assert.deepEqual(Array.from(h.page.data.tasks,row=>row.id),rows.map(row=>row.id));
  assert.equal(h.page.data.tasks.length,119); assert.equal(h.page.data.hasMore,false);
  assert.equal(h.page.data.taskSyncState,'connected'); h.page.onUnload();
});

test('offline keeps last confirmed values and time, stops traffic, recovery immediately reads latest', async () => {
  let rows = [task()]; const h = harness(async q => packet(rows,q)); await h.page.onShow();
  const confirmed = h.page.data.taskLastSyncLabel; h.network(false); await h.clock.tick(60000);
  assert.equal(h.calls.length, 1); assert.equal(h.page.data.tasks[0].version_no, 1);
  assert.equal(h.page.data.taskLastSyncLabel, confirmed); assert.equal(h.page.data.taskSyncState, 'offline');
  rows = [task('task-1', 2)]; h.network(true); await flush();
  assert.equal(h.calls.length, 2); assert.equal(h.page.data.tasks[0].version_no, 2);
  assert.equal(h.page.data.taskSyncError, ''); assert.notEqual(h.page.data.taskLastSyncLabel, confirmed); h.page.onUnload();
});

test('a removed visible window rebases once and reads fresh remaining rows instead of cached values', async () => {
  let rows = Array.from({length: 160}, (_,i) => task(`t${i}`));
  const h = harness(async q => packet(rows, q)); await h.page.onShow();
  for (let i = 0; i < 5; i++) await h.page.loadMore();
  h.state.bounds = rows.slice(0,120).map((_,i) => ({top: (i - 105) * 100, bottom: (i - 104) * 100}));
  rows = Array.from({length: 5}, (_,i) => task(`remaining${i}`, 7));
  const before = h.calls.length; await h.clock.tick(3000);
  assert.equal(h.calls.length, before + 2); assert.equal(h.calls.at(-2).offset,20);
  assert.equal(h.calls.at(-1).offset,0); assert.equal(h.page.data.tasks.length,5);
  assert.ok(h.page.data.tasks.every(row=>row.id.startsWith('remaining')&&row.version_no===7));
  assert.equal(h.page.data.filteredTotal,5); assert.equal(h.page.data.hasMore,false);
  assert.equal(h.page.data.taskSyncState,'connected'); assert.equal(h.navigations.length,0); h.page.onUnload();
});

test('failed background read preserves data and backs off without overlapping or requests storm', async () => {
  let fail = false; const h = harness(async q => {if (fail) throw Error('offline'); return packet([task()], q);});
  await h.page.onShow(); const stamp = h.page.data.taskLastSyncLabel; fail = true;
  await h.clock.tick(3000); assert.equal(h.calls.length, 2);
  assert.equal(h.page.data.tasks.length, 1); assert.equal(h.page.data.taskLastSyncLabel, stamp);
  assert.equal(h.page.data.taskSyncState, 'disconnected'); assert.equal(h.page.data.loadError, '');
  await h.clock.tick(5999); assert.equal(h.calls.length, 2); await h.clock.tick(1); assert.equal(h.calls.length, 3);
  fail = false; await h.clock.tick(12000); assert.equal(h.page.data.taskSyncState, 'connected'); h.page.onUnload();
});

test('slow refresh is single flight and queued pagination still works after it completes', async () => {
  const slow = deferred(), rows = Array.from({length: 45}, (_,i) => task(`t${i}`)); let reads = 0;
  const h = harness(q => ++reads === 2 ? slow.promise : Promise.resolve(packet(rows,q)));
  await h.page.onShow(); await h.clock.tick(3000); const more = h.page.loadMore();
  const repeated=h.page.syncTaskList(); await h.clock.tick(30000); assert.equal(h.calls.length, 2);
  slow.resolve(packet(rows,{page_size:20})); await Promise.all([more,repeated]); await flush();
  assert.equal(h.calls.length, 3); assert.equal(h.calls[2].offset, 20); assert.equal(h.page.data.tasks.length, 40);
  assert.equal(h.timers.size, 1); h.page.onUnload();
});

test('filter change invalidates late sync result and its error without overwriting the new filter', async () => {
  const slow = deferred(); let reads = 0;
  const h = harness(q => ++reads === 2 ? slow.promise : Promise.resolve(packet([task(q.tab)],q)));
  await h.page.onShow(); await h.clock.tick(3000);
  await h.page.selectTab({currentTarget:{dataset:{key:'completed'}}});
  slow.reject(Error('old request')); await flush();
  assert.equal(h.page.data.activeTab, 'completed'); assert.equal(h.page.data.tasks[0].id, 'completed');
  assert.equal(h.page.data.taskSyncError, ''); assert.equal(h.timers.size, 1); h.page.onUnload();
});

test('pagination queued behind a refresh is cancelled when the user changes filters', async () => {
  const slow=deferred(); let reads=0;
  const h=harness(q=>++reads===2?slow.promise:Promise.resolve(packet(Array.from({length:45},(_,i)=>task(`${q.tab}-${i}`)),q)));
  await h.page.onShow(); await h.clock.tick(3000); const more=h.page.loadMore();
  await h.page.selectTab({currentTarget:{dataset:{key:'completed'}}});
  slow.resolve(packet([task('old')])); await more; await flush();
  assert.equal(h.calls.length,3); assert.equal(h.page.data.tasks.length,20);
  assert.equal(h.page.data.tasks[0].id,'completed-0'); assert.equal(h.page.data.nextOffset,20);
  h.page.onUnload();
});

test('hide stops timers and listeners, drops late responses, and show reads immediately', async () => {
  const slow = deferred(); let reads = 0;
  const h = harness(q => ++reads === 2 ? slow.promise : Promise.resolve(packet([task('task-1',reads)],q)));
  await h.page.onShow(); await h.clock.tick(3000); h.page.onHide();
  assert.equal(h.timers.size, 0); assert.equal(h.listeners.size, 0);
  slow.resolve(packet([task('task-1',99)])); await flush(); assert.equal(h.page.data.tasks[0].version_no,1);
  await h.clock.tick(30000); assert.equal(h.calls.length,2); await h.page.onShow();
  assert.equal(h.page.data.tasks[0].version_no,3); assert.equal(h.listeners.size,1); h.page.onUnload();
});

for (const change of ['account','permission','logout','denied-response']) test(`session invalidation clears old data and stops polling: ${change}`, async () => {
  let denied=false; const h=harness(async q=>{if(denied)throw Object.assign(Error('forbidden'),{statusCode:403});return packet([task()],q);});
  await h.page.onShow(); h.page.openCancellation({currentTarget:{dataset:{id:'task-1'}}});
  if(change==='account')h.app.globalData.session.userId='other';
  if(change==='permission')h.app.globalData.session.permissionVersion=2;
  if(change==='logout')h.app.globalData.session=null;
  if(change==='denied-response')denied=true;
  await h.clock.tick(3000); assert.equal(h.page.data.tasks.length,0); assert.equal(h.page.data.cancelOpen,false);
  assert.equal(h.page.data.taskLastSyncLabel,''); assert.equal(h.timers.size,0); assert.equal(h.listeners.size,0);
});

test('automatic sync does not close or replace a cancellation draft/version', async () => {
  let rows=[task()]; const h=harness(async q=>packet(rows,q)); await h.page.onShow();
  h.page.openCancellation({currentTarget:{dataset:{id:'task-1'}}}); rows=[task('task-1',2,{can_cancel:false})];
  await h.clock.tick(3000); assert.equal(h.page.data.cancelOpen,true); assert.equal(h.page.data.cancelTaskVersion,1);
  assert.equal(h.page.data.tasks[0].version_no,2); assert.equal(h.page.data.tasks[0].canCancel,false); h.page.onUnload();
});

test('malformed sync response preserves confirmed list instead of partially committing it', async () => {
  let invalid=false; const h=harness(async q=>invalid?{...packet([task('wrong')],q),summary:null}:packet([task()],q));
  await h.page.onShow(); invalid=true; await h.clock.tick(3000);
  assert.equal(h.page.data.tasks[0].id,'task-1'); assert.equal(h.page.data.taskSyncState,'disconnected'); h.page.onUnload();
});

test('Web adapter keeps existing onShow reads and does not start Mini polling', async () => {
  const h=harness(async q=>packet([task()],q)); h.page.taskManagementParams=()=>({scope:'mine'});
  await h.page.onShow(); await h.page.onShow(); assert.equal(h.calls.length,2); assert.equal(h.timers.size,0);
  assert.equal(h.listeners.size,0); assert.equal(h.page.data.taskSyncState,''); h.page.onUnload();
});

test('Mini template displays only server progress and last confirmed synchronization', () => {
  const source=fs.readFileSync(filename.replace('.js','.wxml'),'utf8');
  assert.match(source,/item\.progressView\.percent/); assert.match(source,/taskLastSyncLabel/);
  assert.match(source,/bindtap="syncTaskList"/); assert.match(source,/item\.updatedLabel/);
});
