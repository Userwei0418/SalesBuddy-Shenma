const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { CUSTOMER_LEVELS } = require("../../utils/customerLevel");

const FIELD_EDITORS = {
  customer_name: { type: "text", placeholder: "请输入客户全称" },
  company_reference: { type: "text", placeholder: "请输入公司客户编号或审批单号" },
  industry: { type: "select", options: businessOptions.customer.industry },
  customer_type: { type: "select", options: businessOptions.customer.customer_type },
  level_code: { type: "select", options: CUSTOMER_LEVELS },
  lead_source: { type: "select", options: businessOptions.customer.source },
  target_team: { type: "select", options: [] },
  partner_name: { type: "text", placeholder: "选填，可留空" },
  contact_name: { type: "text", placeholder: "请输入首要联系人姓名" },
  contact_title: { type: "text", placeholder: "请输入联系人职位" },
  contact_role: { type: "select", options: businessOptions.customer.contact_role },
};

function requestTime(value) {
  if (!value) return "";
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) return "";
  const china = new Date(time.getTime() + 8 * 3600000);
  const pad = number => String(number).padStart(2, "0");
  return `${china.getUTCFullYear()}-${pad(china.getUTCMonth() + 1)}-${pad(china.getUTCDate())} ${pad(china.getUTCHours())}:${pad(china.getUTCMinutes())}`;
}

function fieldMissing(field) {
  return Boolean(field.required) && !String(field.value || "").trim();
}

function normalizeFieldValue(key, value) {
  let normalized = String(value || "").trim();
  if (key === "lead_source" && normalized === "公司分配") normalized = "销售线索";
  const config = FIELD_EDITORS[key];
  if (config && config.options && config.options.length && !config.options.includes(normalized)) return "";
  return normalized;
}

