// Keep old links reachable without restoring retired collaboration requests.
Page({
  data:{},
  goBack() {
    wx.navigateBack({delta:1, fail:()=>wx.switchTab({url:'/pages/workbench/index'})});
  },
});
