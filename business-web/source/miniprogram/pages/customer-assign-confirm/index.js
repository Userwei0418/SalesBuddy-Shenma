const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { CUSTOMER_LEVELS } = require("../../utils/customerLevel");

const FIELD_EDITORS = {
  customer_name: { type: "text", placeholder: "请输入客户名称" },
  industry: { type: "text", placeholder: "请输入客户所属行业" },
  customer_type: { type: "select", options: businessOptions.customer.customer_type },
  level_code: { type: "select", options: CUSTOMER_LEVELS },
  lead_source: { type: "select", options: businessOptions.customer.source },
  contact_role: { type: "select", options: businessOptions.customer.contact_role },
  contact_name: { type: "text", placeholder: "请输入客户联系人姓名" },
  contact_title: { type: "text", placeholder: "请输入联系人职位" },
  partner_name: { type: "text", placeholder: "没有合作伙伴可填写“无”" },
  assigned_sales: { type: "select", options: [] },
  first_action: { type: "textarea", placeholder: "请输入首次跟进要求和完成时限" },
};

function isEmpty(field) {
  return Boolean(field.required) && !String(field.value || "").trim();
}

Page({
  data: {
    fields: [],
    missingCount: 0,
    completedCount: 0,
    progressPercent: 0,
    customerName: "新客户建档",
    recorderName: "",
    roleName: "",
    saved: false,
    submitting: false,
    draftRestored: false,
    editorVisible: false,
    editorIndex: -1,
    editorTitle: "",
    editorType: "text",
    editorValue: "",
    editorPlaceholder: "",
    editorOptions: [],
  },

  async onLoad() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-assign-confirm')) return;
    if (!getApp().ensureLogin()) return;
    const app = getApp();
    const session = app.globalData.session;
    if (!require('../../utils/access').can(session,'customer.create')) {
      wx.showToast({ title: "当前账号没有建档下发权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 600);
      return;
    }
    try {await apiClient.getBusinessOptions();} catch(error) {wx.showToast({title:error.message,icon:'none'});return;}
    const members = [];
    this.draftKey = `managementCustomerDraft:${draftScope(session)}`;
    this.pendingDraftKey = `pendingManagementCustomerDraft:${draftScope(session)}`;
    const pending = wx.getStorageSync(this.pendingDraftKey) || {};
    const savedDraft = wx.getStorageSync(this.draftKey);
    const hasSavedDraft = savedDraft && Array.isArray(savedDraft.fields);
    const defaultSales = pending.assignedSales || "";
    const defaultMember = members.find((item) => item.name === defaultSales) || members[0] || {};
    const defaults = [
      { key: "customer_name", label: "客户名称", value: pending.customerName || "", required: true },
      { key: "industry", label: "所属行业", value: pending.industry || "", required: false },
      { key: "customer_type", label: "客户类型", value: "潜在客户", required: true },
      { key: "level_code", label: "客户优先级", value: pending.levelCode || "", required: true },
      { key: "lead_source", label: "客户来源", value: pending.leadSource || "", required: true },
      { key: "partner_name", label: "合作伙伴", value: pending.partnerName || "", required: false },
      { key: "contact_name", label: "首要联系人", value: pending.contactName || "", required: true },
      { key: "contact_title", label: "联系人职位", value: pending.contactTitle || "", required: true },
      { key: "contact_role", label: "联系人角色", value: "", required: true },
      { key: "assigned_sales", label: "下发给销售", value: defaultSales, required: true },
      { key: "assigned_team", label: "所属团队", value: defaultMember.team || session.team, required: true, readonly: true, system: true },
      { key: "first_action", label: "首次跟进要求", value: pending.firstAction || "", required: true },
    ];
    // Old drafts contribute values only; required flags and removed fields never override this schema.
    const fields = defaults.map(field => {
      const saved = hasSavedDraft && savedDraft.fields.find(item => item.key === field.key);
      let value = saved ? saved.value : field.value;
      if (field.key === "lead_source") value = ({ 公司分配: "销售线索", 自主拓展: "销售自拓" })[value] || value;
      const editor = FIELD_EDITORS[field.key];
      if (editor && editor.options && editor.options.length && !editor.options.includes(value)) value = field.value;
      return { ...field, value };
    });
    this.createdCustomerId = hasSavedDraft ? savedDraft.createdCustomerId || "" : "";
    this.memberOptions = members.map((item) => item.name);
    this.members = members;
    this.setData({ recorderName: session.userName, roleName: session.roleName || "当前身份", draftRestored: Boolean(hasSavedDraft) });
    this.refresh(fields);
    apiClient.getDirectoryMembers().then((response) => {
      this.members = (response.items || []).filter((item) => item.role === "sales");
      this.memberOptions = this.members.map((item) => item.name);
      if (!this.data.fields.find((item) => item.key === "assigned_sales").value && this.members[0]) {
        this.refresh(this.data.fields.map((item) => item.key === "assigned_sales" ? { ...item, value: this.members[0].name } : item.key === "assigned_team" ? { ...item, value: this.members[0].team } : item));
      }
    }).catch((error) => wx.showToast({ title: error.message || "销售列表加载失败", icon: "none" }));
  },

  refresh(fields) {
    const missingCount = fields.filter(isEmpty).length;
    const requiredCount = fields.filter(item => item.required).length;
    const completedCount = requiredCount - missingCount;
    const customer = fields.find((item) => item.key === "customer_name");
    this.setData({
      fields: fields.map((item) => ({ ...item, missing: isEmpty(item) })),
      missingCount,
      completedCount,
      requiredCount,
      progressPercent: requiredCount ? Math.round((completedCount / requiredCount) * 100) : 0,
      customerName: customer && customer.value ? customer.value : "新客户建档",
    });
  },

  openEditor(e) {
    const index = Number(e.currentTarget.dataset.index);
    const field = this.data.fields[index];
    if (this.createdCustomerId && !["assigned_sales", "first_action"].includes(field.key)) {
      wx.showToast({ title: "客户已建档，可在客户详情中修改", icon: "none" }); return;
    }
    if (field.readonly) {
      wx.showToast({ title: "团队由所选销售自动确定", icon: "none" });
      return;
    }
    const config = FIELD_EDITORS[field.key] || { type: "text", placeholder: `请输入${field.label}` };
    const options = (field.key === "assigned_sales" ? this.memberOptions : config.options || []).map((label) => ({ label, selected: label === field.value }));
    this.setData({ editorVisible: true, editorIndex: index, editorTitle: field.label, editorType: config.type, editorValue: String(field.value || ""), editorPlaceholder: config.placeholder || "请输入内容", editorOptions: options });
  },

  inputEditor(e) { this.setData({ editorValue: e.detail.value }); },
  selectOption(e) {
    const index = Number(e.currentTarget.dataset.index);
    const options = this.data.editorOptions.map((item, itemIndex) => ({ ...item, selected: itemIndex === index }));
    this.setData({ editorOptions: options, editorValue: options[index].label });
  },
  closeEditor() { this.setData({ editorVisible: false, editorIndex: -1 }); },
  stopPropagation() {},

  saveEditor() {
    const index = this.data.editorIndex;
    const field = this.data.fields[index];
    const value = String(this.data.editorValue || "").trim();
    if (!value && field.required) {
      wx.showToast({ title: `${field.label}为必填项`, icon: "none" });
      return;
    }
    let fields = this.data.fields.map((item, itemIndex) => itemIndex === index ? { ...item, value, edited: true } : item);
    if (field.key === "assigned_sales") {
      const member = this.members.find((item) => item.name === value);
      fields = fields.map((item) => item.key === "assigned_team" ? { ...item, value: member ? member.team : "待确认", edited: true } : item);
    }
    this.setData({ editorVisible: false, editorIndex: -1, saved: false, draftRestored: false });
    this.refresh(fields);
    wx.vibrateShort({ type: "light" });
  },

  saveDraft(silent = false) {
    const session = getApp().globalData.session;
    wx.setStorageSync(this.draftKey, { fields: this.data.fields, creator: this.data.recorderName,
      ownerUserId: session.userId, workspaceId: session.workspaceId, createdCustomerId: this.createdCustomerId || "", savedAt: Date.now() });
    this.setData({ saved: true, draftRestored: false });
    if (silent !== true) wx.showToast({ title: "草稿已保存", icon: "success" });
  },

  // BACKEND-CONTRACT 两阶段提交：POST /api/v1/customers -> POST /customers/{id}/assignments。
  // assignments 请求 assignee_account_code/first_action；创建成功即缓存 createdCustomerId，失败重试只做下发。
  // 两次调用不是原子事务，后端应保证重复下发安全；first_action 是否生成正式任务须以接口持久化为准。
  // 详见 docs/backend-handoff/客户与商机详解.md「建档并下发」。
  confirmArchive() {
    if (this.data.submitting) return;
    if (this.data.missingCount) {
      wx.showToast({ title: `还有 ${this.data.missingCount} 个字段待补充`, icon: "none" });
      return;
    }
    const values = this.data.fields.reduce((result, item) => { result[item.key] = item.value; return result; }, {});
    wx.showModal({
      title: "确认建档并下发？",
      content: `将“${values.customer_name}”正式建档，并下发给${values.assigned_team}的${values.assigned_sales}。`,
      confirmText: "确认建档",
      confirmColor: "#1677FF",
      success: (result) => {
        if (!result.confirm) return;
        const member = this.members.find((item) => item.name === values.assigned_sales);
        if (!member) { wx.showToast({ title: "请重新选择负责销售", icon: "none" }); return; }
        this.setData({ submitting: true });
        const creation = this.createdCustomerId ? Promise.resolve({ id: this.createdCustomerId }) : apiClient.createCustomer({
          name: values.customer_name,
          industry: values.industry,
          customer_type: values.customer_type,
          level_code: values.level_code,
          source: values.lead_source,
          target_team: values.assigned_team,
          partner_name: values.partner_name,
          contact_name: values.contact_name,
          contact_title: values.contact_title,
          contact_role: values.contact_role,
        });
        creation.then((customer) => {
          this.createdCustomerId = customer.id;
          this.saveDraft(true);
          return apiClient.assignCustomer(customer.id, {
            assignee_account_code: member.account_code,
            first_action: values.first_action,
          }).then((assignment) => ({ ...assignment, customer }));
        }).then((assignment) => {
          wx.setStorageSync("lastManagementCustomerSuccess", {id:assignment.customer_id || assignment.customerId, ownerUserId:getApp().globalData.session.userId, workspaceId:getApp().globalData.session.workspaceId});
          wx.removeStorageSync(this.draftKey);
          wx.removeStorageSync(this.pendingDraftKey);
          wx.removeStorageSync("pendingManagementTaskAudio");
          wx.showToast({ title: "建档并下发成功", icon: "success" });
          setTimeout(() => wx.navigateBack(), 850);
        }).catch((error) => {
          wx.showToast({ title: error.message || (this.createdCustomerId ? "客户已建档，下发未完成" : "建档失败，请重试"), icon: "none" });
        }).finally(() => this.setData({ submitting: false }));
      },
    });
  },
});
