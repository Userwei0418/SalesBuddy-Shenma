const customerIdentity = require('../../utils/customerIdentity');
const uiColors = require("../../utils/uiColors");
const businessOptions = require('../../utils/businessOptions');
const apiClient = require("../../utils/apiClient");
const { draftScope } = require("../../utils/draftScope");
const { CUSTOMER_LEVELS } = require("../../utils/customerLevel");
const { identity } = require('../../utils/access');
const writes = require('../../utils/pageWriteContext').createPageWriteContext(()=>getApp().globalData.session);

const FIELD_EDITORS = {
  customer_name: { type: "text", placeholder: "请输入客户名称" },
  industry: { type: "text", placeholder: "请输入客户所属行业" },
  customer_type: { type: "select", options: businessOptions.customerCreation.customer_type },
  level_code: { type: "select", options: CUSTOMER_LEVELS },
  lead_source: { type: "select", options: businessOptions.customerCreation.source },
  contact_role: { type: "select", options: businessOptions.customerCreation.contact_role },
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
  ...customerIdentity.methods(apiClient, {session:()=>getApp().globalData.session,wx:()=>wx}),
  data: {
    ...customerIdentity.state(),
    fields: [],
    missingCount: 0,invalidFieldCount:0,creationOptionsReady:false,creationOptionsError:"",
    completedCount: 0,
    progressPercent: 0,
    customerName: "新客户建档",
    recorderName: "",
    roleName: "",
    saved: false,
    submitting: false,
    loading:true,
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
    this.writeIdentity=identity(session);this.loadGeneration=(this.loadGeneration||0)+1;
    const load=writes.capture(this,()=>this.loadGeneration);
    this.setData({loading:true,submitting:false});
    if (!require('../../utils/access').can(session,'customer.create')) {
      wx.showToast({ title: "当前账号没有建档下发权限", icon: "none" });
      setTimeout(() => wx.navigateBack(), 600);
      return;
    }
    try {await apiClient.getBusinessOptions();if(!businessOptions.isCustomerCreationReady())throw Error('创建选项目录暂不可用，请重新打开后重试');} catch(error) {if(load.current())this.setData({loading:false,creationOptionsReady:false,creationOptionsError:error.message});return;}
    if(!load.current())return;
    this.setData({creationOptionsReady:true,creationOptionsError:''});
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
      if (field.key === "lead_source") value = ({ 自主拓展: "销售自拓" })[value] || value;
      return { ...field, value };
    });
    this.createdCustomerId = hasSavedDraft ? savedDraft.createdCustomerId || "" : "";
    this.memberOptions = members.map((item) => item.name);
    this.members = members;
    this.setData({ loading:false,recorderName: session.userName, roleName: session.roleName || "当前身份", draftRestored: Boolean(hasSavedDraft) });
    this.initBusinessIdentity({primary_partner_name:(fields.find(f=>f.key==='partner_name') || {}).value || ''}, savedDraft && savedDraft.businessIdentity);
    this.refresh(fields);
    apiClient.getDirectoryMembers().then((response) => {
      if(!load.current())return;
      this.members = (response.items || []).filter((item) => item.role === "sales");
      this.memberOptions = this.members.map((item) => item.name);
      if (!this.data.fields.find((item) => item.key === "assigned_sales").value && this.members[0]) {
        this.refresh(this.data.fields.map((item) => item.key === "assigned_sales" ? { ...item, value: this.members[0].name } : item.key === "assigned_team" ? { ...item, value: this.members[0].team } : item));
      }
    }).catch((error) => {if(load.visible())wx.showToast({ title: error.message || "销售列表加载失败", icon: "none" });});
  },
  onShow(){
    this.writeHidden=false;
    if(this.writeIdentity!==undefined && this.writeIdentity!==identity(getApp().globalData.session))return this.onLoad();
    if(this.completedCreation && this.completedCreation.visible())wx.navigateBack();
  },
  onHide(){this.writeHidden=true;},
  onUnload(){this.unloaded=true;},

  refresh(fields) {
    const partner=fields.find(f=>f.key==='partner_name');
    if(partner && !this.data.associatedPartnerId)this.setData({associatedPartnerName:partner.value || ''});
    fields=fields.map(field=>field.key==='customer_type'?{...field,required:this.data.isCustomerIdentity,hidden:!this.data.isCustomerIdentity,validationError:this.data.isCustomerIdentity?field.validationError:''}:field.key==='partner_name'?{...field,label:'关联合作伙伴',hidden:true}:field);
    fields=fields.map(field=>{
      const options=(FIELD_EDITORS[field.key] || {}).options;
      return {...field,validationError:!field.hidden && field.value && options && options.length && !options.includes(field.value)?'请重新选择有效选项':''};
    });
    const invalidFieldCount=fields.filter(f=>f.validationError).length;
    const missingCount = fields.filter(isEmpty).length;
    const requiredCount = fields.filter(item => item.required).length;
    const completedCount = requiredCount - missingCount;
    const customer = fields.find((item) => item.key === "customer_name");
    this.setData({
      fields: fields.map((item) => ({ ...item, missing: isEmpty(item) })),
      missingCount,invalidFieldCount,
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
    if(this.writeIdentity!==undefined && this.writeIdentity!==identity(getApp().globalData.session))return;
    const session = getApp().globalData.session;
    wx.setStorageSync(this.draftKey, { fields: this.data.fields, businessIdentity:customerIdentity.draft(this.data), creator: this.data.recorderName,
      ownerUserId: session.userId, workspaceId: session.workspaceId, createdCustomerId: this.createdCustomerId || "", savedAt: Date.now() });
    this.setData({ saved: true, draftRestored: false });
    if (silent !== true) wx.showToast({ title: "草稿已保存", icon: "success" });
  },

  // BACKEND-CONTRACT 两阶段提交：POST /api/v1/customers -> POST /customers/{id}/assignments。
  // assignments 请求 assignee_account_code/first_action；创建成功即缓存 createdCustomerId，失败重试只做下发。
  // 两次调用不是原子事务，后端应保证重复下发安全；first_action 是否生成正式任务须以接口持久化为准。
  // 详见 docs/backend-handoff/客户与商机详解.md「建档并下发」。
  confirmArchive() {
    if (this.data.submitting || this.data.loading) return;
    if(!this.data.creationOptionsReady || this.data.invalidFieldCount){wx.showToast({title:this.data.creationOptionsError || '请重选标出的建档选项',icon:'none'});return;}
    if(this.completedCreation && this.completedCreation.current())return;
    if (this.data.missingCount) {
      wx.showToast({ title: `还有 ${this.data.missingCount} 个字段待补充`, icon: "none" });
      return;
    }
    const values = this.data.fields.reduce((result, item) => { result[item.key] = item.value; return result; }, {});
    let identityPayload;
    try {identityPayload=customerIdentity.payload(this.data,values.customer_type);} catch(error) {wx.showToast({title:error.message,icon:'none'});return;}
    const write=writes.begin(this,'createAssign',()=>this.draftKey || '');if(!write)return;
    const draftKey=this.draftKey,pendingDraftKey=this.pendingDraftKey,fields=JSON.parse(JSON.stringify(this.data.fields));
    const fieldsSignature=JSON.stringify(fields),membersSignature=JSON.stringify(this.members);
    this.setData({submitting:true});
    wx.showModal({
      title: "确认建档并下发？",
      content: `将“${values.customer_name}”正式建档，并下发给${values.assigned_team}的${values.assigned_sales}。`,
      confirmText: "确认建档",
      confirmColor: uiColors.primary,
      success: (result) => {
        if(write.started)return;
        if (!result.confirm || !write.visible() || fieldsSignature!==JSON.stringify(this.data.fields) || membersSignature!==JSON.stringify(this.members)) {write.finish('submitting');return;}
        const member = this.members.find((item) => item.name === values.assigned_sales);
        if (!member) { write.finish('submitting');wx.showToast({ title: "请重新选择负责销售", icon: "none" }); return; }
        if(!write.start())return;
        this.setData({ submitting: true });
        try {this.saveDraft(true);} catch(error) {write.finish('submitting');wx.showToast({title:'草稿未能保存，请重试后再建档',icon:'none'});return;}
        const creation = this.createdCustomerId ? Promise.resolve({ id: this.createdCustomerId }) : apiClient.createCustomer({
          name: values.customer_name,
          industry: values.industry,
          ...identityPayload,
          level_code: values.level_code,
          source: values.lead_source,
          target_team: values.assigned_team,
          partner_name: values.partner_name,
          contact_name: values.contact_name,
          contact_title: values.contact_title,
          contact_role: values.contact_role,
        });
        creation.then((customer) => {
          // Preserve the first stage receipt under its original account draft,
          // even if the user switched accounts. Never dispatch stage two then.
          if(write.current())this.createdCustomerId=customer.id;
          const savedDraft=wx.getStorageSync(draftKey);
          if(savedDraft && JSON.stringify(savedDraft.fields)===fieldsSignature)
            wx.setStorageSync(draftKey,{...savedDraft,createdCustomerId:customer.id});
          if(!write.current())return null;
          this.saveDraft(true);
          return apiClient.assignCustomer(customer.id, {
            assignee_account_code: member.account_code,
            first_action: values.first_action,
          }).then((assignment) => ({ ...assignment, customer }));
        }).then((assignment) => {
          if(!assignment || !write.current())return;
          this.completedCreation=writes.capture(this,()=>this.draftKey || '');
          wx.setStorageSync("lastManagementCustomerSuccess", {id:assignment.customer_id || assignment.customerId, ownerUserId:write.session.userId, workspaceId:write.session.workspaceId});
          wx.removeStorageSync(draftKey);
          wx.removeStorageSync(pendingDraftKey);
          wx.removeStorageSync("pendingManagementTaskAudio");
          if(write.visible())wx.showToast({ title: "建档并下发成功", icon: "success" });
          setTimeout(() => {if(write.settledVisible())wx.navigateBack();}, 850);
        }).catch((error) => {
          if(!write.current())return;
          if(write.visible())wx.showToast({ title: error.message || (this.createdCustomerId ? "客户已建档，下发未完成" : "建档失败，请重试"), icon: "none" });
        }).finally(() => write.finish('submitting'));
      },
      fail:()=>write.finish('submitting'),
    });
  },
});
