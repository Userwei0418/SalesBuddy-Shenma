const {auth,names,fields,present,lifecycle,date}=require('../view');
Page({
  data:{loading:true,ready:false,error:'',rows:[],record:null,title:'',updatedAt:'',summary:null,sectionTitle:'',activeKey:'',navItems:[]},...lifecycle,
  onLoad(options){this.kind=names[options.kind]?options.kind:null;this.id=/^[a-f0-9-]{36}$/i.test(options.id||'')?options.id:null;
    if(this.kind){this.setData({title:names[this.kind]+'详情',sectionTitle:this.kind==='customers'?'客户档案':this.kind==='follow-ups'?'跟进内容':'基本信息',activeKey:this.kind==='follow-ups'?'home':this.kind});wx.setNavigationBarTitle({title:names[this.kind]+'详情'});}},
  onShow(){if(!this.kind||!this.id){auth.go();return;}return this.load();},
  load(){return this.checked(async current=>{const result=await auth.request('/'+this.kind+'/'+this.id);if(current())this.setData({record:result,summary:present(this.kind,result),rows:fields(this.kind,result),updatedAt:date(result.updated_at)});});}
});
