Component({methods:{back(){wx.navigateBack({fail:()=>wx.switchTab({url:'/pages/index/index'})});},home(){wx.switchTab({url:'/pages/index/index'});}}});
