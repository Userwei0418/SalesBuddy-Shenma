/**
 * Create independent tasks for human-selected company colleagues. Customer tasks link an existing
 * opportunity that the creator can associate; recipients need no prior CRM role.
 */
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { allTaskRecipients } = require("../../utils/taskRecipients");

Page({
  data: {
    role: "sales",
    roleName: "一线销售",
    scope: "全员协作",
    creatorName: "",
    description: "",
    descriptionCount: 0,
    members: [], recipientLoading:false, recipientError:"",
    selectedMembers: [], selectedNames: "",
    recipientOpen: false, recipientQuery: "", recipientRows: [],
    submissionPending: false,
    priorities: ["普通", "中", "高"],
    selectedPriority: "中",
    selectedDue: "",
    minDueDate: "",
    maxDueDate: "",
    customDueDate: "",
    customDueDateLabel: "",
    customDueTime: "17:00",
    isStarting: false,
    isRecording: false,
    isStopping: false,
    isParsing: false,
    recordingSeconds: 0,
    recordingTime: "00:00",
    voiceFilled: false,
    submitting: false,
    taskType:"daily",customerName:"",opportunityName:"",linkVerified:false,linkLoading:false,linkError:"",selectorOpen:false,selectorKind:"customer",selectorQuery:"",selectorRows:[],selectorLoading:false,selectorError:"",selectorMore:false,selectorOffset:0,
    customerId: "",
    opportunityId: "",
    adviceSource:null,adviceLoading:false,adviceError:'',
    voiceExample: "请描述任务交付物、完成标准和截止要求",
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'management-task-create', options)) return;
    if (!getApp().ensureLogin()) return;
    const app = getApp();
    const session = app.globalData.session;
    const ownerScope = draftScope(session);
    if(options && options.adviceId && options.suggestionId){
      this.setData({adviceLoading:true});
      apiClient.getBusinessAdvice(options.adviceId).then(advice=>{
        if(this.isUnloading || draftScope(app.globalData.session)!==ownerScope)return;
        const suggestion=(advice.suggestions||[]).find(s=>s.id===options.suggestionId);
        if(advice.status!=='succeeded' || advice.stale || !suggestion || suggestion.decision!=='pending')throw Error('建议已变化或已处理，请返回查看最新建议');
        const dailyVisit=!['fde','fde_lead'].includes(session.role) && advice.subject_kind==='visit' && !advice.opportunity_id;
        this.setData({adviceSource:{dailyVisit,id:suggestion.id,version:suggestion.version_no,title:suggestion.title,opportunityFixed:Boolean(advice.opportunity_id)},description:suggestion.action,
          descriptionCount:suggestion.action.length,taskType:dailyVisit?"daily":"customer",customerId:dailyVisit?'':advice.customer_id,opportunityId:advice.opportunity_id||''});
        this.adviceOpportunityId=advice.opportunity_id||'';if(!dailyVisit)this.hydrateTaskLink();
      }).catch(error=>{if(!this.isUnloading)this.setData({adviceError:error.message||'建议加载失败'});})
        .finally(()=>{if(!this.isUnloading)this.setData({adviceLoading:false});});
    }
    const members = [];
    const voiceExample = "请描述任务交付物、完成标准和截止要求";
    const retryKey = `retryTaskDraft:${draftScope(session)}`;
    const retryDraft = (options || {}).retry ? wx.getStorageSync(retryKey) : null;
    if (retryDraft) wx.removeStorageSync(retryKey);
    const today = new Date();
    const defaultDueDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
    const maxDueDate = new Date(today.getFullYear() + 2, today.getMonth(), today.getDate());
    const customDueDateLabel = this.formatDateLabel(defaultDueDate);
    this.setData({
      role: session.role,
      roleName: app.globalData.roles[session.role].name,
      scope: app.globalData.roles[session.role].scope,
      creatorName: session.userName,
      members,
      minDueDate: this.formatDateValue(today),
      maxDueDate: this.formatDateValue(maxDueDate),
      customDueDate: this.formatDateValue(defaultDueDate),
      customDueDateLabel,
      selectedDue: `${customDueDateLabel} 17:00`,
      voiceExample,
      description: retryDraft ? String(retryDraft.description || "") : "",
      descriptionCount: retryDraft ? String(retryDraft.description || "").length : 0,
      selectedPriority: retryDraft ? (retryDraft.priority || "中") : "中",
      taskType: (options.customerId || options.opportunityId || retryDraft && retryDraft.customerId || options.adviceId) ? "customer" : "daily",
      customerId: String((options || {}).customerId || retryDraft && retryDraft.customerId || ""),
      opportunityId: String((options || {}).opportunityId || retryDraft && retryDraft.opportunityId || ""),
    });
    this.loadRecipients(retryDraft);
    if(this.data.taskType==='customer' && !options.adviceId)this.hydrateTaskLink(retryDraft);
  },
  loadRecipients(retryDraft=null) {
    const generation=this.recipientGeneration=(this.recipientGeneration||0)+1,identity=draftScope(getApp().globalData.session);
    const current=()=>!this.isUnloading&&generation===this.recipientGeneration&&identity===draftScope(getApp().globalData.session);
    this.setData({recipientLoading:true,recipientError:'',members:[],selectedMembers:[],selectedNames:'',recipientRows:[]});
    allTaskRecipients(apiClient,current).then((rows) => {
      if(!current())return;
      const members = (rows || []).map((item) => {
        const roleLabel = ({ fde:"FDE",fde_lead:"FDE主管",sales: "一线销售", supervisor: "销售主管", manager: "销售总经理",operations:"运营",administrator:"系统管理员" })[item.role] || item.role;
        return { ...item, account: item.account_code, initial: item.name.substring(0, 1), roleLabel, pickerLabel: `${item.name} · ${roleLabel} · ${item.team || item.team_name || "未填写部门"}` };
      });
      this.setData({recipientLoading:false,members});
      this.setSelectedRecipients(retryDraft ? [retryDraft.assigneeId] : []);
    }).catch((error) => {if(current())this.setData({recipientLoading:false,recipientError:error.message || "负责人列表加载失败"});});
  },
  retryRecipients(){this.loadRecipients();},
  changeTaskType(e){
    if(this.data.submitting||this.data.submissionPending||this.data.adviceSource||this.data.adviceLoading)return;
    const type=e.currentTarget.dataset.type;if(!['daily','customer'].includes(type)||type===this.data.taskType)return;
    this.linkGeneration=(this.linkGeneration||0)+1;this.selectorGeneration=(this.selectorGeneration||0)+1;
    this.setData({taskType:type,customerId:'',customerName:'',opportunityId:'',opportunityName:'',linkVerified:false,linkLoading:false,linkError:'',selectorOpen:false});this.loadRecipients();
  },
  async hydrateTaskLink(retryDraft=null){
    const generation=this.linkGeneration=(this.linkGeneration||0)+1,identity=draftScope(getApp().globalData.session);
    const customerId=this.data.customerId,opportunityId=this.data.opportunityId;
    if(!customerId)return;
    const current=()=>!this.isUnloading&&generation===this.linkGeneration&&identity===draftScope(getApp().globalData.session);
    this.setData({linkLoading:true,linkVerified:false,linkError:''});
    try{
      const customer=await apiClient.getCustomerReference(customerId);if(!current())return;
      if(String(customer.id)!==String(customerId))throw Error('客户信息不匹配');
      this.setData({customerName:customer.name});
      if(opportunityId){
        const raw=await apiClient.listTaskOpportunities({customerId,opportunityId});if(!current())return;
        const row=(raw.items||[]).find(item=>String(item.id)===String(opportunityId));
        if(!row || String(row.customer_id)!==String(customerId))throw Error('商机不存在或当前无权关联，请重新选择');
        this.setData({opportunityName:row.name,linkVerified:true});
        if(retryDraft && retryDraft.assigneeId)this.loadRecipients(retryDraft);
      }
    }catch(error){if(current())this.setData({linkError:error.message||'关联信息加载失败'});}
    finally{if(current())this.setData({linkLoading:false});}
  },
  openTaskSelector(e){
    if(this.data.submitting||this.data.submissionPending||this.data.adviceLoading)return;
    const kind=e.currentTarget.dataset.kind;
    if(kind==='customer'&&this.data.adviceSource)return;
    if(kind==='opportunity'&&!this.data.customerId){wx.showToast({title:'请先选择客户',icon:'none'});return;}
    if(this.data.adviceSource&&this.adviceOpportunityId&&kind==='opportunity')return;
    this.setData({selectorOpen:true,selectorKind:kind,selectorQuery:'',selectorRows:[],selectorOffset:0,selectorMore:false});this.loadTaskChoices();
  },
  closeTaskSelector(){this.selectorGeneration=(this.selectorGeneration||0)+1;clearTimeout(this.selectorTimer);this.setData({selectorOpen:false,selectorLoading:false});},
  searchTaskChoices(e){this.selectorGeneration=(this.selectorGeneration||0)+1;clearTimeout(this.selectorTimer);this.setData({selectorQuery:e.detail.value,selectorRows:[],selectorMore:false,selectorLoading:true});this.selectorTimer=setTimeout(()=>this.loadTaskChoices(),250);},
  async loadTaskChoices(more=false){
    const append=more===true,offset=append?this.data.selectorOffset:0;
    const generation=this.selectorGeneration=(this.selectorGeneration||0)+1,identity=draftScope(getApp().globalData.session);
    const current=()=>!this.isUnloading&&this.data.selectorOpen&&generation===this.selectorGeneration&&identity===draftScope(getApp().globalData.session);
    this.setData({selectorLoading:true,selectorError:''});
    try{
      const result=this.data.selectorKind==='customer'
        ?await apiClient.listTaskCustomers({q:this.data.selectorQuery,pageSize:20,offset})
        :await apiClient.listTaskOpportunities({customerId:this.data.customerId,q:this.data.selectorQuery,pageSize:20,offset});
      if(!current())return;if(!Array.isArray(result.items))throw Error('列表数据不完整');
      this.setData({selectorRows:append?this.data.selectorRows.concat(result.items):result.items,selectorOffset:result.next_offset||0,selectorMore:result.has_more===true});
    }catch(error){if(current())this.setData({selectorError:error.message||'列表加载失败'});}
    finally{if(current())this.setData({selectorLoading:false});}
  },
  moreTaskChoices(){if(!this.data.selectorLoading&&this.data.selectorMore)return this.loadTaskChoices(true);},
  chooseTaskLink(e){
    const row=this.data.selectorRows.find(item=>String(item.id)===String(e.currentTarget.dataset.id));if(!row)return;
    this.linkGeneration=(this.linkGeneration||0)+1;
    if(this.data.selectorKind==='customer')this.setData({customerId:row.id,customerName:row.name,opportunityId:'',opportunityName:'',linkVerified:false,linkError:'',linkLoading:false});
    else{if(String(row.customer_id)!==String(this.data.customerId))return;this.setData({opportunityId:row.id,opportunityName:row.name,linkVerified:true,linkError:'',linkLoading:false});}
    this.closeTaskSelector();
  },

  onReady() {
    this.initTaskRecorder();
  },

  onUnload() {
    this.isUnloading = true;
    clearTimeout(this.selectorTimer);
    this.clearRecordTimer();
    if (this.data.isRecording && this.recorderManager) this.recorderManager.stop();
  },

  formatDateValue(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  },

  formatDateLabel(date) {
    return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  },

  parseCustomDue() {
    const [year, month, day] = this.data.customDueDate.split("-").map(Number);
    const [hour, minute] = this.data.customDueTime.split(":").map(Number);
    return new Date(year, month - 1, day, hour, minute, 0, 0);
  },

  getCustomDueLabel() {
    return `${this.data.customDueDateLabel} ${this.data.customDueTime}`;
  },

  inputDescription(e) {
    if(this.data.submitting || this.data.submissionPending)return;
    const description = e.detail.value;
    this.setData({ description, descriptionCount: description.length, voiceFilled: false });
  },

  initTaskRecorder() {
    if (this.recorderManager || !wx.getRecorderManager) return;
    this.recorderManager = wx.getRecorderManager();
    this.recorderManager.onStart(() => {
      if (this.isUnloading) return;
      this.setData({ isRecording: true, isStarting: false, isStopping: false, isParsing: false, recordingSeconds: 0, recordingTime: "00:00" });
      this.startRecordTimer();
      wx.vibrateShort({ type: "light" });
    });
    this.recorderManager.onStop((result) => {
      this.clearRecordTimer();
      if (this.isUnloading) return;
      const duration = result.duration || this.data.recordingSeconds * 1000;
      this.setData({ isRecording: false, isStarting: false, isStopping: false });
      if (duration < 800) {
        wx.showToast({ title: "录音时间太短，请重新录入", icon: "none" });
        return;
      }
      this.setData({ isParsing: true });
      apiClient.transcribeAudio(result.tempFilePath, "management_task")
        .then((transcription) => this.applyVoiceDescription(transcription.text))
        .catch((error) => {
          this.setData({ isParsing: false });
          wx.showToast({ title: error.message || "语音识别失败", icon: "none" });
        });
    });
    this.recorderManager.onError((error) => {
      this.clearRecordTimer();
      if (this.isUnloading) return;
      this.setData({ isRecording: false, isStarting: false, isStopping: false, isParsing: false });
      wx.showToast({ title: error.errMsg || "录音失败，请重试", icon: "none" });
    });
  },

  toggleTaskVoice() {
    if(this.data.submitting || this.data.submissionPending)return;
    if (this.data.isParsing || this.data.isStarting || this.data.isStopping) return;
    if (this.data.isRecording) {
      this.setData({ isStopping: true });
      this.recorderManager.stop();
      return;
    }
    this.setData({ isStarting: true });
    this.requestRecordPermission(() => {
      this.initTaskRecorder();
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
            content: "语音录入任务需要使用麦克风，请在设置中允许录音权限。",
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
      this.setData({ recordingSeconds, recordingTime: this.formatRecordingTime(recordingSeconds) });
    }, 1000);
  },

  clearRecordTimer() {
    if (!this.recordTimer) return;
    clearInterval(this.recordTimer);
    this.recordTimer = null;
  },

  formatRecordingTime(seconds) {
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  },

  applyVoiceDescription(recognizedText) {
    const currentDescription = String(this.data.description || "").trim();
    const description = (currentDescription ? `${currentDescription}\n${recognizedText}` : recognizedText).slice(0, 500);
    this.setData({ description, descriptionCount: description.length, isParsing: false, voiceFilled: true });
    wx.vibrateShort({ type: "light" });
    wx.showToast({ title: "语音内容已填入", icon: "success" });
  },

  setSelectedRecipients(ids) {
    const selected = new Set(ids);
    const selectedMembers = this.data.members.filter(member => selected.has(member.id));
    this.setData({selectedMembers, selectedNames:selectedMembers.map(member=>member.name).join('、')});
    this.filterRecipients();
  },
  filterRecipients() {
    const query = this.data.recipientQuery.trim().toLocaleLowerCase();
    const selected = new Set(this.data.selectedMembers.map(member=>member.id));
    const recipientRows = this.data.members.filter(member =>
      [member.name,member.account,member.team,member.team_name,member.roleLabel].join(' ').toLocaleLowerCase().includes(query)
    ).map(member=>({...member,selected:selected.has(member.id)}));
    this.setData({recipientRows});
  },
  openRecipients() {
    if(this.data.submitting || this.data.submissionPending || this.data.recipientLoading)return;
    this.setData({recipientOpen:true,recipientQuery:''});this.filterRecipients();
  },
  closeRecipients(){this.setData({recipientOpen:false});},
  searchRecipients(e){this.setData({recipientQuery:e.detail.value});this.filterRecipients();},
  toggleRecipient(e) {
    if(this.data.submitting || this.data.submissionPending)return;
    const id=e.currentTarget.dataset.id;
    if(!this.data.members.some(member=>member.id===id))return;
    const ids=this.data.selectedMembers.map(member=>member.id);
    if(ids.includes(id))this.setSelectedRecipients(ids.filter(value=>value!==id));
    else if(ids.length<100)this.setSelectedRecipients([...ids,id]);
    else wx.showToast({title:'单次最多选择100人，请分次派发',icon:'none'});
  },

  selectPriority(e) {
    if(this.data.submitting || this.data.submissionPending)return;
    this.setData({ selectedPriority: e.currentTarget.dataset.value });
  },

  changeDueDate(e) {
    if(this.data.submitting || this.data.submissionPending)return;
    const value = e.detail.value;
    const [year, month, day] = value.split("-").map(Number);
    const customDueDateLabel = this.formatDateLabel(new Date(year, month - 1, day));
    this.setData({ customDueDate: value, customDueDateLabel }, () => {
      this.setData({ selectedDue: this.getCustomDueLabel() });
    });
  },

  changeDueTime(e) {
    if(this.data.submitting || this.data.submissionPending)return;
    this.setData({ customDueTime: e.detail.value }, () => {
      this.setData({ selectedDue: this.getCustomDueLabel() });
    });
  },

  getSelectedDueAt() {
    return this.parseCustomDue().getTime();
  },

  submitTask() {
    if(this.data.submitting || this._confirming || this._submitted)return;
    if(this.pendingSubmission){this.sendSubmission();return;}
    if(this.data.taskType==='customer'&&(!this.data.customerId||!this.data.opportunityId||!this.data.linkVerified||this.data.linkLoading||this.data.linkError)){
      wx.showToast({title:this.data.linkError||(!this.data.customerId?'请先选择客户':!this.data.opportunityId?'请选择该客户的商机':'请等待关联信息核验'),icon:'none'});return;
    }
    if(this.data.adviceLoading || this.data.adviceError){wx.showToast({title:this.data.adviceError||'正在读取建议',icon:'none'});return;}
    if (this.data.isStarting || this.data.isRecording || this.data.isStopping || this.data.isParsing) {
      wx.showToast({ title: "请先完成本次语音录入", icon: "none" });
      return;
    }
    const description = String(this.data.description || "").trim();
    if (description.length < 5) {
      wx.showToast({ title: "请填写清晰的任务描述", icon: "none" });
      return;
    }
    const recipients=this.data.selectedMembers.slice();
    if (!recipients.length) {
      wx.showToast({ title: "请选择任务负责人", icon: "none" });
      return;
    }
    const dueAt = this.getSelectedDueAt();
    if (!Number.isFinite(dueAt) || dueAt <= Date.now()) {
      wx.showToast({ title: "截止时间需要晚于当前时间", icon: "none" });
      return;
    }
    const session=getApp().globalData.session;
    const ownerScope=draftScope(session);
    const source=this.data.adviceSource;
    const inputs=recipients.map(recipient=>({description,associationKind:this.data.taskType,
      assigneeAccount:recipient.account,targetPosition:null,dueAt,priority:this.data.selectedPriority,
      customerId:this.data.taskType==='customer'?this.data.customerId:'',
      opportunityId:this.data.taskType==='customer'?this.data.opportunityId:''}));
    this._confirming=true;
    wx.showModal({
      title: `确认创建${recipients.length}条待办？`,
      content: `负责人：${recipients.map(member=>member.name).join('、')}。每人各一条，独立接受和完成；截止${this.data.selectedDue}。`,
      confirmText: "确认下发", confirmColor: "#1677FF",
      success: result=>{
        this._confirming=false;
        if(!result.confirm || this.isUnloading || this.data.submitting || this._submitted ||
            ownerScope!==draftScope(getApp().globalData.session))return;
        this.pendingSubmission={inputs,source,ownerScope,session};
        this.sendSubmission();
      },
      fail:()=>{this._confirming=false;},
    });
  },
  async sendSubmission() {
    const pending=this.pendingSubmission;
    if(!pending || this.isUnloading || this.data.submitting || this._submitted ||
        pending.ownerScope!==draftScope(getApp().globalData.session))return;
    const current=()=>!this.isUnloading && pending.ownerScope===draftScope(getApp().globalData.session);
    this.setData({submitting:true,submissionPending:true});
    const {inputs,source,session}=pending;
    try {
      let tasks;
      if(source){
        const bodies=inputs.map(input=>({description:input.description,association_kind:input.associationKind,
          assignee_account_code:input.assigneeAccount,target_position:null,due_at:new Date(input.dueAt).toISOString(),
          priority_code:({'普通':'normal','中':'medium','高':'high'})[input.priority]||'normal',
          customer_id:input.customerId||null,opportunity_id:input.opportunityId||null}));
        const result=await apiClient.decideSuggestion(source.id,{decision:'adopted',version_no:source.version,
          ...(bodies.length===1?{task:bodies[0]}:{tasks:bodies})});
        tasks=result.tasks || [result.task];
      }else if(inputs.length===1){tasks=[await apiClient.createTask(inputs[0])];}
      else {tasks=(await apiClient.createTasks(inputs)).items;}
      if(!current())return;
      // Receipt-cache failure must never turn a confirmed success into another business submission.
      this._submitted=true;this.pendingSubmission=null;
      try {wx.setStorageSync("lastManagementTaskCreated", {id:tasks[0].id,ids:tasks.map(task=>task.id),
        ownerUserId:session.userId,workspaceId:session.workspaceId});} catch (_) { /* list refresh reads server */ }
      wx.showToast({title:`已发送${tasks.length}条待办`,icon:'success'});
      setTimeout(()=>{if(current())wx.navigateBack();},850);
    }catch(error){
      if(!current())return;
      const rejected=[400,403,404,409,422].includes(error.statusCode);
      if(rejected)this.pendingSubmission=null;
      this.setData({submitting:false,submissionPending:!rejected});
      wx.showToast({title:error.message || '发送结果未确认，请重试',icon:'none'});
    }
  },
});
