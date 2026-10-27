const apiClient = require("../../utils/apiClient");
const access = require('../../utils/access');

Page({
  onGrowthTab(e) { return this.selectGrowthDimension({ currentTarget: { dataset: { code: e.detail.key } } }); },

  data: {
    fdeMemberId: '',
    loading: true, ready: false, statusText: "正在读取每日能力复盘",
    subject: { name: "销售成员", team: "", account_code: "", initial: "销" },
    overallScore: "--", reviewDate: "", reviewSummary: "",
    dimensions: [], growthOptions: [], selectedGrowthCode: "overall", selectedGrowthName: "综合评分",
    history: [], visitCount: 0, aiAdvice: [], subjectRoleName: "一线销售",
  },
  onLoad(options = {}) {
    this._options={...options};this._hidden=false;this._loadSerial=(this._loadSerial||0)+1;clearTimeout(this._backTimer);
    this.setData({isFde:false,ready:false,accessBlocked:false});
    const session = getApp().globalData.session;
    this._pageIdentity=access.identity(session);
    this.setData({fdeMemberId:options.member_id||(session && session.userId)||''});
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'member-growth', options)) return;
    if (!getApp().ensureLogin()) return;
    if(session && ['fde','fde_lead'].includes(session.role)) {
      const memberId=this.data.fdeMemberId;
      if(memberId!==session.userId && (session.permissions ? !require('../../utils/access').hasScope(session,'profile.fde_read',['teams','workspace']) : session.role==='fde')){this.setData({accessBlocked:true,accessMessage:'当前身份仅能查看本人协作记录'});return;}
      this.setData({isFde:true,fdeMemberId:memberId});wx.setNavigationBarTitle({title:'成员协作看板'});return;
    }
    if (!session || (session.permissions ? !require("../../utils/access").can(session,"profile.sales_read") : !["supervisor", "manager"].includes(session.role))) {
      wx.showToast({ title: "当前账号没有查看团队画像的权限", icon: "none" });
      const serial=this._loadSerial,identity=access.identity(session);
      this._backTimer=setTimeout(() => {if(!this._hidden&&serial===this._loadSerial&&identity===access.identity(getApp().globalData.session))wx.navigateBack();}, 600);
      return;
    }
    this.accountCode = decodeURIComponent(options.account || "");
    if (!this.accountCode) { wx.navigateBack(); return; }
    return this.loadGrowth();
  },
  onShow(){
    if(!this._hidden)return;
    if(this.data.isFde&&this._pageIdentity===access.identity(getApp().globalData.session)){
      this._hidden=false;
      if(getApp().guardPage&&!getApp().guardPage(this,'member-growth',this._options||{}))return;
      getApp().ensureLogin();return;
    }
    return this.onLoad(this._options||{});
  },
  onHide(){this._hidden=true;this._loadSerial=(this._loadSerial||0)+1;clearTimeout(this._backTimer);this.setData({ready:false});},
  onUnload(){this.onHide();},
  // BACKEND-CONTRACT LEGACY-GROWTH: GET /profile/team-members/{account}/sales-growth?days=30。
  // 本独立页直接用latest.overall_score、缺维度分回退0；与profile主页面缺失/重算策略不同。
  // 后续统一前先保留差异，见docs/backend-handoff/登录看板与个人中心详解.md。
  loadGrowth() {
    const serial=this._loadSerial=(this._loadSerial||0)+1,account=this.accountCode,identity=access.identity(getApp().globalData.session);
    const current=()=>!this._hidden&&serial===this._loadSerial&&account===this.accountCode&&identity===access.identity(getApp().globalData.session);
    this.setData({loading:true,ready:false,statusText:'正在读取每日能力复盘'});
    return apiClient.getMemberSalesGrowth(account, 30).then((payload) => {
      if(!current())return;
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
        loading: false, ready: true, statusText: "最近一次复盘 · 数据已同步",
        subject: { ...(payload.subject || this.data.subject), initial: String((payload.subject || this.data.subject).name || "销").substring(0, 1) },
        overallScore: Number(payload.latest.overall_score || 0).toFixed(1),
        reviewDate: String(payload.latest.review_date || ""), reviewSummary: payload.latest.summary || "已完成六维能力复盘",
        dimensions, growthOptions: [{ code: "overall", name: "综合" }].concat(dimensions.map((item) => ({ code: item.code, name: item.shortName }))),
        history: payload.history || [], visitCount: Number((payload.latest.input_snapshot || {}).visit_count || 0), aiAdvice, subjectRoleName,
      }, () => { if(current()){this.drawRadar(); this.drawGrowthLine();} });
    }).catch((error) => {if(current())this.setData({ loading: false, ready: false, statusText: error.message || "能力数据加载失败" });});
  },
  selectGrowthDimension(e) {
    const code = e.currentTarget.dataset.code;
    const option = this.data.growthOptions.find((item) => item.code === code);
    this.setData({ selectedGrowthCode: code, selectedGrowthName: option ? option.name : "综合评分" }, () => this.drawGrowthLine());
  },
  drawRadar() {
    const serial=this._loadSerial,identity=access.identity(getApp().globalData.session);
    const dimensions = this.data.dimensions;
    if (dimensions.length !== 6) return;
    this.createSelectorQuery().select(".radar-canvas").boundingClientRect((rect) => {
      if (!rect || this._hidden || serial!==this._loadSerial || identity!==access.identity(getApp().globalData.session)) return;
      const ctx = wx.createCanvasContext("memberAbilityRadar", this);
      const width = rect.width, height = rect.height, cx = width / 2, cy = height / 2 + 3, radius = Math.min(width, height) * 0.31;
      const point = (index, scale) => { const angle = -Math.PI / 2 + index * Math.PI / 3; return [cx + Math.cos(angle) * radius * scale, cy + Math.sin(angle) * radius * scale]; };
      for (let level = 1; level <= 5; level += 1) { ctx.beginPath(); for (let i = 0; i < 6; i += 1) { const p = point(i, level / 5); if (!i) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); } ctx.closePath(); ctx.setStrokeStyle("rgba(79,96,120,.18)"); ctx.stroke(); }
      for (let i = 0; i < 6; i += 1) { const p = point(i, 1); ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p[0], p[1]); ctx.setStrokeStyle("rgba(79,96,120,.13)"); ctx.stroke(); }
      ctx.beginPath(); dimensions.forEach((item, index) => { const p = point(index, item.score / 100); if (!index) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]); }); ctx.closePath(); ctx.setFillStyle("rgba(40,99,205,.22)"); ctx.fill(); ctx.setLineWidth(2); ctx.setStrokeStyle("#2863CD"); ctx.stroke();
      dimensions.forEach((item, index) => { const p = point(index, item.score / 100); ctx.beginPath(); ctx.arc(p[0], p[1], 3, 0, Math.PI * 2); ctx.setFillStyle("#2863CD"); ctx.fill(); const label = point(index, 1.28); ctx.setFillStyle("#4F6078"); ctx.setFontSize(11); ctx.setTextAlign(label[0] < cx - 5 ? "right" : label[0] > cx + 5 ? "left" : "center"); ctx.fillText(`${item.shortName} ${Math.round(item.score)}`, label[0], label[1] + 4); });
      ctx.draw();
    }).exec();
  },
  drawGrowthLine() {
    const serial=this._loadSerial,identity=access.identity(getApp().globalData.session);
    const history = this.data.history;
    if (!history.length) return;
    const code = this.data.selectedGrowthCode;
    this.createSelectorQuery().select(".growth-canvas").boundingClientRect((rect) => {
      if (!rect || this._hidden || serial!==this._loadSerial || code!==this.data.selectedGrowthCode || identity!==access.identity(getApp().globalData.session)) return;
      const ctx = wx.createCanvasContext("memberGrowthLine", this);
      const width = rect.width, height = rect.height, left = 34, right = 12, top = 18, bottom = 28;
      const valueOf = (item) => code === "overall" ? Number(item.overall_score || 0) : Number(((item.dimension_scores || {})[code] || {}).score || 0);
      [0, 25, 50, 75, 100].forEach((value) => { const y = top + (100 - value) / 100 * (height - top - bottom); ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.setStrokeStyle("rgba(79,96,120,.12)"); ctx.stroke(); ctx.setFillStyle("#56677E"); ctx.setFontSize(9); ctx.setTextAlign("right"); ctx.fillText(String(value), left - 6, y + 3); });
      ctx.beginPath(); history.forEach((item, index) => { const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right); const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom); if (!index) ctx.moveTo(x, y); else ctx.lineTo(x, y); }); ctx.setLineWidth(2.5); ctx.setStrokeStyle("#2863CD"); ctx.stroke();
      history.forEach((item, index) => { const x = history.length === 1 ? (left + width - right) / 2 : left + index / (history.length - 1) * (width - left - right); const y = top + (100 - valueOf(item)) / 100 * (height - top - bottom); ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.setFillStyle("#2863CD"); ctx.fill(); if (!index || index === history.length - 1) { ctx.setFillStyle("#56677E"); ctx.setFontSize(9); ctx.setTextAlign(!index ? "left" : "right"); ctx.fillText(String(item.review_date).slice(5), x, height - 7); } });
      ctx.draw();
    }).exec();
  },
});
