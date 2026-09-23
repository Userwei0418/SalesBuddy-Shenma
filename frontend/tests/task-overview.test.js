const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { selectOverviewTasks, buildOverviewMetrics, sortTasks, TASK_SORT_OPTIONS } = require("../miniprogram/utils/taskOverview");

const ids = (tasks) => Array.from(tasks, (task) => task.id);

test("today follows the Beijing midnight boundary for due and completion timestamps", () => {
  const now = new Date("2026-09-09T05:00:00Z");
  const timestamps = [
    ["before", "2026-09-08T15:59:59.999Z"],
    ["start", "2026-09-08T16:00:00.000Z"],
    ["end", "2026-09-09T15:59:59.999Z"],
    ["after", "2026-09-09T16:00:00.000Z"],
    ["explicit_china", "2026-09-09T00:00:00+08:00"],
    ["invalid", "not-a-date"],
  ];
  const tasks = timestamps.flatMap(([id, timestamp]) => [
    { id: `pending_${id}`, status: "pending", due_at: timestamp },
    { id: `completed_${id}`, status: "completed", completed_at: timestamp },
  ]);

  assert.deepEqual(ids(selectOverviewTasks(tasks, "today_pending", now)), ["pending_start", "pending_end", "pending_explicit_china"]);
  assert.deepEqual(ids(selectOverviewTasks(tasks, "today_completed", now)), ["completed_start", "completed_end", "completed_explicit_china"]);
});

test("cancelled tasks never enter pending counts and every count matches its drill-down", () => {
  const now = new Date("2026-09-09T05:00:00Z");
  const tasks = [
    { id: "today", status: "pending_confirm", due_at: "2026-09-09T08:00:00Z" },
    { id: "later", status: "in_progress", due_at: "2026-09-10T08:00:00Z" },
    { id: "unscheduled", status: "pending" },
    { id: "cancelled", status: "cancelled", due_at: "2026-09-09T08:00:00Z", completed_at: "2026-09-09T08:00:00Z" },
    { id: "done_today", status: "completed", due_at: "2026-09-10T08:00:00Z", completed_at: "2026-09-09T08:00:00Z" },
    { id: "done_before", status: "completed", due_at: "2026-09-09T08:00:00Z", completed_at: "2026-09-08T08:00:00Z" },
    { id: "done_without_time", status: "completed", due_at: "2026-09-09T08:00:00Z" },
  ];
  const metrics = buildOverviewMetrics(tasks, now);

  assert.deepEqual(metrics, [
    { key: "today_completed", label: "今日已完成", value: 1 },
    { key: "today_pending", label: "今日待办", value: 1 },
    { key: "all_pending", label: "全部待办", value: 3 },
  ]);
  for (const metric of metrics) {
    assert.equal(metric.value, selectOverviewTasks(tasks, metric.key, now).length);
  }
  const allPending = new Set(ids(selectOverviewTasks(tasks, "all_pending", now)));
  assert.ok(selectOverviewTasks(tasks, "today_pending", now).every((task) => allPending.has(task.id)));
  assert.equal(selectOverviewTasks(tasks, "today_pending", now)[0], tasks[0], "original timestamps and task records must be preserved");
});

test("task page preserves server-selected raw statuses when rendering cards and drilldown titles", () => {
  let definition;
  let navigationTitle;
  const source = fs.readFileSync(path.join(__dirname, "../miniprogram/pages/tasks/index.js"), "utf8");
  vm.runInNewContext(source, {
    require: (name) => name.endsWith("taskOverview") ? { selectOverviewTasks, sortTasks, TASK_SORT_OPTIONS } : name.endsWith("statusLight") ? require("../miniprogram/utils/statusLight") : {},
    Page: (page) => { definition = page; },
    wx: { setNavigationBarTitle: ({ title }) => { navigationTitle = title; } },
  });
  const page = {
    ...definition,
    data: { ...definition.data },
    setData(update, callback) { Object.assign(this.data, update); if (callback) callback(); },
  };
  const today = new Date().toISOString();
  const tasks = [
    { id: "pending", status: "pending", due_at: today },
    { id: "cancelled", status: "cancelled", due_at: today },
    { id: "completed", status: "completed", due_at: today, completed_at: today },
  ];

  page.onLoad({ overview: "today_pending" });
  page.acceptTaskPage({items:[tasks[0]],summary:{total:3,pending_count:1,completed_count:1,filtered_total:1},has_more:false,next_offset:null});
  assert.equal(navigationTitle, "今日待办");
  assert.deepEqual(ids(page.data.filteredTasks), ["pending"]);
  assert.equal(page.data.filteredTasks[0].due_at, today);
  assert.equal(page.data.totalCount, 3);

  page.onLoad({ overview: "today_completed" });
  page.acceptTaskPage({items:[tasks[2]],summary:{total:3,pending_count:1,completed_count:1,filtered_total:1},has_more:false,next_offset:null});
  assert.deepEqual(ids(page.data.filteredTasks), ["completed"]);
  assert.equal(page.data.filteredTasks[0].completed_at, today);

  page.onLoad({ tab: "rejected" });
  page.acceptTaskPage({items:[tasks[1]],summary:{total:3,pending_count:1,completed_count:1,filtered_total:1},has_more:false,next_offset:null});
  assert.equal(page.data.overviewFilter, "");
  assert.deepEqual(ids(page.data.filteredTasks), ["cancelled"]);
  assert.equal(page.data.filteredTasks[0].statusLabel, "已拒绝");
});

