const {businessChangeLight} = require("../../utils/statusLight");
const {REPORT_ACTIONS,buildReport} = require("../../utils/operatingReport");
const apiClient = require("../../utils/apiClient");
const { mergeVisitReceipts } = require("../../utils/visitCards");
const { buildOverviewMetrics } = require("../../utils/taskOverview");
const homeConfig = require("../../config");
const homeChatBIEnabled = homeConfig.HOME_CHATBI_ENABLED === true;
const homeMessageOrder = homeConfig.HOME_MESSAGE_ORDER === "asc" ? "asc" : "desc";

function accountKey(session) {
  if (!session) return "";
  return `${session.workspaceId || ""}:${session.userId || session.account || session.userName}`;
}

function chatBIContextKey(app) {
  const session = app.globalData.session;
  if (!session) return "";
  return JSON.stringify([
    accountKey(session), app.globalData.role, session.scope || "",
    (session.teamIds || []).slice().sort(), session.loginAt || "", session.permissionVersion || "",
  ]);
}

function taskCardState(status, task = {}) {
  if (task.handover_required) return { value:"待交接",tag:"待交接",tone:"orange",eyebrow:"任务交接",meta:"原接收人资格或项目权限已变化，等待协调处理" };
  if (status === "cancelled" && task.last_event_type === "cancel") return { value:"已取消",tag:"已取消",tone:"orange",eyebrow:"任务取消",meta:task.last_event_note || "任务已取消，协调记录已保留" };
  const rejectionComment = (task.attributes || {}).rejection_comment || ((task.events || []).slice().reverse().find((event) => event.event_type === "reject") || {}).note || "";
  if (["cancelled", "rejected"].includes(status)) return { value: "已拒绝", tag: "已拒绝", tone: "orange", eyebrow: "TASK REJECTED", meta: `拒绝意见：${rejectionComment || "接收方未补充说明"}` };
  if (status === "pending_review") return {value:"待发起人确认",tag:"待验收",tone:"orange",eyebrow:"任务验收",meta:task.completion_note || "完成说明已提交"};
  if (status === "in_progress" && task.last_event_type === "reject_completion") return {value:"执行中 · 已驳回",tag:"待重新提交",tone:"orange",eyebrow:"验收驳回",meta:task.last_event_note || "请补充处理后再次提交"};
  if (status === "completed") return { value: "已完成", tag: "已完成", tone: "green", eyebrow: "TASK COMPLETED", meta: task.completion_note || task.completionNote || "已同步完成结果" };
  if (["pending_execution", "in_progress", "deferred"].includes(status)) return { value: "已接受", tag: "已接受", tone: "orange", eyebrow: "TASK ACCEPTED", meta: "任务已接受，等待执行或完成" };
  return { value: "待接受", tag: "待接受", tone: "orange", eyebrow: "NEW TASK", meta: "" };
}

function taskIdFromMessage(message) {
  if (!message || !message.card) return "";
  return (message.card.action && message.card.action.taskId) || ((message.card.rows || []).find((row) => row.taskId) || {}).taskId || "";
}

function applyLatestTaskState(message, taskMap) {
  const taskId = taskIdFromMessage(message);
  const task = taskId && taskMap && taskMap.get(String(taskId));
  if (!task || !message.card || !Array.isArray(message.card.metrics) || !message.card.metrics.some((metric) => metric.label === "任务状态")) return message;
  const state = taskCardState(task.status, task);
  return {
    ...message,
    card: {
      ...message.card,
      tone: state.tone,
      eyebrow: state.eyebrow,
      metrics: message.card.metrics.map((metric) => metric.label === "任务状态" ? { ...metric, value: state.value } : metric),
      rows: (message.card.rows || []).map((row) => row.taskId === taskId ? { ...row, tag: state.tag, meta: state.meta || row.meta } : row),
    },
  };
}

