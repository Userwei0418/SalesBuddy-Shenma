const {auth,names,pageTitles,present,lifecycle}=require('../view');
Page({
  data:{loading:true,ready:false,error:'',companyName:'',modules:[],kind:'',title:'',pageTitle:'',unit:'条',activeKey:'',navItems:[],query:'',items:[],total:0,more:false,loadingMore:false},...lifecycle,
  onLoad(options){this.kind=names[options.kind]?options.kind:null;if(this.kind){this.setData({kind:this.kind,title:names[this.kind],pageTitle:pageTitles[this.kind],unit:this.kind==='customers'?'家':this.kind==='opportunities'?'个':'条',activeKey:this.kind==='follow-ups'?'home':this.kind});wx.setNavigationBarTitle({title:pageTitles[this.kind]});}},
  onShow(){if(!this.kind){auth.go();return;}return this.load();},
  load(){const q=this.data.query;return this.checked(async current=>{const result=await auth.request('/'+this.kind,{q,limit:20,offset:0});if(current()){this._appliedQuery=q;this._dataVersion=result.data_version;this.setData({items:(result.items||[]).map(item=>present(this.kind,item)),total:result.total,more:result.items.length<result.total});}});},
  inputQuery(event){this.setData({query:event.detail.value});},
  search(){return this.load();},
  onReachBottom(){return this.more();},
  async more(){
    if(!this.data.more||this.data.loadingMore||this.data.loading||!this._visible)return;
    const identity=auth.snapshot(),serial=this._loadSerial,offset=this.data.items.length;
    const current=()=>this._visible&&serial===this._loadSerial&&auth.isCurrent(identity);
    this.setData({loadingMore:true,error:''});
    try{const result=await auth.request('/'+this.kind,{q:this._appliedQuery||'',limit:20,offset});if(!current())return;
      if(result.data_version!==this._dataVersion){
        this.setData({items:[],ready:false,more:false,loadingMore:false});
        // One explicit page-one refresh; load() does not recursively paginate.
        await this.load();return;
      }
      const seen=new Set(this.data.items.map(item=>item.id));const items=this.data.items.concat((result.items||[]).filter(item=>!seen.has(item.id)).map(item=>present(this.kind,item)));
      this.setData({items,total:result.total,more:result.items.length>0&&offset+result.items.length<result.total});
    }catch(error){if(current())this.setData({error:error.message});}finally{if(current())this.setData({loadingMore:false});}
  },
  openDetail(event){wx.navigateTo({url:'/pages/demo-view/detail/index?kind='+this.kind+'&id='+encodeURIComponent(event.currentTarget.dataset.id)});}
});
