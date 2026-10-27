const customerIdentity = require('../../utils/customerIdentity');
const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { identity } = require("../../utils/access");
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);
const { normalizeCustomerDetail } = require("../../utils/customerDetail");
const { CUSTOMER_LEVELS, normalizeCustomerLevel } = require("../../utils/customerLevel");
const editContract = require('../../utils/customerEditContract');

const OPTIONS = {
  industry: businessOptions.customer.industry,
  customer_type: businessOptions.customer.customer_type,
  level_code: CUSTOMER_LEVELS,
  source: businessOptions.customer.source,
  contact_role: businessOptions.customer.contact_role,
};

function roleName(value) {
  return ({ decision_maker: "决策者", influencer: "影响者", user: "使用者" })[value] || value || "使用者";
}

Page({
  ...customerIdentity.methods(apiClient, {session:()=>getApp().globalData.session,wx:()=>wx}),
  data: {
    ...customerIdentity.state(),
    customerId: "",
    loading: true,
    saving: false,
    submissionError: '',
    form: {
      name: "", industry: "", customer_type: "", level_code: "", source: "", partner_name: "",
      contact_name: "", contact_title: "", contact_role: "使用者",
    },
    industryOptions: OPTIONS.industry,
    customerTypeOptions: OPTIONS.customer_type,
    customerLevelOptions: OPTIONS.level_code,
    sourceOptions: OPTIONS.source,
    contactRoleOptions: OPTIONS.contact_role,
    industryIndex: 0,
    customerTypeIndex: 0,
    customerLevelIndex: 0,
    sourceIndex: 0,
    contactRoleIndex: 2,
    agentView: { quadrant: "待计算", potential: 0, relationship: 0, risk: "暂无重大风险" },
  },
  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-edit', options)) return;
    if (!getApp().ensureLogin()) return;
    const customerId = decodeURIComponent(options.customerId || "");
    if (!customerId) {
      wx.showToast({ title: "缺少客户信息", icon: "none" });
      setTimeout(() => wx.navigateBack(), 600);
      return;
    }
    this.setData({ customerId });
    this.loadCustomer();
  },
  onShow() {
    this.writeHidden=false;
    if (this.loadedIdentity !== undefined && this.loadedIdentity !== identity(getApp().globalData.session)) {
      if (getApp().guardPage && !getApp().guardPage(this, 'customer-edit')) return;
      return this.loadCustomer();
    }
  },
  onHide(){this.writeHidden=true;clearTimeout(this.returnTimer);},
  onUnload() { this.unloaded=true;this.closed = true; this.loadSerial = (this.loadSerial || 0) + 1; },
  loadCustomer() {
    const customerId = this.data.customerId;
    const context = identity(getApp().globalData.session);
    const serial = this.loadSerial = (this.loadSerial || 0) + 1;
    this.loadedIdentity = context;
    this.writeIdentity = context;
    const current = () => !this.closed && serial === this.loadSerial
      && customerId === this.data.customerId && context === identity(getApp().globalData.session);
    this.loadedForm = null;
    this.setData({ loading: true, submissionError:'', saving:!!(this.pageWrites && this.pageWrites.save && this.pageWrites.save.current()) });
    return Promise.all([apiClient.getBusinessOptions(),apiClient.getCustomerOverview(customerId)]).then(([,raw]) => {
      if (!current()) return;
      const detail = normalizeCustomerDetail(raw);
      const contact = raw.primary_contact || {};
      this.initBusinessIdentity(raw);
      const form = {
        name: raw.name || "",
        industry: raw.industry_code || "其他",
        customer_type: raw.customer_type_code || (this.data.isCustomerIdentity ? "潜在客户" : ""),
        level_code: normalizeCustomerLevel(raw.level_code || raw.level),
        source: raw.source_code || "其他",
        partner_name: raw.primary_partner_name || "无",
        contact_name: contact.name || "",
        contact_title: contact.title || "",
        contact_role: roleName(contact.relationship_role_code),
      };
      const choices={};Object.keys(OPTIONS).forEach(k=>{const options=k==='customer_type' && businessOptions.isCustomerCreationReady()?businessOptions.customerCreation.customer_type:OPTIONS[k];choices[k]=options.includes(form[k])||!form[k]?options.slice():[form[k],...options];});
      this.options=choices;
      this.loadedForm={...form};
      this.setData({industryOptions:choices.industry,customerTypeOptions:choices.customer_type,customerLevelOptions:choices.level_code,sourceOptions:choices.source,contactRoleOptions:choices.contact_role,
        form,
        industryIndex: Math.max(0, choices.industry.indexOf(form.industry)),
        customerTypeIndex: Math.max(0, choices.customer_type.indexOf(form.customer_type)),
        customerLevelIndex: Math.max(0, choices.level_code.indexOf(form.level_code)),
        sourceIndex: Math.max(0, choices.source.indexOf(form.source)),
        contactRoleIndex: Math.max(0, choices.contact_role.indexOf(form.contact_role)),
        agentView: { quadrant: detail.quadrant, potential: detail.potential, relationship: detail.relationship, risk: detail.risk },
        loading: false,
      });
    }).catch((error) => {
      if (!current()) return;
      this.setData({ loading: false });
      if(this.writeHidden)return;
      wx.showToast({ title: error.message || "客户信息加载失败", icon: "none" });
      this.returnTimer=setTimeout(() => { if (current() && !this.writeHidden) wx.navigateBack(); }, 700);
    });
  },
  inputField(e) {
    const key = e.currentTarget.dataset.key;
    if (["name", "demand_summary", "next_action"].includes(key)) return;
    this.setData({ [`form.${key}`]: e.detail.value });
  },
  changeIndustry(e) { const index = Number(e.detail.value); this.setData({ industryIndex: index, "form.industry": (this.options||OPTIONS).industry[index] }); },
  changeCustomerType(e) { const index = Number(e.detail.value); this.setData({ customerTypeIndex: index, "form.customer_type": (this.options||OPTIONS).customer_type[index] }); },
  changeCustomerLevel(e) { const index = Number(e.detail.value); this.setData({ customerLevelIndex: index, "form.level_code": (this.options||OPTIONS).level_code[index] }); },
  changeSource(e) { const index = Number(e.detail.value); this.setData({ sourceIndex: index, "form.source": (this.options||OPTIONS).source[index] }); },
  changeContactRole(e) { const index = Number(e.detail.value); this.setData({ contactRoleIndex: index, "form.contact_role": (this.options||OPTIONS).contact_role[index] }); },
  // BACKEND-CONTRACT PATCH /api/v1/customers/{id} 仅写人工字段；不发送 name、demand_summary、next_action。
  // 潜力/关系/象限来自 GET 客户详情，只读；PATCH 后重评由后端负责，本页不轮询重评完成。
  // 编辑保留通用历史目录；创建专用选项不能替代历史值。
  submit() {
    if (this.data.saving || this.data.loading) return;
    let form;
    try {form=editContract.payload({...this.data.form,...customerIdentity.payload(this.data,this.data.form.customer_type)},this.loadedForm);} catch(error) {
      this.setData({submissionError:error.message});
      wx.showToast({ title: error.message, icon: "none" });
      return;
    }
    const write=writes.begin(this,'save',()=>this.data.customerId);if(!write)return;
    const customerId=this.data.customerId;
    this.setData({ saving: true, submissionError:'' });
    return apiClient.updateCustomer(customerId, form).then(() => {
      if(!write.current())return;
      wx.setStorageSync("pendingOpenCustomerId", customerId);
      if(write.visible())wx.showToast({ title: "客户信息已更新", icon: "success" });
      setTimeout(() => {if(write.settledVisible())wx.navigateBack();}, 700);
    }).catch((error) => {
      if(!write.current())return;
      const message=editContract.errorMessage(error);
      this.setData({submissionError:message});
      if(write.visible())wx.showToast({ title: message, icon: "none" });
    }).finally(()=>write.finish('saving'));
  },
});
