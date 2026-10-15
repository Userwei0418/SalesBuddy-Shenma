/**
 * BACKEND-CONTRACT 拜访归档：POST /visits {customer_id, fields, fde_participant_ids}；fields 包含 _quality_review_run_id、可选 _opportunity_mutation/source_import_id/collaborator_ids。
 * 服务端必须复核审核 run 归属、审核正文与字段一致、分数为 0–100 整数且 >60、next_action.passed === true，不能只信前端按钮。
 * 正文或首次拜访必填信息变化会使审核失效；归档前需人工确认。商机变更随归档请求一起提交，须由后端保证一致性。
 * 补充记录 PATCH /visits/:id 携带 version_no；仅提交补充字段，不重写沟通正文/下一步。失败不清草稿。
 * 下一步计划是拜访字段；本页未调用 POST /tasks，不能把“有 next_action”直接视作正式任务已创建。
 */
const { scoreLight } = require('../../utils/statusLight');
const api = require("../../utils/apiClient");
const access = require("../../utils/access");
const {adviceAllowed,adviceState}=require('../../utils/businessAdviceState');
const customerSearch = require('../../utils/visitCustomerSearch');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);
const visitDraft = require("../../utils/visitDraft");
const flow = require("../../utils/visitFlow");
const snapshot = require("../../utils/visitSnapshot");
const dates = require("../../utils/visitDates");
const firstVisit = require("../../utils/visitFirstVisit");
const opportunityAI = require('../../utils/visitOpportunityAI');
const opportunityAmount = require('../../utils/opportunityAmount');

function visitTargetType(value) {
  return ["伙伴", "partner"].includes(String(value || "").toLowerCase()) ? "伙伴" : "客户";
}

