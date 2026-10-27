const api=require('../../utils/apiClient');
const access=require('../../utils/access');
const dateLabel=require('../../utils/visitDates').dateLabel;
const today=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
Page({
  data:{context:null,authorized:false,records:[],recordsLoading:false,recordsError:'',recordsMore:false,recordsOffset:0,recordsTotal:0,query:'',dateFrom:'',dateTo:''},
  onLoad(options = {}) {
    if(getApp().guardPage&&!getApp().guardPage(this,'fde-records',options))return;
    if(!getApp().ensureLogin()) return;
    if(options.mode==='authorized'){this.setData({authorized:true,dateFrom:today().slice(0,7)+'-01',dateTo:today()});return;}
    try {
      const context=JSON.parse(decodeURIComponent(options.context||''));
      if(!['self','team'].includes(context.scope)||!Number.isInteger(context.year)||!Array.isArray(context.quarters)) throw Error();
      this._recordContext=context;this.setData({context});
    } catch(error) {wx.showToast({title:'记录参数无效，请返回重试',icon:'none'});}
  },
  onShow() {
    if(getApp().guardPage&&!getApp().guardPage(this,'fde-records'))return;
    if(!getApp().ensureLogin())return;
    this.hidden=false;
    if(this.data.authorized){const identity=access.identity(getApp().globalData.session);if(this._recordsIdentity&&this._recordsIdentity!==identity)this.setData({query:'',dateFrom:today().slice(0,7)+'-01',dateTo:today()});this._recordsIdentity=identity;return this.loadRecords();}
    if(this._recordContext && !this.data.context)this.setData({context:this._recordContext});
  },
  // Unmount the records component so its outstanding request cannot survive a
  // hidden page or account change; returning rechecks access and starts afresh.
  onHide(){this.hidden=true;this._recordsSeq=(this._recordsSeq||0)+1;this.setData({context:null,records:[],recordsLoading:false,recordsError:'',recordsMore:false,recordsOffset:0,recordsTotal:0});},
  onUnload(){this.onHide();},
  resetFilters(values){this._recordsSeq=(this._recordsSeq||0)+1;this.setData({...values,records:[],recordsMore:false,recordsOffset:0,recordsTotal:0,recordsError:'',recordsLoading:false});},
  inputQuery(e){this.resetFilters({query:String(e.detail.value||'').slice(0,100)});},
  changeDate(e){const key=e.currentTarget.dataset.key;if(['dateFrom','dateTo'].includes(key))this.resetFilters({[key]:e.detail.value});},
  async loadRecords(e){
    if(!this.data.authorized || this.hidden || !access.can(getApp().globalData.session,['visit.read','visit.claimed_customer_read']))return;
    const more=Boolean(e&&e.currentTarget&&e.currentTarget.dataset.more);
    if(more&&this.data.recordsLoading)return;
    const {dateFrom,dateTo}=this.data;
    if(!dateFrom||!dateTo||dateFrom>dateTo){this.setData({recordsError:'请选择有效的起止日期'});return;}
    const seq=this._recordsSeq=(this._recordsSeq||0)+1,identity=access.identity(getApp().globalData.session),offset=more?this.data.recordsOffset:0;
    const current=()=>!this.hidden&&seq===this._recordsSeq&&identity===access.identity(getApp().globalData.session);
    this.setData({recordsLoading:true,recordsError:'',...(more?{}:{records:[],recordsMore:false,recordsOffset:0,recordsTotal:0})});
    try{
      const result=await api.listVisitRange({scope:'all',date_basis:'interaction',date_from:dateFrom,date_to:dateTo,q:this.data.query.trim(),page_size:20,offset});
      if(!current())return;
      if(!result||!Array.isArray(result.items))throw Error('跟进记录响应不完整，请重试');
      const records=more?this.data.records.slice():[],ids=new Set(records.map(row=>row.id));
      for(const row of result.items)if(row.id&&!ids.has(row.id)){records.push({...row,dateLabel:dateLabel(row.visit_date||row.interaction_at),recorderLabel:row.original_recorder_name||row.recorder_name||row.creator_name||'未记录',canReadDetail:row.can_read_detail!==false});ids.add(row.id);}
      const next=result.next_offset,hasMore=result.has_more===true;
      if(hasMore&&(!Number.isSafeInteger(next)||next<=offset||!result.items.length))throw Error('跟进记录分页响应不完整，请重试');
      this.setData({records,recordsTotal:Number(result.summary&&result.summary.total)||records.length,recordsMore:hasMore,recordsOffset:hasMore?next:offset});
    }catch(error){if(current())this.setData({recordsError:error.message||'跟进记录读取失败'});}
    finally{if(current())this.setData({recordsLoading:false});}
  },
  openRecord(e){
    if(!access.can(getApp().globalData.session,['visit.read','visit.claimed_customer_read']))return;
    const row=this.data.records.find(item=>item.id===e.currentTarget.dataset.id);
    if(!row||!row.canReadDetail)return;
    wx.navigateTo({url:`/pages/visit-detail/index?visit_id=${encodeURIComponent(row.id)}${row.customer_id?'&customer_id='+encodeURIComponent(row.customer_id):''}`});
  },
});
