/**
 * 单条拜访使用 GET /visits/:id，不要求拥有整个客户档案的权限。
 * 已归档正文只读，经营建议通过独立 /advice 对象；采纳后进入人工任务表单。
 */
const apiClient = require("../../utils/apiClient");
const { normalizeCustomerDetail } = require("../../utils/customerDetail");
const {adviceAllowed,adviceState}=require('../../utils/businessAdviceState');
const access = require('../../utils/access');

Page({
  data: {
    customerId: "",
    visitId: "",
    customerName: "",
    visit: null,
    loading: true,
    error: "",
    visitAdvice:{status:'idle'},visitAdviceSection:'overview',visitAdviceSections:[{value:'overview',label:'拜访建议'},{value:'tasks',label:'待办建议'}],
  },

  onLoad(options = {}) {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-detail', options)) return;
    this.setData({
      customerId: decodeURIComponent(options.customer_id || ""),
      visitId: decodeURIComponent(options.visit_id || ""),
    });
  },

  onShow() {
    if (typeof getApp === "function" && getApp().guardPage && !getApp().guardPage(this, 'visit-detail')) return;
    if (!getApp().ensureLogin()) return;
    this.loadVisit();
  },

  loadVisit() {
    const { customerId, visitId } = this.data;
    this._adviceSerial=(this._adviceSerial||0)+1;
    this._linkSerial=(this._linkSerial||0)+1;
    this.setData({visit:null,visitAdvice:{status:'idle'}});
    if (!visitId) {
      this.setData({ loading: false, error: "缺少拜访记录标识" });
      return;
    }
    this.setData({ loading: true, error: "" });
    const serial=this._loadSerial=(this._loadSerial||0)+1;
    const identity=access.identity(getApp().globalData.session);
    const current=()=>serial===this._loadSerial && visitId===this.data.visitId && identity===access.identity(getApp().globalData.session);
    return apiClient.getVisit(visitId).then((raw) => {
      if(!current())return;
      if(customerId && String(raw.customer_id)!==String(customerId))throw Error('拜访客户不匹配');
      const hasLinkedOpportunities=Array.isArray(raw.linked_opportunities) && raw.linked_opportunities.some(
        link=>link && typeof link.id==='string' && link.id.trim());
      if(!raw.customer_id && !raw.partner_id && !hasLinkedOpportunities)throw Error('跟进关联主体不完整，请联系运营核对');
      const customer = normalizeCustomerDetail({id:raw.customer_id,name:raw.customer_name,visits:[raw]});
      const visit = customer.visits.find((item) => String(item.id) === String(visitId));
      if (!visit) throw new Error("未找到该拜访记录");
      this.setData({ customerId:raw.customer_id || '',customerName: visit.customerName, visit:{...visit,isParticipant:(visit.fdeParticipantIds||[]).includes(getApp().globalData.session.userId)} });
      this.loadVisitAdvice();
    }).catch((error) => {
      if(current())this.setData({ visit: null, error: error.message || "拜访记录加载失败" });
    }).finally(() => {if(current())this.setData({ loading: false });});
  },

  onHide(){this._loadSerial=(this._loadSerial||0)+1;this._adviceSerial=(this._adviceSerial||0)+1;this._linkSerial=(this._linkSerial||0)+1;this.setData({visitAdvice:{status:'idle'}});},
  onUnload(){this.onHide();},
  loadVisitAdvice(){return this.resolveVisitAdvice(false);},
  onVisitAdviceSection(event){return this.selectVisitAdviceSection({currentTarget:{dataset:{section:event.detail.value}}});},
  selectVisitAdviceSection(event){
    const section=event&&event.currentTarget&&event.currentTarget.dataset.section;
    if(!['overview','tasks'].includes(section)||section===this.data.visitAdviceSection)return;
    this._adviceSerial=(this._adviceSerial||0)+1;
    this.setData({visitAdviceSection:section,visitAdvice:{status:'idle'}});
    return this.loadVisitAdvice();
  },
  requestVisitAdvice(event){
    const action=event&&event.currentTarget&&event.currentTarget.dataset;
    if(!action || action.adviceAction!=='generate' || String(action.visitId)!==String(this.data.visitId) || action.section!==this.data.visitAdviceSection)return;
    return this.resolveVisitAdvice(true);
  },
  async resolveVisitAdvice(generate){
    if(!adviceAllowed(getApp().globalData.session,'visit',generate)){if(!generate)this.setData({visitAdvice:{status:'idle'}});return;}
    if(!this.data.visit || this.data.visitAdvice.status==='loading')return;
    if(generate&&['queued','running'].includes(this.data.visitAdvice.status))return this.loadVisitAdvice();
    const serial=this._adviceSerial=(this._adviceSerial||0)+1;
    const identity=()=>access.identity(getApp().globalData.session);
    const owner=identity();
    const visitId=this.data.visitId;
    const section=this.data.visitAdviceSection;
    const current=()=>serial===this._adviceSerial && visitId===this.data.visitId && section===this.data.visitAdviceSection && identity()===owner && adviceAllowed(getApp().globalData.session,'visit',generate);
    this.setData({visitAdvice:{status:'loading',reading:!generate}});
    try{
      const value=generate?await apiClient.requestBusinessAdvice('visit',visitId,section,true):await apiClient.getLatestBusinessAdvice('visit',visitId,section);
      if(current())this.setData({visitAdvice:adviceState(value,'visit',visitId,section,{allowEmpty:!generate})});
    }catch(error){if(current())this.setData({visitAdvice:{status:'error',error:error.message}});}
  },

  async openOpportunity(event = {}) {
    const visit = this.data.visit;
    const id = event.currentTarget && event.currentTarget.dataset.id || (visit && visit.opportunityId);
    if (!visit || !id || !(visit.linkedOpportunities || []).some(link=>String(link.id)===String(id) && link.canOpen===true)) return;
    const serial=this._linkSerial=(this._linkSerial||0)+1,identity=access.identity(getApp().globalData.session);
    const current=()=>serial===this._linkSerial && visit===this.data.visit && identity===access.identity(getApp().globalData.session);
    try {
      const raw=await apiClient.getOpportunityDetailHeader(id);
      if(!current())return;
      const linked=(raw.opportunities || []).some(item=>String(item.id)===String(id)) || (raw.primary_opportunity && String(raw.primary_opportunity.id)===String(id));
      if(!raw.id || !linked)throw Error('商机不存在或无权查看');
      wx.navigateTo({ url: `/pages/customer-assets/index?customer_id=${encodeURIComponent(raw.id)}&opportunity_id=${encodeURIComponent(id)}&period=all&readonly=1` });
    } catch(error) {if(current())wx.showToast({title:error.message || '商机加载失败，请重试',icon:'none'});}
  },
});
