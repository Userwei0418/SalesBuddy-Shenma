/**
 * BACKEND-CONTRACT GET /risks/:id，POST /risks/:id/resolve {resolution_note}；前端要求 trim 后至少 5 字，返回更新完整风险。
 * 页面未按处理人 ID 限制解除按钮，后端必须按登录身份裁决；无 version_no，且 /risks/:id/resolve 不在现有幂等键白名单。
 * accepted 在页面归入已处理并隐藏解除操作，但 riskLight 仍为黄色。缺失 evidence/next_action 使用解释占位，不是模型生成事实。
 */
const { riskLight } = require('../../utils/statusLight');
const apiClient = require("../../utils/apiClient");

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value : String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function normalizeRisk(item) {
  const status = ["resolved", "accepted"].includes(item.status) ? "resolved" : "open";
  const severityMap = { critical: "严重", high: "高", medium: "中", low: "低" };
  const evidence = Array.isArray(item.evidence) ? item.evidence.map((entry) => typeof entry === "string" ? entry : (entry.label || entry.detail || JSON.stringify(entry))) : [];
  return {
    ...item,
    signal:riskLight(item),
    customerName: item.customer_name || item.customerName || "关联客户待确认",
    owner: item.owner_name || item.owner || "待确认",
    team: item.team_name || item.team || "",
    status,
    statusLabel: item.status === "accepted" ? "已接受" : status === "resolved" ? "已解除" : "待解除",
    severity: severityMap[item.severity_code] || item.severity || "未评级",
    openedLabel: formatDate(item.opened_at || item.openedAt),
    resolvedLabel: formatDate(item.resolved_at || item.resolvedAt),
    resolvedBy: item.resolved_by_name || item.resolvedBy || "负责人",
    resolutionNote: item.resolution_note || item.resolutionNote || "未填写解除依据",
    evidence: evidence.length ? evidence : ["未登记风险依据"],
    nextAction: item.next_action || item.nextAction || "持续关注客户后续进展，必要时重新打开风险。",
    remote: Boolean(item.opened_at || item.risk_type_code),
  };
}

Page({
  data: { riskId: "", risk: null, resolutionNote: "", noteCount: 0, submitting: false, loading: false },

  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'risk-detail', options)) return;
    this.setData({ riskId: decodeURIComponent(options.id || "") });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'risk-detail')) return;
    if (!getApp().ensureLogin()) return;
    this.loadRisk();
  },

  loadRisk() {
    const session = getApp().globalData.session;
    if (!/^[0-9a-f-]{36}$/i.test(this.data.riskId)) { this.showMissing(); return; }
    this.setData({ loading: true });
    apiClient.getRisk(this.data.riskId).then((risk) => this.setData({ risk: normalizeRisk(risk) })).catch(() => {
      this.showMissing();
    }).finally(() => this.setData({ loading: false }));
  },

  showMissing() {
    wx.showToast({ title: "风险不存在或已不可见", icon: "none" });
    setTimeout(() => wx.navigateBack(), 700);
  },

  inputResolutionNote(e) {
    const resolutionNote = e.detail.value;
    this.setData({ resolutionNote, noteCount: resolutionNote.length });
  },

  confirmResolve() {
    const risk = this.data.risk;
    const note = String(this.data.resolutionNote || "").trim();
    if (!risk || risk.status === "resolved" || this.data.submitting) return;
    if (note.length < 5) {
      wx.showToast({ title: "请填写风险解除依据", icon: "none" });
      return;
    }
    wx.showModal({
      title: "确认解除该风险？",
      content: "解除后将保留处理人、处理时间和解除依据，经营摘要会同步更新。",
      confirmText: "确认解除",
      confirmColor: "#2B9A70",
      success: (result) => {
        if (!result.confirm) return;
        this.setData({ submitting: true });
        apiClient.resolveRisk(risk.id, note).then((resolved) => {
          this.setData({ risk: normalizeRisk(resolved), submitting: false });
          wx.setStorageSync("lastResolvedRiskId", risk.id);
          wx.showToast({ title: "风险已解除", icon: "success" });
          wx.vibrateShort({ type: "light" });
          setTimeout(() => wx.navigateBack(), 700);
        }).catch((error) => {
          this.setData({ submitting: false });
          wx.showToast({ title: error.message || "解除失败，请稍后重试", icon: "none" });
        });
      },
    });
  },
});
