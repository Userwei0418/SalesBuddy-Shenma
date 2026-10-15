Page({
  data:{context:null},
  onLoad(options = {}) {
    if(getApp().guardPage&&!getApp().guardPage(this,'fde-records',options))return;
    if(!getApp().ensureLogin()) return;
    try {
      const context=JSON.parse(decodeURIComponent(options.context||''));
      if(!['self','team'].includes(context.scope)||!Number.isInteger(context.year)||!Array.isArray(context.quarters)) throw Error();
      this._recordContext=context;this.setData({context});
    } catch(error) {wx.showToast({title:'记录参数无效，请返回重试',icon:'none'});}
  },
  onShow() {
    if(getApp().guardPage&&!getApp().guardPage(this,'fde-records'))return;
    if(!getApp().ensureLogin())return;
    if(this._recordContext && !this.data.context)this.setData({context:this._recordContext});
  },
  // Unmount the records component so its outstanding request cannot survive a
  // hidden page or account change; returning rechecks access and starts afresh.
  onHide(){this.setData({context:null});},
  onUnload(){this.onHide();},
});
