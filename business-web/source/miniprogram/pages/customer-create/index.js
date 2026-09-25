const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { CUSTOMER_LEVELS } = require("../../utils/customerLevel");

const FIELD_EDITORS = {
  customer_name: { type: "text", placeholder: "请输入客户全称" },
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

  async onLoad() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-create')) return;
    if (!getApp().ensureLogin()) return;
    const app = getApp();
    const session = app.globalData.session;
    if (!require('../../utils/access').can(session,'customer.create')) {
      wx.showToast({ title: "当前账号没有客户建档权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 650);
      return;
    }
    try {await apiClient.getBusinessOptions();} catch(error) {wx.showToast({title:error.message,icon:'none'});return;}
    this.session = session;
    this.teamOptions = [];
    apiClient.getTeamDirectory("assignment").then((response) => {
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
      draftRestored: Boolean(canRestore),
    });
    this.refresh(fields);
    this.initRecorder();
  },

  onUnload() {
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

  // BACKEND-CONTRACT POST /api/v1/customers：下方发出的中文枚举需服务端规范化。
  // sales 负责人必须由会话固定；supervisor/manager 先建档进待分配池，不能仅信任 target_team。
  // 本页无独立提交锁；apiClient 自动给此写接口加 Idempotency-Key，后端须实现幂等与同名冲突处理。
  // 字段与幂等适用范围详见 docs/backend-handoff/客户与商机详解.md。
  submitCustomer() {
    if (this.data.missingCount) {
      wx.showToast({ title: `还有 ${this.data.missingCount} 个必填项待补充`, icon: "none" });
      return;
    }
    const values = this.data.fields.reduce((result, item) => { result[item.key] = item.value; return result; }, {});
    wx.showModal({
      title: "确认创建新客户？",
      content: this.data.role === "sales"
        ? `将创建“${values.customer_name}”，客户负责人固定为本人，不进入下发客户池。`
        : `将创建“${values.customer_name}”，并放入${values.target_team}的待分配客户池。`,
      confirmText: "确认创建",
      confirmColor: "#1677FF",
      success: (result) => {
        if (!result.confirm) return;
        apiClient.createCustomer({
          name: values.customer_name,
          industry: values.industry,
          customer_type: values.customer_type,
          level_code: values.level_code,
          source: values.lead_source,
          target_team: values.target_team,
          target_team_id: (this.data.fields.find(field=>field.key==="target_team")||{}).teamId || undefined,
          partner_name: values.partner_name,
          contact_name: values.contact_name,
          contact_title: values.contact_title,
          contact_role: values.contact_role,
        }).then((customer) => {
          wx.setStorageSync("lastCreatedCustomer", {id:customer.id, ownerUserId:getApp().globalData.session.userId, workspaceId:getApp().globalData.session.workspaceId});
          wx.removeStorageSync(this.draftKey);
          wx.showToast({ title: "客户创建成功", icon: "success" });
          setTimeout(() => wx.navigateBack(), 850);
        }).catch((error) => {
          wx.showToast({ title: error.message || "创建失败，请重试", icon: "none" });
        });
      },
    });
  },
});
