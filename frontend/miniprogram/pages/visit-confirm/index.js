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
const {draftScope} = require("../../utils/draftScope");
const flow = require("../../utils/visitFlow");
const snapshot = require("../../utils/visitSnapshot");
const dates = require("../../utils/visitDates");
const firstVisit = require("../../utils/visitFirstVisit");
const opportunityAI = flow;

function visitTargetType(value) {
  return ["伙伴", "partner"].includes(String(value || "").toLowerCase()) ? "伙伴" : "客户";
}

Page({
  data: {
    flowVersion: 1, flowStep: "edit", sourceRunId: "", summary: "", reviewPayload: null, pendingReviewId: "",
    analysisPhrase: "正在分析这份拜访记录…", advice: null, adviceBusy: false, adviceError: "", showAdvice: false,
    values: {},
    core: [],
    optional: [],
    customerId: "",
    customerName: "",
    customerQuery: "",
    customers: [],
    searching: false,
    customerConfirmed: false,
    opportunityOptions: [{ id: "", name: "不关联商机" }],
    opportunityIndex: 0,
    opportunityQuery: "", opportunityItems: [], opportunityLoading: false, opportunityError: "",
    opportunityHasMore: false, opportunityNextOffset: 0, opportunityPickerOpen: false, opportunityResolveError: "",
    opportunityPendingId: "", opportunityPendingOption: null, opportunityDrafts: {},
    selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null, fdeOpportunityVerified:false,
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
    this.confirmOptions = options;
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-confirm', options)) return;
    if (!getApp().ensureLogin()) return;
    this.confirmInitialized = true;
    this.closed = false;
    this.userKey = draftScope(getApp().globalData.session);
    this.loadBusinessOptions();
    this.setData({ recorderName: getApp().globalData.session.userName });
    this.draftKey = `visitConfirmV2:${this.userKey}`;
    if (!this.data.isFde) api
      .request({ path: "/directory/colleagues" })
      .then((r) => {
        this.allColleagues = r.items || r;
        this.refresh();
      })
      .catch((e) =>
        this.setData({ errorText: `协同人加载失败：${e.message}` }),
      );
    if (options.visitId) {
      this.setData({
        editing: true,
        visitId: options.visitId,
        busy: true,
      });
      api
        .request({ path: `/visits/${options.visitId}` })
        .then((r) => {
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
          this.refresh();
        })
        .catch((e) => this.fail(e));
      return;
    }
    const source = wx.getStorageSync(`visitStructuredV2:${this.userKey}`) || {};
    const storedDraft = wx.getStorageSync(this.draftKey);
    const draft = storedDraft && (!source.draftId || storedDraft.draftId === source.draftId) ? storedDraft : null;
    this.entryDraftId = source.draftId || "";
    const result = source.result || {};
    const restored = flow.restoreReview(source, draft);
    if(restored.flowStep==='analyzing')restored.flowStep='edit';
    this.aiOpportunitySuggestion = draft ? null : opportunityAI.normalizeOpportunitySuggestion(source.opportunitySuggestion || result);
    const isFirstVisit = typeof restored.isFirstVisit === "boolean"
      ? restored.isFirstVisit
      : typeof source.isFirstVisit === "boolean"
        ? source.isFirstVisit
        : firstVisit.enabled(restored.values);
    const boundCustomerId = source.customerHintId || restored.customerId || "";
    const boundCustomerName = source.customerHint || restored.customerName || "";
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
        values: firstVisit.normalizeValues(restored.values, isFirstVisit),
    });
    if(this.data.isFde)this.setData({reviewedOpportunityId:draft ? draft.reviewedOpportunityId || "" : source.opportunityId || "",opportunityId:restored.opportunityId || source.opportunityId || "",fdeOpportunityVerified:false,opportunityEditing:false,opportunityDraft:null,opportunityAIRecognized:false,opportunityAIHint:""});
    this.refresh();
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
    const resume=this.closed;this.closed=false;
    if(!this.data.isFde && this.data.archived && this.data.advice)this.reloadAdvice();
    if(resume && !this.data.isFde && !this.data.editing && this.data.customerConfirmed)this.loadOpportunities();
    if(this.data.isFde && !this.data.editing && this.selectComponent){const picker=this.selectComponent("#fdeVisitOpportunity");if(picker)picker.checkSelection(true);}
  },
  onHide() { this.closed=true;this.closeOpportunityPicker();this.invalidateOpportunityReads(); },
  onUnload() { this.unloaded=true;clearInterval(this.analysisTimer);clearTimeout(this.searchTimer);this.onHide(); },
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
    const opportunityUnverified = !this.data.isFde && !this.data.editing && (this.data.opportunityResolveError ||
      (this.data.opportunityId && this.data.opportunityId !== "__new__" && !this.data.selectedOpportunity));
    const blockReason = opportunityUnverified ? "请先核对关联商机，或明确选择不关联商机" : this.data.isFde && !this.data.editing && (!this.data.opportunityId || !this.data.fdeOpportunityVerified)
      ? "请选择并确认本人参与的商机" : flow.archiveBlockReason({ ...this.data, reviewStale });
    this.setData({ reviewStale, blockReason, canSubmit: !blockReason });
  },
  persist() {
    if (this.data.editing || this.data.archived) return;
    const d = this.data;
    wx.setStorageSync(this.draftKey, {
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
    });
  },
  fail(e) {
    this.setData({ busy: false, errorText: e.message || String(e) });
    this.refresh();
    wx.showToast({ title: this.data.errorText, icon: "none", duration: 3500 });
  },
  inputField(e) {
    const key = e.currentTarget.dataset.key;
    if (this.data.busy || this.data.archived) return;
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
    this.setData({opportunityDrafts:{},opportunityPendingId:"",opportunityPendingOption:null});
    this.invalidateOpportunityReads();
    this.setData({opportunityQuery:"",opportunityItems:[],opportunityHasMore:false,opportunityNextOffset:0,opportunityError:"",opportunityResolveError:"",opportunityPickerOpen:false});
    this.setData({
      customerQuery: e.detail.value,
      customerConfirmed: false,
      customerId: "",
      customerName: "",
      customerType: this.data.customerType || "客户",
      opportunityId: "",
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null, fdeOpportunityVerified:false,
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
    const query = this.data.customerQuery.trim();
    this.setData({ searching: true });
    api
      .listCustomers({ q: query, scope: this.data.isFde ? "self" : "company", pageSize: 100 })
      .then((r) => {
        if (this.closed || query !== this.data.customerQuery.trim()) return;
        this.setData({ customers: r.items || [], searching: false });
      })
      .catch((e) => {
        this.setData({ searching: false });
        this.fail(e);
      });
  },
  chooseCustomer(e) {
    if (this.data.busy) return;
    this.setData({opportunityDrafts:{},opportunityPendingId:"",opportunityPendingOption:null});
    this.invalidateOpportunityReads();
    this.setData({opportunityQuery:"",opportunityItems:[],opportunityHasMore:false,opportunityNextOffset:0,opportunityError:"",opportunityResolveError:"",opportunityPickerOpen:false});
    const customer = this.data.customers.find(
      (c) => c.id === e.currentTarget.dataset.id,
    );
    if (!customer) return;
    this.setData({
      customerId: customer.id,
      customerName: customer.name,
      customerQuery: customer.name,
      customerConfirmed: true,
      customers: [],
      opportunityId: "",
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null, fdeOpportunityVerified:false,
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
      selectedOpportunity: null, opportunityEditing: false, opportunityDraft: null, fdeOpportunityVerified:false,
      opportunityAIRecognized: false, opportunityAIHint: "",
      opportunityIndex: 0,
      opportunityOptions: [{ id: "", name: "不关联商机" }],
    });
    this.refresh();
    this.persist();
    this.searchCustomers();
  },
  fdeOpportunityChanged(e) {
    const row=e.detail.opportunity;
    this.setData({fdeOpportunityVerified:e.detail.verified===true, opportunityEditing:false, opportunityDraft:null,
      ...(row?{opportunityId:row.id,selectedOpportunity:row,opportunityOptions:[{id:"",name:"请选择商机"},row],opportunityIndex:1}:e.detail.cleared?{opportunityId:"",selectedOpportunity:null,opportunityOptions:[{id:"",name:"请选择商机"}],opportunityIndex:0}:{})});
    this.refresh(); this.persist();
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
    if(this.data.canEditOpportunity!==false)options.push({id:"__new__",name:"本次拜访产生新商机"});
    return options;
  },
  async loadOpportunities({more=false,search=false}={}) {
    if(this.data.isFde) {this.setData({fdeOpportunityVerified:false,opportunityEditing:false,opportunityDraft:null});this.refresh();return;}
    const id=this.data.customerId;if(!id || this.closed || (more && (this.data.opportunityLoading || !this.data.opportunityHasMore)))return;
    const identity=this.opportunityIdentity(),seq=this._opportunitySeq=(this._opportunitySeq||0)+1;
    const current=()=>!this.closed && seq===this._opportunitySeq && id===this.data.customerId && identity===this.opportunityIdentity();
    const query=search||more?this.data.opportunityQuery.trim():"",offset=more?this.data.opportunityNextOffset:0;
    const initial=!more&&!search;
    if(initial)this.setData({opportunityQuery:""});
    this.setData({opportunityLoading:true,opportunityError:"",opportunityFailedMore:false,...(initial?{opportunityResolveError:"正在核对商机关联"}:{})});
    this.refresh();
    try {
      // Same personal opportunity scope as before; following a company customer does not grant its history.
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
        const pendingSuggestion=this.data.canEditOpportunity!==false?this.aiOpportunitySuggestion:null;
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
          opportunityEditing:this.data.canEditOpportunity!==false&&Boolean(selectedId),
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
    if(this.data.busy||this.data.archived||this.data.isFde||this.data.editing||!this.data.customerConfirmed)return;
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
    if(selected.id==='__new__'&&this.data.canEditOpportunity===false)return;
    const currentId=this.data.opportunityId||"";
    if(selected.id===currentId&&(!currentId||currentId==='__new__'||this.data.selectedOpportunity)){
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
      opportunityEditing: this.data.canEditOpportunity !== false && Boolean(selected.id), opportunityAIRecognized: false, opportunityAIHint: "" });
    this.refresh();this.persist();
  },
  changeOpportunityForm(e) {
    if(e.detail.customerId!==undefined && (e.detail.customerId!==this.data.customerId || e.detail.opportunityId!==this.data.opportunityId))return;
    const opportunityDraft = e.detail.form;
    this.setData({ opportunityDraft });
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
    this.persist();
    wx.showToast({ title: "草稿已保存", icon: "success" });
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
    if(!this.data.customerConfirmed)return this.fail('请先确认关联客户');
    if(this.data.isFde && (!this.data.opportunityId || !this.data.fdeOpportunityVerified))return this.fail('请选择并确认本人参与的商机');
    let fields=snapshot.fields(this.data);
    const missing=[...flow.CORE,...flow.REQUIRED_DETAILS].find(f=>!fields[f.key]);
    if(missing)return this.fail(`请补充${missing.label}`);
    const firstMissing=firstVisit.missingField(fields);
    if(firstMissing)return this.fail(`首次拜访请补充${firstMissing.label}`);
    if(!this.data.sourceRunId)return this.fail('请返回录入页重新整理原文');
    this.preparing=true;
    let mutation;
    try {mutation=!this.data.isFde && this.data.opportunityEditing ? await this.selectComponent('#visitOpportunityForm').prepare() : null;}
    catch(error){this.preparing=false;return this.fail(error);}
    this.preparing=false;
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
    try {
      if(!id){const accepted=await api.submitVisitStage('quality',payload);id=accepted.run_id;
        if(this.unloaded)return;this.setData({pendingReviewId:id});this.persist();}
      const run=await api.waitVisitRun(id);
      if(this.unloaded)return;
      if(signature!==snapshot.signature(this.data))throw Error('内容已变化，请重新质检');
      const result=run.result || {}, quality=result.quality_review;
      if(result.visit_stage!=='quality' || !quality || !Number.isInteger(quality.follow_up_score))throw Error('质检没有返回有效评分，请重试');
      this.setData({quality,busy:false,flowStep:'result',pendingReviewId:'',reviewRunId:id,reviewedContent:signature});
      this.refresh();this.persist();
    } catch(error) {
      if(this.unloaded)return;
      this.setData({flowStep:'edit',pendingReviewId:error.code==='RUN_TIMEOUT'?id:''});
      this.fail(error);this.persist();
    } finally {clearInterval(this.analysisTimer);}
  },
  async archive() {
    this.refreshGate();
    if(!this.data.canSubmit || this.data.busy)return;
    this.opportunityMutation=this.data.reviewPayload.opportunity_mutation;
    wx.showModal({
      title: "确认并归档这次拜访？",
      content: `客户：${this.data.customerName}\n商机：${this.opportunityMutation ? this.opportunityMutation.name : (this.data.opportunityOptions[this.data.opportunityIndex] || {name:"不关联商机"}).name}\n请确认以上关联与拜访内容准确。`,
      confirmText: "确认归档",
      success: (r) => {
        if (r.confirm) this.submitArchive();
      },
    });
  },
  submitArchive() {
    if (this.data.archived || !this.data.canSubmit || this.data.busy) return;
    this.setData({ busy: true, errorText: "" });
    this.refresh();
    api
      .createVisit(this.data.customerId, {
        ...this.data.reviewPayload.fields,
        opportunity_id:this.data.reviewPayload.opportunity_id,
        _opportunity_mutation:this.data.reviewPayload.opportunity_mutation,
        collaborator_ids:this.data.reviewPayload.collaborator_ids,
        source_import_id:this.data.reviewPayload.source_import_id,
        _quality_review_run_id:this.data.reviewRunId,
      }, this.data.reviewPayload.fde_participant_ids || [])
      .then((r) => {
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
          const currentEntry = wx.getStorageSync(`visitEntryV2:${this.userKey}`);
          const sameAction = !this.entryDraftId || !currentEntry || currentEntry.draftId === this.entryDraftId;
          if (sameAction) [
            this.draftKey,
            `visitEntryV2:${this.userKey}`,
            `visitStructuredV2:${this.userKey}`,
            `visitStructureRun:${this.userKey}`,
          ].forEach((k) => wx.removeStorageSync(k));
        } catch (_) {
          this.setData({archiveWarning:"记录已保存，本地草稿清理失败，请勿重复提交。"});
        }
        if(!this.data.isFde)this.loadAdvice();
        wx.setNavigationBarTitle({ title: "拜访已归档" });
      })
      .catch((e) => this.fail(e));
  },
  async loadAdvice() {
    if(this.data.isFde || this.data.adviceBusy || !this.data.archived)return;
    const retry=Boolean(this.data.adviceError);
    this.setData({showAdvice:true,adviceBusy:true,adviceError:''});
    try {const result=await api.queryBusinessAdvice('visit',this.data.visitId,'tasks',retry);
      if(!this.unloaded)this.setData({advice:result});}
    catch(error){if(!this.unloaded)this.setData({adviceError:'记录已保存，待办建议生成失败：'+(error.message || '请重试')});}
    finally {if(!this.unloaded)this.setData({adviceBusy:false});}
  },
  async reloadAdvice() {
    if(this.data.isFde || !this.data.advice)return;
    try{const result=await api.getBusinessAdvice(this.data.advice.id);if(!this.unloaded)this.setData({advice:result});}
    catch(error){if(!this.unloaded)this.setData({adviceError:error.message});}
  },
  closeAdvice(){this.adviceTouch=null;this.setData({showAdvice:false});},
  openAdvice(){if(!this.data.isFde)this.setData({showAdvice:true});},
  startAdviceDrag(e){this.adviceTouch=e.touches.length===1 ? e.touches[0] : null;},
  endAdviceDrag(e){
    const start=this.adviceTouch, end=e.changedTouches && e.changedTouches[0];
    this.adviceTouch=null;
    if(start && end && end.clientY-start.clientY>45 && end.clientY-start.clientY>Math.abs(end.clientX-start.clientX)*1.5)this.closeAdvice();
  },
  cancelAdviceDrag(){this.adviceTouch=null;},
  stopAdviceTap(){},
  saveSupplement() {
    if (this.data.busy) return;
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
        path: `/visits/${this.data.visitId}`,
        method: "PATCH",
        data: body,
      })
      .then((r) => {
        this.setData({ version: r.version_no, values: r, busy: false });
        this.refresh();
        wx.showToast({ title: "补充信息已保存", icon: "success" });
      })
      .catch((e) => this.fail(e));
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
