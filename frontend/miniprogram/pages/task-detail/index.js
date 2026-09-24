/** Task completion requires creator review, except self-assigned tasks. */
const { taskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const access = require("../../utils/access");
const { draftScope } = require("../../utils/draftScope");
const { allTaskRecipients } = require("../../utils/taskRecipients");

function formatDate(value, fallback) {
  if (!value) return fallback || "—";
  const date = new Date(typeof value === "number" ? value : String(value));
  if (Number.isNaN(date.getTime())) return fallback || String(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function normalizeTask(item, session) {
  const assignees = Array.isArray(item.assignees) ? item.assignees : [];
  const ownerPerson = assignees.find((person) => person.responsibility === "owner") || assignees[0] || {};
  const owner = item.owner || item.owner_name || ownerPerson.name || (item.target_position ? "等待岗位领取" : "待分配");
  const status = item.status;
  const permitted=code=>access.can(session,code)&&(!session.permissions||(item.action_permissions||{})[code]===true);
  const canRespond=!item.handover_required&&status==='pending_confirm'&&Boolean(session.userId)&&(ownerPerson.user_id===session.userId||(Boolean(item.target_position)&&item.requires_action===true));
  const canAccept=canRespond&&permitted('task.accept'), canDecline=canRespond&&permitted('task.decline');
  const wasCancelled = status === "cancelled" && item.last_event_type === "cancel";
  const priorityMap = { high: "高", urgent: "紧急", medium: "中", normal: "普通" };
  const creator = item.creator_name || item.creator || "系统任务";
  const relatedNames = [...new Set([creator, ...assignees.filter((person) => person.responsibility !== "owner").map((person) => person.name), ...(item.relatedParties || [])].filter((name) => name && name !== owner))];
  return {
    ...item,
    signal:taskLight(item),
    owner,
    ownerUserId: ownerPerson.user_id || "",
    creator,
    status,
    wasCancelled,
    cancellationNote: wasCancelled ? item.last_event_note || "未补充取消原因" : "",
    statusLabel: item.handover_required ? "待交接" : wasCancelled ? "已取消" : item.target_position && status==='pending_confirm' ? '待岗位领取' : ({ pending_confirm: "待接受", pending_execution: "已接受", in_progress: item.last_event_type === "reject_completion" ? "执行中 · 已驳回" : "执行中", pending_review: item.creator_user_ref_id === session.userId ? "待我确认" : "等待发起人确认", completed: "已完成", cancelled: "已拒绝", deferred: "已接受" })[status] || "待处理",
    priority: priorityMap[item.priority_code] || item.priority || "普通",
    dueLabel: formatDate(item.due_at || item.dueAt, item.due),
    createdLabel: formatDate(item.created_at || item.createdAt, "—"),
    completedLabel: formatDate(item.completed_at || item.completedAt, "—"),
    completedBy: item.completed_by_name || item.completedBy || owner,
    completionNote: item.completion_note || item.completionNote || "未填写完成反馈",
    associationLabel: item.association_kind === "legacy_customer" ? "历史客户任务（未关联商机）" : item.customer_id ? "客户任务" : "日常工作任务",
    opportunityLabel: item.opportunity_name || (item.opportunity_id ? "关联商机" : "未关联商机"),
    customer: item.customer_name || item.customer || "日常工作任务",
    team: item.team_name || item.team || ownerPerson.team_name || "",
    sourceLabel: item.task_type === "visit_follow_up" ? "待办Agent从跟进记录提取" : `${creator}下发`,
    relatedNames,
    relatedText: relatedNames.length ? relatedNames.join("、") : "无其他相关方",
    responseLabel: item.target_position ? '领取任务' : '接受任务',
    canRespond:canAccept||canDecline,canAccept,canDecline,
    can_coordinate:item.can_coordinate===true&&permitted('task.coordinate'),canCancel:!['completed','cancelled','pending_review'].includes(status)&&permitted('task.cancel'),
    canComplete: !item.handover_required && permitted("task.complete") && ["pending_execution", "in_progress"].includes(status) && Boolean(session.userId) && ownerPerson.user_id === session.userId,
    canReview: permitted("task.review") && status === "pending_review" && Boolean(session.userId) && item.creator_user_ref_id === session.userId,
    selfAssigned: ownerPerson.user_id === item.creator_user_ref_id,
    reviewRejected: status === "in_progress" && item.last_event_type === "reject_completion",
    reviewNote: item.completion_review_note || "",
    history: (item.events || []).filter(e => ["submit_completion", "approve_completion", "reject_completion", "complete"].includes(e.event_type)).map(e => ({...e, label: ({submit_completion:"提交完成",approve_completion:"确认完成",reject_completion:"驳回",complete:"完成"})[e.event_type], time:formatDate(e.occurred_at)})),
    canRetry: status === "cancelled" && !wasCancelled && access.can(session,"task.create") && Boolean(session.userId) && item.creator_user_ref_id === session.userId,
    rejectionComment: (item.last_event_type === "reject" ? item.last_event_note : "") || ((item.attributes || {}).rejection_comment) || ((item.events || []).slice().reverse().find((event) => event.event_type === "reject") || {}).note || "",
    remote: Boolean(item.due_at || item.creator_user_ref_id),
  };
}

Page({
  data: { taskId: "", task: null, completionNote: "", responseComment: "", submitting: false, loading: false },

  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'task-detail', options)) return;
    this.setData({ taskId: decodeURIComponent(options.id || "") });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'task-detail')) return;
    if (!getApp().ensureLogin()) return;
    this.loadTask();
  },

  loadTask() {
    const session = getApp().globalData.session;
    if (!/^[0-9a-f-]{36}$/i.test(this.data.taskId)) { this.showMissing(); return; }
    const identity=access.identity(session),serial=this.loadSerial=(this.loadSerial||0)+1;
    const current=()=>serial===this.loadSerial&&identity===access.identity(getApp().globalData.session);
    this.setData({ loading: true,task:null,coordinateOpen:false });
    return apiClient.getTask(this.data.taskId).then(task=>{if(current())this.setData({task:normalizeTask(task,session)});}).catch(()=>{
      if(current())this.showMissing();
    }).finally(()=>{if(current())this.setData({loading:false});});
  },

  onHide(){this.loadSerial=(this.loadSerial||0)+1;this.setData({coordinateOpen:false});},
  onUnload(){this.onHide();},
  showMissing() {
    wx.showToast({ title: "任务不存在或已不可见", icon: "none" });
    setTimeout(() => wx.navigateBack(), 700);
  },

  async openCoordination(){
    const task=this.data.task;if(!task||!(task.can_coordinate||task.canCancel))return;
    this.setData({coordinateOpen:true,coordinateMembers:[],coordinateIndex:-1,coordinateNote:'',coordinateError:'',coordinateLoading:task.can_coordinate});
    if(!task.can_coordinate)return;
    try{const identity=draftScope(getApp().globalData.session);const current=()=>this.data.coordinateOpen&&identity===draftScope(getApp().globalData.session);const rows=await allTaskRecipients(apiClient,current);if(current())this.setData({coordinateMembers:rows||[],coordinateLoading:false});}catch(e){this.setData({coordinateLoading:false,coordinateError:e.message||'可交接人员加载失败'});}
  },
  closeCoordination(){if(!this.data.submitting)this.setData({coordinateOpen:false});},
  coordinateMember(e){this.setData({coordinateIndex:Number(e.detail.value)});},
  coordinateNote(e){this.setData({coordinateNote:e.detail.value});},
  coordinate(e){
    const task=this.data.task;if(!task||this.data.submitting)return;
    const event=e.currentTarget.dataset.event;if(!(['cancel','reassign'].includes(event))||(event==='cancel'?!task.canCancel:!task.can_coordinate))return;const note=(this.data.coordinateNote||'').trim(),member=(this.data.coordinateMembers||[])[this.data.coordinateIndex];
    if(!note){this.setData({coordinateError:'请填写协调原因，保留交接依据'});return;}
    if(event==='reassign'&&!member){this.setData({coordinateError:'请选择接手人员'});return;}
    wx.showModal({title:event==='reassign'?'确认转交任务？':'确认取消任务？',content:event==='reassign'?`转交给 ${member.name}；原负责人和协调记录会保留。`:'任务会保留取消记录，不会标记为完成。',success:async result=>{if(!result.confirm)return;this.setData({submitting:true,coordinateError:''});try{const updated=await apiClient.coordinateTask(task.id,{event_type:event,note,version_no:task.version_no,...(event==='reassign'?{assignee_account_code:member.account_code}:{})});this.setData({task:normalizeTask(updated,getApp().globalData.session),coordinateOpen:false});wx.showToast({title:event==='reassign'?'已转交':'已取消'});}catch(error){this.setData({coordinateError:error.message||'协调失败，请刷新后重试'});}finally{this.setData({submitting:false});}}});
  },
  inputCompletionNote(e) {
    this.setData({ completionNote: e.detail.value });
  },
  inputResponseComment(e) { this.setData({ responseComment: e.detail.value }); },
  acceptTask() { this.respondTask("accept"); },
  rejectTask() {
    if (!String(this.data.responseComment || "").trim()) {
      wx.showToast({ title: "拒绝任务必须填写原因", icon: "none" });
      return;
    }
    this.respondTask("reject");
  },
  respondTask(eventType) {
    const task = this.data.task;
    if (!task || !(eventType==="accept"?task.canAccept:task.canDecline) || this.data.submitting) return;
    const rejecting = eventType === "reject";
    wx.showModal({
      title: rejecting ? "确认拒绝任务？" : `确认${task.responseLabel}？`,
      content: rejecting ? "拒绝原因会立即推送给任务发起人，原任务将保留记录。" : "接受后任务进入待执行状态。",
      confirmText: rejecting ? "确认拒绝" : task.responseLabel,
      confirmColor: rejecting ? "#D7614E" : "#2B9A70",
      success: (result) => {
        if (!result.confirm) return;
        this.setData({ submitting: true });
        apiClient.respondTask(task.id, eventType, this.data.responseComment,task.version_no).then((updated) => {
          const session = getApp().globalData.session;
          this.setData({ task: normalizeTask(updated, session), submitting: false });
          wx.showToast({ title: rejecting ? "已拒绝并通知发起人" : "任务已接受", icon: "success" });
        }).catch((error) => {
          this.setData({ submitting: false });
          wx.showToast({ title: error.message || "任务响应失败", icon: "none" });
        });
      },
    });
  },

  retryTask() {
    const task = this.data.task;
    if (!task || !task.canRetry) return;
    wx.setStorageSync(`retryTaskDraft:${draftScope(getApp().globalData.session)}`, {
      customerId:task.customer_id || "",opportunityId:task.opportunity_id || "",associationKind:task.association_kind || (task.customer_id?"customer":"daily"),
      description: task.description,
      assigneeId: task.ownerUserId,
      targetPosition: task.target_position || null,
      priority: task.priority,
    });
    wx.navigateTo({ url: "/pages/management-task-create/index?retry=1" });
  },

  inputReviewNote(e) { this.setData({reviewNote:e.detail.value}); },
  reviewCompletion(e) {
    const task=this.data.task, event=e.currentTarget.dataset.event;
    if(!task || !task.canReview || this.data.submitting || !["approve_completion","reject_completion"].includes(event))return;
    const note=String(this.data.reviewNote || "").trim(), approved=event === "approve_completion";
    if(!approved && !note){wx.showToast({title:"请填写驳回原因",icon:"none"});return;}
    wx.showModal({title:approved?"确认任务完成？":"驳回完成申请？",content:approved?"确认后任务闭环，并通知接收方。":"接收方将收到原因，继续处理后可再次提交。",success:result=>{
      if(!result.confirm)return;
      this.setData({submitting:true});
      apiClient.respondTask(task.id,event,note,task.version_no).then(updated=>{
        this.setData({task:normalizeTask(updated,getApp().globalData.session),reviewNote:"",submitting:false});
        wx.showToast({title:approved?"已确认完成":"已驳回并通知",icon:"success"});
      }).catch(error=>{this.setData({submitting:false});wx.showToast({title:error.message || "操作失败，请刷新重试",icon:"none"});});
    }});
  },
  markCompleted() {
    const task = this.data.task;
    const session = getApp().globalData.session;
    if (!task || !task.canComplete || this.data.submitting) return;
    if(!String(this.data.completionNote || "").trim()){wx.showToast({title:"请填写完成说明",icon:"none"});return;}
    wx.showModal({
      title: task.selfAssigned ? "确认任务已完成？" : "提交完成申请？",
      content: task.selfAssigned ? "自建自领任务将直接完成。" : `提交后由 ${task.creator} 确认，验收通过才算完成。`,
      confirmText: task.selfAssigned ? "确认完成" : "提交完成",
      confirmColor: "#2B9A70",
      success: (result) => {
        if (!result.confirm) return;
        this.setData({ submitting: true });
        const note = String(this.data.completionNote || "").trim();
        apiClient.completeTask(task.id, note,task.version_no).then((completed) => {
          this.setData({ task: normalizeTask(completed, session), submitting: false });
          if(completed.status === "completed") wx.setStorageSync("lastCompletedTaskId", task.id);
          wx.showToast({
            title: completed.status === "completed" ? "任务已完成" : "已提交，等待确认",
            icon: "success",
          });
          wx.vibrateShort({ type: "light" });
          setTimeout(() => wx.navigateBack(), 700);
        }).catch((error) => {
          this.setData({ submitting: false });
          wx.showToast({ title: error.message || "更新失败，请稍后重试", icon: "none" });
        });
      },
    });
  },
});
