const uiColors = require("../../utils/uiColors");
/** Legacy tasks use creator review; execution_v1 uses its current reviewer. */
const { outcome: taskOutcome, summary: taskSummary } = require('../../utils/taskExecutionPresentation');
const { taskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");
const access = require("../../utils/access");
const { draftScope } = require("../../utils/draftScope");
const { allTaskReassignmentDirectory } = require("../../utils/taskRecipients");
const { canCancelTask, cancellationIdentity } = require("../../utils/taskCancellation");
const { taskTransferSummary, taskStageLabel } = require("../../utils/taskPresentation");
const taskHistory = require('../../utils/taskHistory');
const taskExecutionPage = require("./taskExecutionPage");

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
  const wasCancelled = status === "cancelled" && ["cancel","cancel_task"].includes(item.last_event_type);
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
    stageLabel: taskStageLabel(item),
    wasCancelled,
    cancellationNote: wasCancelled ? item.last_event_note || "未补充取消原因" : "",
    statusLabel: item.handover_required ? "待交接" : wasCancelled ? "已取消" : item.target_position && status==='pending_confirm' ? '待岗位领取' : ({ pending_confirm: "待接受", pending_execution: "待执行", in_progress: item.last_event_type === "reject_completion" ? "执行中 · 已驳回" : "执行中", pending_review: ['execution_v1','execution_v2'].includes(item.execution_mode) ? '等待验收' : item.creator_user_ref_id === session.userId ? "待我确认" : "等待发起人确认", completed: "已完成", cancelled: "已拒绝", deferred: "历史延期" })[status] || "待处理",
    ...(['execution_v2','execution_v3'].includes(item.execution_mode)?taskSummary(item):{}),
    ...taskOutcome(item),
    endNote:item.last_event_note || item.completion_result || item.completion_note || ((item.attributes || {}).rejection_comment) || "原说明见任务记录",
    priority: priorityMap[item.priority_code] || item.priority || "普通",
    dueLabel: formatDate(item.due_at || item.dueAt, item.due),
    createdLabel: formatDate(item.created_at || item.createdAt, "—"),
    completedLabel: ['execution_v1','execution_v2'].includes(item.execution_mode) ? formatDate(item.executionCompletedAt, '完成时间待核对') : formatDate(item.completed_at || item.completedAt, "—"),
    completedBy: item.completed_by_name || item.completedBy || owner,
    completionNote: ['execution_v1','execution_v2'].includes(item.execution_mode) ? item.executionCompletionNote || '当前完成说明暂不可用，请刷新' : item.completion_note || item.completionNote || "未填写完成反馈",
    associationLabel: item.association_kind === "legacy_customer" ? "历史客户任务（未关联商机）" : item.customer_id ? "客户任务" : "日常工作任务",
    opportunityLabel: item.opportunity_name || (item.opportunity_id ? "关联商机" : "未关联商机"),
    customer: item.customer_name || item.customer || "日常工作任务",
    team: item.team_name || item.team || ownerPerson.team_name || "",
    sourceLabel: item.task_type === "visit_follow_up" ? "待办Agent从跟进记录提取" : `${creator}下发`,
    relatedNames,
    relatedText: relatedNames.length ? relatedNames.join("、") : "无其他相关方",
    responseLabel: item.target_position ? '领取任务' : '接受任务',
    canRespond:canAccept||canDecline,canAccept,canDecline,
    can_coordinate:item.execution_mode!=='execution_v3'&&item.can_coordinate===true&&['pending_confirm','pending_execution','in_progress','deferred'].includes(status)&&permitted('task.coordinate'),canCancel:canCancelTask(item,session),
    canComplete: item.execution_mode!=='execution_v3'&&!item.handover_required && permitted("task.complete") && ["pending_execution", "in_progress"].includes(status) && Boolean(session.userId) && ownerPerson.user_id === session.userId,
    canReview: permitted("task.review") && status === "pending_review" && Boolean(session.userId) && item.creator_user_ref_id === session.userId,
    selfAssigned: ['execution_v2','execution_v3'].includes(item.execution_mode)?item.self_managed===true:ownerPerson.user_id === item.creator_user_ref_id,
    reviewRejected: status === "in_progress" && item.last_event_type === "reject_completion",
    reviewNote: item.completion_review_note || "",
    history: taskHistory.fromEvents(item.events).map(e => ({...e,
      actor_name: typeof (e.payload || {}).actor_name === 'string' && e.payload.actor_name.trim() ? e.payload.actor_name.trim() : e.actor_name,
      label: e.label, time:formatDate(e.occurred_at), transferSummary: e.event_type === 'reassign' ? taskTransferSummary(e.payload || {}) : ''})),
    canRetry: status === "cancelled" && !wasCancelled && access.can(session,"task.create") && Boolean(session.userId) && item.creator_user_ref_id === session.userId,
    rejectionComment: (item.last_event_type === "reject" ? item.last_event_note : "") || ((item.attributes || {}).rejection_comment) || ((item.events || []).slice().reverse().find((event) => event.event_type === "reject") || {}).note || "",
    remote: Boolean(item.due_at || item.creator_user_ref_id),
  };
}

