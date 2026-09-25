/* Web task presentation. Keep API order, pagination and task actions authoritative. */
(function (global) {
  'use strict';
  const DAY = 86400000, OFFSET = 8 * 3600000;
  const dateFormat = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
  function time(value) {
    if (value === undefined || value === null || value === '') return null;
    const result = typeof value === 'number' || /^\d+$/.test(String(value)) ? Number(value) : Date.parse(value);
    return Number.isFinite(result) ? result : null;
  }
  const day = value => Math.floor((value + OFFSET) / DAY);
  function formatted(value, now) {
    const parts = Object.fromEntries(dateFormat.formatToParts(value).map(part => [part.type, part.value]));
    const year = new Date(value + OFFSET).getUTCFullYear();
    const yearLabel = year === new Date(now + OFFSET).getUTCFullYear() ? '' : year + '年';
    return `${yearLabel}${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}`;
  }
  function timing(item, now) {
    if (item.status === 'completed' || item.status === 'rejected') {
      const completed = time(item.completed_at || item.completedAt);
      return {key: 'closed', label: item.status === 'completed' ? '已完成' : '已结束', detail: item.status === 'completed' ? (completed === null ? '完成时间未记录' : formatted(completed, now)) : '任务已结束', tone: 'closed'};
    }
    const due = time(item.due_at || item.dueAt);
    if (due === null) return {key: 'undated', label: '未设截止时间', detail: '待安排', tone: 'undated'};
    const difference = day(due) - day(now), clock = new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(due);
    if (due < now) {
      const days = Math.floor((now - due) / DAY), hours = Math.floor((now - due) / 3600000);
      const elapsed = days ? `${days} 天` : hours ? `${hours} 小时` : '不足 1 小时';
      return {key: 'overdue', label: `已逾期 ${elapsed}`, detail: formatted(due, now), tone: 'overdue'};
    }
    if (difference === 0) return {key: 'today', label: `今天 ${clock}`, detail: '今天截止', tone: 'today'};
    return {key: 'upcoming', label: difference === 1 ? `明天 ${clock}` : formatted(due, now), detail: difference === 1 ? '明天截止' : `${difference} 天后截止`, tone: 'upcoming'};
  }
  const groups = {
    overdue: ['已逾期', '已过截止时间，优先处理'],
    today: ['今天到期', '按截止时间从早到晚'],
    upcoming: ['后续安排', '提前准备，按期推进'],
    undated: ['未设截止时间', '确认期限后再安排'],
  };
  const sorts = {
    today_first: ['今天到期优先', '今天到期排前；同组截止时间从晚到早'],
    due_desc: ['最晚到期优先', '截止时间从晚到早，未设时间的放最后'],
    due_asc: ['最早到期优先', '按截止时间从早到晚，未设时间的放最后'],
    created_desc: ['最近创建优先', '最近创建的任务排前'],
  };
  function configure(page) {
    if (page.route !== 'pages/tasks/index') return;
    const index = page.data.sortOptions.findIndex(item => item.key === 'due_asc');
    if (index >= 0) page.data.sortIndex = index;
    page.data.sortOptions = page.data.sortOptions.map(item => ({...item, label: sorts[item.key]?.[0] || item.label, hint: sorts[item.key]?.[1] || item.hint}));
    const onLoad = page.onLoad, selectTab = page.selectTab;
    if (typeof onLoad === 'function') page.onLoad = function (options) {
      const result = onLoad.call(this, options);
      // Overview and status describe the same initial subset. In particular,
      // today's completed tasks must not also carry the default pending tab.
      if (this.data.overviewFilter) this.setData({activeTab: this.data.overviewFilter === 'today_completed' ? 'completed' : 'pending'});
      return result;
    };
    if (typeof selectTab === 'function') page.selectTab = function (event) {
      const key = event?.currentTarget?.dataset?.key;
      if (!['pending', 'completed', 'rejected', 'all'].includes(key)) return;
      if (!global.SalesRuntime.replacePageQuery(this, {overview: null, tab: key})) return;
      this.setData({overviewFilter: '', overviewTitle: '', overviewDescription: '', overviewEmptyTitle: ''});
      global.SalesRuntime.wx.setNavigationBarTitle({title: this.data.opportunityOnly ? '商机待办' : '任务'});
      // The shared handler keeps scope, sorting, validation and pagination.
      return selectTab.call(this, event);
    };
  }
  function presentation(page, data, now = Date.now()) {
    if (page.route !== 'pages/tasks/index') return data;
    const grouped = data.sortOptions[data.sortIndex]?.key === 'due_asc' && data.activeTab === 'pending' && data.overviewFilter !== 'today_completed';
    let previous = '';
    const rows = (data.filteredTasks || []).map(item => {
      const when = timing(item, now), heading = grouped && when.key !== previous ? groups[when.key] : null;
      previous = when.key;
      const title = item.title || item.description || '未命名任务';
      // Shared task normalization intentionally groups unfinished states as `pending`.
      // Reuse its review label; this display state never grants review permissions.
      const awaitingReview = item.statusLabel === '待发起人确认';
      return {...item, webTitle: title, webDescription: item.description && item.description.trim() !== title.trim() ? item.description : '',
        webTime: when, webGroup: heading ? heading[0] : '', webGroupHint: heading ? heading[1] : '',
        webPriority: ['high', 'urgent'].includes(item.priorityClass) ? (item.priorityClass === 'urgent' ? '紧急' : '高优先级') : '',
        webState: item.status === 'completed' ? 'complete' : item.status === 'rejected' ? 'closed' : item.handover_required ? 'handover' : awaitingReview ? 'review' : item.requires_action ? 'confirm' : 'active',
        webAction: item.status === 'completed' ? '查看结果' : item.status === 'rejected' ? '查看详情' : awaitingReview ? '查看验收' : item.requires_action ? '去确认' : '查看任务',
      };
    });
    return {...data, webTaskRows: rows, webTaskGrouped: grouped,
      webTaskTabs: data.tabs.map(tab => ({...tab, count: data.loading || data.loadError ? '—' : tab.key === 'pending' ? data.pendingCount : tab.key === 'completed' ? data.completedCount : tab.key === 'all' ? data.totalCount : Math.max(0, data.totalCount - data.pendingCount - data.completedCount)})),
    };
  }
  global.SalesTasks = {configure, presentation};
})(globalThis);
