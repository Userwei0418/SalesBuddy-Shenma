/**
 * BACKEND-CONTRACT 风险列表：GET /risks + GET /opportunities?page_size=300&include_closed=true 补充商机名称；后端负责权限与数据完整性。
 * 前端将 resolved/accepted 都归入“已解除”分组，但 accepted 标签为“已接受”，健康灯仍为提醒；两者不能在后端等同消除风险。
 * riskLight 当前只将 resolved 标绿，其余标黄，未按 severity_code 自动升红；严重程度文本和健康灯是两套现有规则。
 * 此页只读列表；解除写操作在 risk-detail，没有风险新建或重新打开入口。
 */
const { riskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");

function formatDate(value) {
  if (!value) return "待确认";
  const date = new Date(typeof value === "number" ? value : String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function normalizeRisk(item) {
  const status = ["resolved", "accepted"].includes(item.status) ? "resolved" : "open";
  const severityMap = { critical: "严重", high: "高", medium: "中", low: "低" };
  const severity = severityMap[item.severity_code] || item.severity || "中";
  const severityClassMap = { 严重: "critical", 高: "high", 中: "medium", 低: "low" };
  return {
    ...item,
    signal:riskLight(item),
    customerName: item.customer_name || item.customerName || "关联客户待确认",
    owner: item.owner_name || item.owner || "待确认",
    team: item.team_name || item.team || "",
    title: item.title || "客户经营风险",
    status,
    statusLabel: item.status === "accepted" ? "已接受" : status === "resolved" ? "已解除" : "待解除",
    severity,
    severityClass: item.severity_code || severityClassMap[severity] || "medium",
    openedLabel: formatDate(item.opened_at || item.openedAt),
    resolvedLabel: formatDate(item.resolved_at || item.resolvedAt),
    remote: Boolean(item.opened_at || item.risk_type_code),
    opportunityId: item.opportunity_id || item.opportunityId || "",
    opportunityName: item.opportunity_name || item.opportunityName || "",
    customerId: item.customer_id || item.customerId || "",
  };
}

Page({
  data: {
    tabs: [{ key: "open", label: "待解除" }, { key: "resolved", label: "已解除" }, { key: "all", label: "全部" }],
    activeTab: "open",
    risks: [],
    filteredRisks: [],
    openCount: 0,
    resolvedCount: 0,
    scope: "",
    loading: false,
    opportunityOnly: false,
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'risks', options)) return;
    this.teamFilter = decodeURIComponent(options.team || "all");
    this.memberFilter = decodeURIComponent(options.member || "all");
    const opportunityOnly = options.opportunity === "1";
    this.setData({ opportunityOnly });
    if (opportunityOnly) wx.setNavigationBarTitle({ title: "商机风险" });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'risks')) return;
    if (!getApp().ensureLogin()) return;
    this.loadRisks();
  },

  loadRisks() {
    const session = getApp().globalData.session;
    this.setData({ scope: session.scope });
    this.setData({ loading: true });
    apiClient.listRisks().then((response) => {
      const risks = (response.items || []).map(normalizeRisk);
      this.setRisks(risks);
    }).catch((error) => {
      this.setRisks([]);
      wx.showToast({ title: error.message || "风险加载失败", icon: "none" });
    }).finally(() => this.setData({ loading: false }));
  },

  setRisks(risks) {
    const sorted = risks.filter((item) => !this.data.opportunityOnly || item.opportunityId).slice().sort((a, b) => {
      if (a.status !== b.status) return a.status === "open" ? -1 : 1;
      const order = { 严重: 4, 高: 3, 中: 2, 低: 1 };
      return (order[b.severity] || 0) - (order[a.severity] || 0);
    });
    this.setData({
      risks: sorted,
      openCount: sorted.filter((item) => item.status === "open").length,
      resolvedCount: sorted.filter((item) => item.status === "resolved").length,
    }, () => this.applyFilter());
  },

  applyFilter() {
    const key = this.data.activeTab;
    const scoped = this.data.risks.filter((item) => (this.teamFilter === "all" || item.team === this.teamFilter) && (this.memberFilter === "all" || item.owner === this.memberFilter));
    this.setData({ filteredRisks: key === "all" ? scoped : scoped.filter((item) => item.status === key) });
  },

  selectTab(e) {
    this.setData({ activeTab: e.currentTarget.dataset.key }, () => this.applyFilter());
  },

  openRisk(e) {
    wx.navigateTo({ url: `/pages/risk-detail/index?id=${encodeURIComponent(e.currentTarget.dataset.id)}` });
  },
  openOpportunity(e) {
    const customerId = e.currentTarget.dataset.customerId;
    const opportunityId = e.currentTarget.dataset.opportunityId;
    if (!customerId || !opportunityId) return;
    wx.setStorageSync("pendingOpenCustomerId", customerId);
    wx.setStorageSync("pendingOpenOpportunityId", opportunityId);
    wx.switchTab({ url: "/pages/customers/index" });
  },
});
