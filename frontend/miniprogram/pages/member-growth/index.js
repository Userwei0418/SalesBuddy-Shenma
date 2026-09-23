const apiClient = require("../../utils/apiClient");

Page({
  data: {
    fdeMemberId: '',
    loading: true, ready: false, statusText: "正在读取每日能力复盘",
    subject: { name: "销售成员", team: "", account_code: "", initial: "销" },
    overallScore: "--", reviewDate: "", reviewSummary: "",
    dimensions: [], growthOptions: [], selectedGrowthCode: "overall", selectedGrowthName: "综合评分",
    history: [], visitCount: 0, aiAdvice: [], subjectRoleName: "一线销售",
  },
  onLoad(options = {}) {
    const session = getApp().globalData.session;
    this.setData({fdeMemberId:options.member_id||(session && session.userId)||''});
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'member-growth', options)) return;
    if (!getApp().ensureLogin()) return;
    if(session && ['fde','fde_lead'].includes(session.role)) {
      const memberId=this.data.fdeMemberId;
      if(session.role==='fde'&&memberId!==session.userId){this.setData({accessBlocked:true,accessMessage:'当前身份仅能查看本人协作记录'});return;}
      this.setData({isFde:true,fdeMemberId:memberId});wx.setNavigationBarTitle({title:'成员协作看板'});return;
    }
    if (!session || !["supervisor", "manager"].includes(session.role)) {
      wx.showToast({ title: "当前账号没有查看团队画像的权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 600);
      return;
    }
    this.accountCode = decodeURIComponent(options.account || "");
    if (!this.accountCode) { wx.navigateBack(); return; }
    this.loadGrowth();
  },
  // BACKEND-CONTRACT LEGACY-GROWTH: GET /profile/team-members/{account}/sales-growth?days=30。
  // 本独立页直接用latest.overall_score、缺维度分回退0；与profile主页面缺失/重算策略不同。
  // 后续统一前先保留差异，见docs/backend-handoff/登录看板与个人中心详解.md。
  loadGrowth() {
    apiClient.getMemberSalesGrowth(this.accountCode, 30).then((payload) => {
      if (!payload.latest) {
        const subject = payload.subject || this.data.subject;
        this.setData({ loading: false, ready: false, subject: { ...subject, initial: String(subject.name || "销").substring(0, 1) }, statusText: "该成员尚未形成能力复盘数据" });
        return;
      }
      const frameworkDimensions = (payload.framework && payload.framework.dimensions) || [];
      const scores = payload.latest.dimension_scores || {};
      const dimensions = frameworkDimensions.map((definition) => {
        const value = scores[definition.code] || {};
        return { code: definition.code, name: definition.name, shortName: definition.short_name || definition.name, score: Number(value.score || 0), coachingAction: value.coaching_action || "继续积累拜访证据" };
      });
      const improvements = Array.isArray(payload.latest.improvements) ? payload.latest.improvements.filter(Boolean) : [];
      const aiAdvice = improvements.length ? improvements.slice(0, 6) : dimensions.map((item) => `${item.name}：${item.coachingAction}`).filter(Boolean).slice(0, 6);
      const subjectRoleName = (payload.subject || {}).role_code === "supervisor" ? "销售主管" : "一线销售";
      this.setData({
        loading: false, ready: true, statusText: "每日自动复盘 · 数据已同步",
        subject: { ...(payload.subject || this.data.subject), initial: String((payload.subject || this.data.subject).name || "销").substring(0, 1) },
        overallScore: Number(payload.latest.overall_score || 0).toFixed(1),
        reviewDate: String(payload.latest.review_date || ""), reviewSummary: payload.latest.summary || "已完成今日六维能力复盘",
        dimensions, growthOptions: [{ code: "overall", name: "综合" }].concat(dimensions.map((item) => ({ code: item.code, name: item.shortName }))),
        history: payload.history || [], visitCount: Number((payload.latest.input_snapshot || {}).visit_count || 0), aiAdvice, subjectRoleName,
      }, () => { this.drawRadar(); this.drawGrowthLine(); });
    }).catch((error) => this.setData({ loading: false, ready: false, statusText: error.message || "能力数据加载失败" }));
  },
  selectGrowthDimension(e) {
    const code = e.currentTarget.dataset.code;
    const option = this.data.growthOptions.find((item) => item.code === code);
    this.setData({ selectedGrowthCode: code, selectedGrowthName: option ? option.name : "综合评分" }, () => this.drawGrowthLine());
  },
  drawRadar() {
    const dimensions = this.data.dimensions;
    if (dimensions.length !== 6) return;
    this.createSelectorQuery().select(".radar-canvas").boundingClientRect((rect) => {
      if (!rect) return;
      const ctx = wx.createCanvasContext("memberAbilityRadar", this);
      const width = rect.width, height = rect.height, cx = width / 2, cy = height / 2 + 3, radius = Math.min(width, height) * 0.31;
      const point = (index, scale) => { const angle = -Math.PI / 2 + index * Math.PI / 3; return [cx + Math.cos(angle) * radius * scale, cy + Math.sin(angle) * radius * scale]; };
      for (let level = 1; level <= 5; level += 1) { ctx.beginPath(); for (let i = 0; i < 6; i += 1) { const p = point(i, level / 5); if (!i) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); } ctx.closePath(); ctx.setStrokeStyle("rgba(61,100,146,.18)"); ctx.stroke(); }
      for (let i = 0; i < 6; i += 1) { const p = point(i, 1); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p[0], p[1]); ctx.setStrokeStyle("rgba(61,100,146,.13)"); ctx.stroke(); }
      ctx.beginPath(); dimensions.forEach((item, index) => { const p = point(index, item.score / 100); if (!index) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); }); ctx.closePath(); ctx.setFillStyle("rgba(22,119,255,.22)"); ctx.fill(); ctx.setLineWidth(2); ctx.setStrokeStyle("#1677ff"); ctx.stroke();
      dimensions.forEach((item, index) => { const p = point(index, item.score / 100); ctx.beginPath(); ctx.arc(p[0], p[1], 3, 0, Math.PI * 2); ctx.setFillStyle("#1677ff"); ctx.fill(); const label = point(index, 1.28); ctx.setFillStyle("#53657a"); ctx.setFontSize(11); ctx.setTextAlign(label[0] < cx - 5 ? "right" : label[0] > cx + 5 ? "left" : "center"); ctx.fillText(`${item.shortName} ${Math.round(item.score)}`, label[0], label[1] + 4); });
      ctx.draw();
    }).exec();
  },
  drawGrowthLine() {
    const history = this.data.history;
    if (!history.length) return;
    const code = this.data.selectedGrowthCode;
    this.createSelectorQuery().select(".growth-canvas").boundingClientRect((rect) => {
      if (!rect) return;
      const ctx = wx.createCanvasContext("memberGrowthLine", this);
      const width = rect.width, height = rect.height, left = 34, right = 12, top = 18, bottom = 28;
      const valueOf = (item) => code === "overall" ? Number(item.overall_score || 0) : Number(((item.dimension_scores || {})[code] || {}).score || 0);
      [0, 25, 50, 75, 100].forEach((value) => { const y = top + (100 - value) / 100 * (height - top - bottom); ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.setStrokeStyle("rgba(61,100,146,.12)"); ctx.stroke(); ctx.setFillStyle("#91a0b2"); ctx.setFontSize(9); ctx.setTextAlign("right"); ctx.fillText(String(value), left - 6, y + 3); });
      ctx.beginPath(); history.forEach((item, index) => { const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right); const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom); if (!index) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.setLineWidth(2.5); ctx.setStrokeStyle("#1677ff"); ctx.stroke();
      history.forEach((item, index) => { const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right); const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.setFillStyle("#1677ff"); ctx.fill(); if (!index || index === history.length - 1) { ctx.setFillStyle("#728196"); ctx.setFontSize(9); ctx.setTextAlign(!index ? "left" : "right"); ctx.fillText(String(item.review_date).slice(5), x, height - 7); } });
      ctx.draw();
    }).exec();
  },
});