test("default order puts Beijing today first, then descending deadlines, without mutating records", () => {
  const now = new Date("2026-09-09T05:00:00Z");
  const tasks = [
    { id: "past", due_at: "2026-09-08T15:59:59Z" },
    { id: "tomorrow", due_at: "2026-09-09T16:00:00Z" },
    { id: "early", due_at: "2026-09-08T16:00:00Z" },
    { id: "missing", due_at: null },
    { id: "late", due_at: "2026-09-09T15:59:59Z" },
  ];
  const before = JSON.stringify(tasks);
  assert.deepEqual(ids(sortTasks(tasks, "today_first", now)), ["late", "early", "tomorrow", "past", "missing"]);
  assert.deepEqual(ids(sortTasks(tasks, "due_desc", now)), ["tomorrow", "late", "early", "past", "missing"]);
  assert.deepEqual(ids(sortTasks(tasks, "due_asc", now)), ["past", "early", "late", "tomorrow", "missing"]);
  assert.equal(JSON.stringify(tasks), before);
});

test("created-time sort ignores deadlines and invalid or missing times remain last", () => {
  const tasks = [
    { id: "invalid", due_at: "bad", created_at: "bad" },
    { id: "old", created_at: "2026-08-01T12:00:00Z" },
    { id: "missing" },
    { id: "new", due_at: "2026-07-01T12:00:00Z", created_at: "2026-09-01T12:00:00Z" },
  ];
  assert.deepEqual(ids(sortTasks(tasks, "created_desc")), ["new", "old", "invalid", "missing"]);
  assert.equal(sortTasks(tasks, "due_desc")[0].id, "new");
});

test("changing the dropdown keeps the existing filters and opens the existing task detail", () => {
  let definition;
  let detailUrl;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../miniprogram/pages/tasks/index.js"), "utf8"), {
    require: (name) => name.endsWith("taskOverview") ? { selectOverviewTasks, sortTasks, TASK_SORT_OPTIONS } : name.endsWith("statusLight") ? require("../miniprogram/utils/statusLight") : {},
    Page: (page) => { definition = page; },
    wx: { setNavigationBarTitle() {}, navigateTo: ({ url }) => { detailUrl = url; } },
  });
  const page = { ...definition, data: { ...definition.data }, setData(update, callback) { Object.assign(this.data, update); if (callback) callback(); } };
  page.onLoad({ overview: "all_pending", team: "南区" });
  const requests = [];
  page.loadTasks = () => { requests.push(page.taskParams()); return Promise.resolve(); };
  page.changeSort({ detail: { value: "1" } });
  assert.equal(requests[0].order, "due_desc");
  page.changeSort({ detail: { value: "2" } });
  assert.equal(requests[1].order, "due_asc");
  assert.equal(requests[1].team, "南区");
  assert.equal(requests[1].overview, "all_pending");
  assert.equal(page.data.overviewFilter, "all_pending");
  page.changeSort({ detail: { value: "99" } });
  assert.equal(page.data.sortIndex, 2);
  page.openTask({ currentTarget: { dataset: { id: "early" } } });
  assert.equal(detailUrl, "/pages/task-detail/index?id=early");
});

test("home summary and quick entry route to the existing task page without running an Agent", () => {
  let definition;
  const urls = [];
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../miniprogram/pages/index/index.js"), "utf8"), {
    require: (name) => name.endsWith("taskOverview") ? { buildOverviewMetrics } : {},
    Page: (page) => { definition = page; },
    wx: { navigateTo: ({ url }) => urls.push(url) },
  });
  for (const key of ["today_completed", "today_pending", "all_pending"]) {
    definition.openOverviewTasks({ currentTarget: { dataset: { key } } });
  }
  definition.tapQuickAction({ currentTarget: { dataset: { action: "查看今日待办" } } });
  assert.deepEqual(urls, [
    "/pages/tasks/index?overview=today_completed",
    "/pages/tasks/index?overview=today_pending",
    "/pages/tasks/index?overview=all_pending",
    "/pages/tasks/index?overview=today_pending",
  ]);
});
