const customerIdentity = require('../../utils/customerIdentity');
const uiColors = require("../../utils/uiColors");
const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { identity } = require('../../utils/access');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);
const CREATION_OPTIONS_ERROR = '创建选项目录暂不可用，请刷新后重试';

const FIELD_EDITORS = {
  customer_name: { type: "text", placeholder: "请输入客户全称" },
  industry: { type: "select", options: businessOptions.customerCreation.industry },
  customer_type: { type: "select", options: businessOptions.customerCreation.customer_type },
  level_code: { type: "select", options: businessOptions.customerCreation.level_code },
  lead_source: { type: "select", options: businessOptions.customerCreation.source },
  target_team: { type: "select", options: [] },
  partner_name: { type: "text", placeholder: "选填，可留空" },
  contact_name: { type: "text", placeholder: "请输入首要联系人姓名" },
  contact_title: { type: "text", placeholder: "请输入联系人职位" },
  contact_role: { type: "select", options: businessOptions.customerCreation.contact_role },
};

function fieldMissing(field) {
  return Boolean(field.required) && !String(field.value || "").trim();
}

function draftFieldValue(value) { return String(value || "").trim(); }

const API_FIELDS = {
  name:['customer_name','客户名称'], industry:['industry','所属行业'], customer_type:['customer_type','客户类型'],
  level_code:['level_code','客户优先级'], source:['lead_source','客户来源'], target_team:['target_team','客户归属团队'],
  target_team_id:['target_team','客户归属团队'], partner_name:['partner_name','合作伙伴'],
  contact_name:['contact_name','首要联系人'], contact_title:['contact_title','联系人职位'], contact_role:['contact_role','联系人角色'],
};
function creationFieldErrors(errors, fields) {
  const result = {};
  (Array.isArray(errors) ? errors : []).forEach(error => {
    if (!error || !Array.isArray(error.loc) || error.loc.length !== 2 || error.loc[0] !== 'body') return;
    const mapping = Object.prototype.hasOwnProperty.call(API_FIELDS, error.loc[1]) ? API_FIELDS[error.loc[1]] : null;
    if (!mapping) return;
    const [key,label] = mapping;
    const field = fields.find(item => item.key === key);
    if (!field || result[key]) return;
    const reason = error.type === 'missing' ? '请补充必填内容'
      : error.type === 'literal_error' || error.type === 'enum' ? '请选择有效选项'
      : ['uuid_parsing','uuid_type','uuid_version'].includes(error.type) ? '请重新选择有效团队'
      : ['string_too_short','string_too_long'].includes(error.type) ? '填写长度不符合要求'
      : '请检查填写内容';
    result[key] = `${label}：${reason}`;
  });
  return result;
}

