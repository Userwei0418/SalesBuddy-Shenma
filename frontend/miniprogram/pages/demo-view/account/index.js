const {auth,lifecycle}=require('../view');
Page({data:{loading:true,ready:false,error:'',companyName:'',account:'',activeKey:'account',navItems:[]},...lifecycle,
  onShow(){return this.load();},load(){return this.checked(async()=>{});},
  logout(){getApp().logout();wx.reLaunch({url:'/pages/login/index'});}
});