Page({
  ...taskExecutionPage.create(apiClient,()=>getApp().globalData.session,normalizeTask),
  confirmRequirementsSheet(){
    if(this.data.executionBusy)return;
    return this.data.requirementsPreview ? this.submitRequirements() : this.previewRequirements();
  },
  editRequirementsSheet(){if(!this.data.executionBusy)this.setData({requirementsPreview:null,requirementsError:''});},
  onInfoTap(e){const task=this.data.task;if(!task||e.detail.key!=='opportunity'||!task.customer_id||!task.opportunity_id)return;wx.setStorageSync('pendingOpenCustomerId',task.customer_id);wx.setStorageSync('pendingOpenOpportunityId',task.opportunity_id);wx.switchTab({url:'/pages/customers/index'});},
  onRejectCompletion(){return this.reviewCompletion({currentTarget:{dataset:{event:'reject_completion'}}});},
  onApproveCompletion(){return this.reviewCompletion({currentTarget:{dataset:{event:'approve_completion'}}});},

  data: { lightweightDialog:null,lightweightActionOptions:[],lightweightDialogError:'',lightweightRecipients:[], execution:null,executionError:"",executionLoading:false,executionBusy:false,executionDialog:null,executionOffline:false,executionContext:"",executionSynced:"", taskId: "", task: null, completionNote: "", responseComment: "", submitting: false, loading: false, cancelOpen:false,
    coordinateOpen:false,coordinateMembers:[],coordinateTeams:[],coordinateDefaultTeamId:'',coordinateSelectedIds:[],coordinateSelectedId:'',coordinateSelectedName:'',
    coordinatePickerOpen:false,coordinateLoading:false,coordinateDirectoryError:'',coordinateError:'',coordinateNote:'' },

  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'task-detail', options)) return;
    this.setData({ taskId: decodeURIComponent(options.id || "") });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'task-detail')) return;
    if (!getApp().ensureLogin()) return;
    this.taskPageVisible=true;
    if(this.taskSessionIdentity===access.identity(getApp().globalData.session)&&this.data.task&&this.hasTaskEditor())return;
    return this.loadTask();
  },
  hasTaskEditor(){return Boolean(this.data.lightweightDialog||this.data.executionDialog||this.data.requirementsDraft||this.data.checklistDraft||this.data.handoverPurpose||this.data.migrationOpen||this.data.coordinateOpen||this.data.cancelOpen||(this.data.task&&this.data.task.canComplete&&String(this.data.completionNote||'').trim())||(this.data.task&&(this.data.task.canRespond||this.data.task.canReview)&&String(this.data.responseComment||'').trim()));},
  refreshTask(){
    if(this.hasTaskEditor()){wx.showToast({title:'请先保存或关闭当前编辑',icon:'none'});return;}
    if(this.data.loading||this.data.executionBusy||this.data.submitting)return;
    return this.loadTask();
  },
  onPullDownRefresh(){return Promise.resolve(this.refreshTask()).finally(()=>{if(typeof wx.stopPullDownRefresh==='function')wx.stopPullDownRefresh();});},

  loadTask() {
    const session = getApp().globalData.session;
    if (!/^[0-9a-f-]{36}$/i.test(this.data.taskId)) { this.showMissing(); return; }
    const identity=access.identity(session),taskId=this.data.taskId,serial=this.loadSerial=(this.loadSerial||0)+1;
    const current=()=>serial===this.loadSerial&&taskId===this.data.taskId&&identity===access.identity(getApp().globalData.session);
    this.coordinateGeneration=(this.coordinateGeneration||0)+1;
    this.actionConfirmation=null;this.taskMutation=null;this.coordinateMutation=null;
    this.setData({ lightweightDialog:null, taskMigration:null,migrationOpen:false,checklistDraft:null,executionHistory:[],executionSubmissions:[],executionHistoryCursors:{},handoverPurpose:"",handoverLoading:false,requirementsDraft:null,requirementsPreview:null,executionBusy:false,execution:null,executionError:"",executionLoading:typeof apiClient.getTaskExecution==='function',executionDialog:null,loading: true,task:null,submitting:this.hasPendingTaskWrites(taskId,identity),coordinateOpen:false,coordinatePickerOpen:false,cancelOpen:false });
    return apiClient.getTask(taskId).then(task=>{if(current()){this.taskIdentity=cancellationIdentity(session);this.taskSessionIdentity=identity;this.setData({task:normalizeTask(task,session)});return this.loadExecution();}}).catch(()=>{
      if(current())this.showMissing();
    }).finally(()=>{if(current())this.setData({loading:false});});
  },

  onHide(){this.handoverSeq=(this.handoverSeq||0)+1;if(typeof clearInterval==='function')clearInterval(this.executionPoll);this.executionSeq=(this.executionSeq||0)+1;this.taskPageVisible=false;this.lightweightRecipientSeq=(this.lightweightRecipientSeq||0)+1;this.loadSerial=(this.loadSerial||0)+1;this.coordinateGeneration=(this.coordinateGeneration||0)+1;this.actionConfirmation=null;this.taskMutation=null;this.coordinateMutation=null;this.setData({loading:false,executionLoading:false,lightweightRecipientsLoading:false,submitting:false,coordinateOpen:false,coordinatePickerOpen:false,cancelOpen:false});},
  onUnload(){this.isUnloading=true;this.onHide();},
  hasPendingTaskWrites(taskId,identity){
    return (this.pendingTaskWrites || []).some(write=>write.taskId===taskId && write.identity===identity);
  },
  beginTaskWrite(taskId,identity){
    const write={taskId,identity};
    this.pendingTaskWrites=(this.pendingTaskWrites || []).concat(write);
    return write;
  },
  finishTaskWrite(write,applied){
    if(!write)return;
    this.pendingTaskWrites=(this.pendingTaskWrites || []).filter(pending=>pending!==write);
    const samePage=!this.isUnloading && this.taskPageVisible!==false && this.data.taskId===write.taskId &&
      (!this.data.task || this.data.task.id===write.taskId) && write.identity===access.identity(getApp().globalData.session);
    if(!samePage)return;
    if(!applied)this.taskWriteRefresh=write;
    if(this.hasPendingTaskWrites(write.taskId,write.identity))return;
    const refresh=this.taskWriteRefresh;
    this.taskWriteRefresh=null;
    // A write can commit after onShow has already read the old version. Its old
    // receipt must not overwrite the page, but one new GET must reconcile it.
    if(refresh && refresh.taskId===write.taskId && refresh.identity===write.identity){
      if(this.data.lightweightDialog){this.loadExecution();return;}
      this.loadTask();return;
    }
    if(!this.taskMutation && !this.coordinateMutation)this.setData({submitting:false});
  },
  showMissing() {
    wx.showToast({ title: "任务不存在或已不可见", icon: "none" });
    setTimeout(() => wx.navigateBack(), 700);
  },

  openCancellation(){
    if(this.data.executionError||this.data.executionLoading)return;
    const task=this.data.task,session=getApp().globalData.session;
    if(!task || this.data.submitting || this.data.loading || !canCancelTask(task,session) || this.taskIdentity!==cancellationIdentity(session))return;
    this.cancelIdentity=cancellationIdentity(session);this.cancelVersion=task.version_no;
    this.coordinateGeneration=(this.coordinateGeneration||0)+1;
    this.setData({cancelOpen:true,coordinateOpen:false,coordinatePickerOpen:false});
  },
  closeCancellation(){this.setData({cancelOpen:false});},
  taskCancelled(event){
    const updated=event.detail,task=this.data.task,session=getApp().globalData.session;
    if(!this.data.cancelOpen || !task || !updated || updated.id!==task.id ||
      this.cancelIdentity!==cancellationIdentity(session) || this.cancelVersion!==task.version_no)return;
    this.loadSerial=(this.loadSerial||0)+1;
    this.setData({task:normalizeTask(updated,session),cancelOpen:false});
    wx.showToast({title:'任务已取消，记录已保留',icon:'success'});
  },
  taskCancellationRefreshed(event){
    const updated=event.detail,task=this.data.task,session=getApp().globalData.session;
    if(!this.data.cancelOpen || !task || !updated || updated.id!==task.id ||
      this.cancelIdentity!==cancellationIdentity(session) || this.cancelVersion!==task.version_no)return;
    this.loadSerial=(this.loadSerial||0)+1;
    this.setData({task:normalizeTask(updated,session),cancelOpen:false});
    wx.showToast({title:updated.status==='cancelled' && updated.last_event_type==='cancel' ? '该任务已取消，记录已保留' : '任务状态已更新',icon:'none'});
  },

  openCoordination(){
    if(this.data.execution || this.data.executionLoading || this.data.executionError)return;
    const task=this.data.task;if(!task||!task.can_coordinate||this.data.submitting)return;
    this.setData({coordinateOpen:true,coordinateMembers:[],coordinateTeams:[],coordinateSelectedId:'',coordinateSelectedName:'',coordinateSelectedIds:[],
      coordinateDefaultTeamId:'',coordinatePickerOpen:false,coordinateNote:'',coordinateError:'',coordinateDirectoryError:'',coordinateLoading:false});
    if(task.can_coordinate)return this.loadCoordinationOptions();
  },
  async loadCoordinationOptions(){
    const task=this.data.task;if(!this.data.coordinateOpen || !task || !task.can_coordinate || this.data.submitting)return;
    const generation=this.coordinateGeneration=(this.coordinateGeneration||0)+1,identity=access.identity(getApp().globalData.session),taskId=task.id,version=task.version_no;
    const current=()=>this.data.coordinateOpen && generation===this.coordinateGeneration && identity===access.identity(getApp().globalData.session) &&
      this.data.task && this.data.task.id===taskId && this.data.task.version_no===version;
    this.setData({coordinateLoading:true,coordinateDirectoryError:'',coordinateMembers:[],coordinateTeams:[],coordinateDefaultTeamId:''});
    try{
      const directory=await allTaskReassignmentDirectory(apiClient,taskId,current);if(!current() || !directory)return;
      const selected=directory.items.find(member=>member.id===this.data.coordinateSelectedId);
      const members=directory.items.map(member=>({...member,teamLabel:member.team_ids.map(id=>directory.teams.find(team=>team.id===id).name).join('、')}));
      this.coordinateIdentity=identity;
      this.setData({coordinateMembers:members,coordinateTeams:directory.teams,coordinateDefaultTeamId:directory.defaults.team_id,
        coordinateSelectedId:selected?selected.id:'',coordinateSelectedIds:selected?[selected.id]:[],coordinateSelectedName:selected?selected.name:''});
    }catch(error){if(current())this.setData({coordinateDirectoryError:error.message||'可交接人员加载失败'});}
    finally{if(current())this.setData({coordinateLoading:false});}
  },
  closeCoordination(){
    if(this.data.submitting)return;
    this.coordinateGeneration=(this.coordinateGeneration||0)+1;this.setData({coordinateOpen:false,coordinatePickerOpen:false});
  },
  openCoordinatePicker(){if(this.data.coordinateOpen && this.data.task && this.data.task.can_coordinate && !this.data.submitting)this.setData({coordinatePickerOpen:true});},
  closeCoordinatePicker(){this.setData({coordinatePickerOpen:false});},
  confirmCoordinateMember(e){
    if(!this.data.coordinateOpen || this.data.submitting || this.data.coordinateLoading || this.data.coordinateDirectoryError ||
        this.coordinateIdentity!==access.identity(getApp().globalData.session))return;
    const ids=e.detail && e.detail.ids;
    const member=Array.isArray(ids) && ids.length===1 && this.data.coordinateMembers.find(item=>item.id===ids[0]);
    if(!member){this.setData({coordinateError:'请选择当前任务允许的接手人员'});return;}
    this.setData({coordinateSelectedId:member.id,coordinateSelectedIds:[member.id],coordinateSelectedName:member.name,coordinatePickerOpen:false,coordinateError:''});
  },
  coordinateNote(e){this.setData({coordinateNote:e.detail.value});},
  coordinate(e){
    const task=this.data.task;if(!task||!this.data.coordinateOpen||this.data.submitting||this.coordinateConfirming||this.actionConfirmation)return;
    const event=e.currentTarget.dataset.event;
    if(event!=='reassign'||!task.can_coordinate)return;
    const note=(this.data.coordinateNote||'').trim(),member=this.data.coordinateMembers.find(item=>item.id===this.data.coordinateSelectedId);
    if(!note){this.setData({coordinateError:'请填写协调原因，保留交接依据'});return;}
    const identity=access.identity(getApp().globalData.session),generation=this.coordinateGeneration,taskId=task.id,version=task.version_no;
    if(event==='reassign' && (this.data.coordinateLoading||this.data.coordinateDirectoryError||!member||this.coordinateIdentity!==identity)){
      this.setData({coordinateError:this.data.coordinateDirectoryError||'请选择当前任务允许的接手人员'});return;
    }
    const current=()=>this.data.coordinateOpen && generation===this.coordinateGeneration && identity===access.identity(getApp().globalData.session) &&
      this.data.task && this.data.task.id===taskId && this.data.task.version_no===version &&
      this.data.task.can_coordinate && access.can(getApp().globalData.session,'task.coordinate');
    this.coordinateConfirming=true;
    wx.showModal({title:event==='reassign'?'确认转交任务？':'确认取消任务？',content:event==='reassign'?`转交给 ${member.name}；原负责人和协调记录会保留。`:'任务会保留取消记录，不会标记为完成。',success:async result=>{
      this.coordinateConfirming=false;
      if(!result.confirm||!current()||this.data.submitting)return;
      if(event==='reassign' && !this.data.coordinateMembers.some(item=>item.id===member.id && item.account_code===member.account_code))return;
      const mutation={};this.coordinateMutation=mutation;this.setData({submitting:true,coordinateError:''});
      const write=this.beginTaskWrite(taskId,identity);let applied=false;
      try{
        const updated=await apiClient.coordinateTask(taskId,{event_type:event,note,version_no:version,...(event==='reassign'?{assignee_account_code:member.account_code}:{})});
        if(!current()||this.coordinateMutation!==mutation)return;
        if(!updated || updated.id!==taskId)throw Error('任务回执不匹配，请刷新后重试');
        this.setData({task:normalizeTask(updated,getApp().globalData.session),coordinateOpen:false,coordinatePickerOpen:false});
        applied=true;
        wx.showToast({title:event==='reassign'?'已转交':'已取消'});
      }catch(error){if(current())this.setData({coordinateError:error.message||'协调失败，请刷新后重试'});}
      finally{if(this.coordinateMutation===mutation){this.coordinateMutation=null;if(!this.isUnloading)this.setData({submitting:false});}this.finishTaskWrite(write,applied);}
    },fail:()=>{this.coordinateConfirming=false;}});
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
  confirmTaskMutation({ capability, permission, modal, submit, onSuccess, errorMessage }) {
    const task=this.data.task,session=getApp().globalData.session;
    if(!task || this.data.loading || this.data.submitting || this.actionConfirmation || this.coordinateConfirming)return;
    const identity=access.identity(session),taskId=task.id,version=task.version_no,serial=this.loadSerial;
    const samePage=()=>!this.isUnloading && serial===this.loadSerial && identity===access.identity(getApp().globalData.session) &&
      this.data.taskId===taskId && this.data.task && this.data.task.id===taskId;
    const current=()=>samePage() && !this.data.loading && this.data.task.version_no===version &&
      this.taskSessionIdentity===identity && access.can(getApp().globalData.session,permission) &&
      normalizeTask(this.data.task,getApp().globalData.session)[capability]===true;
    if(!current())return;
    const confirmation={};this.actionConfirmation=confirmation;
    wx.showModal({...modal,success:result=>{
      if(this.actionConfirmation!==confirmation)return;
      this.actionConfirmation=null;
      if(!result.confirm || !current() || this.data.submitting)return;
      const mutation={};this.taskMutation=mutation;this.setData({submitting:true});
      let write=null,applied=false;
      Promise.resolve().then(()=>{
        if(!current() || this.taskMutation!==mutation)return null;
        write=this.beginTaskWrite(taskId,identity);return submit();
      }).then(updated=>{
        if(!current() || this.taskMutation!==mutation)return;
        if(!updated || updated.id!==taskId)throw Error('任务回执不匹配，请刷新后重试');
        this.setData({task:normalizeTask(updated,getApp().globalData.session)});
        applied=true;
        onSuccess(updated,()=>samePage() && this.data.task.version_no===updated.version_no);
      }).catch(error=>{
        if(current() && this.taskMutation===mutation)wx.showToast({title:error.message || errorMessage,icon:'none'});
      }).finally(()=>{
        if(this.taskMutation===mutation){this.taskMutation=null;if(!this.isUnloading)this.setData({submitting:false});}
        this.finishTaskWrite(write,applied);
      });
    },fail:()=>{if(this.actionConfirmation===confirmation)this.actionConfirmation=null;}});
  },
  respondTask(eventType) {
    if(this.data.execution || this.data.executionLoading || this.data.executionError)return;
    const task = this.data.task;
    if (!task || !(eventType==="accept"?task.canAccept:task.canDecline) || this.data.submitting) return;
    const rejecting = eventType === "reject";
    const taskId=task.id,version=task.version_no,note=this.data.responseComment;
    this.confirmTaskMutation({
      capability:rejecting?'canDecline':'canAccept',permission:rejecting?'task.decline':'task.accept',
      modal:{title: rejecting ? "确认拒绝任务？" : `确认${task.responseLabel}？`,
        content: rejecting ? "拒绝原因会立即推送给任务发起人，原任务将保留记录。" : "接受后任务进入待执行状态。",
        confirmText: rejecting ? "确认拒绝" : task.responseLabel,confirmColor: rejecting ? uiColors.danger : uiColors.success},
      submit:()=>apiClient.respondTask(taskId,eventType,note,version),
      onSuccess:()=>wx.showToast({title:rejecting?'已拒绝并通知发起人':'任务已接受',icon:'success'}),errorMessage:'任务响应失败',
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
    if(this.data.execution || this.data.executionLoading || this.data.executionError)return;
    const task=this.data.task, event=e.currentTarget.dataset.event;
    if(!task || !task.canReview || this.data.submitting || !["approve_completion","reject_completion"].includes(event))return;
    const note=String(this.data.reviewNote || "").trim(), approved=event === "approve_completion";
    if(!approved && !note){wx.showToast({title:"请填写驳回原因",icon:"none"});return;}
    const taskId=task.id,version=task.version_no;
    this.confirmTaskMutation({capability:'canReview',permission:'task.review',
      modal:{title:approved?"确认任务完成？":"驳回完成申请？",content:approved?"确认后任务闭环，并通知接收方。":"接收方将收到原因，继续处理后可再次提交。"},
      submit:()=>apiClient.respondTask(taskId,event,note,version),
      onSuccess:()=>{this.setData({reviewNote:''});wx.showToast({title:approved?'已确认完成':'已驳回并通知',icon:'success'});},
      errorMessage:'操作失败，请刷新重试'});
  },
  markCompleted() {
    if(this.data.execution || this.data.executionLoading || this.data.executionError)return;
    const task = this.data.task;
    if (!task || !task.canComplete || this.data.submitting) return;
    if(!String(this.data.completionNote || "").trim()){wx.showToast({title:"请填写完成说明",icon:"none"});return;}
    const taskId=task.id,version=task.version_no,note=String(this.data.completionNote || '').trim();
    this.confirmTaskMutation({capability:'canComplete',permission:'task.complete',
      modal:{title:task.selfAssigned?'确认任务已完成？':'提交完成申请？',
        content:task.selfAssigned?'自建自领任务将直接完成。':`提交后由 ${task.creator} 确认，验收通过才算完成。`,
        confirmText:task.selfAssigned?'确认完成':'提交完成',confirmColor: uiColors.success},
      submit:()=>apiClient.completeTask(taskId,note,version),
      onSuccess:(completed,current)=>{
        if(completed.status==='completed')wx.setStorageSync('lastCompletedTaskId',taskId);
        wx.showToast({title:completed.status==='completed'?'任务已完成':'已提交，等待确认',icon:'success'});
        wx.vibrateShort({type:'light'});
        setTimeout(()=>{if(current())wx.navigateBack();},700);
      },errorMessage:'更新失败，请稍后重试'});
  },
});