Page({
  ...customerIdentity.methods(apiClient, {session:()=>getApp().globalData.session,wx:()=>wx}),
  data: {
    ...customerIdentity.state(),
    loading:true,submitting:false,
    role: "supervisor",
    roleName: "销售主管",
    creatorName: "",
    fields: [],
    submissionError: '',
    creationOptionsReady: false,
    creationOptionsError: '',
    creationOptionsRefreshing: false,
    invalidFieldCount: 0,
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
    canRequestCreate: false,
    creationRequests: [],
    creationRequestsLoading: false,
    creationRequestsError: "",
  },

  async onLoad() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-create')) return;
    if (!getApp().ensureLogin()) return;
    const app = getApp();
    const session = app.globalData.session;
    this.writeIdentity=identity(session);this.loadGeneration=(this.loadGeneration||0)+1;
    const load=writes.capture(this,()=>this.loadGeneration);
    this.clearRecordTimer();
    if(this.data.isRecording && this.recorderManager)this.recorderManager.stop();
    this.setData({loading:true,submitting:false,isStarting:false,isRecording:false,isStopping:false,isParsing:false,
      fields:this.data.fields.map(field=>({...field,validationError:''})),submissionError:'',creationOptionsReady:false,creationOptionsError:'',creationOptionsRefreshing:false});
    const access = require('../../utils/access');
    const canDirectCreate = access.can(session, 'customer.create');
    const canRequestCreate = access.can(session, 'customer.create_request');
    if (!canDirectCreate && !canRequestCreate) {
      wx.showToast({ title: "当前账号没有客户建档权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 650);
      return;
    }
    const requestMode = !canDirectCreate && canRequestCreate;
    let creationOptionsError='';
    try {
      await apiClient.getBusinessOptions();
      if(!load.current())return;
      if(!businessOptions.isCustomerCreationReady())await apiClient.getBusinessOptions({fresh:true});
    } catch(error) {creationOptionsError=CREATION_OPTIONS_ERROR;}
    if(!load.current())return;
    const creationOptionsReady=!creationOptionsError && businessOptions.isCustomerCreationReady();
    if(!creationOptionsReady)creationOptionsError=CREATION_OPTIONS_ERROR;
    this.session = session;
    this.teamOptions = [];
    apiClient.getTeamDirectory("assignment", {
      permission: requestMode ? "customer.create_request" : "customer.create",
    }).then((response) => {
      if(!load.current())return;
      if (!Array.isArray(response.teams)) throw Error("团队目录不可用");
      this.directoryTeams = response.teams;
      this.teamOptions = response.teams.map(team => team.name);
      this.refresh(this.data.fields.map(field => {
        if (field.key !== "target_team") return field;
        const matches = response.teams.filter(team => field.teamId ? team.id === field.teamId : team.name === field.value);
        return matches.length === 1 ? {...field, value:matches[0].name, teamId:matches[0].id}
          : field.readonly ? field : {...field, value:"", teamId:""};
      }));
    }).catch((error) => {if(load.visible())wx.showToast({ title: error.message || "团队数据加载失败", icon: "none" });});
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
      return { ...field, value: draftFieldValue(previous.value), ...(field.key === "target_team" ? {teamId: previous.teamId || ""} : {}) };
    });
    this.setData({
      role: session.role,
      roleName: session.roleName || "当前身份",
      creatorName: session.userName,
      requestMode,
      canRequestCreate,
      creationRequests: [],
      creationRequestsLoading: canRequestCreate,
      creationRequestsError: "",
      draftRestored: Boolean(canRestore),
      loading:false,
      creationOptionsReady,
      creationOptionsError,
    });
    if (canRequestCreate) {
      apiClient.listCustomerCreationRequests({limit: 20}).then((result) => {
        if (!load.current()) return;
        this.setData({
          creationRequests: Array.isArray(result?.items) ? result.items : [],
          creationRequestsError: "",
        });
      }).catch((error) => {
        if (!load.current()) return;
        this.setData({creationRequestsError: error.message || "申请状态暂时无法读取"});
      }).finally(() => {
        if (load.current()) this.setData({creationRequestsLoading: false});
      });
    }
    this.initBusinessIdentity({primary_partner_name:(fields.find(f=>f.key==='partner_name') || {}).value || ''}, saved && saved.businessIdentity);
    this.refresh(fields);
    this.initRecorder();
  },

  async refreshCreationOptions() {
    if(this.data.loading || this.data.submitting || this.data.creationOptionsRefreshing)return;
    const refresh=writes.capture(this,()=>this.loadGeneration);
    if(!refresh.current())return;
    this.setData({creationOptionsReady:false,creationOptionsRefreshing:true});
    try {
      await apiClient.getBusinessOptions({fresh:true});
      if(!refresh.current())return;
      const creationOptionsReady=businessOptions.isCustomerCreationReady();
      this.setData({identityOptionsReady:businessOptions.businessIdentities.length===2,identityOptions:businessOptions.businessIdentities.length?businessOptions.businessIdentities:customerIdentity.OPTIONS,creationOptionsReady,creationOptionsError:creationOptionsReady?'':CREATION_OPTIONS_ERROR});
      this.refresh(this.data.fields.map(field=>({...field,validationError:''})));
    } catch(error) {
      if(refresh.current())this.setData({creationOptionsReady:false,creationOptionsError:CREATION_OPTIONS_ERROR});
    } finally {
      if(refresh.current())this.setData({creationOptionsRefreshing:false});
    }
  },

  onShow(){
    this.writeHidden=false;
    if(this.writeIdentity!==undefined && this.writeIdentity!==identity(getApp().globalData.session))return this.onLoad();
    if(this.completedCreation && this.completedCreation.visible())wx.navigateBack();
  },
  onHide(){this.writeHidden=true;},

  onUnload() {
    this.unloaded=true;
    this.clearRecordTimer();
    if (this.data.isRecording && this.recorderManager) this.recorderManager.stop();
  },

  refresh(fields) {
    const partner=fields.find(f=>f.key==='partner_name');
    if(partner && !this.data.associatedPartnerId)this.setData({associatedPartnerName:partner.value || ''});
    fields=fields.map(field=>field.key==='customer_type'?{...field,required:this.data.isCustomerIdentity,hidden:!this.data.isCustomerIdentity,validationError:this.data.isCustomerIdentity?field.validationError:''}:field.key==='partner_name'?{...field,label:'关联合作伙伴',hidden:true}:field);
    const hadFieldErrors = this.data.fields.some(field => field.validationError);
    fields = fields.map(field => {
      const previous = this.data.fields.find(item => item.key === field.key);
      const next=previous && (previous.value !== field.value || previous.teamId !== field.teamId)
        ? {...field,validationError:''} : field;
      const editor=FIELD_EDITORS[field.key];
      if(this.data.creationOptionsReady && !field.hidden && field.value && editor && editor.options && editor.options.length && !editor.options.includes(field.value))
        return {...next,validationError:`${field.label}：请选择有效选项`};
      return next;
    });
    const invalidFieldCount=fields.filter(field=>field.validationError).length;
    const requiredFields = fields.filter((item) => item.required);
    const completedRequiredCount = requiredFields.filter((item) => !fieldMissing(item)).length;
    const missingCount = requiredFields.length - completedRequiredCount;
    const customerNameField = fields.find((item) => item.key === "customer_name");
    this.setData({
      invalidFieldCount,
      submissionError: invalidFieldCount ? '请修改标出的字段后重新提交' : hadFieldErrors ? '' : this.data.submissionError,
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
      if(!this.voiceContext || !this.voiceContext.current())return;
      this.setData({ isRecording: true, isStarting: false, isStopping: false, isParsing: false, recordingSeconds: 0, recordingTime: "00:00" });
      this.startRecordTimer();
      wx.vibrateShort({ type: "light" });
    });
    this.recorderManager.onStop((result) => {
      const voice=this.voiceContext;
      if(!voice || !voice.current())return;
      this.clearRecordTimer();
      const duration = result.duration || this.data.recordingSeconds * 1000;
      this.setData({ isRecording: false, isStarting: false, isStopping: false });
      if (duration < 800) {
        if(voice.visible())wx.showToast({ title: "录音时间太短，请重新录入", icon: "none" });
        return;
      }
      this.setData({ isParsing: true });
      apiClient.transcribeAudio(result.tempFilePath, "customer_create")
        .then((transcription) => {if(voice.current() && voice===this.voiceContext)return apiClient.runAgent("customer_create", transcription.text);})
        .then((run) => {if(run && voice.current() && voice===this.voiceContext)this.applyVoiceDraft(run.result || {});})
        .catch((error) => {
          if(!voice.current() || voice!==this.voiceContext)return;
          this.setData({ isParsing: false });
          if(voice.visible())wx.showToast({ title: error.message || "语音识别失败", icon: "none" });
        });
    });
    this.recorderManager.onError((error) => {
      if(!this.voiceContext || !this.voiceContext.current())return;
      this.clearRecordTimer();
      this.setData({ isRecording: false, isStarting: false, isStopping: false, isParsing: false });
      if(this.voiceContext.visible())wx.showToast({ title: error.errMsg || "录音失败，请重试", icon: "none" });
    });
  },

  toggleVoice() {
    if (this.data.isParsing || this.data.isStarting || this.data.isStopping) return;
    if (this.data.isRecording) {
      this.setData({ isStopping: true });
      this.recorderManager.stop();
      return;
    }
    const voice=this.voiceContext=writes.capture(this,()=>this.draftKey || '');
    this.setData({ isStarting: true });
    this.requestRecordPermission(() => {
      if(!voice.current() || voice!==this.voiceContext)return;
      this.initRecorder();
      if (!this.recorderManager) {
        this.setData({ isStarting: false });
        wx.showToast({ title: "当前微信版本不支持录音", icon: "none" });
        return;
      }
      this.recorderManager.start({ duration: 600000, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: "mp3" });
    }, () => {if(voice.current() && voice===this.voiceContext)this.setData({ isStarting: false });});
  },

  requestRecordPermission(onGranted, onDenied) {
    const context=this.voiceContext || writes.capture(this,()=>this.draftKey || '');
    wx.getSetting({
      success: (setting) => {
        if(!context.current())return;
        const permission = setting.authSetting["scope.record"];
        if (permission === true) { onGranted(); return; }
        if (permission === false) {
          wx.showModal({
            title: "需要麦克风权限",
            content: "语音填写客户信息需要使用麦克风，请在设置中允许录音权限。",
            confirmText: "去设置",
            confirmColor: uiColors.primary,
            success: (result) => {
              if(!context.current())return;
              if (!result.confirm) { onDenied(); return; }
              wx.openSetting({ success: (openResult) => openResult.authSetting["scope.record"] ? onGranted() : onDenied(), fail: onDenied });
            },
          });
          return;
        }
        wx.authorize({ scope: "scope.record", success: onGranted, fail: () => { onDenied(); if(context.visible())wx.showToast({ title: "未获得麦克风权限", icon: "none" }); } });
      },
      fail: () => { onDenied(); if(context.visible())wx.showToast({ title: "无法读取录音权限", icon: "none" }); },
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
      const value = draftFieldValue(sample[item.key]);
      const teams = item.key === "target_team" && value ? (this.directoryTeams || []).filter(team => team.name === value) : [];
      return { ...item, value: value || item.value, voiceFilled: Boolean(value),
        ...(item.key === "target_team" && value ? {teamId: teams.length === 1 ? teams[0].id : ""} : {}) };
    });
    this.setData({ isParsing: false, voiceParsed: true, draftRestored: false });
    this.refresh(fields);
    wx.vibrateShort({ type: "light" });
    if(!this.writeHidden)wx.showToast({ title: "语音草案已生成", icon: "success" });
  },

  saveDraft() {
    if(this.writeIdentity!==undefined && this.writeIdentity!==identity(getApp().globalData.session))return;
    wx.setStorageSync(this.draftKey, { creator: this.data.creatorName, fields: this.data.fields, businessIdentity:customerIdentity.draft(this.data), savedAt: Date.now() });
    this.setData({ draftRestored: false });
    wx.showToast({ title: "草稿已保存", icon: "success" });
  },

  // BACKEND-CONTRACT POST /api/v1/customers：下方发出的中文枚举需服务端规范化。
  // sales 负责人必须由会话固定；supervisor/manager 先建档进待分配池，不能仅信任 target_team。
  // 确认与写入共用页面锁；apiClient 保留 Idempotency-Key，未知结果不自动再次提交。
  // 字段与幂等适用范围详见 docs/backend-handoff/客户与商机详解.md。
  submitCustomer() {
    if(this.data.loading || this.data.submitting)return;
    if(this.completedCreation && this.completedCreation.current())return;
    if(!this.data.creationOptionsReady || !businessOptions.isCustomerCreationReady()) {
      const creationOptionsError=CREATION_OPTIONS_ERROR;
      this.setData({creationOptionsReady:false,creationOptionsError});
      wx.showToast({title:creationOptionsError,icon:'none'});return;
    }
    this.refresh(this.data.fields.map(field=>({...field,validationError:''})));
    if (this.data.missingCount) {
      wx.showToast({ title: `还有 ${this.data.missingCount} 个必填项待补充`, icon: "none" });
      return;
    }
    if(this.data.invalidFieldCount) {
      wx.showToast({title:'请修改标出的字段后重新提交',icon:'none'});return;
    }
    const values = this.data.fields.reduce((result, item) => { result[item.key] = item.value; return result; }, {});
    let identityPayload;
    try {identityPayload=customerIdentity.payload(this.data,values.customer_type);} catch(error) {this.setData({submissionError:error.message});wx.showToast({title:error.message,icon:'none'});return;}
    const write=writes.begin(this,'create',()=>this.draftKey || '');if(!write)return;
    this.setData({submitting:true,fields:this.data.fields.map(field=>({...field,validationError:''})),submissionError:''});
    const identitySignature=JSON.stringify(customerIdentity.draft(this.data));
    const fieldsSignature=JSON.stringify(this.data.fields),draftKey=this.draftKey;
    const teamId=(this.data.fields.find(field=>field.key==='target_team')||{}).teamId;
    wx.showModal({
      title: this.data.requestMode ? "确认提交客户新建申请？" : "确认创建新客户？",
      content: this.data.requestMode
        ? `将提交“${values.customer_name}”的新建申请，审核通过后建立未认领的正式客户，不会自动归属本人；需要另行申请认领。`
        : this.data.role === "sales"
        ? `将创建“${values.customer_name}”，客户负责人固定为本人，不进入下发客户池。`
        : `将创建“${values.customer_name}”，并放入${values.target_team}的待分配客户池。`,
      confirmText: this.data.requestMode ? "提交申请" : "确认创建",
      confirmColor: uiColors.primary,
      success: (result) => {
        if(write.started)return;
        if (!result.confirm || !write.visible() || fieldsSignature!==JSON.stringify(this.data.fields) || identitySignature!==JSON.stringify(customerIdentity.draft(this.data))) {write.finish('submitting');return;}
        if(!write.start())return;
        const payload = {
          name: values.customer_name,
          industry: values.industry,
          ...identityPayload,
          level_code: values.level_code,
          source: values.lead_source,
          target_team: values.target_team,
          target_team_id: teamId || undefined,
          partner_name: values.partner_name,
          contact_name: values.contact_name,
          contact_title: values.contact_title,
          contact_role: values.contact_role,
        };
        const creation = this.data.requestMode
          ? apiClient.createCustomerCreationRequest(payload)
          : apiClient.createCustomer(payload);
        creation.then((customer) => {
          if(!write.current())return;
          this.completedCreation=writes.capture(this,()=>this.draftKey || '');
          if (!this.data.requestMode) wx.setStorageSync("lastCreatedCustomer", {id:customer.id, ownerUserId:write.session.userId, workspaceId:write.session.workspaceId});
          wx.removeStorageSync(draftKey);
          if(write.visible())wx.showToast({ title: this.data.requestMode ? "申请已提交，等待运营审批" : "客户创建成功", icon: "success" });
          setTimeout(() => {if(write.settledVisible())wx.navigateBack();}, 850);
        }).catch((error) => {
          if(!write.current())return;
          const fieldErrors = error.statusCode === 422 ? creationFieldErrors(error.validationErrors,
            this.data.fields.filter(field=>field.value===values[field.key] &&
              (field.key!=='target_team' || field.teamId===teamId))) : {};
          const submissionError = error.statusCode === 422
            ? Object.keys(fieldErrors).length ? '请修改标出的字段后重新提交' : '提交内容未通过校验，请检查填写内容后重试'
            : error.message || '创建失败，请重试';
          this.setData({fields:this.data.fields.map(field=>({...field,validationError:fieldErrors[field.key] || ''})),
            invalidFieldCount:Object.keys(fieldErrors).length,submissionError});
          if(write.visible())wx.showToast({ title: submissionError, icon: "none" });
        }).finally(()=>write.finish('submitting'));
      },
      fail:()=>write.finish('submitting'),
    });
  },
});
