/**
 * BACKEND-CONTRACT 拜访新入口：选择客户 → 原文/文件转写 → AI 草案 → visit-confirm 人工归档。
 * 客户 GET /customers；FDE 商机 GET /fde/visit-opportunities 独立核验本人当前协助关系；录音/文件 POST /visit-imports（file + original_filename），再 GET /visit-imports/:id 轮询 status/extracted_text。
 * 结构化分别调用 visit_entry 与 opportunity_draft Agent；后者失败可降级为空，仅预填，不创建业务对象。
 * 草稿按 workspaceId:userId 存储 visitEntryV2/visitStructuredV2；真实记录仅由确认页 POST /visits 写入。
 * 文档“60 分钟/扫描件不支持”的校验须由导入后端负责，前端仅检查文件大小与可选扩展名；没有模拟转写或模拟审核。
 */
const api = require("../../utils/apiClient");
const access = require("../../utils/access");
const {draftScope} = require("../../utils/draftScope");
const newDraftId = () => `visit-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
const draftFields = ["entryMode", "transcript", "customerId", "customerName", "customerQuery",
  "opportunityId", "opportunityName", "isFirstVisit", "importId", "fileName", "importStatus",
  "appliedImportId", "hasTranscription", "localFilePath", "localFileTemporary", "fileSaveErrorCode", "statusText", "errorText"];
const copy = value => JSON.parse(JSON.stringify(value));
Page({
  data: {
    navTop: 20, navHeight: 44, draftNotice: "", draftSaveError: "", undoAvailable: false,
    localFilePath: "", localFileTemporary: false, fileSaveErrorCode: "",
    entryMode: "voice",
    inputHelpVisible: false,
    transcript: "",
    hasTranscription: false,
    customerId: "",
    customerName: "",
    customerInitial: "",
    customerQuery: "",
    customerResults: [],
    customerSearchError: "",
    customerConfirmed: false,
    opportunityId: "", opportunityName: "", fdeOpportunityVerified: false,
    searching: false,
    isFirstVisit: false,
    isRecording: false,
    isStarting: false,
    isStopping: false,
    isProcessing: false,
    recordingTime: "00:00",
    statusText: "先记录事实，稍后确认客户与商机",
    errorText: "",
    canSubmit: false,
    importId: "",
    fileName: "",
    importStatus: "",
    uploadProgress: 0,
  },
  onLoad(options = {}) {
    this.entryOptions = options;
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-entry', options)) return;
    if (!getApp().ensureLogin()) return;
    this.entryInitialized = true;
    this.closed = false;
    this.hidden = false;
    this.unloaded = false;
    this.userKey = draftScope(getApp().globalData.session);
    this.draftKey = `visitEntryV2:${this.userKey}`;
    this.actionRevision = 0;
    this.setupNavigation();
    let draft = {};
    try { draft = wx.getStorageSync(this.draftKey) || {}; }
    catch (_) { this.draftReadFailed = true; this.setData({draftSaveError:"草稿读取失败，请暂时保留此页面并重试"}); }
    const restored = this.hasDraftContent(draft);
    this.draftId = draft.draftId || newDraftId();
    const customerId = restored ? draft.customerId || "" : options.customerId || options.customer_id || "";
    this.customerReferencePending = Boolean(customerId);
    // A draft belongs to this unfinished action; route parameters must not rebind it.
    this.setData({
      ...(restored ? this.pickDraft(draft) : {}),
      // Legacy drafts predate this marker; an applied import proves transcription completed.
      hasTranscription: restored && (typeof draft.hasTranscription === "boolean"
        ? draft.hasTranscription : Boolean(draft.appliedImportId || draft.importStatus === "succeeded")),
      entryMode: draft.entryMode === "file" ? "file" : "voice",
      isFde: access.isFde((getApp().globalData.session || {}).role),
      customerId, customerName: restored ? draft.customerName || "" : "",
      customerInitial: "", customerQuery: restored ? draft.customerQuery || "" : "",
      customerConfirmed: false, canSubmit: false,
      opportunityId: restored ? draft.opportunityId || "" : options.opportunityId || options.opportunity_id || "",
      opportunityName: restored ? draft.opportunityName || "" : "", fdeOpportunityVerified: false,
      isProcessing: false, isRecording: false, isStarting: false, isStopping: false,
      draftNotice: restored ? "已恢复上次未完成的录入，可继续编辑" : "",
    });
    this.appliedImportId = draft.appliedImportId || "";
    if (customerId) this.loadLinkedCustomer(customerId);
    else this.searchDepartmentCustomers();
    this.recorder = wx.getRecorderManager();
    this.onRecorderStart = () => {
      this.setData({
        isStarting: false,
        isRecording: true,
        recordingTime: "00:00",
      });
      let seconds = 0;
      this.timer = setInterval(() => {
        seconds++;
        this.setData({
          recordingTime: `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`,
        });
      }, 1000);
    };
    this.onRecorderStop = (result) => {
      clearInterval(this.timer);
      this.setData({ isStarting: false, isRecording: false, isStopping: false });
      if (this.closed) return;
      if (!result.tempFilePath || result.duration < 800) {
        this.fail("录音太短，请重新录入");
        return;
      }
      this.uploadFile({ path: result.tempFilePath, size: result.fileSize, name: "拜访录音.mp3" }, this.exitAfterRecording === true);
    };
    this.onRecorderError = (error) => {
      clearInterval(this.timer);
      this.setData({
        isStarting: false,
        isRecording: false,
        isStopping: false,
      });
      this.fail(error.errMsg || "录音失败");
    };
    this.recorder.onStart(this.onRecorderStart);
    this.recorder.onStop(this.onRecorderStop);
    this.recorder.onError(this.onRecorderError);
  },
  async loadLinkedCustomer(customerId) {
    const request = this.customerLoadSerial = (this.customerLoadSerial || 0) + 1;
    this.setData({ searching: true });
    try {
      const customer = await api.getCustomerReference(customerId);
      if (this.closed || request !== this.customerLoadSerial) return;
      this.customerReferencePending = false;
      this.customerReferenceInvalid = false;
      this.confirmSelectedCustomer(customer);
      this.setData({ searching: false });
    } catch (error) {
      if (this.closed || request !== this.customerLoadSerial) return;
      this.customerReferencePending = false;
      this.customerReferenceInvalid = true;
      this.setData({ searching: false, errorText: error.message || "客户信息已变化，请重新选择客户" });
    }
  },
  onShow() {
    if (this.entryOptions && !this.entryInitialized) return this.onLoad(this.entryOptions);
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-entry')) return;
    this.closed = false;
    this.hidden = false;
    if (this.forwarded) {
      this.forwarded = false;
      const saved = wx.getStorageSync(this.draftKey);
      if (!saved) {
        this.actionRevision = (this.actionRevision || 0) + 1;
        this.draftId = newDraftId(); this.appliedImportId = "";
        this.setData({transcript:"",hasTranscription:false,customerId:"",customerName:"",customerQuery:"",customerInitial:"",customerConfirmed:false,
          opportunityId:"",opportunityName:"",fdeOpportunityVerified:false,isFirstVisit:false,importId:"",fileName:"",importStatus:"",
          localFilePath:"",localFileTemporary:false,fileSaveErrorCode:"",errorText:"",draftNotice:"",undoAvailable:false,canSubmit:false,isProcessing:false,statusText:"先记录事实，稍后确认客户与商机"});
        this.searchDepartmentCustomers();
      }
    }
    if (this.data.localFilePath && !this.data.localFileTemporary && !this.data.importId && !this.data.isProcessing && !this.fileSavePromise) {
      this.uploadFile({path:this.data.localFilePath,name:this.data.fileName}, false, true);
    }
    if (this.data.isFde) {
      this.setData({fdeOpportunityVerified:false,canSubmit:false});
      const picker=this.selectComponent && this.selectComponent("#fdeVisitOpportunity");
      if(picker)picker.checkSelection(true);
    }
    if (
      this.data.importId &&
      ["queued", "processing"].includes(this.data.importStatus)
    )
      this.pollImport();
  },
  onHide() {
    this.persist();
    this.hidden = true;
    clearTimeout(this.pollTimer);
    if (this.data.isRecording) this.recorder.stop();
  },
  onUnload() {
    this.persist();
    this.closed = true;
    this.unloaded = true;
    clearTimeout(this.customerSearchTimer);
    if (this.recorder) {
      if (this.recorder.offStart) this.recorder.offStart(this.onRecorderStart);
      if (this.recorder.offStop) this.recorder.offStop(this.onRecorderStop);
      if (this.recorder.offError) this.recorder.offError(this.onRecorderError);
      if (this.data.isRecording) this.recorder.stop();
    }
    clearTimeout(this.pollTimer);
    clearInterval(this.timer);
  },
  setupNavigation() {
    try {
      const info = wx.getWindowInfo ? wx.getWindowInfo() : wx.getSystemInfoSync();
      const capsule = wx.getMenuButtonBoundingClientRect();
      const navTop = info.statusBarHeight || 20;
      this.setData({navTop,navHeight:Math.max(44, (capsule.top-navTop)*2+capsule.height)});
    } catch (_) { /* Older runtimes retain the safe default navigation dimensions. */ }
  },
  pickDraft(source = this.data) {
    const snapshot = {};
    draftFields.forEach(key => { if (source[key] !== undefined) snapshot[key] = source[key]; });
    return snapshot;
  },
  hasDraftContent(d = this.data) {
    return Boolean(String(d.transcript || "").length || d.customerId || d.customerQuery || d.opportunityId || d.isFirstVisit || d.fileName || d.importId || d.localFilePath);
  },
  operationToken() { return {revision:this.actionRevision || 0, draftId:this.draftId, scope:this.userKey}; },
  operationCurrent(token) {
    if (this.discarded || this.unloaded || token.revision !== (this.actionRevision || 0) || token.draftId !== this.draftId) return false;
    if (token.scope && token.scope !== draftScope(getApp().globalData.session)) return false;
    if (token.scope && wx.getStorageSync) {
      try {
        const current = wx.getStorageSync(this.draftKey);
        if (current && current.draftId && current.draftId !== token.draftId) return false;
      } catch (_) { this.setData({isProcessing:false,draftSaveError:"草稿暂不可读，请保留此页面后重试"});return false; }
    }
    return true;
  },
  persist() {
    if (!this.draftKey || this.discarded || this.forwarded || this.unloaded || this.draftReadFailed) return false;
    if (this.userKey && this.userKey !== draftScope(getApp().globalData.session)) return false;
    this.draftId = this.draftId || newDraftId();
    try {
      if (wx.getStorageSync) {
        const current = wx.getStorageSync(this.draftKey);
        if (current && current.draftId && current.draftId !== this.draftId && !this.replacingDraft) return false;
      }
      wx.setStorageSync(this.draftKey, {
        ...this.pickDraft(), draftId:this.draftId, savedAt:Date.now(),
        appliedImportId:this.appliedImportId || this.data.appliedImportId || "",
      });
      if (this.data.draftSaveError) this.setData({draftSaveError:""});
      return true;
    } catch (_) {
      this.setData({draftSaveError:"草稿未能保存，请保留此页面后重试"});
      return false;
    }
  },
  relatedDraftKeys() {
    const scope = this.userKey || draftScope(getApp().globalData.session);
    return ["visitStructuredV2", "visitConfirmV2", "visitStructureRun"].map(prefix => `${prefix}:${scope}`);
  },
  leavePage() {
    this.discardUndo();
    wx.navigateBack({fail:() => wx.switchTab({url:"/pages/index/index"})});
  },
  requestBack() {
    if (this.backPromptOpen || this.exitAfterRecording) return;
    if (!this.hasDraftContent() && !this.data.isRecording && !this.data.isStarting) { this.leavePage(); return; }
    this.backPromptOpen = true;
    wx.showModal({
      title:"是否保存本次拜访草稿？", content:"保存后，下次进入可继续编辑。放弃将清除本次未提交的内容。",
      confirmText:"保存", cancelText:"放弃", confirmColor:"#1677e8",
      success:async result => {
        this.backPromptOpen = false;
        if (result.confirm) {
          if (this.data.isStarting || this.data.isStopping) { wx.showToast({title:"录音正在准备或保存，请稍候",icon:"none"}); return; }
          if (this.data.isRecording) { this.exitAfterRecording = true; this.recorder.stop(); return; }
          if (this.fileSavePromise) { await this.fileSavePromise; return; }
          if (this.data.localFileTemporary) { await this.uploadFile({path:this.data.localFilePath,name:this.data.fileName},true); return; }
          if (this.persist()) this.leavePage();
        } else if (result.cancel && this.discardDraft()) this.leavePage();
      },
      fail:() => { this.backPromptOpen = false; },
    });
  },
  discardDraft() {
    try {
      if (this.userKey !== draftScope(getApp().globalData.session)) return false;
      const current = wx.getStorageSync(this.draftKey);
      if (current && current.draftId && current.draftId !== this.draftId) return false;
      this.relatedDraftKeys().forEach(key => wx.removeStorageSync(key));
      wx.removeStorageSync(this.draftKey);
      this.discarded = true;
      this.actionRevision = (this.actionRevision || 0) + 1;
      clearTimeout(this.pollTimer);
      this.removeLocalFile(this.data.localFilePath);
      return true;
    } catch (_) { this.setData({draftSaveError:"草稿清除失败，请重试"}); return false; }
  },
  clearTranscript() {
    if (this.data.isRecording || this.data.isStarting || this.data.isStopping || (!this.data.transcript && !this.data.fileName && !this.data.importId)) return;
    const before = {...this.pickDraft(),appliedImportId:this.appliedImportId || this.data.appliedImportId || ""};
    const oldId = this.draftId;
    const related = {};
    try { this.relatedDraftKeys().forEach(key => { related[key] = wx.getStorageSync(key); }); }
    catch (_) { this.setData({draftSaveError:"草稿读取失败，暂未清空"}); return; }
    this.actionRevision = (this.actionRevision || 0) + 1;
    clearTimeout(this.pollTimer);
    this.draftId = newDraftId(); this.appliedImportId = "";
    this.setData({transcript:"",hasTranscription:false,isFirstVisit:false,importId:"",fileName:"",importStatus:"",appliedImportId:"",localFilePath:"",localFileTemporary:false,fileSaveErrorCode:"",
      uploadProgress:0,isProcessing:false,errorText:"",canSubmit:false,statusText:"已清空本次录入内容",draftNotice:"",undoAvailable:false});
    this.replacingDraft = true;
    const saved = this.persist(); this.replacingDraft = false;
    if (!saved) { this.draftId=oldId; this.appliedImportId=before.appliedImportId; this.setData({...before,canSubmit:this.entryReady(before.transcript)}); return; }
    this.undoDraft = {before,related};
    this.setData({undoAvailable:true});
    // The new action ID also isolates stale results if cleanup cannot complete.
    try { this.relatedDraftKeys().forEach(key => wx.removeStorageSync(key)); } catch (_) {}
  },
  undoClear() {
    if (!this.undoDraft || !this.data.undoAvailable) return;
    const snapshot = this.undoDraft;
    this.actionRevision = (this.actionRevision || 0) + 1;
    this.appliedImportId = snapshot.before.appliedImportId;
    this.setData({...snapshot.before,isProcessing:false,undoAvailable:false,statusText:"已撤销清空，可继续编辑",canSubmit:this.entryReady(snapshot.before.transcript)});
    if (!this.persist()) { this.setData({undoAvailable:true}); return; }
    // Keep raw input; AI output must be regenerated for this new action ID.
    this.undoDraft = null;
    if (this.data.importId && ["queued","processing"].includes(this.data.importStatus)) this.pollImport();
  },
  discardUndo() {
    if (this.undoDraft && this.undoDraft.before.localFilePath !== this.data.localFilePath) this.removeLocalFile(this.undoDraft.before.localFilePath);
    this.undoDraft = null;
    if (this.data.undoAvailable) this.setData({undoAvailable:false});
  },
  removeLocalFile(path) {
    if (path && wx.getFileSystemManager) wx.getFileSystemManager().removeSavedFile({filePath:path,fail:() => {}});
  },
  entryReady(text, context = this.data) { return Boolean(!this.customerReferencePending && !this.customerReferenceInvalid && context.customerId && String(text || "").trim() && (!context.isFde || (context.opportunityId && context.fdeOpportunityVerified))); },
  fdeOpportunityChanged(e) {
    const row=e.detail.opportunity;
    const update={fdeOpportunityVerified:e.detail.verified===true, ...(row?{opportunityId:row.id,opportunityName:row.name}:e.detail.cleared?{opportunityId:"",opportunityName:""}:{})};
    this.setData({...update,canSubmit:this.entryReady(this.data.transcript,{...this.data,...update})});
    this.persist();
  },
  inputTranscript(e) {
    this.discardUndo();
    this.setData({
      transcript: e.detail.value,
      canSubmit: this.entryReady(e.detail.value),
      errorText: "",
    });
    this.persist();
  },
  inputCustomerQuery(e) {
    if(this.data.isProcessing)return;
    this.customerLoadSerial = (this.customerLoadSerial || 0) + 1;
    const customerQuery = e.detail.value;
    this.setData({ customerQuery, customerId: "", customerName: "", customerInitial: "", customerConfirmed: false, canSubmit: false, opportunityId:"", opportunityName:"", fdeOpportunityVerified:false });
    clearTimeout(this.customerSearchTimer);
    this.customerSearchTimer = setTimeout(() => this.searchDepartmentCustomers(), 250);
    this.persist();
  },
  async searchDepartmentCustomers() {
    const query = String(this.data.customerQuery || "").trim();
    const request = this.customerLoadSerial = (this.customerLoadSerial || 0) + 1;
    const identity = access.identity(getApp().globalData.session);
    const isFde = access.isFde((getApp().globalData.session || {}).role);
    this.setData({ isFde, searching: true, customerSearchError: "", customerResults: [] });
    try {
      // /customers uses mine/department/company, unlike FDE statistics' self/team.
      const response = await api.listCustomers({ q: query, scope: isFde ? "mine" : "company", pageSize: 100 });
      if (this.closed || request !== this.customerLoadSerial || identity !== access.identity(getApp().globalData.session)) return;
      if (!Array.isArray(response.items)) throw new Error("客户列表响应不完整，请重试");
      const customerResults = response.items.map(item => ({ ...item, initial: String(item.name || "?").substring(0, 1) }));
      this.setData({ customerResults, searching: false });
    } catch (error) {
      if (this.closed || request !== this.customerLoadSerial || identity !== access.identity(getApp().globalData.session)) return;
      const customerSearchError = error.statusCode === 422 ? "客户列表请求暂不可用，请重试" : "客户列表加载失败，请检查网络后重试";
      this.setData({ customerResults: [], searching: false, customerSearchError });
    }
  },
  chooseCustomer(e) {
    if(this.data.isProcessing)return;
    const customer = this.data.customerResults.find((item) => item.id === e.currentTarget.dataset.id);
    if (!customer) return;
    this.confirmSelectedCustomer(customer);
  },
  confirmSelectedCustomer(customer) {
    this.customerReferencePending = false;
    this.customerReferenceInvalid = false;
    this.customerLoadSerial = (this.customerLoadSerial || 0) + 1;
    this.setData({
      customerId: customer.id,
      customerName: customer.name,
      customerInitial: String(customer.name || "?").substring(0, 1),
      customerQuery: customer.name,
      customerResults: [],
      customerConfirmed: true,
      ...(this.data.isFde ? {fdeOpportunityVerified:false} : {}),
      canSubmit: Boolean(String(this.data.transcript || "").trim()) && !this.data.isFde,
    });
    this.persist();
  },
  changeCustomer() {
    if(this.data.isProcessing)return;
    this.customerLoadSerial = (this.customerLoadSerial || 0) + 1;
    this.setData({ customerId: "", customerName: "", customerInitial: "", customerConfirmed: false, canSubmit: false, opportunityId:"", opportunityName:"", fdeOpportunityVerified:false });
    this.persist();
    this.searchDepartmentCustomers();
  },
  toggleFirstVisit(e) {
    if (this.data.isProcessing || this.data.isFde) return;
    this.setData({ isFirstVisit: e.detail.value.includes("first") });
    this.persist();
  },
  fail(message) {
    this.exitAfterRecording = false;
    this.setData({
      isProcessing: false,
      errorText: message,
      statusText: message,
      canSubmit: this.entryReady(this.data.transcript),
    });
    this.persist();
  },
  toggleRecording() {
    if (this.data.isStarting || this.data.isStopping || this.data.isProcessing)
      return;
    if (this.data.isRecording) {
      this.setData({ isStopping: true });
      this.recorder.stop();
      return;
    }
    if (this.pendingLocalFile()) return;
    this.setData({ isStarting: true, errorText: "" });
    wx.authorize({
      scope: "scope.record",
      success: () =>
        this.recorder.start({
          duration: 600000,
          sampleRate: 16000,
          numberOfChannels: 1,
          encodeBitRate: 48000,
          format: "mp3",
        }),
      fail: (error) => this.recordingAuthorizationFailed(error),
    });
  },
  recordingAuthorizationFailed(error = {}) {
    const unavailable = () => {
      if (this.closed) return;
      this.setData({ isStarting: false });
      const network = /network|timeout|timed out|ECONN|proxy|tunnel|网络/i.test(error.errMsg || "");
      this.fail(network
        ? "录音授权网络请求失败，请检查网络；开发者工具中请检查代理是否与当前网络状态一致，然后重试。"
        : "录音授权请求失败，尚不能确认是权限问题，请重试；开发者工具中请检查网络与代理状态。");
    };
    wx.getSetting({
      success: (result) => {
        if (this.closed) return;
        if (!result.authSetting || result.authSetting['scope.record'] !== false) return unavailable();
        this.setData({ isStarting: false });
        wx.showModal({
          title: "需要麦克风权限",
          content: "录音权限已关闭，可在设置中允许录音后重试，也可以继续文字录入或上传文件。",
          confirmText: "去设置",
          success: (r) => {
            if (r.confirm && !this.closed) wx.openSetting({});
          },
        });
      },
      fail: unavailable,
    });
  },
  switchInputMode(e) {
    if (
      this.data.isRecording ||
      this.data.isStarting ||
      this.data.isStopping ||
      this.data.isProcessing
    )
      return;
    const mode = e.currentTarget.dataset.mode;
    if (!["voice", "file"].includes(mode)) return;
    this.setData({ entryMode: mode, inputHelpVisible: false });
    this.persist();
  },
  toggleInputHelp() {
    this.setData({ inputHelpVisible: !this.data.inputHelpVisible });
  },
  dismissInputHelp() {
    if (this.data.inputHelpVisible) this.setData({ inputHelpVisible: false });
  },
  stopBubble() {},
  chooseMaterial() {
    this.setData({ inputHelpVisible: false });
    this.chooseFile([
      "mp3",
      "wav",
      "m4a",
      "aac",
      "ogg",
      "flac",
      "amr",
      "webm",
      "pdf",
      "docx",
      "pptx",
      "md",
      "txt",
    ]);
  },
  chooseFile(extension) {
    if (this.data.isProcessing || this.data.isRecording) return;
    if (this.pendingLocalFile()) return;
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension,
      success: (r) => {
        if (r.tempFiles[0]) this.uploadFile(r.tempFiles[0]);
      },
      fail: (e) => {
        if (!String(e.errMsg).includes("cancel"))
          this.fail("未能选择文件，请重试");
      },
    });
  },
  async uploadFile(file, exitAfterSave = false, alreadySaved = false, temporaryUpload = false) {
    if (file.size > 100 * 1024 * 1024) { this.fail("录音文件最大100MB"); return; }
    const extension = String(file.name || "").split(".").pop().toLowerCase();
    if (["pdf","docx","pptx","md","txt"].includes(extension) && file.size > 20 * 1024 * 1024) { this.fail("文档最大20MB，请精简后上传"); return; }
    if (this.data.isProcessing) return;
    this.discardUndo();
    const token = this.operationToken();
    // Keep the only audio reference before any storage API can fail. Temporary
    // paths are recoverable while available, never advertised as durable drafts.
    this.appliedImportId = "";
    this.setData({importId:"",appliedImportId:"",fileName:file.name,localFilePath:file.path,
      localFileTemporary:!alreadySaved,importStatus:"saving",fileSaveErrorCode:"",errorText:""});
    if (!this.persist()) { this.exitAfterRecording=false; return; }
    this.setData({isProcessing:true,statusText:"正在保存文件…"});
    let path = file.path;
    if (!alreadySaved && !temporaryUpload) {
      this.fileSavePromise = new Promise(resolve => {
        try {
          if (!wx.getFileSystemManager) { resolve({error:{errCode:"UNAVAILABLE"}}); return; }
          wx.getFileSystemManager().saveFile({tempFilePath:file.path,
            success:r=>resolve(r.savedFilePath ? {path:r.savedFilePath} : {error:{errCode:"EMPTY_PATH"}}),
            fail:error=>resolve({error})});
        } catch (error) { resolve({error}); }
      });
      const saved = await this.fileSavePromise; this.fileSavePromise = null;
      if (!this.operationCurrent(token)) { if(saved.path)this.removeLocalFile(saved.path); return; }
      if (!saved.path) {
        this.exitAfterRecording=false;
        const error=saved.error||{}, message=String(error.errMsg||error.message||"");
        const code=String(error.errCode||error.code||"UNKNOWN").replace(/[^A-Za-z0-9_-]/g,"").slice(0,32)||"UNKNOWN";
        const reason=/quota|space|storage.*limit|exceed.*limit/i.test(message) ? "本地文件存储空间不足" :
          /no such|not found|not exist/i.test(message) ? "临时文件已不可读取" :
          /permission|denied/i.test(message) ? "微信未允许保存本地文件" : "微信本地文件保存失败";
        this.setData({importStatus:"save_failed",fileSaveErrorCode:code});
        this.fail(`${reason}（${code}）。尚未上传，已保留临时文件引用，请保持当前页面并点击“重试保存”。不要清缓存；临时文件失效后需重新录入。`);
        return;
      }
      path = saved.path;
    }
    this.appliedImportId = "";
    this.setData({importId:"",fileName:file.name,importStatus:"uploading",appliedImportId:"",localFilePath:path,localFileTemporary:temporaryUpload,fileSaveErrorCode:"",uploadProgress:0,errorText:"",statusText:"正在上传文件"});
    if (!this.persist()) { this.setData({isProcessing:false});this.exitAfterRecording=false;return; }
    if (exitAfterSave) { this.exitAfterRecording=false;this.setData({isProcessing:false});this.leavePage();return; }
    try {
      const r = await api.uploadVisitFile(path,file.name,progress => { if (this.operationCurrent(token) && !this.hidden) this.setData({uploadProgress:progress.progress}); });
      if (!this.operationCurrent(token)) return;
      this.setData({importId:r.id,importStatus:r.status,statusText:"文件已上传，正在提取文字…"});
      if (this.persist()) { this.removeLocalFile(path);this.setData({localFilePath:"",localFileTemporary:false});this.persist(); }
      this.pollImport();
    } catch (error) {
      if (!this.operationCurrent(token)) return;
      this.setData({importStatus:"failed"});this.fail(error.message);
    }
  },
  pollImport() {
    clearTimeout(this.pollTimer);
    if (this.closed || !this.data.importId) return;
    const requestedImportId = this.data.importId;
    const token = this.operationToken();
    this.setData({ isProcessing: true });
    api
      .request({ path: `/visit-imports/${requestedImportId}` })
      .then((r) => {
        if (!this.operationCurrent(token) || requestedImportId !== this.data.importId) return;
        this.setData({ importStatus: r.status, fileName: r.filename });
        if (r.status === "succeeded") {
          const alreadyApplied =
            (this.appliedImportId || this.data.appliedImportId) ===
            requestedImportId;
          const transcript = alreadyApplied
            ? this.data.transcript
            : [this.data.transcript, r.extracted_text]
                .filter(Boolean)
                .join("\n\n");
          this.appliedImportId = requestedImportId;
          this.setData({
            transcript,
            hasTranscription: this.data.hasTranscription || Boolean(String(r.extracted_text || "").trim()) || (alreadyApplied && Boolean(transcript)),
            isProcessing: false,
            canSubmit: this.entryReady(transcript),
            errorText: "",
            statusText: "文字已提取，可修改后提交 AI 整理",
          });
          this.persist();
          return;
        }
        if (r.status === "failed") {
          this.fail(r.error_message || "文件处理失败，可重试");
          this.persist();
          return;
        }
        this.setData({
          statusText:
            r.status === "queued"
              ? "已进入处理队列，离开页面后仍会继续"
              : "正在提取文字，长录音需要一些时间",
        });
        this.persist();
        if (!this.hidden) this.pollTimer = setTimeout(() => this.pollImport(), 2000);
      })
      .catch((e) => { if (this.operationCurrent(token)) this.fail(`${e.message}，可点击继续查看`); });
  },
  uploadTemporaryFile() {
    if (this.data.isProcessing || !this.data.localFileTemporary || !this.data.localFilePath || this.data.importId) return;
    return this.uploadFile({path:this.data.localFilePath,name:this.data.fileName},false,false,true);
  },
  pendingLocalFile() {
    if (!this.data.localFilePath || this.data.importId) return false;
    wx.showToast({title:"请先重试或移除待处理文件",icon:"none"});
    return true;
  },
  retryImport() {
    if (this.data.isProcessing) return;
    if (!this.data.importId) {
      if (this.data.localFilePath) { return this.uploadFile({path:this.data.localFilePath,name:this.data.fileName},false,!this.data.localFileTemporary); }
      this.chooseMaterial(); return;
    }
    const token = this.operationToken(), importId = this.data.importId;
    this.setData({isProcessing:true});
    api.request({path:`/visit-imports/${importId}/retry`,method:"POST"})
      .then(() => { if (this.operationCurrent(token) && this.data.importId === importId) this.pollImport(); })
      .catch(error => { if (this.operationCurrent(token)) this.fail(error.message); });
  },
  removeFile() {
    if (this.data.isProcessing) return;
    const previous = this.pickDraft(), applied = this.appliedImportId;
    this.appliedImportId = "";
    this.setData({importId:"",fileName:"",importStatus:"",appliedImportId:"",localFilePath:"",localFileTemporary:false,fileSaveErrorCode:"",errorText:""});
    if (this.persist()) this.removeLocalFile(previous.localFilePath);
    else { this.appliedImportId = applied;this.setData(previous); }
  },
  async structureVisit(body, token = this.operationToken()) {
    const key=`visitStructureRun:${this.userKey || draftScope(getApp().globalData.session)}`;
    const signature=JSON.stringify(body), saved=wx.getStorageSync(key);
    let id=saved && saved.signature===signature && (!saved.draftId || saved.draftId===this.draftId) ? saved.id : '';
    if(!id){const accepted=await api.submitVisitStage('structure',body);if(!this.operationCurrent(token))throw Error('本次录入已结束');id=accepted.run_id;wx.setStorageSync(key,{signature,id,draftId:this.draftId});}
    try { return await api.waitVisitRun(id); }
    catch(error){if(this.operationCurrent(token) && error.code && error.code!=='RUN_TIMEOUT' && error.code!=='AUTH_EXPIRED')wx.removeStorageSync(key);throw error;}
  },
  submitTranscript() {
    const text = this.data.transcript.trim();
    if (!this.data.customerId) {
      wx.showToast({ title: "请先选择客户", icon: "none" });
      return;
    }
    if (this.data.isFde && (!this.data.opportunityId || !this.data.fdeOpportunityVerified)) { this.fail("请选择并确认本人参与的商机"); return; }
    if (!text || this.data.isProcessing || this.data.isRecording) return;
    if (this.pendingLocalFile()) return;
    if (text.length > 50000) {
      this.fail("原文超过5万字，请拆分录入");
      return;
    }
    if (!this.draftKey) this.draftKey = `visitEntryV2:${draftScope(getApp().globalData.session)}`;
    if (!this.persist()) return;
    const token = this.operationToken();
    this.setData({
      isProcessing: true,
      errorText: "",
      statusText: "AI 正在整理沟通内容和下一步计划…",
    });
    const isFirstVisit = !this.data.isFde && this.data.isFirstVisit;
    const opportunityPrompt = [
      "请从以下拜访记录识别商机信息，只提取原文明确出现的内容，不要猜测。",
      "请识别：已有商机名称或新商机名称、商机阶段或赢单概率、ACV、预计关单日期、合作伙伴、产品线；未明确的字段留空。",
      "识别结果仅用于人工确认前的表单预填，不执行创建或修改。",
      "",
      text,
    ].join("\n");
    return Promise.all([
      this.structureVisit({text,is_first_visit:isFirstVisit,customer_id:this.data.customerId,
        opportunity_id:this.data.isFde ? this.data.opportunityId : null,source_import_id:this.data.importId || null}, token),
      this.data.isFde ? Promise.resolve(null) : api.runAgent("opportunity_draft", opportunityPrompt, this.data.customerId || null).catch(() => null),
    ]).then(([run, opportunityRun]) => {
        if (!this.operationCurrent(token)) return;
        wx.setStorageSync(
          `visitStructuredV2:${draftScope(getApp().globalData.session)}`,
          {
            draftId:this.draftId,
            result: run.result || {},
            opportunitySuggestion: opportunityRun && opportunityRun.result ? opportunityRun.result : null,
            runId: run.run_id || run.id,
            customerHintId: this.data.customerId,
            customerHint: this.data.customerName,
            opportunityId: this.data.isFde ? this.data.opportunityId : "",
            isFirstVisit,
            sourceImportId: this.data.importId,
            original: text,
          },
        );
        wx.removeStorageSync(
          `visitConfirmV2:${draftScope(getApp().globalData.session)}`,
        );
        this.setData({
          isProcessing: false,
          statusText: "整理完成，请确认关联与内容",
        });
        this.persist();
        if (!this.hidden) {
          this.forwarded = true;
          wx.navigateTo({url:"/pages/visit-confirm/index",fail:() => {this.forwarded=false;this.fail("页面打开失败，原文已保留，可重试");}});
        }
      })
      .catch((e) => { if (this.operationCurrent(token)) this.fail(`${e.message}；原文已保留，可修改后重新提交`); });
  },
});
