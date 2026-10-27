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
    if (item.isEnded || ['completed','cancelled','rejected'].includes(item.status)) {
      const completed = time(item.endedAt || item.ended_at || item.completed_at || item.completedAt);
      return {key: 'closed', label: item.statusLabel || '已结束', detail: completed === null ? '结束时间未记录' : formatted(completed, now), tone: 'closed'};
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
  const TASK_TABS = [
    {key: 'all', label: '全部'}, {key: 'pending', label: '未完成'},
    {key: 'in_progress', label: '进行中'}, {key: 'completed', label: '正常完成'},
    {key: 'deferred', label: '已延期'}, {key: 'needs_adjustment', label: '需调整'},
    {key: 'ended', label: '已结束'},
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
  const syncIdentity = () => global.SalesRuntime.require('utils/access').identity(global.SalesRuntime.app.globalData.session);
  function readShadow(page) {
    const shadow = Object.create(page);
    shadow.data = {...page.data};
    shadow.setData = values => Object.assign(shadow.data, values);
    return shadow;
  }
  async function refreshTaskList(page, current) {
    const serial = page.taskSerial, offset = page.data.nextOffset;
    const query = JSON.stringify([page.taskManagementParams(), page.data.activeTab, page.data.taskSummaryGroup, page.data.taskPeriod, page.data.sortIndex]);
    const shadow = readShadow(page);
    // Reuse the shared validator/normalizer without clearing visible rows, page
    // selection or an open cancellation dialog. Read only the loaded prefix.
    const pages = Math.max(1, Math.ceil(Number(offset || 0) / 20), Math.ceil((page.data.tasks || []).length / 20));
    const visibleEnd = page._webTaskVisibleEnd || 20;
    await shadow.fetchTaskPage(false);
    if (shadow.data.taskSyncState === 'unavailable') throw Object.assign(Error(shadow.data.taskSyncError || '任务查看权限已变化，请重新进入页面'), {permissionDenied: true});
    if (shadow.data.loadError) throw Error(shadow.data.loadError);
    for (let i = 1; i < pages && shadow.data.hasMore && shadow.data.filteredTasks.length < visibleEnd && current(); i++) {
      await shadow.fetchTaskPage(true);
      if (shadow.data.taskSyncState === 'unavailable') throw Object.assign(Error(shadow.data.taskSyncError || '任务查看权限已变化，请重新进入页面'), {permissionDenied: true});
      if (shadow.data.moreError) throw Error(shadow.data.moreError);
    }
    if (!current() || page.taskSerial !== serial || page.data.nextOffset !== offset || page.data.loading || page.data.loadingMore || page.data.cancelOpen ||
        query !== JSON.stringify([page.taskManagementParams(), page.data.activeTab, page.data.taskSummaryGroup, page.data.taskPeriod, page.data.sortIndex])) return false;
    const patch = {};
    for (const key of ['tasks', 'filteredTasks', 'webTaskRows', 'hasMore', 'nextOffset', 'pendingCount', 'completedCount',
      'rejectedCount', 'endedCount', 'stoppedCount', 'unknownOutcomeCount', 'inProgressCount', 'deferredCount', 'needsAdjustmentCount', 'overdueCount', 'teamMemberCount', 'totalCount', 'filteredTotal']) patch[key] = shadow.data[key];
    page.setData(patch);
    return true;
  }
  function configureSync(page) {
    if (page._webTaskSyncConfigured) return;
    page._webTaskSyncConfigured = true;
    const list = page.route === 'pages/tasks/index';
    const onShow = page.onShow, onHide = page.onHide, onUnload = page.onUnload;
    let generation = 0, flight = null;
    const status = (state, message = '', timestamp) => {
      if (page._destroyed) return;
      const previous = page.data.webTaskSync || {};
      page.setData({webTaskSync: {status: state, message, lastSyncedAt: timestamp || previous.lastSyncedAt || null}});
    };
    async function read(request) {
      const token = ++generation, actor = syncIdentity(), taskId = page.data.taskId;
      const current = () => !page._destroyed && token === generation && actor === syncIdentity() && taskId === page.data.taskId;
      status('syncing');
      try {
        const result = await request(current);
        if (!current()) return result;
        const error = list ? page.data.loadError || (page.data.accessBlocked && page.data.accessMessage) : page.data.executionError;
        if (error) status('error', error);
        else status('current', '', result === false ? undefined : new Date().toISOString());
        return result;
      } catch (error) {
        if (current()) {
          if (error.permissionDenied) page.setData({tasks: [], filteredTasks: [], webTaskRows: [], accessBlocked: true, accessMessage: error.message});
          status(error.code === 'NETWORK_ERROR' ? 'offline' : 'error', error.message || '刷新失败，请重试');
        }
      }
    }
    // Record initial reads and explicit post-save reads as well as button reads.
    // No timer, visibility event or network event starts a background request.
    const method = list ? 'loadTasks' : 'loadExecution', original = page[method];
    if (typeof original === 'function') page[method] = function (...args) {
      return read(() => original.apply(this, args));
    };
    page.refreshWebTasks = function () {
      const d = this.data;
      if (flight || d.loading || d.loadingMore || d.executionLoading || d.executionBusy || d.submitting ||
          d.cancelOpen || d.coordinateOpen || d.executionDialog || d.lightweightDialog || d.checklistDraft ||
          d.requirementsDraft || d.handoverPurpose || d.migrationOpen) return flight;
      const request = list && d.tasks?.length && !d.loadError
        ? read(current => refreshTaskList(this, current))
        : !list && !d.execution && !d.taskMigration ? read(() => this.loadTask()) : this[method]();
      flight = Promise.resolve(request).finally(() => {flight = null;});
      return flight;
    };
    page.onShow = function (...args) {
      const result = onShow?.apply(this, args);
      // Mini keeps its own polling policy; Web only reads on entry, after a
      // mutation or when the user clicks refresh.
      if (this.executionPoll != null) {global.clearInterval(this.executionPoll); this.executionPoll = null;}
      if (this.taskSyncTimer != null) {global.clearTimeout(this.taskSyncTimer); this.taskSyncTimer = null;}
      return result;
    };
    page.onHide = function (...args) {generation++; return onHide?.apply(this, args);};
    page.onUnload = function (...args) {generation++; return onUnload?.apply(this, args);};
  }
  function configure(page) {
    if (page.route === 'pages/task-detail/index') {configureSync(page); return;}
    if (page.route !== 'pages/tasks/index') return;
    if (page._salesTasksConfigured) return;
    page._salesTasksConfigured = true;
    const index = page.data.sortOptions.findIndex(item => item.key === 'due_asc');
    if (index >= 0) page.data.sortIndex = index;
    page.data.sortOptions = page.data.sortOptions.map(item => ({...item, label: sorts[item.key]?.[0] || item.label, hint: sorts[item.key]?.[1] || item.hint}));
    const onLoad = page.onLoad, onShow = page.onShow, selectTab = page.selectTab, taskParams = page.taskParams, acceptTaskPage = page.acceptTaskPage;
    if (typeof onLoad === 'function') page.onLoad = function (options) {
      const result = onLoad.call(this, options);
      this.setData({webTaskTeamId: options?.team_id || ''});
      if (TASK_TABS.some(tab => tab.key === options?.tab)) this.setData({activeTab: options.tab, taskSummaryGroup: ''});
      const requested = options?.period;
      const overview = this.data.overviewFilter || options?.overview;
      const initial = periodValues.has(requested) ? requested : overview === 'today_pending' || overview === 'today_completed' ? 'day' : 'all';
      this.setData(periodState(this, initial));
      // Overview and status describe the same initial subset. In particular,
      // today's completed tasks must not also carry the default pending tab.
      if (this.data.overviewFilter) this.setData({activeTab: this.data.overviewFilter === 'today_completed' ? 'completed' : 'pending', taskSummaryGroup: ''});
      this.setData(periodState(this, initial));
      return result;
    };
    if (typeof acceptTaskPage === 'function') page.acceptTaskPage = function (result, ...args) {
      const total = result?.summary?.filtered_total, scopeTotal = result?.summary?.scope_total;
      if ([total, scopeTotal].some(value => value !== undefined && (!Number.isInteger(value) || value < 0))) throw Error('任务列表数量不完整，请刷新后重试');
      const accepted = acceptTaskPage.call(this, result, ...args);
      // The management summary stays scoped to people and period; this count
      // alone describes the status-filtered list, including later pages.
      if (total !== undefined || scopeTotal !== undefined) this.setData({
        ...(total !== undefined ? {filteredTotal: total} : {}),
        ...(scopeTotal !== undefined ? {totalCount: scopeTotal} : {}),
      });
      return accepted;
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
      const params = {order: this.data.sortOptions[this.data.sortIndex]?.key || 'due_asc', scope: (this.data.taskView || 'self') === 'self' ? 'mine' : 'all', member_id: this.data.fdeTaskMemberId || undefined, team_id: this.data.webTaskTeamId || undefined, undated: 'include'};
      Object.assign(params, periodQuery(this.data.taskPeriod || 'all', this.data.activeTab || 'pending').params);
      if (this.data.taskSummaryGroup==='progressing' || this.data.activeTab==='pending') params.tab = 'pending';
      if (['completed','ended'].includes(this.data.activeTab)) params.tab = this.data.activeTab;
      if (['in_progress','deferred','needs_adjustment'].includes(this.data.activeTab)) params.status = this.data.activeTab;
      return params;
    };
    if (typeof selectTab === 'function') page.selectTab = function (event) {
      const key = event?.currentTarget?.dataset?.key;
      if (!TASK_TABS.some(tab => tab.key === key)) return;
      if (!global.SalesRuntime.replacePageQuery(this, {overview: null, tab: key})) return;
      this.setData({overviewFilter: '', overviewTitle: '', overviewDescription: '', overviewEmptyTitle: ''});
      this.setData(periodState(this, this.data.taskPeriod || 'all', key));
      global.SalesRuntime.wx.setNavigationBarTitle({title: this.data.opportunityOnly ? '商机待办' : '任务'});
      if (key === 'pending' || key === 'ended') {
        this.setData({activeTab: key, taskSummaryGroup: ''});
        return this.loadTasks();
      }
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
    configureSync(page);
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
      const lightweight = item.execution_mode === 'execution_v3' || item.executionMode === 'execution_v3';
      const awaitingReview = item.processing_reason === 'awaiting_review' || item.processingReason === 'awaiting_review' || item.statusLabel === '待发起人确认';
      return {...item, showProgress:lightweight?false:item.showProgress, canCancel:lightweight?false:item.canCancel, webTitle: title, webDescription: item.description && item.description.trim() !== title.trim() ? item.description : '',
        webTime: when, webGroup: heading ? heading[0] : '', webGroupHint: heading ? heading[1] : '',
        webPriority: ['high', 'urgent'].includes(item.priorityClass) ? (item.priorityClass === 'urgent' ? '紧急' : '高优先级') : '',
        webState: item.isSuccessful ? 'complete' : item.isEnded || ['completed','cancelled','rejected'].includes(item.status) ? 'closed' : item.handover_required ? 'handover' : awaitingReview ? 'review' : item.requires_action ? 'confirm' : 'active',
        webAction: item.isSuccessful ? '查看结果' : item.isEnded || ['completed','cancelled','rejected'].includes(item.status) ? '查看详情' : awaitingReview ? '查看验收' : lightweight ? '查看任务' : item.requires_action ? '去确认' : '查看任务',
      };
    });
    const serverNumber = value => value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
    return {...data, webTaskRows: rows, webTaskGrouped: grouped,
      taskPeriodOptions: data.taskPeriodOptions || PERIOD_OPTIONS,
      webTaskKpis: {pending: serverNumber(data.pendingCount), completed: serverNumber(data.completedCount), overdue: serverNumber(data.overdueCount), teamMembers: serverNumber(data.teamMemberCount)},
      taskKpiMissing: ['overdue', 'teamMembers'].filter(key => (key === 'overdue' ? data.overdueCount : data.teamMemberCount) === null || (key === 'overdue' ? data.overdueCount : data.teamMemberCount) === undefined),
      webTaskTabs: TASK_TABS.map(tab => ({...tab, count: data.loading || data.loadError ? '—' : tab.key === 'pending' ? data.pendingCount : tab.key === 'completed' ? (data.completedCount ?? '—') : tab.key === 'all' ? data.totalCount : tab.key === 'in_progress' ? (data.inProgressCount ?? '—') : tab.key === 'deferred' ? (data.deferredCount ?? '—') : tab.key === 'needs_adjustment' ? (data.needsAdjustmentCount ?? '—') : (data.endedCount ?? '—')})),
    };
  }
  global.SalesTasks = {configure, presentation, periodQuery, PERIOD_OPTIONS};
})(globalThis);
