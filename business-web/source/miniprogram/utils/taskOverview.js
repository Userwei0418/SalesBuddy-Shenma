/**
 * BACKEND-CONTRACT 今日任务摘要与列表共用北京时间口径：今日待办=今天 due_at 且 status 非 completed/cancelled；今日已完成=今天 completed_at。
 * 全部待办包含过期与未来任务，不是仅今天；统计只遍历传入的已加载任务数组，不处理服务端分页。
 * 后端拒绝任务应返回 cancelled；字面 rejected 未被 isPending 排除，是对接需统一的状态兼容缺口。
 */
const CHINA_OFFSET_MS = 8 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;

function chinaDay(value) {
  if (value === undefined || value === null || value === "") return null;
  const timestamp = value instanceof Date
    ? value.getTime()
    : typeof value === "number" || /^\d+$/.test(String(value))
      ? Number(value)
      : Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return Math.floor((timestamp + CHINA_OFFSET_MS) / DAY_MS);
}

function isPending(task) {
  return task.status !== "completed" && task.status !== "cancelled";
}

// Compare dates in Beijing time, independent of the device's local time zone.
// Keep the original records so list drill-down and summary counts share one rule.
function selectOverviewTasks(tasks, filter, now = new Date()) {
  const items = Array.isArray(tasks) ? tasks.filter((task) => task && typeof task === "object") : [];
  const today = chinaDay(now);
  if (filter === "all_pending") return items.filter(isPending);
  if (today === null) return [];
  if (filter === "today_completed") {
    return items.filter((task) => task.status === "completed" && chinaDay(task.completed_at) === today);
  }
  if (filter === "today_pending") {
    return items.filter((task) => isPending(task) && chinaDay(task.due_at) === today);
  }
  return [];
}

function buildOverviewMetrics(tasks, now = new Date()) {
  return [
    { key: "today_completed", label: "今日已完成", value: selectOverviewTasks(tasks, "today_completed", now).length },
    { key: "today_pending", label: "今日待办", value: selectOverviewTasks(tasks, "today_pending", now).length },
    { key: "all_pending", label: "全部待办", value: selectOverviewTasks(tasks, "all_pending", now).length },
  ];
}

const TASK_SORT_OPTIONS = [
  { key: "today_first", label: "按今日优先排序", hint: "今日事项在前，同组按截止时间倒序" },
  { key: "due_desc", label: "按截止时间倒序", hint: "截止时间从晚到早" },
  { key: "due_asc", label: "按截止时间正序", hint: "截止时间从早到晚" },
  { key: "created_desc", label: "按创建时间倒序", hint: "最近创建的事项在前" },
];

function timestamp(value) {
  if (value === undefined || value === null || value === "") return null;
  const time = value instanceof Date ? value.getTime()
    : typeof value === "number" || /^\d+$/.test(String(value)) ? Number(value) : Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

function compareTime(a, b, ascending = false) {
  const left = timestamp(a);
  const right = timestamp(b);
  if (left === null || right === null) return left === right ? 0 : left === null ? 1 : -1;
  return ascending ? left - right : right - left;
}

function sortTasks(tasks, sortKey = "today_first", now = new Date()) {
  const today = chinaDay(now);
  const key = TASK_SORT_OPTIONS.some((option) => option.key === sortKey) ? sortKey : "today_first";
  return tasks.slice().sort((a, b) => {
    if (key === "today_first") {
      const aToday = today !== null && chinaDay(a.due_at || a.dueAt) === today;
      const bToday = today !== null && chinaDay(b.due_at || b.dueAt) === today;
      if (aToday !== bToday) return aToday ? -1 : 1;
    }
    const primary = key === "created_desc"
      ? compareTime(a.created_at, b.created_at)
      : compareTime(a.due_at || a.dueAt, b.due_at || b.dueAt, key === "due_asc");
    return primary || compareTime(a.created_at, b.created_at)
      || String(a.id || "").localeCompare(String(b.id || ""));
  });
}

module.exports = { selectOverviewTasks, buildOverviewMetrics, sortTasks, TASK_SORT_OPTIONS };
