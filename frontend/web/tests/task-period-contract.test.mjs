import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const workspaceSource = fs.readFileSync(new URL('../task-workspace.js', import.meta.url), 'utf8');
const tasksSource = fs.readFileSync(new URL('../department-ui/Tasks.jsx', import.meta.url), 'utf8');

function fixture() {
  const calls = [];
  const context = {Intl, Date, Object, Set, Math};
  context.globalThis = context;
  context.SalesRuntime = {
    replacePageQuery: () => true,
    app:{globalData:{session:{userId:'actor'}}},require:()=>({identity:s=>s.userId}),
    wx: {setNavigationBarTitle: () => {}},
  };
  vm.runInNewContext(workspaceSource, context, {filename: 'task-workspace.js'});
  const page = {
    route: 'pages/tasks/index',
    data: {sortOptions: [{key: 'due_asc', label: '旧'}], activeTab: 'pending', overviewFilter: '', taskPeriod: 'all'},
    setData(patch) { Object.assign(this.data, patch); },
    onLoad(options) { this.setData({overviewFilter: options?.overview || ''}); },
    taskParams() { return {tab: this.data.activeTab, overview: this.data.overviewFilter || undefined, completed_year: 2025, completed_quarters: [1]}; },
    selectTab(event) { this.setData({activeTab: event.currentTarget.dataset.key}); },
    loadTasks() { calls.push(this.taskManagementParams ? this.taskManagementParams() : this.taskParams()); return 'loaded'; },
  };
  context.SalesTasks.configure(page);
  return {page, calls, periods: context.SalesTasks.PERIOD_OPTIONS, tasks: context.SalesTasks};
}

test('task period contract exposes five choices and maps supported API filters', () => {
  const {page, calls, periods} = fixture();
  assert.deepEqual(Array.from(periods, item => item.value), ['all', 'day', 'week', 'month', 'quarter']);
  page.onLoad({period: 'day'});
  assert.equal(page.data.taskPeriod, 'day');
  page.changeTaskPeriod({detail: {value: 'day'}});
  assert.equal(calls.at(-1).date_from, calls.at(-1).date_to);
  assert.equal('overview' in calls.at(-1), false);
  page.selectTab({currentTarget: {dataset: {key: 'completed'}}});
  page.changeTaskPeriod({detail: {value: 'day'}});
  assert.equal(calls.at(-1).tab, 'completed');
  assert.equal('completed_quarters' in calls.at(-1), false);
  page.changeTaskPeriod({detail: {value: 'quarter'}});
  assert.equal(Number.isInteger(Number(calls.at(-1).date_from.slice(0, 4))), true);
  assert.equal(calls.at(-1).date_from < calls.at(-1).date_to, true);
});

test('management task periods map to Beijing date ranges instead of guessed legacy filters', () => {
  const {page, calls} = fixture();
  page.selectTab({currentTarget: {dataset: {key: 'pending'}}});
  page.changeTaskPeriod({detail: {value: 'week'}});
  assert.equal(page.data.taskPeriodUnsupported, false);
  assert.match(calls.at(-1).date_from, /^\d{4}-\d{2}-\d{2}$/);
  assert.match(calls.at(-1).date_to, /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(calls.at(-1).scope, 'mine');
  assert.equal('overview' in calls.at(-1), false);
  assert.equal('completed_year' in calls.at(-1), false);
  assert.equal('completed_quarters' in calls.at(-1), false);
});

test('task page presents the four requested KPI labels and an unsupported-data notice', () => {
  for (const label of ['未完成任务', '正常完成', '已逾期', '涉及执行人', '接口未返回']) assert.match(tasksSource, new RegExp(label));
  assert.match(tasksSource, /changeTaskPeriod/);
});

test('task KPI presentation consumes server counts and leaves unsupported fields empty', () => {
  const {tasks} = fixture();
  const result = tasks.presentation({route: 'pages/tasks/index'}, {
    filteredTasks: [], sortOptions: [{key: 'due_asc'}], sortIndex: 0, activeTab: 'pending', overviewFilter: '', tabs: [],
    pendingCount: 7, completedCount: 2, overdueCount: 3, teamMemberCount: 4, totalCount: 9, loading: false, loadError: '',
  });
  assert.equal(result.webTaskKpis.pending, 7);
  assert.equal(result.webTaskKpis.completed, 2);
  assert.equal(result.webTaskKpis.overdue, 3);
  assert.equal(result.webTaskKpis.teamMembers, 4);
});

test('ended uses the unified server tab and unknown totals stay unavailable',()=>{
 const {page,tasks}=fixture();page.data.activeTab='ended';
 const query=page.taskManagementParams();assert.equal(query.tab,'ended');assert.equal(query.status,undefined);
 page.data.activeTab='completed';assert.equal(page.taskManagementParams().tab,'completed');assert.equal(page.taskManagementParams().status,undefined);
 const value=tasks.presentation({route:'pages/tasks/index'},{filteredTasks:[],sortOptions:[],totalCount:100,pendingCount:30,completedCount:null,endedCount:null});
 assert.equal(value.webTaskKpis.completed,null);assert.equal(value.webTaskTabs.find(row=>row.key==='ended').count,'—');
 assert.equal(value.webTaskTabs.some(row=>row.key==='rejected'),false);
});