Page({
  data: {
    requestMode: false,
    canViewRequests: false,
    canVoiceCreate: false,
    activeTab: "form",
    submitting: false,
    requests: [],
    requestsLoading: false,
    requestsError: "",
    requestsTotal: 0,
    requestsHasMore: false,
    requestsNextOffset: 0,
    withdrawingId: "",
    role: "supervisor",
    roleName: "销售主管",
    creatorName: "",
    fields: [],
    missingCount: 0,
    requiredCount: 0,
    completedRequiredCount: 0,
    progressPercent: 0,
    customerName: "待创建客户",
    draftRestored: false,
    voiceParsed: false,
    isRecording: false,
    isStarting: false,
    isStopping: false,
    isParsing: false,
    recordingSeconds: 0,
    recordingTime: "00:00",
    editorVisible: false,
    editorIndex: -1,
    editorTitle: "",
    editorType: "text",
    editorValue: "",
    editorPlaceholder: "",
    editorOptions: [],
  },

  async onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-create')) return;
    if (!getApp().ensureLogin()) return;
    const app = getApp();
    const session = app.globalData.session;
    const access = require('../../utils/access');
    if (!access.can(session,'customer.create') && !access.can(session,'customer.create_request')) {
      wx.showToast({ title: "当前账号没有客户建档权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 650);
      return;
    }
    this.requestMode = access.can(session, "customer.create_request") && (["sales", "supervisor", "manager"].includes(session.role) || !access.can(session, "customer.create"));
    this.setData({ requestMode: this.requestMode, canViewRequests: access.can(session, "customer.create_request"), canVoiceCreate: access.can(session, "customer.create"), canClaimCustomer: access.can(session, "customer.claim") });
    this.session = session;
    this.draftKey = `customerCreateDraft:${draftScope(session)}`;
    if (wx.setNavigationBarTitle) wx.setNavigationBarTitle({ title: this.requestMode ? "客户建档申请" : "创建客户" });
    if (options.view === "requests" && this.data.canViewRequests) this.showRequests();
    try {await apiClient.getBusinessOptions();} catch(error) {wx.showToast({title:error.message,icon:'none'});return;}
    this.session = session;
    this.teamOptions = [];
    apiClient.getTeamDirectory(this.requestMode ? "creation_request" : "assignment").then((response) => {
      if (!Array.isArray(response.teams)) throw Error("团队目录不可用");
      this.directoryTeams = response.teams;
      this.teamOptions = response.teams.map(team => team.name);
      this.refresh(this.data.fields.map(field => {
        if (field.key !== "target_team") return field;
        const matches = response.teams.filter(team => field.teamId ? team.id === field.teamId : team.name === field.value);
        return matches.length === 1 ? {...field, value:matches[0].name, teamId:matches[0].id}
          : field.readonly ? field : {...field, value:"", teamId:""};
      }));
    }).catch((error) => wx.showToast({ title: error.message || "团队数据加载失败", icon: "none" }));
    this.draftKey = `customerCreateDraft:${draftScope(session)}`;
    const saved = wx.getStorageSync(this.draftKey);
    const canRestore = saved && Array.isArray(saved.fields);
    const defaultTeam = ["sales", "supervisor"].includes(session.role) ? session.team : "";
    const initialFields = [
      { key: "customer_name", label: "客户名称", value: "", required: true },
      { key: "company_reference", label: "公司客户编号 / 审批单号", value: "", required: true },
      { key: "industry", label: "所属行业", value: "", required: false },
      { key: "customer_type", label: "客户类型", value: "潜在客户", required: true },
      { key: "level_code", label: "客户优先级", value: "", required: true },
      { key: "lead_source", label: "客户来源", value: "", required: true },
      { key: "target_team", label: "客户归属团队", value: defaultTeam, required: true, readonly: false, system: false },
      { key: "partner_name", label: "合作伙伴", value: "", required: false },
      { key: "contact_name", label: "首要联系人", value: "", required: true },
      { key: "contact_title", label: "联系人职位", value: "", required: true },
      { key: "contact_role", label: "联系人角色", value: "", required: true },
    ];
    const fields = initialFields.map((field) => {
      const previous = canRestore && saved.fields.find((item) => item && item.key === field.key);
      if (!previous || field.readonly) return field;
      return { ...field, value: normalizeFieldValue(field.key, previous.value), ...(field.key === "target_team" ? {teamId: previous.teamId || ""} : {}) };
    });
    this.setData({
      role: session.role,
      roleName: session.roleName || "当前身份",
      creatorName: session.userName,
      requestMode: this.requestMode,
      draftRestored: Boolean(canRestore),
    });
    this.refresh(fields);
    if (this.data.canVoiceCreate) this.initRecorder();
  },

  onUnload() {
    this.disposed = true;
    this.requestsSerial = (this.requestsSerial || 0) + 1;
    this.clearRecordTimer();
    if (this.data.isRecording && this.recorderManager) this.recorderManager.stop();
  },

  refresh(fields) {
    const requiredFields = fields.filter((item) => item.required);
    const completedRequiredCount = requiredFields.filter((item) => !fieldMissing(item)).length;
    const missingCount = requiredFields.length - completedRequiredCount;
    const customerNameField = fields.find((item) => item.key === "customer_name");
    this.setData({
      fields: fields.map((item) => ({ ...item, missing: fieldMissing(item) })),
      missingCount,
      requiredCount: requiredFields.length,
      completedRequiredCount,
      progressPercent: requiredFields.length ? Math.round((completedRequiredCount / requiredFields.length) * 100) : 100,
      customerName: customerNameField && customerNameField.value ? customerNameField.value : "待创建客户",
    });
  },

  openEditor(e) {
    if (this.data.submitting) return;
    const index = Number(e.currentTarget.dataset.index);
    const field = this.data.fields[index];
    if (!field) return;
    if (field.readonly) {
      wx.showToast({ title: this.data.role === "sales" ? "一线销售创建的客户仅归属本人" : "销售主管创建的客户默认进入本团队", icon: "none" });
      return;
    }
    const config = FIELD_EDITORS[field.key] || { type: "text", placeholder: `请输入${field.label}` };
    const sourceOptions = field.key === "target_team" ? this.teamOptions : (config.options || []);
    this.setData({
      editorVisible: true,
      editorIndex: index,
      editorTitle: field.label,
      editorType: config.type,
      editorValue: String(field.value || ""),
      editorTeamId: field.teamId || "",
      editorPlaceholder: config.placeholder || "请选择内容",
      editorOptions: field.key === "target_team" ? (this.directoryTeams || []).map(team=>({id:team.id,label:team.name,selected:team.id===field.teamId})) : sourceOptions.map((label) => ({ label, selected: label === field.value })),
    });
  },

  inputEditor(e) { this.setData({ editorValue: e.detail.value }); },
  selectOption(e) {
    const index = Number(e.currentTarget.dataset.index);
    const editorOptions = this.data.editorOptions.map((item, itemIndex) => ({ ...item, selected: itemIndex === index }));
    this.setData({ editorOptions, editorValue: editorOptions[index].label, editorTeamId: editorOptions[index].id || "" });
  },
  closeEditor() { this.setData({ editorVisible: false, editorIndex: -1 }); },
  stopPropagation() {},

  saveEditor() {
    const index = this.data.editorIndex;
    const field = this.data.fields[index];
    if (!field) return;
    const value = String(this.data.editorValue || "").trim();
    if (!value && field.required) {
      wx.showToast({ title: `${field.label}为必填项`, icon: "none" });
      return;
    }
    const fields = this.data.fields.map((item, itemIndex) => itemIndex === index ? { ...item, value, edited: true, ...(item.key === "target_team" ? {teamId: this.data.editorTeamId} : {}) } : item);
    this.setData({ editorVisible: false, editorIndex: -1, draftRestored: false });
    this.refresh(fields);
    wx.vibrateShort({ type: "light" });
  },

  initRecorder() {
    if (this.recorderManager || !wx.getRecorderManager) return;
    this.recorderManager = wx.getRecorderManager();
    this.recorderManager.onStart(() => {
      this.setData({ isRecording: true, isStarting: false, isStopping: false, isParsing: false, recordingSeconds: 0, recordingTime: "00:00" });
      this.startRecordTimer();
      wx.vibrateShort({ type: "light" });
    });
    this.recorderManager.onStop((result) => {
      this.clearRecordTimer();
      const duration = result.duration || this.data.recordingSeconds * 1000;
      this.setData({ isRecording: false, isStarting: false, isStopping: false });
      if (duration < 800) {
        wx.showToast({ title: "录音时间太短，请重新录入", icon: "none" });
        return;
      }
      this.setData({ isParsing: true });
      apiClient.transcribeAudio(result.tempFilePath, "customer_create")
        .then((transcription) => apiClient.runAgent("customer_create", transcription.text))
        .then((run) => this.applyVoiceDraft(run.result || {}))
        .catch((error) => {
          this.setData({ isParsing: false });
          wx.showToast({ title: error.message || "语音识别失败", icon: "none" });
        });
    });
    this.recorderManager.onError((error) => {
      this.clearRecordTimer();
      this.setData({ isRecording: false, isStarting: false, isStopping: false, isParsing: false });
      wx.showToast({ title: error.errMsg || "录音失败，请重试", icon: "none" });
    });
  },

  toggleVoice() {
    if (!this.data.canVoiceCreate || this.data.submitting) return;
    if (this.data.isParsing || this.data.isStarting || this.data.isStopping) return;
    if (this.data.isRecording) {
      this.setData({ isStopping: true });
      this.recorderManager.stop();
      return;
    }
    this.setData({ isStarting: true });
    this.requestRecordPermission(() => {
      this.initRecorder();
      if (!this.recorderManager) {
        this.setData({ isStarting: false });
        wx.showToast({ title: "当前微信版本不支持录音", icon: "none" });
        return;
      }
      this.recorderManager.start({ duration: 600000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: "mp3" });
    }, () => this.setData({ isStarting: false }));
  },

  requestRecordPermission(onGranted, onDenied) {
    wx.getSetting({
      success: (setting) => {
        const permission = setting.authSetting["scope.record"];
        if (permission === true) { onGranted(); return; }
        if (permission === false) {
          wx.showModal({
            title: "需要麦克风权限",
            content: "语音填写客户信息需要使用麦克风，请在设置中允许录音权限。",
            confirmText: "去设置",
            confirmColor: "#1677FF",
            success: (result) => {
              if (!result.confirm) { onDenied(); return; }
              wx.openSetting({ success: (openResult) => openResult.authSetting["scope.record"] ? onGranted() : onDenied(), fail: onDenied });
            },
          });
          return;
        }
        wx.authorize({ scope: "scope.record", success: onGranted, fail: () => { onDenied(); wx.showToast({ title: "未获得麦克风权限", icon: "none" }); } });
      },
      fail: () => { onDenied(); wx.showToast({ title: "无法读取录音权限", icon: "none" }); },
    });
  },

  startRecordTimer() {
    this.clearRecordTimer();
    this.recordTimer = setInterval(() => {
      const recordingSeconds = this.data.recordingSeconds + 1;
      this.setData({ recordingSeconds, recordingTime: this.formatTime(recordingSeconds) });
    }, 1000);
  },
  clearRecordTimer() { if (this.recordTimer) { clearInterval(this.recordTimer); this.recordTimer = null; } },
  formatTime(seconds) { return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`; },

  applyVoiceDraft(result) {
    const sample = {
      customer_name: result.customer_name,
      industry: result.industry,
      customer_type: result.customer_type,
      level_code: result.level_code || result.customer_level,
      lead_source: result.source,
      target_team: ["sales", "supervisor"].includes(this.session.role) ? this.session.team : result.target_team,
      partner_name: result.partner_name,
      contact_name: result.contact_name,
      contact_title: result.contact_title,
      contact_role: result.contact_role,
    };
    const fields = this.data.fields.map((item) => {
      const value = normalizeFieldValue(item.key, sample[item.key]);
      const teams = item.key === "target_team" && value ? (this.directoryTeams || []).filter(team => team.name === value) : [];
      return { ...item, value: value || item.value, voiceFilled: Boolean(value),
        ...(item.key === "target_team" && value ? {teamId: teams.length === 1 ? teams[0].id : ""} : {}) };
    });
    this.setData({ isParsing: false, voiceParsed: true, draftRestored: false });
    this.refresh(fields);
    wx.vibrateShort({ type: "light" });
    wx.showToast({ title: "语音草案已生成", icon: "success" });
  },

  saveDraft() {
    wx.setStorageSync(this.draftKey, { creator: this.data.creatorName, fields: this.data.fields, savedAt: Date.now() });
    this.setData({ draftRestored: false });
    wx.showToast({ title: "草稿已保存", icon: "success" });
  },

  showForm() { this.setData({ activeTab: "form" }); },
  showRequests() {
    if (!this.data.canViewRequests) return;
    this.setData({ activeTab: "requests" });
    return this.loadRequests(false);
  },
  loadMoreRequests() { return this.loadRequests(true); },
  async loadRequests(append = false) {
    if (!this.data.canViewRequests || this.data.requestsLoading || (append && !this.data.requestsHasMore)) return;
    const serial = this.requestsSerial = (this.requestsSerial || 0) + 1;
    const offset = append ? this.data.requestsNextOffset : 0;
    this.setData({ requestsLoading: true, requestsError: "" });
    try {
      const response = await apiClient.listCustomerCreationRequests({ offset, limit: 20 });
      if (this.disposed || serial !== this.requestsSerial) return;
      const labels = { pending: "待审批", approved: "已通过", rejected: "已驳回", cancelled: "已撤回" };
      const items = (response.items || []).map(item => ({ ...item,
        customerName: (item.payload || {}).name || "未填写名称",
        companyReference: (item.payload || {}).company_reference || "",
        statusLabel: labels[item.status] || item.status,
        requestedLabel: requestTime(item.requested_at),
        reviewedLabel: requestTime(item.reviewed_at),
      }));
      this.setData({ requests: append ? this.data.requests.concat(items) : items,
        requestsTotal: response.total || 0, requestsHasMore: response.has_more === true,
        requestsNextOffset: response.next_offset == null ? 0 : response.next_offset });
    } catch (error) {
      if (!this.disposed && serial === this.requestsSerial) this.setData({ requestsError: error.message || "申请加载失败，请重试" });
    } finally {
      if (!this.disposed && serial === this.requestsSerial) this.setData({ requestsLoading: false });
    }
  },
  withdrawRequest(e) {
    if (this.data.withdrawingId) return;
    const id = e.currentTarget.dataset.id;
    const item = this.data.requests.find(row => row.id === id && row.status === "pending");
    if (!item) return;
    this.setData({ withdrawingId: id });
    wx.showModal({ title: "撤回建档申请？", content: `撤回“${item.customerName}”后运营将不再审批本次申请。`, confirmText: "撤回申请", confirmColor: "#1677FF",
      success: async result => {
        if (!result.confirm) { this.setData({ withdrawingId: "" }); return; }
        try {
          await apiClient.withdrawCustomerCreationRequest(id);
          wx.showToast({ title: "申请已撤回", icon: "success" });
          await this.loadRequests(false);
        } catch (error) { wx.showToast({ title: error.message || "撤回失败，请刷新后重试", icon: "none" }); }
        finally { this.setData({ withdrawingId: "" }); }
      }, fail: () => this.setData({ withdrawingId: "" }),
    });
  },
  openCustomerClaim() { wx.navigateTo({ url: "/pages/customer-claim/index" }); },

  // 申请仅写申请快照；审批通过后的客户仍须单独认领。两种写入均沿用传输幂等键。
  submitCustomer() {
    if (this.data.submitting) return;
    if (this.data.missingCount) {
      wx.showToast({ title: `还有 ${this.data.missingCount} 个必填项待补充`, icon: "none" });
      return;
    }
    const values = this.data.fields.reduce((result, item) => { result[item.key] = item.value; return result; }, {});
    const payload = {
      name: values.customer_name, company_reference: values.company_reference,
      industry: values.industry, customer_type: values.customer_type, level_code: values.level_code,
      source: values.lead_source, target_team: values.target_team,
      target_team_id: (this.data.fields.find(field => field.key === "target_team") || {}).teamId || undefined,
      partner_name: values.partner_name, contact_name: values.contact_name,
      contact_title: values.contact_title, contact_role: values.contact_role,
    };
    this.setData({ submitting: true });
    wx.showModal({
      title: this.requestMode ? "确认提交建档申请？" : "确认创建新客户？",
      content: this.requestMode
        ? `将提交“${values.customer_name}”的建档申请，运营审批通过后进入客户主档，客户归属仍需另行认领。`
        : `将创建“${values.customer_name}”，并放入${values.target_team}的待分配客户池。`,
      confirmText: this.requestMode ? "提交申请" : "确认创建", confirmColor: "#1677FF",
      success: async result => {
        if (!result.confirm) { this.setData({ submitting: false }); return; }
        try {
          wx.setStorageSync(this.draftKey, { creator: this.data.creatorName, fields: this.data.fields, savedAt: Date.now() });
          const saved = await (this.requestMode ? apiClient.submitCustomerCreationRequest(payload) : apiClient.createCustomer(payload));
          if (!this.requestMode) wx.setStorageSync("lastCreatedCustomer", { id: saved.id, ownerUserId: this.session.userId, workspaceId: this.session.workspaceId });
          wx.removeStorageSync(this.draftKey);
          wx.showToast({ title: this.requestMode ? "申请已提交" : "客户创建成功", icon: "success" });
          if (this.requestMode) {
            this.refresh(this.data.fields.map(field => ({ ...field, value: field.key === "target_team" ? field.value : "", edited: false, voiceFilled: false })));
            this.setData({ voiceParsed: false, draftRestored: false });
            await this.showRequests();
          } else setTimeout(() => wx.navigateBack(), 850);
        } catch (error) {
          wx.showToast({ title: error.message || "提交失败，草稿已保留，请重试", icon: "none" });
        } finally { this.setData({ submitting: false }); }
      }, fail: () => this.setData({ submitting: false }),
    });
  },
});
