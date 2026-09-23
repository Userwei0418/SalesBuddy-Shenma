const access=require('../../utils/access');
const {DetailReadSession,customerLoaders,activeSections,detailWithPages,pageStates} = require('../../utils/detailReadSession');
const apiClient = require("../../utils/apiClient");
const { normalizeCustomerDetail } = require("../../utils/customerDetail");

Page({
  data: {
    customerId: "",
    role: "sales",
    opportunityId: "",
    customer: null,
    activeTab: "overview",
    expandedVisitId: "",
    tabs: [
      { key: "overview", label: "经营概览" },
      { key: "visits", label: "拜访记录" },
      { key: "opportunity", label: "商机进展" },
    ],
  },

  onLoad(options) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-detail', options)) return;
    this.setData({ customerId: options.id || "", opportunityId: options.opportunityId || "" });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'customer-detail')) return;
    if (!getApp().ensureLogin()) return;
    this.setData({ role: getApp().globalData.role,userName:getApp().globalData.session.userName,viewerUserId:getApp().globalData.session.userId });
    this.loadCustomer();
  },

  // BACKEND-CONTRACT 这是仍由 visit-confirm 跳入的独立旧详情路由，不等同于作战地图四 Tab。
  // /header 先呈现主档；/overview 按需补全量统计和最近 next_action。列表独立分页，展开后读完整拜访。
  // 详见 docs/backend-handoff/客户与商机详解.md「独立客户详情」中的现状差异；注释不改变业务行为。
  async loadCustomer() {
    if(!this._reader)this._reader=new DetailReadSession(()=>this.renderCustomer(),()=>access.identity(getApp().globalData.session));
    const reader=this._reader,token=reader.reset(this.data.customerId);
    this._raw=null;this._focused=null;this._fullVisits={};this._visitSerial=0;
    this.setData({customer:null,detailPages:{},expandedVisitId:'',visitDetailState:{loading:false,error:'',ready:false},detailFocusError:''});
    wx.showNavigationBarLoading();
    try {
      const raw=await apiClient.getCustomerHeader(this.data.customerId);
      if(!reader.current(token))return;
      this._raw=raw;this._loaders=customerLoaders(apiClient,this.data.customerId);this.renderCustomer();this.loadSections();
      if(this.data.opportunityId){
        const target=await apiClient.getCustomerOpportunityHeader(this.data.customerId,this.data.opportunityId);
        if(!reader.current(token))return;
        if(String(target.id)!==String(this.data.customerId))throw Error('商机客户不匹配');
        this._focused=(target.opportunities||[]).find(item=>String(item.id)===String(this.data.opportunityId));
        if(!this._focused)throw Error('商机不存在或无权查看');
        this.renderCustomer();
      }
    } catch(error) {if(reader.current(token)){if(this._raw)this.setData({detailFocusError:error.message});else wx.showModal({title:'客户加载失败',content:error.message||'客户暂不可用',showCancel:false});}}
    finally {if(reader.current(token))wx.hideNavigationBarLoading();}
  },
  onHide(){if(this._reader)this._reader.close();this._visitSerial=(this._visitSerial||0)+1;if(wx.hideNavigationBarLoading)wx.hideNavigationBarLoading();},
  onUnload(){this.onHide();},
  renderCustomer(){
    if(!this._reader||!this._reader.current()||!this._raw)return;
    const summary=this._reader.resource('overview');
    const raw=detailWithPages(summary.data||this._raw,this._reader.pages,this._focused);
    raw.visits=raw.visits.map(visit=>this._fullVisits[visit.id]||visit);
    const detail=normalizeCustomerDetail(raw,this.data.opportunityId);
    const stages=require('../../utils/opportunity').STAGES.slice(0,6).map(s=>s.label),current=Math.max(0,stages.indexOf(detail.opportunity.stage));
    this.setData({detailSummary:{loading:summary.loading,loaded:summary.loaded,error:summary.error},detailPages:pageStates(this._reader.pages),customer:{...detail,initial:detail.name.substring(0,1),contacts:detail.contacts.map(item=>({...item,initial:item.name?item.name.substring(0,1):'?',strengthTone:item.strength==='首要联系人'?'strong':'attention'})),stageSteps:stages.map((label,index)=>({label,status:index<current?'done':index===current?'current':'upcoming'}))}});
  },
  loadCustomerSummary(options={}){
    if(!this._reader||!this._raw)return;
    const id=this.data.customerId;
    return this._reader.loadResource('overview',async()=>{
      const raw=await apiClient.getCustomerOverview(id);
      if(String(raw.id)!==String(id)||!raw.summary||!raw.profile)throw Error('客户经营汇总响应不完整');return raw;
    },options);
  },
  retryCustomerSummary(){return this.loadCustomerSummary({retry:true});},
  loadSections(){if(this._reader&&this._loaders){activeSections(this.data.activeTab).forEach(key=>this._reader.load(key,this._loaders[key]));if(this.data.activeTab==='overview')this.loadCustomerSummary();}},
  moreSection(e){const key=e.currentTarget.dataset.section;return this._reader.load(key,this._loaders[key],{more:true});},
  retrySection(e){const key=e.currentTarget.dataset.section;return this._reader.load(key,this._loaders[key],{retry:true});},
  selectTab(e) {
    this.setData({ activeTab: e.currentTarget.dataset.tab, expandedVisitId: "" });
    this._visitSerial=(this._visitSerial||0)+1;this.loadSections();
  },

  supplementVisit(e) {
    const visit=(this.data.customer && this.data.customer.visits || []).find(item=>String(item.id)===String(e.currentTarget.dataset.id));
    if(!visit || !visit.recorderId || visit.recorderId!==getApp().globalData.session.userId)return;
    wx.navigateTo({ url: `/pages/visit-confirm/index?visitId=${encodeURIComponent(visit.id)}` });
  },

  async toggleVisit(e) {
    const visitId=e.currentTarget.dataset.id;
    if(!(this.data.customer && this.data.customer.visits || []).some(visit=>String(visit.id)===String(visitId)))return;
    if(this.data.expandedVisitId===visitId){this._visitSerial+=1;this.setData({expandedVisitId:''});return;}
    this._visitSerial=(this._visitSerial||0)+1;this.setData({expandedVisitId:visitId,visitDetailState:{loading:false,error:'',ready:Boolean(this._fullVisits[visitId])}});return this.loadVisitDetail(visitId);
  },
  retryVisitDetail(){return this.loadVisitDetail(this.data.expandedVisitId);},
  async loadVisitDetail(id){
    if(!id||!this._reader||this._fullVisits[id])return;
    const summary=(this.data.customer && this.data.customer.visits || []).find(visit=>String(visit.id)===String(id));
    if(!summary)return;
    const reader=this._reader,token=reader.token(),serial=this._visitSerial=(this._visitSerial||0)+1;
    this.setData({visitDetailState:{loading:true,error:'',ready:false}});
    try {
      const visit=await apiClient.getVisit(id);
      if(!reader.current(token)||serial!==this._visitSerial||this.data.expandedVisitId!==id)return;
      if(String(visit.id)!==String(id)||(summary.customerId&&String(visit.customer_id)!==String(summary.customerId)))throw Error('拜访记录不匹配');
      this._fullVisits[id]={...visit,is_summary:false};this.renderCustomer();this.setData({visitDetailState:{loading:false,error:'',ready:true}});
    } catch(error){if(reader.current(token)&&serial===this._visitSerial)this.setData({visitDetailState:{loading:false,error:error.message||'完整记录加载失败',ready:false}});}
  },
  createOpportunity() { wx.navigateTo({ url: `/pages/opportunity-create/index?customerId=${this.data.customer.id}` }); },
  editOpportunity(e) { wx.navigateTo({url:`/pages/opportunity-create/index?customerId=${this.data.customer.id}&opportunityId=${e.currentTarget.dataset.id}`}); },
  recordVisit() {
    const customer = this.data.customer;
    wx.navigateTo({
      url: `/pages/visit-entry/index?customerId=${encodeURIComponent(customer.id)}&customerName=${encodeURIComponent(customer.name)}`,
    });
  },

  openAssets() {wx.navigateTo({url:`/pages/customer-assets/index?customer_id=${this.data.customerId}&customer_name=${encodeURIComponent(this.data.customer.name)}&period=all`});},
  createTask() {
    const customer = this.data.customer;
    wx.navigateTo({ url: `/pages/management-task-create/index?customerId=${encodeURIComponent(customer.id)}` });
  },
});