Page({
  data: {
    flowVersion: 1, flowStep: "edit", sourceRunId: "", summary: "", reviewPayload: null, pendingReviewId: "",
    analysisPhrase: "正在分析这份拜访记录…", advice: null, adviceStatus:'idle',adviceBusy: false, adviceError: "", showAdvice: false,
    values: {},
    linkedPartnerName: '',
    core: [],
    optional: [],
    customerId: "",
    customerName: "",
    customerQuery: "",
    customers: [],
    searching: false,
    customerSearchError:'',customerSearchDone:false,
    customerConfirmed: false,
    opportunityOptions: [{ id: "", name: "不关联商机" }],
    opportunityIndex: 0,
    opportunityQuery: "", opportunityItems: [], opportunityLoading: false, opportunityError: "",
    opportunityHasMore: false, opportunityNextOffset: 0, opportunityPickerOpen: false, opportunityResolveError: "",
    opportunityPendingId: "", opportunityPendingOption: null, opportunityDrafts: {},
    selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null,
    opportunityAIRecognized: false, opportunityAIHint: "",
    collaboratorIds: [],
    colleagues: [],
    showColleagues: false,
    collaboratorNames: "",
    quality: null,
    score: null, scoreSignal:scoreLight(null),
    grade: "",
    nextReviewPassed: false,
    nextReviewStatus: "待审核",
    reviewStale: true,
    reviewRunId: "",
    reviewedContent: "", reviewedOpportunityId: "",
    blockReason: "",
    busy: false,
    canSubmit: false,
    archived: false,
    editing: false,
    visitId: "",
    version: 1,
    errorText: "",
    sourceImportId: "",
    archivedCount: 0,
    customerType: "客户",
    customerTypeLabel: "客户",
    customerTypeOptions: ["客户", "伙伴"],
    customerTypeIndex: 0,
    colleagueQuery: "",
    recorderName: "",
    visitDate: "",
    visitDateLabel: "",
    datePickerValue: dates.today(),
    createdDate: "",
    createdDateLabel: "",
    createdDatePickerValue: dates.today(),
    sevenLabel: "未判断",
    isFirstVisit: false,
    firstVisitFields: [],
    contactRoleOptions: firstVisit.CONTACT_ROLE_OPTIONS,
    contactRoleIndex: 0,
  },
  onLoad(options = {}) {
    this.draftReady = false; this.draftReadFailed = false;
    this.confirmOptions = options;
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-confirm', options)) return;
    if (!getApp().ensureLogin()) return;
    this.writeIdentity=access.identity(getApp().globalData.session);this.loadGeneration=(this.loadGeneration||0)+1;this.adviceReadSerial=(this.adviceReadSerial||0)+1;
    const load=writes.capture(this,()=>this.loadGeneration);
    this.setData({isFde:require('../../utils/access').assignedVisitOnly(getApp().globalData.session)});
    this.confirmInitialized = true;
    this.closed = false;
    this.userKey = visitDraft.scope(getApp().globalData.session, api);
    this.loadBusinessOptions();
    this.setData({ recorderName: getApp().globalData.session.userName });
    this.draftContext = visitDraft.context(options);
    this.draftKey = options.visitId ? visitDraft.key("visitSupplementV1", this.userKey, encodeURIComponent(options.visitId))
      : visitDraft.key("visitConfirmV2", this.userKey, this.draftContext);
    this.setData({draftNotice:"",draftSaveError:"",draftSavedAt:0,draftConflict:false});
    try { this.draftStore = visitDraft.open(wx, this.draftKey); }
    catch (_) { this.draftReadFailed = true; this.setData({draftSaveError:"草稿读取失败，请保留此页面并重新打开后重试"}); }
    if (!this.data.isFde) api
      .request({ path: "/directory/colleagues" })
      .then((r) => {
        if(!load.current())return;
        this.allColleagues = r.items || r;
        this.refresh();
      })
      .catch((e) => {if(load.current())this.setData({ errorText: `协同人加载失败：${e.message}` });});
    if (options.visitId) {
      this.setData({
        editing: true,
        visitId: options.visitId,
        busy: true,
      });
      api
        .request({ path: `/visits/${options.visitId}` })
        .then((r) => {
          if(!load.current())return;
          this.setData({
            values: firstVisit.normalizeValues(r, firstVisit.enabled(r)),
            isFirstVisit: firstVisit.enabled(r),
            customerId: r.customer_id,
            customerName: r.customer_name,
            customerConfirmed: true,
            collaboratorIds: r.collaborator_ids || [],
            customerType: visitTargetType(r.customer_type),
            recorderName: r.recorder_name || "未记录",
            version: r.version_no,
            busy: false,
          });
          this.editSavedSignature = JSON.stringify([this.data.values, this.data.collaboratorIds]);
          const saved = this.draftStore && this.draftStore.value;
          if (saved && saved.visitId === options.visitId && saved.customerId === r.customer_id) {
            this.setData({values:saved.values,collaboratorIds:saved.collaboratorIds || [],version:saved.version,
              draftNotice:"已恢复本条跟进未提交的补充信息",draftConflict:saved.version !== r.version_no});
            if (this.data.draftConflict) this.setData({draftSaveError:"这条跟进已被更新，草稿仍保留。请核对后放弃旧草稿并重新编辑"});
          }
          this.draftReady = true;
          this.refresh();
        })
        .catch((e) => {if(load.current())this.fail(e);});
      return;
    }
    let source = {};
    try { source = wx.getStorageSync(visitDraft.key("visitStructuredV2", this.userKey, this.draftContext)) || {}; }
    catch (_) { this.draftReadFailed = true; this.setData({draftSaveError:"草稿读取失败，请重新打开后重试"}); }
    const storedDraft = this.draftStore && this.draftStore.value;
    const draft = storedDraft && (!source.draftId || storedDraft.draftId === source.draftId) ? storedDraft : null;
    this.entryDraftId = source.draftId || "";
    if (draft) this.setData({draftNotice:"已恢复上次未提交的跟进草稿"});
    const result = source.result || {};
    const restored = flow.restoreReview(source, draft);
    if(restored.flowStep==='analyzing')restored.flowStep='edit';
    this.aiOpportunitySuggestion = draft ? draft.pendingOpportunitySuggestion || null : opportunityAI.normalizeOpportunitySuggestion(source.opportunitySuggestion || result);
    const isFirstVisit = typeof restored.isFirstVisit === "boolean"
      ? restored.isFirstVisit
      : typeof source.isFirstVisit === "boolean"
        ? source.isFirstVisit
        : firstVisit.enabled(restored.values);
    const boundCustomerId = source.customerHintId || restored.customerId || "";
    const boundCustomerName = source.customerHint || restored.customerName || "";
    const values = firstVisit.normalizeValues(restored.values, isFirstVisit);
    this.setData({
        sourceImportId: source.sourceImportId || "",
        sourceRunId: source.runId || (draft || {}).sourceRunId || "", summary: result.summary || "",
        ...restored,
        customerId: boundCustomerId,
        customerName: boundCustomerName,
        customerQuery: boundCustomerName,
        customerConfirmed: Boolean(boundCustomerId && boundCustomerName),
        customerType: visitTargetType(restored.customerType || (result.fields && result.fields.customer_type)),
        isFirstVisit,
        values: draft ? values : dates.withDefaultDates(values),
    });
    // A saved empty selection is intentional; never revive the entry's old ID.
    this.setData({opportunityId:draft ? draft.opportunityId || "" : source.opportunityId || "",selectedOpportunity:null});
    if(this.data.isFde) {this.aiOpportunitySuggestion=null;this.setData({opportunityEditing:false,opportunityDraft:null,opportunityAIRecognized:false,opportunityAIHint:""});}
    const optionsForRestore=this.opportunityOptionsFor([]);
    this.setData({opportunityOptions:optionsForRestore,opportunityIndex:Math.max(0,optionsForRestore.findIndex(row=>row.id===this.data.opportunityId))});
    this.refresh();
    this.draftReady = true;
    // Persist the actual defaults once so reopening after midnight keeps this visit's dates.
    if (!draft) this.persist();
    if (!this.data.customerConfirmed) this.searchCustomers();
    else this.loadOpportunities();
  },
  loadBusinessOptions() {
    if(require('../../utils/businessOptions').isReady()) {this.setData({contactRoleOptions:firstVisit.CONTACT_ROLE_OPTIONS,catalogError:''});return;}
    const context=access.identity(getApp().globalData.session);
    return api.getBusinessOptions().then(()=>{
      if(!this.closed&&context===access.identity(getApp().globalData.session))this.setData({contactRoleOptions:firstVisit.CONTACT_ROLE_OPTIONS,catalogError:''});
    }).catch(error=>{if(!this.closed&&context===access.identity(getApp().globalData.session))this.setData({catalogError:error.message||'业务选项加载失败，请重试'});});
  },
  onReady() {},
  onShow() {
    if (this.confirmOptions && !this.confirmInitialized) return this.onLoad(this.confirmOptions);
    if(typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this,"visit-confirm",this.data.editing?{visitId:this.data.visitId}:{}))return;
    if(this.writeIdentity!==undefined && this.writeIdentity!==access.identity(getApp().globalData.session)){
      this.preparing=false;this.allColleagues=undefined;clearInterval(this.analysisTimer);clearTimeout(this.searchTimer);
      this.invalidateOpportunityReads();
      this.setData({busy:false,adviceBusy:false,advice:null,adviceStatus:'idle',adviceError:'',showAdvice:false,archived:false,values:{},reviewPayload:null,quality:null,reviewRunId:'',reviewedContent:'',
        collaboratorsOpen:false,colleagueQuery:'',colleagues:[],collaboratorIds:[],collaboratorNames:'',customers:[],searching:false,
        selectedOpportunity:null,opportunityItems:[],opportunityDrafts:{},opportunityDraft:null,opportunityPendingId:'',opportunityPendingOption:null,opportunityPickerOpen:false});
      return this.onLoad(this.confirmOptions || {});
    }
    const resume=this.closed;this.closed=false;
    if(!this.data.isFde && this.data.archived)this.reloadAdvice();
    if(resume && !this.data.editing && !this.data.archived){if(this.data.customerConfirmed)this.loadOpportunities();else this.searchCustomers();}
  },
  onHide() { this.persist();this.closed=true;clearTimeout(this.searchTimer);this.searchSerial=(this.searchSerial||0)+1;this.adviceReadSerial=(this.adviceReadSerial||0)+1;this.setData({searching:false,...(!(this.pageWrites&&this.pageWrites.advice)?{adviceBusy:false}: {})});this.closeOpportunityPicker();this.invalidateOpportunityReads(); },
  onUnload() { this.onHide();this.unloaded=true;clearInterval(this.analysisTimer);clearTimeout(this.searchTimer); },
  refresh() {
    const d = this.data;
    // Reconcile restored selections only after the current directory has loaded.
    // Tell the user before review/save; do not retain hidden, unselectable IDs.
    if (Array.isArray(this.allColleagues)) {
      const eligible = new Set(this.allColleagues.map(c => c.id));
      const ids = d.collaboratorIds.filter(id => eligible.has(id));
      if (ids.length !== d.collaboratorIds.length) this.setData({
        collaboratorIds: ids,
        errorText: '已移除不在当前协同人目录中的选择，请重新核对；FDE 请在商机的协助 FDE 中选择。',
      });
    }
    this.refreshGate();
    const names = (this.allColleagues || [])
      .filter((c) => d.collaboratorIds.includes(c.id))
      .map((c) => c.display_name || c.name);
    this.setData({
      customerTypeLabel: visitTargetType(d.customerType),
      customerTypeIndex: visitTargetType(d.customerType) === "伙伴" ? 1 : 0,
      visitDate: dates.dateValue(d.values.interaction_at),
      visitDateLabel: dates.dateLabel(d.values.interaction_at),
      datePickerValue: dates.dateValue(d.values.interaction_at) || dates.today(),
      createdDate: dates.dateValue(d.values.created_date),
      createdDateLabel: dates.dateLabel(d.values.created_date),
      createdDatePickerValue: dates.dateValue(d.values.created_date) || dates.today(),
      sevenLabel: dates.sevenLabel(dates.withinSevenDays(d.values.interaction_at)),
      firstVisitFields: firstVisit.REQUIRED_FIELDS.map((field) => ({
        ...field,
        value: d.values[field.key] || "",
      })),
      contactRoleIndex: Math.max(0, firstVisit.CONTACT_ROLE_OPTIONS.indexOf(d.values.contact_role)),
      core: flow.CORE.map((f) => ({ ...f, value: d.values[f.key] || "" })),
      optional: flow.OPTIONAL.map((f) => ({
        ...f,
        value: d.values[f.key] || "",
      })),
      colleagues: (this.allColleagues || []).filter((c) =>
        `${c.display_name || c.name} ${c.account_code || ""}`.toLowerCase().includes(d.colleagueQuery.trim().toLowerCase())
      ).map((c) => ({
        ...c,
        name: c.display_name || c.name,
        selected: d.collaboratorIds.includes(c.id),
      })),
      collaboratorNames: names.join("、"),
      score: d.quality ? d.quality.follow_up_score : null,
      scoreSignal:scoreLight(d.reviewStale ? null : d.quality ? d.quality.follow_up_score : null),
      grade: d.quality ? flow.grade(d.quality.follow_up_score, d.quality) : "",
      scorePassed: flow.scorePasses(d.quality),
      admissionRequirement: flow.admissionRequirement(d.quality),
      nextReviewPassed: Boolean(d.quality && d.quality.next_action && d.quality.next_action.passed === true && !d.reviewStale),
      nextReviewStatus: !d.quality
        ? "待审核"
        : d.reviewStale
          ? "需重新审核"
          : d.quality.next_action && d.quality.next_action.passed === true
            ? "通过"
            : "未通过",
    });
  },
  refreshGate() {
    const reviewStale = !this.data.reviewedContent ||
      snapshot.signature(this.data) !== this.data.reviewedContent;
    const blockReason = this.opportunityNeedsResolution()
      ? "请先核对关联商机，或明确选择不关联商机" : flow.archiveBlockReason({ ...this.data, reviewStale });
    this.setData({ reviewStale, blockReason, canSubmit: !blockReason, linkedPartnerName: snapshot.partnerName(this.data) });
  },
  persist() {
    if (!this.draftReady || this.unloaded || this.data.archived || this.draftReadFailed || !this.draftKey) return false;
    if(this.userKey && this.userKey!==visitDraft.scope(getApp().globalData.session, api))return false;
    const d = this.data;
    try {
      if (!this.draftStore) this.draftStore = visitDraft.open(wx, this.draftKey);
      if (d.editing && JSON.stringify([d.values, d.collaboratorIds]) === this.editSavedSignature &&
          !this.draftStore.hasValue() && this.draftStore.current()) return true;
      this.draftStore.write(d.editing ? {visitId:d.visitId,customerId:d.customerId,version:d.version,
        values:d.values,collaboratorIds:d.collaboratorIds,savedAt:Date.now()} : {
      draftId:this.entryDraftId || "",
      values: d.values,
      customerId: d.customerId,
      customerName: d.customerName,
      customerQuery: d.customerQuery,
      customerConfirmed: d.customerConfirmed,
      customerType: d.customerType,
      opportunityId: d.opportunityId || "",
      opportunityDraft: d.opportunityDraft,
      opportunityDrafts: d.opportunityDrafts,
      opportunityAIRecognized: d.opportunityAIRecognized,
      opportunityAIHint: d.opportunityAIHint,
      pendingOpportunitySuggestion: this.aiOpportunitySuggestion || null,
      collaboratorIds: d.collaboratorIds,
      flowVersion:1, flowStep:d.flowStep==='analyzing'?'edit':d.flowStep,
      sourceRunId:d.sourceRunId, summary:d.summary, reviewPayload:d.reviewPayload, pendingReviewId:d.pendingReviewId,
      quality: d.quality,
      reviewStale: d.reviewStale,
      reviewRunId: d.reviewRunId,
      reviewedContent: d.reviewedContent,
      reviewedOpportunityId: d.reviewedOpportunityId,
      sourceImportId: d.sourceImportId,
      isFirstVisit: d.isFirstVisit,
      savedAt:Date.now(),
    });
      this.setData({draftSavedAt:Date.now(),...(d.draftConflict ? {} : {draftSaveError:""})});
      return true;
    } catch(error) {
      this.setData({draftSaveError:["DRAFT_CHANGED","DRAFT_STORAGE_PENDING","DRAFT_STORAGE_CONFLICT"].includes(error.code) ? error.message : "草稿未能保存，请保留此页面后重试"});
      return false;
    }
  },
  discardSavedDraft() {
    if (!this.data.editing || this.data.busy || !this.draftStore) return;
    wx.showModal({title:"放弃这条跟进的本地草稿？",content:"未提交的补充信息将清除，重新加载已保存的跟进。",confirmText:"放弃草稿",success: result => {
      if (!result.confirm || this.userKey !== visitDraft.scope(getApp().globalData.session, api)) return;
      try { this.draftStore.remove(); this.draftReady=false; this.onLoad(this.confirmOptions); }
      catch(error) {this.setData({draftSaveError:error.message || "草稿清除失败，请重试"});}
    }});
  },
  fail(e) {
    this.setData({ busy: false, errorText: e.message || String(e) });
    this.refresh();
    if(!this.closed && !this.unloaded)wx.showToast({ title: this.data.errorText, icon: "none", duration: 3500 });
  },
  inputField(e) {
    const key = e.currentTarget.dataset.key;
    if (this.data.busy || this.data.archived) return;
    if (key === 'partner_name' && !this.data.editing && this.data.opportunityId) return;
    this.setData({
      [`values.${key}`]: e.detail.value,
      errorText: "",
    });
    // Avoid rebuilding the focused textarea on every keystroke.
    this.refreshGate();
    this.persist();
  },
  selectContactRole(e) {
    if (this.data.busy || this.data.archived) return;
    const index = Number(e.detail.value);
    this.setData({
      contactRoleIndex: index,
      "values.contact_role": firstVisit.CONTACT_ROLE_OPTIONS[index],
      errorText: "",
    });
    this.refresh();
    this.persist();
  },
  selectCustomerType(e) {
    if (this.data.busy || this.data.archived) return;
    const customerTypeIndex = Number(e.detail.value);
    const customerType = this.data.customerTypeOptions[customerTypeIndex] || "客户";
    this.setData({ customerTypeIndex, customerType, customerTypeLabel: customerType });
    this.persist();
  },
  inputCustomer(e) {
    if (this.data.busy) return;
    this.searchSerial=(this.searchSerial||0)+1;
    this.setData({opportunityDrafts:{},opportunityPendingId:"",opportunityPendingOption:null});
    this.invalidateOpportunityReads();
    this.setData({opportunityQuery:"",opportunityItems:[],opportunityHasMore:false,opportunityNextOffset:0,opportunityError:"",opportunityResolveError:"",opportunityPickerOpen:false});
    this.setData({
      customerQuery: customerSearch.query(e.detail.value),
      customers:[],searching:true,customerSearchError:'',customerSearchDone:false,
      customerConfirmed: false,
      customerId: "",
      customerName: "",
      customerType: this.data.customerType || "客户",
      opportunityId: "",
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null,
      opportunityAIRecognized: false, opportunityAIHint: "",
      opportunityIndex: 0,
      opportunityOptions: [{ id: "", name: "不关联商机" }],
      canSubmit: false,
    });
    this.refreshGate();
    clearTimeout(this.searchTimer);
    this.searchTimer = setTimeout(() => this.searchCustomers(), 250);
    this.persist();
  },
  searchCustomers() {
    if(this.data.busy)return;
    clearTimeout(this.searchTimer);
    const query = customerSearch.query(this.data.customerQuery).trim(),serial=this.searchSerial=(this.searchSerial||0)+1;
    const context=writes.capture(this,()=>[this.loadGeneration,this.data.customerQuery.trim()]);
    const current=()=>serial===this.searchSerial && context.visible();
    const session=getApp().globalData.session;this.customerSearchIdentity=access.identity(session);
    this.setData({ searching: true,customers:[],customerSearchError:'',customerSearchDone:false,isFde:access.assignedVisitOnly(session) });
    return customerSearch.list(api,session,{q:query,pageSize:100})
      .then((r) => {
        if (!current()) return;
        if(!r || !Array.isArray(r.items))throw Error('客户列表响应不完整');
        this.setData({ customers: r.items, searching: false,customerSearchDone:true });
      })
      .catch((e) => {
        if(!current())return;
        this.setData({ searching: false,customers:[],customerSearchError:customerSearch.errorMessage(e),customerSearchDone:false });
      });
  },
  retryCustomerSearch(){return this.searchCustomers();},
  chooseCustomer(e) {
    if (this.data.busy || this.data.searching) return;
    if(this.customerSearchIdentity!==undefined && this.customerSearchIdentity!==access.identity(getApp().globalData.session))return;
    this.setData({opportunityDrafts:{},opportunityPendingId:"",opportunityPendingOption:null});
    this.invalidateOpportunityReads();
    this.setData({opportunityQuery:"",opportunityItems:[],opportunityHasMore:false,opportunityNextOffset:0,opportunityError:"",opportunityResolveError:"",opportunityPickerOpen:false});
    const customer = this.data.customers.find(
      (c) => c.id === e.currentTarget.dataset.id,
    );
    if (!customer) return;
    clearTimeout(this.searchTimer);this.searchSerial=(this.searchSerial||0)+1;
    this.setData({
      customerId: customer.id,
      customerName: customer.name,
      customerQuery: customer.name,
      customerConfirmed: true,
      customers: [],
      searching:false,customerSearchError:'',customerSearchDone:false,
      opportunityId: "",
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null,
      opportunityAIRecognized: false, opportunityAIHint: "",
      opportunityIndex: 0,
      errorText: "",
    });
    this.loadOpportunities();
    this.refresh();
    this.persist();
  },
  changeCustomer() {
    if (this.data.busy) return;
    this.setData({opportunityDrafts:{},opportunityPendingId:"",opportunityPendingOption:null});
    this.invalidateOpportunityReads();
    this.setData({opportunityQuery:"",opportunityItems:[],opportunityHasMore:false,opportunityNextOffset:0,opportunityError:"",opportunityResolveError:"",opportunityPickerOpen:false});
    this.setData({
      customerConfirmed: false,
      customerId: "",
      customerType: this.data.customerType || "客户",
      opportunityId: "",
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null,
      opportunityAIRecognized: false, opportunityAIHint: "",
      opportunityIndex: 0,
      opportunityOptions: [{ id: "", name: "不关联商机" }],
    });
    this.refresh();
    this.persist();
    this.searchCustomers();
  },
  canMutateOpportunity() { return !this.data.isFde && this.data.canEditOpportunity !== false; },
  opportunityNeedsResolution() {
    if (this.data.editing) return false;
    const id=this.data.opportunityId || "";
    if (id==='__new__') return !this.canMutateOpportunity();
    return Boolean((this.data.opportunityResolveError && (id || this.aiOpportunitySuggestion)) ||
      (id && (!this.data.selectedOpportunity || this.data.selectedOpportunity.id!==id)));
  },
  opportunityIdentity() { return typeof getApp === "function" ? access.identity(getApp().globalData.session) : ""; },
  invalidateOpportunityReads() {
    clearTimeout(this.opportunitySearchTimer);this._opportunitySeq=(this._opportunitySeq||0)+1;
    this.setData({opportunityLoading:false});
  },
  opportunityOptionsFor(items, selected=this.data.selectedOpportunity) {
    const options=[{id:"",name:"不关联商机"}];
    if(selected && !items.some(row=>row.id===selected.id))options.push(selected);
    options.push(...items);
    if(this.data.opportunityId && this.data.opportunityId!=="__new__" && !options.some(row=>row.id===this.data.opportunityId))
      options.push({id:this.data.opportunityId,name:"已关联商机（待核对）",unverified:true});
    const pending=this.data.opportunityPickerOpen&&this.data.opportunityPendingOption;
    if(pending&&pending.id&&pending.id!=="__new__"&&!options.some(row=>row.id===pending.id))options.push(pending);
    if(this.canMutateOpportunity())options.push({id:"__new__",name:"本次拜访产生新商机"});
    return options;
  },
  async loadOpportunities({more=false,search=false}={}) {
    const id=this.data.customerId;if(!id || this.closed || (more && (this.data.opportunityLoading || !this.data.opportunityHasMore)))return;
    const identity=this.opportunityIdentity(),seq=this._opportunitySeq=(this._opportunitySeq||0)+1;
    const current=()=>!this.closed && seq===this._opportunitySeq && id===this.data.customerId && identity===this.opportunityIdentity();
    const query=search||more?this.data.opportunityQuery.trim():"",offset=more?this.data.opportunityNextOffset:0;
    const initial=!more&&!search;
    if(initial)this.setData({opportunityQuery:""});
    this.setData({opportunityLoading:true,opportunityError:"",opportunityFailedMore:false,...(initial?{opportunityResolveError:"正在核对商机关联"}:{})});
    this.refresh();
    try {
      // Candidate reads are not write authorization; every submission is validated by the server.
      const result=await api.listOpportunities({customerId:id,pageSize:20,includeClosed:true,query,offset});
      if(!current())return;
      if(!result || !Array.isArray(result.items) || result.items.length>20 || typeof result.has_more!=="boolean" ||
        (result.has_more && (!Number.isInteger(result.next_offset)||result.next_offset<=offset)))throw Error("商机列表响应不完整，请重试");
      const items=more?this.data.opportunityItems.slice():[],seen=new Set(items.map(row=>row.id));
      for(const row of result.items){if(!row.id || (row.customer_id && String(row.customer_id)!==String(id)))throw Error("商机客户数据不匹配");if(!seen.has(row.id)){items.push(row);seen.add(row.id);}}
      this.setData({opportunityItems:items,opportunityHasMore:result.has_more,opportunityNextOffset:result.next_offset});
      if(initial){
        // A requested ID is retained while its lookup is pending; it is not a manual choice.
        // Explicit selection consumes the suggestion in selectOpportunity, successful resolution here.
        const pendingSuggestion=this.canMutateOpportunity()?this.aiOpportunitySuggestion:null;
        const suggestion=pendingSuggestion && (!this.data.opportunityId ||
          String(pendingSuggestion.opportunityId||"")===String(this.data.opportunityId))?pendingSuggestion:null;
        const wanted=this.data.opportunityId || (suggestion&&suggestion.opportunityId) || "";
        let selected=items.find(row=>row.id===wanted)||null;
        if(wanted && wanted!=="__new__" && !selected){
          this.setData({opportunityId:wanted,opportunityResolveError:"正在核对已关联商机"});this.refresh();
          const target=await api.getOpportunityDetailOverview(wanted);if(!current())return;
          if(String(target.id)!==String(id))throw Error("已关联商机不属于当前客户，请重新选择");
          selected=(target.opportunities||[]).find(row=>String(row.id)===String(wanted)) || (target.primary_opportunity&&String(target.primary_opportunity.id)===String(wanted)?target.primary_opportunity:null);
          if(!selected)throw Error("已关联商机不存在或无权查看，请重新选择");
        }
        if(suggestion&&suggestion.hasContent&&!wanted){
          selected=opportunityAI.matchExistingOpportunity(items,suggestion);
          if(!selected&&suggestion.name){
            const matches=await api.listOpportunities({customerId:id,pageSize:20,includeClosed:true,query:suggestion.name,offset:0});if(!current())return;
            if(!matches || !Array.isArray(matches.items) || matches.items.length>20 || typeof matches.has_more!=="boolean")throw Error("AI 商机匹配响应不完整，请重试");
            if(matches.items.some(row=>!row.id || (row.customer_id && String(row.customer_id)!==String(id))))throw Error("商机客户数据不匹配");
            selected=opportunityAI.matchExistingOpportunity(matches.items,suggestion);
            if(!selected&&matches.has_more)throw Error("AI 识别的商机名称有多个匹配，请搜索并确认关联");
          }
        }
        const selectedId=wanted || (suggestion&&suggestion.hasContent?(selected?selected.id:"__new__"):"");
        const applySuggestion=Boolean(suggestion&&suggestion.hasContent&&!this.data.opportunityDraft);
        this.setData({opportunityId:selectedId,selectedOpportunity:selected,
          opportunityEditing:this.canMutateOpportunity()&&Boolean(selectedId),
          opportunityDraft:applySuggestion?opportunityAI.formFromSuggestion(selected,suggestion):this.data.opportunityDraft,
          opportunityAIRecognized:applySuggestion||this.data.opportunityAIRecognized,opportunityResolveError:"",
          opportunityAIHint:applySuggestion?(selected?`AI 已匹配已有商机“${selected.name}”，请核对识别字段`:"AI 已识别到新商机信息，请核对并补齐必填项"):this.data.opportunityAIHint});
        if(suggestion)this.aiOpportunitySuggestion=null;
        this.persist();
      }
      const options=this.opportunityOptionsFor(items);
      this.setData({opportunityOptions:options,opportunityIndex:Math.max(0,options.findIndex(row=>row.id===this.data.opportunityId))});
    } catch(error) {if(current()){
      this.setData({opportunityError:error.message||"商机加载失败，请重试",opportunityFailedMore:more,...(initial?{opportunityResolveError:error.message||"请核对商机关联"}:{})});
      const options=this.opportunityOptionsFor(this.data.opportunityItems);this.setData({opportunityOptions:options,opportunityIndex:Math.max(0,options.findIndex(row=>row.id===this.data.opportunityId))});
    }} finally {if(current()){this.setData({opportunityLoading:false});this.refresh();}}
  },
  toggleOpportunityPicker() {
    if(this.data.busy||this.data.archived||this.data.editing||!this.data.customerConfirmed)return;
    if(this.data.opportunityPickerOpen)return this.closeOpportunityPicker();
    const id=this.data.opportunityId||"";
    this.setData({opportunityPickerOpen:true,opportunityPendingId:id,
      opportunityPendingOption:this.data.opportunityOptions.find(row=>row.id===id)||null});
  },
  closeOpportunityPicker() {this.setData({opportunityPickerOpen:false,opportunityPendingId:"",opportunityPendingOption:null});},
  stopOpportunityTap() {},
  confirmOpportunityPicker() {
    if(!this.data.opportunityPickerOpen||this.data.busy)return;
    const index=this.data.opportunityOptions.findIndex(row=>row.id===this.data.opportunityPendingId);
    if(index>=0)this.selectOpportunity({detail:{value:index}});
  },
  searchOpportunities(e) {
    if(this.data.busy)return;this.invalidateOpportunityReads();
    const options=this.opportunityOptionsFor([]);
    this.setData({opportunityQuery:e.detail.value,opportunityItems:[],opportunityHasMore:false,opportunityError:"",opportunityOptions:options,opportunityIndex:Math.max(0,options.findIndex(row=>row.id===this.data.opportunityId))});
    this.opportunitySearchTimer=setTimeout(()=>this.loadOpportunities({search:true}),250);
  },
  moreOpportunities(){return this.loadOpportunities({more:true});},
  retryOpportunities(){return this.loadOpportunities(this.data.opportunityResolveError?{}:this.data.opportunityFailedMore?{more:true}:{search:true});},
  chooseOpportunity(e) {
    if(this.data.busy)return;
    const index=this.data.opportunityOptions.findIndex(row=>row.id===e.currentTarget.dataset.id);
    const selected=this.data.opportunityOptions[index];
    if(!selected||selected.unverified)return;
    if(this.data.opportunityPickerOpen){
      this.setData({opportunityPendingId:selected.id,opportunityPendingOption:selected});return;
    }
    if(index>=0)this.selectOpportunity({detail:{value:index}});
  },
  selectOpportunity(e) {
    if (this.data.busy) return;
    const index = Number(e.detail.value);
    const selected = this.data.opportunityOptions[index];
    if(!selected || selected.unverified)return;
    if(selected.id==='__new__'&&!this.canMutateOpportunity())return;
    const currentId=this.data.opportunityId||"";
    if(selected.id===currentId&&!this.data.opportunityResolveError&&!this.aiOpportunitySuggestion&&(!currentId||currentId==='__new__'||this.data.selectedOpportunity)){
      this.closeOpportunityPicker();return;
    }
    const drafts={...this.data.opportunityDrafts};
    if(currentId&&this.data.opportunityDraft)drafts[currentId]=JSON.parse(JSON.stringify(this.data.opportunityDraft));
    this.invalidateOpportunityReads();
    this.aiOpportunitySuggestion = null;
    this.setData({ opportunityResolveError:"",opportunityError:"",opportunityPickerOpen:false,opportunityPendingId:"",opportunityPendingOption:null,
      opportunityDrafts:drafts,opportunityDraft: selected.id&&drafts[selected.id]?JSON.parse(JSON.stringify(drafts[selected.id])):null,
      opportunityIndex: index, opportunityId: selected.id,
      selectedOpportunity: selected.id && selected.id !== '__new__' ? selected : null,
      opportunityEditing: this.canMutateOpportunity() && Boolean(selected.id), opportunityAIRecognized: false, opportunityAIHint: "" });
    this.refresh();this.persist();
  },
  changeOpportunityForm(e) {
    if(e.detail.customerId!==undefined && (e.detail.customerId!==this.data.customerId || e.detail.opportunityId!==this.data.opportunityId))return;
    const opportunityDraft = e.detail.form;
    this.setData({ opportunityDraft });
    this.refreshGate();
    this.persist();
  },
  toggleColleagues() {
    if (this.data.isFde) return;
    this.setData({ showColleagues: !this.data.showColleagues });
  },
  inputColleagueQuery(e) {
    this.setData({ colleagueQuery: e.detail.value });
    this.refresh();
  },
  selectVisitDate(e) {
    if (this.data.busy) return;
    this.setData({ "values.interaction_at": e.detail.value });
    this.refresh();
    this.persist();
  },
  clearVisitDate() {
    if (this.data.busy) return;
    this.setData({ "values.interaction_at": "" });
    this.refresh();
    this.persist();
  },
  selectCreatedDate(e) {
    if (this.data.busy) return;
    this.setData({ "values.created_date": e.detail.value });
    this.refresh();
    this.persist();
  },
  clearCreatedDate() {
    if (this.data.busy) return;
    this.setData({ "values.created_date": "" });
    this.refresh();
    this.persist();
  },
  selectColleagues(e) {
    if (this.data.isFde) return;
    const visible = new Set(this.data.colleagues.map(c => c.id));
    const hiddenSelected = this.data.collaboratorIds.filter(id => !visible.has(id));
    this.setData({ collaboratorIds: [...new Set([...hiddenSelected, ...e.detail.value])] });
    this.refresh();
    this.persist();
  },
  saveDraft() {
    if (this.persist()) wx.showToast({ title: "草稿已保存", icon: "success" });
  },
  backToEdit() {
    if(this.data.busy)return;
    this.setData({flowStep:'edit',errorText:''});this.refresh();this.persist();
    wx.pageScrollTo({scrollTop:0,duration:180});
  },
  startAnalysis() {
    const phrases=['正在分析这份拜访记录…','请稍候，AI 正在生成质检意见…','内容已提交，正在等待分析结果…'];
    let index=0;clearInterval(this.analysisTimer);
    this.setData({flowStep:'analyzing',busy:true,errorText:'',analysisPhrase:phrases[0]});
    this.analysisTimer=setInterval(()=>{if(!this.unloaded)this.setData({analysisPhrase:phrases[++index%phrases.length]});},3500);
    wx.pageScrollTo({scrollTop:0,duration:180});
  },
  async review() {
    if(this.data.busy || this.preparing)return;
    if(this.persist() === false)return;
    if(!this.data.customerConfirmed)return this.fail('请先确认关联客户');
    if(this.opportunityNeedsResolution())return this.fail('请先核对关联商机，或明确选择不关联商机');
    let fields=snapshot.fields(this.data);
    const missing=[...flow.CORE,...flow.REQUIRED_DETAILS].find(f=>!fields[f.key]);
    if(missing)return this.fail(`请补充${missing.label}`);
    const firstMissing=firstVisit.missingField(fields);
    if(firstMissing)return this.fail(`首次拜访请补充${firstMissing.label}`);
    if(!this.data.sourceRunId)return this.fail('请返回录入页重新整理原文');
    const write=writes.begin(this,'review',()=>[this.data.customerId,this.data.sourceRunId,this.entryDraftId || '']);if(!write)return;
    const prepareSignature=snapshot.signature(this.data);
    this.preparing=write;
    let mutation;
    try {mutation=!this.data.isFde && this.data.opportunityEditing ? await this.selectComponent('#visitOpportunityForm').prepare() : null;}
    catch(error){if(this.preparing===write)this.preparing=false;if(write.current())this.fail(error);write.finish();return;}
    if(this.preparing===write)this.preparing=false;
    if(!write.visible()){write.finish();return;}
    if(prepareSignature!==snapshot.signature(this.data)){write.finish();return this.fail('内容已变化，请重新质检');}
    fields=snapshot.fields(this.data);
    const signature=snapshot.signature(this.data);
    const payload={customer_id:this.data.customerId,opportunity_id:this.data.opportunityId && this.data.opportunityId!=='__new__'?this.data.opportunityId:null,
      source_run_id:this.data.sourceRunId,fields,summary:fields.follow_up_record.slice(0,5000),
      source_import_id:this.data.sourceImportId || null,collaborator_ids:this.data.isFde?[]:this.data.collaboratorIds,
      fde_participant_ids:!this.data.isFde && this.data.opportunityEditing ? (this.data.opportunityDraft || {}).visit_fde_member_ids || [] : [],
      opportunity_mutation:mutation};
    const same=this.data.reviewPayload && JSON.stringify(this.data.reviewPayload)===JSON.stringify(payload);
    let id=same?this.data.pendingReviewId:'';
    this.setData({reviewPayload:payload,quality:null,reviewRunId:'',reviewedContent:'',pendingReviewId:id});
    this.startAnalysis();this.persist();
    const analysisTimer=this.analysisTimer;
    try {
      if(!id){const accepted=await api.submitVisitStage('quality',payload);id=accepted.run_id;
        if(!write.current())return;this.setData({pendingReviewId:id});this.persist();}
      const run=await api.waitVisitRun(id);
      if(!write.current())return;
      if(signature!==snapshot.signature(this.data))throw Error('内容已变化，请重新质检');
      const result=run.result || {}, quality=result.quality_review;
      if(result.visit_stage!=='quality' || !quality || !Number.isInteger(quality.follow_up_score))throw Error('质检没有返回有效评分，请重试');
      this.setData({quality,busy:false,flowStep:'result',pendingReviewId:'',reviewRunId:id,reviewedContent:signature});
      this.refresh();this.persist();
    } catch(error) {
      if(!write.current())return;
      // Keep the accepted run through transport/read failures; changed content is separated by reviewPayload.
      this.setData({flowStep:'edit',pendingReviewId:['failed','cancelled'].includes(error.runStatus)?'':id});
      this.fail(error);this.persist();
    } finally {clearInterval(analysisTimer);write.finish('busy');}
  },
  async archive() {
    this.refreshGate();
    if(!this.data.canSubmit || this.data.busy)return;
    const confirmation=writes.begin(this,'archiveConfirm',()=>[this.data.customerId,this.data.reviewRunId,snapshot.signature(this.data),this.data.reviewPayload,this.data.selectedOpportunity]);if(!confirmation)return;
    try {
      const mutation=this.data.reviewPayload.opportunity_mutation;
      const assessment=mutation ? await opportunityAmount.assess(api,mutation,this.data.selectedOpportunity) : {mutation:null,required:false,content:''};
      if(!confirmation.visible()){confirmation.finish();return;}
      wx.showModal({
      title: "确认并归档这次拜访？",
      content: `客户：${this.data.customerName}\n商机：${mutation ? mutation.name : (this.data.opportunityOptions[this.data.opportunityIndex] || {name:"不关联商机"}).name}\n${assessment.required ? assessment.content+'\n' : ''}请确认以上关联与拜访内容准确。`,
      confirmText: "确认归档",
      success: (r) => {
        if(confirmation.started)return;
        if (r.confirm && confirmation.visible() && confirmation.start()) this.submitArchive(opportunityAmount.acknowledged(assessment));
        confirmation.finish();
      },
      fail:()=>confirmation.finish(),
      });
    } catch(error) {
      if(confirmation.current())this.setData({errorText:error.message || '金额核对失败，请重试保存'});
      confirmation.finish();
    }
  },
  submitArchive(confirmedMutation) {
    if (this.data.archived || !this.data.canSubmit || this.data.busy) return;
    if (this.persist() === false) return;
    const write=writes.begin(this,'archive',()=>[this.data.customerId,this.data.reviewRunId,this.entryDraftId || '']);if(!write)return;
    const customerId=this.data.customerId,payload=JSON.parse(JSON.stringify(this.data.reviewPayload));
    payload.opportunity_mutation=confirmedMutation || opportunityAmount.cleanMutation(payload.opportunity_mutation);
    const reviewRunId=this.data.reviewRunId,userKey=this.userKey,draftKey=this.draftKey,entryDraftId=this.entryDraftId;
    this.setData({ busy: true, errorText: "" });
    this.refresh();
    api
      .createVisit(customerId, {
        ...payload.fields,
        opportunity_id:payload.opportunity_id,
        _opportunity_mutation:payload.opportunity_mutation,
        collaborator_ids:payload.collaborator_ids,
        source_import_id:payload.source_import_id,
        _quality_review_run_id:reviewRunId,
      }, payload.fde_participant_ids || [])
      .then((r) => {
        if(!write.current())return;
        this.setData({
          archived: true,
          visitId: r.id,
          busy: false,
          archivedCount:
            r.completed_count ||
            Object.values(r.fields || {}).filter(Boolean).length,
          quality: { ...this.data.quality, ...r.quality_review },
        });
        this.refresh();
        try {
          if (this.draftStore && !this.draftStore.current()) throw Error("草稿已在其他页面更新");
          visitDraft.removeAction(wx, [draftKey, ...["visitEntryV2", "visitStructuredV2", "visitStructureRun"]
            .map(prefix => visitDraft.key(prefix, userKey, this.draftContext))], entryDraftId);
        } catch (_) {
          this.setData({archiveWarning:"记录已保存，本地草稿清理失败，请勿重复提交。"});
        }
        if(!this.data.isFde)this.loadAdvice();
        if(write.visible())wx.setNavigationBarTitle({ title: "拜访已归档" });
      })
      .catch((e) => {if(write.current())this.fail(e);}).finally(()=>write.finish('busy'));
  },
  async loadAdvice() {
    if(this.data.isFde || this.data.adviceBusy || !this.data.archived || !adviceAllowed(getApp().globalData.session,'visit',true))return;
    if(['queued','running'].includes(this.data.adviceStatus))return this.reloadAdvice();
    const write=writes.begin(this,'advice',()=>this.data.visitId);if(!write)return;
    const visitId=this.data.visitId;this.adviceReadSerial=(this.adviceReadSerial||0)+1;
    const retry=Boolean(this.data.adviceError || ['ready','stale'].includes(this.data.adviceStatus));
    this.setData({showAdvice:true,adviceBusy:true,adviceError:''});
    try {const result=await api.requestBusinessAdvice('visit',visitId,'tasks',retry);
      const state=adviceState(result,'visit',visitId,'tasks');
      if(write.current())this.setData({advice:result,adviceStatus:state.status,adviceError:state.error || ''});}
    catch(error){if(write.current())this.setData({advice:null,adviceStatus:'error',adviceError:'记录已保存，待办建议生成未确认：'+(error.message || '请重试')});}
    finally {write.finish('adviceBusy');}
  },
  async reloadAdvice() {
    if(this.data.isFde || !this.data.archived || this.data.adviceBusy || !adviceAllowed(getApp().globalData.session,'visit'))return;
    const visitId=this.data.visitId;
    const read=writes.capture(this,()=>this.data.visitId),serial=this.adviceReadSerial=(this.adviceReadSerial||0)+1;
    const current=()=>serial===this.adviceReadSerial && read.current();
    this.setData({adviceBusy:true,advice:null,adviceStatus:'idle',adviceError:''});
    try{const result=await api.getLatestBusinessAdvice('visit',visitId,'tasks');const state=adviceState(result,'visit',visitId,'tasks',{allowEmpty:true});
      if(current())this.setData({advice:result,adviceStatus:state.status,adviceError:state.error || ''});}
    catch(error){if(current())this.setData({advice:null,adviceStatus:'error',adviceError:error.message});}
    finally{if(current())this.setData({adviceBusy:false});}
  },
  closeAdvice(){this.adviceTouch=null;this.setData({showAdvice:false});},
  openAdvice(){if(!this.data.isFde){this.setData({showAdvice:true});this.reloadAdvice();}},
  startAdviceDrag(e){this.adviceTouch=e.touches.length===1 ? e.touches[0] : null;},
  endAdviceDrag(e){
    const start=this.adviceTouch, end=e.changedTouches && e.changedTouches[0];
    this.adviceTouch=null;
    if(start && end && end.clientY-start.clientY>45 && end.clientY-start.clientY>Math.abs(end.clientX-start.clientX)*1.5)this.closeAdvice();
  },
  cancelAdviceDrag(){this.adviceTouch=null;},
  stopAdviceTap(){},
  saveSupplement() {
    if (this.data.busy || this.data.draftConflict) return;
    if (this.persist() === false) return;
    const write=writes.begin(this,'supplement',()=>[this.data.visitId,this.data.customerId]);if(!write)return;
    const visitId=this.data.visitId;
    const body = {
      version_no: this.data.version,
      ...(this.data.isFde ? {} : {collaborator_ids: this.data.collaboratorIds}),
      interaction_at: this.data.values.interaction_at || "",
      created_date: this.data.values.created_date || "",
    };
    flow.OPTIONAL.forEach((f) => {
      body[f.key] = this.data.values[f.key] || "";
    });
    if (this.data.isFirstVisit) {
      body.is_first_visit = true;
      firstVisit.REQUIRED_FIELDS.forEach((field) => {
        body[field.key] = this.data.values[field.key] || "";
      });
    }
    this.setData({ busy: true, errorText: "" });
    api
      .request({
        path: `/visits/${visitId}`,
        method: "PATCH",
        data: body,
      })
      .then((r) => {
        if(!write.current())return;
        this.setData({ version: r.version_no, values: r, busy: false });
        this.editSavedSignature = JSON.stringify([this.data.values, this.data.collaboratorIds]);
        try {
          if (this.draftStore) this.draftStore.remove();
          this.draftStore = visitDraft.open(wx, this.draftKey);
          this.setData({draftNotice:"",draftSavedAt:0,draftSaveError:""});
        } catch (_) {this.setData({draftSaveError:"补充信息已保存，另一页面的草稿已保留，请勿重复提交"});}
        this.refresh();
        if(write.visible())wx.showToast({ title: "补充信息已保存", icon: "success" });
      })
      .catch((e) => {if(write.current())this.fail(e);}).finally(()=>write.finish('busy'));
  },
  openVisit() {
    const {customerId,visitId}=this.data;
    if(!customerId || !visitId)return wx.showToast({title:"缺少本次跟进记录标识",icon:"none"});
    wx.navigateTo({url:`/pages/visit-detail/index?customer_id=${encodeURIComponent(customerId)}&visit_id=${encodeURIComponent(visitId)}`,
      fail:()=>wx.showToast({title:"打开跟进详情失败，请重试",icon:"none"})});
  },
  openCustomer() {
    const customerId = this.data.customerId;
    if (!customerId) {
      wx.showToast({ title: "未找到本次拜访的客户", icon: "none" });
      return;
    }
    // 作战地图的新版详情通过待打开客户接收跨 tab 导航。
    wx.removeStorageSync("pendingBattleCustomerId");
    wx.removeStorageSync("pendingOpenOpportunityId");
    wx.setStorageSync("pendingOpenCustomerId", customerId);
    wx.switchTab({
      url: "/pages/customers/index",
      fail: () => {
        if (wx.getStorageSync("pendingOpenCustomerId") === customerId) {
          wx.removeStorageSync("pendingOpenCustomerId");
        }
        wx.showToast({ title: "打开客户详情失败，请重试", icon: "none" });
      },
    });
  },
});
