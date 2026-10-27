/** Task cards request one server-filtered page; summaries describe the full filtered scope. */
const { taskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const { TASK_SORT_OPTIONS } = require("../../utils/taskOverview");
const { progress: taskProgress, outcome: taskOutcome, summary: taskSummary } = require("../../utils/taskExecutionPresentation");
const { createWindowCache, cloneWindowCache, applyWindow, repairWindow, projectWindowCache } = require('../../utils/taskListSync');

const access = require("../../utils/access");
const { canCancelTask, cancellationIdentity } = require("../../utils/taskCancellation");

const OVERVIEW_FILTERS = {
  today_completed: { title: "今日正常完成", description: "今天正常完成的任务，不含停止、历史取消及拒绝，按北京时间统计", emptyTitle: "今天还没有已完成任务" },
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
  const execution=item.execution||{},flags=item.execution_flags||{};
  if(item.execution_mode!=='legacy'&&(Object.prototype.hasOwnProperty.call(item,'executor')||Object.prototype.hasOwnProperty.call(execution,'executor'))){
    const executor=Object.prototype.hasOwnProperty.call(item,'executor')?item.executor:execution.executor;
    item={...item,owner:executor&&executor.name||'待确认',owner_name:executor&&executor.name||'待确认'};
  }
  const reviewer=Object.prototype.hasOwnProperty.call(item,'current_reviewer')?item.current_reviewer:execution.current_reviewer;
  const assignees = Array.isArray(item.assignees) ? item.assignees : [];
  const owner = item.owner || item.owner_name || (assignees.find((person) => person.responsibility === "owner") || assignees[0] || {}).name || "待分配";
  const ownerRecord = assignees.find((person) => person.responsibility === "owner") || assignees[0] || {};
  const outcome = taskOutcome(item);
  const status = item.execution_mode === "execution_v3" ? item.status : item.status === "completed" ? "completed" : ['cancelled','rejected'].includes(item.status) ? "rejected" : "pending";
  const priorityMap = { high: "高", urgent: "紧急", medium: "中", normal: "普通" };
  const priority = priorityMap[item.priority_code] || item.priority || "普通";
  const priorityClassMap = { 高: "high", 紧急: "urgent", 中: "medium", 普通: "normal" };
  return {
    ...item,
    showProgress: ['execution_v1','execution_v2'].includes(item.execution_mode) || [1,2].includes(execution.protocol_version),
    responsibilityLabel:item.self_managed===true||execution.self_managed===true?'本人自办':item.review_required===true||execution.review_required===true?'需整体验收':'',
    progressView: taskProgress(item.progress || execution.progress),
    updatedLabel: formatTime(item.updated_at || execution.updated_at, '更新时间待确认'),
    sourceStatus: item.status,
    canCancel: canCancelTask(item, session),
    signal:taskLight(item),
    owner,
    team: item.team || item.team_name || ownerRecord.team_name || "",
    status,
    statusLabel: ['completed','cancelled'].includes(item.status)?(item.status==='completed'?'已完成':item.last_event_type==='cancel'?'已取消':'已拒绝'):flags.execution_handover_pending?'待接手':flags.paused?'已暂停':item.status==='pending_review'&&reviewer?(reviewer===session.userId?'待我验收':'等待验收'):item.handover_required?"待交接":item.status==="cancelled"&&item.last_event_type==="cancel"?"已取消":({ pending_confirm: "待接受", pending_review: "待发起人确认", pending_execution: "待执行", in_progress: "执行中", deferred: "历史延期", completed: "已完成", cancelled: "已拒绝", rejected: "已拒绝" })[item.status] || "待处理",
    ...(['execution_v2','execution_v3'].includes(item.execution_mode)?taskSummary(item):{}),
    ...outcome,
    priority,
    priorityClass: item.priority_code || priorityClassMap[priority] || "normal",
    dueLabel: formatTime(item.due_at || item.dueAt, item.due),
    completedLabel: formatTime(item.completed_at || item.completedAt, "—"),
    endedLabel: formatTime(outcome.endedAt, "结束时间未记录"),
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
      { key: 'all', label: '全部' },{ key: 'pending', label: '未完成' },
      { key: 'in_progress', label: '进行中' },{ key: 'completed', label: '正常完成' },
      { key: 'deferred', label: '已延期' },{ key: 'needs_adjustment', label: '需调整' },
      { key: 'ended', label: '已结束' },
    ],
    activeTab: 'all', taskSummaryGroup:'',
    tasks: [],
    filteredTasks: [],
    inProgressCount:null,deferredCount:null,needsAdjustmentCount:null,pendingCount: 0,
    completedCount: null, endedCount:null, stoppedCount:null, unknownOutcomeCount:null,
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
    taskSyncState: '', taskSyncError: '', taskLastSyncLabel: '', taskSyncing: false,
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'tasks', options)) return;
    this.setData({taskView:options.scope==="team"?"team":"self",fdeTaskMemberId:options.member_id||"",...completedPeriod(options)});
    this.teamFilter = decodeURIComponent(options.team || "all");
    this.memberFilter = decodeURIComponent(options.member || "all");
    const tab = options.tab==='rejected'?'ended':options.tab;
    const requestedTab = this.data.tabs.some(item=>item.key===tab) ? tab : 'all';
    const overview = OVERVIEW_FILTERS[options.overview];
    const opportunityOnly = options.opportunity === "1";
    this.setData({
      activeTab: overview ? options.overview==='today_completed'?'completed':'pending' : requestedTab,
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
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'tasks')) { this.stopTaskListSync(true); return; }
    if (!getApp().ensureLogin()) { this.stopTaskListSync(true); return; }
    const session=getApp().globalData.session, identity=access.identity(session);
    if(this.taskIdentity && identity!==this.taskIdentity){this.teamFilter=this.memberFilter='all';this.setData({...access.readStateReset('tasks'),tabs:this.data.tabs.map(tab=>({...tab,count:null})),taskView:'self',fdeTaskMemberId:'',...completedPeriod({})});}
    this.taskIdentity=identity; this.closed=false; this.taskPageVisible=true; this.taskSyncStopped=false; this.taskOffline=false;
    this.setData({...access.flags(session,'tasks'),roleName:session.roleName,...(typeof this.taskManagementParams==='function'?{scope:access.scopeLabel(session,'task.read')}:{})});
    if(this.data.taskView==='team'&&!this.data.canViewTeam)this.setData({taskView:'self',fdeTaskMemberId:''});
    this.listenTaskNetwork();
    return this.loadTasks();
  },
  onHide(){this.closed=true;this.taskPageVisible=false;this.stopTaskListSync();this.taskSerial=(this.taskSerial||0)+1;this.setData({loading:false,loadingMore:false,taskSyncing:false});this.closeCancellation();},
  onUnload(){this.onHide();},
  stopTaskListSync(clear=false){
    if(typeof clearTimeout==='function')clearTimeout(this.taskSyncTimer);
    this.taskSyncTimer=null;
    if(this.taskNetworkListener && typeof wx.offNetworkStatusChange==='function')wx.offNetworkStatusChange(this.taskNetworkListener);
    this.taskNetworkListener=null;
    if(clear){this.taskSyncStopped=true;this.taskSerial=(this.taskSerial||0)+1;this.taskLoadedKey=null;this.taskSyncCache=null;this.closeCancellation();this.setData({...access.readStateReset('tasks'),tabs:this.data.tabs.map(tab=>({...tab,count:null})),loading:false,loadingMore:false,taskSyncing:false,taskLastSyncLabel:'',taskSyncState:'unavailable',taskSyncError:'任务查看权限已变化，请重新进入页面'});}
  },
  scheduleTaskListSync(){
    // Task changes are loaded explicitly so an open editor is never interrupted.
    if(typeof clearTimeout==='function')clearTimeout(this.taskSyncTimer);
    this.taskSyncTimer=null;
  },
  refreshTasks(){
    if(this.data.cancelOpen){wx.showToast({title:'请先完成或关闭当前操作',icon:'none'});return;}
    if(this.data.loading||this.data.loadingMore)return;
    return this.loadTasks();
  },
  onPullDownRefresh(){return Promise.resolve(this.refreshTasks()).finally(()=>{if(typeof wx.stopPullDownRefresh==='function')wx.stopPullDownRefresh();});},
  listenTaskNetwork(){
    if(typeof this.taskManagementParams==='function'||typeof wx.onNetworkStatusChange!=='function'||this.taskNetworkListener)return;
    this.taskNetworkListener=event=>{
      if(!this.taskPageVisible||this.closed||this.taskSyncStopped)return;
      this.taskOffline=event.isConnected===false;
      if(this.taskOffline){
        if(typeof clearTimeout==='function')clearTimeout(this.taskSyncTimer);
        this.taskSerial=(this.taskSerial||0)+1;
        this.setData({loading:false,loadingMore:false,taskSyncing:false,taskSyncState:'offline',taskSyncError:'同步已断开，保留最近确认的任务'});
      }else{this.taskSyncFailures=0;this.setData({taskSyncState:'connected',taskSyncError:'网络已恢复，点击刷新查看最新任务'});}
    };
    wx.onNetworkStatusChange(this.taskNetworkListener);
  },
  taskSyncConfirmed(){
    if(typeof this.taskManagementParams==='function')return;
    this.taskSyncFailures=0;this.taskLoadedKey=JSON.stringify(this.taskParams());
    const now=new Date();
    const partial=Boolean(this.taskSyncCache&&this.taskSyncCache.dirty.size);
    this.setData({taskSyncState:partial?'partial':'connected',taskSyncError:partial?'部分已加载任务待重新同步，已保留最近确认的数据':'',taskLastSyncLabel:`${now.getMonth()+1}月${now.getDate()}日 ${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`});
  },
  async taskSyncWindow(){
    const count=this.taskSyncCache?this.taskSyncCache.slots.length:this.data.tasks.length,page_size=Math.max(20,Math.min(100,Math.ceil(count/20)*20));
    if(count<=100||typeof wx.createSelectorQuery!=='function')return {offset:0,page_size};
    // One bounded request refreshes the visible window, never every loaded page.
    let first=0;
    try{first=await new Promise(resolve=>{
      const query=wx.createSelectorQuery().in(this);
      query.selectAll('.task-card').boundingClientRect(rows=>{
        const index=Array.isArray(rows)?rows.findIndex(row=>row.bottom>0):-1;
        resolve(Math.max(0,index));
      }).exec();
    });}catch(_){first=0;}
    const visible=this.data.tasks[first],position=visible&&Number.isInteger(visible.taskListPosition)?visible.taskListPosition:first;
    return {offset:Math.min(position,Math.max(0,count-page_size)),page_size};
  },
  async syncTaskList(options={}){
    if(this.closed||!this.taskPageVisible||this.taskSyncStopped||typeof this.taskManagementParams==='function')return;
    const identity=access.identity(getApp().globalData.session);
    if(!identity||identity!==this.taskIdentity){this.stopTaskListSync(true);return;}
    if(this.taskOffline){this.setData({taskSyncState:'offline',taskSyncError:'同步已断开，保留最近确认的任务'});return;}
    if(this.data.loading||this.data.loadingMore)return;
    if(this.taskSyncFlight&&this.taskSyncFlight.serial===this.taskSerial)return this.taskSyncFlight.promise;
    const params=this.taskParams(),key=JSON.stringify(params),serial=this.taskSerial;
    const current=()=>!this.closed&&this.taskPageVisible&&!this.taskOffline&&!this.taskSyncStopped&&serial===this.taskSerial&&identity===access.identity(getApp().globalData.session)&&key===JSON.stringify(this.taskParams());
    const flight={serial};this.taskSyncFlight=flight;
    this.setData({taskSyncing:true});
    flight.promise=(async()=>{
      try{
        const cache=this.taskSyncCache?cloneWindowCache(this.taskSyncCache):createWindowCache(this.data.tasks,this.data.filteredTotal);
        let window=options.repair&&repairWindow(cache)||await this.taskSyncWindow();if(!current())return;
        let result=await apiClient.listTaskPage({...params,...window});if(!current())return;
        let reads=1;
        // Validate in isolation before replacing any previously confirmed rows.
        this.validateTaskPage(result,window.offset,window.page_size);
        if(!Number.isInteger(result.summary.filtered_total)||result.summary.filtered_total<0)throw Error('任务统计数据不完整');
        if(window.offset>0&&window.offset>=result.summary.filtered_total&&result.summary.filtered_total>0){
          // The current window may disappear when another actor completes/deletes tasks.
          // Rebase once to the remaining tail; never label old cached rows as fresh.
          window={...window,offset:Math.floor(Math.max(0,result.summary.filtered_total-window.page_size)/20)*20};
          result=await apiClient.listTaskPage({...params,...window});if(!current())return;
          reads++;
          this.validateTaskPage(result,window.offset,window.page_size);
          if(!Number.isInteger(result.summary.filtered_total)||result.summary.filtered_total<0||
             (result.summary.filtered_total>0&&window.offset>=result.summary.filtered_total))throw Error('任务列表已变化，请稍后重试');
        }
        applyWindow(cache,result,window,!options.repair);
        const repair=repairWindow(cache);
        if(repair&&reads<2){
          const repaired=await apiClient.listTaskPage({...params,...repair});if(!current())return;
          this.validateTaskPage(repaired,repair.offset,repair.page_size);
          if(!Number.isInteger(repaired.summary.filtered_total)||repaired.summary.filtered_total<0)throw Error('任务统计数据不完整');
          applyWindow(cache,repaired,repair,false);result=repaired;
        }
        const projection=projectWindowCache(cache,result.summary.filtered_total);
        this.acceptTaskPage({...result,...projection},false,-1,Math.max(100,projection.items.length));
        this.taskSyncCache=cache;
        this.setData({loadError:'',moreError:''});this.taskSyncConfirmed();
      }catch(error){if(current()){
        if([401,403].includes(error.statusCode)){this.stopTaskListSync(true);return;}
        this.taskSyncFailures=(this.taskSyncFailures||0)+1;
        this.setData({taskSyncState:'disconnected',taskSyncError:'同步暂时中断，保留最近确认的任务'});
      }}finally{
        if(this.taskSyncFlight===flight)this.taskSyncFlight=null;
        if(identity!==access.identity(getApp().globalData.session)&&!this.closed)this.stopTaskListSync(true);
        if(current()){this.setData({taskSyncing:false});this.scheduleTaskListSync();}
      }
    })();
    return flight.promise;
  },
  onReachBottom(){return this.loadMore();},
  taskParams(){return {tab:['pending','completed','ended'].includes(this.data.activeTab)?this.data.activeTab:this.data.taskSummaryGroup==='progressing'?'pending':'all',status:['in_progress','deferred','needs_adjustment'].includes(this.data.activeTab)?this.data.activeTab:undefined,overview:this.data.overviewFilter||undefined,
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
    this.taskLoadedKey=null;this.taskSyncFailures=0;this.taskSyncCache=null;
    this.setData({tasks:[],filteredTasks:[],tabs:this.data.tabs.map(tab=>({...tab,count:null})),loading:false,loadingMore:false,loadError:'',moreError:'',taskSyncing:false,taskLastSyncLabel:'',taskSyncState:'',taskSyncError:'',
      hasMore:false,nextOffset:0,totalCount:0,filteredTotal:0,inProgressCount:null,deferredCount:null,needsAdjustmentCount:null,pendingCount:0,completedCount:null,endedCount:null,stoppedCount:null,unknownOutcomeCount:null,rejectedCount:0,overdueCount:null,teamMemberCount:null});
    return this.fetchTaskPage(false);
  },
  async fetchTaskPage(more) {
    const requestedSerial=this.taskSerial;
    if(more&&this.taskSyncFlight&&this.taskSyncFlight.serial===this.taskSerial)await this.taskSyncFlight.promise;
    if(more&&requestedSerial!==this.taskSerial)return;
    if(this.closed || this.data.loading || this.data.loadingMore || (more&&!this.data.hasMore))return;
    if(more&&this.taskSyncCache&&this.taskSyncCache.dirty.size)return this.syncTaskList({repair:true});
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
      // Current management responses include the count in the same snapshot.
      // Keep old-server fallback, but never serialize a directory read after
      // every automatic refresh before exposing newer task progress.
      if (!more && !Number.isInteger(result.team_member_count) && typeof apiClient.listTaskManagementOwners === 'function' && typeof this.taskManagementParams === 'function') {
        try {
          const owners = await apiClient.listTaskManagementOwners({...this.taskManagementParams(),page_size:100,offset:0});
          result.team_member_count = Array.isArray(owners?.items) ? owners.items.filter(item => Number(item.task_count) > 0).length : null;
        } catch (_) { result.team_member_count = null; }
      }
      if(!current())return;
      this.acceptTaskPage(result,more,offset);
      this.taskSyncCache=null;
      this.taskSyncConfirmed();
    } catch(error){if(current()){
      if([401,403].includes(error.statusCode)){this.stopTaskListSync(true);return;}
      this.taskSyncFailures=(this.taskSyncFailures||0)+1;
      this.setData({...(more?{moreError:error.message||'下一页加载失败'}:{loadError:error.message||'待办加载失败，请重试'}),taskSyncState:'disconnected',taskSyncError:'同步暂时中断，请稍后重试'});
    }}
    finally{if(current()){this.setData(more?{loadingMore:false}:{loading:false});this.scheduleTaskListSync();}}
  },
  validateTaskPage(result,offset=0,maxItems=20){
    const summary=result&&result.summary;
    if(!result||!Array.isArray(result.items)||result.items.length>maxItems||result.items.some(t=>!t.id)||
      !summary||!['total','pending_count','completed_count'].every(k=>Number.isInteger(summary[k])&&summary[k]>=0)||
      (!Number.isInteger(summary.filtered_total) && !Number.isInteger(summary.overdue_count)) ||
      typeof result.has_more!=='boolean'||(result.has_more&&(!Number.isInteger(result.next_offset)||result.next_offset<=offset)))throw Error('任务分页数据不完整');
  },
  acceptTaskPage(result,more=false,offset=0,maxItems=20){
    this.validateTaskPage(result,offset,maxItems);
    const summary=result.summary;
    if(['in_progress','deferred','needs_adjustment'].includes(this.data.activeTab)&&!Number.isInteger(summary[this.data.activeTab+'_count']))throw Error('当前服务尚未提供四状态统计，请刷新后重试');
    const count=key=>Number.isInteger(summary[key])&&summary[key]>=0?summary[key]:null;
    const ended=count('ended_count'), completed=ended===null?null:count('completed_count');
    if(this.data.activeTab==='ended'&&ended===null)throw Error('当前服务尚未提供已结束统计，请刷新后重试');
    const management = typeof this.taskManagementParams==='function' || Number.isInteger(summary.overdue_count) && Number.isInteger(summary.cancelled_count);
    const items=more?this.data.tasks.slice():[],seen=new Set(items.map(t=>t.id));
    for(const row of result.items)if(!seen.has(row.id)){items.push(normalizeTask(row,getApp().globalData.session));seen.add(row.id);}
    const visible = items.filter(item => {
      if (!management) return true;
      if (['in_progress','deferred','needs_adjustment'].includes(this.data.activeTab)) return item.sourceStatus===this.data.activeTab;
      if (this.data.activeTab === 'pending') return !item.isEnded;
      if (this.data.activeTab === 'completed') return item.isSuccessful;
      if (this.data.activeTab === 'ended') return item.isEnded;
      return true;
    });
    const tabTotal = !management ? summary.filtered_total : this.data.taskSummaryGroup==='progressing' ? summary.pending_count : ['in_progress','deferred','needs_adjustment'].includes(this.data.activeTab) ? summary[this.data.activeTab+'_count'] : management ? this.data.activeTab === 'pending' ? summary.pending_count : this.data.activeTab === 'completed' ? completed : this.data.activeTab === 'ended' ? ended : summary.total : summary.filtered_total;
    const scopeTotal=management && Number.isInteger(summary.scope_total)?summary.scope_total:summary.total;
    const rejected=count(management?'cancelled_count':'rejected_count');
    const counts={all:scopeTotal,pending:summary.pending_count,completed,ended,
      in_progress:summary.in_progress_count,deferred:summary.deferred_count,needs_adjustment:summary.needs_adjustment_count};
    this.setData({tasks:visible,filteredTasks:visible,tabs:this.data.tabs.map(tab=>({...tab,count:counts[tab.key]??null})),hasMore:result.has_more,nextOffset:result.next_offset,
      inProgressCount:summary.in_progress_count??null,deferredCount:summary.deferred_count??null,needsAdjustmentCount:summary.needs_adjustment_count??null,pendingCount:summary.pending_count,completedCount:completed,endedCount:ended,stoppedCount:count('stopped_count'),unknownOutcomeCount:count('unknown_outcome_count'),rejectedCount:rejected,
      overdueCount:Number.isInteger(summary.overdue_count)?summary.overdue_count:null,teamMemberCount:management ? result.team_member_count ?? null : null,
      totalCount:scopeTotal,filteredTotal:Number.isInteger(summary.filtered_total)?summary.filtered_total:tabTotal,webTaskRows:visible});
  },
  loadMore(){return this.fetchTaskPage(true);},
  selectTaskSummary(e){const group=e.currentTarget.dataset.group;if(!['completed','progressing'].includes(group))return;this.setData({activeTab:group==='completed'?'completed':'all',taskSummaryGroup:group});return this.loadTasks();},
  changeTaskView(e){if(e.currentTarget.dataset.view==='team'&&!this.data.canViewTeam)return;this.setData({taskView:e.currentTarget.dataset.view,fdeTaskMemberId:''});return this.loadTasks();},
  changeSort(e){const index=Number(e.detail.value);if(!Number.isInteger(index)||!TASK_SORT_OPTIONS[index])return;this.setData({sortIndex:index});return this.loadTasks();},
  selectTab(e){
    const key=e.currentTarget.dataset.key;if(!this.data.tabs.some(tab=>tab.key===key))return;
    if(this.data.overviewFilter)wx.setNavigationBarTitle({title:this.data.opportunityOnly?'商机待办':'全部待办'});
    this.setData({activeTab:key,taskSummaryGroup:'',overviewFilter:'',overviewTitle:'',overviewDescription:'',overviewEmptyTitle:''});
    return this.loadTasks();
  },

  openCancellation(e) {
    const session=getApp().globalData.session,identity=access.identity(session);
    const task=this.data.tasks.find(item=>item.id===e.currentTarget.dataset.id);
    if(this.closed || this.taskIdentity!==identity || !task || !canCancelTask({...task,status:task.sourceStatus},session))return;
    if(task.execution_mode==='execution_v2'){wx.navigateTo({url:`/pages/task-detail/index?id=${encodeURIComponent(task.id)}`});return;}
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