function formatTime(value, fallback = "") {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  const china = new Date(date.getTime() + 8 * 3600000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${china.getUTCMonth() + 1}月${china.getUTCDate()}日 ${pad(china.getUTCHours())}:${pad(china.getUTCMinutes())}`;
}

function sortHomeMessages(messages, order = homeMessageOrder) {
  return (messages || []).map((message, index) => ({ message, index, timestamp: Number(message.sortAt) || 0 }))
    .sort((left, right) => (order === "asc" ? left.timestamp - right.timestamp : right.timestamp - left.timestamp) || left.index - right.index)
    .map((item) => item.message);
}


function formatAmount(value) {
  const amount = Number(value || 0);
  if (!amount) return "待评估";
  return amount >= 10000 ? `${(amount / 10000).toFixed(amount % 10000 ? 1 : 0)}万` : `${amount.toFixed(0)}元`;
}

Page({
  data: {
    role: "sales",
    roleName: "一线销售",
    scope: "仅本人",
    userName: "",
    greeting: "你好",
    todayLabel: "今天",
    isRecording: false,
    isStarting: false,
    isStopping: false,
    isProcessing: false,
    recordingSeconds: 0,
    recordingTime: "00:00",
    recordingPath: "",
    selectedCustomer: null,
    textInput: "",
    showResult: false,
    managementTaskMode: false,
    visitRecordingMode: false,
    visitTranscriptVisible: false,
    visitTranscript: "",
    visitTranscriptStatus: "",
    visitTranscriptEditable: false,
    showManagementResult: false,
    managementDraft: null,
    isThinking: false,
    initializedRole: "",
    initializedAccount: "",
    messages: [],
    homeChatBIEnabled,
    chatScrollTarget: "",
    quickActions: [],
    overviewMetrics: [],
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'index')) return;
    if (!getApp().ensureLogin()) return;
    this.chatBIPageDisposed = false;
    this.homeVisible = true;
    const enteringContext = chatBIContextKey(getApp());
    const shouldFocusLatest = this.data.initializedAccount !== accountKey(getApp().globalData.session) || !this.data.messages.length;
    const homeReady = this.syncRole();
    this.consumePendingCustomerContext();
    wx.removeStorageSync("lastCreatedOpportunity");
    this.consumeCreatedCustomerSuccess();
    this.consumeManagementCustomerSuccess();
    this.consumeManagementTaskCreated();
    const notificationsReady = this.consumeRemoteTaskNotification();
    Promise.all([homeReady, notificationsReady]).then(() => {
      if (shouldFocusLatest && this.homeVisible && enteringContext === chatBIContextKey(getApp())) this.focusLatestMessage();
    });
    this.startNotificationPolling();
    this.consumeCompletedTask();
    this.consumeResolvedRisk();
  },

  onReady() {
    this.initRecorder();
  },

  onHide() {
    this.homeVisible = false;
    if (this.homeReadVersions) this.homeReadVersions.membershipNavigation = (this.homeReadVersions.membershipNavigation || 0) + 1;
    this.stopNotificationPolling();
    if (this.data.isRecording) this.stopRecord();
  },

  onUnload() {
    this.chatBIPageDisposed = true;
    this.chatBIRequest = null;
    this.notificationReceipts = null;
    this.stopNotificationPolling();
    this.clearRecordTimer();
    if (this.data.isRecording && this.recorderManager) this.recorderManager.stop();
  },

  beginHomeRead(resource) {
    const context = chatBIContextKey(getApp());
    if (this.homeReadContext !== context) {
      const hadContext = this.homeReadContext !== undefined;
      this.homeReadContext = context;
      this.homeReadGeneration = (this.homeReadGeneration || 0) + 1;
      this.homeReadVersions = {};
      this.currentTaskMap = new Map();
      this.notificationReceipts = null;
      if (hadContext) this.setData({ messages: [], overviewMetrics: [], fdeTeamSummary: null });
    }
    const version = (this.homeReadVersions[resource] || 0) + 1;
    this.homeReadVersions[resource] = version;
    return { context, generation: this.homeReadGeneration, resource, version };
  },

  isCurrentHomeContext(request) {
    return !!request && !this.chatBIPageDisposed
      && request.context === chatBIContextKey(getApp())
      && request.generation === this.homeReadGeneration;
  },

  isCurrentHomeRead(request) {
    return this.isCurrentHomeContext(request)
      && this.homeReadVersions[request.resource] === request.version;
  },

  commitHomeRead(request, commit) {
    if (!this.isCurrentHomeRead(request)) return false;
    commit();
    return true;
  },

  async refreshHomeTaskState(taskIds = []) {
    // Both initial loading and polling publish through this one ordered stream.
    // Stage every batch before publishing: a partial failure keeps the last
    // complete card state and summary, while notifications remain independent.
    const request = this.beginHomeRead('task-overview');
    const ids = [...new Set(taskIds.filter(Boolean).map(String))];
    const items = [];
    let metrics;
    try {
      for (let offset = 0; offset < Math.max(1, ids.length); offset += 100) {
        if (!this.isCurrentHomeRead(request)) return false;
        const response = await apiClient.getTaskOverview(ids.slice(offset, offset + 100));
        items.push(...response.items);
        metrics = response.metrics;
      }
      return this.commitHomeRead(request, () => {
        const taskMap = new Map(this.currentTaskMap || []);
        ids.forEach(id => taskMap.delete(id));
        items.forEach(item => taskMap.set(String(item.id), item));
        const overviewMetrics = buildOverviewMetrics([]).map(item => ({ ...item, value: String(metrics[item.key]) }));
        const messages = sortHomeMessages(this.data.messages.map(message => applyLatestTaskState(message, taskMap)), this.homeOrder);
        this.currentTaskMap = taskMap;
        this.setData({
          overviewMetrics,
          messages,
        });
      });
    } catch (error) {
      if (this.isCurrentHomeRead(request)) console.error('任务状态暂未更新，将在下次刷新重试', error);
      return false;
    }
  },

  syncRole() {
    const app = getApp();
    const displayRequest = this.beginHomeRead('display');
    if (this.chatBIRequest && !this.isCurrentChatBIRequest(this.chatBIRequest)) {
      this.chatBIRequest = null;
      this.setData({ isProcessing: false });
    }
    const session = app.globalData.session;
    const role = app.globalData.role;
    const roleInfo = app.globalData.roles[role];
    const isSales = role === "sales";
    const now = new Date();
    const hour = now.getHours();
    const greeting = hour < 6 ? "夜深了" : hour < 11 ? "早上好" : hour < 14 ? "中午好" : hour < 19 ? "下午好" : "晚上好";
    const todayLabel = `今天 ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;
    const loginGreetingKey = `${accountKey(session)}:${session.loginAt || "current"}`;
    const shouldShowLoginGreeting = wx.getStorageSync("homeGreetingShownLogin") !== loginGreetingKey;
    const shouldInitialize = this.homeDisplayContext !== displayRequest.context || this.data.initializedRole !== role || this.data.initializedAccount !== accountKey(session) || !this.data.messages.length;
    this.homeDisplayContext = displayRequest.context;
    if (shouldInitialize) this.homeOrder = homeMessageOrder;
    this.setData({
      role,
      isFde:["fde","fde_lead"].includes(role),
      homeChatBIEnabled,
      roleName: roleInfo.name,
      scope: roleInfo.scope,
      userName: session.userName,
      greeting,
      todayLabel,
      initializedRole: role,
      initializedAccount: accountKey(session),
      showResult: false,
      visitRecordingMode: false,
      selectedCustomer: isSales ? this.data.selectedCustomer : null,
      managementTaskMode: isSales || shouldInitialize ? false : this.data.managementTaskMode,
      showManagementResult: isSales || shouldInitialize ? false : this.data.showManagementResult,
      textInput: shouldInitialize ? "" : this.data.textInput,
      quickActions: [
        { label: "客户认领", action: "客户认领", kind: "customer" },
        { label: "记录客户拜访", action: "记录客户拜访", kind: "visit" },
        { label: "创建任务", action: "创建任务", kind: "task" },
      ].filter(item=>!app.can || app.can(item.kind==="customer"?"customer.claim":item.kind==="visit"?"visit.create":"task.create")),
      messages: shouldInitialize ? [] : this.data.messages,
    });
    const taskStateReady = this.refreshHomeTaskState();
    const displayReady = Promise.all([apiClient.getAssistantHome(), apiClient.listCustomers({ pageSize: 1 })]).then(([home, page]) => {
      if (!this.isCurrentHomeRead(displayRequest)) return;
      const policy = home.display_policy;
      const requestedOrder = policy && policy.definition && policy.definition.message_order;
      this.homeOrder = requestedOrder === "asc" || requestedOrder === "desc" ? requestedOrder : homeMessageOrder;
      this.homeDisplayPolicy = policy || null;
      this.setData({ fdeTeamSummary: role === 'fde_lead' ? (home.team_summary || null) : null });
      const firstCustomer = (page.items || [])[0] || null;
      const initialMessages = shouldShowLoginGreeting ? [{
        id: `${role}_greeting_card`,
        from: "agent",
        kind: "greeting",
        time: "刚刚",
        sortAt: now.getTime(),
        card: {
          timeLabel: todayLabel,
          title: `${greeting}，${session.userName}`,
          text: "我是Raccoon SalesBuddy，今天可以帮你查看客户、商机和待办。",
        },
      }] : [];
      const currentMessages = this.data.messages || [];
      const initialMessageIds = new Set([`${role}_greeting_card`]);
      const mergedMessages = [
        ...initialMessages,
        ...currentMessages.filter((item) => !initialMessageIds.has(item.id)),
      ];
      this.setData({
        selectedCustomer: isSales ? (this.data.selectedCustomer || firstCustomer) : null,
        messages: sortHomeMessages(mergeVisitReceipts(mergedMessages, home.archived_visits || []), this.homeOrder),
      }, () => {
        if (!shouldShowLoginGreeting || !this.isCurrentHomeRead(displayRequest)) return;
        wx.setStorageSync("homeGreetingShownLogin", loginGreetingKey);
      });
    }).catch((error) => {
      if (!this.isCurrentHomeRead(displayRequest)) return;
      wx.showToast({ title: error.message || "首页数据加载失败", icon: "none" });
    });
    return Promise.all([displayReady, taskStateReady]);
  },

  openFdeTeamTasks(){wx.navigateTo({url:"/pages/tasks/index?scope=team"});},
  openOverviewTasks(e) {
    const key = e.currentTarget.dataset.key;
    if (!["today_completed", "today_pending", "all_pending"].includes(key)) return;
    wx.navigateTo({ url: `/pages/tasks/index?overview=${key}` });
  },

  tapQuickAction(e) {
    const action = e.currentTarget.dataset.action;
    if (action === "客户认领") {
      this.openCustomerClaim();
      return;
    }
    if (action === "创建任务") {
      if(getApp().can&&!getApp().can("task.create"))return;
      wx.navigateTo({ url: "/pages/management-task-create/index" });
      return;
    }
    if (action === "记录客户拜访") {
      if(getApp().can&&!getApp().can("visit.create"))return;
      wx.navigateTo({ url: "/pages/visit-entry/index" });
      return;
    }
    if (action === "查看今日待办") {
      wx.navigateTo({ url: "/pages/tasks/index?overview=today_pending" });
      return;
    }
    if (action === "查看个人风险") {
      if (this.data.isThinking || this.data.isProcessing) return;
      this.runPersonalRiskAgent();
      return;
    }
    if (REPORT_ACTIONS[action]) {
      if (this.data.isThinking || this.data.isProcessing) return;
      this.runOperatingReport(action);
      return;
    }
    if (this.data.isThinking) return;
    this.setData({ isThinking: true });
    this.runChatBIQuestion(action, true, 0);
    this.setData({ isThinking: false });
  },

  inputText(e) {
    this.setData({ textInput: e.detail.value });
  },

  sendText() {
    if (!homeChatBIEnabled) return;
    const question = String(this.data.textInput || "").trim();
    if (!question || this.data.isThinking || this.data.isProcessing || this.data.isRecording || this.data.isStarting || this.data.isStopping) return;
    this.setData({ textInput: "" });
    this.appendMessage({ id: `text_question_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: question });
    return this.runChatBIQuestion(question, false, 420);
  },

  appendMessage(message, focus = true) {
    const pushedAt = Date.now();
    const nextMessage = { ...message, sortAt: Number(message.sortAt) || pushedAt, time: message.time === "刚刚" ? formatTime(new Date(pushedAt).toISOString(), this.data.todayLabel) : message.time };
    const messages = (this.homeOrder || homeMessageOrder) === "asc" ? [...this.data.messages, nextMessage] : [nextMessage, ...this.data.messages];
    this.setData({ messages: sortHomeMessages(messages, this.homeOrder) }, () => {
      if (focus) this.focusLatestMessage();
    });
  },

  focusLatestMessage() {
    if (this.chatBIPageDisposed) return;
    // Clear the previous target so another request can return to the same
    // latest content after the user has scrolled through older messages.
    this.setData({ chatScrollTarget: "" }, () => {
      if (!this.chatBIPageDisposed) this.setData({ chatScrollTarget: (this.homeOrder || homeMessageOrder) === "asc" ? "chat-latest" : "chat-newest" });
    });
  },

  startVisitRecording() {
    if (this.data.role !== "sales") return;
    if (this.data.visitRecordingMode && this.data.isRecording && !this.data.isStopping) {
      this.stopRecord();
      return;
    }
    if (this.data.isProcessing || this.data.isStarting || this.data.isStopping || this.data.isRecording) {
      wx.showToast({ title: "当前语音任务尚未结束", icon: "none" });
      return;
    }
    this.liveVisitFrames = [];
    this.liveVisitBytes = 0;
    this.lastLiveTranscribedBytes = 0;
    this.lastLiveTranscriptionAt = Date.now();
    this.livePreviewCount = 0;
    this.liveTranscribing = false;
    this.liveTranscriptionPromise = null;
    this.liveTranscriptGeneration = Date.now();
    this.setData({
      visitRecordingMode: true,
      showResult: false,
      visitTranscriptVisible: true,
      visitTranscript: "",
      visitTranscriptStatus: "正在录音 · 较长内容会显示阶段性文字",
      visitTranscriptEditable: false,
    }, () => this.startRecord());
  },

  tapRecordingState() {
    if (this.data.isRecording && !this.data.isStopping) this.stopRecord();
  },

  openCustomerClaim() {
    wx.navigateTo({ url: "/pages/customer-claim/index" });
  },

  stopPropagation() {},

  startManagementTaskFlow() {
    if (!["supervisor","manager"].includes(this.data.role) || (getApp().can && !getApp().can("customer.create"))) return;
    this.setData({ managementTaskMode: true, showManagementResult: false, managementDraft: null, showResult: false });
    this.appendMessage({
      id: `management_voice_guide_${Date.now()}`,
      from: "agent",
      kind: "insight",
      time: "刚刚",
      badge: "语音建档",
      title: "请语音描述要建档并下发的新客户",
      text: "建议包含客户名称、行业、线索来源、商机、预计金额、联系人、负责销售和首次跟进要求。点击底部语音按钮开始录入。",
    });
  },

  consumePendingCustomerContext() {
    const context = wx.getStorageSync("pendingCustomerContext");
    if (!context || !context.customerId) return;
    wx.removeStorageSync("pendingCustomerContext");
    const { normalizeCustomerDetail } = require("../../utils/customerDetail");
    const request = this.beginHomeRead('pendingCustomerContext');
    return apiClient.getCustomerOverview(context.customerId).then((raw) => {
      if (!this.isCurrentHomeRead(request)) return;
      const customer = normalizeCustomerDetail(raw);
      if (context.mode === "record") {
        this.setData({ selectedCustomer: { id: customer.id, name: customer.name } });
        this.appendMessage({ id: `customer_record_${Date.now()}`, from: "agent", kind: "text", time: "刚刚", text: `已选择客户“${customer.name}”。点击底部语音按钮开始录入，本次记录会自动关联该客户。` });
        return;
      }
      this.appendMessage({
      id: `customer_brief_${Date.now()}`,
      from: "agent",
      kind: "data-card",
      time: "刚刚",
      card: {
        tone: customer.risk === "暂无重大风险" ? "green" : "orange",
        eyebrow: "CUSTOMER BRIEF",
        title: `${customer.name} · ${customer.quadrant}`,
        subtitle: customer.nextAction,
        metrics: [
          { value: String(customer.potential), label: "潜力分" },
          { value: String(customer.relationship), label: "关系分" },
          { value: customer.opportunity.amount, label: "在推金额" },
        ],
        rows: [
          { title: customer.opportunity.name, meta: `${customer.opportunity.stage} · 赢单概率 ${customer.opportunity.probability}%`, tag: "商机" },
          { title: customer.risk, meta: customer.riskDetail, tag: "风险" },
        ],
        emptyText: "",
        action: { label: "返回客户资产", code: "open_customers" },
      },
      });
    }).catch((error) => {
      if (this.isCurrentHomeRead(request)) wx.showToast({ title: error.message || "客户数据加载失败", icon: "none" });
    });
  },

  refreshVisitReceipts() {
    if (this.receiptsLoading) return;
    const account = accountKey(getApp().globalData.session);
    this.receiptsLoading = true;
    apiClient.getAssistantHome().then((home) => {
      if (accountKey(getApp().globalData.session) !== account) return;
      this.setData({ messages: sortHomeMessages(mergeVisitReceipts(this.data.messages, home.archived_visits || []), this.homeOrder) });
    }).catch(() => undefined).finally(() => { this.receiptsLoading = false; });
  },

  consumeSavedRecord(key, loader, render) {
    const reference=wx.getStorageSync(key);
    wx.removeStorageSync(key);
    const session=getApp().globalData.session;
    if (!reference || !reference.id || reference.ownerUserId !== session.userId || reference.workspaceId !== session.workspaceId) return Promise.resolve();
    const request=this.beginHomeRead('savedRecord:'+key);
    return loader(reference.id).then(record=>{
      if (!this.isCurrentHomeRead(request) || !record) return;
      render(record);
    }).catch(()=>undefined);
  },
  consumeManagementCustomerSuccess() {
    return this.consumeSavedRecord('lastManagementCustomerSuccess',apiClient.getCustomerOverview,customer=>this.appendCustomerReceipt(customer));
  },
  consumeCreatedCustomerSuccess() {
    return this.consumeSavedRecord('lastCreatedCustomer',apiClient.getCustomerOverview,customer=>this.appendCustomerReceipt(customer));
  },
  appendCustomerReceipt(customer) {
    this.appendMessage({id:`customer_created_${customer.id}`,from:'agent',kind:'data-card',time:'刚刚',card:{
      tone:'green',eyebrow:'CUSTOMER PROFILE',title:'客户资料已保存',
      subtitle:customer.name,
      metrics:[{value:customer.level_code || '未填写',label:'客户优先级'},
        {value:customer.team_name || '未分组',label:'所属团队'},
        {value:customer.owner_name || '待认领',label:'负责销售'}],rows:[],emptyText:'',
      action:{label:'查看客户档案',code:'open_assigned_customer',customerId:customer.id},
    }});
  },
  consumeManagementTaskCreated() {
    return this.consumeSavedRecord('lastManagementTaskCreated',apiClient.getTask,task=>{
      const state=taskCardState(task.status,task);
      this.appendMessage({id:`management_task_created_${task.id}`,from:'agent',kind:'data-card',time:'刚刚',card:{
        tone:state.tone,eyebrow:'TASK SAVED',title:'任务已保存',subtitle:task.title,
        metrics:[{value:task.owner_name || '待确认',label:'任务负责人'},
          {value:state.value,label:'任务状态'},{value:formatTime(task.due_at),label:'截止时间'}],
        rows:[{taskId:task.id,title:task.title,meta:task.description || '',tag:state.tag}],emptyText:'',
        action:{label:'查看任务',code:'open_task_detail',taskId:task.id},
      }});
    });
  },

  consumeRemoteTaskNotification() {
    const session = getApp().globalData.session;
    if (!session || !session.remote || !apiClient.isEnabled()) return;
    if (this.notificationLoading && this.isCurrentHomeRead(this.notificationRequest)) return;
    const request = this.beginHomeRead('notifications');
    this.notificationRequest = request;
    this.notificationLoading = true;
    return apiClient.listNotifications(false).then(response => {
      // Notification receipts are useful even while auxiliary task states fail
      // or time out. Render them first, using only the last complete state map.
      if (!this.commitHomeRead(request, () => this.presentRemoteNotifications(response.items || [], session, request))) return;
      const ids = [...new Set([
        ...(response.items || []).filter(n=>n.object_type==='task').map(n=>n.object_id),
        ...this.data.messages.map(taskIdFromMessage),
      ].filter(Boolean))];
      return this.refreshHomeTaskState(ids);
    }).catch((error) => {
      if (this.isCurrentHomeRead(request)) console.error("通知卡加载失败", error);
    }).finally(() => {
      if (this.notificationRequest === request) {
        this.notificationLoading = false;
        this.notificationRequest = null;
      }
    });
  },

  presentRemoteNotifications(items, session, request) {
    const taskMap = this.currentTaskMap;
    const supported = ["customer_claim","visit_archived","fde_joined","fde_removed","task_reassigned","task_cancelled","task_claimed","task_candidate_declined","business_changed","task_assigned", "task_accepted", "task_rejected", "task_completed", "task_completion_submitted", "task_completion_rejected", "risk_resolved", "customer_assigned", "battle_map_updated"];
    const refreshed = new Map(items.filter(n=>n.template_code === 'business_changed').map(n=>[`remote_${n.id}`,this.buildRemoteNotificationMessage(n,session)]));
    const currentMessages = this.data.messages.map(message => applyLatestTaskState(refreshed.get(message.id) || message, taskMap));
    const existingIds = new Set(currentMessages.map(item => item.id));
    const notifications = items.filter(item => supported.includes(item.template_code) && !existingIds.has(`remote_${item.id}`));
    const newMessages = notifications.map(notification => applyLatestTaskState(this.buildRemoteNotificationMessage(notification, session), taskMap));
    this.setData({ messages: sortHomeMessages([...currentMessages, ...newMessages], this.homeOrder) }, () => {
      // A later poll can supersede this read before the native render callback.
      // Receipts belong to the displayed cards and identity, not the poll version.
      this.confirmDisplayedNotifications(items.filter(item => supported.includes(item.template_code)), request);
    });
    if (notifications.length) wx.vibrateShort({ type: "light" });
  },

  confirmDisplayedNotifications(items, request) {
    if (!this.isCurrentHomeContext(request) || this.homeVisible === false) return;
    let receipts = this.notificationReceipts;
    if (!receipts || receipts.context !== request.context || receipts.generation !== request.generation) {
      receipts = { context: request.context, generation: request.generation,
        pending: new Set(), inFlight: new Set(), succeeded: new Set(), flushing: null };
      this.notificationReceipts = receipts;
    }
    const displayed = new Set(this.data.messages.map(message => message.id));
    for (const item of items) {
      if (!displayed.has(`remote_${item.id}`)) continue;
      if (item.status === 'read') {
        receipts.succeeded.add(item.id);
        receipts.pending.delete(item.id);
      } else if (!receipts.succeeded.has(item.id)) {
        receipts.pending.add(item.id);
      }
    }
    this.flushNotificationReceipts(receipts);
  },

  flushNotificationReceipts(receipts) {
    if (receipts.flushing || this.notificationReceipts !== receipts || !this.isCurrentHomeContext(receipts)) return;
    // Snapshot one attempt per ID. Failures stay pending until the next normal
    // refresh; an unread response while a receipt is in flight cannot duplicate it.
    const pending = [...receipts.pending].filter(id => !receipts.inFlight.has(id));
    if (!pending.length) return;
    let index = 0;
    const active = () => this.notificationReceipts === receipts && this.isCurrentHomeContext(receipts);
    const drain = async () => {
      while (index < pending.length && active()) {
        const id = pending[index++];
        if (!receipts.pending.has(id) || receipts.succeeded.has(id)) continue;
        receipts.inFlight.add(id);
        try {
          await apiClient.markNotificationRead(id);
          if (active()) {
            receipts.pending.delete(id);
            receipts.succeeded.add(id);
          }
        } catch (_) {
          // Keep the displayed receipt pending; do not block or remove its card.
        } finally {
          if (active()) receipts.inFlight.delete(id);
        }
      }
    };
    receipts.flushing = Promise.all(Array.from({ length: Math.min(3, pending.length) }, drain))
      .finally(() => { if (active()) receipts.flushing = null; });
  },

  buildRemoteNotificationMessage(notification, session) {
    if (notification.template_code === 'customer_claim') {
      const payload = notification.payload || {};
      const customerId = notification.object_id || payload.customer_id;
      // These are event receipts, not the customer's current ownership state.
      const approved = notification.title === '客户认领已通过';
      const rejected = notification.title === '客户认领未通过';
      const released = notification.title === '客户已释放';
      const pending = notification.title === '客户认领待审批';
      return {
        id: `remote_${notification.id}`, from: 'agent', kind: 'data-card',
        time: formatTime(notification.created_at, ''),
        sortAt: new Date(notification.created_at || 0).getTime() || 0,
        card: {
          tone: approved ? 'green' : rejected || released ? 'orange' : 'blue',
          eyebrow: '客户认领', title: notification.title || '客户认领动态',
          subtitle: payload.customer_name || '客户信息暂不可用',
          metrics: [{ value: approved ? '已通过' : rejected ? '未通过' : released ? '已释放' : pending ? '待审批' : '状态更新', label: '认领结果' }],
          rows: notification.body ? [{ title: rejected ? '驳回原因' : released ? '释放原因' : '审批说明', meta: notification.body, tag: '' }] : [],
          footer: approved ? '客户档案按当前归属和权限读取' : '',
          action: approved && customerId
            ? { label: '查看客户档案', code: 'open_assigned_customer', customerId }
            : rejected || released
              ? { label: '查看认领名单', code: 'open_customer_claim' } : null,
        },
      };
    }
    if(notification.template_code==='visit_archived') {
      const p=notification.payload||{};
      return {id:`remote_${notification.id}`,from:'agent',kind:'data-card',time:formatTime(notification.created_at,''),sortAt:new Date(notification.created_at||0).getTime()||0,card:{tone:'blue',eyebrow:'跟进记录',title:String(notification.title || '跟进记录已归档').replace(/协同拜访/g, '跟进记录'),subtitle:notification.body,metrics:[],rows:[],footer:'实际参与记录已归档，完整记录按当前资料权限读取',action:{label:'查看跟进记录',code:'open_fde_visit',visitId:notification.object_id||p.visit_id}}};
    }
    if(['fde_joined','fde_removed'].includes(notification.template_code)) {
      const p=notification.payload||{},removed=notification.template_code==='fde_removed';
      return {id:`remote_${notification.id}`,from:'agent',kind:'data-card',time:formatTime(notification.created_at,''),sortAt:new Date(notification.created_at||0).getTime()||0,
        card:{tone:removed?'orange':'blue',eyebrow:'协作关系',title:notification.title,subtitle:notification.body,metrics:[],rows:[],footer:removed?'已保留关系变更回执，详情按当前权限读取':'加入协助名单；具体待办在任务中处理',
          action:{label:'查看当前项目权限',code:'open_fde_membership',opportunityId:p.membership_opportunity_id||p.opportunity_id||(!removed?notification.object_id:'')}}};
    }
    if(['task_reassigned','task_cancelled'].includes(notification.template_code)) {
      return {id:`remote_${notification.id}`,from:'agent',kind:'data-card',time:formatTime(notification.created_at,''),sortAt:new Date(notification.created_at||0).getTime()||0,card:{tone:'orange',eyebrow:'任务协调',title:notification.title,subtitle:notification.body,metrics:[],rows:[],action:{label:'查看任务',code:'open_task_detail',taskId:notification.object_id||(notification.payload||{}).task_id}}};
    }
    if(notification.template_code === 'business_changed') {
      const p=notification.payload || {};
      const changes=Array.isArray(p.changes)?p.changes:[];
      const opportunityCreated=/商机创建成功/.test(notification.title || '') || ['opportunity_created','created'].includes(p.event_type);
      const visibleChanges=opportunityCreated
        ? changes.filter(c=>/商机阶段|ACV|商机金额/.test(c.label || '')).slice(0,2)
        : changes;
      const health=businessChangeLight(p);
      const tone=health.tone;
      return { id:`remote_${notification.id}`, from:'agent', kind:'data-card', time:formatTime(notification.created_at,''), sortAt:new Date(notification.created_at || 0).getTime() || 0,
        card:{ tone, eyebrow:'业务动态', title:health.title || notification.title, subtitle:notification.body,
          statusLabel: health.label,
          metrics:[], rows:visibleChanges.map(c=>({title:c.label,meta:`${c.before} → ${c.after}`,tag:''})).concat(p.change_review ? [{title:p.change_review.source==='rules'?'变化判断':'商机变化评估',meta:health.reason,tag:''}] : []).concat(!opportunityCreated && p.ai_review ? [{title:'客户关系 · AI评估建议',meta:`${p.ai_review.relationship_before === null ? '首次评估' : p.ai_review.relationship_before} → ${p.ai_review.relationship_after}。${p.ai_review.summary}`,tag:''}] : []),
          footer:`${p.actor_name || '同事'}更新`, action:{label:p.opportunity_id ? '查看商机' : '查看客户档案',code:'open_changed_business',customerId:p.customer_id,opportunityId:p.opportunity_id} } };
    }

    if (['task_claimed','task_candidate_declined'].includes(notification.template_code)) {
      const claimed=notification.template_code==='task_claimed',payload=notification.payload||{};
      return {id:`remote_${notification.id}`,from:'agent',kind:'data-card',time:formatTime(notification.created_at,''),sortAt:new Date(notification.created_at||0).getTime()||0,
        card:{tone:claimed?'green':'orange',eyebrow:'岗位待办',title:notification.title,subtitle:notification.body,
          metrics:[{value:claimed?'已领取':payload.status==='cancelled'?'已取消':'待其他人领取',label:'任务状态'}],rows:[],
          action:{label:'查看任务详情',code:'open_task_detail',taskId:notification.object_id||payload.task_id}}};
    }
    if (["task_completion_submitted","task_completion_rejected"].includes(notification.template_code)) {
      const submitted=notification.template_code === "task_completion_submitted",p=notification.payload || {};
      return {id:`remote_${notification.id}`,from:"agent",kind:"data-card",time:formatTime(notification.created_at,""),sortAt:new Date(notification.created_at || 0).getTime() || 0,
        card:{tone:"orange",eyebrow:"任务验收",title:notification.title,subtitle:notification.body,
        metrics:[{value:submitted?"待发起人确认":"执行中 · 已驳回",label:"任务状态"}],
        rows:[{taskId:notification.object_id || p.task_id,title:submitted?"完成说明":"驳回原因",meta:p.note || "",tag:submitted?"待验收":"待重新提交"}],
        action:{label:submitted?"查看并验收":"查看并继续处理",code:"open_task_detail",taskId:notification.object_id || p.task_id}}};
    }
    const completed = notification.template_code === "task_completed";
    const accepted = notification.template_code === "task_accepted";
    const rejected = notification.template_code === "task_rejected";
    const riskResolved = notification.template_code === "risk_resolved";
    const customerAssigned = notification.template_code === "customer_assigned";
    const battleMapUpdated = notification.template_code === "battle_map_updated";
    const payload = notification.payload || {};
    const quadrantName = {
      main_attack: "主攻区",
      customer_asset: "客户资产",
      customer_resource: "客户资源",
      order_driven: "见单打单",
    }[payload.quadrant_code] || "已重新评估";
    const dueLabel = payload.due_at ? formatTime(payload.due_at, "") : "";
    const priorityLabel = { high: "高", urgent: "紧急", medium: "中", normal: "普通" }[payload.priority_code] || "普通";
    const isAssignedTask = notification.template_code === "task_assigned";
    return {
      id: `remote_${notification.id}`,
      from: "agent",
      kind: "data-card",
      time: formatTime(notification.created_at, ""),
      sortAt: new Date(notification.created_at || 0).getTime() || 0,
      card: {
        tone: rejected || accepted ? "orange" : completed || riskResolved || battleMapUpdated ? "green" : "blue",
        eyebrow: battleMapUpdated ? "BATTLE MAP UPDATED" : customerAssigned ? "NEW CUSTOMER ASSIGNED" : riskResolved ? "RISK RESOLVED" : rejected ? "TASK REJECTED" : accepted ? "TASK ACCEPTED" : completed ? "TASK COMPLETED" : "NEW TASK",
        title: notification.title,
        subtitle: battleMapUpdated ? "最新跟进已同步，客户作战位置已重新评估。" : isAssignedTask ? `${payload.creator_name || "任务发起人"}向你下发了新任务，请进入详情确认${payload.target_position ? "领取" : "接受或拒绝"}。` : notification.body,
        metrics: [
          { value: battleMapUpdated ? (payload.potential_score || "--") : customerAssigned ? "新分配" : riskResolved ? "已解除" : rejected ? "已拒绝" : accepted ? "已接受" : completed ? "已完成" : "待接受", label: battleMapUpdated ? "客户潜力" : customerAssigned ? "客户状态" : riskResolved ? "风险状态" : "任务状态" },
          { value: battleMapUpdated ? (payload.relationship_score || "--") : isAssignedTask ? priorityLabel : session.scope, label: battleMapUpdated ? "关系深度" : isAssignedTask ? "优先级" : "数据范围" },
        ],
        rows: [{ taskId: (!riskResolved && !customerAssigned && !battleMapUpdated) ? (notification.object_id || payload.task_id) : "", riskId: riskResolved ? (notification.object_id || payload.risk_id) : "", title: notification.body, meta: battleMapUpdated ? `作战象限：${quadrantName}` : customerAssigned ? (payload.first_action || "请尽快完成首次联系") : riskResolved ? (payload.resolution_note || "已同步风险解除依据") : rejected ? `拒绝意见：${payload.comment || "接收方未补充说明"}` : accepted ? "接收方已确认接受，任务进入执行" : completed ? (payload.completion_note || "已同步完成结果") : dueLabel ? `截止 ${dueLabel}，点击查看并确认` : "点击查看并选择接受或拒绝", tag: battleMapUpdated ? "地图已更新" : customerAssigned ? "新客户" : riskResolved ? "已解除" : rejected ? "已拒绝" : accepted ? "已接受" : completed ? "已完成" : "待确认" }],
        emptyText: "",
        action: battleMapUpdated
          ? { label: "查看客户档案", code: "open_assigned_customer", customerId: notification.object_id || payload.customer_id }
          : customerAssigned
          ? { label: "查看客户档案", code: "open_assigned_customer", customerId: notification.object_id || payload.customer_id }
          : riskResolved
          ? { label: "查看风险详情", code: "open_risk_detail", riskId: notification.object_id || payload.risk_id }
          : { label: "查看任务详情", code: "open_task_detail", taskId: notification.object_id || payload.task_id },
      },
    };
  },

  reconcileTaskCard(message, tasks) {
    const taskMap = new Map((tasks || []).map((item) => [String(item.id), item]));
    return applyLatestTaskState(message, taskMap);
  },

  startNotificationPolling() {
    this.stopNotificationPolling();
    const session = getApp().globalData.session;
    if (!session || !session.remote || !apiClient.isEnabled()) return;
    this.receiptsTimer = setInterval(() => this.refreshVisitReceipts(), 30000);
    this.notificationTimer = setInterval(() => this.consumeRemoteTaskNotification(), 10000);
  },

  stopNotificationPolling() {
    clearInterval(this.receiptsTimer);
    this.receiptsTimer = null;
    if (!this.notificationTimer) return;
    clearInterval(this.notificationTimer);
    this.notificationTimer = null;
  },

  consumeCompletedTask() {
    const taskId = wx.getStorageSync("lastCompletedTaskId");
    if (!taskId) return;
    wx.removeStorageSync("lastCompletedTaskId");
    let changed = false;
    const messages = this.data.messages.map((message) => {
      // Only the generated pending-task summary removes completed rows.
      // Individual notification cards are event receipts, not task counters.
      if (!String(message.id || "").startsWith("today_tasks_result_")) return message;
      if (message.kind !== "data-card" || !message.card || !Array.isArray(message.card.rows)) return message;
      const rows = message.card.rows.filter((row) => row.taskId !== taskId);
      if (rows.length === message.card.rows.length) return message;
      changed = true;
      const managementCount = rows.filter((row) => row.source === "management_task").length;
      const followUpCount = rows.filter((row) => row.source === "visit_follow_up").length;
      return {
        ...message,
        card: {
          ...message.card,
          rows,
          metrics: [
            { label: "待处理", value: String(rows.length) },
            { label: "管理任务", value: String(managementCount) },
            { label: "跟进行动", value: String(followUpCount) },
          ],
          emptyText: rows.length ? "" : "今日待办已全部完成",
        },
      };
    });
    if (changed) this.setData({ messages });
  },

  consumeResolvedRisk() {
    const riskId = wx.getStorageSync("lastResolvedRiskId");
    if (!riskId) return;
    wx.removeStorageSync("lastResolvedRiskId");
    let changed = false;
    const messages = this.data.messages.map((message) => {
      if (message.kind !== "data-card" || !message.card || !Array.isArray(message.card.rows)) return message;
      const rows = message.card.rows.filter((row) => row.riskId !== riskId);
      if (rows.length === message.card.rows.length) return message;
      changed = true;
      const highCount = rows.filter((row) => ["critical", "high"].includes(row.severity)).length;
      const customerCount = new Set(rows.map((row) => row.customer).filter(Boolean)).size;
      return {
        ...message,
        card: {
          ...message.card,
          rows,
          metrics: [
            { label: "待处理", value: String(rows.length) },
            { label: "高风险", value: String(highCount) },
            { label: "涉及客户", value: String(customerCount) },
          ],
          emptyText: rows.length ? "" : "当前个人风险已全部解除",
        },
      };
    });
    if (changed) this.setData({ messages });
  },

  handleCardRow(e) {
    const {visitId,customerId}=e.currentTarget.dataset;
    if(visitId){wx.navigateTo({url:'/pages/visit-detail/index?visit_id='+encodeURIComponent(visitId)+'&customer_id='+encodeURIComponent(customerId||'')});return;}
    const taskId = e.currentTarget.dataset.taskId;
    const riskId = e.currentTarget.dataset.riskId;
    const reportDetailId = e.currentTarget.dataset.reportDetailId;
    if (taskId) {
      wx.navigateTo({ url: `/pages/task-detail/index?id=${encodeURIComponent(taskId)}` });
      return;
    }
    if (riskId) {
      wx.navigateTo({ url: `/pages/risk-detail/index?id=${encodeURIComponent(riskId)}` });
      return;
    }
    if (reportDetailId && this.reportDetailMap && this.reportDetailMap[reportDetailId]) {
      const ref=this.reportDetailMap[reportDetailId];
      wx.navigateTo({url:`/pages/report-detail/index?runId=${encodeURIComponent(ref.runId)}&action=${encodeURIComponent(ref.action)}&section=${encodeURIComponent(ref.sectionKey)}&row=${ref.rowIndex}`});
    }
  },

  handleMetricAction(e) {
    const action = e.currentTarget.dataset.action;
    if (action === "open_completed_tasks") {
      wx.navigateTo({ url: "/pages/tasks/index?tab=completed" });
      return;
    }
    if (action === "open_unfinished_tasks") {
      wx.navigateTo({ url: "/pages/tasks/index?tab=pending" });
    }
  },

  handleCardAction(e) {
    if(e.currentTarget.dataset.action==='open_fde_visit') {
      const id=e.currentTarget.dataset.visitId;if(!id)return;const identity=chatBIContextKey(getApp());
      apiClient.getVisit(id).then(visit=>{if(identity!==chatBIContextKey(getApp()))return;wx.navigateTo({url:`/pages/visit-detail/index?visit_id=${encodeURIComponent(id)}&customer_id=${encodeURIComponent(visit.customer_id||'')}`});}).catch(error=>wx.showModal({title:'完整记录暂不可用',content:error.message||'当前无完整资料权限，可在协同记录查看参与摘要。',showCancel:false}));return;
    }
    if(e.currentTarget.dataset.action==='open_fde_membership') {
      const id = e.currentTarget.dataset.opportunityId;
      if (!id) return;
      const request = this.beginHomeRead('membershipNavigation');
      const isCurrent = () => this.homeVisible !== false && this.isCurrentHomeRead(request);
      return apiClient.getOpportunityDetailOverview(id).then(raw => {
        if (!isCurrent()) return;
        wx.navigateTo({ url: `/pages/customer-assets/index?customer_id=${encodeURIComponent(raw.id)}&opportunity_id=${encodeURIComponent(id)}&period=all&readonly=1` });
      }).catch(error => {
        if (!isCurrent()) return;
        wx.showModal({ title: '项目详情暂不可用', content: error.message || '协作关系已结束，当前没有完整资料权限。', showCancel: false });
      });
    }
    const action = e.currentTarget.dataset.action;
    if (["open_archived_customer", "open_assigned_customer"].includes(action)) {
      const customerId = e.currentTarget.dataset.customerId;
      if (customerId) wx.setStorageSync("pendingOpenCustomerId", customerId);
      wx.switchTab({ url: "/pages/customers/index" });
      return;
    }
    if(action === 'open_changed_business') {
      const customerId=e.currentTarget.dataset.customerId;
      const opportunityId=e.currentTarget.dataset.opportunityId;
      if(customerId&&opportunityId){
        wx.navigateTo({url:`/pages/customer-assets/index?customer_id=${encodeURIComponent(customerId)}&opportunity_id=${encodeURIComponent(opportunityId)}&period=all&readonly=1`});
      }else if(customerId){
        wx.setStorageSync('pendingOpenCustomerId',customerId);
        wx.switchTab({url:'/pages/customers/index'});
      }
      return;
    }
    if (action === "review_opportunity") {
      const customerId = e.currentTarget.dataset.customerId;
      if (customerId) wx.navigateTo({ url: `/pages/opportunity-create/index?customerId=${encodeURIComponent(customerId)}` });
      return;
    }
    if (action === "open_customers") {
      wx.switchTab({ url: "/pages/customers/index" });
      return;
    }
    if (action === "open_workbench") {
      wx.switchTab({ url: "/pages/bi/index" });
      return;
    }
    if (action === "open_tasks") {
      wx.navigateTo({ url: "/pages/tasks/index" });
      return;
    }
    if (action === "open_task_detail") {
      const taskId = e.currentTarget.dataset.taskId;
      if (taskId) wx.navigateTo({ url: `/pages/task-detail/index?id=${encodeURIComponent(taskId)}` });
      return;
    }
    if (action === "open_risks") {
      wx.navigateTo({ url: "/pages/risks/index" });
      return;
    }
    if (action === "open_risk_detail") {
      const riskId = e.currentTarget.dataset.riskId;
      if (riskId) wx.navigateTo({ url: `/pages/risk-detail/index?id=${encodeURIComponent(riskId)}` });
      return;
    }
    if (action === "open_customer_claim") {
      this.openCustomerClaim();
      return;
    }
    if (action === "open_task_plan") {
      wx.navigateTo({ url: "/pages/tasks/index" });
      return;
    }
    if (action === "create_management_task") {
      wx.navigateTo({ url: "/pages/management-task-create/index" });
    }
  },

  openWorkbench() {
    wx.switchTab({ url: "/pages/bi/index" });
  },

  initRecorder() {
    if (this.recorderManager || !wx.getRecorderManager) return;
    this.recorderManager = wx.getRecorderManager();
    this.recorderManager.onStart(() => {
      if (!this.recorderOwnerActive) return;
      this.setData({ isRecording: true, isStarting: false, isStopping: false, isProcessing: false, showResult: false, showManagementResult: false, recordingSeconds: 0, recordingTime: "00:00", recordingPath: "" });
      this.startRecordTimer();
      wx.vibrateShort({ type: "light" });
    });
    if (this.recorderManager.onFrameRecorded) {
      this.recorderManager.onFrameRecorded((frame) => {
        if (!this.data.visitRecordingMode || !this.data.isRecording || !frame.frameBuffer) return;
        this.liveVisitFrames = [...(this.liveVisitFrames || []), frame.frameBuffer];
        this.liveVisitBytes = (this.liveVisitBytes || 0) + frame.frameBuffer.byteLength;
        this.transcribeLiveVisitFrames();
      });
    }
    this.recorderManager.onStop((result) => {
      if (!this.recorderOwnerActive) return;
      this.recorderOwnerActive = false;
      this.clearRecordTimer();
      const duration = result.duration || this.data.recordingSeconds * 1000;
      const recordingTime = this.formatRecordingTime(Math.max(1, Math.round(duration / 1000)));
      this.setData({ isRecording: false, isStarting: false, isStopping: false, recordingTime, recordingPath: result.tempFilePath || "" });
      if (duration < 800) {
        this.setData({ visitRecordingMode: false });
        wx.showToast({ title: "录音时间太短，请重新录入", icon: "none" });
        return;
      }
      if (this.data.role === "sales" && this.data.visitRecordingMode) {
        wx.setStorageSync("pendingVisitAudio", {
          tempFilePath: result.tempFilePath || "",
          duration,
          recordedAt: Date.now(),
          customerId: this.data.selectedCustomer ? this.data.selectedCustomer.id : "",
          customerName: this.data.selectedCustomer ? this.data.selectedCustomer.name : "",
        });
        this.processVisitRecording(result);
        return;
      }
      this.processChatBIRecording(result, recordingTime);
    });
    this.recorderManager.onError((error) => {
      if (!this.recorderOwnerActive) return;
      this.recorderOwnerActive = false;
      this.clearRecordTimer();
      this.setData({ isRecording: false, isStarting: false, isStopping: false, isProcessing: false, visitRecordingMode: false });
      wx.showToast({ title: error.errMsg || "录音失败，请重试", icon: "none" });
    });
  },

  startRecord() {
    if (!homeChatBIEnabled && !this.data.managementTaskMode && !this.data.visitRecordingMode) return;
    if (this.data.isThinking || this.data.isProcessing) {
      wx.showToast({ title: "正在处理上一条请求，请稍候", icon: "none" });
      return;
    }
    if (this.data.isStarting) return;
    if (this.data.isStopping) return;
    if (this.data.isRecording) {
      this.stopRecord();
      return;
    }
    this.setData({ isStarting: true });
    this.requestRecordPermission(() => {
      this.initRecorder();
      if (!this.recorderManager) {
        this.setData({ isStarting: false, visitRecordingMode: false });
        wx.showToast({ title: "当前微信版本不支持录音", icon: "none" });
        return;
      }
      this.recorderOwnerActive = true;
      this.recorderManager.start({
        duration: 240000,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
        format: "mp3",
        frameSize: 32,
      });
    }, () => this.setData({ isStarting: false, visitRecordingMode: false }));
  },

  stopRecord() {
    if (!this.recorderManager || !this.data.isRecording) return;
    this.setData({ isStopping: true });
    this.recorderManager.stop();
  },

  requestRecordPermission(onGranted, onDenied) {
    wx.getSetting({
      success: (setting) => {
        const permission = setting.authSetting["scope.record"];
        if (permission === true) {
          onGranted();
          return;
        }
        if (permission === false) {
          wx.showModal({
            title: "需要麦克风权限",
            content: this.data.visitRecordingMode ? "语音录入客户拜访需要使用麦克风，请在设置中允许录音权限。" : "语音问数需要使用麦克风，请在设置中允许录音权限。",
            confirmText: "去设置",
            confirmColor: "#1677FF",
            success: (res) => {
              if (!res.confirm) {
                onDenied();
                return;
              }
              wx.openSetting({
                success: (openResult) => {
                  if (openResult.authSetting["scope.record"]) onGranted();
                  else onDenied();
                },
                fail: onDenied,
              });
            },
          });
          return;
        }
        wx.authorize({
          scope: "scope.record",
          success: onGranted,
          fail: () => {
            onDenied();
            wx.showToast({ title: "未获得麦克风权限", icon: "none" });
          },
        });
      },
      fail: () => {
        onDenied();
        wx.showToast({ title: "无法读取录音权限", icon: "none" });
      },
    });
  },

  startRecordTimer() {
    this.clearRecordTimer();
    this.recordTimer = setInterval(() => {
      const recordingSeconds = this.data.recordingSeconds + 1;
      this.setData({ recordingSeconds, recordingTime: this.formatRecordingTime(recordingSeconds) });
    }, 1000);
  },

  clearRecordTimer() {
    if (!this.recordTimer) return;
    clearInterval(this.recordTimer);
    this.recordTimer = null;
  },

  formatRecordingTime(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds % 60;
    return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  },

  processVisitRecording(result) {
    const generation = this.liveTranscriptGeneration;
    this.setData({ isProcessing: true, showResult: false, visitTranscriptStatus: "正在完成最终语音转写…" });
    const pendingLive = this.liveTranscriptionPromise || Promise.resolve();
    pendingLive.catch(() => null).then(() => apiClient.transcribeAudio(result.tempFilePath, "visit_entry"))
      .then((transcript) => {
        if (generation !== this.liveTranscriptGeneration) return;
        const text = String(transcript.text || "").trim();
        if (!text) throw new Error("未识别到有效的拜访内容");
        this.setData({
          isProcessing: false,
          visitRecordingMode: false,
          visitTranscriptVisible: true,
          visitTranscript: text,
          visitTranscriptStatus: "转写完成 · 可手动编辑后提交",
          visitTranscriptEditable: true,
        });
        wx.vibrateShort({ type: "light" });
      })
      .catch((error) => {
        this.setData({ isProcessing: false, showResult: false, visitRecordingMode: false, visitTranscriptStatus: "转写失败，可重新录音" });
        wx.showToast({ title: error.message || "拜访语音解析失败", icon: "none" });
      });
  },

  transcribeLiveVisitFrames() {
    if (this.liveTranscribing || (this.livePreviewCount || 0) >= 3 || !this.liveVisitFrames || !this.liveVisitFrames.length) return;
    const generation = this.liveTranscriptGeneration;
    const now = Date.now();
    const total = this.liveVisitBytes || this.liveVisitFrames.reduce((sum, buffer) => sum + buffer.byteLength, 0);
    // SenseAudio's file endpoint is not a streaming socket. Limit preview calls
    // to roughly one complete 10-second window instead of re-uploading every few seconds.
    if (total - (this.lastLiveTranscribedBytes || 0) < 64000 || now - (this.lastLiveTranscriptionAt || 0) < 10000) return;
    const frames = this.liveVisitFrames.slice();
    const merged = new Uint8Array(total);
    let offset = 0;
    frames.forEach((buffer) => { merged.set(new Uint8Array(buffer), offset); offset += buffer.byteLength; });
    const path = `${wx.env.USER_DATA_PATH}/live-visit-${generation}.mp3`;
    this.liveTranscribing = true;
    this.livePreviewCount = (this.livePreviewCount || 0) + 1;
    this.lastLiveTranscribedBytes = total;
    this.lastLiveTranscriptionAt = now;
    this.liveTranscriptionPromise = new Promise((resolve, reject) => {
      wx.getFileSystemManager().writeFile({ filePath: path, data: merged.buffer, success: resolve, fail: reject });
    }).then(() => apiClient.transcribeAudio(path, "visit_entry"))
      .then((transcript) => {
          if (generation !== this.liveTranscriptGeneration || !this.data.isRecording) return;
          const text = String(transcript.text || "").trim();
          if (text) this.setData({ visitTranscript: text, visitTranscriptStatus: "正在聆听并增量转写…" });
      }).catch(() => {
        if (generation === this.liveTranscriptGeneration && this.data.isRecording) this.setData({ visitTranscriptStatus: "正在录音，结束后将完成全文转写" });
      }).finally(() => { this.liveTranscribing = false; });
  },

  inputVisitTranscript(e) {
    this.setData({ visitTranscript: e.detail.value });
  },

  redoVisitRecording() {
    if (this.data.isProcessing) return;
    this.setData({ visitTranscriptVisible: false, visitTranscript: "", visitTranscriptEditable: false });
    this.startVisitRecording();
  },

  submitVisitTranscript() {
    const text = String(this.data.visitTranscript || "").trim();
    if (!text || this.data.isProcessing) {
      wx.showToast({ title: "请先补充拜访文字内容", icon: "none" });
      return;
    }
    const {draftScope}=require("../../utils/draftScope");
    const key=`visitEntryV2:${draftScope(getApp().globalData.session)}`;
    const selected=this.data.selectedCustomer;
    wx.setStorageSync(key,{transcript:text,entryMode:'text',customerId:selected ? selected.id : '',isFirstVisit:false});
    this.setData({visitTranscriptVisible:false});
    wx.navigateTo({url:'/pages/visit-entry/index'});
  },

  processChatBIRecording(result, recordingTime) {
    const request = this.beginChatBIRequest();
    if (!request) return Promise.resolve();
    const session = getApp().globalData.session;
    wx.setStorageSync("pendingChatBIAudio", {
      tempFilePath: result.tempFilePath || "",
      duration: result.duration || 0,
      recordedAt: Date.now(),
      role: session.role,
      scope: session.scope,
    });
    return apiClient.transcribeAudio(result.tempFilePath, "chatbi").then((transcript) => {
      if (!this.isCurrentChatBIRequest(request)) return;
      const question = String(transcript.text || "").trim();
      if (!question) throw new Error("未识别到有效语音内容");
      this.appendMessage({ id: `chatbi_voice_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: `${question}（语音 ${recordingTime}）` });
      return this.runChatBIQuestion(question, false, 0);
    }).catch((error) => {
      if (!this.isCurrentChatBIRequest(request)) return;
      this.chatBIRequest = null;
      this.setData({ isProcessing: false, showResult: true, visitRecordingMode: false });
      wx.showToast({ title: error.message || "语音问数失败", icon: "none" });
    });
  },

  runTodayTasksAgent() {
    if(this.data.isFde){wx.navigateTo({url:"/pages/tasks/index?overview=today_pending"});return;}
    this.setData({ isThinking: true, isProcessing: false, showResult: false });
    this.appendMessage({ id: `today_tasks_question_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: "查看今日待办" });
    apiClient.queryTodayTasks().then((run) => {
      const result = run.result || {};
      const rows = (Array.isArray(result.rows) ? result.rows : []).map((item) => ({
        taskId: item.task_id || item.taskId || "",
        title: item.title || "待办事项",
        meta: item.detail || item.meta || "点击查看任务详情",
        tag: item.tone || "待处理",
        source: item.source || "management_task",
      }));
      (result.pending_candidates||[]).forEach(item=>rows.push({title:item.description||'待补充商机的行动建议',meta:item.customer_name||'',tag:'待补充商机',visitId:item.source_visit_id,customerId:item.customer_id}));
      const card = {
        tone: "cyan",
        eyebrow: "TODAY TASK AGENT · LIVE DATA",
        title: result.title || "今日行动计划",
        subtitle: result.summary || "已结合跟进记录和管理任务按时间整理。",
        metrics: Array.isArray(result.metrics) ? result.metrics : [],
        rows,
        emptyText: rows.length ? "" : "当前没有未完成的待办事项",
        action: { label: "查看全部待办", code: "open_tasks" },
      };
      this.appendMessage({ id: `today_tasks_result_${Date.now()}`, from: "agent", kind: "data-card", time: "刚刚", card });
      wx.vibrateShort({ type: "light" });
    }).catch((error) => {
      this.appendMessage({ id: `today_tasks_error_${Date.now()}`, from: "agent", kind: "text", time: "刚刚", text: error.message || "待办Agent暂时无法生成计划，请稍后重试。" });
    }).finally(() => this.setData({ isThinking: false, isProcessing: false }));
  },

  runPersonalRiskAgent() {
    if(this.data.isFde){wx.navigateTo({url:"/pages/risks/index"});return;}
    this.setData({ isThinking: true, isProcessing: false, showResult: false });
    this.appendMessage({ id: `personal_risks_question_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: "查看个人风险" });
    apiClient.queryPersonalRisks().then((run) => {
      const result = run.result || {};
      const rows = (Array.isArray(result.rows) ? result.rows : []).map((item) => ({
        riskId: item.risk_id || item.riskId || "",
        title: item.title || "客户经营风险",
        meta: item.detail || item.meta || "点击查看风险依据与建议",
        tag: item.tone || "中",
        severity: item.severity || "medium",
        customer: item.customer || "",
      }));
      const card = {
        tone: "orange",
        eyebrow: "PERSONAL RISK AGENT · LIVE DATA",
        title: result.title || "个人客户风险清单",
        subtitle: result.summary || "已结合历史跟进记录完成资深销售视角分析。",
        metrics: Array.isArray(result.metrics) ? result.metrics : [],
        rows,
        emptyText: rows.length ? "" : "当前没有待处理的个人客户风险",
        action: { label: "查看全部风险", code: "open_risks" },
      };
      this.appendMessage({ id: `personal_risks_result_${Date.now()}`, from: "agent", kind: "data-card", time: "刚刚", card });
      wx.vibrateShort({ type: "light" });
    }).catch((error) => {
      this.appendMessage({ id: `personal_risks_error_${Date.now()}`, from: "agent", kind: "text", time: "刚刚", text: error.message || "个人风险Agent暂时无法完成分析，请稍后重试。" });
    }).finally(() => this.setData({ isThinking: false, isProcessing: false }));
  },

  runOperatingReport(action) {
    const config = REPORT_ACTIONS[action];
    if (!config) return;
    this.setData({ isThinking: true, isProcessing: false, showResult: false });
    this.appendMessage({ id: `operating_report_question_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: config.label });
    apiClient.queryOperatingReport(this.data.isFde ? "根据我的FDE协作身份和当前授权范围，总结协助项目变化、本人填写并确认归档的拜访记录及任务进展；不进行销售绩效评分。" : config.prompt).then((run) => {
      const result = run.result || {};
      const rendered = buildReport(run, action);
      const sections = rendered.sections;
      this.reportDetailMap = {...this.reportDetailMap, ...rendered.details};
      const metrics = sections.slice(0, 3).map((section) => ({
        value: String(section.rows.length),
        label: section.title,
      }));
      const reportScope = action.indexOf("personal") >= 0 ? "SALES" : action.indexOf("team") >= 0 ? "DIRECTOR" : "GM";
      const reportPeriod = "INSTANT";
      const card = {
        tone: "cyan",
        eyebrow: `${reportScope} ${reportPeriod} REVIEW · LIVE DATA`,
        title: result.title || config.label,
        subtitle: result.summary || `${result.scope || "当前权限范围"} · ${result.period || "最新业务周期"}`,
        metrics,
        rows: [],
        sections,
        emptyText: "",
        action: null,
      };
      this.appendMessage({ id: `operating_report_result_${Date.now()}`, from: "agent", kind: "data-card", time: "刚刚", card });
      wx.vibrateShort({ type: "light" });
    }).catch((error) => {
      this.appendMessage({ id: `operating_report_error_${Date.now()}`, from: "agent", kind: "text", time: "刚刚", text: error.message || `${config.label}生成失败，请稍后重试。` });
    }).finally(() => this.setData({ isThinking: false, isProcessing: false }));
  },

  beginChatBIRequest() {
    if (!homeChatBIEnabled) return null;
    const contextKey = chatBIContextKey(getApp());
    if (!contextKey || this.chatBIPageDisposed) return null;
    const request = { contextKey };
    this.chatBIRequest = request;
    this.setData({ isProcessing: true, showResult: false }, () => this.focusLatestMessage());
    return request;
  },

  isCurrentChatBIRequest(request) {
    return this.chatBIRequest === request && request.contextKey === chatBIContextKey(getApp());
  },

  runChatBIQuestion(question, appendQuestion, delay) {
    const request = this.beginChatBIRequest();
    if (!request) return Promise.resolve();
    if (appendQuestion) this.appendMessage({ id: `chatbi_question_${Date.now()}`, from: "user", kind: "text", time: "刚刚", text: question }, false);
    return apiClient.queryChatBI(question).then((run) => {
        if (!this.isCurrentChatBIRequest(request)) return;
        const result = run.result || {};
        const card = {
          tone: "cyan",
          eyebrow: "CHATBI · LIVE DATA",
          title: result.title || "经营问数结果",
          subtitle: result.summary || `问题：“${question}”`,
          metrics: Array.isArray(result.metrics) ? result.metrics : [],
          rows: (Array.isArray(result.rows) ? result.rows : []).map((item) => ({
            title: item.title || "分析结论",
            meta: item.detail || item.meta || "",
            tag: item.tone || "事实",
          })),
          emptyText: "当前数据范围内暂无可展示结果",
          action: { label: "查看看板", code: "open_workbench" },
        };
        this.appendMessage({ id: `chatbi_result_${Date.now()}`, from: "agent", kind: "data-card", time: "刚刚", card });
        this.chatBIRequest = null;
        this.setData({ isProcessing: false });
        wx.vibrateShort({ type: "light" });
    }).catch((error) => {
        if (!this.isCurrentChatBIRequest(request)) return;
        this.chatBIRequest = null;
        this.appendMessage({ id: `chatbi_error_${Date.now()}`, from: "agent", kind: "text", time: "刚刚", text: error.message || "问数暂时失败，请稍后重试。" });
        this.setData({ isProcessing: false });
    });
  },

  openVisitConfirm() {
    const customerId = this.data.selectedCustomer ? this.data.selectedCustomer.id : "";
    wx.navigateTo({ url: `/pages/visit-confirm/index${customerId ? `?customerId=${customerId}` : ""}` });
  },
  openManagementCustomerConfirm() {
    wx.navigateTo({ url: "/pages/customer-assign-confirm/index" });
  },
});
