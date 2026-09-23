Page({
  data:{context:null},
  onLoad(options) {
    if(getApp().guardPage&&!getApp().guardPage(this,'fde-records',options))return;
    if(!getApp().ensureLogin()) return;
    try {
      const context=JSON.parse(decodeURIComponent(options.context||''));
      if(!['self','team'].includes(context.scope)||!Number.isInteger(context.year)||!Array.isArray(context.quarters)) throw Error();
      this.setData({context});
    } catch(error) {wx.showToast({title:'记录参数无效，请返回重试',icon:'none'});}
  }
});
