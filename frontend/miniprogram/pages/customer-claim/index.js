const apiClient = require("../../utils/apiClient");
const { normalizeCustomerSummary } = require("../../utils/customerDetail");
const PAGE_SIZE = 50;

function identityKey() {
  const app = getApp(), session = app.globalData.session || {};
  return JSON.stringify([session.workspaceId, session.userId || session.account,
    app.globalData.role, session.scope, (session.teamIds || []).slice().sort(),
    session.loginAt, session.permissionVersion,
    typeof apiClient.getBaseUrl === 'function' ? apiClient.getBaseUrl() : '']);
}

function customerItem(raw) {
  const item = normalizeCustomerSummary(raw);
  return { ...item,
    claimEligible: item.can_claim === true && item.claim_status !== "pending",
    claimLabel: item.claimed ? "本人已认领" : item.claim_status === "pending" ? "申请待审批"
      : item.ownership_state === "legacy_review" ? "待运营核对" : item.can_claim
        ? (item.claim_status === "rejected" ? "可重新申请" : "可申请认领") : "已被认领",
  };
}

Page({
  data: {
    loading: true, loadingMore: false, submitting: false, query: "",
    industry: "", claimStatus: "", industryIndex: 0, statusIndex: 0, hasFilters: false,
    industryOptions: [], statusOptions: [], optionsLoading: false, optionsError: "",
    customers: [], total: null, hasMore: false, nextOffset: null,
    loadError: "", loadMoreError: "", refreshRequired: false, selectedCustomerId: "", resultMessage: "",
  },

  onLoad() {
    if (getApp().guardPage && !getApp().guardPage(this, 'customer-claim')) return;
    if (!getApp().ensureLogin()) return;
    this.disposed = false;
    this.directoryIdentity = identityKey();
    return this.loadCustomers();
  },

  onShow() {
    if (this.disposed === undefined) return this.onLoad();
    const identityChanged = this.directoryIdentity !== identityKey();
    if (this.disposed === false && (this.refreshOnReturn || identityChanged)) {
      if (getApp().guardPage && !getApp().guardPage(this, 'customer-claim')) return;
      if (!getApp().ensureLogin()) return;
      this.refreshOnReturn = false;
      this.optionsLoaded = false;
      this.setData({ submitting: false, resultMessage: "" });
      return this.loadCustomers(identityChanged ? "" : this.data.query);
    }
  },

  onHide() {
    this.refreshOnReturn = true;
    clearTimeout(this.searchTimer);
    // Returning reads the authoritative state. A pre-hide read or application
    // receipt must not replace a newer approval with its old pending state.
    this.directoryEpoch = (this.directoryEpoch || 0) + 1;
  },

  onUnload() {
    this.disposed = true;
    clearTimeout(this.searchTimer);
    this.customerRequestId = (this.customerRequestId || 0) + 1;
  },

  currentIdentity(request) {
    return !this.disposed && request.identity === identityKey() && request.epoch === this.directoryEpoch;
  },

  currentRequest(request) {
    return this.currentIdentity(request) && request.id === this.customerRequestId;
  },

  beginCustomers(query) {
    clearTimeout(this.searchTimer);
    this.selectedCustomer = null;
    const identity = identityKey();
    if (this.directoryIdentity !== identity) {
      this.directoryEpoch = (this.directoryEpoch || 0) + 1;
      this.optionsLoaded = false;
      this.setData({ submitting: false, resultMessage: "", industry: "", claimStatus: "",
        industryIndex: 0, statusIndex: 0, industryOptions: [], statusOptions: [], optionsError: "" });
    }
    this.directoryEpoch = this.directoryEpoch || 1;
    this.directoryIdentity = identity;
    const request = { id: (this.customerRequestId || 0) + 1, identity, epoch: this.directoryEpoch,
      industry: this.data.industry, claimStatus: this.data.claimStatus };
    this.customerRequestId = request.id;
    this.setData({ query, hasFilters: !!(query || this.data.industry || this.data.claimStatus), loading: true, loadingMore: false, customers: [], total: null,
      hasMore: false, nextOffset: null, selectedCustomerId: "", loadError: "", loadMoreError: "", refreshRequired: false });
    return request;
  },

  loadCustomers(query = this.data.query) {
    const request = this.beginCustomers(query);
    const options = !this.optionsLoaded ? this.loadFilterOptions(request) : Promise.resolve();
    return Promise.all([this.fetchCustomers(request, query, 0, false), options]);
  },

  loadMore() {
    if (this.directoryIdentity !== identityKey()) return this.loadCustomers("");
    if (this.data.loading || this.data.loadingMore || !this.data.hasMore) return Promise.resolve();
    const request = { id: ++this.customerRequestId, identity: this.directoryIdentity, epoch: this.directoryEpoch,
      industry: this.data.industry, claimStatus: this.data.claimStatus };
    this.setData({ loadingMore: true, loadMoreError: "" });
    return this.fetchCustomers(request, this.data.query, this.data.nextOffset, true);
  },

  onReachBottom() { if (!this.data.loadMoreError) return this.loadMore(); },
  retryMore() { return this.data.refreshRequired ? this.refreshCustomers() : this.loadMore(); },

  fetchCustomers(request, query, offset, append) {
    const filters = {};
    if (request.industry) filters.industry = request.industry;
    if (request.claimStatus) filters.claimStatus = request.claimStatus;
    return apiClient.listCustomerClaimPool({ q: query, pageSize: PAGE_SIZE, offset, ...filters }).then(page => {
      if (!this.currentRequest(request)) return;
      const valid = page && Array.isArray(page.items) && page.items.length <= PAGE_SIZE
        && Number.isInteger(page.total) && page.total >= 0 && typeof page.has_more === 'boolean'
        && (page.has_more ? page.items.length > 0 && page.next_offset === offset + page.items.length
          && page.next_offset < page.total : page.next_offset === null);
      if (!valid) throw new Error("客户分页数据不完整，请刷新名单");
      const customers = append ? this.data.customers.slice() : [];
      const ids = new Set(customers.map(item => String(item.id)));
      const changed = () => Object.assign(new Error("客户名单已变化，请刷新名单"), { code: 'DIRECTORY_CHANGED' });
      if (append && page.total !== this.data.total) throw changed();
      for (const item of page.items) {
        if (!item.id || ids.has(String(item.id))) throw changed();
        ids.add(String(item.id)); customers.push(customerItem(item));
      }
      if (customers.length > page.total || (!page.has_more && customers.length !== page.total)) {
        throw changed();
      }
      this.setData({ loading: false, loadingMore: false, customers, total: page.total,
        hasMore: page.has_more, nextOffset: page.next_offset, loadError: "", loadMoreError: "", refreshRequired: false });
    }).catch(error => {
      if (!this.currentRequest(request)) return;
      this.setData({ loading: false, loadingMore: false,
        refreshRequired: error.code === 'DIRECTORY_CHANGED',
        [append ? 'loadMoreError' : 'loadError']: error.message || "公司客户加载失败" });
    });
  },

  inputQuery(e) {
    const query = String(e.detail.value || "").trim();
    // Invalidate immediately, not after the debounce window: a previous search
    // may finish while the user is already looking at the new query text.
    const request = this.beginCustomers(query);
    this.searchTimer = setTimeout(() => {
      if (this.currentRequest(request)) this.fetchCustomers(request, query, 0, false);
    }, 250);
  },

  refreshCustomers() { this.optionsLoaded = false; return this.loadCustomers(this.data.query); },

  loadFilterOptions(context = { identity: this.directoryIdentity, epoch: this.directoryEpoch }) {
    const requestId = this.optionsRequestId = (this.optionsRequestId || 0) + 1;
    const current = () => this.currentIdentity(context) && requestId === this.optionsRequestId;
    this.setData({ optionsLoading: true, optionsError: "" });
    return apiClient.listCustomerClaimOptions().then(options => {
      if (!current()) return;
      const valid = list => Array.isArray(list) && list.length && list[0].value === ""
        && list.every(item => item && typeof item.value === 'string' && typeof item.label === 'string' && item.label)
        && new Set(list.map(item => item.value)).size === list.length;
      if (!options || !valid(options.industries) || !valid(options.claim_statuses)) {
        throw new Error("筛选选项数据不完整，请重试");
      }
      const industryIndex = options.industries.findIndex(item => item.value === this.data.industry);
      const statusIndex = options.claim_statuses.findIndex(item => item.value === this.data.claimStatus);
      this.optionsLoaded = true;
      this.setData({ industryOptions: options.industries, statusOptions: options.claim_statuses,
        industryIndex: Math.max(0, industryIndex), statusIndex: Math.max(0, statusIndex),
        optionsLoading: false, optionsError: "" });
      // A removed industry must not leave a hidden filter behind after refresh.
      if (industryIndex < 0 || statusIndex < 0) {
        this.setData({ industry: industryIndex < 0 ? "" : this.data.industry,
          claimStatus: statusIndex < 0 ? "" : this.data.claimStatus });
        return this.loadCustomers(this.data.query);
      }
    }).catch(error => {
      if (!current()) return;
      this.optionsLoaded = false;
      this.setData({ optionsLoading: false, optionsError: error.message || "筛选选项加载失败，请重试" });
    });
  },

  retryFilterOptions() { return this.loadFilterOptions(); },

  changeIndustry(e) { return this.changeFilter('industry', 'industryIndex', 'industryOptions', e); },
  changeClaimStatus(e) { return this.changeFilter('claimStatus', 'statusIndex', 'statusOptions', e); },
  changeFilter(field, indexField, optionsField, e) {
    if (this.directoryIdentity !== identityKey() || this.data.optionsLoading || this.data.submitting) return;
    const index = Number(e.detail.value), option = this.data[optionsField][index];
    if (!Number.isInteger(index) || !option || option.value === this.data[field]) return;
    this.setData({ [field]: option.value, [indexField]: index, resultMessage: "" });
    return this.loadCustomers(this.data.query);
  },

  clearFilters() {
    if (this.data.submitting) return;
    this.setData({ industry: "", claimStatus: "", industryIndex: 0, statusIndex: 0, resultMessage: "" });
    return this.loadCustomers("");
  },

  selectCustomer(e) {
    if (this.data.submitting || this.directoryIdentity !== identityKey()) return;
    const selectedCustomerId = e.currentTarget.dataset.id;
    this.selectedCustomer = this.data.customers.find(item => String(item.id) === String(selectedCustomerId)) || null;
    if (!this.selectedCustomer || !this.selectedCustomer.claimEligible) {
      wx.showToast({ title: this.selectedCustomer ? this.selectedCustomer.claimLabel : "请重新选择", icon: "none" });
      this.selectedCustomer = null; this.setData({ selectedCustomerId: "" }); return;
    }
    this.setData({ selectedCustomerId });
  },

  // Applying and approved ownership are separate server states.
  confirmClaim() {
    if (this.data.submitting || this.directoryIdentity !== identityKey()) return;
    const customer = this.selectedCustomer;
    if (!customer || !customer.claimEligible) {
      wx.showToast({ title: "请先选择要认领的客户", icon: "none" }); return;
    }
    const request = { identity: this.directoryIdentity, epoch: this.directoryEpoch };
    wx.showModal({
      title: "提交认领申请？",
      content: `申请认领“${customer.name}”，运营审批通过后加入你的作战地图。记录拜访无需先认领。`,
      confirmText: "提交申请", confirmColor: "#1677FF",
      success: result => {
        if (!result.confirm || this.data.submitting || !this.currentIdentity(request)
          || this.data.selectedCustomerId !== customer.id) return;
        this.setData({ submitting: true });
        apiClient.claimCustomer(customer.id).then(assignment => {
          if (!this.currentIdentity(request)) return;
          if (assignment.status !== "pending") throw new Error("申请状态已变化，请刷新名单确认");
          this.selectedCustomer = null;
          this.setData({ submitting: false, selectedCustomerId: "",
            resultMessage: `“${customer.name}”的认领申请已提交，等待运营审批。` });
          wx.showToast({ title: "申请已提交", icon: "success" });
          this.loadCustomers(this.data.query);
        }).catch(error => {
          if (!this.currentIdentity(request)) return;
          this.setData({ submitting: false });
          wx.showToast({ title: error.message || "认领失败，请重试", icon: "none" });
          if (error.statusCode === 409) this.loadCustomers(this.data.query);
        });
      },
    });
  },
});
