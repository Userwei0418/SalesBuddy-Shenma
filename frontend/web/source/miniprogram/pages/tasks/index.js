/** Task cards request one server-filtered page; summaries describe the full filtered scope. */
const { taskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const { TASK_SORT_OPTIONS } = require("../../utils/taskOverview");

const access = require("../../utils/access");
const { canCancelTask, cancellationIdentity } = require("../../utils/taskCancellation");

const OVERVIEW_FILTERS = {
  today_completed: { title: "今日已完成", description: "今天完成的任务，按北京时间统计", emptyTitle: "今天还没有已完成任务" },
  today_pending: { title: "今日待办", description: "今天到期且尚未完成的任务，按北京时间统计", emptyTitle: "今天没有待办任务" },
  all_pending: { title: "全部待办", description: "全部尚未完成的任务，包含今天及其他日期的待办", emptyTitle: "当前没有未完成任务" },
};

function completedPeriod(options) {
  const year = Number(options.year);
  if (!Number.isInteger(year) || year < 2000 || year > 2100) return {completedYear:0,completedQuarters:[],completedPeriodLabel:''};
  const quarters = [...new Set(String(options.quarters || '').split(',').map(Number).filter(q=>Number.isInteger(q)&&q>=1&&q<=4))].sort();
  return {completedYear:year,completedQuarters:quarters,completedPeriodLabel:`${year} 年 ${!quarters.length || quarters.length===4 ? '全年' : quarters.map(q=>`Q${q}`).join(' + ')}`};
}

function formatTime(value, fallback) {
  if (!value) return fallback || "待安排";
  if (typeof value === "string" && !/^\d+$/.test(value)) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return `${parsed.getMonth() + 1}月${parsed.getDate()}日 ${String(parsed.getHours()).padStart(2, "0")}:${String(parsed.getMinutes()).padStart(2, "0")}`;
    return value;
  }
  const date = new Date(Number(value));
  if (Number.isNaN(date.getTime())) return fallback || "待安排";
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function normalizeTask(item, session) {
  const assignees = Array.isArray(item.assignees) ? item.assignees : [];
  const owner = item.owner || item.owner_name || (assignees.find((person) => person.responsibility === "owner") || assignees[0] || {}).name || "待分配";
  const ownerRecord = assignees.find((person) => person.responsibility === "owner") || assignees[0] || {};
  const status = item.status === "completed" ? "completed" : item.status === "cancelled" ? "rejected" : "pending";
  const priorityMap = { high: "高", urgent: "紧急", medium: "中", normal: "普通" };
  const priority = priorityMap[item.priority_code] || item.priority || "普通";
  const priorityClassMap = { 高: "high", 紧急: "urgent", 中: "medium", 普通: "normal" };
  return {
    ...item,
    sourceStatus: item.status,
    canCancel: canCancelTask(item, session),
    signal:taskLight(item),
    owner,
    team: item.team || item.team_name || ownerRecord.team_name || "",
    status,
    statusLabel: item.handover_required?"待交接":item.status==="cancelled"&&item.last_event_type==="cancel"?"已取消":({ pending_confirm: "待接受", pending_review: "待发起人确认", pending_execution: "已接受", in_progress: "执行中", deferred: "已接受", completed: "已完成", cancelled: "已拒绝", rejected: "已拒绝" })[item.status] || "待处理",
    priority,
    priorityClass: item.priority_code || priorityClassMap[priority] || "normal",
    dueLabel: formatTime(item.due_at || item.dueAt, item.due),
    completedLabel: formatTime(item.completed_at || item.completedAt, ""),
    customer: item.customer_name || item.customer || "协作任务",
    creator: item.creator_name || item.creator || "系统任务",
    sourceLabel: item.task_type === "visit_follow_up" ? "待办Agent从跟进记录提取" : `${item.creator_name || item.creator || "管理层"}下发`,
    opportunityId: item.opportunity_id || item.opportunityId || "",
    opportunityName: item.opportunity_name || item.opportunityName || "",
    customerId: item.customer_id || item.customerId || "",
    remote: Boolean(item.due_at || item.creator_user_ref_id),
  };
}

Page({
  onTaskViewChange(e){return this.changeTaskView({currentTarget:{dataset:{view:e.detail.value}}});},
  onTabChange(e){return this.selectTab({currentTarget:{dataset:{key:e.detail.key}}});},

  data: {
    taskViewOptions: [{ value: "self", label: "本人任务" }, { value: "team", label: "团队任务" }],
    tabs: [
      { key: "pending", label: "待处理" },
      { key: "completed", label: "已完成" },
      { key: "rejected", label: "已结束" },
      { key: "all", label: "全部" },
    ],
    activeTab: "pending",
    tasks: [],
    filteredTasks: [],
    pendingCount: 0,
    completedCount: 0,
    totalCount: 0, filteredTotal: 0, rejectedCount: 0, overdueCount: null, teamMemberCount: null, hasMore: false, nextOffset: 0, loadingMore: false, moreError: "",
    loading: false,
    roleName: "",
    scope: "",
    overviewFilter: "",
    overviewTitle: "",
    overviewDescription: "",
    overviewEmptyTitle: "",
    sortOptions: TASK_SORT_OPTIONS,
    sortIndex: 0,
    opportunityOnly: false,
    completedYear: 0, completedQuarters: [], completedPeriodLabel: '',
    cancelOpen: false, cancelTaskId: "", cancelTaskVersion: 0,
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'tasks', options)) return;
    this.setData({taskView:options.scope==="team"?"team":"self",fdeTaskMemberId:options.member_id||"",...completedPeriod(options)});
    this.teamFilter = decodeURIComponent(options.team || "all");
    this.memberFilter = decodeURIComponent(options.member || "all");
    const requestedTab = ["pending", "completed", "rejected", "all"].includes(options.tab) ? options.tab : "pending";
    const overview = OVERVIEW_FILTERS[options.overview];
    const opportunityOnly = options.opportunity === "1";
    this.setData({
      activeTab: requestedTab,
      overviewFilter: overview ? options.overview : "",
      overviewTitle: overview ? overview.title : "",
      overviewDescription: overview ? overview.description : "",
      overviewEmptyTitle: overview ? overview.emptyTitle : "",
      opportunityOnly,
    });
    if (opportunityOnly) wx.setNavigationBarTitle({ title: "商机待办" });
    else if (overview) wx.setNavigationBarTitle({ title: overview.title });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'tasks')) return;
    if (!getApp().ensureLogin()) return;
    const session=getApp().globalData.session, identity=access.identity(session);
    if(this.taskIdentity && identity!==this.taskIdentity){this.teamFilter=this.memberFilter='all';this.setData({...access.readStateReset('tasks'),taskView:'self',fdeTaskMemberId:'',...completedPeriod({})});}
    this.taskIdentity=identity; this.closed=false;
    this.setData({...access.flags(session,'tasks'),roleName:session.roleName,...(typeof this.taskManagementParams==='function'?{scope:access.scopeLabel(session,'task.read')}:{})});
    if(this.data.taskView==='team'&&!this.data.canViewTeam)this.setData({taskView:'self',fdeTaskMemberId:''});
    return this.loadTasks();
  },
  onHide(){this.closed=true;this.taskSerial=(this.taskSerial||0)+1;this.closeCancellation();},
  onUnload(){this.onHide();},
  onReachBottom(){return this.loadMore();},
  taskParams(){return {tab:this.data.activeTab,overview:this.data.overviewFilter||undefined,
    order:TASK_SORT_OPTIONS[this.data.sortIndex].key,view:this.data.taskView||'self',
    member_id:this.data.fdeTaskMemberId||undefined,team:this.teamFilter==='all'?undefined:this.teamFilter,
    member:this.memberFilter==='all'?undefined:this.memberFilter,completed_year:this.data.completedYear||undefined,
    completed_quarters:this.data.completedQuarters||[],opportunity_only:this.data.opportunityOnly};},
  taskScopeLabel(params){
    if(params.view!=='team')return '本人';
    if(params.member_id || params.member)return '所选成员';
    if(params.team)return '所选部门';
    return access.scopeLabel(getApp().globalData.session,'task.read');
  },
  loadTasks() {
    this.closeCancellation();
    this.taskSerial=(this.taskSerial||0)+1;
    this.setData({tasks:[],filteredTasks:[],loading:false,loadingMore:false,loadError:'',moreError:'',
      hasMore:false,nextOffset:0,totalCount:0,filteredTotal:0,pendingCount:0,completedCount:0,rejectedCount:0,overdueCount:null,teamMemberCount:null});
    return this.fetchTaskPage(false);
  },
  async fetchTaskPage(more) {
    if(this.closed || this.data.loading || this.data.loadingMore || (more&&!this.data.hasMore))return;
    const serial=this.taskSerial,identity=access.identity(getApp().globalData.session);
    const current=()=>!this.closed && serial===this.taskSerial && identity===access.identity(getApp().globalData.session);
    const offset=more?this.data.nextOffset:0;
    try {
      const webAdapter=typeof this.taskManagementParams==='function';
      const params = webAdapter ? this.taskManagementParams() : this.taskParams();
      this.setData({...(!webAdapter?{scope:this.taskScopeLabel(params)}:{}),...(more?{loadingMore:true,moreError:''}:{loading:true,loadError:''})});
      const result=typeof this.taskManagementParams === 'function' && typeof apiClient.listTaskManagement === 'function'
        ? await apiClient.listTaskManagement({...params,page_size:20,offset})
        : await apiClient.listTaskPage({...params,page_size:20,offset});
      if (!more && typeof apiClient.listTaskManagementOwners === 'function' && typeof this.taskManagementParams === 'function') {
        try {
          const owners = await apiClient.listTaskManagementOwners({...this.taskManagementParams(),page_size:100,offset:0});
          result.team_member_count = Array.isArray(owners?.items) ? owners.items.filter(item => Number(item.task_count) > 0).length : null;
        } catch (_) { result.team_member_count = null; }
      }
      if(!current())return;
      this.acceptTaskPage(result,more,offset);
    } catch(error){if(current())this.setData(more?{moreError:error.message||'下一页加载失败'}:{loadError:error.message||'待办加载失败，请重试'});}
    finally{if(current())this.setData(more?{loadingMore:false}:{loading:false});}
  },
  acceptTaskPage(result,more=false,offset=0){
    const summary=result&&result.summary;
    const management = summary && Number.isInteger(summary.overdue_count) && Number.isInteger(summary.cancelled_count);
    if(!result||!Array.isArray(result.items)||result.items.length>20||result.items.some(t=>!t.id)||
      !summary||!['total','pending_count','completed_count'].every(k=>Number.isInteger(summary[k])&&summary[k]>=0)||
      (!Number.isInteger(summary.filtered_total) && !Number.isInteger(summary.overdue_count)) ||
      typeof result.has_more!=='boolean'||(result.has_more&&(!Number.isInteger(result.next_offset)||result.next_offset<=offset)))throw Error('任务分页数据不完整');
    const items=more?this.data.tasks.slice():[],seen=new Set(items.map(t=>t.id));
    for(const row of result.items)if(!seen.has(row.id)){items.push(normalizeTask(row,getApp().globalData.session));seen.add(row.id);}
    const visible = items.filter(item => {
      if (!management) return true;
      if (this.data.activeTab === 'pending') return item.sourceStatus !== 'completed' && item.sourceStatus !== 'cancelled';
      if (this.data.activeTab === 'completed') return item.sourceStatus === 'completed';
      if (this.data.activeTab === 'rejected') return item.sourceStatus === 'cancelled';
      return true;
    });
    const tabTotal = management ? this.data.activeTab === 'pending' ? summary.pending_count : this.data.activeTab === 'completed' ? summary.completed_count : this.data.activeTab === 'rejected' ? summary.cancelled_count : summary.total : summary.filtered_total;
    this.setData({tasks:visible,filteredTasks:visible,hasMore:result.has_more,nextOffset:result.next_offset,
      pendingCount:summary.pending_count,completedCount:summary.completed_count,rejectedCount:management ? summary.cancelled_count : Math.max(0, summary.total - summary.pending_count - summary.completed_count),
      overdueCount:management ? summary.overdue_count : null,teamMemberCount:management ? result.team_member_count ?? null : null,
      totalCount:summary.total,filteredTotal:tabTotal,webTaskRows:visible});
  },
  loadMore(){return this.fetchTaskPage(true);},
  changeTaskView(e){if(e.currentTarget.dataset.view==='team'&&!this.data.canViewTeam)return;this.setData({taskView:e.currentTarget.dataset.view,fdeTaskMemberId:''});return this.loadTasks();},
  changeSort(e){const index=Number(e.detail.value);if(!Number.isInteger(index)||!TASK_SORT_OPTIONS[index])return;this.setData({sortIndex:index});return this.loadTasks();},
  selectTab(e){if(!['pending','completed','rejected','all'].includes(e.currentTarget.dataset.key))return;this.setData({activeTab:e.currentTarget.dataset.key});return this.loadTasks();},

  openCancellation(e) {
    const session=getApp().globalData.session,identity=access.identity(session);
    const task=this.data.tasks.find(item=>item.id===e.currentTarget.dataset.id);
    if(this.closed || this.taskIdentity!==identity || !task || !canCancelTask({...task,status:task.sourceStatus},session))return;
    this.cancelIdentity=cancellationIdentity(session);
    this.setData({cancelOpen:true,cancelTaskId:task.id,cancelTaskVersion:task.version_no});
  },
  closeCancellation() {
    this.cancelIdentity=null;
    this.setData({cancelOpen:false,cancelTaskId:"",cancelTaskVersion:0});
  },
  taskCancelled(e) {
    const updated=e.detail;
    if(this.closed || !this.data.cancelOpen || this.cancelIdentity!==cancellationIdentity(getApp().globalData.session) ||
       !updated || updated.id!==this.data.cancelTaskId || updated.status!=="cancelled")return;
    wx.showToast({title:"任务已取消，记录已保留",icon:"success"});
    // Reload the filtered page and authoritative counts; never decrement a guessed total.
    return this.loadTasks();
  },
  taskCancellationRefreshed(e) {
    const updated=e.detail;
    if(this.closed || !this.data.cancelOpen || this.cancelIdentity!==cancellationIdentity(getApp().globalData.session) ||
       !updated || updated.id!==this.data.cancelTaskId)return;
    wx.showToast({title:updated.status==="cancelled"&&updated.last_event_type==="cancel"?"该任务已取消，记录已保留":"任务状态已更新",icon:"none"});
    // A fresh read may discover another actor's change or a previously lost response.
    return this.loadTasks();
  },

  openTask(e) {
    wx.navigateTo({ url: `/pages/task-detail/index?id=${encodeURIComponent(e.currentTarget.dataset.id)}` });
  },
  openOpportunity(e) {
    const customerId = e.currentTarget.dataset.customerId;
    const opportunityId = e.currentTarget.dataset.opportunityId;
    if (!customerId || !opportunityId) return;
    wx.setStorageSync("pendingOpenCustomerId", customerId);
    wx.setStorageSync("pendingOpenOpportunityId", opportunityId);
    wx.switchTab({ url: "/pages/customers/index" });
  },
});
