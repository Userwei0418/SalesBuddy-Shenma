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
  const PERIOD_OPTIONS = [
    {value: 'all', label: '全部'},
    {value: 'day', label: '日'},
    {value: 'week', label: '周'},
    {value: 'month', label: '月'},
    {value: 'quarter', label: '季'},
  ];
  const periodValues = new Set(PERIOD_OPTIONS.map(item => item.value));
  function shanghaiDateParts(now = Date.now()) {
    return Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric'}).formatToParts(now).map(part => [part.type, Number(part.value)]));
  }
  function dateKey(now = Date.now()) {
    const p = shanghaiDateParts(now);
    return `${p.year}-${String(p.month).padStart(2, '0')}-${String(p.day).padStart(2, '0')}`;
  }
  function addDate(key, days) {
    return new Date(Date.parse(`${key}T00:00:00Z`) + days * DAY).toISOString().slice(0, 10);
  }
  function managementRange(period = 'all', now = Date.now()) {
    if (period === 'all') return {};
    const today = dateKey(now), parts = shanghaiDateParts(now);
    let start = today, end = today;
    if (period === 'week') {
      const weekday = (new Date(`${today}T00:00:00Z`).getUTCDay() + 6) % 7;
      start = addDate(today, -weekday); end = addDate(start, 6);
    } else if (period === 'month') {
      start = `${parts.year}-${String(parts.month).padStart(2, '0')}-01`;
      end = new Date(Date.UTC(parts.year, parts.month, 0)).toISOString().slice(0, 10);
    } else if (period === 'quarter') {
      const first = Math.floor((parts.month - 1) / 3) * 3 + 1;
      start = `${parts.year}-${String(first).padStart(2, '0')}-01`;
      end = new Date(Date.UTC(parts.year, first + 2, 0)).toISOString().slice(0, 10);
    }
    return {date_from: start, date_to: end};
  }
  function periodQuery(period = 'all', tab = 'pending', now = Date.now()) {
    if (!periodValues.has(period)) return {supported: false, params: {}, message: '任务周期无效'};
    return {supported: true, params: managementRange(period, now), message: ''};
  }
  function periodState(page, period = page.data.taskPeriod || 'all', tab = page.data.activeTab || 'pending') {
    const result = periodQuery(period, tab);
    return {taskPeriod: period, taskPeriodOptions: PERIOD_OPTIONS, taskPeriodUnsupported: !result.supported, taskPeriodMessage: result.message};
  }
  function configure(page) {
    if (page.route !== 'pages/tasks/index') return;
    if (page._salesTasksConfigured) return;
    page._salesTasksConfigured = true;
    const index = page.data.sortOptions.findIndex(item => item.key === 'due_asc');
    if (index >= 0) page.data.sortIndex = index;
    page.data.sortOptions = page.data.sortOptions.map(item => ({...item, label: sorts[item.key]?.[0] || item.label, hint: sorts[item.key]?.[1] || item.hint}));
    const onLoad = page.onLoad, onShow = page.onShow, selectTab = page.selectTab, taskParams = page.taskParams;
    if (typeof onLoad === 'function') page.onLoad = function (options) {
      const result = onLoad.call(this, options);
      this.setData({webTaskTeamId: options?.team_id || ''});
      const requested = options?.period;
      const overview = this.data.overviewFilter || options?.overview;
      const initial = periodValues.has(requested) ? requested : overview === 'today_pending' || overview === 'today_completed' ? 'day' : 'all';
      this.setData(periodState(this, initial));
      // Overview and status describe the same initial subset. In particular,
      // today's completed tasks must not also carry the default pending tab.
      if (this.data.overviewFilter) this.setData({activeTab: this.data.overviewFilter === 'today_completed' ? 'completed' : 'pending'});
      this.setData(periodState(this, initial));
      return result;
    };
    if (typeof onShow === 'function') page.onShow = function () {
      const identity = global.SalesRuntime.require('utils/access').identity(global.SalesRuntime.app.globalData.session);
      if (this.taskIdentity && this.taskIdentity !== identity) this.setData({webTaskTeamId: ''});
      return onShow.call(this);
    };
    page.changeWebTaskScope = function (event) {
      const scope = event?.detail?.scope;
      if (!scope || !['self', 'team', 'member'].includes(scope.kind)) return;
      const session = global.SalesRuntime.app.globalData.session;
      const access = global.SalesRuntime.require('utils/access');
      if (!access.can(session, 'task.read')) return;
      if (scope.kind === 'team' && !access.canViewTeam(session, 'task.read')) return;
      this.setData({taskView: scope.kind === 'self' ? 'self' : 'team',
        fdeTaskMemberId: scope.kind === 'member' ? scope.memberId : '',
        webTaskTeamId: scope.kind === 'team' && scope.teamId !== 'all' ? scope.teamId : ''});
      return this.loadTasks();
    };
    if (typeof taskParams === 'function') page.taskParams = function () {
      const base = taskParams.call(this) || {};
      const period = this.data.taskPeriod || 'all';
      if (period === 'all') return base;
      const result = {...base};
      delete result.overview;
      delete result.completed_year;
      delete result.completed_quarters;
      const query = periodQuery(period, this.data.activeTab || 'pending');
      if (query.supported) Object.assign(result, query.params);
      return result;
    };
    page.taskManagementParams = function () {
      // A page can be queried before the shared onLoad handler has hydrated
      // taskView (for example when a browser restores the route). Default to
      // the least surprising and least expansive scope: the signed-in user's
      // tasks. The explicit team view still maps to the authorized `all`
      // scope below.
      const params = {scope: (this.data.taskView || 'self') === 'self' ? 'mine' : 'all', member_id: this.data.fdeTaskMemberId || undefined, team_id: this.data.webTaskTeamId || undefined, undated: 'include'};
      Object.assign(params, periodQuery(this.data.taskPeriod || 'all', this.data.activeTab || 'pending').params);
      if (this.data.activeTab === 'completed') params.status = 'completed';
      else if (this.data.activeTab === 'rejected') params.status = 'cancelled';
      return params;
    };
    if (typeof selectTab === 'function') page.selectTab = function (event) {
      const key = event?.currentTarget?.dataset?.key;
      if (!['pending', 'completed', 'rejected', 'all'].includes(key)) return;
      if (!global.SalesRuntime.replacePageQuery(this, {overview: null, tab: key})) return;
      this.setData({overviewFilter: '', overviewTitle: '', overviewDescription: '', overviewEmptyTitle: ''});
      this.setData(periodState(this, this.data.taskPeriod || 'all', key));
      global.SalesRuntime.wx.setNavigationBarTitle({title: this.data.opportunityOnly ? '商机待办' : '任务'});
      // The shared handler keeps scope, sorting, validation and pagination.
      return selectTab.call(this, event);
    };
    page.changeTaskPeriod = function (event) {
      const value = typeof event === 'string' ? event : event?.detail?.value || event?.currentTarget?.dataset?.period;
      if (!periodValues.has(value)) return;
      this.setData({...periodState(this, value), overviewFilter: '', overviewTitle: '', overviewDescription: '', overviewEmptyTitle: ''});
      if (!global.SalesRuntime.replacePageQuery(this, {overview: null, period: value === 'all' ? null : value})) return;
      return this.loadTasks();
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
    const serverNumber = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
    return {...data, webTaskRows: rows, webTaskGrouped: grouped,
      taskPeriodOptions: data.taskPeriodOptions || PERIOD_OPTIONS,
      webTaskKpis: {pending: serverNumber(data.pendingCount), completed: serverNumber(data.completedCount), overdue: serverNumber(data.overdueCount), teamMembers: serverNumber(data.teamMemberCount)},
      taskKpiMissing: ['overdue', 'teamMembers'].filter(key => (key === 'overdue' ? data.overdueCount : data.teamMemberCount) === null || (key === 'overdue' ? data.overdueCount : data.teamMemberCount) === undefined),
      webTaskTabs: data.tabs.map(tab => ({...tab, count: data.loading || data.loadError ? '—' : tab.key === 'pending' ? data.pendingCount : tab.key === 'completed' ? data.completedCount : tab.key === 'all' ? data.totalCount : (data.rejectedCount ?? Math.max(0, data.totalCount - data.pendingCount - data.completedCount))})),
    };
  }
  global.SalesTasks = {configure, presentation, periodQuery, PERIOD_OPTIONS};
})(globalThis);
