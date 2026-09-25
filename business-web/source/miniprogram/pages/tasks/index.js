/** Task cards request one server-filtered page; summaries describe the full filtered scope. */
const { taskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const { TASK_SORT_OPTIONS } = require("../../utils/taskOverview");

const access = require("../../utils/access");

const OVERVIEW_FILTERS = {
  today_completed: { title: "今日已完成", description: "今天完成的任务，按北京时间统计", emptyTitle: "今天还没有已完成任务" },
  today_pending: { title: "今日待办", description: "今天到期且尚未完成的任务，按北京时间统计", emptyTitle: "今天没有待办任务" },
  all_pending: { title: "全部待办", description: "全部尚未完成的任务，包含今天及其他日期的待办", emptyTitle: "当前没有未完成任务" },
};

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

function normalizeTask(item) {
  const assignees = Array.isArray(item.assignees) ? item.assignees : [];
  const owner = item.owner || item.owner_name || (assignees.find((person) => person.responsibility === "owner") || assignees[0] || {}).name || "待分配";
  const ownerRecord = assignees.find((person) => person.responsibility === "owner") || assignees[0] || {};
  const status = item.status === "completed" ? "completed" : item.status === "cancelled" ? "rejected" : "pending";
  const priorityMap = { high: "高", urgent: "紧急", medium: "中", normal: "普通" };
  const priority = priorityMap[item.priority_code] || item.priority || "普通";
  const priorityClassMap = { 高: "high", 紧急: "urgent", 中: "medium", 普通: "normal" };
  return {
    ...item,
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
  data: {
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
    totalCount: 0, filteredTotal: 0, hasMore: false, nextOffset: 0, loadingMore: false, moreError: "",
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
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'tasks', options)) return;
    this.setData({taskView:options.scope==="team"?"team":"self",fdeTaskMemberId:options.member_id||"",completedYear:Number(options.year)||0,completedQuarters:String(options.quarters||"").split(",").map(Number).filter(q=>q>=1&&q<=4)});
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
    if(this.taskIdentity && identity!==this.taskIdentity){this.teamFilter=this.memberFilter='all';this.setData({taskView:'self',fdeTaskMemberId:''});}
    this.taskIdentity=identity; this.closed=false;
    this.setData({...access.flags(session),roleName:session.roleName,scope:session.scope});
    if(this.data.taskView==='team'&&!this.data.canViewTeam)this.setData({taskView:'self',fdeTaskMemberId:''});
    return this.loadTasks();
  },
  onHide(){this.closed=true;this.taskSerial=(this.taskSerial||0)+1;},
  onUnload(){this.onHide();},
  onReachBottom(){return this.loadMore();},
  taskParams(){return {tab:this.data.activeTab,overview:this.data.overviewFilter||undefined,
    order:TASK_SORT_OPTIONS[this.data.sortIndex].key,view:this.data.taskView||'self',
    member_id:this.data.fdeTaskMemberId||undefined,team:this.teamFilter==='all'?undefined:this.teamFilter,
    member:this.memberFilter==='all'?undefined:this.memberFilter,completed_year:this.data.completedYear||undefined,
    completed_quarters:this.data.completedQuarters||[],opportunity_only:this.data.opportunityOnly};},
  loadTasks() {
    this.taskSerial=(this.taskSerial||0)+1;
    this.setData({tasks:[],filteredTasks:[],loading:false,loadingMore:false,loadError:'',moreError:'',
      hasMore:false,nextOffset:0,totalCount:0,filteredTotal:0,pendingCount:0,completedCount:0});
    return this.fetchTaskPage(false);
  },
  async fetchTaskPage(more) {
    if(this.closed || this.data.loading || this.data.loadingMore || (more&&!this.data.hasMore))return;
    const serial=this.taskSerial,identity=access.identity(getApp().globalData.session);
    const current=()=>!this.closed && serial===this.taskSerial && identity===access.identity(getApp().globalData.session);
    const offset=more?this.data.nextOffset:0;
    this.setData(more?{loadingMore:true,moreError:''}:{loading:true,loadError:''});
    try {
      const result=await apiClient.listTaskPage({...this.taskParams(),page_size:20,offset});
      if(!current())return;
      this.acceptTaskPage(result,more,offset);
    } catch(error){if(current())this.setData(more?{moreError:error.message||'下一页加载失败'}:{loadError:error.message||'待办加载失败，请重试'});}
    finally{if(current())this.setData(more?{loadingMore:false}:{loading:false});}
  },
  acceptTaskPage(result,more=false,offset=0){
    const summary=result&&result.summary;
    if(!result||!Array.isArray(result.items)||result.items.length>20||result.items.some(t=>!t.id)||
      !summary||!['total','pending_count','completed_count','filtered_total'].every(k=>Number.isInteger(summary[k])&&summary[k]>=0)||
      typeof result.has_more!=='boolean'||(result.has_more&&(!Number.isInteger(result.next_offset)||result.next_offset<=offset)))throw Error('任务分页数据不完整');
    const items=more?this.data.tasks.slice():[],seen=new Set(items.map(t=>t.id));
    for(const row of result.items)if(!seen.has(row.id)){items.push(normalizeTask(row));seen.add(row.id);}
    this.setData({tasks:items,filteredTasks:items,hasMore:result.has_more,nextOffset:result.next_offset,
      pendingCount:summary.pending_count,completedCount:summary.completed_count,totalCount:summary.total,filteredTotal:summary.filtered_total});
  },
  loadMore(){return this.fetchTaskPage(true);},
  changeTaskView(e){if(e.currentTarget.dataset.view==='team'&&!this.data.canViewTeam)return;this.setData({taskView:e.currentTarget.dataset.view,fdeTaskMemberId:''});return this.loadTasks();},
  changeSort(e){const index=Number(e.detail.value);if(!Number.isInteger(index)||!TASK_SORT_OPTIONS[index])return;this.setData({sortIndex:index});return this.loadTasks();},
  selectTab(e){if(!['pending','completed','rejected','all'].includes(e.currentTarget.dataset.key))return;this.setData({activeTab:e.currentTarget.dataset.key});return this.loadTasks();},

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
